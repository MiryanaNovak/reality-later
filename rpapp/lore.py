"""Which lore book entries go into a prompt: SillyTavern's "World Info" activation, choosing by relevance.

For every reply, the entries of the books in play (the character's and the
persona's) are checked against the end of the chat:

- an entry is a candidate when one of its keywords appears in the last few messages
  ("scan depth"), or always when it's marked constant;
- keywords follow the entry's settings, or the global ones in Settings: case
  sensitivity, whole words (key_matches: with plural endings, and any ending
  after a *); a keyword written /like this/i is a regular expression;
- optional secondary keywords filter further (and any / and all / not any / not all);
- with vector search on (vectors.py), an entry is also a candidate when it's close enough in meaning to
  the last messages (the same scan depth), without a keyword: the closest first, up to a number
  (Closeness). Unless it's set to come in only by keyword; and one set to come in only by meaning
  ignores its keywords (LoreEntry.strategy);
- chaining (Settings › Lore books): the text of the candidates is searched for keywords too, so one entry
  can bring in another (unless the entries say otherwise), for so many levels (1 by default: only
  entries named in those found in the chat). With vector search on for lore books, an entry brought in
  that way must also be close enough in meaning to the chat, as one found by meaning alone must
  ("unrelated" otherwise, and it brings in nothing in turn).

The candidates then share a budget of tokens. By default, the most related to the chat go first (rank()):
those always included; then those whose keyword is in the last message, then in the one before, and so on;
then those found in the scenario or your persona's description; then by meaning; then those brought in by
another entry, level by level. Among equals: the closest in meaning to the chat (with vector search),
then the higher Order. An entry brought in by another goes in only if that one did. The budget is a
limit, not a target: what isn't a candidate stays out, room left or not. With "Order first" (as
SillyTavern), the higher Order goes first instead, level by level.

Same rules as SillyTavern 1.19 (public/scripts/world-info.js) for finding the candidates, with the
messages scanned as "Name: text" like SillyTavern does by default. But keywords
are found differently: as whole words by default, plurals included, and a * at
the end allows any ending; keywords of 3 letters or fewer are whole words even
with whole words off; and in languages written without spaces, keywords are
found anywhere (see key_matches). (In SillyTavern, a * is just a *.) Not applied
yet: trigger chance, inclusion groups, timed effects (sticky, cooldown, delay)
and character filters; outlet entries are left out (they need a macro). The
result lists them so the prompt view can say so.
"""
from __future__ import annotations

import functools
import re
from collections.abc import Sequence
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
    label: str                # for the prompt view: 'the last message', 'the scenario', 'entry "The forest"'
    text: str
    age: int | None = None    # a message: how many come after it (0: the last one)
    level: int = -1           # an entry's text: that entry's level (see Activation)
    key: tuple[str, int] | None = None  # an entry's text: which entry (book id, uid)


@dataclass
class Activation:
    """An entry that triggered (whether or not it made it into the prompt)."""
    book: LoreBook
    entry: LoreEntry
    content: str           # macros filled in
    reason: str            # 'always included', 'keyword "forest" in the last message', ...
    status: str = 'used'   # 'used', 'over_budget', 'skipped' or 'unrelated' (brought in, but not close in meaning)
    note: str = ''         # why it was skipped
    level: int = 0         # 0: found in the chat or the texts (or always included); 1+: brought in by an entry of
    #                        the level before (`by`)
    by: tuple[str, int] | None = None  # (book id, uid) of the entry that brought it in
    age: int | None = None             # found by keyword in a message: how many come after it
    meaning: bool = False              # found by meaning alone
    closeness: float | None = None     # to the last messages (with vector search), from 0 to 1

    @property
    def tokens(self) -> int:
        """What the entry costs in the prompt. At a depth it's a message of its own."""
        return estimate(self.content) + (MESSAGE_OVERHEAD if self.entry.position == AT_DEPTH else 0)


@dataclass
class Closeness:
    """How close in meaning the entries are to the last messages (from vector search, vectors.py)."""
    scores: dict[tuple[str, int], list[float]]  # (book id, entry uid) -> one per message, oldest first
    threshold: float                            # what's close enough
    limit: int                                  # how many entries it may bring in, at most

    def score(self, book_id: str, uid: int, depth: int) -> float | None:
        """Its closeness to the last `depth` messages: to the closest of them."""
        values = self.scores.get((book_id, uid))
        return max(values[-depth:]) if values and depth > 0 else None


