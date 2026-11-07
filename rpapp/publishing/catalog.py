"""The catalog: the publications this computer knows of, in an SQLite database of its own.

Everything in it comes from signed events (docs/card-announcements.md), checked before they go in; the
events are kept as received, and never changed. What changes over time is worked out from them, so that
it can't disagree with them:

- a card's **latest version**: its newest publication not withdrawn (for equal dates, the smallest id);
- a publisher's **current name**: the published name of their newest publication not withdrawn, any
  card; their **previous names**, those of the others. (A name only in withdrawn publications is no
  longer theirs: a withdrawal can be for a name given by mistake.)
- **withdrawn** publications: those named by a withdrawal of the same publisher. Kept, but not listed
  among the cards, nor downloaded; get() says when and why, for those who have the card.

Blocking a publisher deletes all they published, and refuses what comes from them next.

    receive() / receive_all()   events from a relay (or the app's own): checked, then kept
    cards(CardQuery)            the latest version of each card (or every version), by filters, by pages
    tags(CardQuery)             the tags of those cards, with how many have each
    versions(), get()           a card's versions; one publication (withdrawn ones too)
    publishers(PublisherQuery), publisher()
    block(), unblock(), blocked()
    relays(), add_relay(), remove_relay(); save_sync(), sync_failed(): how far discovery is (discovery.py)

Its calls wait for the database (milliseconds; longer when the disk is busy): from asyncio code, they
run in a thread, as the sharing service will do. A lock makes them safe from several threads.

Its structure changes with the app's versions: each change is a migration (MIGRATIONS), applied when the
catalog is opened, and the database records how far it is (PRAGMA user_version).
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import unicodedata
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar
from urllib.parse import urlsplit

from . import announcements as fmt
from .announcements import Publication, Rejected, Withdrawal
from .nostr import Event, to_npub

MAX_PAGE = 500

MIGRATIONS = [
    # 1: publications, withdrawals, blocked publishers
    """
    CREATE TABLE publications (
        event_id         TEXT PRIMARY KEY CHECK (length(event_id) = 64),
        publisher_key    TEXT NOT NULL CHECK (length(publisher_key) = 64),
        card_uuid        TEXT NOT NULL CHECK (length(card_uuid) = 36),
        created_at       INTEGER NOT NULL,
        published_name   TEXT NOT NULL,
        name_folded      TEXT NOT NULL,      -- (for searching: see fold())
        title_folded     TEXT NOT NULL,
        content_rating   TEXT NOT NULL CHECK (content_rating IN ('sfw', 'nsfw')),
        card_format      TEXT NOT NULL,
        language         TEXT,
        file_sha256      TEXT NOT NULL CHECK (length(file_sha256) = 64),
        torrent_infohash TEXT NOT NULL CHECK (length(torrent_infohash) = 40),
        card_size        INTEGER NOT NULL CHECK (card_size > 0),
        event_json       TEXT NOT NULL,      -- the signed event, as received: what's above comes from it
        received_at      INTEGER NOT NULL
    );
    CREATE TRIGGER publications_never_change BEFORE UPDATE ON publications
    BEGIN SELECT RAISE(ABORT, 'publications never change'); END;
    CREATE INDEX publications_by_date ON publications (created_at DESC, event_id);
    CREATE INDEX publications_by_card ON publications (publisher_key, card_uuid, created_at DESC, event_id);
    CREATE INDEX publications_by_publisher ON publications (publisher_key, created_at DESC, event_id);
    CREATE INDEX publications_by_file ON publications (file_sha256);
    CREATE INDEX publications_by_torrent ON publications (torrent_infohash);

    CREATE TABLE publication_tags (
        event_id TEXT NOT NULL REFERENCES publications (event_id) ON DELETE CASCADE,
        tag      TEXT NOT NULL,
        PRIMARY KEY (event_id, tag)
    ) WITHOUT ROWID;
    CREATE INDEX tags_by_tag ON publication_tags (tag, event_id);

    -- A withdrawal names publications by id: they may come after it, and only the same publisher's count.
    CREATE TABLE withdrawals (
        event_id      TEXT PRIMARY KEY CHECK (length(event_id) = 64),
        publisher_key TEXT NOT NULL CHECK (length(publisher_key) = 64),
        created_at    INTEGER NOT NULL,
        reason        TEXT NOT NULL,
        event_json    TEXT NOT NULL,
        received_at   INTEGER NOT NULL
    );
    CREATE TRIGGER withdrawals_never_change BEFORE UPDATE ON withdrawals
    BEGIN SELECT RAISE(ABORT, 'withdrawals never change'); END;
    CREATE INDEX withdrawals_by_publisher ON withdrawals (publisher_key);
    CREATE TABLE withdrawn_events (
        withdrawal_id TEXT NOT NULL REFERENCES withdrawals (event_id) ON DELETE CASCADE,
        event_id      TEXT NOT NULL,
        PRIMARY KEY (withdrawal_id, event_id)
    ) WITHOUT ROWID;
    CREATE INDEX withdrawn_by_event ON withdrawn_events (event_id);

    CREATE TABLE blocked_publishers (
        public_key TEXT PRIMARY KEY CHECK (length(public_key) = 64),
        blocked_at INTEGER NOT NULL
    );

    -- The publications not withdrawn: those listed, downloaded, and that names and versions come from.
    CREATE VIEW live_publications AS
    SELECT p.* FROM publications p
    WHERE NOT EXISTS (SELECT 1 FROM withdrawn_events w JOIN withdrawals d ON d.event_id = w.withdrawal_id
                      WHERE w.event_id = p.event_id AND d.publisher_key = p.publisher_key);

    -- Each card's latest version.
    CREATE VIEW latest_publications AS
    SELECT p.* FROM live_publications p
    WHERE NOT EXISTS (SELECT 1 FROM live_publications q
                      WHERE q.publisher_key = p.publisher_key AND q.card_uuid = p.card_uuid
                        AND (q.created_at > p.created_at OR (q.created_at = p.created_at AND q.event_id < p.event_id)));

    -- Each name a publisher used, in publications not withdrawn.
    CREATE VIEW publisher_names AS
    SELECT publisher_key, published_name AS name, name_folded, MIN(created_at) AS first_used,
           MAX(created_at) AS last_used
    FROM live_publications GROUP BY publisher_key, published_name;
    """,
    # 2: the relays, and how far discovery is on each
    """
    CREATE TABLE relays (
        url             TEXT PRIMARY KEY,
        read            INTEGER NOT NULL DEFAULT 1 CHECK (read IN (0, 1)),     -- discovery asks it
        write           INTEGER NOT NULL DEFAULT 1 CHECK (write IN (0, 1)),    -- publishing sends to it
        added_at        INTEGER NOT NULL,
        last_attempt_at INTEGER,           -- its last discovery
        last_success_at INTEGER,           -- its last discovery that went to its end
        last_error      TEXT               -- why its last discovery didn't (NULL: it did)
    );
    -- How far discovery is on a relay, for each kind of event asked for (publications, withdrawals).
    CREATE TABLE relay_sync (
        relay_url  TEXT NOT NULL REFERENCES relays (url) ON DELETE CASCADE,
        what       TEXT NOT NULL,
        checkpoint INTEGER NOT NULL,       -- when its last complete discovery started
        gap_since  INTEGER,                -- what's left to get, older than what was got: from gap_until
        gap_until  INTEGER,                -- down to gap_since (NULL: nothing)
        PRIMARY KEY (relay_url, what)
    ) WITHOUT ROWID;
    """,
    # 3: a blocked publisher's name, as it was when they were blocked (their publications are gone)
    """
    ALTER TABLE blocked_publishers ADD COLUMN name TEXT NOT NULL DEFAULT '';
    """,
]


class CatalogError(Exception):
    """The catalog can't be opened or used: the message says why, in plain words."""


