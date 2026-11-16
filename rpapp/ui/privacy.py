"""Settings › Privacy, on the pages: away from home (rpapp/privacy.py: the internet, Tailscale...), every picture is
blurred, whatever page it's on: characters', personas', those in descriptions and chats. So that people around
can't see them on the screen. It's only that: the pictures still reach the browser, which blurs them.

    blur_pictures(on)    at the start of every page: whether its pictures are blurred (theme.py's .privacy-blur)
    changed(on)          the setting changed: every page open now follows, away from home
"""
from __future__ import annotations

import logging
import weakref

from nicegui import Client, ui

from ..privacy import Place, where

log = logging.getLogger(__name__)

BLUR = 'privacy-blur'  # (on the page's body)
_pages: weakref.WeakKeyDictionary[Client, Place] = weakref.WeakKeyDictionary()  # each page open, and where it is


def this_place() -> Place:
    """Where the browser showing this page connects from (see rpapp/privacy.py)."""
    client = ui.context.client
    if client in _pages:
        return _pages[client]
    request = client.request
    if request is None:
        return 'away'
    headers = {k.lower(): v for k, v in request.headers.items()}
    return where(request.client.host if request.client else None, headers)


def blur_pictures(enabled: bool) -> None:
    """At the start of a page: blur its pictures if the setting is on (`enabled`) and it's away from home."""
    place = this_place()
    _pages[ui.context.client] = place
    if enabled and place == 'away':
        ui.query('body').classes(add=BLUR)


def changed(enabled: bool) -> None:
    """The setting changed: the pages open away from home follow, now."""
    for client, place in list(_pages.items()):
        if place != 'away':
            continue
        try:
            with client:
                ui.query('body').classes(add=BLUR) if enabled else ui.query('body').classes(remove=BLUR)
        except Exception:  # (a page closed meanwhile)
            log.debug('A page closed while its pictures changed', exc_info=True)
