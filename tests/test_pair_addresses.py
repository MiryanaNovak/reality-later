"""Which address a device pairs with (access.network_choices, the pairing dialog in ui/devices_page.py): each one
says what it is (Wi-Fi, Ethernet, a VPN...), Wi-Fi comes first, and nothing is chosen ahead: the QR code comes only
once you chose, the one used last time just marked."""
import json
import subprocess

import pytest
from nicegui.testing import user_simulation

from rpapp import access as access_module
from rpapp.access import Access, Who, network_addresses, network_choices
from rpapp.devices import DeviceRegistry
from rpapp.ui.app import build_root


def seen(monkeypatch, cards, host='desk'):
    monkeypatch.setattr(access_module, '_cards', lambda: cards)
    monkeypatch.setattr(access_module.socket, 'gethostname', lambda: host)
    return [(c.kind, c.title, c.address) for c in network_choices('0.0.0.0')]


@pytest.fixture(autouse=True)
def no_system(monkeypatch, tmp_path):
    """What the system says of the cards: nothing, unless a test says (a fake /sys/class/net, a fake macOS)."""
    monkeypatch.setattr(access_module, 'SYS_NET', tmp_path / 'sys-class-net')
    access_module._mac_ports.cache_clear()
    yield
    access_module._mac_ports.cache_clear()


# --- what each address is, likeliest first ------------------------------------------------------------

def test_linux_names(monkeypatch):
    assert seen(monkeypatch, [
        ('lo', '127.0.0.1'), ('enp3s0', '192.168.1.10'), ('docker0', '172.17.0.1'), ('br-3fa2', '172.18.0.1'),
        ('wlp2s0', '192.168.1.20'), ('wlp2s0', 'fe80::1'), ('tailscale0', '100.101.1.2'), ('wg0', '10.8.0.2'),
        ('zt5u4', '10.147.17.3'), ('virbr0', '192.168.122.1'), ('tun0', '10.9.0.6'),
    ]) == [
        ('wifi', 'Wi-Fi · wlp2s0', '192.168.1.20'),       # Wi-Fi first: a phone on it, nearly always
        ('ethernet', 'Ethernet · enp3s0', '192.168.1.10'),
        ('vpn', 'VPN (WireGuard) · wg0', '10.8.0.2'),
        ('vpn', 'VPN · tun0', '10.9.0.6'),
        ('vpn', 'VPN (ZeroTier) · zt5u4', '10.147.17.3'),
        ('vpn', 'VPN (Tailscale) · tailscale0', '100.101.1.2'),
        ('name', 'This computer\'s name', 'desk.local'),
        ('virtual', 'Virtual machines · virbr0', '192.168.122.1'),  # (containers and virtual machines: last)
        ('virtual', 'Docker · docker0', '172.17.0.1'),
        ('virtual', 'Docker · br-3fa2', '172.18.0.1'),
    ]


def test_windows_names(monkeypatch):
    assert seen(monkeypatch, [
        ('Ethernet 2', '192.168.0.12'), ('vEthernet (WSL)', '172.28.0.1'), ('Wi-Fi', '192.168.0.15'),
        ('Tailscale', '100.88.1.4'), ('NordLynx', '10.5.0.2'), ('VirtualBox Host-Only Network', '192.168.56.1'),
    ]) == [
        ('wifi', 'Wi-Fi', '192.168.0.15'),                 # (the card's name is the label: said once)
        ('ethernet', 'Ethernet · Ethernet 2', '192.168.0.12'),
        ('vpn', 'VPN (NordVPN) · NordLynx', '10.5.0.2'),
        ('vpn', 'VPN (Tailscale)', '100.88.1.4'),
        ('name', 'This computer\'s name', 'desk.local'),
        ('virtual', 'VirtualBox · VirtualBox Host-Only Network', '192.168.56.1'),
        ('virtual', 'Hyper-V or WSL · vEthernet (WSL)', '172.28.0.1'),
    ]


def test_without_wifi_ethernet_first_and_by_the_address_itself(monkeypatch):
    assert seen(monkeypatch, [
        ('net7', '100.70.2.3'), ('eth0', '10.0.0.5'), ('eth1', '81.12.40.7'), ('weird', '172.17.0.5'),
        ('mystery', '192.168.8.4'),
    ], host='localhost') == [
        ('ethernet', 'Ethernet · eth0', '10.0.0.5'),
        ('network', 'Network · mystery', '192.168.8.4'),  # (a card it doesn't know: a local network)
        ('vpn', 'VPN (Tailscale?) · net7', '100.70.2.3'),  # (100.64.0.0/10: Tailscale's, nearly always)
        ('internet', 'Public address (Ethernet) · eth1', '81.12.40.7'),
        ('virtual', 'Docker? · weird', '172.17.0.5'),     # (Docker's default network)
    ]
    assert [c.note for c in network_choices('0.0.0.0')][:1] == [
        'For a device on the same home network: the same router, by Wi-Fi or cable.']