@dataclass(frozen=True, slots=True)
class WithdrawnInfo:
    withdrawal_id: str
    at: int          # the withdrawal's date (Unix seconds)
    reason: str      # '' if none was given


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """A publication, with what's worked out about it now."""
    publication: Publication
    publisher_name: str              # the publisher's current name (the published one may differ)
    is_latest: bool
    withdrawn: WithdrawnInfo | None
    received_at: int


@dataclass(frozen=True, slots=True)
class PublisherInfo:
    publisher_id: str
    current_name: str
    previous_names: tuple[str, ...]  # most recently used first
    cards: int                       # cards with a version not withdrawn
    first_seen_at: int               # when the first of their publications was received
    last_published_at: int

    @property
    def npub(self) -> str:
        return to_npub(self.publisher_id)


T = TypeVar('T')


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None          # for the next page; None when it was the last


@dataclass(frozen=True, slots=True)
class CardQuery:
    """Which cards: the latest version of each by default, newest first. Text is matched ignoring case
    and accents, every word somewhere in it."""
    publisher_id: str | None = None
    publisher_name: str | None = None    # their current or a previous name
    card_uuid: str | None = None
    card_name: str | None = None
    tags: tuple[str, ...] = ()
    match_all_tags: bool = False
    exclude_tags: tuple[str, ...] = ()   # none of these
    card_format: str | None = None
    content_rating: Literal['sfw', 'nsfw'] | None = None
    language: str | None = None
    published_since: int | None = None   # Unix seconds, included
    published_until: int | None = None
    include_versions: bool = False       # every version, not just the latest
    limit: int = 100
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class TagCount:
    tag: str
    cards: int                           # how many of the cards asked about have it


