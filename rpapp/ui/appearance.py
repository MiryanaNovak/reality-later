"""How the app looks on this device: light or dark, and the size of the text in chats.

Kept in a cookie, rp_look ("dark.large"), so each device has its own: your phone can be dark
and your computer light. The page reads it as it's built, so it opens the right way. The
accent color is for every device: it's in the settings (Settings › Appearance).
"""
from __future__ import annotations

import json
import weakref
from dataclasses import dataclass

from nicegui import Client, ui

COOKIE = 'rp_look'
COOKIE_DAYS = 400  # the most browsers keep a cookie

MODES = {'auto': 'Like this device', 'light': 'Light', 'dark': 'Dark'}
DARK = {'auto': None, 'light': False, 'dark': True}
SIZES = {'small': ('Small', 0.9375), 'normal': ('Normal', 1.0), 'large': ('Large', 1.125), 'larger': ('Larger', 1.25)}

_dark_modes: weakref.WeakKeyDictionary[Client, ui.dark_mode] = weakref.WeakKeyDictionary()


@dataclass
class Look:
    mode: str = 'auto'
    size: str = 'normal'

    @property
    def text_size(self) -> str:
        return f'{SIZES[self.size][1]}rem'

    def cookie_value(self) -> str:
        return f'{self.mode}.{self.size}'


def device_look() -> Look:
    """This device's choices, from its cookie (the defaults when there's none, or it's garbled)."""
    request = ui.context.client.request
    value = request.cookies.get(COOKIE, '') if request is not None else ''
    mode, _, size = value.partition('.')
    return Look(mode=mode if mode in MODES else 'auto', size=size if size in SIZES else 'normal')


# The dark background, from the very first frame (before the page's scripts switch it to dark mode)
EARLY_DARK = 'body:not(.body--light) { --page: #13161D; --ink: #E7E9EE; color-scheme: dark; }'


def apply_look(look: Look) -> None:
    """At the start of every page: open it in this device's mode, with its text size."""
    _dark_modes[ui.context.client] = ui.dark_mode(DARK[look.mode])
    early = {'dark': EARLY_DARK, 'auto': f'@media (prefers-color-scheme: dark) {{ {EARLY_DARK} }}'}.get(look.mode, '')
    ui.add_head_html(f'<style id="rp-look">:root {{ --chat-text-size: {look.text_size}; }} {early}</style>')


def change_look(look: Look) -> None:
    """From Settings: switch this page now, and remember it for this device."""
    client = ui.context.client
    dark = _dark_modes.get(client)
    if dark is not None:
        dark.set_value(DARK[look.mode])
    cookie = f'{COOKIE}={look.cookie_value()}; path=/; max-age={COOKIE_DAYS * 86400}; samesite=lax'
    client.run_javascript(f'document.cookie = {json.dumps(cookie)}; '
                          f'document.documentElement.style.setProperty("--chat-text-size", '
                          f'{json.dumps(look.text_size)});')
