"""Which lore book entries go into a prompt: SillyTavern's "World Info" activation.

For every reply, the entries of the books in play (the character's and the
persona's) are checked against the end of the chat:

- an entry goes in when one of its keywords appears in the last few messages
  ("scan depth"), or always when it's marked constant;
- keywords follow the entry's settings, or the global ones in Settings: case
  sensitivity, whole words; a keyword written /like this/i is a regular expression;
- optional secondary keywords filter further (and any / and all / not any / not all);
- with recursion on, the text of the entries that went in is scanned too, so one
  entry can bring in another (unless the entries say otherwise);
- the entries share a budget of tokens: when more match than fit, the higher
  Order wins.

Same rules as SillyTavern 1.19 (public/scripts/world-info.js), with the
messages scanned as "Name: text" like SillyTavern does by default. Not applied
yet: trigger chance, inclusion groups, timed effects (sticky, cooldown, delay)
and character filters; outlet entries are left out (they need a macro). The
result lists them so the prompt view can say so.
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field

from .lorebooks import AT_DEPTH, OUTLET, LoreBook, LoreEntry
from .macros import fill
from .settings import LoreSettings
from .tokens import MESSAGE_OVERHEAD, estimate

MAX_PASSES = 10  # the chat scan, then up to 9 rounds of recursion

# SillyTavern's selectiveLogic values
AND_ANY, NOT_ALL, NOT_ANY, AND_ALL = 0, 1, 2, 3

_REGEX_KEY = re.compile(r'^/([\s\S]+?)/([gimsuy]*)$')


@dataclass
class Source:
    """Text an entry's keywords are looked for in."""
    label: str  # for the prompt view: 'the chat', 'the scenario', 'entry "The forest"'
    text: str


@dataclass
class Activation:
    """An entry that triggered (whether or not it made it into the prompt)."""
    book: LoreBook
    entry: LoreEntry
    content: str           # macros filled in
    reason: str            # 'always included', 'keyword "forest" in the chat', ...
    status: str = 'used'   # 'used', 'over_budget' or 'skipped'
    note: str = ''         # why it was skipped

    @property
    def tokens(self) -> int:
        """What the entry costs in the prompt. At a depth it's a message of its own."""
        return estimate(self.content) + (MESSAGE_OVERHEAD if self.entry.position == AT_DEPTH else 0)


@dataclass
class LoreResult:
    activations: list[Activation] = field(default_factory=list)  # in the order they triggered
    notes: list[str] = field(default_factory=list)               # features of the books not applied yet

    @property
    def used(self) -> list[Activation]:
        return [a for a in self.activations if a.status == 'used']

    @property
    def tokens(self) -> int:
        return sum(a.tokens for a in self.used)


def activate(books: list[LoreBook], *, chat: list[tuple[str, str]], settings: LoreSettings, budget: int,
             char: str, user: str, texts: dict[str, str] | None = None) -> LoreResult:
    """The entries to put in the prompt.

    `chat` is the conversation as (speaker name, text), oldest first. `texts` are the other texts
    some entries can scan too: 'scenario' (the character's), 'persona' (its description).
    `budget` is in tokens; entries set to ignore it don't count.
    """
    texts = texts or {}
    result = LoreResult(notes=_unsupported(books))
    candidates = [(book, entry) for book in books for entry in _by_priority(book.entries.values())
                  if not entry.disable and (not entry.triggers or 'normal' in entry.triggers)]
    candidates.sort(key=lambda be: -be[1].order)  # stable: within one order, books and entries keep their order
    done: set[tuple[str, int]] = set()
    recursion: list[Source] = []
    left = budget

    for pass_no in range(MAX_PASSES):
        found = []
        for book, entry in candidates:
            key = (book.id, entry.uid)
            if key in done or not _may_trigger(entry, pass_no):
                continue
            if entry.constant:
                reason = 'always included'
            else:
                sources = _sources(entry, chat, settings, texts) + (recursion if pass_no else [])
                reason = _match(entry, sources, settings, char=char, user=user)
                if not reason:
                    continue
            done.add(key)
            found.append(Activation(book, entry, fill(entry.content, char=char, user=user).strip(), reason))
        if not found:
            break

        overflow = False
        for a in found:
            if a.entry.position == OUTLET:
                a.status, a.note = 'skipped', 'outlet entries are placed by a macro, which isn\'t supported yet'
            elif not a.content:
                a.status, a.note = 'skipped', 'it has no text'
            elif a.entry.ignoreBudget or a.tokens <= left:
                if not a.entry.ignoreBudget:
                    left -= a.tokens
                if not a.entry.preventRecursion:
                    recursion.append(Source(f'entry "{a.entry.title}"', a.content))
            else:
                a.status, overflow = 'over_budget', True
            result.activations.append(a)
        if not settings.recursive or overflow:  # SillyTavern stops scanning once the budget is full
            break
    return result


# -- the rules ------------------------------------------------------------------------

def _by_priority(entries) -> list[LoreEntry]:
    return sorted(entries, key=lambda e: (e.displayIndex if e.displayIndex is not None else e.uid, e.uid))


