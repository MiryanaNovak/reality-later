"""The "This computer" page: only on the computer, what it says, and its buttons."""
import asyncio
import shutil
from pathlib import Path

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

from rpapp import desktop, launcher
from rpapp.access import Access, Who
from rpapp.config import https_setup, load_config
from rpapp.devices import DeviceRegistry
from rpapp.ui.app import build_root

EXAMPLE = Path(__file__).parent.parent / 'config.example.toml'


def here(monkeypatch, local: bool = True) -> None:
    for where in ('rpapp.ui.computer_page.on_this_computer', 'rpapp.ui.app.on_this_computer'):
        monkeypatch.setattr(where, lambda: local)
    monkeypatch.setattr('rpapp.ui.devices_page._who', lambda access: Who(local=local))


def text(user, marker: str) -> str:
    [label] = user.find(marker=marker).elements
    return label.text


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """The app as started with the defaults (HTTPS, its own certificate), seen from the computer."""
    shutil.copy(EXAMPLE, tmp_path / 'config.example.toml')
    config = load_config(tmp_path)
    info = launcher.ServerInfo(config, https_setup(config), tmp_path / 'data')
    access = Access(DeviceRegistry(tmp_path / 'data'), scheme='https')
    monkeypatch.setattr(launcher, 'network_addresses', lambda listen: ['192.168.1.20', '100.101.1.2'])
    monkeypatch.setattr('rpapp.access.network_addresses', lambda listen: ['192.168.1.20'])
    calls = []
    monkeypatch.setattr(desktop, 'open_in_editor', lambda path: calls.append(('edit', path)))
    monkeypatch.setattr(desktop, 'show_in_folder', lambda path: calls.append(('show', path)))
    here(monkeypatch)
    return tmp_path, info, access, calls


async def test_what_it_says(setup):
    tmp_path, info, access, _ = setup
    async with user_simulation(build_root(info.data_dir, access=access, server=info)) as user:
        await user.open('/computer')
        await user.should_see('Roleplay is running')
        await user.should_see('This browser tab is only a window onto it', retries=5)
        with user.scope(marker='local-address'):
            await user.should_see('http://localhost:8091')
        with user.scope(marker='network-address'):
            await user.should_see('https://192.168.1.20:8090')
        await user.should_see(marker='other-addresses')
        await user.should_see('Pair it, the first time')
        await user.should_see(marker='pair-device')
        await user.should_see(marker='https-warning')
        assert text(user, 'fingerprint') == info.certificate.fingerprint
        assert text(user, 'settings-path') == str(tmp_path / 'config.toml')
        await user.should_see(marker='no-settings-file')
        assert text(user, 'data-path') == str(tmp_path / 'data')
        await user.should_see(marker='nav-this-computer')  # in the menu, on the computer


async def test_pairing_from_it(setup):
    _, info, access, _ = setup
    async with user_simulation(build_root(info.data_dir, access=access, server=info)) as user:
        await user.open('/computer')
        request = access.devices.new_request(address='192.168.1.40', user_agent='Mozilla/5.0 (X11; Linux x86_64) '
                                             'Gecko/20100101 Firefox/131.0')
        await user.should_see(marker=f'request-{request.code}', retries=40)  # a device asking to join
        user.find(marker='pair-device').click()
        await user.should_see(marker='pair-qr')
        await user.should_see('https://192.168.1.20:8090/pair?code=')


async def test_settings_file_and_folders(setup):
    tmp_path, info, access, calls = setup
    async with user_simulation(build_root(info.data_dir, access=access, server=info)) as user:
        await user.open('/computer')
        user.find(marker='open-settings').click()
        await user.should_see('Made the settings file, and opened it in your text editor.', retries=50)
        config = tmp_path / 'config.toml'
        assert config.read_text() == EXAMPLE.read_text()  # made from the example, every setting explained
        assert calls == [('edit', config)]
        await user.should_not_see(marker='no-settings-file')
        assert load_config(tmp_path).server == info.config.server  # and it holds the defaults

        user.find(marker='open-settings').click()
        await user.should_see('Opened it in your text editor.', retries=50)
        user.find(marker='show-settings').click()
        user.find(marker='show-data').click()
        for _ in range(100):  # they run in threads, side by side
            if len(calls) == 4:
                break
            await asyncio.sleep(0.05)
        assert sorted(calls[2:]) == [('show', config), ('show', tmp_path / 'data')]


