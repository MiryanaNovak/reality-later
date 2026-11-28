"""Placeholders in texts, filled in before they're sent to the AI:

    {{char}}     the character's name
    {{user}}     your persona's name
    {{sub}}      your persona's pronoun as a subject: she, he, they
    {{obj}}      as an object: her, him, them
    {{poss}}     possessive, before a noun: her, his, their (book)
    {{poss_p}}   possessive pronoun: hers, his, theirs
    {{ref}}      reflexive: herself, himself, themselves

    {{format}}   your message format (Settings › Message format: reformat.py), in a prompt's instructions

The names: same spelling as SillyTavern, case doesn't matter; its older <BOT> and <USER> work too.
The pronouns are your persona's (Personas › Pronouns: personas.PRONOUNS); without any chosen, or without
a persona, they/them. A pronoun starts with a capital at the start of a sentence or a line ("{{sub}} smiles."
→ "She smiles.", "*{{sub}} smiles*" too), or when its macro does ({{Sub}}, {{SUB}}).
"""
from __future__ import annotations

import re
from collections.abc import Sequence

_CHAR = re.compile(r'\{\{char\}\}|<BOT>', re.IGNORECASE)
_FORMAT = re.compile(r'\{\{format\}\}', re.IGNORECASE)
_USER = re.compile(r'\{\{user\}\}|<USER>', re.IGNORECASE)

FORMS = {'sub': 0, 'obj': 1, 'poss': 2, 'poss_p': 3, 'ref': 4}  # macro -> place in personas.Pronouns
THEY = ('they', 'them', 'their', 'theirs', 'themselves')         # without pronouns chosen
# A pronoun macro, with what comes before it when that's a sentence's start: the text's or a line's, or after
# . ! ? … (and a closing quote or bracket) and spaces; then opening marks (*actions*, "quotes", brackets)
_PRONOUN = re.compile(
    r'(?P<start>(?:^|(?<=[.!?…])[ \t]+|(?<=[.!?…]["”’\')\]*_])[ \t]+)[*_"“‘\'(\[]*)?'
    r'\{\{(?P<name>sub|obj|poss_p|poss|ref)\}\}', re.IGNORECASE | re.MULTILINE)


def fill(text: str, *, char: str, user: str, pronouns: Sequence[str] | None = None,
         format_rules: str | None = None) -> str:
    """Replace the placeholders with the names, and the pronouns' with `pronouns` (personas.Pronouns: subject,
    object, determiner, possessive, reflexive; None: they/them). `format_rules`: what {{format}} stands for
    (None: it stays as it is, as outside a prompt's instructions)."""
    if format_rules is not None:
        text = _FORMAT.sub(lambda _: format_rules, text)
    text = _USER.sub(lambda _: user, _CHAR.sub(lambda _: char, text))
    if '{{' not in text:
        return text
    forms = tuple(pronouns) if pronouns else THEY

    def pronoun(m: re.Match) -> str:
        name = m.group('name')
        word = forms[FORMS[name.lower()]]
        if m.group('start') is not None or name[0].isupper():
            word = word[:1].upper() + word[1:]
        return (m.group('start') or '') + word

    return _PRONOUN.sub(pronoun, text)