def _delay_level(entry: LoreEntry) -> int:
    value = entry.delayUntilRecursion
    return 0 if value is False or value is None else 1 if value is True else int(value)


def _may_trigger(entry: LoreEntry, pass_no: int) -> bool:
    """Pass 0 scans the chat; later passes are recursion (the text of entries that went in)."""
    if pass_no == 0:
        return _delay_level(entry) == 0
    return not entry.excludeRecursion and _delay_level(entry) <= pass_no


def _sources(entry: LoreEntry, chat: list[tuple[str, str]], settings: LoreSettings,
             texts: dict[str, str]) -> list[Source]:
    depth = entry.scanDepth if entry.scanDepth is not None else settings.scan_depth
    sources = []
    if depth > 0 and chat:
        sources.append(Source('the chat', '\n'.join(f'{name}: {text}' for name, text in chat[-depth:])))
    # SillyTavern cards keep the character's description, personality and scenario apart; here, the
    # scenario holds all three (the description is only for you), so entries looking in any of them look there.
    looked = set()
    for flag, key, label in (('matchCharacterDescription', 'scenario', 'the scenario'),
                             ('matchCharacterPersonality', 'scenario', 'the scenario'),
                             ('matchScenario', 'scenario', 'the scenario'),
                             ('matchPersonaDescription', 'persona', 'your persona\'s description')):
        if getattr(entry, flag) and texts.get(key) and key not in looked:
            looked.add(key)
            sources.append(Source(label, texts[key]))
    return sources


def _match(entry: LoreEntry, sources: list[Source], settings: LoreSettings, *, char: str, user: str) -> str:
    """Why the entry triggers ('keyword "x" in the chat'), or '' if it doesn't."""
    if not sources:
        return ''
    case = entry.caseSensitive if entry.caseSensitive is not None else settings.case_sensitive
    whole = entry.matchWholeWords if entry.matchWholeWords is not None else settings.whole_words
    everything = '\n'.join(s.text for s in sources)

    def found(key: str, text: str) -> bool:
        return key_matches(key, text, case_sensitive=case, whole_words=whole)

    keys = [k for k in (fill(k, char=char, user=user).strip() for k in entry.key) if k]
    primary = next((k for k in keys if found(k, everything)), None)
    if primary is None:
        return ''

    secondary = [k for k in (fill(k, char=char, user=user).strip() for k in entry.keysecondary) if k]
    if entry.selective and secondary:
        hits = [found(k, everything) for k in secondary]
        logic = entry.selectiveLogic
        ok = (any(hits) if logic == AND_ANY else all(hits) if logic == AND_ALL
              else not any(hits) if logic == NOT_ANY else not all(hits) if logic == NOT_ALL else False)
        if not ok:
            return ''

    where = next((s.label for s in sources if found(primary, s.text)), 'the scanned text')
    return f'keyword "{primary}" in {where}'


def key_matches(key: str, text: str, *, case_sensitive: bool = False, whole_words: bool = False) -> bool:
    """Whether a keyword appears in the text, the way SillyTavern checks it.

    /pattern/flags is a regular expression (flags i, m, s apply; case and whole-words settings don't).
    With whole words, a single-word keyword must not be part of a longer word; a keyword of
    several words is matched as written.
    """
    if _REGEX_KEY.match(key):
        regex = _regex(key)
        return regex is not None and regex.search(text) is not None  # an invalid pattern matches nothing
    if not case_sensitive:
        key, text = key.lower(), text.lower()
    if whole_words and len(key.split()) <= 1:
        return re.search(rf'(?:^|\W){re.escape(key)}(?:$|\W)', text) is not None
    return key in text


@functools.lru_cache(maxsize=512)
def _regex(key: str) -> re.Pattern | None:
    m = _REGEX_KEY.match(key)
    flags = 0
    for letter, flag in (('i', re.IGNORECASE), ('m', re.MULTILINE), ('s', re.DOTALL)):
        if letter in m[2]:
            flags |= flag
    try:
        return re.compile(m[1], flags)
    except re.error:
        return None


def _unsupported(books: list[LoreBook]) -> list[str]:
    """Plain-language notes on what these books use that isn't applied yet."""
    counts = {'chance': 0, 'group': 0, 'timed': 0, 'filter': 0}
    for book in books:
        for e in book.entries.values():
            if e.disable:
                continue
            counts['chance'] += e.useProbability and e.probability < 100
            counts['group'] += bool(e.group.strip())
            counts['timed'] += any(v for v in (e.sticky, e.cooldown, e.delay))
            counts['filter'] += bool(e.characterFilter and (e.characterFilter.names or e.characterFilter.tags))
    labels = {'chance': 'a trigger chance', 'group': 'an inclusion group',
              'timed': 'timed effects (sticky, cooldown, delay)', 'filter': 'a character filter'}
    return [f'{n} {"entry has" if n == 1 else "entries have"} {labels[k]}, which isn\'t applied yet: '
            f'{"it triggers" if n == 1 else "they trigger"} as if it weren\'t set.'
            for k, n in counts.items() if n]