@dataclass(frozen=True, slots=True)
class PublisherQuery:
    """Which publishers: those who published most recently first."""
    publisher_id: str | None = None
    name: str | None = None              # matches current and previous names
    limit: int = 100
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class Received:
    """What became of an event received."""
    status: Literal['added', 'duplicate', 'rejected', 'withdrawal']
    event_id: str = ''
    code: str = ''                       # why it was rejected (the format's codes)
    withdrawn: tuple[str, ...] = ()      # a withdrawal: the publications it withdraws now


@dataclass(frozen=True, slots=True)
class SyncPoint:
    """How far discovery is on a relay, for one kind of event."""
    checkpoint: int                      # when its last complete discovery started
    gap: tuple[int, int] | None = None   # (since, until): older ones still to get from it


@dataclass(frozen=True, slots=True)
class RelayInfo:
    """A relay, and how far discovery is on it."""
    url: str
    read: bool                           # discovery asks it
    write: bool                          # publishing sends to it
    added_at: int
    last_attempt_at: int | None = None
    last_success_at: int | None = None
    last_error: str | None = None
    points: tuple[tuple[str, SyncPoint], ...] = ()  # ('cards', ...), ('withdrawals', ...)

    def point(self, what: str) -> SyncPoint | None:
        return dict(self.points).get(what)

    @property
    def checkpoint(self) -> int | None:
        """Up to date from then (for everything asked: the oldest of them)."""
        return min((p.checkpoint for _, p in self.points), default=None)

    @property
    def gap(self) -> tuple[int, int] | None:
        """Older ones still to get from it (for everything asked, together)."""
        gaps = [p.gap for _, p in self.points if p.gap]
        return (min(g[0] for g in gaps), max(g[1] for g in gaps)) if gaps else None


@dataclass(frozen=True, slots=True)
class BlockedPublisher:
    public_key: str
    name: str                            # their current name when they were blocked ('' if unknown)
    blocked_at: int

    @property
    def npub(self) -> str:
        return to_npub(self.public_key)


@dataclass(frozen=True, slots=True)
class BlockResult:
    blocked: tuple[str, ...]
    purged: tuple[str, ...]              # the publications deleted (their files are for the caller)


