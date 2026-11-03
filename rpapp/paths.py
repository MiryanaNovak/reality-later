"""Where the app keeps its files: its settings (config.toml), its certificate, and your data.

Running from its source folder (python main.py, or installed from it with pip install -e), as before:

    <source folder>/config.toml, <source folder>/certificates/, <source folder>/data/

Installed (pip install, a packaged program, Flatpak), the folders a user's programs keep their files in,
since the app's own folder may be read-only, or replaced by the next version:

    Linux    ~/.config/reality-later/      config.toml, certificates/   ($XDG_CONFIG_HOME if set)
             ~/.local/share/reality-later/ the data                     ($XDG_DATA_HOME if set)
             In a Flatpak, those two are in ~/.var/app/<app id>/ (Flatpak sets them).
    macOS    ~/Library/Application Support/Reality Later/   config.toml, certificates/, data/
    Windows  %APPDATA%\\Reality Later\\                      config.toml, certificates\\, data\\

RPAPP_DATA, if set, is the data folder in every case (see main.py).
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import APP_NAME

PACKAGE = Path(__file__).resolve().parent
LINUX_NAME = 'reality-later'  # (Linux folders are lowercase, without spaces)
EXAMPLE_NAME = 'config.example.toml'
FLATPAK_SHARE = Path('/app/share/reality-later')  # where the Flatpak puts config.example.toml (see packaging/)
FLATPAK_ID = 'io.github.MiryanaNovak.RealityLater'
CONSOLE_PROGRAM = 'reality-later.exe'  # the Windows program with a console, for the commands (packaging/windows)


@dataclass(frozen=True)
class Places:
    config: Path  # the folder of config.toml, and of certificates/
    data: Path
    installed: bool  # False: running from the source folder


def in_flatpak(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return bool(env.get('FLATPAK_ID')) or Path('/.flatpak-info').exists()


def source_folder(env: Mapping[str, str] | None = None) -> Path | None:
    """The source folder the app runs from (main.py next to the rpapp package), or None if it's installed."""
    if getattr(sys, 'frozen', False) or in_flatpak(env):
        return None
    root = PACKAGE.parent
    return root if (root / 'main.py').is_file() else None


def places(env: Mapping[str, str] | None = None, platform: str | None = None,
           source: Path | None | bool = True) -> Places:
    """Where the files go (see the module's notes). `source`: the source folder, None for none (installed),
    True to find it out."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    folder = source_folder(env) if source is True else source
    if folder is not None:
        places_ = Places(config=folder, data=folder / 'data', installed=False)
    elif platform.startswith('win'):
        root = Path(env.get('APPDATA') or Path.home() / 'AppData' / 'Roaming') / APP_NAME
        places_ = Places(config=root, data=root / 'data', installed=True)
    elif platform == 'darwin':
        root = Path.home() / 'Library' / 'Application Support' / APP_NAME
        places_ = Places(config=root, data=root / 'data', installed=True)
    else:
        config = Path(env.get('XDG_CONFIG_HOME') or Path.home() / '.config') / LINUX_NAME
        data = Path(env.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / LINUX_NAME
        places_ = Places(config=config, data=data, installed=True)
    if env.get('RPAPP_DATA'):
        places_ = Places(config=places_.config, data=Path(env['RPAPP_DATA']), installed=places_.installed)
    return places_


def command(env: Mapping[str, str] | None = None, platform: str | None = None) -> str:
    """How to run the app's commands (pair, approve, quit...) in a terminal, for the messages that say so."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    if in_flatpak(env):
        return f'flatpak run {env.get("FLATPAK_ID") or FLATPAK_ID}'
    if getattr(sys, 'frozen', False):  # a packaged program: on Windows, the one with a console (packaging/windows)
        # (its name, not its path: the pages show this to other devices too, and a path has the user's name)
        name = CONSOLE_PROGRAM if platform.startswith('win') else Path(sys.executable).name
        return f'"{name}"' if ' ' in name else name
    if source_folder(env) is not None:
        return 'python main.py'
    return 'reality-later'


def example_config(config_folder: Path) -> Path:
    """config.example.toml, every setting explained: next to config.toml, in the source folder, or where the
    packaged program has it. (config_folder / its name if there's none, for messages.)"""
    candidates = [config_folder, source_folder()]
    if getattr(sys, 'frozen', False):
        candidates.append(Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent)))  # PyInstaller's files
    if in_flatpak():
        candidates.append(FLATPAK_SHARE)
    for folder in candidates:
        if folder is not None and (folder / EXAMPLE_NAME).is_file():
            return folder / EXAMPLE_NAME
    return config_folder / EXAMPLE_NAME
