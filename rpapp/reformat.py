"""Reformatting: a character's first messages and example dialogues rewritten by an AI model to follow your
message format (Settings › Message format), changing only the formatting marks, never the words.

Characters come written in all sorts of styles, and the AI copies their first message and examples more
closely than it follows instructions: a greeting in another format can outweigh the rules in your prompt.
Reformatting them once, in the character, settles it.

How
---
- Each text is sent on its own (they're small jobs: a cheap model does them well) to the reformat connection,
  or the chat's (format_connection()), with INSTRUCTIONS around your rules (DEFAULT_RULES until you change them).
  The instructions are fixed: they're what makes the answer checkable. A long text goes in parts, cut at blank
  lines (parts()): all of them must come back right.
- Thinking is turned off, on OpenRouter: it takes time and, worse, the room of the answer (a model can think
  through a long text line by line, and have none left to write it). Models that must think refuse that: they're
  asked to think as little as they can, and given room for it (max_tokens()).
- The answer is checked (check()): with the formatting marks taken out, its words must be the text's, in the
  same order (capitals aside), and its placeholders ({{char}}, {{user}}...) and <START> lines the same.
  Otherwise it isn't used, and the review says what changed: cheap models like to "improve" the prose,
  shorten it, or tone it down.
- With the default rules, a text that already follows them (follows()) isn't sent: there's nothing to do.
- Nothing changes until you apply it, in the review (ui/reformat_view.py); what the texts were is kept in the
  character, to undo it (Character.reformatted).

{{format}} in a prompt's instructions stands for your rules (macros.py), so they're written in one place.
"""
from __future__ import annotations

import asyncio
import difflib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .connections import GenerationParams, Message, ModelError, OpenRouterConnection
from .tokens import estimate

if TYPE_CHECKING:
    from .characters import Character
    from .connections import Connection
    from .settings import Settings
    from .storage import JsonStore

DEFAULT_RULES = '''\
- *narration and actions in asterisks*
- "dialogue and vocalizations in quotation marks"
- `internal thoughts in backticks`
- 'text messages, notes, and world text in single quotes'
- **bold** only for sound effects
- each span closes before the next opens; never nested, never across paragraphs
- thought inside narration → close the *, write the `thought`, reopen the *; no * touching a backtick'''

INSTRUCTIONS = '''\
You reformat roleplay texts. Rewrite the text you're given so that it follows these formatting rules:

{rules}

Change only the formatting marks: add, remove, move or replace them, and close and reopen a span where the rules \
say so. Never add, remove, reorder or reword anything else: every word stays exactly as it is, in the same order, \
with its punctuation. Keep the line breaks and blank lines.
Keep these exactly as they are, never replace or translate them: {{char}}, {{user}} and the other {{...}} \
placeholders, <START> lines, and the speaker labels at the start of lines ("{{user}}:", "{{char}}:").
If the text already follows the rules, give it back unchanged.
Answer with the reformatted text only: no comments, no code block, no quotes around it.'''

KINDS = {
    'first_messages': 'The text is a character\'s first message in a roleplay chat.',
    'example_dialogues': 'The text is an example dialogue: lines of "{{user}}: ..." and "{{char}}: ...", showing '
                         'how the character speaks and acts.',
}
Kind = Literal['first_messages', 'example_dialogues']

TEMPERATURE = 0.2   # low: the same marks, consistently
AT_ONCE = 4         # texts sent at the same time
PART_TOKENS = 500   # a long text goes in parts of about this many tokens: a cheap model does them better

Status = Literal['same', 'changed', 'refused', 'failed']


@dataclass
class Outcome:
    """What became of one text."""
    status: Status   # 'same': it follows the format already; 'changed': the answer, checked; 'refused': the
    #                  answer changed more than the marks (not used); 'failed': no answer
    text: str = ''   # the answer ('changed': the text to use)
    note: str = ''   # 'refused', 'failed': why


# --- the rules ------------------------------------------------------------------------------------------

def rules_of(settings: Settings) -> str:
    """Your rules (Settings › Message format): the default ones until you change them."""
    return settings.format.rules.strip() or DEFAULT_RULES


