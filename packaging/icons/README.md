# Icons for the packaged app

The app's own icons, for the programs built for each system. (The ones the running app uses, the
browser's and the tray's, are in `rpapp/icons/`.)

| File | For |
|---|---|
| `windows/reality-later.ico` | The Windows program (PyInstaller: `--icon packaging/icons/windows/reality-later.ico`) |
| `macos/reality-later.icns` | The Mac app (PyInstaller: `--icon packaging/icons/macos/reality-later.icns`) |
| `linux/reality-later.svg` | Linux and Flatpak: installed as `share/icons/hicolor/scalable/apps/<app id>.svg` |
| `linux/reality-later-<size>.png` | Linux and Flatpak: `share/icons/hicolor/<size>x<size>/apps/<app id>.png` |
| `source/` | The drawings they were all made from |

The SVGs must start with a plain `<svg …>` tag: desktops, and Flatpak's check of the icons it
installs, look for it to tell an SVG ("is not a valid icon: Format not recognized" otherwise).
Recraft exports them as `<ns0:svg …>`, `<ns0:path …>`: valid, and browsers show them, but not
recognized there. To fix a new export (the drawing stays the same):

    sed -i 's/<ns0:/</g; s/<\/ns0:/<\//g; s/xmlns:ns0=/xmlns=/' drawing.svg

`tests/test_icons.py` checks every SVG of the project for it.

In `rpapp/icons/`:

| Folder | For |
|---|---|
| `web/` | The browser: tab icon (`favicon.svg`, `favicon.ico`, PNG sizes), iPhone and iPad home screen (`apple-touch-icon.png`), Android home screen (`manifest.json` and its `icon-*.png`) |
| `tray/` | The tray icon: `white-*` for dark panels and taskbars, `black-*` for light ones (and the Mac menu bar, which recolors it) |
