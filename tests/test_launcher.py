"""Starting the app: one copy at a time, this computer's own address, the messages and the browser."""
import asyncio
import socket
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from rpapp import access, cli, desktop, instance, launcher
from rpapp.config import Config, load_config
from rpapp.instance import Instance, Running


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


# --- one copy at a time -------------------------------------------------------------------------

def test_one_copy_at_a_time(tmp_path):
    first, second = Instance(tmp_path / 'data'), Instance(tmp_path / 'data')
    assert first.acquire()
    assert not second.acquire()  # held by the first one, as by another process
    assert second.running() is None  # the first hasn't said where it answers yet

    me = first.publish('http://localhost:8091')
    found = second.running()
    assert (found.id, found.url, found.api) == (me.id, 'http://localhost:8091', 'http://127.0.0.1:8091')

    first.release()
    assert second.running() is None  # running.json goes with it
    assert second.acquire()          # and the next start can go ahead
    second.release()


def test_a_crashed_copy_does_not_block_the_next_start(tmp_path):
    (tmp_path / 'running.json').write_text('{"id": "old", "pid": 99999, "url": "http://localhost:8091", '
                                           '"started": "2026-10-01T10:00:00+00:00"}', encoding='utf-8')
    fresh = Instance(tmp_path)
    assert fresh.acquire()  # the lock went away with the crashed process; the file is just left over
    assert fresh.publish('http://localhost:8091').id != 'old'
    fresh.release()


async def test_the_running_copy_answers_on_this_computers_address(tmp_path):
    app = FastAPI()
    instance.add_route(app)

    @app.get('/')
    def home():
        return PlainTextResponse('the app')

    port = free_port()
    listener = launcher.LocalListener(app, port)
    await listener.start()
    try:
        held = Instance(tmp_path)
        assert held.acquire()
        me = held.publish(f'http://localhost:{port}')
        assert await asyncio.to_thread(instance.answers, me)
        impostor = Running(id='someone-else', pid=1, url=me.url, started=me.started)
        assert not await asyncio.to_thread(instance.answers, impostor)  # something else answers there

        async with httpx.AsyncClient(trust_env=False) as http:
            assert (await http.get(f'http://127.0.0.1:{port}/')).text == 'the app'
            # Only this computer, by name: like the main address, nothing else counts as local.
            refused = await http.get(f'http://127.0.0.1:{port}{instance.PATH}', headers={'host': 'example.com'})
            assert refused.status_code == 403
        held.release()
    finally:
        await listener.stop()
    assert launcher.port_problem('127.0.0.1', port) is None  # let go of the port


async def test_the_quit_command_stops_the_running_copy(tmp_path, capsys):
    held = Instance(tmp_path)
    app = FastAPI()
    instance.add_route(app, quit_app=held.release)  # (as the app does: it stops, and lets go of the lock)
    port = free_port()
    listener = launcher.LocalListener(app, port)
    await listener.start()
    try:
        assert held.acquire()
        held.publish(f'http://localhost:{port}')
        assert Instance(tmp_path).in_use()
        assert await asyncio.to_thread(cli.run, ['quit'], None, data_dir=tmp_path) == 0
        assert 'Reality Later has stopped.' in capsys.readouterr().out
        assert not Instance(tmp_path).in_use()
    finally:
        held.release()
        await listener.stop()


async def test_quit_waits_for_it_to_stop(tmp_path):
    held = Instance(tmp_path)
    app = FastAPI()
    instance.add_route(app, quit_app=lambda: None)  # it says yes, but keeps running
    port = free_port()
    listener = launcher.LocalListener(app, port)
    await listener.start()
    try:
        held.acquire()
        held.publish(f'http://localhost:{port}')
        result = await asyncio.to_thread(instance.stop_running, tmp_path, timeout=0.6, step=0.1)
        assert result == instance.STILL_RUNNING
    finally:
        held.release()
        await listener.stop()


