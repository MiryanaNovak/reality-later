"""Terminal commands, for pairing devices without a screen on the computer, and to stop the app
(`python main.py` from the source folder; installed, `reality-later`):

    python main.py pair [address]   a pairing link and its QR code, for another device to open
    python main.py approve          the devices waiting to be allowed, with their codes
    python main.py approve CODE     allow the device showing that code
    python main.py deny CODE        refuse it
    python main.py quit             stop the app, if it runs (the Windows installer does, first)
    python main.py check-sharing    check that card sharing works on this computer (publishing/selfcheck.py)

They talk to the running app over this computer's own address (http://localhost:8091
by default), which counts as the computer itself (see access.py). The app must be
running: where it answers is in the data folder's running.json (see instance.py).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx

from . import APP_NAME, paths
from .config import Config
from .instance import NO_ANSWER, NOT_RUNNING, STILL_RUNNING, Instance, stop_running

COMMANDS = {'pair', 'approve', 'deny', 'quit', 'check-sharing'}
NO_SETTINGS = {'quit', 'check-sharing'}  # (they work even when the settings file has a mistake)


def run(args: list[str], config: Config | None, *, data_dir: str | Path | None = None,
        prog: str | None = None, transport: httpx.BaseTransport | None = None) -> int:
    """Run a command; returns the exit code. `config`: the settings (quit needs none, so that it works
    even when the settings file has a mistake). `prog`: how the app is started, for the messages.
    (`transport`: for tests.)"""
    if args[0] == 'quit':
        return _quit(data_dir, transport=transport) if data_dir is not None else 1
    if args[0] == 'check-sharing':  # (whether the app runs or not: it uses nothing of yours)
        from .publishing.selfcheck import run as check_sharing
        print('Checking card sharing on this computer, between two copies of it (nothing of yours is used, and '
              'nothing leaves this computer)...', flush=True)
        if check_sharing():
            print('Card sharing works.')
            return 0
        return 1
    if config is None:
        return 1
    prog = prog or paths.command()
    server = config.server
    if not server.pairing:
        print('Pairing is off (pairing = false in config.toml): any allowed browser can use the app already.')
        return 1
    running = Instance(data_dir).running() if data_dir is not None else None
    url = running.url if running and running.url else server.local_url
    if url is None:
        print(f'This computer can\'t reach the app: it listens on {server.address} only, and local_port = 0 in '
              'config.toml. Remove local_port from it, or pair from the app\'s Settings › Devices.')
        return 1
    base = url.replace('://localhost', '://127.0.0.1', 1)
    command, rest = args[0], args[1:]
    # verify=False: this only ever talks to this computer itself, whose certificate may be self-signed;
    # trust_env=False: never through a proxy set for the internet.
    with httpx.Client(base_url=base, verify=False, trust_env=False, timeout=10, transport=transport) as http:
        try:
            if command == 'pair':
                return _pair(http, rest, prog)
            if command in ('approve', 'deny'):
                return _answer(http, rest, prog, allow=command == 'approve')
        except httpx.ConnectError:
            print(f'The app isn\'t running at {base}. Start it first: {prog}')
            return 1
        except httpx.HTTPError as e:
            print(f'Couldn\'t reach the app at {base}: {e}')
            return 1
    print(f'Unknown command {command!r}. Commands: {", ".join(sorted(COMMANDS))}')
    return 1


def _quit(data_dir: str | Path, *, transport: httpx.BaseTransport | None = None) -> int:
    result = stop_running(data_dir, transport=transport)
    if result == NOT_RUNNING:
        print(f'{APP_NAME} isn\'t running.')
    elif result == NO_ANSWER:
        print(f'{APP_NAME} is running, but doesn\'t answer. If it\'s stuck, stop it (Task Manager on Windows, '
              'Activity Monitor on a Mac, kill on Linux).')
        return 1
    elif result == STILL_RUNNING:
        print(f'{APP_NAME} was asked to quit, but it\'s still running.')
        return 1
    else:
        print(f'{APP_NAME} has stopped.')
    return 0


def _pair(http: httpx.Client, rest: list[str], prog: str) -> int:
    answer = http.post('/local/pair', json={'address': rest[0]} if rest else {})
    if answer.status_code != 200:
        print(answer.json().get('error', answer.text))
        return 1
    data = answer.json()
    until = datetime.fromisoformat(data['expires']).astimezone()
    try:
        import segno
        segno.make(data['url'], error='m').terminal(compact=True)
    except ImportError:
        pass
    print(f'Open this link on the device, or scan the code above (works once, until {until:%H:%M}):\n  {data["url"]}')
    others = [a for a in data.get('addresses', []) if a not in data['url']]
    if others:
        print(f'For another of this computer\'s addresses: {prog} pair {others[0]}')
    return 0


def _answer(http: httpx.Client, rest: list[str], prog: str, *, allow: bool) -> int:
    if not rest:
        waiting = http.get('/local/requests').json().get('requests', [])
        if not waiting:
            print('No device is waiting. On the device, open the app: it shows a code to allow here.')
            return 0
        print('Waiting to be allowed:')
        for r in waiting:
            print(f'  {r["code"]}   {r["name"]}, from {r["address"]}, {r["minutes"]} min ago')
        print(f'Allow one with: {prog} approve CODE   (or refuse it: {prog} deny CODE)')
        return 0
    answer = http.post('/local/answer', json={'code': rest[0], 'allow': allow})
    data = answer.json()
    if answer.status_code != 200:
        print(data.get('error', answer.text))
        return 1
    print(f'{"Allowed" if allow else "Refused"}: {data["name"]}, from {data["address"]}.')
    return 0
