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

How others reach this computer (network()): the port it listens on (6881, or the next free one, or the one
set: configure()), whether the router opened it (UPnP, NAT-PMP), and whether someone from the internet
has connected: the only proof that it's reachable. Without that, sharing works with those who are
reachable themselves (this computer connects to them), not with those who aren't either.

libtorrent's packages exist for Python 3.13 and earlier (torrent_problem() says what's missing).
"""
from __future__ import annotations

import asyncio
import ipaddress
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
DEFAULT_PORT = 6881   # BitTorrent's usual one; taken, the next ones are tried (unless a port is set)
ROUTER_WAIT = 30.0    # seconds: the router hasn't answered by then, it's said


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


def listen_on(port: int | None) -> str:
    """libtorrent's listen_interfaces: every address of this computer, IPv4 and IPv6, on that port
    (None: DEFAULT_PORT)."""
    port = port or DEFAULT_PORT
    return f'0.0.0.0:{port},[::]:{port}'


@dataclass(frozen=True, slots=True)
class NetworkStatus:
    """How others reach this computer, as far as it can tell (TorrentSession.network())."""
    started: bool                    # False: not listening yet (nothing shared or downloaded)
    port: int                        # listened on (0: none)
    wanted_port: int | None          # the one set (None: automatic)
    listen_error: str                # why it can't listen ('' if it does)
    port_mapping: bool               # whether the router is asked to open the port
    mapped: tuple[tuple[str, str, int], ...]     # (UPnP or NAT-PMP, TCP or UDP, port on the internet side)
    mapping_errors: tuple[tuple[str, str], ...]  # (UPnP or NAT-PMP, what went wrong)
    asking_since: float | None       # when the router was asked (time.time())
    external_address: str            # this computer's internet address, as others see it ('' not known)
    reached_at: float | None         # the last connection from the internet (TCP), time.time()
    reached_by: str                  # its kind: 'IPv4' or 'IPv6'


@dataclass(frozen=True, slots=True)
class TorrentStatus:
    infohash: str
    state: str                # 'seeding', 'checking', 'downloading', 'error'...
    peers: int                # connected now
    uploaded: int             # bytes sent, since the app started
    progress: float           # 0 to 1
    error: str = ''


class TorrentSession:
    def __init__(self, *, port: int | None = None, listen: str | None = None, dht: bool = True,
                 local_discovery: bool = True, port_mapping: bool = True,
                 trackers: tuple[str, ...] = DEFAULT_TRACKERS, limits: Limits = Limits(),
                 peers: tuple[tuple[str, int], ...] = ()) -> None:
        """`port`: the one to listen on (None: DEFAULT_PORT, or the next free one; set, that one only).
        `listen`: libtorrent's listen_interfaces instead: the tests give 127.0.0.1:0, without the DHT, local
        discovery and port mapping (two sessions on this computer), and `peers`, where to find the other one
        (asked for every file downloaded)."""
        self.wanted_port = port
        self.settings = {
            'listen_interfaces': listen or listen_on(port), **_port_rules(port),
            'enable_dht': dht, 'enable_lsd': local_discovery,
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
        # How others reach this computer (network()), from libtorrent's alerts
        self._listening: dict[str, int] = {}    # 'tcp' or 'udp' → port
        self._listen_error = ''
        self._mapped: dict[tuple[str, str], int] = {}  # (UPnP or NAT-PMP, TCP or UDP) → port on the internet side
        self._mapping_errors: dict[str, str] = {}      # UPnP or NAT-PMP → what went wrong
        self._asking_since: float | None = None
        self._external = ''
        self._reached: tuple[float, str] | None = None  # (when, IPv4 or IPv6)

    @property
    def started(self) -> bool:
        return self._session is not None

    def start(self) -> None:
        """Open the session (its port, the DHT): when there's something to share."""
        if self._session is None:
            import libtorrent as lt
            category = lt.alert.category_t
            settings = dict(self.settings)
            settings['alert_mask'] = (category.error_notification | category.status_notification
                                      | category.port_mapping_notification | category.peer_notification)
            self._session = lt.session(settings)
            self._asking_since = time.time() if settings['enable_upnp'] or settings['enable_natpmp'] else None

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

    # --- how others reach this computer ----------------------------------------------------------------

    def network(self) -> NetworkStatus:
        """The port listened on, the router's answer, and whether someone from the internet connected."""
        self._read_alerts()
        settings = self.settings
        return NetworkStatus(
            started=self.started, port=self._listening.get('tcp', 0) if self.started else 0,
            wanted_port=self.wanted_port, listen_error=self._listen_error if 'tcp' not in self._listening else '',
            port_mapping=bool(settings['enable_upnp'] or settings['enable_natpmp']),
            mapped=tuple(sorted((t, p, port) for (t, p), port in self._mapped.items())),
            mapping_errors=tuple(sorted(self._mapping_errors.items())), asking_since=self._asking_since,
            external_address=self._external, reached_at=self._reached[0] if self._reached else None,
            reached_by=self._reached[1] if self._reached else '')

    def configure(self, *, port: int | None, port_mapping: bool) -> None:
        """Another port (None: automatic), the router asked to open it or not: at once if the session runs
        (libtorrent listens again, and asks the router for the new port), or when it starts."""
        changed = {'listen_interfaces': listen_on(port), **_port_rules(port),
                   'enable_upnp': port_mapping, 'enable_natpmp': port_mapping}
        if changed.items() <= self.settings.items() and port == self.wanted_port:
            return
        self.settings.update(changed)
        self.wanted_port = port
        if self._session is not None:
            self._read_alerts()  # (what was said of the old port: not of the new one)
            self._listening.clear()
            self._listen_error = ''
            self._mapped.clear()
            self._mapping_errors.clear()
            self._asking_since = time.time() if port_mapping else None
            self._session.apply_settings(changed)

    def poll(self) -> None:
        """Read what libtorrent said (the service does it every few seconds: its queue of alerts is limited)."""
        self._read_alerts()

    def _read_alerts(self) -> None:
        """What libtorrent said: a torrent's own errors (its file), kept for status(); and how others reach
        this computer, for network(). A tracker or a peer that fails isn't an error of the torrent: there
        are others, and the DHT (logged only)."""
        if self._session is None:
            return
        for alert in self._session.pop_alerts():
            handle = getattr(alert, 'handle', None)
            what = alert.what()
            if what in NETWORK_ALERTS:
                self._network_alert(what, alert)
                continue
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

    def _network_alert(self, what: str, alert) -> None:
        import libtorrent as lt
        if what == 'listen_succeeded':  # (on each address of this computer: the same port, normally)
            self._listening.setdefault('tcp' if _is_tcp(lt, alert.socket_type) else 'udp', alert.port)
        elif what == 'listen_failed':  # (said only if TCP listens nowhere: IPv6 fails where there's none)
            if _is_tcp(lt, alert.socket_type):
                self._listen_error = _error_text(alert.error) or alert.message()
            log.info('BitTorrent: %s', alert.message())
        elif what == 'portmap':
            self._mapped[(_transport(lt, alert.map_transport), _protocol(lt, alert.map_protocol))] = alert.external_port
            self._mapping_errors.pop(_transport(lt, alert.map_transport), None)
            log.info('BitTorrent: %s', alert.message())
        elif what == 'portmap_error':
            self._mapping_errors[_transport(lt, alert.map_transport)] = _error_text(alert.error) or alert.message()
            log.info('BitTorrent: %s', alert.message())
        elif what == 'external_ip':
            self._external = str(alert.external_address)
        elif what == 'incoming_connection':  # (from the internet, by TCP: proof that the port is reachable)
            try:
                address = ipaddress.ip_address(alert.endpoint[0])
            except ValueError:
                return
            if _is_tcp(lt, alert.socket_type) and address.is_global:
                self._reached = (time.time(), f'IPv{address.version}')

    def close(self) -> None:
        """When the app stops (downloads under way are the caller's to cancel first: they delete what they
        received)."""
        if self._session is not None:
            for infohash in list(self._handles):
                self.stop(infohash)
            self._session = None


