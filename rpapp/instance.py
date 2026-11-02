"""One copy of the app at a time for a data folder, and how to find the running one.

The running copy holds a lock on <data>/app.lock and writes <data>/running.json:

    {"id": "3f9c...", "pid": 1234, "url": "http://localhost:8091", "started": "..."}

Starting the app again while it runs finds it there: main.py then opens the browser
on the running copy and exits, rather than failing with "port already in use". The
terminal commands (cli.py) read it too, to reach the running copy where it listens.

The lock is the operating system's (flock on Linux and macOS, msvcrt on Windows): it
goes away with the process, even after a crash, so a left-over file never stops a
start. To tell that what answers at the URL is that copy, it answers GET
/local/instance with its id.

The tray icon (tray.py) quits the app with POST /local/quit, from this computer only, with that
id in an X-Roleplay-Instance header: a web page can't send that header to the app (the browser
would have to ask the app first, and it says no), so no page can quit the app behind your back.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import APP_NAME

LOCK_FILE = 'app.lock'
INFO_FILE = 'running.json'
PATH = '/local/instance'
QUIT_PATH = '/local/quit'
HEADER = 'X-Roleplay-Instance'
ENV = 'RPAPP_INSTANCE'  # the running copy's id, for the process NiceGUI starts in --reload mode


@dataclass
class Running:
    id: str
    pid: int
    url: str | None  # how this computer opens the app (config.ServerConfig.local_url)
    started: datetime

    @property
    def api(self) -> str | None:
        """The URL for programs on this computer: 127.0.0.1, which needs no name lookup."""
        return self.url.replace('://localhost', '://127.0.0.1', 1) if self.url else None


class Instance:
    def __init__(self, data_dir: str | Path) -> None:
        self.folder = Path(data_dir)
        self.lock_path = self.folder / LOCK_FILE
        self.info_path = self.folder / INFO_FILE
        self._file: IO[bytes] | None = None
        self.me: Running | None = None

    def acquire(self) -> bool:
        """Take the lock: False if another copy of the app holds it."""
        if self._file is not None:
            return True
        self.folder.mkdir(parents=True, exist_ok=True)
        f = open(self.lock_path, 'a+b')
        if not _lock(f):
            f.close()
            return False
        self._file = f
        return True

    def publish(self, url: str | None) -> Running:
        """Say where this copy answers (holding the lock)."""
        self.me = Running(id=secrets.token_hex(8), pid=os.getpid(), url=url,
                          started=datetime.now(timezone.utc))
        os.environ[ENV] = self.me.id
        data = {'id': self.me.id, 'pid': self.me.pid, 'url': url, 'started': self.me.started.isoformat()}
        tmp = self.info_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
        os.replace(tmp, self.info_path)
        return self.me

    def release(self) -> None:
        if self._file is None:
            return
        if self.me and (self.running() or self.me).id == self.me.id:
            self.info_path.unlink(missing_ok=True)
        _unlock(self._file)
        self._file.close()
        self._file = None

    def running(self) -> Running | None:
        """What running.json says (it may be left over from a copy that crashed)."""
        try:
            data: dict[str, Any] = json.loads(self.info_path.read_text(encoding='utf-8'))
            return Running(id=str(data['id']), pid=int(data['pid']), url=data.get('url') or None,
                           started=datetime.fromisoformat(data['started']))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def wait_for_running(self, *, timeout: float = 20, step: float = 0.25) -> Running | None:
        """The copy holding the lock, once it answers (it may be starting); None if it doesn't in time."""
        deadline = time.monotonic() + timeout
        while True:
            running = self.running()
            if running is not None and answers(running):
                return running
            if time.monotonic() >= deadline:
                return None
            time.sleep(step)


def answers(running: Running, *, timeout: float = 2) -> bool:
    """Whether that copy of the app answers at its address."""
    if not running.api:
        return False
    try:
        # trust_env=False: never through a proxy set for the internet, this is this computer.
        with httpx.Client(timeout=timeout, verify=False, trust_env=False) as http:
            answer = http.get(running.api + PATH)
        return answer.status_code == 200 and answer.json().get('id') == running.id
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def add_route(app: Any, *, quit_app: Callable[[], Any] | None = None) -> None:
    """GET /local/instance: which copy of the app this is (only to this computer). With `quit_app`,
    POST /local/quit too, which calls it (see the module's notes)."""
    from .access import is_local

    def local(request: Request) -> bool:
        headers = {k.lower(): v for k, v in request.headers.items()}
        return is_local(request.client.host if request.client else None, headers)

    async def instance(request: Request) -> JSONResponse:
        if not local(request):
            return JSONResponse({'error': 'Only from this computer itself.'}, status_code=403)
        return JSONResponse({'app': APP_NAME, 'id': os.environ.get(ENV, '')})

    async def quit_route(request: Request) -> JSONResponse:
        me = os.environ.get(ENV, '')
        if not local(request) or not me or not secrets.compare_digest(request.headers.get(HEADER, ''), me):
            return JSONResponse({'error': 'Only from this computer itself, by the app\'s own tray icon.'},
                                status_code=403)
        quit_app()
        return JSONResponse({'quitting': True})

    app.add_api_route(PATH, instance, methods=['GET'], include_in_schema=False)
    if quit_app is not None:
        app.add_api_route(QUIT_PATH, quit_route, methods=['POST'], include_in_schema=False)


# -- the operating system's file locks ----------------------------------------------------------

def _lock(f: IO[bytes]) -> bool:
    try:
        if os.name == 'nt':
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(f: IO[bytes]) -> None:
    try:
        if os.name == 'nt':
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