class Catalog:
    def __init__(self, path: str | Path) -> None:
        """Open the catalog at `path` (made if there's none), up to date. ':memory:' for one in memory."""
        self.path = str(path)
        self._lock = threading.RLock()
        try:
            if self.path != ':memory:':
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute('PRAGMA busy_timeout = 5000')
            self._db.execute('PRAGMA foreign_keys = ON')
            if self.path != ':memory:':
                self._db.execute('PRAGMA journal_mode = WAL')
                self._db.execute('PRAGMA synchronous = NORMAL')  # (with WAL: safe, and quicker)
            self._migrate()
            self._blocked = {row[0] for row in self._db.execute('SELECT public_key FROM blocked_publishers')}
        except sqlite3.DatabaseError as e:
            raise CatalogError(f'The catalog of shared cards ({self.path}) can\'t be opened ({e}). It only holds '
                               f'what the relays sent, and the publishers you blocked: deleting it starts a new '
                               f'one.') from None

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> Catalog:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # --- receiving --------------------------------------------------------------------------------

    def receive(self, data: Any, *, now: int | None = None, size_limit: int = fmt.DEFAULT_SIZE_LIMIT) -> Received:
        """An event from a relay (parsed JSON, or an Event): a publication or a withdrawal, checked, then kept.
        Rejected ones are reported, not raised."""
        return self.receive_all([data], now=now, size_limit=size_limit)[0]

    def receive_all(self, events: Iterable[Any], *, now: int | None = None,
                    size_limit: int = fmt.DEFAULT_SIZE_LIMIT) -> list[Received]:
        """Several, in one transaction (a relay's page of events)."""
        now = int(time.time()) if now is None else now
        checked: list[Publication | Withdrawal | Received] = []
        for data in events:  # (checked outside the lock: signatures take the most time)
            kind = data.kind if isinstance(data, Event) else data.get('kind') if isinstance(data, dict) else None
            try:
                if kind == fmt.KIND_WITHDRAWAL:
                    checked.append(fmt.check_withdrawal(data, now=now, blocked=self._blocked))
                else:
                    checked.append(fmt.check_publication(data, now=now, size_limit=size_limit, blocked=self._blocked))
            except Rejected as r:
                event_id = data.get('id', '') if isinstance(data, dict) else getattr(data, 'id', '')
                checked.append(Received('rejected', event_id if isinstance(event_id, str) else '', code=r.code))
        with self._write():
            return [item if isinstance(item, Received)
                    else self._add_withdrawal(item, now) if isinstance(item, Withdrawal)
                    else self._add_publication(item, now)
                    for item in checked]

    def add(self, item: Publication | Withdrawal) -> Received:
        """One already checked (the app's own, just made)."""
        now = int(time.time())
        with self._write():
            return self._add_withdrawal(item, now) if isinstance(item, Withdrawal) else self._add_publication(item, now)

    def _add_publication(self, p: Publication, now: int) -> Received:
        if p.publisher_id in self._blocked:  # (blocked between its check and now)
            return Received('rejected', p.event_id, code='blocked')
        added = self._db.execute(
            'INSERT INTO publications (event_id, publisher_key, card_uuid, created_at, published_name, name_folded, '
            'title_folded, content_rating, card_format, language, file_sha256, torrent_infohash, card_size, '
            'event_json, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT (event_id) DO NOTHING',  # (never REPLACE: it deletes, then inserts)
            (p.event_id, p.publisher_id, p.card_uuid, p.created_at, p.published_name, fold(p.published_name),
             fold(p.card_name), p.content_rating, p.card_format, p.language, p.card_sha256, p.torrent_infohash,
             p.card_size, p.event.to_json(), now)).rowcount
        if not added:
            return Received('duplicate', p.event_id)
        self._db.executemany('INSERT INTO publication_tags (event_id, tag) VALUES (?, ?) ON CONFLICT DO NOTHING',
                             [(p.event_id, tag) for tag in p.card_tags])
        return Received('added', p.event_id)

    def _add_withdrawal(self, w: Withdrawal, now: int) -> Received:
        if w.publisher_id in self._blocked:
            return Received('rejected', w.event_id, code='blocked')
        added = self._db.execute(
            'INSERT INTO withdrawals (event_id, publisher_key, created_at, reason, event_json, received_at) '
            'VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (event_id) DO NOTHING',
            (w.event_id, w.publisher_id, w.created_at, w.reason, w.event.to_json(), now)).rowcount
        if not added:
            return Received('duplicate', w.event_id)
        marks = ','.join('?' * len(w.withdrawn))
        live = [row[0] for row in self._db.execute(  # (what it withdraws now, before it's recorded)
            f'SELECT event_id FROM live_publications WHERE publisher_key = ? AND event_id IN ({marks})',
            (w.publisher_id, *w.withdrawn))]
        self._db.executemany('INSERT INTO withdrawn_events (withdrawal_id, event_id) VALUES (?, ?) '
                             'ON CONFLICT DO NOTHING', [(w.event_id, i) for i in w.withdrawn])
        return Received('withdrawal', w.event_id, withdrawn=tuple(live))

    # --- cards --------------------------------------------------------------------------------------

    def cards(self, query: CardQuery = CardQuery()) -> Page[CatalogEntry]:
        """The cards (the latest version of each, unless include_versions), newest first."""
        source, where, args = _conditions(query)
        return self._page(source, where, args, query.limit, query.cursor)

    def tags(self, query: CardQuery = CardQuery(), *, prefix: str = '', limit: int = 100) -> list[TagCount]:
        """The tags of the cards `query` finds (by default, all: the latest version of each), with how many
        of them have each one, the most used first. `prefix`: only the tags starting with it (as it's typed;
        normalized like tags are). With a query's tags, the tags found with them."""
        source, where, args = _conditions(query)
        start = fmt.normalize_tags([prefix])
        if start:
            where = [*where, "t.tag LIKE ? ESCAPE '\\'"]
            args = [*args, f'{_like(start[0])}%']
        sql = (f'SELECT t.tag, COUNT(*) AS cards FROM {source} p JOIN publication_tags t ON t.event_id = p.event_id '
               f'{"WHERE " + " AND ".join(where) if where else ""} GROUP BY t.tag ORDER BY cards DESC, t.tag LIMIT ?')
        with self._lock:
            return [TagCount(row['tag'], row['cards'])
                    for row in self._db.execute(sql, (*args, max(1, min(limit, MAX_PAGE))))]

    def versions(self, publisher_id: str, card_uuid: str, *, include_withdrawn: bool = False) -> list[CatalogEntry]:
        """All the versions of a card, newest first."""
        source = 'publications' if include_withdrawn else 'live_publications'
        with self._lock:
            rows = self._db.execute(f'SELECT {_ENTRY} FROM {source} p {_ENTRY_FROM} '
                                    f'WHERE p.publisher_key = ? AND p.card_uuid = ? '
                                    f'ORDER BY p.created_at DESC, p.event_id', (publisher_id, card_uuid)).fetchall()
            return [self._entry(row) for row in rows]

    def get(self, event_id: str) -> CatalogEntry | None:
        """One publication, withdrawn or not."""
        with self._lock:
            row = self._db.execute(f'SELECT {_ENTRY} FROM publications p {_ENTRY_FROM} WHERE p.event_id = ?',
                                   (event_id,)).fetchone()
            return self._entry(row) if row else None

    def _page(self, source: str, where: list[str], args: list, limit: int, cursor: str | None) -> Page[CatalogEntry]:
        limit = max(1, min(limit, MAX_PAGE))
        if cursor:
            created_at, event_id = _cursor(cursor)
            where = [*where, '(p.created_at < ? OR (p.created_at = ? AND p.event_id > ?))']
            args = [*args, created_at, created_at, event_id]
        sql = (f'SELECT {_ENTRY} FROM {source} p {_ENTRY_FROM} {"WHERE " + " AND ".join(where) if where else ""} '
               f'ORDER BY p.created_at DESC, p.event_id LIMIT ?')
        with self._lock:
            rows = self._db.execute(sql, (*args, limit + 1)).fetchall()
            items = tuple(self._entry(row) for row in rows[:limit])
        more = len(rows) > limit
        last = items[-1].publication if items else None
        return Page(items, f'{last.created_at}:{last.event_id}' if more and last else None)

    def _entry(self, row: sqlite3.Row) -> CatalogEntry:
        withdrawn = WithdrawnInfo(row['withdrawal_id'], row['withdrawn_at'], row['withdrawal_reason']) \
            if row['withdrawal_id'] else None
        return CatalogEntry(publication=fmt.read_publication(Event.from_json(row['event_json'])),
                            publisher_name=row['current_name'] or row['published_name'],
                            is_latest=row['latest_id'] == row['event_id'], withdrawn=withdrawn,
                            received_at=row['received_at'])

    # --- publishers ---------------------------------------------------------------------------------

    def publishers(self, query: PublisherQuery = PublisherQuery()) -> Page[PublisherInfo]:
        """The publishers (with a publication not withdrawn), those who published most recently first."""
        where, args = [], []
        if query.publisher_id:
            where.append('p.publisher_key = ?')
            args.append(query.publisher_id)
        if _words(query.name):
            where.append(_NAMED)
            args += _named(query.name)
        limit = max(1, min(query.limit, MAX_PAGE))
        having = ''
        if query.cursor:
            last, key = _cursor(query.cursor)
            having = 'HAVING MAX(p.created_at) < ? OR (MAX(p.created_at) = ? AND p.publisher_key > ?)'
            cursor_args = [last, last, key]
        else:
            cursor_args = []
        sql = (f'SELECT p.publisher_key, MIN(p.received_at) AS first_seen_at, MAX(p.created_at) AS last_published_at, '
               f'COUNT(DISTINCT p.card_uuid) AS cards FROM live_publications p '
               f'{"WHERE " + " AND ".join(where) if where else ""} GROUP BY p.publisher_key {having} '
               f'ORDER BY last_published_at DESC, p.publisher_key LIMIT ?')
        with self._lock:
            rows = self._db.execute(sql, (*args, *cursor_args, limit + 1)).fetchall()
            items = tuple(self._publisher(row) for row in rows[:limit])
        more = len(rows) > limit
        return Page(items, f'{items[-1].last_published_at}:{items[-1].publisher_id}' if more and items else None)

    def publisher(self, publisher_id: str) -> PublisherInfo | None:
        page = self.publishers(PublisherQuery(publisher_id=publisher_id, limit=1))
        return page.items[0] if page.items else None

    def _publisher(self, row: sqlite3.Row) -> PublisherInfo:
        names = [r['name'] for r in self._db.execute(
            'SELECT name FROM publisher_names WHERE publisher_key = ? ORDER BY last_used DESC, name',
            (row['publisher_key'],))]
        current = self._db.execute(
            'SELECT published_name FROM live_publications WHERE publisher_key = ? ORDER BY created_at DESC, event_id '
            'LIMIT 1', (row['publisher_key'],)).fetchone()[0]
        return PublisherInfo(publisher_id=row['publisher_key'], current_name=current,
                             previous_names=tuple(n for n in names if n != current), cards=row['cards'],
                             first_seen_at=row['first_seen_at'], last_published_at=row['last_published_at'])

    # --- blocking -----------------------------------------------------------------------------------

    def block(self, publisher_ids: Iterable[str]) -> BlockResult:
        """Block these publishers: all they published is deleted from the catalog, and what comes from
        them next is refused. Returns the publications deleted: their files are the caller's to delete."""
        ids = tuple(dict.fromkeys(publisher_ids))
        if not ids:
            return BlockResult((), ())
        now = int(time.time())
        with self._write():
            marks = ','.join('?' * len(ids))
            names = {i: (self._db.execute('SELECT published_name FROM live_publications WHERE publisher_key = ? '
                                          'ORDER BY created_at DESC, event_id LIMIT 1', (i,)).fetchone() or [''])[0]
                     for i in ids}
            purged = tuple(row[0] for row in self._db.execute(
                f'SELECT event_id FROM publications WHERE publisher_key IN ({marks})', ids))
            self._db.execute(f'DELETE FROM publications WHERE publisher_key IN ({marks})', ids)
            self._db.execute(f'DELETE FROM withdrawals WHERE publisher_key IN ({marks})', ids)
            self._db.executemany('INSERT INTO blocked_publishers (public_key, blocked_at, name) VALUES (?, ?, ?) '
                                 'ON CONFLICT DO NOTHING', [(i, now, names[i]) for i in ids])
            self._blocked |= set(ids)
        return BlockResult(ids, purged)

    def unblock(self, publisher_ids: Iterable[str]) -> None:
        """Their publications are accepted again: those already sent come back with a discovery of what
        they published (the relays won't send them again otherwise)."""
        ids = tuple(dict.fromkeys(publisher_ids))
        with self._write():
            self._db.executemany('DELETE FROM blocked_publishers WHERE public_key = ?', [(i,) for i in ids])
            self._blocked -= set(ids)

    def blocked(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(row[0] for row in self._db.execute(
                'SELECT public_key FROM blocked_publishers ORDER BY blocked_at DESC, public_key'))

    def blocked_publishers(self) -> tuple[BlockedPublisher, ...]:
        """Who's blocked, the most recently first, with the name they had."""
        with self._lock:
            rows = self._db.execute('SELECT public_key, name, blocked_at FROM blocked_publishers '
                                    'ORDER BY blocked_at DESC, public_key').fetchall()
            return tuple(BlockedPublisher(row['public_key'], row['name'], row['blocked_at']) for row in rows)

    def is_blocked(self, publisher_id: str) -> bool:
        return publisher_id in self._blocked

    # --- relays -------------------------------------------------------------------------------------

    def relays(self) -> tuple[RelayInfo, ...]:
        with self._lock:
            rows = self._db.execute('SELECT * FROM relays ORDER BY added_at, url').fetchall()
            return tuple(self._relay(row) for row in rows)

    def relay(self, url: str) -> RelayInfo | None:
        with self._lock:
            row = self._db.execute('SELECT * FROM relays WHERE url = ?', (normalize_relay_url(url),)).fetchone()
            return self._relay(row) if row else None

    def _relay(self, row: sqlite3.Row) -> RelayInfo:
        points = tuple((p['what'], SyncPoint(p['checkpoint'], (p['gap_since'], p['gap_until'])
                                             if p['gap_until'] is not None else None))
                       for p in self._db.execute('SELECT * FROM relay_sync WHERE relay_url = ? ORDER BY what',
                                                 (row['url'],)))
        return RelayInfo(url=row['url'], read=bool(row['read']), write=bool(row['write']), added_at=row['added_at'],
                         last_attempt_at=row['last_attempt_at'], last_success_at=row['last_success_at'],
                         last_error=row['last_error'], points=points)

    def add_relay(self, url: str, *, read: bool = True, write: bool = True) -> RelayInfo:
        """A relay to discover from (read) and publish to (write). Its address as written by
        normalize_relay_url(); ValueError if it isn't a relay's. Already there: its settings change."""
        url = normalize_relay_url(url)
        with self._write():
            self._db.execute('INSERT INTO relays (url, read, write, added_at) VALUES (?, ?, ?, ?) '
                             'ON CONFLICT (url) DO UPDATE SET read = excluded.read, write = excluded.write',
                             (url, int(read), int(write), int(time.time())))
        return self.relay(url)

    def remove_relay(self, url: str) -> None:
        """No longer used; what came from it stays. Added again, discovery there starts over."""
        with self._write():
            self._db.execute('DELETE FROM relays WHERE url = ?', (normalize_relay_url(url),))

    def save_sync(self, url: str, points: dict[str, SyncPoint], *, at: int) -> None:
        """A discovery on that relay went to its end: how far it is now, for each kind of event."""
        with self._write():
            if not self._db.execute('UPDATE relays SET last_attempt_at = ?, last_success_at = ?, last_error = NULL '
                                    'WHERE url = ?', (at, at, url)).rowcount:
                return  # (removed meanwhile)
            self._db.executemany(
                'INSERT INTO relay_sync (relay_url, what, checkpoint, gap_since, gap_until) VALUES (?, ?, ?, ?, ?) '
                'ON CONFLICT (relay_url, what) DO UPDATE SET checkpoint = excluded.checkpoint, '
                'gap_since = excluded.gap_since, gap_until = excluded.gap_until',
                [(url, what, p.checkpoint, *(p.gap or (None, None))) for what, p in points.items()])

    def sync_failed(self, url: str, error: str, *, at: int) -> None:
        """A discovery on that relay didn't finish: how far it is doesn't change."""
        with self._write():
            self._db.execute('UPDATE relays SET last_attempt_at = ?, last_error = ? WHERE url = ?',
                             (at, error[:500], url))

    # --- the database -------------------------------------------------------------------------------

    @contextmanager
    def _write(self) -> Iterator[None]:
        """A transaction: all of it, or none of it."""
        with self._lock:
            self._db.execute('BEGIN IMMEDIATE')
            try:
                yield
            except BaseException:
                self._db.execute('ROLLBACK')
                raise
            self._db.execute('COMMIT')

    def _migrate(self) -> None:
        version = self._db.execute('PRAGMA user_version').fetchone()[0]
        self.created = version == 0  # (a new catalog: the app adds its default relays)
        if version > len(MIGRATIONS):
            raise CatalogError(f'The catalog of shared cards ({self.path}) was made by a newer version of the app: '
                               f'update it, or delete the catalog to start a new one.')
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            try:  # (each one whole, or not at all)
                self._db.executescript(f'BEGIN IMMEDIATE; {script}; PRAGMA user_version = {number}; COMMIT;')
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute('ROLLBACK')
                raise


# A publication's row, with what's worked out about it: its publisher's current name, its card's latest
# version, and its withdrawal (the first, if several).
_ENTRY = """p.event_id, p.published_name, p.event_json, p.received_at,
    (SELECT c.published_name FROM live_publications c WHERE c.publisher_key = p.publisher_key
     ORDER BY c.created_at DESC, c.event_id LIMIT 1) AS current_name,
    (SELECT l.event_id FROM live_publications l WHERE l.publisher_key = p.publisher_key AND l.card_uuid = p.card_uuid
     ORDER BY l.created_at DESC, l.event_id LIMIT 1) AS latest_id,
    w.event_id AS withdrawal_id, w.created_at AS withdrawn_at, w.reason AS withdrawal_reason"""
_ENTRY_FROM = """
    LEFT JOIN withdrawals w ON w.event_id = (
        SELECT d.event_id FROM withdrawn_events x JOIN withdrawals d ON d.event_id = x.withdrawal_id
        WHERE x.event_id = p.event_id AND d.publisher_key = p.publisher_key
        ORDER BY d.created_at, d.event_id LIMIT 1)"""


def normalize_relay_url(url: str) -> str:
    """A relay's address, written one way: wss://relay.example.com (ws:// too, for a relay on this computer
    or network). ValueError if it isn't one."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ('wss', 'ws') or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError(f'not a relay\'s address (wss://...): {url!r}')
    try:
        port = parts.port
    except ValueError:
        raise ValueError(f'not a relay\'s address (its port): {url!r}') from None
    host = parts.hostname.lower()
    host = f'[{host}]' if ':' in host else host
    path = parts.path.rstrip('/')
    text = f'{scheme}://{host}{f":{port}" if port else ""}{path}{f"?{parts.query}" if parts.query else ""}'
    if len(text) > 256 or any(c.isspace() for c in text):
        raise ValueError(f'not a relay\'s address: {url!r}')
    return text


def fold(text: str) -> str:
    """Text as it's searched: without case or accents ('Café' → 'cafe')."""
    decomposed = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in decomposed if not unicodedata.combining(c)).casefold()


