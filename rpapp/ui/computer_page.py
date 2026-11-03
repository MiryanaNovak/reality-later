"""This computer: the page the app opens in the browser when it starts.

    /computer

Only on the computer the app runs on (elsewhere, it says so). In plain words: that
the app runs in the background and the browser tab is only a window onto it; the
address to open it at here, and how other devices connect, with pairing and the
browser's security warning explained; buttons to open the settings file, show the
data folder, and quit the app. Everything it shows comes from launcher.ServerInfo.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from nicegui import Client, app, background_tasks, run, ui

from .. import APP_NAME, desktop
from ..access import Access, is_local
from ..launcher import ServerInfo
from .devices_page import DevicesSection
from .widgets import confirm, page_bar, panel

# When the example file can't be found (a packaged app without it)
MINIMAL_SETTINGS = f'''# Settings of the {APP_NAME} app: see config.example.toml for every setting, and what it does.
# After a change, quit the app and start it again.

[server]
'''

STOPPED_PAGE = f'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{APP_NAME} has stopped</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
         font: 16px/1.5 system-ui, -apple-system, 'Segoe UI', sans-serif; background: Canvas; color: CanvasText; }}
  main {{ max-width: 26rem; margin: 24px; }}
  h1 {{ font: 600 1.5rem/1.25 Georgia, serif; margin: 0 0 12px; }}
  p {{ margin: 0 0 10px; opacity: .8; }}
</style></head>
<body><main><h1>{APP_NAME} has stopped</h1>
<p>It doesn't answer any more, here or on your other devices.</p>
<p>To use it again, start it again: it opens this page by itself. You can close this tab.</p></main></body></html>'''


def quit_everywhere(quit_app: Callable[[], None] | None = None) -> None:
    """Quit the app: every tab open on it, here and on other devices, says it stopped (once it's gone, it
    couldn't update them), then it stops. The Quit button below and the tray icon's Quit (tray.py) do this."""
    code = (f'window.socket && window.socket.disconnect(); document.open(); '
            f'document.write({json.dumps(STOPPED_PAGE)}); document.close();')
    for client in list(Client.instances.values()):
        if client.has_socket_connection:
            client.run_javascript(code)

    async def stop() -> None:
        await asyncio.sleep(0.5)  # for the pages to change first
        (quit_app or app.shutdown)()

    background_tasks.create(stop(), name='quit the app')


def on_this_computer() -> bool:
    """Whether the browser showing this page runs on the computer the app runs on (see access.is_local)."""
    request = ui.context.client.request
    if request is None:
        return False
    headers = {k.lower(): v for k, v in request.headers.items()}
    return is_local(request.client.host if request.client else None, headers)


class ComputerPage:
    def __init__(self, server: ServerInfo, access: Access | None, *,
                 quit_app: Callable[[], None] | None = None) -> None:
        self.server = server
        self.access = access
        self.devices = DevicesSection(access)
        self.quit_app = quit_app or app.shutdown

    def page(self) -> None:
        page_bar('This computer')
        if not on_this_computer():
            ui.label(f'This page only opens on the computer {APP_NAME} runs on.') \
                .classes('muted').mark('computer-elsewhere')
            return
        self._status()
        self._here()
        self._other_devices()
        self._settings_file()
        self._data()
        self._quit()

    # -- the parts ---------------------------------------------------------------------------

    def _status(self) -> None:
        with ui.column().classes('gap-2 w-full'):
            with ui.row().classes('items-center gap-3 no-wrap'):
                ui.element('span').classes('running-dot')
                ui.label(f'{APP_NAME} is running').classes('page-title').mark('computer-running')
            ui.label('It runs in the background on this computer. This browser tab is only a window onto it: '
                     'you can close it, and the app keeps running. To come back to this page, start the app '
                     'again, or open the address below.').classes('muted')
            started = self.server.started
            ui.label(f'Running since {started:%H:%M}, {started.day} {started:%b %Y}.').classes('text-sm muted')

    def _here(self) -> None:
        url = self.server.local_url
        with panel('On this computer'):
            if url:
                ui.label('Open the app at this address. Bookmark it to come back: here, it needs no pairing, '
                         'and the browser shows no warning.').classes('text-sm')
                _address(url, link=True, marker='local-address')
            with ui.row().classes('gap-2'):
                ui.button('Open the app', icon='arrow_forward', on_click=lambda: ui.navigate.to('/')) \
                    .props('unelevated').mark('open-app')

    def _other_devices(self) -> None:
        server = self.server.config.server
        urls = self.server.network_urls()
        with panel('On your phone, tablet or another computer'):
            if not urls:
                ui.label(f'Other devices can\'t reach the app: it only listens on this computer '
                         f'(address = "{server.address}" in the settings file). To use it from your phone, set '
                         'address = "0.0.0.0" there, then quit the app and start it again.') \
                    .classes('text-sm').mark('no-network')
                return
            ui.label('Connect the device to the same Wi-Fi or network as this computer. Then:').classes('text-sm')

            if server.pairing:
                ui.label('1. Pair it, the first time').classes('step-title')
                ui.label('So that only your devices can use the app, each one is paired once. Click Pair a '
                         'device, and scan the code it shows with the device\'s camera: that opens the app on the '
                         'device, paired.').classes('text-sm')
                self.devices.pairing_tools()
                ui.label('A device that can\'t scan a code (a laptop, a TV…) can open the address below '
                         'instead. It then shows a code, and asks to be allowed, here.').classes('text-sm muted')
                ui.label('2. Its address').classes('step-title')
            else:
                ui.label('Pairing is off (pairing = false in the settings file): any device on your network can '
                         'use the app.').classes('text-sm muted').mark('pairing-off-note')
            ui.label('Open this on the device, and bookmark it (or add it to its home screen):').classes('text-sm')
            _address(urls[0], marker='network-address')
            if len(urls) > 1:
                with ui.row().classes('items-baseline gap-x-2 gap-y-0').mark('other-addresses'):
                    ui.label('If it doesn\'t answer there, depending on the network:').classes('text-xs muted')
                    for other in urls[1:4]:
                        ui.label(other).classes('text-xs muted file-path')

            if self.server.certificate:
                ui.label(f'{"3. " if server.pairing else ""}The browser\'s warning, the first time') \
                    .classes('step-title')
                self._warning()
            elif not self.server.https:
                ui.label('Connections from other devices aren\'t encrypted (https = false in the settings file): '
                         'others on the same network could read them.').classes('text-sm muted').mark('no-https')

    def _warning(self) -> None:
        certificate = self.server.certificate
        assert certificate is not None
        ui.label('The device\'s browser warns that the connection isn\'t private (or "not secure", or "potential '
                 'security risk"). That\'s expected: to encrypt the connection, the app made its own certificate, '
                 'and browsers only vouch for certificates bought from a few companies. The connection is '
                 'encrypted all the same. Continue past the warning:').classes('text-sm').mark('https-warning')
        ui.html('<ul class="browser-steps text-sm">'
                '<li><b>Chrome, Edge, Samsung Internet:</b> Advanced, then Proceed to … (unsafe)</li>'
                '<li><b>Firefox:</b> Advanced…, then Accept the Risk and Continue</li>'
                '<li><b>Safari</b> (iPhone, iPad, Mac): Show Details, then visit this website</li>'
                '</ul>')
        renewal = certificate.expires - timedelta(days=30)
        ui.label('The browser remembers it, though it may ask again now and then, and after the app renews its '
                 f'certificate (around {renewal:%B %Y}).').classes('text-sm muted')
        ui.label('To be sure that it\'s this app and not an impostor on your network, check in the warning\'s '
                 'details that the certificate\'s SHA-256 fingerprint is:').classes('text-sm muted')
        ui.label(certificate.fingerprint).classes('fingerprint').mark('fingerprint')

    def _settings_file(self) -> None:
        path = self.server.config_path

        @ui.refreshable
        def note() -> None:
            if not path.exists():
                ui.label('It doesn\'t exist yet: the app uses its defaults. Opening it creates it, with every '
                         'setting explained.').classes('text-sm muted').mark('no-settings-file')

        with panel('Settings file', 'The port, which devices may connect, HTTPS… each setting is explained in it.'):
            ui.label(str(path)).classes('file-path').mark('settings-path')
            note()

            async def open_it() -> None:
                created = False
                if not path.exists():
                    try:
                        self._create_settings(path)
                    except OSError as e:
                        ui.notify(f'The settings file can\'t be made: {e}', type='negative', multi_line=True)
                        return
                    created = True
                    note.refresh()
                if await _run(desktop.open_in_editor, path, where=path):
                    ui.notify('Made the settings file, and opened it in your text editor.' if created
                              else 'Opened it in your text editor.')

            with ui.row().classes('gap-2'):
                ui.button('Open the settings file', icon='edit_note', on_click=open_it) \
                    .props('outline').mark('open-settings')
                ui.button('Show in folder', icon='folder_open',
                          on_click=lambda: _run(desktop.show_in_folder, path if path.exists() else path.parent,
                                                where=path.parent)) \
                    .props('flat').mark('show-settings')
            ui.label('Changes apply the next time the app starts: save the file, quit the app (below), and start it '
                     'again.').classes('text-sm muted')

    def _create_settings(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)  # (installed: the user's folder for it may not exist yet)
        example = self.server.example_path
        if example.is_file():
            shutil.copyfile(example, path)
        else:
            path.write_text(MINIMAL_SETTINGS, encoding='utf-8')

    def _data(self) -> None:
        folder = self.server.data_dir
        with panel('Your data', 'Characters, personas, lore books, conversations, connections and settings.'):
            ui.label(str(folder)).classes('file-path').mark('data-path')
            ui.label('To back them up, quit the app, then copy this folder.').classes('text-sm muted')
            ui.button('Show the folder', icon='folder_open',
                      on_click=lambda: _run(desktop.show_in_folder, folder, where=folder)) \
                .props('outline').classes('self-start').mark('show-data')

    def _quit(self) -> None:
        async def quit_app() -> None:
            if not await confirm(f'Quit {APP_NAME}?', 'It stops answering, here and on your other devices, until you '
                                 'start it again.', 'Quit'):
                return
            quit_everywhere(self.quit_app)

        with panel('Quit'):
            ui.label('Quitting stops the app, here and on your other devices, until you start it again. Closing the '
                     'window it runs in stops it too, and so does Quit in its tray icon\'s menu.').classes('text-sm')
            ui.button('Quit the app', icon='power_settings_new', color='negative', on_click=quit_app) \
                .props('outline').classes('self-start').mark('quit-app')


def _address(url: str, *, link: bool = False, marker: str = '') -> None:
    with ui.row().classes('address-box items-center no-wrap gap-1').mark(marker):
        if link:
            ui.link(url, url).classes('address grow min-w-0')
        else:
            ui.label(url).classes('address grow min-w-0')
        ui.button(icon='content_copy', on_click=lambda: ui.notify('Copied')) \
            .props('flat round dense aria-label="Copy the address"') \
            .on('click', js_handler=f'() => window.rpCopy({json.dumps(url)})')


async def _run(action: Callable[[Path], None], path: Path, *, where: Path) -> bool:
    """Do a desktop action off the page's thread; say so if it can't be done."""
    try:
        await run.io_bound(action, path)
        return True
    except (desktop.DesktopError, OSError) as e:
        ui.notify(f'{e} It\'s in {where}', type='warning', multi_line=True, close_button=True)
        return False
