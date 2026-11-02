"""Placeholders in texts: {{char}} is the character's name, {{user}} is your persona's name.

Same spelling as SillyTavern, case doesn't matter; its older <BOT> and <USER> work too.
"""
from __future__ import annotations

import re

_CHAR = re.compile(r'\{\{char\}\}|<BOT>', re.IGNORECASE)
_USER = re.compile(r'\{\{user\}\}|<USER>', re.IGNORECASE)


def fill(text: str, *, char: str, user: str) -> str:
    """Replace the placeholders with the names."""
    return _USER.sub(lambda _: user, _CHAR.sub(lambda _: char, text))
