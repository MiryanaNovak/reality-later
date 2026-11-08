"""Starting the app: what `python main.py` (in the source folder), `reality-later` (installed: pip, the
Flatpak) and `python -m rpapp` all run. See HELP, or --help.

With --reload, NiceGUI runs the app's server in a second process, which runs main.py again:
main(first=False) there, so what's done once (the lock, the messages, the tray icon) isn't done twice.
"""
from __future__ import annotations

import atexit
import sys
from pathlib import Path

HELP = '''Start {app}:

    {prog}

It opens its "This computer" page in your browser, at http://localhost:8091: how to
use the app here and from your other devices, and buttons for its settings file,
its data folder, and to quit it. Starting it again while it runs opens that page
again. Other devices use this computer's address on your network, over HTTPS:
https://192.168.1.20:8090 for instance (the page and this window show it).

    {prog} --no-browser    don't open the browser (also: open_browser = false)
    {prog} --no-tray       no icon in the system tray (also: tray = false)
    {prog} --reload        restart by itself when the code changes (for development)
    {prog} quit            stop it, if it's running (as Quit, in its tray icon's menu)
    {prog} check-sharing   check that card sharing works on this computer

The port, the addresses the app listens on, the networks allowed to connect, HTTPS
and the rest can be set in its settings file (see config.example.toml), here:
    {config}
Without it, the defaults apply.

Devices other than this computer must be paired once (on the "This computer" page,
or with these commands, while the app runs):

    {prog} pair [address]    a pairing link and its QR code
    {prog} approve [CODE]    the devices waiting to be allowed, or allow one
    {prog} deny CODE         refuse one

Your data is saved here (to keep it somewhere else, set the RPAPP_DATA environment
variable to that folder):
    {data}
The first time, it starts with the examples in rpapp/starter (see rpapp/starter.py).
'''
OPTIONS = {'--no-browser', '--no-tray', '--reload'}


def main(argv: list[str] | None = None, *, first: bool = True, script: Path | None = None) -> None:
    """Run the app (or one of its commands). `first`: this is the process it was started in (False: the one
    NiceGUI starts with --reload). `script`: main.py, when running from the source folder."""
    argv = sys.argv[1:] if argv is None else argv
    from .paths import command
    prog = command()  # (python main.py, reality-later, reality-later.exe, flatpak run ...: for the messages)
    if first and argv[:1] == ['tray']:  # the tray icon's own process (see tray.py): without the server's weight
        if len(argv) != 2:
            sys.exit(f'The app starts this itself: {prog} tray <data folder>')
        from . import tray
        sys.exit(tray.run(argv[1]))

    from . import APP_NAME, cli
    from .config import ConfigError, load_config
    from .paths import places

    where = places()
    data_dir = where.data

    if first and argv[:1] and argv[0] in cli.COMMANDS:  # (before NiceGUI, which takes a while to load)
        try:
            config = None if argv[0] in cli.NO_SETTINGS else load_config()  # (even if the settings file is wrong)
            sys.exit(cli.run(argv, config, data_dir=data_dir, prog=prog))
        except ConfigError as e:
            sys.exit(str(e))

    from nicegui import app, ui

    from . import ICONS, instance, launcher, starter
    from .access import Access, AccessGuard, SecurityHeaders
    from .config import NetworkFilter, https_setup
    from .devices import DeviceRegistry
    from .ui.app import build_root
    from .ui.computer_page import quit_everywhere

    if first and {'-h', '--help'} & set(argv):
        print(HELP.format(app=APP_NAME, prog=prog, config=where.config / 'config.toml', data=data_dir))
        sys.exit(0)

    options = {arg for arg in argv if arg}
    if unknown := options - OPTIONS:
        sys.exit(f'Unknown option {sorted(unknown)[0]}. See {prog} --help')
    try:
        config = load_config()  # config.toml (see paths.py), or the defaults
    except ConfigError as e:
        sys.exit(f'Can\'t start: {e}')
    server = config.server

    why_no_browser = launcher.browser_wanted(config, no_browser='--no-browser' in options)
    why_no_tray = launcher.tray_wanted(config, no_tray='--no-tray' in options, reload='--reload' in options)

    if first:  # (not again in the process NiceGUI starts with --reload)
        running_copy = instance.Instance(data_dir)
        if not running_copy.acquire():  # already running: show it, and stop here
            sys.exit(launcher.show_running_copy(running_copy, open_browser=why_no_browser is None))

    try:
        https = https_setup(config)  # None for plain HTTP; makes the self-signed certificate on first start
        devices = DeviceRegistry(data_dir)
    except (ConfigError, ValueError, OSError) as e:
        sys.exit(f'Can\'t start: {e}')
    info = launcher.ServerInfo(config, https, data_dir)

    tray_process = None
    if first:
        if problem := launcher.check_ports(info):
            sys.exit(problem)
        me = running_copy.publish(info.local_url)
        atexit.register(running_copy.release)  # (with --reload: this process doesn't run the app)
        print('\n'.join(launcher.starting_lines(info)), flush=True)
        launcher.when_ready(info, me, open_browser=why_no_browser is None)
        # (it waits for the app to answer, and goes away by itself when it stops)
        tray_process = launcher.start_tray(data_dir, script=script) if why_no_tray is None else None
        atexit.register(launcher.stop_tray, tray_process)

    access = Access(devices, scheme=info.scheme, port=server.port, enabled=server.pairing, listen=server.address)
    if server.pairing:
        access.register_routes(app)
        app.add_middleware(AccessGuard, access=access)
    instance.add_route(app, quit_app=quit_everywhere)  # (Quit, from the tray icon's menu)
    if (local_port := server.local_http_port) is not None:  # http://localhost:<local_port>, for this computer
        listener = launcher.LocalListener(app, local_port)
        app.on_startup(listener.start)
        app.on_shutdown(listener.stop)
    app.add_middleware(NetworkFilter, networks=server.networks())
    app.add_middleware(SecurityHeaders)  # added last: runs first, so even refusals get the headers
    if first and (seeded := starter.seed(data_dir)) and seeded.items:  # the very first start
        print(f'First start: added the examples ({seeded.summary()}).', flush=True)
    root = build_root(data_dir, access=access, server=info, models=where.models)
    if first and '--reload' not in options:
        # Once everything is saved. (Stopped by a signal, uvicorn ends the process before atexit would run.)
        app.on_shutdown(running_copy.release)
        app.on_shutdown(lambda: launcher.stop_tray(tray_process))

    try:
        ui.run(
            root,
            title=APP_NAME,
            host=server.address,
            port=server.port,
            ssl_certfile=str(https.cert) if https else None,
            ssl_keyfile=str(https.key) if https else None,
            dark=None,        # follow the device's light/dark setting
            favicon=ICONS / 'web' / 'favicon.ico',  # (and the others: ui/theme.py, ICON_LINKS)
            reload='--reload' in options,
            show=False,       # the browser is opened by launcher.when_ready, on this computer's own address
            show_welcome_message=False,  # launcher's messages say where to open it
        )
    except KeyboardInterrupt:  # Ctrl+C: the app has stopped cleanly; uvicorn passes it on afterwards
        pass
    if first:
        print(f'{APP_NAME} has stopped.')
