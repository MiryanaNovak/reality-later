"""Starting the app so that it's clear it runs, and where to open it.

When you start the app (main.py):

1. If it's already running (see instance.py), the browser opens on the running copy's
   "This computer" page, and this second start ends there.
2. Otherwise it checks that its ports are free, says in the terminal how to open it,
   and once it answers, opens its "This computer" page in the browser (unless
   open_browser = false, --no-browser, or there's no desktop: see desktop.py).

The app answers on two addresses: the main one, which other devices use (HTTPS by
default), and, when needed, http://localhost:<local_port>, plain HTTP that only this
computer reaches (LocalListener). Browsers count localhost as secure, so this computer
never sees a certificate warning. Both serve the same app, with the same checks.
"""
from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn

from . import APP_NAME, desktop, windowless
from .access import network_addresses, url_for
from .certificates import Certificate
from .config import Config, HttpsSetup
from .instance import Instance, Running, answers
from .paths import example_config

log = logging.getLogger(__name__)

PAGE = '/computer'  # the "This computer" page (ui/computer_page.py)


@dataclass
class ServerInfo:
    """How the app runs, for the "This computer" page and the startup messages."""
    config: Config
    https: HttpsSetup | None
    data_dir: Path
    started: datetime = field(default_factory=lambda: datetime.now().astimezone())

    @property
    def scheme(self) -> str:
        return 'https' if self.https else 'http'

    @property
    def local_url(self) -> str | None:
        return self.config.server.local_url

    @property
    def page_url(self) -> str | None:
        return self.local_url + PAGE if self.local_url else None

    @property
    def certificate(self) -> Certificate | None:
        """The app's own self-signed certificate, when it uses one."""
        return self.https.self_signed if self.https else None

    @property
    def config_path(self) -> Path:
        return self.config.path

    @property
    def example_path(self) -> Path:
        return example_config(self.config.folder)

    def network_urls(self) -> list[str]:
        """The addresses other devices open, likeliest first."""
        return [url_for(self.scheme, host, self.config.server.port)
                for host in network_addresses(self.config.server.address)]


# -- this computer's own address ---------------------------------------------------------------

class _Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield  # Ctrl+C is the main server's: this one stops with the app (LocalListener.stop)


class LocalListener:
    """Serves the app as plain HTTP on 127.0.0.1 and ::1 (where available): only this computer reaches it."""

    def __init__(self, app: Any, port: int) -> None:
        self.app = app
        self.port = port
        self.server: _Server | None = None
        self.task: asyncio.Task | None = None

    async def start(self) -> None:
        try:
            sockets = loopback_sockets(self.port)
        except OSError as e:
            log.error('This computer\'s address, http://localhost:%s, can\'t be used: %s', self.port, e)
            return
        config = uvicorn.Config(self.app, lifespan='off', log_config=None, log_level='warning', ws='wsproto',
                                timeout_graceful_shutdown=2)
        self.server = _Server(config)
        self.task = asyncio.create_task(self._serve(sockets), name='local listener')

    async def _serve(self, sockets: list[socket.socket]) -> None:
        try:
            await self.server.serve(sockets=sockets)
        except SystemExit:  # uvicorn's way of saying it couldn't start
            log.error('This computer\'s address, http://localhost:%s, couldn\'t start', self.port)
        finally:
            for s in sockets:
                s.close()

    async def stop(self) -> None:
        if self.server is None or self.task is None:
            return
        self.server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(self.task), timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self.task.cancel()


def loopback_sockets(port: int) -> list[socket.socket]:
    """Listening sockets on 127.0.0.1 and, if this computer has IPv6, ::1 (browsers may try either)."""
    sockets = [_listen(socket.AF_INET, '127.0.0.1', port)]
    try:
        sockets.append(_listen(socket.AF_INET6, '::1', port))
    except OSError as e:
        if _in_use(e):  # something else would answer there
            for s in sockets:
                s.close()
            raise
    return sockets


def _listen(family: int, host: str, port: int) -> socket.socket:
    s = socket.socket(family, socket.SOCK_STREAM)
    try:
        if os.name != 'nt':  # (on Windows, this would let others take the port too)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        s.bind((host, port))
        s.listen(128)
        s.setblocking(False)
    except OSError:
        s.close()
        raise
    return s


def _in_use(e: OSError) -> bool:
    return e.errno == errno.EADDRINUSE or getattr(e, 'winerror', None) == 10048


def port_problem(host: str, port: int) -> str | None:
    """Why the app can't listen there, or None if it can (it tries, and lets go at once)."""
    host = '127.0.0.1' if host == 'localhost' else host
    family = socket.AF_INET6 if ':' in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        if os.name != 'nt':
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError as e:
            if _in_use(e):
                return f'port {port} is already used by another program'
            return f'port {port} of {host} can\'t be used ({e.strerror or e})'
    return None


# -- starting ------------------------------------------------------------------------------------

def check_ports(info: ServerInfo) -> str | None:
    """A message if a port the app needs is taken, else None."""
    server = info.config.server
    problems = []
    if problem := port_problem(server.address, server.port):
        problems.append(f'its address ({server.address}): {problem}')
    if (local := server.local_http_port) is not None and (problem := port_problem('127.0.0.1', local)):
        problems.append(f'this computer\'s address (http://localhost:{local}): {problem}')
    if not problems:
        return None
    return (f'{APP_NAME} can\'t start, as {" and ".join(problems)}. Maybe another copy of {APP_NAME}, using '
            f'another data folder, runs there. Set other ports in {info.config_path}: port, and local_port.')


