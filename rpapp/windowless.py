"""Running without a console window: the Windows program "Reality Later.exe" (see packaging/windows).

Started from the Start menu, it has no window at all: the tray icon and the browser are the app.
What it would print goes to a log file instead, next to the settings file:

    %APPDATA%\\Reality Later\\reality-later.log      (when it grows too big: reality-later.old.log)

and when it can't start (its port is taken, the settings file has a mistake...), it says why in a
message box. The program next to it, reality-later.exe, is the same app with a console: for the
commands (pair, approve, deny, quit) and --help, and to watch its messages.
"""
from __future__ import annotations

import os
import sys
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from . import APP_NAME

LOG_NAME = 'reality-later.log'
OLD_LOG_NAME = 'reality-later.old.log'
MAX_LOG = 1_000_000  # bytes: past that, the next start begins a new log, keeping the one before
SHOWN_LINES = 12     # of what a start that failed printed, in its message box

log_path: Path | None = None  # where the messages go, while running without a console (see run)


def has_console() -> bool:
    """Whether there's somewhere to print to (PyInstaller's windowed programs have no stdout or stderr)."""
    return sys.stdout is not None and sys.stderr is not None


def open_log(folder: Path) -> Path:
    """From now on, what's printed (stdout and stderr) goes to the log file in that folder."""
    global log_path
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LOG_NAME
    try:
        if path.stat().st_size > MAX_LOG:
            os.replace(path, folder / OLD_LOG_NAME)
    except OSError:
        pass  # there's none yet, or the running copy has it open (Windows doesn't rename files in use)
    stream = open(path, 'a', encoding='utf-8', errors='replace', buffering=1)  # (written a line at a time)
    sys.stdout = sys.stderr = stream
    log_path = path
    return path


def message_box(text: str) -> None:
    """Show the text in a message box (on Windows; elsewhere, it's in the log only)."""
    if sys.platform != 'win32':
        return
    import ctypes
    MB_ICONERROR, MB_SETFOREGROUND = 0x10, 0x10000
    ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, MB_ICONERROR | MB_SETFOREGROUND)


def run(main: Callable[[list[str]], object], argv: list[str], *, folder: Path,
        show_error: Callable[[str], None] = message_box) -> None:
    """main(argv), without a console: its messages in the log file (in `folder`), and a message box
    if it can't start. (The tray icon's own process shows none: the app is what matters.)"""
    if argv[:1] == ['tray']:
        show_error = _nothing
    log = open_log(folder)
    print(f'--- {datetime.now():%Y-%m-%d %H:%M:%S}, process {os.getpid()}: {" ".join(argv) or "started"}',
          flush=True)
    start = log.stat().st_size  # (this start's messages follow)
    try:
        main(argv)
    except SystemExit as e:
        if e.code is None or e.code == 0:
            raise
        if isinstance(e.code, str):  # sys.exit('Can't start: ...'): that says it all
            print(e.code, flush=True)
            show_error(e.code)
        else:
            said = _printed_since(log, start)
            show_error(f'{APP_NAME} couldn\'t start.\n\n{said}' if said
                       else f'{APP_NAME} couldn\'t start. Its log may say why:\n{log}')
        sys.exit(1)
    except Exception as e:
        traceback.print_exc()
        sys.stdout.flush()
        show_error(f'{APP_NAME} stopped because of an error:\n{type(e).__name__}: {e}\n\nIts log has the '
                   f'details:\n{log}')
        sys.exit(1)


def _printed_since(log: Path, start: int) -> str:
    """The last lines written to the log since `start` (a size in bytes)."""
    sys.stdout.flush()
    try:
        with open(log, 'rb') as f:
            f.seek(start)
            lines = f.read().decode('utf-8', errors='replace').strip().splitlines()
    except OSError:
        return ''
    return '\n'.join(lines[-SHOWN_LINES:])


def _nothing(text: str) -> None:
    pass
