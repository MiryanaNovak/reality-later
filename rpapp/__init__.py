"""Reality Later: chat with AI characters, in your browser, from a program on your computer."""

from pathlib import Path

APP_NAME = 'Reality Later'
TAGLINE = 'The real world can wait.'
# The app's version: the only place it's written (pyproject.toml reads it). A release is its tag, v + this
# (v0.2.0): the release workflows refuse a tag that isn't, as the app compares it with GitHub's latest
# release to tell you when a newer one is out (updates.py).
__version__ = '0.1.0'
# Where to learn more about it, and whom to write to (the side menu's last lines: ui/menu_links.py)
PROJECT_URL = 'https://github.com/MiryanaNovak/reality-later'
MANUAL_URL = f'{PROJECT_URL}/blob/main/UserManual.md'
CONTACT_EMAIL = 'miryana.novak@tutamail.com'
ICONS = Path(__file__).parent / 'icons'  # web/: the browser's; tray/: the tray's (and packaging/icons: the programs')
