# The Windows program

Reality Later for Windows, in two forms made from the same program:

- `reality-later-setup.exe`, the installer: for the current user, no administrator rights needed;
  a Start menu entry, an uninstaller (Settings › Apps), and two boxes to tick if wanted: a desktop
  shortcut, and "Start Reality Later when I sign in";
- `reality-later-windows.zip`, the same program, nothing to install: unzip it anywhere, and start
  `Reality Later.exe`.

The program is a folder (`Reality Later\`):

| | |
|---|---|
| `Reality Later.exe` | The app, without a console window: its icon in the tray and your browser are the app. Its messages go to a log file, and if it can't start, a message box says why (see `rpapp/windowless.py`). |
| `reality-later.exe` | The same app with a console: for its commands (`pair`, `approve`, `deny`, `quit`), `--help`, and to watch its messages. |
| `_internal\` | Python, the libraries, the app's files. |

## On GitHub

`.github/workflows/windows.yml` builds it on GitHub's Windows machines:

- push a version tag (`git tag v0.2.0 && git push --tags`): the installer and the zip are attached to
  that version's release, once the tests pass on Windows too (the Flatpak's workflow adds its files to
  the same release);
- or run it by hand (Actions › Windows › Run workflow): the files are in the run's artifacts.

Before handing them out, it checks them there: the tests, on Windows; the program starts, and its
pages, files and HTTPS answer (`packaging/smoke_test.py`, used for the Flatpak too); and the installer installs it, installs again over a
running copy (which it must stop properly first), and uninstalls it (`installer_test.py`).

## On a Windows computer

With Python 3.13 and [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install -e --id
JRSoftware.InnoSetup`), from the project's folder, in PowerShell:

    pip install . pyinstaller
    pyinstaller --noconfirm packaging\windows\reality-later.spec
    python packaging\smoke_test.py "dist\Reality Later"
    & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DVersion=0.2.0 /DFileVersion=0.2.0.0 packaging\windows\installer.iss

(Inno Setup's compiler, ISCC.exe, isn't on the PATH: `&` runs it by its full path. Installed for you
only, it's in `%LOCALAPPDATA%\Programs\Inno Setup 6` instead.) The program is then in
`dist\Reality Later\`, and the installer is `dist\reality-later-setup.exe`. To try the installer:
`python packaging\windows\installer_test.py dist\reality-later-setup.exe`, with the app not running;
it installs the app for your Windows account, then uninstalls it, leaving `%APPDATA%\Reality Later`
(data kept, as uninstalling does).
(`build\` and `dist\` are the build's own folders: keep them out of git.)

PyInstaller builds for the system it runs on, so this only makes a Windows program on Windows. But the
same build file works on Linux and macOS too, for that system: a way to try a change to it without
Windows (`packaging/smoke_test.py` runs on those too).

## For the people who install it

- **"Windows protected your PC"**: Windows' SmartScreen says so for programs that aren't signed, the
  first time. *More info › Run anyway* starts it.
- **Windows Firewall** asks, on the first start, whether Reality Later may use the network: allow it on
  private networks, for your phone and other devices to reach it. (This computer reaches it either way.)
- **Where things are**: the program in `%LOCALAPPDATA%\Programs\Reality Later`; the settings file, the
  certificate and the log (`reality-later.log`) in `%APPDATA%\Reality Later`, and the data in its
  `data` folder. The "This computer" page shows where, and opens them. Updates and uninstalling leave
  the data alone.
- **Quitting**: Quit, in its tray icon's menu or on its "This computer" page.
- **Starting when you sign in** (if ticked when installing): it starts quietly, with just its tray icon.
  It's turned off in Settings › Apps › Startup, or by installing again without ticking it.
- **A new version** installs over the old one: run its installer (it stops the running copy first).
- **The commands**, in a terminal: `"%LOCALAPPDATA%\Programs\Reality Later\reality-later.exe" pair`
  (or `approve`, `deny`, `quit`, `--help`).

## Signing

Not signed for now, hence SmartScreen's warning. [SignPath Foundation](https://signpath.org/) signs
open-source programs for free, once a project has releases and some users (they check); the version
details the build file gives the programs (name, version) are what they ask for. Microsoft's own
Artifact Signing is only for companies, or for individuals in the USA and Canada. Even signed,
SmartScreen's warning only goes away as the program builds up a reputation, over the releases.
