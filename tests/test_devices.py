"""Pairing devices: the registry, who counts as the computer itself, the guard and its pages, the commands."""
import asyncio
import json
import os
import stat
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from nicegui import ui
from nicegui.testing import user_simulation

from rpapp import cli
from rpapp.access import COOKIE, JOIN_COOKIE, Access, AccessGuard, Who, is_local
from rpapp.config import load_config
from rpapp.devices import DeviceRegistry, TooManyRequests, describe_agent, normalize_code
from rpapp.instance import Instance
from rpapp.ui.app import build_root

ON_WINDOWS = os.name == 'nt'  # (file permissions below are Unix's: on Windows, a user's folder is theirs alone)

PHONE = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36'
LAPTOP = 'Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0'


# --- the registry ----------------------------------------------------------------------------

def test_pairing_links_work_once(tmp_path):
    devices = DeviceRegistry(tmp_path)
    link = devices.new_link()
    device, token = devices.use_link(link.code, user_agent=PHONE, address='192.168.1.33')
    assert device.name == 'Chrome on Android' and devices.find(token) is device
    assert link.used_by == 'Chrome on Android'
    assert devices.use_link(link.code, user_agent=PHONE, address='192.168.1.34') is None  # once only
    assert devices.use_link('made-up', user_agent=PHONE, address='x') is None

    expired = devices.new_link()
    expired.expires = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert devices.use_link(expired.code, user_agent=PHONE, address='x') is None

    # Saved: only a hash of the token, in a file for you only.
    data = json.loads(devices.path.read_text(encoding='utf-8'))
    assert token not in devices.path.read_text(encoding='utf-8') and data['devices'][0]['name'] == 'Chrome on Android'
    assert ON_WINDOWS or stat.S_IMODE(devices.path.stat().st_mode) == 0o600
    again = DeviceRegistry(tmp_path)
    assert again.find(token).id == device.id and again.find('wrong') is None and again.find('') is None

    again.rename(device.id, '  Mira\'s phone ')
    assert DeviceRegistry(tmp_path).devices[0].name == 'Mira\'s phone'
    assert again.remove(device.id) and again.find(token) is None


def test_requests_are_allowed_or_denied(tmp_path):
    devices = DeviceRegistry(tmp_path)
    request = devices.new_request(address='192.168.1.40', user_agent=LAPTOP)
    assert len(request.code) == 7 and request.code[3] == '-' and request.name == 'Firefox on Linux'
    assert devices.pending() == [request]
    assert devices.claim(request.id) is None  # not allowed yet
    assert devices.answer(request.code.lower().replace('-', ' '), allow=True) is request  # as typed
    assert devices.pending() == []
    device, token = devices.claim(request.id)
    assert devices.find(token) is device and device.how == 'request'
    assert devices.claim(request.id) is None  # once

    refused = devices.new_request(address='192.168.1.41', user_agent=LAPTOP)
    devices.answer(refused.code, allow=False)
    assert devices.claim(refused.id) is None and refused.status == 'denied'
    assert devices.answer('ZZZ-ZZZ', allow=True) is None

    for _ in range(3):
        devices.new_request(address='10.0.0.9', user_agent=LAPTOP)
    with pytest.raises(TooManyRequests):
        devices.new_request(address='10.0.0.9', user_agent=LAPTOP)


def test_describe_agent_and_codes():
    assert describe_agent(PHONE) == 'Chrome on Android'
    assert describe_agent(LAPTOP) == 'Firefox on Linux'
    assert describe_agent('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) AppleWebKit Version/18.0 Mobile Safari') \
        == 'Safari on iPhone'
    assert describe_agent('') == 'A browser'
    assert normalize_code(' 7kq-2mx ') == '7KQ2MX'


# --- who counts as the computer itself -----------------------------------------------------------