def browser_wanted(config: Config, *, no_browser: bool = False) -> str | None:
    """Why not to open the browser at startup, or None to open it."""
    if no_browser:
        return '--no-browser'
    if not config.server.open_browser:
        return 'open_browser = false'
    if config.server.local_url is None:
        return 'this computer can\'t reach the app'
    return desktop.why_no_browser()


def tray_wanted(config: Config, *, no_tray: bool = False, reload: bool = False) -> str | None:
    """Why not to show the tray icon, or None to show it (see tray.py)."""
    if no_tray:
        return '--no-tray'
    if not config.server.tray:
        return 'tray = false'
    if reload:
        return '--reload'  # (the process holding the icon would outlive each restart)
    if config.server.local_url is None:
        return 'this computer can\'t reach the app'
    return desktop.why_no_browser()  # no desktop to show it on


def start_tray(data_dir: Path, *, script: Path | None = None) -> subprocess.Popen | None:
    """Start the tray icon's process: this program again, as `tray <data folder>` (see tray.py).
    `script`: main.py, when running from the source folder. None if it can't be started."""
    if getattr(sys, 'frozen', False):  # a packaged program: itself
        program = [sys.executable]
    elif script is not None:  # the source folder: main.py
        program = [sys.executable, str(script)]
    else:  # installed (pip, the Flatpak): the package
        program = [sys.executable, '-m', 'rpapp']
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0  # (no console window of its own)
    try:
        return subprocess.Popen([*program, 'tray', str(data_dir)], stdin=subprocess.DEVNULL, creationflags=flags)
    except OSError as e:
        print(f'No tray icon: it couldn\'t be started ({e}).', flush=True)
        return None


def stop_tray(process: subprocess.Popen | None) -> None:
    """When the app stops (the tray process also sees it by itself, within seconds)."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()


def starting_lines(info: ServerInfo) -> list[str]:
    server = info.config.server
    lines = [f'Starting {APP_NAME}…',
             f'Settings: {info.config.source}' if info.config.source
             else f'Settings: the defaults (there\'s no {info.config_path})',
             f'Data: {info.data_dir}']
    if info.https and info.https.self_signed:
        made = info.https.self_signed
        lines.append(f'HTTPS: {"made a new" if made.created else "its"} self-signed certificate, valid until '
                     f'{made.expires:%d %b %Y}. Other devices\' browsers warn about it the first time: check that its '
                     f'SHA-256 fingerprint is\n    {made.fingerprint}')
    elif info.https:
        lines.append(f'HTTPS: with the certificate {info.https.cert}')
    if server.allows_everyone:
        lines.append('Warning: allowed_networks lets anyone who can reach this computer connect'
                     + ('.' if server.pairing else ', and pairing is off: they can use the app, and read your '
                                                    'API keys.'))
    return lines


def ready_lines(info: ServerInfo, *, opening: bool) -> list[str]:
    server = info.config.server
    if windowless.log_path is not None:  # (the Windows program without a console: see windowless.py)
        lines = [f'{APP_NAME} is running, without a window: quit it from its tray icon\'s menu, or its '
                 f'"This computer" page.']
    else:
        lines = [f'{APP_NAME} is running. Leave this window open: closing it, or Ctrl+C, stops the app.']
    if info.local_url:
        lines.append(f'  On this computer, open   {info.local_url}')
    others = info.network_urls()
    if others:
        pairing = '   (pair each one once, on the page below)' if server.pairing else ''
        lines.append(f'  On other devices, open   {others[0]}{pairing}')
        if len(others) > 1:
            lines.append(f'      (or, depending on the network: {", ".join(others[1:3])})')
    else:
        lines.append(f'  Other devices can\'t reach it: it only listens on this computer (address = '
                     f'"{server.address}" in the settings).')
    if info.page_url:
        lines.append(f'Opening {info.page_url} in your browser…' if opening
                     else f'To see how to use it, open {info.page_url}')
    return lines


def when_ready(info: ServerInfo, running: Running, *, open_browser: bool, timeout: float = 120,
               ping: Callable[[Running], bool] = answers) -> threading.Thread:
    """Once the app answers: say so, and open its page in the browser (in a thread: the server runs meanwhile)."""
    def wait() -> None:
        deadline = time.monotonic() + timeout
        while not ping(running):
            if time.monotonic() > deadline:
                print(f'{APP_NAME} doesn\'t answer at {running.url}: see the messages above.', flush=True)
                return
            time.sleep(0.2)
        print('\n'.join(ready_lines(info, opening=open_browser)), flush=True)
        if open_browser and info.page_url and not desktop.open_browser(info.page_url):
            print(f'The browser couldn\'t be opened: open {info.page_url} yourself.', flush=True)

    thread = threading.Thread(target=wait, name='open the browser', daemon=True)
    thread.start()
    return thread


def show_running_copy(instance: Instance, *, open_browser: bool) -> int:
    """Another copy of the app holds the lock: open the browser on it. Returns the exit code."""
    print(f'{APP_NAME} is already running.', flush=True)
    running = instance.wait_for_running()
    if running is None:
        known = instance.running()
        pid = f' (process {known.pid})' if known else ''
        print(f'It doesn\'t answer{pid}. If it\'s stuck, stop it (Task Manager on Windows, Activity Monitor on '
              f'a Mac, kill on Linux), then start {APP_NAME} again.', file=sys.stderr)
        return 1
    if running.url is None:
        print('This computer can\'t open it: see its settings (local_port and address).', file=sys.stderr)
        return 1
    page = running.url + PAGE
    if open_browser and desktop.open_browser(page):
        print(f'Opened it in your browser: {page}')
    else:
        print(f'Open it in your browser: {page}')
    return 0
