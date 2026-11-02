"""The tray icon: the app's icon in the system tray (the menu bar on a Mac), with a menu.

    Open Roleplay      the app in the browser (also a click on the icon, where the desktop allows)
    This computer      its "This computer" page: addresses, pairing, settings file, data folder
    Quit Roleplay      stops it, as the Quit button on that page does

It runs in a process of its own, started by main.py once the app runs (launcher.start_tray), as
`main.py tray <data folder>`: a tray icon wants its process's main thread (macOS insists), which the
app's server already has, and a problem with the desktop's tray can't take the app down with it. It
finds the app in <data>/running.json (see instance.py), asks it to quit with POST /local/quit, and
goes away by itself once the app stops answering, however it stopped.

On Windows and macOS it's pystray's; on Linux, the StatusNotifierItem standard over D-Bus, which
the desktops' trays understand (see tray_linux.py). Turned off with --no-tray or tray = false in
config.toml, and never shown without a desktop (started over SSH, or a server).
"""
from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

from . import APP_NAME, desktop
from .instance import HEADER, QUIT_PATH, Instance, Running, answers

PAGE = '/computer'  # (launcher.PAGE, without importing the server's side)
WATCH_EVERY = 2.0  # seconds between checks that the app still runs
ACCENT, ACCENT_DARK = (31, 122, 112), (21, 92, 84)  # the default accent (ui/theme.py)


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
        try:
            with httpx.Client(timeout=5, verify=False, trust_env=False, transport=self.transport) as http:
                http.post(f'{self.running.api}{QUIT_PATH}', headers={HEADER: self.running.id})
        except httpx.HTTPError:
            pass  # it's gone already, or about to: the watch notices either way

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

def icon_image(size: int = 64) -> Image.Image:
    """The app's icon: a speech bubble on the accent color, drawn large then shrunk (smooth at any size)."""
    big = 256
    image = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, big - 8, big - 8), radius=56, fill=ACCENT)
    draw.rounded_rectangle((44, 56, 212, 172), radius=44, fill='white')  # the bubble
    draw.polygon([(76, 160), (124, 166), (68, 214)], fill='white')       # its tail
    for x in (88, 128, 168):                                             # the "…" of someone writing
        draw.ellipse((x - 13, 101, x + 13, 127), fill=ACCENT_DARK)
    return image.resize((size, size), Image.Resampling.LANCZOS)


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
    if sys.platform == 'darwin':  # in the menu bar only, not the Dock as well
        try:
            import AppKit
            application = AppKit.NSApplication.sharedApplication()
            application.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
            options['nsapplication'] = application
        except Exception:
            pass
    return pystray.Icon('roleplay', icon_image(64), APP_NAME, menu=pystray.Menu(*items), **options)


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
