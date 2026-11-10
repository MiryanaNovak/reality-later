"""The side menu's last lines: the user manual, the project's page on GitHub, how to write to its author,
and the app's version. Small, and at the bottom of the menu when it has room (the space left goes above
them); otherwise right after the menu's entries, scrolled to.

Contact opens a dialog with the address, to copy, beside a button that writes an email: an email link alone
does nothing on a computer without a mail program (as for those who use their mail in the browser).
"""
from __future__ import annotations

from urllib.parse import quote

from nicegui import ui

from .. import APP_NAME, CONTACT_EMAIL, MANUAL_URL, PROJECT_URL, __version__
from .widgets import copy_js


def menu_links() -> None:
    with ui.element('div').classes('menu-links').mark('menu-links'):
        with ui.element('div').classes('menu-links-row'):
            ui.button('User manual').props(f'flat dense no-caps href="{MANUAL_URL}" target=_blank') \
                .mark('link-manual')
            ui.button('GitHub').props(f'flat dense no-caps href="{PROJECT_URL}" target=_blank') \
                .mark('link-github')
            ui.button('Contact', on_click=contact).props('flat dense no-caps').mark('link-contact')
        ui.label(f'Version {__version__}').classes('menu-version').mark('app-version')


def contact() -> None:
    mail = f'mailto:{CONTACT_EMAIL}?subject={quote(f"{APP_NAME} {__version__}")}'
    with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes('dialog-card').mark('contact-dialog'):
        ui.label('Contact').classes('panel-title')
        ui.label(f'A question, a problem, an idea about {APP_NAME}? Write to:').classes('muted')
        ui.label(CONTACT_EMAIL).classes('contact-address').mark('contact-address')
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Close', on_click=dialog.close).props('flat').mark('contact-close')
            ui.button('Copy the address', on_click=lambda: ui.notify('Address copied')).props('outline') \
                .on('click', js_handler=copy_js(CONTACT_EMAIL)).mark('contact-copy')
            ui.button('Write an email').props(f'unelevated href="{mail}"').mark('contact-write')
    dialog.on_value_change(lambda e: _delete(dialog) if not e.value else None)
    dialog.open()


def _delete(dialog: ui.dialog) -> None:
    try:
        dialog.delete()
    except ValueError:  # (the page is already gone)
        pass