async def test_when_it_cant_open_something(setup, monkeypatch):
    tmp_path, info, access, _ = setup

    def fail(path):
        raise desktop.DesktopError('No file manager was found to show it.')

    monkeypatch.setattr(desktop, 'show_in_folder', fail)
    async with user_simulation(build_root(info.data_dir, access=access, server=info)) as user:
        await user.open('/computer')
        user.find(marker='show-data').click()
        await user.should_see(f'No file manager was found to show it. It\'s in {tmp_path / "data"}')


async def test_quitting(setup, monkeypatch):
    _, info, access, _ = setup
    quits = []
    monkeypatch.setattr('rpapp.ui.computer_page.app.shutdown', lambda: quits.append(True))
    async with user_simulation(build_root(info.data_dir, access=access, server=info)) as user:
        await user.open('/computer')
        user.find(marker='quit-app').click()
        await user.should_see(marker='confirm-dialog')
        with user.scope(marker='confirm-dialog'):
            user.find(kind=ui.button, content='Cancel').click()
        await user.should_not_see(marker='confirm-dialog')
        assert quits == []
        user.find(marker='quit-app').click()
        await user.should_see(marker='confirm-dialog')
        with user.scope(marker='confirm-dialog'):
            user.find(kind=ui.button, content='Quit').click()
        for _ in range(40):
            if quits:
                break
            await asyncio.sleep(0.05)
        assert quits == [True]


async def test_only_on_the_computer(setup, monkeypatch):
    _, info, access, _ = setup
    here(monkeypatch, local=False)
    async with user_simulation(build_root(info.data_dir, access=access, server=info)) as user:
        await user.open('/computer')
        await user.should_see(marker='computer-elsewhere')
        await user.should_not_see('Roleplay is running')
        await user.should_not_see(marker='quit-app')
        await user.should_not_see(marker='nav-this-computer')


async def test_plain_http_and_no_pairing(tmp_path, monkeypatch):
    (tmp_path / 'config.toml').write_text('[server]\nhttps = false\npairing = false\n')
    config = load_config(tmp_path)
    info = launcher.ServerInfo(config, https_setup(config), tmp_path / 'data')
    monkeypatch.setattr(launcher, 'network_addresses', lambda listen: ['192.168.1.20'])
    here(monkeypatch)
    access = Access(DeviceRegistry(tmp_path / 'data'), enabled=False)
    async with user_simulation(build_root(info.data_dir, access=access, server=info)) as user:
        await user.open('/computer')
        with user.scope(marker='local-address'):
            await user.should_see('http://localhost:8090')  # the main address is plain HTTP already
        with user.scope(marker='network-address'):
            await user.should_see('http://192.168.1.20:8090')
        await user.should_see(marker='pairing-off-note')
        await user.should_see(marker='no-https')
        await user.should_not_see(marker='https-warning')
        await user.should_not_see(marker='pair-device')


async def test_listening_on_this_computer_only(tmp_path, monkeypatch):
    (tmp_path / 'config.toml').write_text('[server]\naddress = "127.0.0.1"\n')
    config = load_config(tmp_path)
    info = launcher.ServerInfo(config, https_setup(config), tmp_path / 'data')
    here(monkeypatch)
    async with user_simulation(build_root(info.data_dir, access=Access(DeviceRegistry(tmp_path)), server=info)) \
            as user:
        await user.open('/computer')
        await user.should_see(marker='no-network')
        await user.should_not_see(marker='https-warning')
