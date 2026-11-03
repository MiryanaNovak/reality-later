"""Opening things on the computer the app runs on: the browser, the settings file, a folder.

    Windows   the file's usual program (else Notepad); File Explorer
    macOS     the default text editor (open -t); Finder (open -R)
    Linux     the file's usual program, through xdg-open (else a text editor found
              on the computer); the file manager, through xdg-open

In a Flatpak, xdg-open is the desktop's own opener (its "portal"), which opens them outside the
sandbox, in the programs the user chose: it's used for the browser too.

These run on the computer, so only the computer's own pages offer them (see
ui/computer_page.py). They raise DesktopError with a message to show when they
can't do it (no desktop, no program for it...).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Mapping
from pathlib import Path

from .paths import in_flatpak

# Text editors to try on Linux when xdg-open has no program for the file
LINUX_EDITORS = ('gnome-text-editor', 'gedit', 'kate', 'kwrite', 'mousepad', 'xed', 'pluma', 'featherpad',
                 'leafpad', 'code', 'codium')


class DesktopError(Exception):
    """It couldn't be opened: the message says why."""


def why_no_browser(env: Mapping[str, str] | None = None, platform: str | None = None) -> str | None:
    """Why not to open a browser at startup, or None to go ahead.

    Not when there's no desktop to show it on: started over SSH, or on Linux without a display
    (a server, a service started with the computer). Windows and macOS always have one.
    """
    env = os.environ if env is None else env
    platform = platform or sys.platform
    if env.get('SSH_CONNECTION') or env.get('SSH_TTY'):
        return 'started over SSH'
    if platform.startswith(('win', 'cygwin', 'darwin')):
        return None
    if in_flatpak(env):  # started from the desktop's menu (its sandbox has no display: the app opens no window)
        return None
    if not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
        return 'no desktop to open it on'
    return None


def open_browser(url: str) -> bool:
    """Open the URL in the default browser; False if that didn't work."""
    if sys.platform.startswith('linux') and (_frozen() or in_flatpak()):
        # A packaged app changes the library path for itself: the browser must not inherit that. A Flatpak
        # has no display, so Python's webbrowser wouldn't even look for xdg-open there.
        if _try(['xdg-open', url]):
            return True
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def open_in_editor(path: Path) -> None:
    """Open a text file (the settings file) for editing."""
    path = Path(path)
    if sys.platform == 'win32':
        try:
            os.startfile(path)  # type: ignore[attr-defined]  # its usual program, if .toml has one
        except OSError:
            _spawn(['notepad.exe', str(path)])
    elif sys.platform == 'darwin':
        _check(['open', '-t', str(path)], 'TextEdit couldn\'t open it')
    else:
        if _try(['xdg-open', str(path)]):
            return
        for editor in LINUX_EDITORS:
            if shutil.which(editor):
                _spawn([editor, str(path)])
                return
        raise DesktopError('No program was found to open it with: open it with your text editor.')


def show_in_folder(path: Path) -> None:
    """Show a folder, or the folder holding a file, in the file explorer."""
    path = Path(path)
    folder = path if path.is_dir() else path.parent
    if sys.platform == 'win32':
        if path.is_file():
            _spawn(f'explorer /select,"{path}"')  # the folder, with the file selected
        else:
            os.startfile(folder)  # type: ignore[attr-defined]
    elif sys.platform == 'darwin':
        _check(['open', '-R', str(path)] if path.is_file() else ['open', str(folder)], 'Finder couldn\'t show it')
    elif not _try(['xdg-open', str(folder)]):
        raise DesktopError('No file manager was found to show it.')


# -- running programs ---------------------------------------------------------------------------

def _frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def _env() -> dict[str, str]:
    """The environment for programs started from here: as the user's, not the packaged app's."""
    env = dict(os.environ)
    if _frozen() and sys.platform.startswith('linux'):  # PyInstaller keeps the original as ..._ORIG
        original = env.pop('LD_LIBRARY_PATH_ORIG', None)
        if original is None:
            env.pop('LD_LIBRARY_PATH', None)
        else:
            env['LD_LIBRARY_PATH'] = original
    return env


def _spawn(command: list[str] | str) -> None:
    try:
        subprocess.Popen(command, env=_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=sys.platform != 'win32')
    except OSError as e:
        raise DesktopError(f'It couldn\'t be opened: {e}') from None


def _try(command: list[str]) -> bool:
    """Run an opener like xdg-open: whether it worked (it's still running after a few seconds: it did)."""
    try:
        process = subprocess.Popen(command, env=_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:  # not installed
        return False
    try:
        return process.wait(timeout=5) == 0
    except subprocess.TimeoutExpired:
        return True


def _check(command: list[str], failure: str) -> None:
    try:
        result = subprocess.run(command, env=_env(), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DesktopError(f'{failure}: {e}') from None
    if result.returncode != 0:
        raise DesktopError(f'{failure}: {(result.stderr or result.stdout).strip() or "it failed"}')