@pytest.mark.parametrize('client, headers, local', [
    ('127.0.0.1', {'host': 'localhost:8090'}, True),
    ('127.0.0.1', {'host': '127.0.0.1:8090'}, True),
    ('::1', {'host': '[::1]:8090'}, True),
    ('::ffff:127.0.0.1', {'host': 'localhost'}, True),
    ('192.168.1.33', {'host': 'localhost:8090'}, False),               # another device
    ('127.0.0.1', {'host': 'desktop.tail1234.ts.net'}, False),         # Tailscale Serve
    ('127.0.0.1', {'host': 'localhost:8090', 'x-forwarded-for': '100.64.1.2'}, False),  # any proxy
    ('127.0.0.1', {'host': 'localhost', 'tailscale-user-login': 'me@example.com'}, False),
    ('127.0.0.1', {'host': 'evil.example:8090'}, False),               # DNS rebinding
    ('testclient', {'host': 'localhost'}, False),
])
def test_is_local(client, headers, local):
    assert is_local(client, headers) is local


# --- the guard and the pairing pages ------------------------------------------------------------

def guarded_app(tmp_path) -> tuple[FastAPI, Access]:
    access = Access(DeviceRegistry(tmp_path), port=8090)
    app = FastAPI()
    access.register_routes(app)

    @app.get('/')
    def home():
        return PlainTextResponse('the app')

    @app.get('/files/x.js')
    def asset():
        return PlainTextResponse('code')

    app.add_middleware(AccessGuard, access=access)
    return app, access


def client(app, ip: str, host: str = '192.168.1.20:8090') -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 51000)), base_url=f'http://{host}',
                             headers={'user-agent': LAPTOP})


HTML = {'accept': 'text/html'}


async def test_unpaired_devices_are_sent_to_join_and_can_be_allowed(tmp_path):
    app, access = guarded_app(tmp_path)
    async with client(app, '192.168.1.40') as laptop:
        assert (await laptop.get('/', headers=HTML)).headers['location'] == '/join'
        assert (await laptop.get('/files/x.js')).status_code == 401

        page = await laptop.get('/join')
        assert page.status_code == 200 and JOIN_COOKIE in page.cookies
        [request] = access.devices.pending()
        assert request.code in page.text and 'python main.py approve' in page.text
        assert (await laptop.get('/join')).text.count(request.code) >= 1  # a reload keeps the same request
        assert len(access.devices.pending()) == 1
        assert (await laptop.get('/join/status')).json() == {'status': 'waiting'}

        access.devices.answer(request.code, allow=True)  # on the computer
        status = await laptop.get('/join/status')
        assert status.json() == {'status': 'approved'} and COOKIE in status.cookies
        home = await laptop.get('/', headers=HTML)
        assert home.status_code == 200 and home.text == 'the app'
        assert access.devices.devices[0].name == 'Firefox on Linux'

        # Signing out removes the device.
        out = await laptop.get('/devices/sign-out')
        assert out.headers['location'] == '/join' and access.devices.devices == []
        laptop.cookies.clear()
        assert (await laptop.get('/', headers=HTML)).status_code == 303


async def test_denied_requests_can_ask_again(tmp_path):
    app, access = guarded_app(tmp_path)
    async with client(app, '192.168.1.40') as laptop:
        await laptop.get('/join')
        [request] = access.devices.pending()
        access.devices.answer(request.code, allow=False)
        assert (await laptop.get('/join/status')).json() == {'status': 'denied'}
        assert request.code in (await laptop.get('/join')).text  # still says so
        await laptop.get('/join?again')
        [fresh] = access.devices.pending()
        assert fresh.code != request.code or fresh.id != request.id


async def test_pairing_link(tmp_path):
    app, access = guarded_app(tmp_path)
    link = access.devices.new_link()
    url = access.link_url(link.code, '192.168.1.20')
    assert url == f'http://192.168.1.20:8090/pair?code={link.code}'
    assert access.link_url('c', 'fe80::1').startswith('http://[fe80::1]:8090/')
    async with client(app, '192.168.1.33') as phone:
        paired = await phone.get(f'/pair?code={link.code}')
        assert paired.status_code == 303 and paired.headers['location'] == '/' and COOKIE in paired.cookies
        assert (await phone.get('/', headers=HTML)).text == 'the app'
    async with client(app, '192.168.1.34') as other:
        used = await other.get(f'/pair?code={link.code}')
        assert used.status_code == 410 and 'work any more' in used.text


