"""BitTorrent, with libtorrent: sharing card files (seeding), and downloading them (fetch()).

One session for the app, started when there's something to share or to get (TorrentSession.start()), not
before: until then, no port is opened. libtorrent works in threads of its own: what's called here returns
at once, and the app isn't held up while files come and go.

Downloading a card's file starts from its infohash alone (a magnet link): the torrent's description comes
from the peers first, and it's checked against what the signed announcement says (the file's name, its
size, one file only) before any of the file is asked for. A torrent that isn't the one announced, or a
file too big, isn't downloaded at all. Once it's there, the caller checks the file itself (its SHA-256).

How peers find each other: the trackers given (in each announcement, and here), the DHT (BitTorrent's
own directory, without a server), and the local network (LSD). UPnP and NAT-PMP ask the router to let
other peers reach this one; without them, or behind a strict router, sharing works less well (it needs
the other side to be reachable) but works.

Everyone in a file's swarm sees the others' addresses: publishing a card, and seeding it, shows this
computer's address to whoever looks at that file.

libtorrent's packages exist for Python 3.13 and earlier (torrent_problem() says what's missing).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Public trackers, well known for years. They speed up finding peers; the DHT works without them.
DEFAULT_TRACKERS = (
    'udp://tracker.opentrackr.org:1337/announce',
    'udp://open.demonii.com:1337/announce',
    'udp://tracker.torrent.eu.org:451/announce',
)
USER_AGENT = 'Reality Later'
# libtorrent's alerts that mean a torrent can't be shared (its file can't be read...), unlike those of a
# tracker or a peer (tracker_error, peer_error, scrape_failed...)
TORRENT_ERRORS = frozenset({'file_error', 'torrent_error', 'hash_failed'})
FIND_TIMEOUT = 120.0  # seconds: nobody sharing the file found by then, a download gives up
STALL_TIMEOUT = 120.0  # seconds: nothing more received for that long, too
POLL = 0.25           # seconds between two looks at a download


class FetchError(Exception):
    """A file couldn't be downloaded: the message says why, in plain words."""


@dataclass(frozen=True, slots=True)
class FetchProgress:
    stage: str                # 'looking' (for people sharing it, and its description), 'downloading'
    progress: float           # 0 to 1
    peers: int                # connected now


def torrent_problem() -> str:
    """Why files can't be shared on this computer ('' if they can): a missing library."""
    try:
        import libtorrent  # noqa: F401
    except Exception as e:  # (ImportError, or a library that fails to load)
        return f'a library it needs is missing ({e})'
    return ''


@dataclass(frozen=True, slots=True)
class Limits:
    """How much of the connection sharing may take (0: no limit)."""
    upload: int = 0           # bytes per second
    download: int = 0
    connections: int = 200


@dataclass(frozen=True, slots=True)
class TorrentStatus:
    infohash: str
    state: str                # 'seeding', 'checking', 'downloading', 'error'...
    peers: int                # connected now
    uploaded: int             # bytes sent, since the app started
    progress: float           # 0 to 1
    error: str = ''


