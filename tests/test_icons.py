"""The app's icons: in the browser (tab, home screens), open to devices not paired yet, and in the package."""
import json
import re

from nicegui.testing import user_simulation

from rpapp import ICONS
from rpapp.access import AccessGuard, is_open
from rpapp.ui import theme
from rpapp.ui.app import build_root
from test_devices import guarded_app


async def test_the_browser_gets_the_icons(tmp_path, monkeypatch):
    async with user_simulation(build_root(tmp_path)) as user:
        # (theme.setup() runs once per process, before the app starts; the test's app is reset on starting)
        monkeypatch.setattr(theme, '_defaults_set', False)
        theme.setup()
        await user.open('/')
        head = user.client.head_html
        for link in ('/icons/favicon.svg', '/icons/apple-touch-icon.png', '/icons/manifest.json'):
            assert link in head
        for path, kind in (('/icons/favicon.svg', 'image/svg+xml'), ('/icons/favicon-32.png', 'image/png'),
                           ('/icons/apple-touch-icon.png', 'image/png'), ('/icons/manifest.json', 'application/json')):
            answer = await user.http_client.get(path)
            assert answer.status_code == 200 and answer.headers['content-type'].startswith(kind), path
        manifest = (await user.http_client.get('/icons/manifest.json')).json()
        assert manifest['name'] == 'Reality Later' and manifest['display'] == 'browser'
        for icon in manifest['icons']:  # every icon it names is there
            assert (await user.http_client.get(icon['src'])).status_code == 200, icon['src']


def test_the_files():
    web = {p.name for p in (ICONS / 'web').iterdir()}
    assert {'favicon.svg', 'favicon.ico', 'favicon-32.png', 'apple-touch-icon.png', 'manifest.json',
            'icon-192.png', 'icon-512.png', 'icon-maskable-192.png', 'icon-maskable-512.png'} <= web
    manifest = json.loads((ICONS / 'web' / 'manifest.json').read_text(encoding='utf-8'))
    assert all((ICONS / 'web' / icon['src'].removeprefix('/icons/')).is_file() for icon in manifest['icons'])
    svg = (ICONS / 'web' / 'favicon.svg').read_text(encoding='utf-8')
    assert '<script' not in svg and 'href="http' not in svg  # (served to browsers: nothing that runs, or calls out)
    assert theme.ICON_LINKS in theme.HEAD


def test_svgs_say_they_are_svgs():
    # Desktops (and Flatpak's check of the icons it exports) tell an SVG by a plain <svg tag near the
    # start: <ns0:svg ...> (as Recraft exports them) is valid XML, but "Format not recognized" there.
    project = ICONS.parent.parent
    svgs = [*ICONS.rglob('*.svg'), *(project / 'packaging' / 'icons').rglob('*.svg')]
    assert svgs
    for svg in svgs:
        assert re.search(r'<svg[\s>]', svg.read_text(encoding='utf-8')[:256]), f'{svg}: no plain <svg> tag at the start'


def test_unpaired_devices_may_fetch_them():
    assert is_open('/icons/favicon.svg') and is_open('/favicon.ico') and is_open('/join')
    assert not is_open('/icons/../settings') and not is_open('/iconsx/a.png') and not is_open('/settings')


async def test_through_the_guard(tmp_path):
    _, access = guarded_app(tmp_path)
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope['path'])

    async def send(message):
        pass

    guard = AccessGuard(inner, access)
    phone = {'type': 'http', 'method': 'GET', 'client': ('192.168.1.40', 1),
             'headers': [(b'host', b'192.168.1.20:8090'), (b'accept', b'image/*')]}
    for path in ('/icons/favicon.svg', '/favicon.ico', '/conversations', '/icons/../conversations'):
        await guard({**phone, 'path': path}, None, send)
    assert reached == ['/icons/favicon.svg', '/favicon.ico']  # the rest needs pairing
