# PyInstaller's build file for the Windows program (see README.md). From the project's folder:
#
#     pip install . pyinstaller
#     pyinstaller --noconfirm packaging/windows/reality-later.spec
#
# makes dist/Reality Later/, the program, in one folder:
#
#     Reality Later.exe   the app, without a console window: the tray icon and the browser are the app
#                         (its messages go to a log file: see rpapp/windowless.py)
#     reality-later.exe   the same app with a console: its commands (pair, approve, quit...) and --help
#     _internal/          Python, the libraries, the app's files
#
# The installer (installer.iss) and the zip are made from that folder. The version is
# REALITY_LATER_VERSION's if set (the GitHub workflow sets it from the version tag), else pyproject.toml's.
# (It builds on Linux and macOS too, for that system: a way to try this file without Windows.)
import os
import re
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = Path(SPECPATH)
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))  # (for collect_* to find the rpapp package, installed or not)

APP_NAME = 'Reality Later'
VERSION = (os.environ.get('REALITY_LATER_VERSION', '').removeprefix('v')
           or tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version'])
ICON = str(ROOT / 'packaging' / 'icons' / 'windows' / 'reality-later.ico')

datas = [
    *collect_data_files('rpapp'),                  # its pictures framing tool, fonts, icons, starter set
    (str(ROOT / 'config.example.toml'), '.'),      # every setting explained (paths.example_config finds it)
]
if (ROOT / 'LICENSE').is_file():
    datas.append((str(ROOT / 'LICENSE'), '.'))

hiddenimports = [
    *collect_submodules('rpapp'),       # (the parts it imports only when needed, like the tray icon's)
    'engineio.async_drivers.asgi',      # NiceGUI's live connection to the browser: engineio loads it by name
    # Card sharing's signatures: coincurve's compiled part imports its own cffi backend from C, where
    # PyInstaller can't see it (without it: "No module named 'coincurve._cffi_backend'", and no sharing)
    'coincurve._cffi_backend',
]


def version_info(description: str, file_name: str):
    """The program's Properties › Details in Windows: its name, version and description."""
    if sys.platform != 'win32':
        return None
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)
    numbers = tuple(int(n) for n in re.findall(r'\d+', VERSION)[:4])
    numbers += (0,) * (4 - len(numbers))
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[StringFileInfo([StringTable('040904B0', [
                  StringStruct('CompanyName', 'MiryanaNovak'),
                  StringStruct('FileDescription', description),
                  StringStruct('FileVersion', VERSION),
                  StringStruct('InternalName', file_name.removesuffix('.exe')),
                  StringStruct('LegalCopyright', 'GNU General Public License v3'),
                  StringStruct('OriginalFilename', file_name),
                  StringStruct('ProductName', APP_NAME),
                  StringStruct('ProductVersion', VERSION)])]),
              VarFileInfo([VarStruct('Translation', [0x0409, 1200])])])


a = Analysis(
    [str(HERE / 'reality_later.py')],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    # Not needed, but taken along if the Python building it has them: Pillow's Tk support, the tests, and
    # what NiceGUI uses for its charts and tables (only when installed), and for its own windows. (numpy
    # stays: vector search needs it.)
    excludes=['tkinter', 'pytest', 'pandas', 'polars', 'scipy', 'matplotlib', 'plotly', 'webview', 'IPython'],
)
pyz = PYZ(a.pure)

# Two programs, one folder: they share _internal/.
# Python's UTF-8 mode: text in UTF-8 whatever Windows' own code page (the console's output, when it goes to
# another program, and any file the app would open without saying how).
UTF8 = [('X utf8', None, 'OPTION')]

windowless = EXE(
    pyz, a.scripts, UTF8,
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    icon=ICON,
    version=version_info(APP_NAME, f'{APP_NAME}.exe'),
    upx=False,  # (packed programs get flagged by antivirus software more often)
)
console = EXE(
    pyz, a.scripts, UTF8,
    exclude_binaries=True,
    name='reality-later',
    console=True,
    icon=ICON,
    version=version_info(f'{APP_NAME} (with a console: its commands)', 'reality-later.exe'),
    upx=False,
)
COLLECT(windowless, console, a.binaries, a.datas, name=APP_NAME, upx=False)