def test_quit_when_its_not_running(tmp_path, capsys):
    assert instance.stop_running(tmp_path / 'never started') == instance.NOT_RUNNING
    assert not (tmp_path / 'never started').exists()  # (it doesn't make the data folder)
    left_over = Instance(tmp_path)
    left_over.acquire()
    left_over.publish(f'http://localhost:{free_port()}')
    left_over.release()  # (as a copy that crashed: its files stay, but not its lock)
    (tmp_path / 'running.json').write_text('{"id": "old", "pid": 99999, "url": "http://localhost:1", '
                                           '"started": "2026-10-01T10:00:00+00:00"}', encoding='utf-8')
    assert cli.run(['quit'], None, data_dir=tmp_path) == 0
    assert 'Reality Later isn\'t running.' in capsys.readouterr().out


def test_quit_when_it_doesnt_answer(tmp_path, capsys):
    stuck = Instance(tmp_path)
    stuck.acquire()
    stuck.publish(f'http://localhost:{free_port()}')  # (nothing answers there)
    try:
        assert instance.stop_running(tmp_path, timeout=0.5, step=0.1) == instance.NO_ANSWER
    finally:
        stuck.release()


def test_nothing_answering(tmp_path):
    held = Instance(tmp_path)
    held.acquire()
    held.publish(f'http://localhost:{free_port()}')
    assert Instance(tmp_path).wait_for_running(timeout=0.3, step=0.1) is None
    held.release()


# --- ports ---------------------------------------------------------------------------------------

