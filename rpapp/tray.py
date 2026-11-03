"""The tray icon: the app's icon in the system tray (the menu bar on a Mac), with a menu.

    Open Reality Later    the app in the browser (also a click on the icon, where the desktop allows)
    This computer         its "This computer" page: addresses, pairing, settings file, data folder
    Quit Reality Later    stops it, as the Quit button on that page does

It runs in a process of its own, started by main.py once the app runs (launcher.start_tray), as
`main.py tray <data folder>`: a tray icon wants its process's main thread (macOS insists), which the
app's server already has, and a problem with the desktop's tray can't take the app down with it. It
finds the app in <data>/running.json (see instance.py), asks it to quit with POST /local/quit, and
goes away by itself once the app stops answering, however it stopped.

On Windows and macOS it's pystray's; on Linux, the StatusNotifierItem standard over D-Bus, which
the desktops' trays understand (see tray_linux.py). Turned off with --no-tray or tray = false in
config.toml, and never shown without a desktop (started over SSH, or a server).

The icon is rpapp/icons/tray/: white on dark panels and taskbars, black on light ones. Windows says
which its taskbar is; on a Mac it's black, as a "template" the menu bar recolors itself; on Linux,
see tray_linux.py.
"""
from __future__ import annotations

import io
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image

from . import APP_NAME, ICONS, desktop
from .instance import Instance, Running, answers, ask_to_quit

PAGE = '/computer'  # (launcher.PAGE, without importing the server's side)
WATCH_EVERY = 2.0  # seconds between checks that the app still runs
TRAY_SIZES = (16, 20, 22, 24, 32, 44, 48, 64)  # the sizes in icons/tray/, made for each


class NoTray(Exception):
    """The tray icon can't be shown here: the message says why."""


@dataclass
class Entry:
    """One line of the menu (label None: a separator)."""
    label: str | None
    action: Callable[[], None] | None = None
    default: bool = False  # what a click on the icon does


class Actions:
    """What the menu does, to the running copy of the app."""

    def __init__(self, running: Running, *, opener: Callable[[str], bool] = desktop.open_browser,
                 ping: Callable[[Running], bool] = answers, transport: httpx.BaseTransport | None = None) -> None:
        self.running = running
        self.opener = opener
        self.ping = ping
        self.transport = transport

    def open_app(self) -> None:
        self._open('/')

    def open_computer(self) -> None:
        self._open(PAGE)

    def quit_app(self) -> None:
        ask_to_quit(self.running, transport=self.transport)  # (if it's gone already, the watch notices)

    def alive(self) -> bool:
        return self.ping(self.running)

    def menu(self) -> list[Entry]:
        return [Entry(f'Open {APP_NAME}', self.open_app, default=True),
                Entry('This computer', self.open_computer),
                Entry(None),
                Entry(f'Quit {APP_NAME}', self.quit_app)]

    def _open(self, path: str) -> None:
        if self.running.url and not self.opener(self.running.url + path):
            print(f'The browser couldn\'t be opened: open {self.running.url + path} yourself.', flush=True)


def watch(actions: Actions, stop: Callable[[], None], *, every: float = WATCH_EVERY,
          stopped: threading.Event | None = None) -> None:
    """Call `stop` once the app no longer answers (checks every `every` seconds, until `stopped` is set)."""
    stopped = stopped or threading.Event()
    while not stopped.wait(every):
        if not actions.alive():
            stop()
            return


# --- the icon -----------------------------------------------------------------------------------

def tray_image(size: int = 64, variant: str = 'white') -> Image.Image:
    """The tray icon, 'white' (for dark panels) or 'black' (light ones): the file made for that size, or
    the next bigger one, shrunk."""
    made = min((s for s in TRAY_SIZES if s >= size), default=max(TRAY_SIZES))
    with Image.open(ICONS / 'tray' / f'{variant}-{made}.png') as image:
        image = image.convert('RGBA')
    return image if made == size else image.resize((size, size), Image.Resampling.LANCZOS)