def default_rules(settings: Settings) -> bool:
    """Whether your rules are the default ones: follows() can only tell for those."""
    return rules_of(settings) == DEFAULT_RULES.strip()


def instructions(rules: str, kind: Kind) -> str:
    return INSTRUCTIONS.replace('{rules}', rules.strip()) + '\n\n' + KINDS[kind]


# --- the structure of the default rules ---------------------------------------------------------------------

# A span of the default rules, on one line, with nothing of another span inside: **sound**, *narration*,
# "dialogue" (or “curly”), `thought`, 'written text' (or ‘curly’)
_SPAN = re.compile(r'''\*\*[^*\n]+?\*\*|\*[^*\n`"“”]+?\*|"[^"\n*`]*"|“[^”\n*`]*”|`[^`\n*"“”]+`'''
                   r'''|'[^'\n*`"“”]*'|‘[^’\n*`"“”]*’''')
_SPEAKER = re.compile(r'^\s*(?:\{\{(?:user|char)\}\}|<USER>|<BOT>|[^\s:*"“`\'‘][^:*"“`\'‘\n]{0,40}):\s*',
                      re.IGNORECASE)  # "{{user}}: ..." in example dialogues


def follows(text: str) -> bool:
    """Whether a text follows the default rules: everything in a span, none inside another, none across lines,
    no * touching a backtick. When unsure, no (it's then sent: the model gives it back unchanged)."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.upper() == '<START>':
            continue
        line = _SPEAKER.sub('', line, count=1)
        if _SPAN.sub(' ', line).strip() or re.search(r'\*`|`\*', line):
            return False
    return True


def to_reformat(texts: list[str], settings: Settings) -> list[int]:
    """Which of these texts need reformatting (by index): with the default rules, those that don't follow them;
    with yours, all of them (only the model can tell). Empty ones never."""
    check = default_rules(settings)
    return [i for i, text in enumerate(texts) if text.strip() and not (check and follows(text))]


# --- checking an answer -------------------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r'\{\{[^{}\n]*\}\}|<START>|<BOT>|<USER>', re.IGNORECASE)
_WORD = re.compile(r"\w+(?:['’]\w+)*")


def _words(text: str) -> list[str]:
    return [w.lower().replace('’', "'") for w in _WORD.findall(_PLACEHOLDER.sub(' ', text))]


def check(before: str, after: str) -> str:
    """'' if `after` changes only `before`'s formatting marks (and punctuation, and capitals); else what else
    changed, to say why it isn't used."""
    if [p.lower() for p in _PLACEHOLDER.findall(before)] != [p.lower() for p in _PLACEHOLDER.findall(after)]:
        return 'it changed the placeholders ({{char}}, {{user}}...) or the <START> lines'
    old, new = _words(before), _words(after)
    if old == new:
        return ''
    changes = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if op == 'equal':
            continue
        gone, came = _quoted(old[i1:i2]), _quoted(new[j1:j2])
        changes.append(f'{gone} became {came}' if op == 'replace' else f'{gone} was left out' if op == 'delete'
                       else f'{came} was added')
    shown = '; '.join(changes[:2]) + ('; and more' if len(changes) > 2 else '')
    return f'it changed the words: {shown}'


def _quoted(words: list[str], most: int = 6) -> str:
    return '"' + ' '.join(words[:most]) + (' …' if len(words) > most else '') + '"'


