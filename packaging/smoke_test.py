"""Start the built program and check that it works (the GitHub workflows run this before a release):

    python packaging/smoke_test.py "dist/Reality Later"                          # PyInstaller's program
    python packaging/smoke_test.py --flatpak io.github.MiryanaNovak.RealityLater  # the installed Flatpak

As a new user's (folders of its own, or none yet), it checks that:
  - `--help` answers;
  - the app starts (the program with a console), and its pages, its files, NiceGUI's live connection to
    the browser and HTTPS for other devices all answer, and vector search's and card sharing's libraries
    load;
  - starting it again finds the running copy;
  - `quit` stops it;
  - PyInstaller's program without a console starts too, writes its log (on Windows), and quits.

A Flatpak keeps its files in ~/.var/app/<its id>, whatever the environment says: this checks it on a
computer where it has none yet, such as GitHub's (on yours, it stops rather than use your data and
quit your running app). It removes that folder at the end, once all is well.
Only Python's own modules: it runs with any Python.
"""
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

WINDOWS = sys.platform == 'win32'
LOCAL = 'http://127.0.0.1:8091'
NETWORK = 'https://127.0.0.1:8090'
START_TIMEOUT = 180  # seconds: a first start, scanned by the antivirus, can be slow


@dataclass
class Target:
    """What's tested, and where it keeps its files."""
    console: list[str]      # the command starting the app with a console (a program, or flatpak run <id>)
    command: str            # what its messages call it ('reality-later.exe', 'flatpak run <id>')
    config: Path            # its settings folder
    logs: Path              # (this test's: the app's messages)
    env: dict = field(default_factory=dict)
    windowless: list[str] | None = None  # PyInstaller's program without a console
    flatpak: str = ''       # the Flatpak's id
    own_folder: Path | None = None  # (a Flatpak's, removed at the end)


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == '--flatpak':
        target = flatpak(argv[1])
    elif len(argv) == 1 and not argv[0].startswith('-'):
        target = packaged(Path(argv[0]).resolve())
    else:
        raise SystemExit(__doc__)

    out = run(target.console + ['--help'], target.env)
    check(f'{target.command} quit' in out, '--help answers')

    log = target.logs / 'console.log'
    app = start(target.console + ['--no-browser', '--no-tray'], target.env, log)
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
        check((target.config / 'certificates' / 'self-signed.pem').is_file(), 'its certificate is in its settings folder')
        settings = get(LOCAL + '/settings')
        check('Vector search' in settings and 'can\'t run on this computer' not in settings,
              'vector search\'s libraries load (onnxruntime, tokenizers, numpy)')
        check('Sharing cards' in settings and 'isn\'t available here' not in settings,
              'card sharing\'s libraries load (coincurve, websockets), and its catalog opens')

        again = run(target.console + ['--no-browser'], target.env)
        check('already running' in again, 'starting it again finds the running copy')

        stopped = run(target.console + ['quit'], target.env)
        check('has stopped' in stopped, '`quit` stops it')
        check(wait_for_exit(app), 'the app has ended')
    except SystemExit:
        print('--- its messages:\n' + read(log))
        raise
    finally:
        stop(app, target)

    if target.windowless:
        # As the Start menu starts it: on Windows, with nowhere to print (its messages then go to its log).
        its_log = target.config / 'reality-later.log' if WINDOWS else target.logs / 'windowless.log'
        app = start(target.windowless + ['--no-browser'], target.env, its_log, as_from_the_start_menu=WINDOWS)
        try:
            wait_until_it_answers(app, its_log)
            check('<title>Reality Later</title>' in get(LOCAL + '/'), 'the program without a console starts too')
            if WINDOWS:  # (it says it's running a moment after it answers: once its own check of that is done)
                check(wait_for_text(its_log, 'is running, without a window'), 'it writes its messages to its log')
            check('has stopped' in run(target.console + ['quit'], target.env), '`quit` stops it')
            check(wait_for_exit(app), 'it has ended')
        finally:
            stop(app, target)
            if WINDOWS:
                print('--- its log:\n' + read(its_log))
    check('isn\'t running' in run(target.console + ['quit'], target.env), '`quit` says when it isn\'t running')
    if target.own_folder is not None:
        shutil.rmtree(target.own_folder, ignore_errors=True)
    print('All good.')
    return 0