@dataclass
class Meaning:
    """Vector search's part in a prompt: for the lore books, for the memories (None: not used), and why
    it couldn't be used if it was on ('Vector search is on, but...')."""
    lore: Closeness | None = None
    memory: Closeness | None = None
    note: str = ''


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
             char: str, user: str, texts: dict[str, str] | None = None,
             closeness: Closeness | None = None, scan_all: bool = False,
             pronouns: Sequence[str] | None = None) -> LoreResult:
    """The entries to put in the prompt: the candidates (see the notes at the top), then those that fit the budget,
    in the order of rank(). The result lists them all in that order, with why each is in or out.

    `chat` is the conversation as (speaker name, text), oldest first. `texts` are the other texts
    some entries can scan too: 'scenario' (the character's), 'persona' (its description).
    `budget` is in tokens; entries set to ignore it don't count. `closeness`: from vector search.
    `scan_all`: every message of `chat` is scanned, whatever the scan depth (a memory call's messages: all of
    them are being remembered); entries that don't scan the chat (a depth of 0) still don't. `pronouns`: your
    persona's, for the pronoun macros (see macros.py).
    """
    texts = texts or {}
    result = LoreResult(notes=_unsupported(books))
    if closeness is None and (n := sum(not e.disable and not e.constant and e.onlyBy == 'meaning'
                                       for b in books for e in b.entries.values())):
        result.notes.append(f'{n} {"entry is" if n == 1 else "entries are"} set to come in only by meaning, but '
                            f'vector search isn\'t used for lore books: {"it" if n == 1 else "they"} can\'t come in '
                            f'(Settings › Vector search).')
    candidates = [(book, entry) for book in books for entry in _by_priority(book.entries.values())
                  if not entry.disable and (not entry.triggers or 'normal' in entry.triggers)]
    candidates.sort(key=lambda be: -be[1].order)  # stable: within one order, books and entries keep their order
    levels = min(settings.recursion_levels, MAX_PASSES - 1) if settings.recursive else 0
    done: set[tuple[str, int]] = set()
    chained: list[Source] = []  # the text of the candidates, for those they bring in
    found: list[Activation] = []

    def closeness_of(book: LoreBook, entry: LoreEntry) -> float | None:
        return closeness.score(book.id, entry.uid, _depth(entry, chat, settings, scan_all)) if closeness else None

    # (with chaining on, a last pass only looks: to say what chaining further would bring)
    for pass_no in range(levels + (2 if settings.recursive else 1)):
        looking = pass_no > levels
        new: list[Activation] = []
        near: list[tuple[float, LoreBook, LoreEntry]] = []  # close in meaning, without a keyword (the chat's pass)
        for book, entry in candidates:
            key = (book.id, entry.uid)
            if key in done or not _may_trigger(entry, pass_no):
                continue
            if entry.constant:
                if looking:
                    continue
                where, reason = None, 'always included'
            else:
                sources = _sources(entry, chat, settings, texts, scan_all) + (chained if pass_no else [])
                where, reason = (None, '') if entry.onlyBy == 'meaning' else _match(
                    entry, sources, settings, char=char, user=user, pronouns=pronouns)
                if not reason:
                    if closeness and pass_no == 0 and entry.onlyBy != 'keywords':
                        score = closeness_of(book, entry)
                        if score is not None and score >= closeness.threshold:
                            near.append((score, book, entry))
                    continue
            done.add(key)
            new.append(Activation(book, entry, fill(entry.content, char=char, user=user, pronouns=pronouns).strip(),
                                  reason, level=where.level + 1 if where and where.key else 0,
                                  by=where.key if where else None, age=where.age if where else None,
                                  closeness=closeness_of(book, entry)))
        if looking:
            if n := sum(1 for a in new if a.by):
                result.notes.append(f'{n} more {"entry is" if n == 1 else "entries are"} named in entries brought in '
                                    f'by others: chaining stops after {levels} level{"s" if levels > 1 else ""} '
                                    f'(Settings › Lore books).')
            break
        if closeness and near:  # after the keyword matches: the closest first, so many at most
            near.sort(key=lambda n: -n[0])
            for score, book, entry in near[:closeness.limit]:
                done.add((book.id, entry.uid))
                new.append(Activation(book, entry, fill(entry.content, char=char, user=user,
                                                        pronouns=pronouns).strip(),
                                      f'close in meaning to the chat ({score:.2f})', meaning=True, closeness=score))
        if not new:
            break
        for a in new:
            if a.entry.position == OUTLET:
                a.status, a.note = 'skipped', 'outlet entries are placed by a macro, which isn\'t supported yet'
            elif not a.content:
                a.status, a.note = 'skipped', 'it has no text'
            elif a.by and settings.chained_close and closeness and a.closeness is not None \
                    and a.closeness < closeness.threshold:
                a.status, a.note = 'unrelated', (f'it\'s not close enough in meaning to the chat ({a.closeness:.2f}, '
                                                 f'{closeness.threshold:.2f} needed)')
            if a.status == 'used' and not a.entry.preventRecursion:
                chained.append(Source(f'entry "{a.entry.title}"', a.content, level=a.level,
                                      key=(a.book.id, a.entry.uid)))
        found += new

    # The budget: in the order of rank, each one that fits (and whose entry that brought it in did)
    order = {book.id: i for i, book in enumerate(books)}
    found.sort(key=lambda a: rank(a, settings, order))
    went_in: set[tuple[str, int]] = set()
    left = budget
    for a in found:
        if a.status != 'used':
            pass
        elif a.by and a.by not in went_in:
            a.status, a.note = 'skipped', 'the entry that brought it in isn\'t in'
        elif a.entry.ignoreBudget or a.tokens <= left:
            if not a.entry.ignoreBudget:
                left -= a.tokens
            went_in.add((a.book.id, a.entry.uid))
        else:
            a.status = 'over_budget'
    result.activations = found
    return result