async def test_the_computer_itself_needs_no_pairing_but_proxies_do(tmp_path):
    app, access = guarded_app(tmp_path)
    async with client(app, '127.0.0.1', 'localhost:8090') as computer:
        assert (await computer.get('/', headers=HTML)).text == 'the app'
        assert (await computer.get('/', headers={**HTML, 'x-forwarded-for': '100.64.1.2'})).status_code == 303
    async with client(app, '127.0.0.1', 'desktop.tail1234.ts.net') as via_tailscale:
        assert (await via_tailscale.get('/', headers=HTML)).status_code == 303


async def test_local_endpoints_are_for_the_computer_and_not_web_pages(tmp_path):
    app, access = guarded_app(tmp_path)
    async with client(app, '127.0.0.1', '127.0.0.1:8090') as computer:
        made = await computer.post('/local/pair', json={'address': '192.168.1.20'})
        assert made.status_code == 200 and made.json()['url'].startswith('http://192.168.1.20:8090/pair?code=')
        refused = await computer.post('/local/pair', json={}, headers={'origin': 'https://evil.example'})
        assert refused.status_code == 403
    async with client(app, '192.168.1.40') as laptop:
        assert (await laptop.post('/local/pair', json={})).status_code == 401


async def test_websockets_of_unpaired_devices_are_closed(tmp_path):
    _, access = guarded_app(tmp_path)
    reached, sent = [], []

    async def inner(scope, receive, send):
        reached.append(scope['type'])

    async def send(message):
        sent.append(message)

    guard = AccessGuard(inner, access)
    await guard({'type': 'websocket', 'path': '/_nicegui_ws/socket.io/', 'client': ('192.168.1.40', 1),
                 'headers': [(b'host', b'192.168.1.20:8090')]}, None, send)
    assert sent == [{'type': 'websocket.close', 'code': 1008}] and reached == []
    await guard({'type': 'websocket', 'path': '/_nicegui_ws/socket.io/', 'client': ('127.0.0.1', 1),
                 'headers': [(b'host', b'localhost:8090')]}, None, send)
    assert reached == ['websocket']


# --- the terminal commands ----------------------------------------------------------------------

def test_commands(tmp_path, capsys):
    app, access = guarded_app(tmp_path)
    config = load_config(tmp_path)  # no config.toml: the defaults

    assert cli.run(['pair', '192.168.1.20'], config, transport=SyncBridge(app)) == 0
    assert 'http://192.168.1.20:8090/pair?code=' in capsys.readouterr().out

    assert cli.run(['approve'], config, transport=SyncBridge(app)) == 0
    assert 'No device is waiting' in capsys.readouterr().out
    request = access.devices.new_request(address='192.168.1.40', user_agent=LAPTOP)
    assert cli.run(['approve'], config, transport=SyncBridge(app)) == 0
    assert request.code in capsys.readouterr().out
    assert cli.run(['approve', request.code.lower()], config, transport=SyncBridge(app)) == 0
    assert 'Allowed: Firefox on Linux, from 192.168.1.40.' in capsys.readouterr().out
    assert request.status == 'approved'
    assert cli.run(['deny', 'ZZZ-ZZZ'], config, transport=SyncBridge(app)) == 1


def test_commands_reach_the_running_copy_where_it_answers(tmp_path, capsys):
    app, access = guarded_app(tmp_path)
    bridge = SyncBridge(app)
    config = load_config(tmp_path)
    assert cli.run(['approve'], config, data_dir=tmp_path / 'data', transport=bridge) == 0
    assert bridge.urls[-1] == 'http://127.0.0.1:8091/local/requests'  # no running.json: as config.toml says

    held = Instance(tmp_path / 'data')
    held.acquire()
    held.publish('http://localhost:9191')  # the running copy started with other settings
    assert cli.run(['approve'], config, data_dir=tmp_path / 'data', transport=bridge) == 0
    assert bridge.urls[-1] == 'http://127.0.0.1:9191/local/requests'
    held.release()

    (tmp_path / 'config.toml').write_text('[server]\naddress = "192.168.1.20"\nlocal_port = 0\n', encoding='utf-8')
    assert cli.run(['approve'], load_config(tmp_path), transport=bridge) == 1
    assert 'This computer can\'t reach the app' in capsys.readouterr().out