def test_ports_in_use(tmp_path):
    with socket.socket() as taken:  # on this computer's address
        taken.bind(('127.0.0.1', 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert launcher.port_problem('127.0.0.1', port) == f'port {port} is already used by another program'
    assert launcher.port_problem('127.0.0.1', port) is None

    # On all addresses, where the app listens for other devices. (Taken on 127.0.0.1 only, the app could
    # still have it on Windows, which lets the two share a port; not on Linux. Bound without listening:
    # Windows' firewall asks nothing.)
    with socket.socket() as taken:
        taken.bind(('0.0.0.0', 0))
        port = taken.getsockname()[1]
        (tmp_path / 'config.toml').write_text(f'[server]\nport = {port}\n', encoding='utf-8')
        info = launcher.ServerInfo(load_config(tmp_path), None, tmp_path / 'data')
        message = launcher.check_ports(info)
        assert f'its address (0.0.0.0): port {port} is already used' in message
        assert 'Set other ports in' in message and str(tmp_path / 'config.toml') in message
    assert launcher.check_ports(info) is None or f'port {port} is' not in launcher.check_ports(info)


# --- the browser ---------------------------------------------------------------------------------

@pytest.mark.parametrize('env, platform, reason', [
    ({'DISPLAY': ':0'}, 'linux', None),
    ({'WAYLAND_DISPLAY': 'wayland-0'}, 'linux', None),
    ({}, 'linux', 'no desktop to open it on'),                    # a server, a service started with the computer
    ({'DISPLAY': ':0', 'SSH_CONNECTION': '1.2.3.4 5 6.7.8.9 22'}, 'linux', 'started over SSH'),
    ({}, 'win32', None),
    ({}, 'darwin', None),
    ({'SSH_TTY': '/dev/ttys001'}, 'darwin', 'started over SSH'),
])
def test_when_to_open_the_browser(env, platform, reason):
    assert desktop.why_no_browser(env, platform) == reason


def test_browser_wanted(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'why_no_browser', lambda: None)
    assert launcher.browser_wanted(load_config(tmp_path)) is None
    assert launcher.browser_wanted(load_config(tmp_path), no_browser=True) == '--no-browser'
    (tmp_path / 'config.toml').write_text('[server]\nopen_browser = false\n', encoding='utf-8')
    assert launcher.browser_wanted(load_config(tmp_path)) == 'open_browser = false'
    monkeypatch.setattr(desktop, 'why_no_browser', lambda: 'no desktop to open it on')
    (tmp_path / 'config.toml').write_text('[server]\n', encoding='utf-8')
    assert launcher.browser_wanted(load_config(tmp_path)) == 'no desktop to open it on'


def test_once_it_answers_it_says_so_and_opens_the_page(tmp_path, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(desktop, 'open_browser', lambda url: opened.append(url) or True)
    monkeypatch.setattr(launcher, 'network_addresses', lambda listen: ['192.168.1.20', '100.101.1.2'])
    info = launcher.ServerInfo(load_config(tmp_path), None, tmp_path)
    me = Running(id='x', pid=1, url=info.local_url, started=datetime.now(timezone.utc))
    tries = iter([False, False, True])
    launcher.when_ready(info, me, open_browser=True, ping=lambda running: next(tries)).join(5)
    out = capsys.readouterr().out
    assert 'Reality Later is running. Leave this window open' in out
    assert 'On this computer, open   http://localhost:8091' in out
    assert 'On other devices, open   http://192.168.1.20:8090   (pair each one once, on the page below)' in out
    assert 'depending on the network: http://100.101.1.2:8090' in out
    assert opened == ['http://localhost:8091/computer']


def test_starting_again_opens_the_running_copy(tmp_path, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(desktop, 'open_browser', lambda url: opened.append(url) or True)
    running = Running(id='x', pid=1234, url='http://localhost:8091', started=datetime.now(timezone.utc))
    monkeypatch.setattr(Instance, 'wait_for_running', lambda self: running)
    assert launcher.show_running_copy(Instance(tmp_path), open_browser=True) == 0
    assert opened == ['http://localhost:8091/computer']
    assert 'already running' in capsys.readouterr().out

    assert launcher.show_running_copy(Instance(tmp_path), open_browser=False) == 0
    assert 'Open it in your browser: http://localhost:8091/computer' in capsys.readouterr().out

    monkeypatch.setattr(Instance, 'wait_for_running', lambda self: None)  # stuck
    assert launcher.show_running_copy(Instance(tmp_path), open_browser=True) == 1
    assert 'doesn\'t answer' in capsys.readouterr().err


# --- what's shown --------------------------------------------------------------------------------

def test_starting_lines(tmp_path):
    from rpapp.config import https_setup
    (tmp_path / 'config.toml').write_text('[server]\nallowed_networks = ["any"]\n', encoding='utf-8')
    config = load_config(tmp_path)
    info = launcher.ServerInfo(config, https_setup(config), tmp_path / 'data')
    text = '\n'.join(launcher.starting_lines(info))
    assert f'Settings: {tmp_path / "config.toml"}' in text
    assert 'made a new self-signed certificate' in text and info.certificate.fingerprint in text
    assert 'anyone who can reach this computer' in text
    assert info.network_urls() == [f'https://{host}:8090' for host in access.network_addresses('0.0.0.0')]


def test_network_addresses_likeliest_first(monkeypatch):
    monkeypatch.setattr(access, '_cards', lambda: [
        ('lo', '127.0.0.1'), ('docker0', '172.17.0.1'), ('tailscale0', '100.101.1.2'), ('eth0', '10.0.0.5'),
        ('wlan0', '192.168.1.20'), ('wlan0', 'fe80::1'), ('wlan0', 'fd00::20'), ('vEthernet (WSL)', '172.28.0.1')])
    monkeypatch.setattr(access.socket, 'gethostname', lambda: 'desk')
    assert access.network_addresses('0.0.0.0') == ['192.168.1.20', '10.0.0.5', '100.101.1.2', '172.17.0.1',
                                                   '172.28.0.1', 'desk.local']
    assert access.network_addresses('::')[:4] == ['192.168.1.20', '10.0.0.5', '100.101.1.2', 'fd00::20']
    assert access.network_addresses('192.168.1.20') == ['192.168.1.20']
    assert access.network_addresses('127.0.0.1') == [] and access.network_addresses('localhost') == []
    assert access.url_for('https', 'fd00::20', 8090) == 'https://[fd00::20]:8090'


def test_defaults_for_tests_and_the_page(tmp_path):
    info = launcher.ServerInfo(Config(folder=tmp_path), None, tmp_path / 'data')
    assert (info.scheme, info.page_url, info.certificate) == ('http', 'http://localhost:8091/computer', None)
    assert info.example_path == Path(__file__).resolve().parent.parent / 'config.example.toml'  # (the source's)
