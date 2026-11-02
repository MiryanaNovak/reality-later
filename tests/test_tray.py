"""The tray icon: when it's shown, what its menu does, the quit request, and the Linux tray over D-Bus."""
import asyncio
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from rpapp import desktop, instance, launcher, tray
from rpapp.config import load_config
from rpapp.instance import ENV, HEADER, QUIT_PATH, Running

RUNNING = Running(id='abc123', pid=1, url='http://localhost:8091', started=datetime.now(timezone.utc))


def wait_until(condition, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


# --- when it's shown ----------------------------------------------------------------------------

def test_when_the_tray_icon_is_shown(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'why_no_browser', lambda: None)
    config = load_config(tmp_path)
    assert config.server.tray and launcher.tray_wanted(config) is None
    assert launcher.tray_wanted(config, no_tray=True) == '--no-tray'
    assert launcher.tray_wanted(config, reload=True) == '--reload'
    (tmp_path / 'config.toml').write_text('[server]\ntray = false\n')
    assert launcher.tray_wanted(load_config(tmp_path)) == 'tray = false'
    monkeypatch.setattr(desktop, 'why_no_browser', lambda: 'started over SSH')
    assert launcher.tray_wanted(config) == 'started over SSH'


def test_its_process_is_the_app_again(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(subprocess, 'Popen', lambda args, **kw: started.append(args) or 'process')
    assert launcher.start_tray(tmp_path, script=Path('/app/main.py')) == 'process'
    assert started[-1] == [sys.executable, '/app/main.py', 'tray', str(tmp_path)]
    monkeypatch.setattr(sys, 'frozen', True, raising=False)  # a packaged app: the program itself
    launcher.start_tray(tmp_path, script=Path('/app/main.py'))
    assert started[-1] == [sys.executable, 'tray', str(tmp_path)]


def test_stopping_it():
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    launcher.stop_tray(process)
    assert process.poll() is not None
    launcher.stop_tray(process)  # (already stopped: nothing to do)
    launcher.stop_tray(None)


def test_main_py_knows_the_option():
    out = subprocess.run([sys.executable, 'main.py', '--help'], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parent.parent)
    assert '--no-tray' in out.stdout
    tray_alone = subprocess.run([sys.executable, 'main.py', 'tray'], capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parent.parent)
    assert tray_alone.returncode != 0 and 'starts this itself' in tray_alone.stderr


# --- the quit request -----------------------------------------------------------------------------

@pytest.fixture
def quit_app(monkeypatch):
    monkeypatch.setenv(ENV, 'abc123')
    quits = []
    app = FastAPI()
    instance.add_route(app, quit_app=lambda: quits.append(True))
    return TestClient(app, base_url='http://localhost:8091', client=('127.0.0.1', 50000)), quits


def test_only_the_tray_icon_can_quit(quit_app):
    client, quits = quit_app
    assert client.post(QUIT_PATH).status_code == 403  # a web page can't add the header
    assert client.post(QUIT_PATH, headers={HEADER: 'guess'}).status_code == 403
    assert client.post(QUIT_PATH, headers={HEADER: 'abc123', 'X-Forwarded-For': '100.64.1.2'}).status_code == 403
    assert client.get(QUIT_PATH).status_code == 405
    assert quits == []
    assert client.post(QUIT_PATH, headers={HEADER: 'abc123'}).json() == {'quitting': True}
    assert quits == [True]


def test_not_from_another_device(monkeypatch):
    monkeypatch.setenv(ENV, 'abc123')
    quits = []
    app = FastAPI()
    instance.add_route(app, quit_app=lambda: quits.append(True))
    client = TestClient(app, base_url='http://192.168.1.20:8090', client=('192.168.1.50', 50000))
    assert client.post(QUIT_PATH, headers={HEADER: 'abc123'}).status_code == 403 and quits == []
    plain = FastAPI()
    instance.add_route(plain)  # (without quit_app: no such route)
    assert TestClient(plain, base_url='http://localhost', client=('127.0.0.1', 1)).post(QUIT_PATH).status_code == 404


# --- what the menu does ---------------------------------------------------------------------------

def test_the_menu_actions():
    opened, sent = [], []

    def server(request: httpx.Request) -> httpx.Response:
        sent.append((request.method, str(request.url), request.headers.get(HEADER)))
        return httpx.Response(200, json={'quitting': True})

    actions = tray.Actions(RUNNING, opener=lambda url: opened.append(url) or True, ping=lambda r: True,
                           transport=httpx.MockTransport(server))
    assert [(e.label, e.default) for e in actions.menu()] == [
        ('Open Reality Later', True), ('This computer', False), (None, False), ('Quit Reality Later', False)]
    actions.open_app()
    actions.open_computer()
    assert opened == ['http://localhost:8091/', 'http://localhost:8091/computer']
    actions.quit_app()
    assert sent == [('POST', 'http://127.0.0.1:8091/local/quit', 'abc123')]  # (127.0.0.1: no name lookup)


def test_quitting_an_app_already_gone_is_fine():
    def gone(request):
        raise httpx.ConnectError('refused')
    tray.Actions(RUNNING, transport=httpx.MockTransport(gone)).quit_app()


def test_it_goes_away_with_the_app():
    answers = iter([True, True, False])
    actions = tray.Actions(RUNNING, ping=lambda r: next(answers))
    stopped = []
    tray.watch(actions, lambda: stopped.append(True), every=0.01)
    assert stopped == [True]


def test_without_the_app_there_is_no_icon(tmp_path, capsys):
    assert tray.run(tmp_path, wait=0.1) == 1
    assert 'No tray icon' in capsys.readouterr().err


def test_the_icon():
    for size in (16, 22, 32, 64):
        image = tray.icon_image(size)
        assert image.size == (size, size) and image.mode == 'RGBA'
    image = tray.icon_image(64)
    assert image.getpixel((0, 0))[3] == 0  # rounded corners: transparent
    assert image.getpixel((32, 6)) == (*tray.ACCENT, 255)  # the accent, around the bubble
    assert image.getpixel((32, 20))[:3] == (255, 255, 255)  # the bubble


# --- Windows and macOS: pystray ---------------------------------------------------------------------

def test_the_pystray_menu(monkeypatch):
    monkeypatch.setenv('PYSTRAY_BACKEND', 'dummy')  # (the menu is the same on every backend)
    pytest.importorskip('pystray')
    done = []
    actions = tray.Actions(RUNNING, opener=lambda url: done.append(url) or True, ping=lambda r: True)
    monkeypatch.setattr(actions, 'quit_app', lambda: done.append('quit'))
    icon = tray.pystray_icon(actions)
    items = list(icon.menu.items)
    assert [item.text for item in items] == ['Open Reality Later', 'This computer', '- - - -', 'Quit Reality Later']
    assert items[0].default and not items[1].default
    items[0](icon)
    items[3](icon)
    assert wait_until(lambda: len(done) == 2) and set(done) == {'http://localhost:8091/', 'quit'}


# --- Linux: a StatusNotifierItem on the session bus -------------------------------------------------

@pytest.fixture
def session_bus():
    """A D-Bus session bus of the test's own."""
    pytest.importorskip('dbus_fast')
    if not shutil.which('dbus-daemon'):
        pytest.skip('no dbus-daemon')
    daemon = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--print-address'], stdout=subprocess.PIPE,
                              text=True)
    address = daemon.stdout.readline().strip()
    yield address
    daemon.terminate()
    daemon.wait(5)


async def fake_tray(address: str):
    """A stand-in for the desktop's tray: it takes in the icons registered with it."""
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, method

    class Watcher(ServiceInterface):
        def __init__(self):
            super().__init__('org.kde.StatusNotifierWatcher')
            self.items = []

        @method()
        def RegisterStatusNotifierItem(self, service: 's'):  # noqa: F821 (a D-Bus type)
            self.items.append(service)

    bus = await MessageBus(bus_address=address).connect()
    watcher = Watcher()
    bus.export('/StatusNotifierWatcher', watcher)
    await bus.request_name('org.kde.StatusNotifierWatcher')
    return bus, watcher


async def until(condition, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.02)
    return True


async def test_the_linux_tray_icon(session_bus):
    from dbus_fast import Variant
    from dbus_fast.aio import MessageBus

    from rpapp.tray_linux import ITEM_PATH, MENU_PATH, show
    tray_bus, watcher = await fake_tray(session_bus)
    done, alive = [], [True]
    actions = tray.Actions(RUNNING, opener=lambda url: done.append(url) or True, ping=lambda r: alive[0])
    actions.quit_app = lambda: done.append('quit')
    shown = []
    task = asyncio.create_task(show(actions, bus_address=session_bus, every=0.05, on_shown=lambda: shown.append(1)))
    assert await until(lambda: watcher.items) and shown
    [name] = watcher.items
    assert name == f'org.kde.StatusNotifierItem-{os.getpid()}-1'

    # What the tray reads, then a click, as a desktop's tray does
    client = await MessageBus(bus_address=session_bus).connect()
    item = client.get_proxy_object(name, ITEM_PATH, await client.introspect(name, ITEM_PATH)) \
        .get_interface('org.kde.StatusNotifierItem')
    assert await item.get_title() == 'Reality Later' and await item.get_status() == 'Active'
    assert await item.get_menu() == MENU_PATH and not await item.get_item_is_menu()
    pixmaps = await item.get_icon_pixmap()
    assert [(w, h) for w, h, _ in pixmaps] == [(s, s) for s in (16, 22, 24, 32, 48, 64)]
    assert all(len(data) == w * h * 4 for w, h, data in pixmaps)
    await item.call_activate(0, 0)
    assert await until(lambda: done == ['http://localhost:8091/'])

    menu = client.get_proxy_object(name, MENU_PATH, await client.introspect(name, MENU_PATH)) \
        .get_interface('com.canonical.dbusmenu')
    revision, (root, props, children) = await menu.call_get_layout(0, -1, [])
    entries = [child.value for child in children]
    assert [e[1].get('label', Variant('s', '-')).value for e in entries] == \
        ['Open Reality Later', 'This computer', '-', 'Quit Reality Later']
    assert entries[2][1]['type'].value == 'separator'
    await menu.call_event(2, 'clicked', Variant('s', ''), 0)  # This computer
    await menu.call_event(4, 'hovered', Variant('s', ''), 0)  # (only clicks do something)
    await menu.call_event_group([[4, 'clicked', Variant('s', ''), 0]])  # Quit
    assert await until(lambda: done[1:] == ['http://localhost:8091/computer', 'quit'] or
                       sorted(done[1:]) == sorted(['http://localhost:8091/computer', 'quit']))

    # The tray restarts: the icon registers again with the new one
    tray_bus.disconnect()
    tray_bus, watcher = await fake_tray(session_bus)
    assert await until(lambda: watcher.items == [name])

    # The app stops: the icon goes, and its name with it
    alive[0] = False
    await asyncio.wait_for(task, 5)
    reply = await client.call(__import__('dbus_fast').Message(
        destination='org.freedesktop.DBus', path='/org/freedesktop/DBus', interface='org.freedesktop.DBus',
        member='NameHasOwner', signature='s', body=[name]))
    assert reply.body == [False]
    client.disconnect()
    tray_bus.disconnect()


async def test_no_tray_on_this_desktop(session_bus):
    from rpapp.tray_linux import show
    actions = tray.Actions(RUNNING, ping=lambda r: True)
    with pytest.raises(tray.NoTray, match='AppIndicator'):
        await show(actions, bus_address=session_bus)


async def test_no_session_bus():
    pytest.importorskip('dbus_fast')
    from rpapp.tray_linux import show
    with pytest.raises(tray.NoTray, match='session bus'):
        await show(tray.Actions(RUNNING), bus_address='unix:path=/nonexistent/bus')


def test_threads_are_daemons():
    """(The menu's actions run in threads that never keep the tray's process alive.)"""
    actions = []
    call = tray._calling(lambda: actions.append(threading.current_thread().daemon))
    call()
    assert wait_until(lambda: actions) and actions == [True]