class SyncBridge(httpx.BaseTransport):
    """Lets the (synchronous) command talk to the app in a test, as if from the computer itself."""

    def __init__(self, app) -> None:
        self.inner = httpx.ASGITransport(app=app, client=('127.0.0.1', 51000))
        self.urls: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        async def go():
            response = await self.inner.handle_async_request(request)
            body = b''.join([chunk async for chunk in response.stream])
            return httpx.Response(response.status_code, headers=response.headers, content=body)
        return asyncio.run(go())


# --- Settings › Devices -------------------------------------------------------------------------

@pytest.fixture
async def devices_page(tmp_path, monkeypatch):
    access = Access(DeviceRegistry(tmp_path), port=8090)
    monkeypatch.setattr('rpapp.ui.devices_page._who', lambda access: Who(local=True))  # as on the computer
    async with user_simulation(build_root(tmp_path, access=access)) as user:
        await user.open('/settings')
        await user.should_see('Devices')
        yield user, access


async def test_pairing_from_settings(devices_page):
    user, access = devices_page
    await user.should_see(marker='devices-local')
    await user.should_see(marker='no-devices')
    user.find(marker='pair-device').click()
    await user.should_see(marker='pair-qr')
    [link] = access.devices.links.values()
    await user.should_see(f'/pair?code={link.code}')
    access.devices.use_link(link.code, user_agent=PHONE, address='192.168.1.33')  # the phone opens it
    await user.should_see('Paired: Chrome on Android', retries=40)
    [device] = access.devices.devices
    await user.should_see(marker=f'device-{device.id}', retries=40)


async def test_allowing_a_waiting_device(devices_page):
    user, access = devices_page
    request = access.devices.new_request(address='192.168.1.40', user_agent=LAPTOP)
    await user.should_see(f'asks to use the app, with the code {request.code}', retries=40)  # the notice
    await user.should_see(marker=f'request-{request.code}', retries=40)
    with user.scope(marker=f'request-{request.code}'):
        user.find(marker='request-allow').click()
    await user.should_not_see(marker=f'request-{request.code}')
    assert request.status == 'approved'


async def test_removing_a_device(devices_page):
    user, access = devices_page
    link = access.devices.new_link()
    device, _ = access.devices.use_link(link.code, user_agent=PHONE, address='192.168.1.33')
    await user.open('/settings')
    await user.should_see(marker=f'device-{device.id}')
    with user.scope(marker=f'device-{device.id}'):
        user.find(marker='device-remove').click()
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Remove').click()
    await user.should_see(marker='no-devices')
    assert access.devices.devices == []


async def test_pairing_off(tmp_path):
    access = Access(DeviceRegistry(tmp_path), enabled=False)
    async with user_simulation(build_root(tmp_path, access=access)) as user:
        await user.open('/settings')
        await user.should_see(marker='pairing-off')


async def test_joining_returns_to_the_page_asked_for(tmp_path):
    app, access = guarded_app(tmp_path)
    async with client(app, '192.168.1.40') as laptop:
        sent = await laptop.get('/conversations/abc?x=1', headers=HTML)
        assert sent.headers['location'] == '/join?next=%2Fconversations%2Fabc%3Fx%3D1'
        page = await laptop.get(sent.headers['location'])
        assert 'location.href = "/conversations/abc?x=1"' in page.text.replace('\\u003c', '<')
        # Only paths of the app, and nothing that could break out of the page's script.
        evil = await laptop.get('/join', params={'next': '/</script><script>alert(1)</script>'})
        assert '<script>alert(1)' not in evil.text
        offsite = await laptop.get('/join', params={'next': '//evil.example/'})
        assert 'evil.example' not in offsite.text
