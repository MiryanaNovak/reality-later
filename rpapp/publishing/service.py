"""Card sharing, for the app: its catalog, discovery in the background, publishing, and what the pages need.

One per data folder, made by the app at startup (ui/app.py), if sharing can run on this computer
(publishing.runtime_problem()). In <data folder>/sharing/:

    catalog.sqlite3     the catalog (catalog.py); a new one starts with DEFAULT_RELAYS (Settings changes them)
    published/          the files of the cards published from here, named <sha256>.<extension>, shared by
                        BitTorrent while the app runs (seeder.py)

The publisher's key is elsewhere: in the settings folder (identity.py).

Discovery runs as a background task, one at a time: pages ask for one (discover()), show its progress
(progress, running), and go on with what's there meanwhile. The catalog's calls run in a thread (call()),
and so does what reads or hashes files: the pages don't wait on the disk.

Publishing a card (publish()): its file is kept in published/ and shared, its announcement signed and sent
to the relays written to (deliver()), each relay's answer kept. The same card again, unchanged: the same
announcement, sent again where it hasn't arrived (no new version). Withdrawing (withdraw()): a withdrawal
sent the same way, and the file no longer shared.

**While the format is a draft** (docs/card-announcements.md), PUBLIC_PUBLISHING is False: events only go
to relays on this computer or its network (tools/test_relay.py makes one); the others are "held".
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar
from urllib.parse import urlsplit

from . import announcements as fmt
from . import identity as keys
from .announcements import Publication, PublicationDraft
from .catalog import Catalog, CatalogEntry, Delivery
from .discovery import Discovery, DiscoveryResult
from .identity import Identity
from .nostr import Event
from .relay import Relay, RelayError
from .seeder import DEFAULT_TRACKERS, TorrentSession, TorrentStatus
from .torrents import card_file

log = logging.getLogger(__name__)

# Tried from Paris in November 2026 (tools/try_relays.py): they answer, keep torrents, accept publishing,
# and delete what's withdrawn. Free, from different operators: none of them is needed, several are better.
DEFAULT_RELAYS = (
    'wss://relay.damus.io',
    'wss://relay.primal.net',
    'wss://relay.nostr.net',
    'wss://nostr.oxtr.dev',
    'wss://offchain.pub',
    'wss://nostr.mom',
)
DUE_AFTER = 3600  # seconds: opening the shared cards looks for new ones when the last look is older

# False while the format is a draft: nothing goes to public relays (see the module's notes).
PUBLIC_PUBLISHING = False
HELD = ('Not sent: while card sharing\'s format is a draft, cards only go to relays on this computer or its '
        'network.')

T = TypeVar('T')


class SharingError(Exception):
    """Publishing (or withdrawing) can't be done: the message says why, in plain words."""


@dataclass(frozen=True, slots=True)
class Published:
    action: Literal['new_card', 'new_version', 'resumed']  # resumed: the same as one published before
    publication: Publication
    deliveries: tuple[Delivery, ...]


