"""Settings › Privacy: away from home (the internet, Tailscale...), every picture is blurred, on every page."""
import ipaddress

import pytest
from nicegui.elements.query import QueryElement
from nicegui.testing import user_simulation

from rpapp.privacy import where
from rpapp.settings import Settings, SettingsStore
from rpapp.ui.app import build_root

LOCAL = {'host': 'localhost:8091'}


@pytest.mark.parametrize('host, headers, place', [
    ('127.0.0.1', LOCAL, 'computer'),
    ('::1', {'host': '[::1]:8091'}, 'computer'),
    ('127.0.0.1', {'host': 'my-pc.tail1234.ts.net'}, 'away'),        # (a tunnel to this computer, by another name)
    ('127.0.0.1', {**LOCAL, 'tailscale-user-login': 'me@x.y'}, 'away'),  # (Tailscale Serve: relayed)
    ('127.0.0.1', {**LOCAL, 'x-forwarded-for': '203.0.113.5'}, 'away'),  # (a reverse proxy)
    ('192.168.1.20', {}, 'home'),
    ('10.0.0.7', {}, 'home'),
    ('172.20.1.1', {}, 'home'),
    ('169.254.3.3', {}, 'home'),
    ('fe80::1%eth0', {}, 'home'),
    ('fd00::5', {}, 'home'),
    ('::ffff:192.168.1.3', {}, 'home'),
    ('100.101.102.103', {}, 'away'),                                   # (Tailscale)
    ('fd7a:115c:a1e0::1', {}, 'away'),                                 # (Tailscale, IPv6)
    ('203.0.113.5', {}, 'away'),
    ('2a01:e0a:1:2::5', {}, 'away'),
    ('not an address', {}, 'away'),
    (None, {}, 'away'),
])
def test_where_a_browser_connects_from(host, headers, place):
    assert where(host, headers, own_networks=[]) == place


def test_public_ipv6_addresses_of_the_home_network():
    """Home networks often give their devices public IPv6 addresses: those of this computer's network are home."""
    home = [ipaddress.ip_network('2a01:e0a:1:2::/64')]
    assert where('2a01:e0a:1:2::5', {}, own_networks=home) == 'home'
    assert where('2a01:e0a:1:3::5', {}, own_networks=home) == 'away'


def test_on_by_default(tmp_path):
    assert Settings().privacy.blur_away is True
    (tmp_path / 'settings.json').write_text('{"id": "settings", "chat": {}}', encoding='utf-8')  # (from before)
    assert SettingsStore(tmp_path).load().privacy.blur_away is True


def body_classes(user) -> list[str]:
    found = [e for e in user.client.elements.values()
             if isinstance(e, QueryElement) and e.props.get('selector') == 'body']
    return found[0].props['classes'] if found else []


@pytest.fixture
def place(monkeypatch):
    """Where the browser is, as rpapp.privacy.where would say: set by the test."""
    import rpapp.ui.privacy as privacy
    here = {'place': 'away'}
    monkeypatch.setattr(privacy, 'where', lambda host, headers: here['place'])
    return here


@pytest.mark.parametrize('where_, on, blurred', [
    ('away', True, True), ('away', False, False), ('home', True, False), ('computer', True, False),
])
async def test_pictures_blurred_away_from_home(tmp_path, place, where_, on, blurred):
    place['place'] = where_
    store = SettingsStore(tmp_path)
    settings = store.load()
    settings.privacy.blur_away = on
    store.save(settings)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open('/')
        await user.should_see('Recent characters')
        assert ('privacy-blur' in body_classes(user)) is blurred


async def test_the_setting_changes_the_pages_open(tmp_path, place):
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open('/settings')
        await user.should_see(marker='privacy-away')
        await user.should_see('This browser connects from away from home: its pictures are blurred.')
        assert 'privacy-blur' in body_classes(user)
        [switch] = user.find(marker='privacy-blur-away').elements
        switch.set_value(False)
        await user.should_see('This browser connects from away from home: its pictures would be blurred.')
        assert 'privacy-blur' not in body_classes(user)  # (at once, without reloading)
        assert SettingsStore(tmp_path).load().privacy.blur_away is False
        switch.set_value(True)
        await user.should_see('its pictures are blurred.')
        assert 'privacy-blur' in body_classes(user)


async def test_at_home_it_says_so(tmp_path, place):
    place['place'] = 'home'
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open('/settings')
        await user.should_see('This browser connects from your home network: its pictures show.')
        assert 'privacy-blur' not in body_classes(user)
