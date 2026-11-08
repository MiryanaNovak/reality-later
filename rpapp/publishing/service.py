"""Card sharing, for the app: its catalog, discovery in the background, publishing, and what the pages need.

One per data folder, made by the app at startup (ui/app.py), if sharing can run on this computer
(publishing.runtime_problem()). In <data folder>/sharing/:

    catalog.sqlite3     the catalog (catalog.py); a new one starts with DEFAULT_RELAYS (Settings changes them)
    files/              the card files this computer shares by BitTorrent while the app runs (seeder.py): those
                        it published, and those it downloaded (unless asked not to), named <sha256>.<extension>
    downloading/        files on their way (emptied when the app starts)

The publisher's key is elsewhere: in the settings folder (identity.py).

Discovery runs as a background task, one at a time: pages ask for one (discover()), show its progress
(progress, running), and go on with what's there meanwhile. The catalog's calls run in a thread (call()),
and so does what reads or hashes files: the pages don't wait on the disk.

Publishing a card (publish()): its file is kept in files/ and shared, its announcement signed and sent
to the relays written to (deliver()), each relay's answer kept. The same card again, unchanged: the same
announcement, sent again where it hasn't arrived (no new version). Withdrawing (withdraw()): a withdrawal
sent the same way, and the file no longer shared.

Downloading one (download(), in the background like discovery): its file from those who share it, checked
(the torrent's description before anything else, then the file's SHA-256 and torrent: the announced
ones), then added as a new character (`importer`, given by the app), and shared from here in turn while
`share_downloads`. Withdrawn, or its publisher blocked: no longer shared (and the file goes); the character
stays.

**While the format is a draft** (docs/card-announcements.md), PUBLIC_PUBLISHING is False: events only go
to relays on this computer or its network (tools/test_relay.py makes one); the others are "held".
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

from . import announcements as fmt
from . import identity as keys
from .announcements import Publication, PublicationDraft
from .catalog import Catalog, CatalogEntry, Delivery, Download
from .discovery import Discovery, DiscoveryResult
from .identity import Identity
from .nostr import Event
from .relay import Relay, RelayError, is_local
from .seeder import DEFAULT_TRACKERS, FetchError, FetchProgress, TorrentSession, TorrentStatus
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
WATCH = 5.0       # seconds: how often what BitTorrent says is read (TorrentSession.poll())

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


@dataclass
class Fetch:
    """A download, while it runs and once it's over (until the app stops)."""
    event_id: str
    stage: Literal['looking', 'downloading', 'adding', 'done', 'failed'] = 'looking'
    progress: float = 0.0     # 0 to 1, while downloading
    peers: int = 0
    error: str = ''           # failed: why
    character_id: str = ''    # done: the character it became
    message: str = ''         # done: what the import said
    languages: tuple[str, ...] = ()  # the user's, best first: for the import (a card's notes in several)
    task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.stage not in ('done', 'failed')


# Adds a card file as a character: (file name, content, the user's languages) → (character id, what was
# done, what its card holds as added: its fingerprint, kept to tell whether it's edited since). Called on the
# event loop, like the app's own Import.
Importer = Callable[[str, bytes, tuple[str, ...]], tuple[str, str, str]]


