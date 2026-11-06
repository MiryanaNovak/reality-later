"""Try the installer, on Windows (the GitHub workflow runs this on its Windows machine):

    python packaging/windows/installer_test.py dist/reality-later-setup.exe

It installs it silently, for this user, with "Start when I sign in"; starts the installed app; installs
it again over the running copy (which must stop it properly first, and remove the sign-in shortcut,
unticked this time); then uninstalls it, while running too, and checks that the user's data stays.
Only Python's own modules.
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP = Path(os.environ['LOCALAPPDATA']) / 'Programs' / 'Reality Later'
USER = Path(os.environ['APPDATA']) / 'Reality Later'
START_MENU = Path(os.environ['APPDATA']) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
STARTUP = START_MENU / 'Startup'
LOG = USER / 'reality-later.log'


def main() -> int:
    setup = Path(sys.argv[1]).resolve()
    install(setup, '/MERGETASKS=startup')
    for file in ('Reality Later.exe', 'reality-later.exe', 'unins000.exe'):
        check((APP / file).is_file(), f'installed: {file}')
    check((START_MENU / 'Reality Later.lnk').is_file(), 'its Start menu entry')
    check((STARTUP / 'Reality Later.lnk').is_file(), 'its shortcut for starting at sign-in')
    check('Reality Later' in run([APP / 'reality-later.exe', '--help']), 'the installed program answers')

    app = start()
    install(setup, '/MERGETASKS=!startup')  # (over the running copy)
    check(ended(app), 'installing again stopped the running copy first')
    # (a fresh log on GitHub's machine: this is the first time the app stops there, as the user's)
    check('Reality Later has stopped.' in LOG.read_text(encoding='utf-8', errors='replace'),
          'it stopped properly, by being asked to quit (not closed by force)')
    check(not (STARTUP / 'Reality Later.lnk').exists(), 'unticked this time: no more starting at sign-in')
    check((APP / 'Reality Later.exe').is_file(), 'still installed')

    app = start()
    subprocess.run([str(APP / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], check=True)
    deadline = time.monotonic() + 120  # (the uninstaller carries on in a copy of itself, once started)
    while (APP / 'Reality Later.exe').exists() and time.monotonic() < deadline:
        time.sleep(1)
    check(not (APP / 'Reality Later.exe').exists(), 'uninstalled')
    check(ended(app), 'uninstalling stopped the running copy first')
    check(not (START_MENU / 'Reality Later.lnk').exists(), 'its Start menu entry went too')
    check((USER / 'data').is_dir() and (USER / 'certificates').is_dir(), 'the user\'s data stays')
    print('All good.')
    return 0


def install(setup: Path, tasks: str) -> None:
    result = subprocess.run([str(setup), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', tasks,
                             f'/LOG={Path.cwd() / "install.log"}'], timeout=600)
    if result.returncode != 0:
        print(Path('install.log').read_text(encoding='utf-8', errors='replace'))
    check(result.returncode == 0, f'the installer ran ({tasks})')


def start() -> subprocess.Popen:
    # As the Start menu starts it: with no input or output at all, so that it writes its log (given
    # somewhere to print, a program without a console prints there instead, as pythonw does).
    nowhere = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESTDHANDLES)  # (its handles: none)
    app = subprocess.Popen([str(APP / 'Reality Later.exe'), '--no-browser'], startupinfo=nowhere)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen('http://127.0.0.1:8091/local/instance', timeout=5).read()
            print('ok: the installed app runs', flush=True)
            return app
        except OSError:
            if app.poll() is not None:
                break
            time.sleep(1)
    print(LOG.read_text(encoding='utf-8', errors='replace') if LOG.is_file() else '(no log)')
    raise SystemExit('FAILED: the installed app doesn\'t answer')


def ended(app: subprocess.Popen) -> bool:
    try:
        app.wait(30)
        return True
    except subprocess.TimeoutExpired:
        app.kill()
        return False


def run(command: list) -> str:
    result = subprocess.run([str(c) for c in command], capture_output=True, text=True, timeout=120,
                            encoding='utf-8', errors='replace')
    return result.stdout + result.stderr


def check(ok: object, what: str) -> None:
    if not ok:
        if LOG.is_file():
            print('--- the app\'s log:\n' + LOG.read_text(encoding='utf-8', errors='replace'))
        raise SystemExit(f'FAILED: {what}')
    print(f'ok: {what}', flush=True)


if __name__ == '__main__':
    sys.exit(main())
