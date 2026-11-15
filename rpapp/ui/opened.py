"""Which folding parts of a page are open: kept while you go elsewhere and back (for the browser tab), and in a
cookie (for the device), as appearance.py keeps the look.

    opened = Opened('rp_character_sections', default={'summary', 'scenario'})
    opened.is_open('summary')        # the default, until changed on this device
    opened.set('summary', False)

Keys are short words: lowercase letters, digits and dashes (folder ids, section names).
"""
from __future__ import annotations

import json
import re
import weakref
from collections.abc import Iterable
from typing import Any

from nicegui import ui

COOKIE_DAYS = 400
KEY = re.compile(r'[a-z0-9-]{1,64}')
_tabs: weakref.WeakKeyDictionary[Any, dict[str, set[str]]] = weakref.WeakKeyDictionary()


class Opened:
    def __init__(self, cookie: str, *, default: Iterable[str] = ()) -> None:
        self.cookie = cookie
        self.default = set(default)

    def keys(self) -> set[str]:
        """The parts open on this browser tab's page (from the device's cookie the first time; without one,
        the default)."""
        client = ui.context.client
        tabs = _tabs.setdefault(client, {})
        if self.cookie not in tabs:
            request = client.request
            value = request.cookies.get(self.cookie) if request is not None else None
            tabs[self.cookie] = set(self.default) if value is None else \
                {v for v in value.split('.') if KEY.fullmatch(v)}
        return tabs[self.cookie]

    def is_open(self, key: str) -> bool:
        return key in self.keys()

    def set(self, key: str, is_open: bool) -> None:
        keys = self.keys()
        if (key in keys) == is_open:
            return
        (keys.add if is_open else keys.discard)(key)
        cookie = f'{self.cookie}={".".join(sorted(keys))}; path=/; max-age={COOKIE_DAYS * 86400}; samesite=lax'
        ui.run_javascript(f'document.cookie = {json.dumps(cookie)}')