def _port_rules(port: int | None) -> dict:
    """Automatic: the next ports if it's taken, and at worst any. Set: that one or none (it's the one
    opened on the router; another would be of no use), and a problem said."""
    return {'max_retry_port_bind': 0 if port else 10, 'listen_system_port_fallback': not port}


NETWORK_ALERTS = frozenset({'listen_succeeded', 'listen_failed', 'portmap', 'portmap_error', 'external_ip',
                            'incoming_connection'})


def _is_tcp(lt, socket_type) -> bool:
    return socket_type in (lt.socket_type_t.tcp, lt.socket_type_t.tcp_ssl)


def _transport(lt, transport) -> str:
    return 'UPnP' if transport == lt.portmap_transport.upnp else 'NAT-PMP'


def _protocol(lt, protocol) -> str:
    return 'TCP' if protocol == lt.portmap_protocol.tcp else 'UDP'


def _error_text(error) -> str:
    try:
        return str(error.message())
    except Exception:  # (not an error code)
        return ''


def _check(info, *, name: str, size: int) -> None:
    """The torrent's description against the announcement's: the same name, the same size, one file."""
    if info is None or info.num_files() != 1 or info.name() != name or info.total_size() != size:
        raise FetchError('What\'s shared under its infohash isn\'t the file announced: it wasn\'t downloaded.')