# A publisher who used a name: their current one or a previous one (a name as JSON list of folded words).
_NAMED = """EXISTS (SELECT 1 FROM publisher_names n WHERE n.publisher_key = p.publisher_key
    AND NOT EXISTS (SELECT 1 FROM json_each(?) w WHERE instr(n.name_folded, w.value) = 0))"""


def _conditions(query: CardQuery) -> tuple[str, list[str], list]:
    """Where a CardQuery's cards are (the view), and the conditions they meet, for SQL with `p` as the card."""
    where: list[str] = []
    args: list = []
    if query.publisher_id:
        where.append('p.publisher_key = ?')
        args.append(query.publisher_id)
    if query.card_uuid:
        where.append('p.card_uuid = ?')
        args.append(query.card_uuid)
    for word in _words(query.card_name):
        where.append("p.title_folded LIKE ? ESCAPE '\\'")
        args.append(f'%{_like(word)}%')
    if _words(query.publisher_name):
        where.append(_NAMED)
        args += _named(query.publisher_name)
    tags = fmt.normalize_tags(query.tags)
    if tags:  # (the tags' index first: the publications with them, then the rest of the conditions)
        marks = ','.join('?' * len(tags))
        enough = len(tags) if query.match_all_tags else 1
        where.append(f'p.event_id IN (SELECT t.event_id FROM publication_tags t WHERE t.tag IN ({marks}) '
                     f'GROUP BY t.event_id HAVING COUNT(*) >= ?)')
        args += [*tags, enough]
    excluded = fmt.normalize_tags(query.exclude_tags)
    if excluded:
        marks = ','.join('?' * len(excluded))
        where.append(f'NOT EXISTS (SELECT 1 FROM publication_tags x WHERE x.event_id = p.event_id '
                     f'AND x.tag IN ({marks}))')
        args += excluded
    for column, value in (('card_format', query.card_format), ('content_rating', query.content_rating),
                          ('language', query.language)):
        if value:
            where.append(f'p.{column} = ?')
            args.append(value)
    if query.published_since is not None:
        where.append('p.created_at >= ?')
        args.append(query.published_since)
    if query.published_until is not None:
        where.append('p.created_at <= ?')
        args.append(query.published_until)
    return ('live_publications' if query.include_versions else 'latest_publications'), where, args


def _named(name: str) -> list[str]:
    return [json.dumps(_words(name))]


def _words(text: str | None) -> list[str]:
    return fold(text or '').split()


def _like(word: str) -> str:
    """A word as a LIKE pattern matches it, its % and _ included."""
    return re.sub(r'([\\%_])', r'\\\1', word)


def _cursor(cursor: str) -> tuple[int, str]:
    number, _, key = cursor.partition(':')
    if not number.isdigit() or not re.fullmatch(r'[0-9a-f]{64}', key):
        raise ValueError(f'not a cursor: {cursor!r}')
    return int(number), key
