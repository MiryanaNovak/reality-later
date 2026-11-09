"""The notice at the top of every page when a newer version of the app is out (rpapp/updates.py): which
one, what to do, a button to its page on GitHub, and Dismiss. Dismissed, it doesn't come back until a newer
release is out still.

Every page of every device shows it (it's above the pages, so it stays as you go from one to the next).
Each browser tab looks every half a minute whether there's something new to show: a release found in the
meantime, or the notice dismissed from another device; it's redrawn only then, so the page doesn't move.
"""
from __future__ import annotations

from nicegui import ui

from .. import APP_NAME
from ..updates import Updates, how_to_update

TICK = 30.0  # seconds


def update_notice(updates: Updates) -> None:
    box = ui.element('div').classes('update-notice').mark('update-notice').props('role=status')
    box.set_visibility(False)
    shown: list[str | None] = [None]  # (the version it shows: None, none)

    def draw() -> None:
        release = updates.available()
        version = release.version if release is not None else None
        if version == shown[0]:
            return
        shown[0] = version
        box.clear()
        box.set_visibility(release is not None)
        if release is None:
            return
        with box:
            ui.icon('new_releases').classes('update-notice-icon')
            with ui.element('div').classes('update-notice-text'):
                ui.label(f'{APP_NAME} {release.version} is out').classes('update-notice-title')
                ui.label(f'You have {updates.current}. {how_to_update()}').classes('text-sm')
            with ui.element('div').classes('update-notice-actions'):
                ui.button('Get it').props(f'unelevated dense href="{release.url}" target=_blank') \
                    .classes('px-3').mark('update-get')
                ui.button('Dismiss', on_click=dismiss).props('flat dense').classes('px-2').mark('update-dismiss')

    def dismiss() -> None:
        updates.dismiss()
        draw()

    draw()
    ui.timer(TICK, draw)
