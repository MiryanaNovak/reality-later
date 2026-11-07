"""BitTorrent, with libtorrent: seeding the card files this computer published (and, later, downloading).

One session for the app, started when there's something to share (TorrentSession.start()), not before:
until then, no port is opened. libtorrent works in threads of its own: what's called here returns at once,
and the app isn't held up while files go out.

How peers find each other: the trackers given (in each announcement, and here), the DHT (BitTorrent's
own directory, without a server), and the local network (LSD). UPnP and NAT-PMP ask the router to let
other peers reach this one; without them, or behind a strict router, sharing works less well (it needs
the other side to be reachable) but works.

Everyone in a file's swarm sees the others' addresses: publishing a card, and seeding it, shows this
computer's address to whoever looks at that file.

libtorrent's packages exist for Python 3.13 and earlier (torrent_problem() says what's missing).
"""
from __future__ import annotations

import logging
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
                 limits: Limits = Limits()) -> None:
        """`listen`: libtorrent's listen_interfaces (6881 taken: it tries the next ports). The tests give
        127.0.0.1:0, without the DHT, local discovery and port mapping: two sessions on this computer."""
        self.settings = {
            'listen_interfaces': listen, 'enable_dht': dht, 'enable_lsd': local_discovery,
            'enable_upnp': port_mapping, 'enable_natpmp': port_mapping, 'user_agent': USER_AGENT,
            'upload_rate_limit': limits.upload, 'download_rate_limit': limits.download,
            'connections_limit': limits.connections, 'allow_multiple_connections_per_ip': not dht,
        }
        self.trackers = trackers
        self._session = None
        self._handles: dict[str, object] = {}   # infohash → torrent handle
        self._errors: dict[str, str] = {}       # infohash → what went wrong with it

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
        error = self._errors.get(infohash) or (str(s.errc.message()) if s.errc.value() else '')
        state = 'error' if error else 'seeding' if s.is_seeding else str(s.state).rsplit('.', 1)[-1]
        return TorrentStatus(infohash=infohash, state=state, peers=s.num_peers, uploaded=s.total_upload,
                             progress=s.progress, error=error)

    def connect(self, infohash: str, address: tuple[str, int]) -> None:
        """Tell a torrent where a peer is (the tests: two sessions on this computer, without the DHT)."""
        self._handles[infohash].connect_peer(address)

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
            self._errors[infohash] = alert.message()[:300]
            log.warning('Sharing %s: %s', infohash, alert.message())

    def close(self) -> None:
        """When the app stops."""
        if self._session is not None:
            for infohash in list(self._handles):
                self.stop(infohash)
            self._session = None