def rank(a: Activation, settings: LoreSettings, books: dict[str, int]) -> tuple:
    """Where an entry comes when the candidates share the budget: the lower, the sooner (see the notes at the
    top). `books`: each book's place among those in play."""
    then = (-a.entry.order, books.get(a.book.id, 0), a.entry.displayIndex if a.entry.displayIndex is not None
            else a.entry.uid, a.entry.uid)
    if settings.priority == 'order':  # (as SillyTavern: level by level, those by meaning after the keywords')
        return (a.level, a.meaning, *then)
    if a.by:
        kind, step = 4, a.level              # brought in by another entry, level by level
    elif a.entry.constant:
        kind, step = 0, 0                    # always included
    elif a.age is not None:
        kind, step = 1, a.age                # keyword in the chat: the last message first
    elif a.meaning:
        kind, step = 3, 0                    # by meaning
    else:
        kind, step = 2, 0                    # keyword in the scenario, or your persona's description
    return (kind, step, -(a.closeness or 0.0), *then)


def most_tokens(books: list[LoreBook], *, budget: int, char: str, user: str,
                pronouns: Sequence[str] | None = None) -> int:
    """The most the books' entries could ever take in a prompt, whichever come in: those that may come in, up to
    the budget, and those that ignore it. (The prompt keeps that much room for lore: prompts.py.)"""
    counted = ignoring = 0
    for book in books:
        for entry in book.entries.values():
            if entry.disable or (entry.triggers and 'normal' not in entry.triggers) or entry.position == OUTLET:
                continue
            content = fill(entry.content, char=char, user=user, pronouns=pronouns).strip()
            if not content:
                continue
            tokens = estimate(content) + (MESSAGE_OVERHEAD if entry.position == AT_DEPTH else 0)  # (as Activation)
            if entry.ignoreBudget:
                ignoring += tokens
            else:
                counted += tokens
    return min(budget, counted) + ignoring


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


def _depth(entry: LoreEntry, chat: list[tuple[str, str]], settings: LoreSettings, scan_all: bool) -> int:
    """How many of the last messages the entry scans."""
    depth = entry.scanDepth if entry.scanDepth is not None else settings.scan_depth
    return len(chat) if scan_all and depth > 0 else depth


def _sources(entry: LoreEntry, chat: list[tuple[str, str]], settings: LoreSettings,
             texts: dict[str, str], scan_all: bool = False) -> list[Source]:
    """What the entry's keywords are looked for in: the messages it scans, the last one first, then the texts it
    looks in."""
    depth = _depth(entry, chat, settings, scan_all)
    sources = []
    if depth > 0 and chat:
        for age, (name, text) in enumerate(reversed(chat[-depth:])):
            label = 'the last message' if age == 0 else \
                f'the chat, {age} message{"s" if age > 1 else ""} before the last'
            sources.append(Source(label, f'{name}: {text}', age=age))
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