class Sharing:
    def __init__(self, data_dir: str | Path, *, key_file: str | Path | None = None,
                 default_relays: Iterable[str] = DEFAULT_RELAYS, session: TorrentSession | None = None,
                 public_publishing: bool = PUBLIC_PUBLISHING) -> None:
        """`key_file`: the publisher's key (identity.key_path(<settings folder>); in the sharing folder if not
        given: tests). `session`: BitTorrent's (the tests give one on 127.0.0.1)."""
        self.folder = Path(data_dir) / 'sharing'
        self.files = self.folder / 'files'
        self.downloading = self.folder / 'downloading'
        if (self.folder / 'published').is_dir() and not self.files.exists():  # (its name before downloading)
            (self.folder / 'published').rename(self.files)
        self.key_file = Path(key_file) if key_file else self.folder / keys.KEY_FILE_NAME
        self.public_publishing = public_publishing
        self.catalog = Catalog(self.folder / 'catalog.sqlite3')
        if self.catalog.created:
            for url in default_relays:
                self.catalog.add_relay(url)
        self.discovery = Discovery(self.catalog)
        self.session = session or TorrentSession()
        self.seeding_problems: dict[str, str] = {}  # event id → why its file isn't shared
        self.importer: Importer | None = None    # adds a card file as a character: the app gives it
        self.share_downloads = True              # Settings › Sharing cards (set_share_downloads())
        self.fetches: dict[str, Fetch] = {}      # event id → its download, this time the app runs
        self._files_lock = asyncio.Lock()        # (files/ and what's shared from it change one at a time)
        self.progress: dict[str, int] = {}       # events received from each relay, by the discovery running
        self.result: DiscoveryResult | None = None
        self.error = ''                          # the last discovery's, if it failed as a whole
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self._task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()
        self._watcher: asyncio.Task | None = None

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
            await self._tidy_sharing()  # (withdrawn since: no longer shared)
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
        """Block a publisher: their cards leave the catalog, and their files aren't shared from here any more
        (characters had from them stay). How many publications went."""
        gone = len((await self.call(self.catalog.block, [publisher_id])).purged)
        await self._tidy_sharing()
        return gone

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

    def own_identities(self) -> list[Identity]:
        """This computer's publisher keys: the one in use, then those it replaced (kept beside it). What
        they published is "yours": listed apart from the others', and withdrawn with the key that signed it."""
        found: list[Identity] = []
        try:
            if (current := self.identity()) is not None:
                found.append(current)
        except keys.IdentityError:  # (damaged: the Settings page says so)
            pass
        for old in keys.replaced_identities(self.key_file):
            if all(old.public_key != f.public_key for f in found):
                found.append(old)
        return found

    def own_keys(self) -> tuple[str, ...]:
        return tuple(i.public_key for i in self.own_identities())

    def own_versions(self, card_uuid: str) -> list[CatalogEntry]:
        """A card's versions published with your keys (the one in use, or one it replaced), the newest
        first, the withdrawn ones too. (In a thread: it reads the catalog.)"""
        found = [v for key in self.own_keys()
                 for v in self.catalog.versions(key, card_uuid, include_withdrawn=True)] if card_uuid else []
        return sorted(found, key=lambda v: (-v.publication.created_at, v.publication.event_id))

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

        async with self._files_lock:
            await asyncio.to_thread(self._keep_file, file.name, file.sha256, content)
            await self.call(self.catalog.save_torrent, file.infohash, file.sha256, file.name, file.metainfo())
            if same is None:
                await self.call(self.catalog.add, publication)
            self.session.seed(file.metainfo(), self.files, trackers=publication.trackers)
            await self.call(self.catalog.set_seeding, publication.event_id, file.infohash, True)
        self.seeding_problems.pop(publication.event_id, None)
        return Published(action, publication, await self.deliver(publication.event))

    async def withdraw(self, event_ids: Iterable[str], *, reason: str = '') -> tuple[Delivery, ...]:
        """Withdraw publications of this computer's keys (the one in use, or one it replaced): no longer
        shared from here, a withdrawal signed by the key that published them sent to the relays (those who
        have the cards keep them)."""
        own = {i.public_key: i for i in self.own_identities()}
        by_key: dict[str, list[str]] = {}
        for event_id in dict.fromkeys(event_ids):
            entry = await self.call(self.catalog.get, event_id)
            if entry is None or entry.publication.publisher_id not in own:
                raise SharingError('Only your own publications can be withdrawn: those published with a key of '
                                   'this computer.')
            by_key.setdefault(entry.publication.publisher_id, []).append(event_id)
        deliveries: list[Delivery] = []
        for key, ids in by_key.items():  # (one withdrawal per key: each signs its own)
            withdrawal = fmt.build_withdrawal(ids, secret=own[key].secret, reason=reason)
            await self.call(self.catalog.add, withdrawal)
            for event_id in ids:
                await self.stop_sharing(event_id)
            deliveries += await self.deliver(withdrawal.event)
        return tuple(deliveries)

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
        """When the app starts: share again the files shared when it stopped, but those withdrawn since, and
        delete what's left of downloads stopped midway. A file missing or changed isn't replaced:
        seeding_problems says so."""
        await asyncio.to_thread(shutil.rmtree, self.downloading, True)
        if self._watcher is None:
            self._watcher = asyncio.create_task(self._watch())
        await self._tidy_sharing()
        items = await self.call(self.catalog.seeding, enabled_only=True)
        if items and (problem := torrent_problem()):
            for item in items:
                self.seeding_problems[item.event_id] = f'Its file can\'t be shared from this computer: {problem}.'
            return
        for item in items:
            path = self.files / item.file_name
            expected = item.file_name.split('.')[0]
            if not await asyncio.to_thread(_has_hash, path, expected):
                self.seeding_problems[item.event_id] = (f'Its file is missing or changed ({path}): publish the card '
                                                        f'again, or download it again, to share it.')
                continue
            metainfo = await self.call(self.catalog.metainfo, item.infohash)
            entry = await self.call(self.catalog.get, item.event_id)
            self.session.seed(metainfo, self.files, trackers=entry.publication.trackers if entry else ())

    async def stop_sharing(self, event_id: str) -> None:
        """No longer share a publication's file from here; and the file goes when no other one needs it."""
        async with self._files_lock:
            found = next((s for s in await self.call(self.catalog.seeding) if s.event_id == event_id), None)
            if found is None:
                return
            await self.call(self.catalog.set_seeding, event_id, found.infohash, False)
            still = {s.infohash for s in await self.call(self.catalog.seeding, enabled_only=True)}
            if found.infohash not in still:
                self.session.stop(found.infohash)
            if found.file_name not in await self.call(self.catalog.files_in_use):
                await asyncio.to_thread((self.files / found.file_name).unlink, missing_ok=True)
        self.seeding_problems.pop(event_id, None)

    async def _tidy_sharing(self) -> None:
        """No longer share the files of publications withdrawn, or gone from the catalog (their publisher
        blocked); and delete the files nothing shares any more."""
        async with self._files_lock:
            for item in await self.call(self.catalog.seeding, enabled_only=True):
                entry = await self.call(self.catalog.get, item.event_id)
                if entry is not None and entry.withdrawn is not None:
                    await self.call(self.catalog.set_seeding, item.event_id, item.infohash, False)
                    self.seeding_problems.pop(item.event_id, None)
            # (a blocked publisher's publications are gone, and with them, whether their files were shared)
            still = {s.infohash for s in await self.call(self.catalog.seeding, enabled_only=True)}
            for infohash in self.session.sharing():
                if infohash not in still:
                    self.session.stop(infohash)
            in_use = await self.call(self.catalog.files_in_use)
            await asyncio.to_thread(self._delete_unused, in_use)

    def _delete_unused(self, in_use: set[str]) -> None:
        if not self.files.is_dir():
            return
        for path in self.files.iterdir():
            if _CARD_FILE.fullmatch(path.name) and path.name not in in_use:
                path.unlink(missing_ok=True)

    def sharing_status(self, entry: CatalogEntry) -> TorrentStatus | str | None:
        """How a publication's file is shared from here: its torrent's status, why it isn't (a problem), or
        None (not shared from here)."""
        p = entry.publication
        if p.event_id in self.seeding_problems:
            return self.seeding_problems[p.event_id]
        return self.session.status(p.torrent_infohash)

    def _keep_file(self, name: str, sha256: str, content: bytes) -> None:
        """The file, in files/ under its name (already there: checked; written atomically)."""
        path = self.files / name
        if _has_hash(path, sha256):
            return
        self.files.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.files, prefix=name, suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(content)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        if not _has_hash(path, sha256):
            raise SharingError(f'The card\'s file couldn\'t be kept ({path}): it isn\'t what was written.')

    # --- downloading ----------------------------------------------------------------------------------

    def download(self, event_id: str, *, languages: Iterable[str] = ()) -> Fetch:
        """Download a publication's card and add it as a new character, in the background: its Fetch says
        how far it is (fetches). The one running already, if there's one. `languages`: the user's, best
        first (the browser's: read when it's asked, since there's no page to ask once it's there)."""
        fetch = self.fetches.get(event_id)
        if fetch is not None and fetch.running:
            return fetch
        if self.importer is None:
            raise SharingError('Cards can\'t be added as characters here.')
        fetch = Fetch(event_id, languages=tuple(languages))
        fetch.task = asyncio.create_task(self._download(fetch))
        self.fetches[event_id] = fetch
        return fetch

    async def _download(self, fetch: Fetch) -> None:
        try:
            await self._fetch(fetch)
        except (SharingError, FetchError) as e:
            fetch.stage, fetch.error = 'failed', str(e)
        except asyncio.CancelledError:
            fetch.stage, fetch.error = 'failed', 'Stopped: the app was closing.'
            raise
        except Exception as e:  # (a bug, or the disk: said on the page rather than lost)
            log.exception('Downloading %s failed', fetch.event_id)
            fetch.stage, fetch.error = 'failed', f'It couldn\'t be downloaded: {e}'

    async def _fetch(self, fetch: Fetch) -> None:
        entry = await self.call(self.catalog.get, fetch.event_id)
        if entry is None:
            raise SharingError('It isn\'t in the catalog any more.')
        if entry.withdrawn is not None:
            raise SharingError('Its publisher withdrew it: it can no longer be downloaded.')
        p = entry.publication
        kept = self.files / p.file_name
        got: Path | None = None
        if await asyncio.to_thread(_has_hash, kept, p.card_sha256):  # (here already: shared from here)
            content = await asyncio.to_thread(kept.read_bytes)
        else:
            if problem := torrent_problem():
                raise SharingError(f'Cards can\'t be downloaded on this computer: {problem}.')
            got = await self.session.fetch(p.torrent_infohash, self.downloading, name=p.file_name, size=p.card_size,
                                           trackers=p.trackers, on_progress=lambda now: _progressed(fetch, now))
            content = await asyncio.to_thread(got.read_bytes)
            file = await asyncio.to_thread(card_file, content)
            if (file.sha256, file.infohash, file.name) != (p.card_sha256, p.torrent_infohash, p.file_name):
                await asyncio.to_thread(got.unlink, missing_ok=True)
                raise SharingError('The file received isn\'t the one announced: it was deleted.')
        fetch.stage = 'adding'
        try:  # (on the event loop, as the app's own Import: the stores' hooks expect it)
            character_id, message, fingerprint = self.importer(p.file_name, content, fetch.languages)
        except Exception as e:  # (cards.CardError: not a card this app reads; or the disk)
            if got is not None:
                await asyncio.to_thread(got.unlink, missing_ok=True)
            raise SharingError(f'It can\'t be added as a character: {e}') from None
        await self.call(self.catalog.record_download, p.event_id, p.torrent_infohash, p.file_name, character_id,
                        fingerprint)
        if self.share_downloads:  # (shared from here in turn: kept in files/)
            async with self._files_lock:
                if got is not None:
                    self.files.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(os.replace, got, kept)
                file = await asyncio.to_thread(card_file, content)
                await self.call(self.catalog.save_torrent, file.infohash, file.sha256, file.name, file.metainfo())
                await self.call(self.catalog.set_seeding, p.event_id, file.infohash, True)
                self.session.seed(file.metainfo(), self.files, trackers=p.trackers)
        elif got is not None:
            await asyncio.to_thread(got.unlink, missing_ok=True)
        fetch.character_id, fetch.message, fetch.stage = character_id, message, 'done'

    async def set_share_downloads(self, on: bool) -> int:
        """Share the cards downloaded from now on, or not; not: those shared stop being, and their files go
        (the characters stay). How many stopped."""
        self.share_downloads = on
        if on:
            return 0
        downloaded = {d.event_id for d in await self.call(self.catalog.downloads)}
        own = set(self.own_keys())
        stopped = 0
        for item in await self.call(self.catalog.seeding, enabled_only=True):
            entry = await self.call(self.catalog.get, item.event_id)
            mine = entry is not None and entry.publication.publisher_id in own
            if item.event_id in downloaded and not mine:
                await self.stop_sharing(item.event_id)
                stopped += 1
        return stopped

    async def downloaded(self, event_id: str) -> Download | None:
        """Whether a publication was downloaded here, and the character it became."""
        return await self.call(self.catalog.download, event_id)

    # --- the end ----------------------------------------------------------------------------------

    async def _watch(self) -> None:
        """What BitTorrent says (the router's answer, connections from the internet...), read as it comes:
        libtorrent keeps only so much of it."""
        while True:
            await asyncio.sleep(WATCH)
            if self.session.started:
                self.session.poll()

    async def close(self) -> None:
        """When the app stops: discovery and downloads stop (what discovery got is kept; what downloads got
        is deleted), and the catalog is closed."""
        for task in [self._task, self._watcher, *self._background, *(f.task for f in self.fetches.values())]:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:  # (cancelled, or failed: either way, over)
                    pass
        self.session.close()
        self.catalog.close()


def torrent_problem() -> str:
    from .seeder import torrent_problem as problem
    return problem()


def _same(a: Event, b: Event) -> bool:
    """The same publication, but for its date and signature: same text, same tags (trackers aside)."""
    def tags(event: Event) -> list:
        return [t for t in event.tags if t[0] != 'tracker']
    return a.content == b.content and tags(a) == tags(b) and a.pubkey == b.pubkey


def _progressed(fetch: Fetch, now: FetchProgress) -> None:
    fetch.stage, fetch.progress, fetch.peers = now.stage, now.progress, now.peers


_CARD_FILE = re.compile(r'[0-9a-f]{64}\.(png|json|charx)')


def _has_hash(path: Path, sha256: str) -> bool:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == sha256
    except OSError:
        return False