def windows_taskbar_is_light(read: Callable[[], int] | None = None) -> bool:
    """Whether Windows' taskbar is light (Settings › Personalization › Colors: "Windows mode"). Dark if unknown."""
    def from_registry() -> int:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            return winreg.QueryValueEx(key, 'SystemUsesLightTheme')[0]
    try:
        return bool((read or from_registry)())
    except Exception:  # (not Windows, or an older one without the setting)
        return False


# --- running it -----------------------------------------------------------------------------------

def run(data_dir: str | Path, *, wait: float = 120) -> int:
    """The tray icon's process (main.py tray <data folder>): until the app stops. Returns the exit code."""
    running = Instance(data_dir).wait_for_running(timeout=wait)
    if running is None or not running.url:
        print(f'No tray icon: {APP_NAME} doesn\'t answer.', file=sys.stderr, flush=True)
        return 1
    actions = Actions(running)
    try:
        if sys.platform.startswith('linux'):
            from .tray_linux import run_sni  # (D-Bus: only on Linux)
            run_sni(actions)
        else:
            run_pystray(actions)
    except NoTray as e:
        print(f'No tray icon: {e}', file=sys.stderr, flush=True)
        return 1
    return 0


def pystray_icon(actions: Actions):
    """The pystray icon for these actions (Windows, macOS), not shown yet."""
    try:
        import pystray
    except ImportError as e:
        raise NoTray(f'the pystray package is missing ({e}): pip install pystray') from e
    items = [pystray.Menu.SEPARATOR if entry.label is None else
             pystray.MenuItem(entry.label, _calling(entry.action), default=entry.default)
             for entry in actions.menu()]
    options = {}
    icon_class = pystray.Icon
    if sys.platform == 'darwin':  # in the menu bar only, not the Dock as well
        try:
            import AppKit
            application = AppKit.NSApplication.sharedApplication()
            application.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
            options['nsapplication'] = application
        except Exception:
            pass
        icon_class = _menu_bar_icon(pystray.Icon)
        variant = 'black'
    else:
        variant = 'black' if sys.platform == 'win32' and windows_taskbar_is_light() else 'white'
    return icon_class('reality-later', tray_image(64, variant), APP_NAME, menu=pystray.Menu(*items), **options)


def _menu_bar_icon(base: type) -> type:
    """pystray's Mac icon, sharp on Retina screens (the 44-pixel picture at 22 points, where pystray would
    shrink it to 22 pixels) and a "template": the menu bar colors it for its light or dark look."""
    class MenuBarIcon(base):
        def _assert_image(self) -> None:
            try:
                import AppKit
                import Foundation
                points = int(self._status_bar.thickness())
                if self._icon_image is not None and tuple(self._icon_image.size()) == (points, points):
                    return
                data = io.BytesIO()
                tray_image(points * 2, 'black').save(data, 'png')
                image = AppKit.NSImage.alloc().initWithData_(Foundation.NSData(data.getvalue()))
                image.setSize_((points, points))
                image.setTemplate_(True)
                self._icon_image = image
                self._status_item.button().setImage_(image)
            except Exception:  # (whatever it is, pystray's own way still shows an icon)
                super()._assert_image()
    return MenuBarIcon


def run_pystray(actions: Actions) -> None:
    icon = pystray_icon(actions)

    def setup(icon) -> None:  # (in a thread of pystray's, while it runs the icon)
        icon.visible = True
        print(f'Tray icon: in the system tray. Its menu opens {APP_NAME}, or quits it.', flush=True)
        watch(actions, icon.stop)

    icon.run(setup)


def _calling(action: Callable[[], None] | None) -> Callable[[], None]:
    """A menu action, in a thread of its own: opening the browser or quitting can take a moment."""
    def call() -> None:
        if action:
            threading.Thread(target=action, name='tray action', daemon=True).start()
    return call
