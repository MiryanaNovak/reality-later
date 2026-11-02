"""Start the app:

    python main.py

It opens its "This computer" page in your browser, at http://localhost:8091: how to
use the app here and from your other devices, and buttons for its settings file,
its data folder, and to quit it. Starting it again while it runs opens that page
again. Other devices use this computer's address on your network, over HTTPS:
https://192.168.1.20:8090 for instance (the page and this window show it).

    python main.py --no-browser    don't open the browser (also: open_browser = false)
    python main.py --reload        restart by itself when the code changes (for development)

The port, the addresses the app listens on, the networks allowed to connect, HTTPS
and the rest can be set in config.toml next to this file (see config.example.toml).
Without it, the defaults apply.

Devices other than this computer must be paired once (on the "This computer" page,
or with these commands, while the app runs):

    python main.py pair [address]    a pairing link and its QR code
    python main.py approve [CODE]    the devices waiting to be allowed, or allow one
    python main.py deny CODE         refuse one

Your data is saved in the "data" folder next to this file. To keep it
somewhere else, set the RPAPP_DATA environment variable to that folder.
The first time, it starts with the examples in rpapp/starter (see rpapp/starter.py).
"""
import atexit
import os
import sys
from pathlib import Path

from nicegui import app, ui

from rpapp import APP_NAME, cli, instance, launcher, starter
from rpapp.access import Access, AccessGuard, SecurityHeaders
from rpapp.config import ConfigError, NetworkFilter, app_dir, https_setup, load_config
from rpapp.devices import DeviceRegistry
from rpapp.ui.app import build_root

DATA_DIR = Path(os.environ.get('RPAPP_DATA') or app_dir() / 'data')
OPTIONS = {'--no-browser', '--reload'}

if __name__ == '__main__' and sys.argv[1:2] and sys.argv[1] in cli.COMMANDS:
    try:
        sys.exit(cli.run(sys.argv[1:], load_config(), data_dir=DATA_DIR))
    except ConfigError as e:
        sys.exit(str(e))

if __name__ == '__main__' and {'-h', '--help'} & set(sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

if __name__ in {'__main__', '__mp_main__'}:
    options = {arg for arg in sys.argv[1:] if arg}
    if unknown := options - OPTIONS:
        sys.exit(f'Unknown option {sorted(unknown)[0]}. See python main.py --help')
    try:
        config = load_config()  # config.toml next to this script, or the defaults
    except ConfigError as e:
        sys.exit(f'Can\'t start: {e}')
    server = config.server

    why_no_browser = launcher.browser_wanted(config, no_browser='--no-browser' in options)

    if __name__ == '__main__':  # (not again in the process NiceGUI starts with --reload)
        running_copy = instance.Instance(DATA_DIR)
        if not running_copy.acquire():  # already running: show it, and stop here
            sys.exit(launcher.show_running_copy(running_copy, open_browser=why_no_browser is None))

    try:
        https = https_setup(config)  # None for plain HTTP; makes the self-signed certificate on first start
        devices = DeviceRegistry(DATA_DIR)
    except (ConfigError, ValueError) as e:
        sys.exit(f'Can\'t start: {e}')
    info = launcher.ServerInfo(config, https, DATA_DIR)

    if __name__ == '__main__':
        if problem := launcher.check_ports(info):
            sys.exit(problem)
        me = running_copy.publish(info.local_url)
        atexit.register(running_copy.release)  # (with --reload: this process doesn't run the app)
        print('\n'.join(launcher.starting_lines(info)), flush=True)
        launcher.when_ready(info, me, open_browser=why_no_browser is None)

    access = Access(devices, scheme=info.scheme, port=server.port, enabled=server.pairing, listen=server.address)
    if server.pairing:
        access.register_routes(app)
        app.add_middleware(AccessGuard, access=access)
    instance.add_route(app)
    if (local_port := server.local_http_port) is not None:  # http://localhost:<local_port>, for this computer
        listener = launcher.LocalListener(app, local_port)
        app.on_startup(listener.start)
        app.on_shutdown(listener.stop)
    app.add_middleware(NetworkFilter, networks=server.networks())
    app.add_middleware(SecurityHeaders)  # added last: runs first, so even refusals get the headers
    if __name__ == '__main__' and (seeded := starter.seed(DATA_DIR)) and seeded.items:  # the very first start
        print(f'First start: added the examples ({seeded.summary()}).', flush=True)
    root = build_root(DATA_DIR, access=access, server=info)
    if __name__ == '__main__' and '--reload' not in options:
        # Once everything is saved. (Stopped by a signal, uvicorn ends the process before atexit would run.)
        app.on_shutdown(running_copy.release)

    try:
        ui.run(
            root,
            title=APP_NAME,
            host=server.address,
            port=server.port,
            ssl_certfile=str(https.cert) if https else None,
            ssl_keyfile=str(https.key) if https else None,
            dark=None,        # follow the device's light/dark setting
            favicon='🎭',
            reload='--reload' in options,
            show=False,       # the browser is opened by launcher.when_ready, on this computer's own address
            show_welcome_message=False,  # launcher's messages say where to open it
        )
    except KeyboardInterrupt:  # Ctrl+C: the app has stopped cleanly; uvicorn passes it on afterwards
        pass
    if __name__ == '__main__':
        print(f'{APP_NAME} has stopped.')
