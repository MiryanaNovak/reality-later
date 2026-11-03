"""Start the built program and check that it works (the GitHub workflow runs this on Windows):

    python packaging/windows/smoke_test.py "dist/Reality Later"

With folders of its own (as a new user's), it checks that:
  - reality-later --help answers;
  - the app starts (the console program), and its pages, its files, NiceGUI's live connection to the
    browser and HTTPS for other devices all answer;
  - starting it again finds the running copy;
  - `reality-later quit` stops it;
  - the program without a console starts too, writes its log (on Windows), and quits.
Only Python's own modules: it runs with any Python.
"""
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

WINDOWS = sys.platform == 'win32'
LOCAL = 'http://127.0.0.1:8091'
NETWORK = 'https://127.0.0.1:8090'
START_TIMEOUT = 180  # seconds: a first start, scanned by the antivirus, can be slow


def main() -> int:
    folder = Path(sys.argv[1]).resolve()
    console = folder / ('reality-later.exe' if WINDOWS else 'reality-later')
    windowless = folder / ('Reality Later.exe' if WINDOWS else 'Reality Later')
    for program in (console, windowless):
        check(program.is_file(), f'{program.name} is there')

    home = Path(tempfile.mkdtemp(prefix='reality-later-smoke-'))
    env = {**os.environ, 'APPDATA': str(home / 'AppData'), 'XDG_CONFIG_HOME': str(home / 'config'),
           'XDG_DATA_HOME': str(home / 'data')}
    env.pop('RPAPP_DATA', None)
    config = home / 'AppData' / 'Reality Later' if WINDOWS else home / 'config' / 'reality-later'

    out = run([console, '--help'], env)
    check('reality-later.exe' in out or 'reality-later' in out, '--help answers')

    log = home / 'console.log'
    app = start([console, '--no-browser', '--no-tray'], env, log)
    try:
        wait_until_it_answers(app, log)
        page = get(LOCAL + '/')
        check('<title>Reality Later</title>' in page, 'the home page answers')
        check('Reality Later is running' in get(LOCAL + '/computer'), 'the "This computer" page answers')
        check(get(LOCAL + '/icons/favicon.svg').lstrip().startswith('<?xml'), 'the icons are there')
        static = re.search(r'(?:src|href)="(/_nicegui/[^"]+\.(?:js|css))"', page)
        check(static is not None and get(LOCAL + static.group(1)), f'NiceGUI\'s files are there ({static and static.group(1)})')
        handshake = get(LOCAL + '/_nicegui_ws/socket.io/?EIO=4&transport=polling')
        check(handshake.startswith('0{') and 'sid' in json.loads(handshake[1:]),
              'the live connection to the browser answers')
        insecure = ssl.create_default_context()
        insecure.check_hostname, insecure.verify_mode = False, ssl.CERT_NONE  # (its own self-signed certificate)
        try:
            urllib.request.urlopen(NETWORK + '/', context=insecure, timeout=10)
            network = 200
        except urllib.error.HTTPError as e:
            network = e.code  # (an unpaired device gets the pairing page, or a refusal: HTTPS works either way)
        check(network in (200, 401, 403), f'HTTPS for other devices answers ({network})')
        check((config / 'certificates' / 'self-signed.pem').is_file(), 'its certificate is in its settings folder')

        again = run([console, '--no-browser'], env)
        check('already running' in again, 'starting it again finds the running copy')

        stopped = run([console, 'quit'], env)
        check('has stopped' in stopped, '`quit` stops it')
        check(wait_for_exit(app), 'the app has ended')
    finally:
        stop(app)

    app = start([windowless, '--no-browser'], env, home / 'windowless.log')
    try:
        wait_until_it_answers(app, home / 'windowless.log')
        check('<title>Reality Later</title>' in get(LOCAL + '/'), 'the program without a console starts too')
        if WINDOWS:
            written = (config / 'reality-later.log').read_text(encoding='utf-8')
            check('is running, without a window' in written, 'it writes its messages to its log')
        check('has stopped' in run([console, 'quit'], env), '`quit` stops it')
        check(wait_for_exit(app), 'it has ended')
    finally:
        stop(app)
        if WINDOWS and (config / 'reality-later.log').is_file():
            print('--- its log:\n' + (config / 'reality-later.log').read_text(encoding='utf-8'))
    check('isn\'t running' in run([console, 'quit'], env), '`quit` says when it isn\'t running')
    print('All good.')
    return 0


# --- helpers -------------------------------------------------------------------------------------

def check(ok: object, what: str) -> None:
    if not ok:
        raise SystemExit(f'FAILED: {what}')
    print(f'ok: {what}', flush=True)


def run(command: list, env: dict) -> str:
    result = subprocess.run([str(c) for c in command], env=env, capture_output=True, text=True, timeout=120,
                            encoding='utf-8', errors='replace')
    return result.stdout + result.stderr


def start(command: list, env: dict, log: Path) -> subprocess.Popen:
    with open(log, 'w', encoding='utf-8') as out:
        return subprocess.Popen([str(c) for c in command], env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)


def get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=15) as answer:
        return answer.read().decode('utf-8', errors='replace')


def wait_until_it_answers(app: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if app.poll() is not None:
            raise SystemExit(f'FAILED: it stopped (exit code {app.returncode}):\n{log.read_text(errors="replace")}')
        try:
            get(LOCAL + '/local/instance')
            return
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    raise SystemExit(f'FAILED: no answer after {START_TIMEOUT} s:\n{log.read_text(errors="replace")}')


def wait_for_exit(app: subprocess.Popen, timeout: float = 30) -> bool:
    try:
        app.wait(timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def stop(app: subprocess.Popen) -> None:
    if app.poll() is None:
        app.kill()
        app.wait(10)


if __name__ == '__main__':
    sys.exit(main())