def test_what_linux_and_macos_say(monkeypatch, tmp_path):
    (tmp_path / 'sys-class-net' / 'wlx00c0ca').mkdir(parents=True)  # a USB Wi-Fi stick: not named wl...
    (tmp_path / 'sys-class-net' / 'wlx00c0ca' / 'wireless').mkdir()
    (tmp_path / 'sys-class-net' / 'custom0').mkdir()
    (tmp_path / 'sys-class-net' / 'custom0' / 'tun_flags').write_text('0x1002')
    (tmp_path / 'sys-class-net' / 'enx1').mkdir()
    (tmp_path / 'sys-class-net' / 'enx1' / 'phy80211').mkdir()  # (named like Ethernet, but wireless)
    assert [k for k, _, _ in seen(monkeypatch, [('custom0', '10.1.0.2'), ('enx1', '192.168.2.5'),
                                                ('wlx00c0ca', '192.168.1.5')])][:3] == ['wifi', 'wifi', 'vpn']

    ports = ('Hardware Port: Ethernet\nDevice: en1\nEthernet Address: a\n\n'
             'Hardware Port: Wi-Fi\nDevice: en0\nEthernet Address: b\n\n'
             'Hardware Port: Thunderbolt Bridge\nDevice: bridge0\nEthernet Address: N/A\n')
    monkeypatch.setattr(access_module.sys, 'platform', 'darwin')
    monkeypatch.setattr(access_module.subprocess, 'run',
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=ports, stderr=''))
    access_module._mac_ports.cache_clear()
    assert seen(monkeypatch, [('en1', '192.168.1.9'), ('en0', '192.168.1.8')])[:2] == [
        ('wifi', 'Wi-Fi · en0', '192.168.1.8'), ('ethernet', 'Ethernet · en1', '192.168.1.9')]


def test_listening_on_one_address(monkeypatch):
    monkeypatch.setattr(access_module, '_cards', lambda: [('wlp2s0', '192.168.1.20'), ('eth0', '10.0.0.5')])
    assert [(c.kind, c.address) for c in network_choices('192.168.1.20')] == [('wifi', '192.168.1.20')]
    assert network_choices('127.0.0.1') == [] and network_addresses('localhost') == []


# --- the dialog -----------------------------------------------------------------------------------------

CARDS = [('enp3s0', '192.168.1.10'), ('docker0', '172.17.0.1'), ('wlp2s0', '192.168.1.20'), ('tailscale0', '100.101.1.2')]


@pytest.fixture
async def dialog(tmp_path, monkeypatch):
    monkeypatch.setattr(access_module, '_cards', lambda: CARDS)
    monkeypatch.setattr(access_module.socket, 'gethostname', lambda: 'desk')
    devices = DeviceRegistry(tmp_path)
    access = Access(devices, port=8090)
    monkeypatch.setattr('rpapp.ui.devices_page._who', lambda access: Who(local=True))  # (on the computer)
    async with user_simulation(build_root(tmp_path, access=access)) as user:
        await user.open('/settings')
        await user.should_see('Devices')
        yield user, access, tmp_path


def markers(user, prefix):
    return [m for e in user.client.layout.descendants() for m in e._markers if m.startswith(prefix)]


def found(user, marker) -> bool:
    return any(marker in e._markers for e in user.client.layout.descendants())


async def test_choose_then_the_code(dialog):
    user, access, tmp_path = dialog
    user.find(marker='pair-device').click()
    await user.should_see('Which network is the device on?')
    assert not found(user, 'pair-qr') and not found(user, 'pair-last')  # (nothing chosen, nothing used before)
    assert markers(user, 'pair-address-') == ['pair-address-192.168.1.20', 'pair-address-192.168.1.10',
                                              'pair-address-100.101.1.2', 'pair-address-desk.local',
                                              'pair-address-172.17.0.1']
    with user.scope(marker='pair-address-192.168.1.20'):
        await user.should_see('Wi-Fi · wlp2s0')
        await user.should_see('For a device on the same Wi-Fi, or the same router.')
    [other] = [e for e in user.client.layout.descendants() if 'pair-other' in e._markers]
    assert other.value is False  # (Docker's: folded away)

    user.find(marker='pair-address-192.168.1.10').click()
    await user.should_see(marker='pair-qr')
    [link] = access.devices.links.values()
    await user.should_see(f'http://192.168.1.10:8090/pair?code={link.code}')
    with user.scope(marker='pair-chosen'):
        await user.should_see('Ethernet · enp3s0')
    assert json.loads((tmp_path / 'devices.json').read_text())['pair_address'] == '192.168.1.10'

    user.find(marker='pair-change').click()  # (changed: chosen again)
    await user.should_see(marker='pair-question')
    assert not found(user, 'pair-qr')
    user.find(marker='pair-address-192.168.1.20').click()
    await user.should_see(f'http://192.168.1.20:8090/pair?code={link.code}')


async def test_the_last_one_marked_not_chosen(dialog):
    user, access, _ = dialog
    access.devices.chose_address('192.168.1.10')
    assert DeviceRegistry(access.devices.path.parent).pair_address == '192.168.1.10'  # (kept in the file)
    user.find(marker='pair-device').click()
    await user.should_see(marker='pair-question')
    assert not found(user, 'pair-qr')  # (still to be chosen)
    with user.scope(marker='pair-address-192.168.1.10'):
        await user.should_see('Used last time')
    assert len(markers(user, 'pair-last')) == 1


async def test_another_address_typed(dialog):
    user, access, _ = dialog
    user.find(marker='pair-device').click()
    await user.should_see(marker='pair-question')
    [typed] = [e for e in user.client.layout.descendants() if 'pair-typed' in e._markers]
    typed.set_value('desk.tailnet.ts.net')
    user.find(marker='pair-use-typed').click()
    await user.should_see(marker='pair-qr')
    [link] = access.devices.links.values()
    await user.should_see(f'http://desk.tailnet.ts.net:8090/pair?code={link.code}')
    with user.scope(marker='pair-chosen'):
        await user.should_see('Typed by you')