class Sharing:
    def __init__(self, data_dir: str | Path, *, key_file: str | Path | None = None,
                 default_relays: Iterable[str] = DEFAULT_RELAYS, session: TorrentSession | None = None,
                 public_publishing: bool = PUBLIC_PUBLISHING) -> None:
        """`key_file`: the publisher's key (identity.key_path(<settings folder>); in the sharing folder if not
        given: tests). `session`: BitTorrent's (the tests give one on 127.0.0.1)."""
        self.folder = Path(data_dir) / 'sharing'
        self.published = self.folder / 'published'
        self.key_file = Path(key_file) if key_file else self.folder / keys.KEY_FILE_NAME
        self.public_publishing = public_publishing
        self.catalog = Catalog(self.folder / 'catalog.sqlite3')
        if self.catalog.created:
            for url in default_relays:
                self.catalog.add_relay(url)
        self.discovery = Discovery(self.catalog)
        self.session = session or TorrentSession()
        self.seeding_problems: dict[str, str] = {}  # event id → why its file isn't shared
        self.progress: dict[str, int] = {}       # events received from each relay, by the discovery running
        self.result: DiscoveryResult | None = None
        self.error = ''                          # the last discovery's, if it failed as a whole
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self._task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()

    async def call(self, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """A catalog call (or any that waits on the disk), in a thread."""
        return await asyncio.to_thread(function, *args, **kwargs)

    # --- discovery --------------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def due(self, *, now: float | None = None) -> bool:
        """Whether it's time to look for new cards: never looked since the app started, or not for a while."""
        now = time.time() if now is None else now
        return not self.running and (self.finished_at is None or now - self.finished_at > DUE_AFTER)

    def discover(self, *, full: bool = False) -> asyncio.Task:
        """Look for new cards on the relays, in the background; the one already running, if there's one."""
        if not self.running:
            self.progress = {}
            self.started_at, self.error = time.time(), ''
            self._task = asyncio.create_task(self._discover(full))
        return self._task

    async def _discover(self, full: bool) -> DiscoveryResult | None:
        try:
            self.result = await self.discovery.discover(full=full, on_progress=self._progressed)
            return self.result
        except Exception as e:  # (a bug, or the disk: said on the page rather than lost)
            log.exception('Looking for shared cards failed')
            self.error = str(e) or type(e).__name__
            return None
        finally:
            self.finished_at = time.time()

    def _progressed(self, url: str, received: int) -> None:
        self.progress[url] = received

    # --- blocking ---------------------------------------------------------------------------------

    async def block(self, publisher_id: str) -> int:
        """Block a publisher: their cards leave the catalog. How many publications went."""
        return len((await self.call(self.catalog.block, [publisher_id])).purged)

    async def unblock(self, publisher_id: str) -> None:
        """Unblock a publisher, and get their cards again from the relays, in the background."""
        await self.call(self.catalog.unblock, [publisher_id])
        task = asyncio.create_task(self.discovery.discover_publisher(publisher_id))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # --- the publisher's key ----------------------------------------------------------------------

    def identity(self) -> Identity | None:
        """This computer's publisher key, if there's one (IdentityError if its file is damaged)."""
        return keys.load_identity(self.key_file)

    def create_identity(self, *, replace: bool = False) -> Identity:
        return keys.create_identity(self.key_file, replace=replace)

    def import_identity(self, text: str, *, replace: bool = False) -> Identity:
        return keys.import_identity(self.key_file, text, replace=replace)

    # --- publishing ---------------------------------------------------------------------------------

    async def publish(self, draft: PublicationDraft, content: bytes, *, card_uuid: str) -> Published:
        """Publish a card file (a new card, or a new version of the one with that UUID), and share it.
        The same as one published already: that one, sent again where it hasn't arrived. Rejected if the
        card breaks a rule of the format (a title too long...); SharingError if it can't be done."""
        me = self.identity()
        if me is None:
            raise SharingError('You need a publisher key to publish: make one in Settings › Sharing cards.')
        if problem := torrent_problem():
            raise SharingError(f'Cards can\'t be shared from this computer: {problem}.')
        file = await asyncio.to_thread(card_file, content)
        versions = await self.call(self.catalog.versions, me.public_key, card_uuid, include_withdrawn=True)
        # (a new version is always the newest: after the last one, even published within the same second)
        created_at = max([int(time.time())] + [v.publication.created_at + 1 for v in versions])
        made = fmt.build_publication(draft, file, card_uuid=card_uuid, secret=me.secret, trackers=DEFAULT_TRACKERS,
                                     created_at=created_at)
        versions = [v for v in versions if v.withdrawn is None]
        same = next((v.publication for v in versions if _same(v.publication.event, made.event)), None)
        publication = same or made
        action = 'resumed' if same else 'new_version' if versions else 'new_card'

        await asyncio.to_thread(self._keep_file, file.name, file.sha256, content)
        await self.call(self.catalog.save_torrent, file.infohash, file.sha256, file.name, file.metainfo())
        if same is None:
            await self.call(self.catalog.add, publication)
        self.session.seed(file.metainfo(), self.published, trackers=publication.trackers)
        await self.call(self.catalog.set_seeding, publication.event_id, file.infohash, True)
        self.seeding_problems.pop(publication.event_id, None)
        return Published(action, publication, await self.deliver(publication.event))

    async def withdraw(self, event_ids: Iterable[str], *, reason: str = '') -> tuple[Delivery, ...]:
        """Withdraw publications of this computer's key: no longer shared from here, a withdrawal sent to
        the relays (those who have the cards keep them)."""
        me = self.identity()
        if me is None:
            raise SharingError('Withdrawing needs the key that published: there\'s none here.')
        ids = []
        for event_id in dict.fromkeys(event_ids):
            entry = await self.call(self.catalog.get, event_id)
            if entry is None or entry.publication.publisher_id != me.public_key:
                raise SharingError('Only your own publications can be withdrawn.')
            ids.append(event_id)
        withdrawal = fmt.build_withdrawal(ids, secret=me.secret, reason=reason)
        await self.call(self.catalog.add, withdrawal)
        for event_id in ids:
            await self.stop_sharing(event_id)
        return await self.deliver(withdrawal.event)

    async def deliver(self, event: Event) -> tuple[Delivery, ...]:
        """Send one of this computer's events to the relays written to, where it hasn't arrived yet, at the
        same time; each answer kept. A relay that can't be reached: pending, for later."""
        done = {d.relay_url for d in await self.call(self.catalog.deliveries, event.id) if d.state == 'accepted'}
        relays = [r.url for r in await self.call(self.catalog.relays) if r.write and r.url not in done]

        async def one(url: str) -> None:
            if not self.public_publishing and not is_local(url):
                await self.call(self.catalog.record_delivery, event.id, url, 'held', message=HELD, attempted=False)
                return
            try:
                async with Relay(url, timeout=self.discovery.timeout) as relay:
                    accepted, said = await relay.publish(event)
                state = 'accepted' if accepted else 'refused'
            except RelayError as e:
                state, said = 'pending', str(e)
            await self.call(self.catalog.record_delivery, event.id, url, state, message=said)

        await asyncio.gather(*(one(url) for url in relays))
        return await self.call(self.catalog.deliveries, event.id)

    async def send_again(self, event_id: str) -> tuple[Delivery, ...]:
        """Send a publication (or withdrawal) of this computer's again, where it hasn't arrived."""
        entry = await self.call(self.catalog.get, event_id)
        if entry is not None and entry.withdrawn is not None:
            raise SharingError('It was withdrawn: it isn\'t sent again (its withdrawal can be).')
        event = entry.publication.event if entry else None
        if event is None:
            withdrawal = await self.call(self.catalog.withdrawal, event_id)
            event = withdrawal.event if withdrawal else None
        if event is None:
            raise SharingError('It isn\'t in the catalog any more.')
        return await self.deliver(event)

    # --- sharing the files --------------------------------------------------------------------------

    async def resume_sharing(self) -> None:
        """When the app starts: share again the files of the publications shared when it stopped. A file
        missing or changed isn't replaced: seeding_problems says so."""
        items = await self.call(self.catalog.seeding, enabled_only=True)
        if items and (problem := torrent_problem()):
            for item in items:
                self.seeding_problems[item.event_id] = f'Its file can\'t be shared from this computer: {problem}.'
            return
        for item in items:
            path = self.published / item.file_name
            expected = item.file_name.split('.')[0]
            if not await asyncio.to_thread(_has_hash, path, expected):
                self.seeding_problems[item.event_id] = (f'Its file is missing or changed ({path}): publish the card '
                                                        f'again to share it.')
                continue
            metainfo = await self.call(self.catalog.metainfo, item.infohash)
            entry = await self.call(self.catalog.get, item.event_id)
            self.session.seed(metainfo, self.published, trackers=entry.publication.trackers if entry else ())

    async def stop_sharing(self, event_id: str) -> None:
        """No longer share a publication's file from here; and the file goes when no other one needs it."""
        found = next((s for s in await self.call(self.catalog.seeding) if s.event_id == event_id), None)
        if found is None:
            return
        await self.call(self.catalog.set_seeding, event_id, found.infohash, False)
        still = {s.infohash for s in await self.call(self.catalog.seeding, enabled_only=True)}
        if found.infohash not in still:
            self.session.stop(found.infohash)
        if found.file_name not in await self.call(self.catalog.files_in_use):
            await asyncio.to_thread((self.published / found.file_name).unlink, missing_ok=True)
        self.seeding_problems.pop(event_id, None)

    def sharing_status(self, entry: CatalogEntry) -> TorrentStatus | str | None:
        """How a publication's file is shared from here: its torrent's status, why it isn't (a problem), or
        None (not shared from here)."""
        p = entry.publication
        if p.event_id in self.seeding_problems:
            return self.seeding_problems[p.event_id]
        return self.session.status(p.torrent_infohash)

    def _keep_file(self, name: str, sha256: str, content: bytes) -> None:
        """The file, in published/ under its name (already there: checked; written atomically)."""
        path = self.published / name
        if _has_hash(path, sha256):
            return
        self.published.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.published, prefix=name, suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(content)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        if not _has_hash(path, sha256):
            raise SharingError(f'The card\'s file couldn\'t be kept ({path}): it isn\'t what was written.')

    # --- the end ----------------------------------------------------------------------------------

    async def close(self) -> None:
        """When the app stops: discovery stops (what it got is kept), and the catalog is closed."""
        for task in [self._task, *self._background]:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:  # (cancelled, or failed: either way, over)
                    pass
        self.session.close()
        self.catalog.close()


def is_local(url: str) -> bool:
    """A relay on this computer or its network (by its address: localhost, a private IP, a .local name)."""
    host = (urlsplit(url).hostname or '').lower()
    if host == 'localhost' or host.endswith('.local') or host.endswith('.localhost'):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def torrent_problem() -> str:
    from .seeder import torrent_problem as problem
    return problem()


def _same(a: Event, b: Event) -> bool:
    """The same publication, but for its date and signature: same text, same tags (trackers aside)."""
    def tags(event: Event) -> list:
        return [t for t in event.tags if t[0] != 'tracker']
    return a.content == b.content and tags(a) == tags(b) and a.pubkey == b.pubkey


def _has_hash(path: Path, sha256: str) -> bool:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == sha256
    except OSError:
        return False