class TorrentSession:
    def __init__(self, *, listen: str = '0.0.0.0:6881,[::]:6881', dht: bool = True, local_discovery: bool = True,
                 port_mapping: bool = True, trackers: tuple[str, ...] = DEFAULT_TRACKERS,
                 limits: Limits = Limits(), peers: tuple[tuple[str, int], ...] = ()) -> None:
        """`listen`: libtorrent's listen_interfaces (6881 taken: it tries the next ports). The tests give
        127.0.0.1:0, without the DHT, local discovery and port mapping: two sessions on this computer, and
        `peers`, where to find the other one (asked for every file downloaded)."""
        self.settings = {
            'listen_interfaces': listen, 'enable_dht': dht, 'enable_lsd': local_discovery,
            'enable_upnp': port_mapping, 'enable_natpmp': port_mapping, 'user_agent': USER_AGENT,
            'upload_rate_limit': limits.upload, 'download_rate_limit': limits.download,
            'connections_limit': limits.connections, 'allow_multiple_connections_per_ip': not dht,
            # (a download wants nothing until its torrent's description is checked: meanwhile, libtorrent
            # would close its connections to those sharing the file as useless, and they'd be lost)
            'close_redundant_connections': False,
        }
        self.trackers = trackers
        self.peers = peers
        self.find_timeout, self.stall_timeout = FIND_TIMEOUT, STALL_TIMEOUT  # (fetch()'s; shorter in tests)
        self._session = None
        self._handles: dict[str, object] = {}   # infohash → torrent handle, of the files shared
        self._fetching: dict[str, object] = {}  # infohash → torrent handle, of the files being downloaded
        self._errors: dict[str, tuple[str, str]] = {}  # infohash → what went wrong with it: (alert, message)

    @property
    def started(self) -> bool:
        return self._session is not None

    def start(self) -> None:
        """Open the session (its port, the DHT): when there's something to share."""
        if self._session is None:
            import libtorrent as lt
            settings = dict(self.settings)
            settings['alert_mask'] = lt.alert.category_t.error_notification | lt.alert.category_t.status_notification
            self._session = lt.session(settings)

    @property
    def port(self) -> int:
        return self._session.listen_port() if self._session is not None else 0

    def seed(self, metainfo: bytes, folder: Path, *, trackers: tuple[str, ...] = ()) -> str:
        """Share the file this torrent describes, from `folder` (where it is, under its name in the torrent).
        Its infohash; the same file shared already: nothing more."""
        import libtorrent as lt
        self.start()
        params = lt.add_torrent_params()
        params.ti = lt.torrent_info(lt.bdecode(metainfo))
        infohash = str(params.ti.info_hashes().v1)
        if infohash in self._handles:
            return infohash
        params.save_path = str(folder)
        params.flags |= lt.torrent_flags.seed_mode  # (checked when published: its pieces are checked as they go)
        params.trackers = list(dict.fromkeys([*trackers, *self.trackers]))
        self._handles[infohash] = self._session.add_torrent(params)
        self._errors.pop(infohash, None)
        return infohash

    def stop(self, infohash: str) -> None:
        """No longer share it (the file stays where it is)."""
        handle = self._handles.pop(infohash, None)
        if handle is not None and self._session is not None:
            self._session.remove_torrent(handle)

    def sharing(self) -> tuple[str, ...]:
        return tuple(self._handles)

    def status(self, infohash: str) -> TorrentStatus | None:
        handle = self._handles.get(infohash)
        if handle is None:
            return None
        self._read_alerts()
        s = handle.status()
        error = self._errors.get(infohash, ('', ''))[1] or (str(s.errc.message()) if s.errc.value() else '')
        state = 'error' if error else 'seeding' if s.is_seeding else str(s.state).rsplit('.', 1)[-1]
        return TorrentStatus(infohash=infohash, state=state, peers=s.num_peers, uploaded=s.total_upload,
                             progress=s.progress, error=error)

    def connect(self, infohash: str, address: tuple[str, int]) -> None:
        """Tell a torrent where a peer is (the tests: two sessions on this computer, without the DHT)."""
        (self._handles.get(infohash) or self._fetching[infohash]).connect_peer(address)

    # --- downloading --------------------------------------------------------------------------------

    async def fetch(self, infohash: str, folder: Path, *, name: str, size: int, trackers: tuple[str, ...] = (),
                    on_progress: Callable[[FetchProgress], None] | None = None,
                    find_timeout: float | None = None, stall_timeout: float | None = None) -> Path:
        """Download this torrent's file into `folder`, if it's the one announced: one file, `name`, `size`
        bytes. Its path once all of it is there (each piece checked against the torrent; the file's own
        SHA-256 is the caller's to check). FetchError if it can't be had: what was received is deleted.

        Nothing of the file is asked for before its description (from the peers) is checked; nobody
        found sharing it within `find_timeout` seconds, or nothing more received for `stall_timeout`
        seconds, and it gives up (FIND_TIMEOUT and STALL_TIMEOUT, unless the session says otherwise)."""
        import libtorrent as lt
        find_timeout = self.find_timeout if find_timeout is None else find_timeout
        stall_timeout = self.stall_timeout if stall_timeout is None else stall_timeout
        if infohash in self._fetching:
            raise FetchError('It\'s being downloaded already.')
        if infohash in self._handles:
            raise FetchError('This computer shares that file already.')
        self.start()
        params = lt.parse_magnet_uri(f'magnet:?xt=urn:btih:{infohash}')
        params.save_path = str(folder)
        params.trackers = list(dict.fromkeys([*trackers, *self.trackers]))
        # (none of the file until its description is checked; started now, not queued behind others: not
        # paused, and not managed by libtorrent's queue, which unpauses torrents when it sees fit)
        params.flags = (params.flags | lt.torrent_flags.default_dont_download) \
            & ~lt.torrent_flags.auto_managed & ~lt.torrent_flags.paused
        folder.mkdir(parents=True, exist_ok=True)
        try:
            handle = self._session.add_torrent(params)
        except RuntimeError as e:  # (libtorrent refuses it)
            raise FetchError(f'It couldn\'t be downloaded: {e}') from None
        self._fetching[infohash] = handle
        self._errors.pop(infohash, None)
        for peer in self.peers:
            handle.connect_peer(peer)
        done = False
        try:
            started = changed = time.monotonic()
            wanted, received = False, -1
            while True:
                self._read_alerts()
                s = handle.status()
                what, error = self._errors.get(infohash, ('', ''))
                if what == 'hash_failed':  # (a peer sent a damaged piece: it's asked for again, elsewhere)
                    error = ''
                if not error and s.errc.value():
                    error = str(s.errc.message())
                if error:
                    raise FetchError(f'It couldn\'t be downloaded: {error}')
                now = time.monotonic()
                if not wanted:
                    if s.has_metadata:
                        _check(handle.torrent_file(), name=name, size=size)
                        handle.prioritize_files([4])  # (libtorrent's default priority: now, all of it)
                        wanted, changed = True, now
                    elif now - started > find_timeout:
                        raise FetchError('Nobody sharing it was found: its publisher, and those who have it, may '
                                         'be away. Try again later.')
                elif s.is_seeding:
                    done = True
                    break
                elif s.total_wanted_done != received:
                    received, changed = s.total_wanted_done, now
                elif now - changed > stall_timeout:
                    raise FetchError('It stopped coming: those sharing it went away. Try again later.')
                if on_progress is not None:
                    on_progress(FetchProgress('downloading' if wanted else 'looking',
                                              s.progress if wanted else 0.0, s.num_peers))
                await asyncio.sleep(POLL)
        finally:
            self._fetching.pop(infohash, None)
            self._errors.pop(infohash, None)
            await self._remove(infohash, handle, delete=not done)
        return folder / name

    async def _remove(self, infohash: str, handle, *, delete: bool) -> None:
        """Remove a torrent, and wait until it's gone: libtorrent does it in its own time, and the same file
        added again meanwhile (to share it, once downloaded) would be the torrent going away."""
        import libtorrent as lt
        if self._session is None:  # (closed meanwhile: the app is stopping)
            return
        self._session.remove_torrent(handle, lt.session.delete_files if delete else 0)
        key = lt.sha1_hash(bytes.fromhex(infohash))
        for _ in range(100):  # (5 seconds at most)
            if self._session is None or not self._session.find_torrent(key).is_valid():
                return
            await asyncio.sleep(0.05)

    def fetching(self) -> tuple[str, ...]:
        return tuple(self._fetching)

    def _read_alerts(self) -> None:
        """What libtorrent said: a torrent's own errors (its file), kept for status(). A tracker or a peer
        that fails isn't one: there are others, and the DHT (logged only)."""
        if self._session is None:
            return
        for alert in self._session.pop_alerts():
            handle = getattr(alert, 'handle', None)
            what = alert.what()
            if handle is None or 'error' not in what and 'failed' not in what:
                continue
            if what not in TORRENT_ERRORS:
                log.debug('Sharing: %s', alert.message())
                continue
            try:
                infohash = str(handle.info_hashes().v1)
            except Exception:  # (a handle no longer valid)
                continue
            self._errors[infohash] = (what, alert.message()[:300])
            log.warning('Sharing %s: %s', infohash, alert.message())

    def close(self) -> None:
        """When the app stops (downloads under way are the caller's to cancel first: they delete what they
        received)."""
        if self._session is not None:
            for infohash in list(self._handles):
                self.stop(infohash)
            self._session = None


def _check(info, *, name: str, size: int) -> None:
    """The torrent's description against the announcement's: the same name, the same size, one file."""
    if info is None or info.num_files() != 1 or info.name() != name or info.total_size() != size:
        raise FetchError('What\'s shared under its infohash isn\'t the file announced: it wasn\'t downloaded.')