def _unwrapped(answer: str, before: str) -> str:
    """The answer without what models add around it sometimes: a code block, or a line like "Here it is:"."""
    text = answer.strip()
    fence = re.fullmatch(r'```[^\n]*\n(.*)\n```', text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    first, _, rest = text.partition('\n')
    if rest and first.rstrip().endswith(':') and check(before, text) and not check(before, rest.strip()):
        text = rest.strip()
    return text


# --- the calls ----------------------------------------------------------------------------------------------

def format_connection(settings: Settings, character: Character | None,
                      connections: JsonStore[Connection]) -> tuple[Connection | None, str]:
    """The connection to reformat with (Settings › Message format's, or the character's chats'), or why there's
    none."""
    from .chat_setup import chat_setup
    ref = settings.format.connection
    if ref is not None:
        found = connections.get(ref.id)
        return (found, '') if found is not None else \
            (None, f'The reformat connection "{ref.name}" is missing: choose another one in Settings › Message format.')
    ref = chat_setup(settings, character).connection
    if ref is None:
        return None, 'No connection to reformat with: choose one in Settings › Message format, or a chat connection.'
    found = connections.get(ref.id)
    return (found, '') if found is not None else \
        (None, f'The chat connection "{ref.name}" is missing: choose another one in Settings.')


async def reformat(text: str, kind: Kind, *, connection: Connection, rules: str) -> Outcome:
    """One text, reformatted by the model and checked: in parts, if it's long (parts()), each checked, all of
    them needed."""
    pieces = parts(text)
    limit = asyncio.Semaphore(AT_ONCE)

    async def one(piece: str) -> Outcome:
        async with limit:
            return await _reformat_part(piece, kind, connection=connection, rules=rules)

    outcomes = await asyncio.gather(*(one(piece) for piece, _ in pieces))
    for n, outcome in enumerate(outcomes, 1):
        if outcome.status in ('failed', 'refused'):
            where = f'in part {n} of {len(pieces)}, ' if len(pieces) > 1 else ''
            return Outcome(outcome.status, text=outcome.text, note=where + outcome.note)
    answer = ''.join(o.text + gap for o, (_, gap) in zip(outcomes, pieces))
    if all(o.status == 'same' for o in outcomes):
        return Outcome('same', text=text)
    return Outcome('changed', text=answer)


async def _reformat_part(text: str, kind: Kind, *, connection: Connection, rules: str) -> Outcome:
    connection = connection.model_copy(deep=True)
    connection.params.stop = []  # (stop sequences are for chat replies)
    params = GenerationParams(temperature=TEMPERATURE, max_tokens=max_tokens(text))
    messages = [Message(role='system', content=instructions(rules, kind)), Message(role='user', content=text)]
    # Thinking only takes time, and the room of the answer (max_tokens counts both): off. Models that must think
    # refuse that: then as little as they can, then as they like.
    efforts = ['none', 'minimal', None] if isinstance(connection, OpenRouterConnection) else [None]
    for effort in efforts:
        if isinstance(connection, OpenRouterConnection):
            connection.reasoning_effort = effort
        try:
            reply = await connection.generate(messages, params)
            break
        except ModelError as e:
            if e.status != 400 or effort == efforts[-1]:
                return Outcome('failed', note=str(e))
    answer = _unwrapped(reply.text, text)
    if not answer.strip():
        return Outcome('failed', note='the model spent all its room thinking, and wrote no answer: try one that '
                                      'thinks less' if reply.finish_reason == 'length' else
                                      'the model gave an empty answer')
    if problem := check(text, answer):
        return Outcome('refused', text=answer, note=problem)
    return Outcome('same' if _lines(answer) == _lines(text) else 'changed', text=answer.strip())


def _lines(text: str) -> list[str]:
    """The text's lines, without the spaces at their ends (which don't make it another text)."""
    return [line.rstrip() for line in text.strip().splitlines()]


def max_tokens(text: str) -> int:
    """Room for the answer (the text, with its marks), and for thinking, for models that can't do without."""
    return estimate(text) * 4 + 1024


_GAP = re.compile(r'\n[ \t]*\n\s*')  # a blank line (or more) between paragraphs


def parts(text: str, size: int = PART_TOKENS) -> list[tuple[str, str]]:
    """A long text in parts of about `size` tokens at most (a paragraph longer than that: a part of its own),
    cut at blank lines; each with what came after it (the blank lines, '' after the last): put back together, it's
    the text."""
    pieces = _GAP.split(text.strip())
    gaps = _GAP.findall(text.strip()) + ['']
    out: list[tuple[str, str]] = []
    current, current_gap = '', ''
    for piece, gap in zip(pieces, gaps):
        if current and estimate(current + current_gap + piece) > size:
            out.append((current, current_gap))
            current = piece
        else:
            current = current + current_gap + piece if current else piece
        current_gap = gap
    out.append((current, ''))
    return out
