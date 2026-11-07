"""How others reach this computer (rpapp/publishing/seeder.py's network(), Settings › Sharing cards): the
BitTorrent port, the router's answer, connections from the internet."""
import asyncio
import socket
import time
from types import SimpleNamespace

import pytest
from nicegui.testing import user_simulation

pytest.importorskip('coincurve', reason='coincurve (sharing cards) is installed for Python 3.13 and earlier')
lt = pytest.importorskip('libtorrent', reason='libtorrent (sharing files) is installed for Python 3.13 and earlier')

from fake_relay import FakeRelay  # noqa: E402

from rpapp.publishing import identity  # noqa: E402
from rpapp.publishing.seeder import DEFAULT_PORT, ROUTER_WAIT, NetworkStatus, TorrentSession  # noqa: E402
from rpapp.publishing.service import Sharing  # noqa: E402
from rpapp.settings import SettingsStore  # noqa: E402
from rpapp.ui.app import _sharing, build_root  # noqa: E402
from rpapp.ui.sharing_page import network_lines  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('0.0.0.0', 0))
        return s.getsockname()[1]


def quiet(**kwargs) -> TorrentSession:
    """A session that stays on this computer: no DHT, no local discovery, no trackers."""
    return TorrentSession(dht=False, local_discovery=False, trackers=(), **kwargs)