def packaged(folder: Path) -> Target:
    """PyInstaller's program (see packaging/windows), with folders of its own."""
    console = folder / ('reality-later.exe' if WINDOWS else 'reality-later')
    windowless = folder / ('Reality Later.exe' if WINDOWS else 'Reality Later')
    for program in (console, windowless):
        check(program.is_file(), f'{program.name} is there')
    home = Path(tempfile.mkdtemp(prefix='reality-later-smoke-'))
    env = {**os.environ, 'APPDATA': str(home / 'AppData'), 'LOCALAPPDATA': str(home / 'LocalAppData'),
           'XDG_CONFIG_HOME': str(home / 'config'), 'XDG_DATA_HOME': str(home / 'data'),
           'XDG_CACHE_HOME': str(home / 'cache')}
    env.pop('RPAPP_DATA', None)
    config = home / 'AppData' / 'Reality Later' if WINDOWS else home / 'config' / 'reality-later'
    return Target(console=[str(console)], command=console.name, config=config, logs=home, env=env,
                  windowless=[str(windowless)])


def flatpak(app_id: str) -> Target:
    """The installed Flatpak, on a computer where it has no files yet."""
    check(shutil.which('flatpak') is not None, 'flatpak is there')
    check(subprocess.run(['flatpak', 'info', app_id], capture_output=True).returncode == 0,
          f'{app_id} is installed')
    own = Path.home() / '.var' / 'app' / app_id
    if own.exists():
        raise SystemExit(f'Not run: {own} is there, the settings and data of the Flatpak on this computer. '
                         f'This test is for a computer where it has none yet, such as GitHub\'s: here it would '
                         f'use them, and quit the app if it runs.')
    env = dict(os.environ)  # (as it is: flatpak finds the apps installed for you with it)
    env.pop('RPAPP_DATA', None)
    return Target(console=['flatpak', 'run', app_id], command=f'flatpak run {app_id}',
                  config=own / 'config' / 'reality-later',
                  logs=Path(tempfile.mkdtemp(prefix='reality-later-smoke-')), env=env, flatpak=app_id,
                  own_folder=own)


# --- helpers -------------------------------------------------------------------------------------

def check(ok: object, what: str) -> None:
    if not ok:
        raise SystemExit(f'FAILED: {what}')
    print(f'ok: {what}', flush=True)


def run(command: list, env: dict) -> str:
    result = subprocess.run([str(c) for c in command], env=env, capture_output=True, text=True, timeout=120,
                            encoding='utf-8', errors='replace')
    return result.stdout + result.stderr


def start(command: list, env: dict, log: Path, *, as_from_the_start_menu: bool = False) -> subprocess.Popen:
    """Start it, its output in `log`; or (as_from_the_start_menu, on Windows) as Explorer starts programs,
    with no input or output at all: a program without a console given somewhere to print prints there,
    as pythonw does, rather than in its own log."""
    if as_from_the_start_menu:
        nowhere = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESTDHANDLES)  # (its handles: none)
        return subprocess.Popen([str(c) for c in command], env=env, startupinfo=nowhere)
    with open(log, 'w', encoding='utf-8') as out:
        return subprocess.Popen([str(c) for c in command], env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return f'(no {path.name})'


def wait_for_text(path: Path, text: str, timeout: float = 30) -> bool:
    """Whether the file says `text`, within `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while text not in read(path):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.25)
    return True


def get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=15) as answer:
        return answer.read().decode('utf-8', errors='replace')


def wait_until_it_answers(app: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if app.poll() is not None:
            raise SystemExit(f'FAILED: it stopped (exit code {app.returncode}):\n{read(log)}')
        try:
            get(LOCAL + '/local/instance')
            return
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    raise SystemExit(f'FAILED: no answer after {START_TIMEOUT} s:\n{read(log)}')


def wait_for_exit(app: subprocess.Popen, timeout: float = 30) -> bool:
    try:
        app.wait(timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def stop(app: subprocess.Popen, target: Target) -> None:
    if app.poll() is None:
        app.kill()
        app.wait(10)
    if target.flatpak:  # (in case its sandbox outlived flatpak run)
        subprocess.run(['flatpak', 'kill', target.flatpak], capture_output=True)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