def _match(entry: LoreEntry, sources: list[Source], settings: LoreSettings, *, char: str, user: str,
           pronouns: Sequence[str] | None = None) -> tuple[Source | None, str]:
    """Where the entry's keyword is (the first source that has it), and why it triggers ('keyword "x" in the last
    message'); (None, '') if it doesn't."""
    if not sources:
        return None, ''
    case = entry.caseSensitive if entry.caseSensitive is not None else settings.case_sensitive
    whole = entry.matchWholeWords if entry.matchWholeWords is not None else settings.whole_words
    everything = '\n'.join(s.text for s in sources)

    def found(key: str, text: str) -> bool:
        return key_matches(key, text, case_sensitive=case, whole_words=whole)

    keys = [k for k in (fill(k, char=char, user=user, pronouns=pronouns).strip() for k in entry.key) if k]
    primary = next((k for k in keys if found(k, everything)), None)
    if primary is None:
        return None, ''

    secondary = [k for k in (fill(k, char=char, user=user, pronouns=pronouns).strip() for k in entry.keysecondary)
                 if k]
    if entry.selective and secondary:
        hits = [found(k, everything) for k in secondary]
        logic = entry.selectiveLogic
        ok = (any(hits) if logic == AND_ANY else all(hits) if logic == AND_ALL
              else not any(hits) if logic == NOT_ANY else not all(hits) if logic == NOT_ALL else False)
        if not ok:
            return None, ''

    where = next((s for s in sources if found(primary, s.text)), None)
    return where, f'keyword "{primary}" in {where.label if where else "the scanned text"}'


SHORT_KEY = 3  # keywords this short (in letters) are always whole words: inside words, "K" is in "know"

# Scripts written without spaces between words (Thai, Lao, Myanmar, Khmer, Japanese, Chinese, and Korean,
# whose words take their endings without a space): there, a keyword is found anywhere.
_NO_SPACES = re.compile('[฀-໿က-႟ᄀ-ᇿក-៿぀-ヿ㄰-㆏'
                        'ㇰ-ㇿ㐀-䶿一-鿿가-힯豈-﫿\U00020000-\U0003134f]')


def key_matches(key: str, text: str, *, case_sensitive: bool = False, whole_words: bool = True) -> bool:
    """Whether a keyword appears in the text.

    - /pattern/flags is a regular expression (flags i, m, s apply; case and whole-words settings don't).
    - With whole words, the keyword must not be part of a longer word, but it can take a plural ending
      (s, es; x after a u, for French: "château", "châteaux"). A possessive is fine as it is ("K's":
      the apostrophe ends the word). Written with a * at the end, it can take any ending ("duel*",
      "dueling"). A keyword of several words is matched as a whole too, with any spaces between them.
    - Without, it's found anywhere, even inside words, as SillyTavern does ("art" in "start"); except
      a keyword of SHORT_KEY letters or fewer, which is a whole word anyway.
    - In scripts written without spaces (_NO_SPACES), it's found anywhere: words have no edges to go by.
    """
    if _REGEX_KEY.match(key):
        regex = _regex(key)
        return regex is not None and regex.search(text) is not None  # an invalid pattern matches nothing
    pattern = _word_pattern(key, case_sensitive, whole_words)
    if pattern is not None:
        return pattern.search(text) is not None
    key = key.removesuffix('*')
    if not key:
        return False
    if not case_sensitive:
        key, text = key.lower(), text.lower()
    return key in text


@functools.lru_cache(maxsize=4096)
def _word_pattern(key: str, case_sensitive: bool, whole_words: bool) -> re.Pattern | None:
    """The regular expression that finds the keyword as a word (see key_matches), or None to look for it
    anywhere."""
    any_ending = key.endswith('*')
    key = key.removesuffix('*').strip()
    if not key or _NO_SPACES.search(key) or not (whole_words or len(key) <= SHORT_KEY):
        return None
    words = r'\s+'.join(re.escape(w) for w in key.split())
    start = r'(?<!\w)' if re.match(r'\w', key) else ''  # (a keyword starting or ending with "#", "-" or "."
    if not re.search(r'\w$', key):                       # has its own edge there)
        end = ''
    elif any_ending:
        end = r'\w*'
    else:
        end = r'(?i:es|s|x)?(?!\w)' if key[-1] in 'uU' else r'(?i:es|s)?(?!\w)'
    return re.compile(start + words + end, 0 if case_sensitive else re.IGNORECASE)


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