async def until(condition, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError('never happened')


def status(**changes) -> NetworkStatus:
    base = dict(started=True, port=6881, wanted_port=None, listen_error='', port_mapping=True, mapped=(),
                mapping_errors=(), asking_since=time.time(), external_address='', reached_at=None, reached_by='')
    return NetworkStatus(**{**base, **changes})


def texts(st: NetworkStatus, **kwargs) -> list[str]:
    return [text for _, _, text in network_lines(st, **kwargs)]


# --- the session -----------------------------------------------------------------------------------------

async def test_the_port_set_then_changed():
    s = quiet(port=free_port(), port_mapping=False)
    try:
        assert not s.network().started  # (nothing shared: no port open)
        s.start()
        await until(lambda: s.network().port == s.wanted_port)
        assert s.network().port_mapping is False

        with socket.socket() as busy:  # (taken by another program: said, not another port instead)
            busy.bind(('0.0.0.0', 0))
            busy.listen()
            taken = busy.getsockname()[1]
            s.configure(port=taken, port_mapping=False)
            await until(lambda: s.network().listen_error != '')
            assert s.network().port == 0

        other = free_port()
        s.configure(port=other, port_mapping=True)
        await until(lambda: s.network().port == other)
        assert s.network().port_mapping and s.network().asking_since is not None
        s.configure(port=None, port_mapping=False)  # (automatic: 6881, or the next free one)
        await until(lambda: s.network().port >= DEFAULT_PORT)
    finally:
        s.close()


def test_what_libtorrent_says():
    s = quiet(port_mapping=True)
    tcp, utp = lt.socket_type_t.tcp, lt.socket_type_t.utp
    for what, alert in (
            ('portmap', SimpleNamespace(map_transport=lt.portmap_transport.upnp, map_protocol=lt.portmap_protocol.tcp,
                                        external_port=6881, message=lambda: 'mapped')),
            ('portmap_error', SimpleNamespace(map_transport=lt.portmap_transport.natpmp, error=None,
                                              message=lambda: 'NAT-PMP: no router')),
            ('external_ip', SimpleNamespace(external_address='203.0.113.7')),
            ('incoming_connection', SimpleNamespace(endpoint=('192.168.1.20', 50000), socket_type=tcp)),  # (network)
            ('incoming_connection', SimpleNamespace(endpoint=('8.8.8.8', 50000), socket_type=utp))):  # (UDP: no proof)
        s._network_alert(what, alert)
    st = s.network()
    assert st.mapped == (('UPnP', 'TCP', 6881),) and st.mapping_errors == (('NAT-PMP', 'NAT-PMP: no router'),)
    assert st.external_address == '203.0.113.7' and st.reached_at is None
    s._network_alert('incoming_connection', SimpleNamespace(endpoint=('2606:4700:4700::1111', 50000), socket_type=tcp))
    st = s.network()
    assert st.reached_at is not None and st.reached_by == 'IPv6'


def test_ipv4_taken_ipv6_free():
    """A computer with IPv6, where another program holds the port for IPv4 only: IPv6 listens, but most peers
    connect by IPv4, so it's said; IPv6 failing alone (no IPv6 there, often) isn't."""
    tcp = lt.socket_type_t.tcp
    in_use = SimpleNamespace(message=lambda: 'Address already in use')

    def listening(address, port=7000):
        return SimpleNamespace(address=address, port=port, socket_type=tcp)

    def failed(address):
        return SimpleNamespace(address=address, port=7000, socket_type=tcp, error=in_use, message=lambda: 'failed')

    s = quiet(port=7000, port_mapping=False)
    s._session = SimpleNamespace(pop_alerts=list)  # (as if started: what's tested is what alerts make of it)
    s._network_alert('listen_succeeded', listening('::'))
    s._network_alert('listen_failed', failed('192.168.1.20'))
    assert (s.network().port, s.network().listen_error) == (0, 'Address already in use')

    s._listening.clear(), s._listen_errors.clear()
    s._network_alert('listen_failed', failed('::'))
    s._network_alert('listen_succeeded', listening('192.168.1.20'))
    assert (s.network().port, s.network().listen_error) == (7000, '')

    s._listening.clear(), s._listen_errors.clear()
    s._network_alert('listen_succeeded', listening('2a01:e0a::5'))  # (IPv6 only, nothing failed: so be it)
    assert s.network().port == 7000
    s._session = None


# --- in words ----------------------------------------------------------------------------------------------

def test_in_words():
    now = time.time()
    assert texts(status(started=False))[0].startswith('Not listening yet')
    assert texts(status(port=0, wanted_port=7000, listen_error='Address already in use'))[0] == \
        'Can\'t listen on port 7000: Address already in use. Another program may use it: choose another one.'
    assert texts(status(port=6882))[0] == 'Listening on port 6882 (TCP and UDP). 6881 was taken.'

    router = [texts(st, now=now)[1] for st in (
        status(mapped=(('NAT-PMP', 'TCP', 6881), ('UPnP', 'TCP', 6881), ('UPnP', 'UDP', 6881))),
        status(mapping_errors=(('UPnP', 'conflict in mapping'),)),
        status(asking_since=now - 5),
        status(asking_since=now - ROUTER_WAIT - 1),
        status(port_mapping=False, asking_since=None))]
    assert router[0] == 'The router opened it (NAT-PMP, UPnP): port 6881 on the internet side.'
    assert router[1].startswith('The router didn\'t open it (UPnP: conflict in mapping).')
    assert router[2] == 'Asking the router to open it…'
    assert router[3].startswith('No answer from the router')
    assert router[4] == 'The router isn\'t asked: open port 6881 (TCP and UDP) in its settings, to this computer.'

    assert texts(status(reached_at=now - 120, reached_by='IPv4'), now=now)[2] == \
        'Reachable from the internet: someone connected from outside 2 minutes ago (IPv4).'
    assert texts(status(), now=now)[2].startswith('Not confirmed yet')
    assert texts(status(external_address='203.0.113.7'), now=now)[-1].endswith('find port 6881 open on 203.0.113.7.')


# --- the settings ----------------------------------------------------------------------------------------

async def test_the_settings_when_the_app_starts(tmp_path):
    store = SettingsStore(tmp_path)
    settings = store.load()
    settings.sharing.port, settings.sharing.port_mapping = 7000, False
    store.save(settings)
    section = _sharing(tmp_path, store, [], key_folder=tmp_path)
    try:
        session = section.sharing.session
        assert session.wanted_port == 7000 and '0.0.0.0:7000' in session.settings['listen_interfaces']
        assert session.settings['enable_upnp'] is False and not session.started
    finally:
        await section.sharing.close()


@pytest.fixture
async def relay():
    async with FakeRelay() as fake:
        yield fake


async def test_the_settings_page(tmp_path, relay):
    sharing = Sharing(tmp_path, key_file=identity.key_path(tmp_path), default_relays=[relay.url], session=quiet())
    async with user_simulation(build_root(tmp_path, sharing=sharing)) as user:
        await user.open('/settings')
        await user.should_see('How others reach this computer')
        await user.should_see('Not listening yet: the port opens once there\'s a card to share or to download.')

        port = free_port()
        user.find(marker='sharing-port').elements.pop().set_value(port)
        assert SettingsStore(tmp_path).load().sharing.port == port and sharing.session.wanted_port == port
        user.find(marker='sharing-port-mapping').elements.pop().set_value(False)
        assert sharing.session.settings['enable_upnp'] is False
        assert SettingsStore(tmp_path).load().sharing.port_mapping is False

        sharing.session.start()  # (as when a card is shared)
        await user.should_see(f'Listening on port {port} (TCP and UDP).', retries=50)
        await user.should_see(f'The router isn\'t asked: open port {port}', retries=50)
        await user.should_see('Not confirmed yet', retries=50)

        user.find(marker='sharing-port').elements.pop().set_value(80)  # (below 1024: refused, kept as it was)
        assert SettingsStore(tmp_path).load().sharing.port == port and sharing.session.wanted_port == port
