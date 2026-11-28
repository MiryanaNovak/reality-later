"""The dialog showing the prompt sent after one of your messages (read-only).

- a pie chart of what the prompt is made of, in tokens, its key under it in the order the parts are sent
  (a click on either opens that part's section);
- what it was built for, its size against the space there is, and what was left out;
- a folding section for each kind of part (SHARES), in the order they're sent, with their text and how many
  tokens they take: for lore and memories, the entries that triggered, why, and where they went (or why they
  didn't); then the prompt as sent (same-role parts joined into messages).

A section is drawn when it's first opened (a long chat's messages: only if you look at them). Those you leave
open are open for the next prompt you look at too (opened.py).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from html import escape

from nicegui import ui

from ..conversations import ChatMessage
from ..prompts import MARGIN, Prompt, PromptPart, PromptReport
from ..tokens import MESSAGE_OVERHEAD, estimate
from .list_view import plural
from .opened import Opened
from .widgets import Dialog

ROLE_LABELS = {'system': 'Instructions (system)', 'user': 'You (user)', 'assistant': 'Character (assistant)'}
LORE_STATUS = {'over_budget': 'Left out: over the lore budget', 'skipped': 'Left out'}

# The kinds of parts a prompt is made of, by the order they come in (prompts.LAYOUT); each has its color in the
# chart (theme.SHARE_COLORS)
SHARES: dict[str, str] = {
    'instructions': 'Instructions',
    'modules': 'Instructions of modules',
    'character': 'Character',
    'persona': 'Persona',
    'examples': 'Example dialogues',
    'lore': 'Lore',
    'memories': 'Memories',
    'messages': 'Messages',
    'final': 'Final instructions',
    'final-modules': 'Final instructions of modules',
    'hidden': 'Hidden instruction',
    'other': 'Other parts',  # (the first saved prompts didn't say what their parts were)
}
BY_KIND = {'instructions': 'instructions', 'scenario': 'character', 'persona': 'persona', 'examples': 'examples',
           'lore': 'lore', 'memory': 'memories', 'message': 'messages', 'start': 'messages', 'final': 'final',
           'extend': 'hidden', 'continue': 'hidden'}
OF_MODULES = {'instructions': 'modules', 'final': 'final-modules'}


def share_of(part: PromptPart) -> str:
    """Which kind of part this is, in SHARES."""
    share = BY_KIND.get(part.kind) or ('messages' if not part.kind and part.role != 'system' else 'other')
    return OF_MODULES.get(share, share) if part.of_module else share


@dataclass
class Share:
    """The parts of one kind in a prompt."""
    key: str
    parts: list[tuple[int, PromptPart]] = field(default_factory=list)  # (where in the prompt, the part)

    @property
    def name(self) -> str:
        return SHARES[self.key]

    @property
    def tokens(self) -> int:
        return sum(part.tokens for _, part in self.parts)


def shares_of(prompt: Prompt) -> list[Share]:
    """The parts of the prompt by kind, in the order they're sent: by where each kind's first part is."""
    found: dict[str, Share] = {}
    for index, part in enumerate(prompt.parts):
        key = share_of(part)
        found.setdefault(key, Share(key)).parts.append((index, part))
    return list(found.values())


def percent(tokens: int, total: int) -> str:
    share = tokens * 100 / total if total else 0
    return '<1%' if 0 < share < 1 else f'{round(share)}%'


def show_prompt(prompt: Prompt | None, message: ChatMessage | None, *, time: Callable[[datetime], str],
                copy_js: Callable[[str], str], title: str = 'Prompt for this message') -> None:
    """`message` is the message the prompt follows (to say if it was edited since)."""
    with ui.context.client.layout, Dialog() as dialog, \
            ui.card().classes('dialog-card prompt-card').mark('prompt-dialog'):
        with ui.row().classes('w-full items-center no-wrap'):
            ui.label(title).classes('panel-title')
            ui.space()
            ui.button(icon='close', on_click=dialog.close).props('flat round dense aria-label="Close"')
        if prompt is None:
            ui.label('No prompt was saved for this message.').classes('muted')
        else:
            shares = shares_of(prompt)
            sections: dict[str, _Section] = {}  # (filled below the chart, which opens them)
            with ui.column().classes('w-full gap-3 prompt-parts'):
                _chart(shares, lambda key: sections[key].show() if key in sections else None)
                with ui.column().classes('w-full gap-1'):
                    _summary(prompt, message, time)
                _sections(prompt, shares, sections)
            sent = '\n\n'.join(f'[{m.role}]\n{m.content}' for m in prompt.messages())
            with ui.row().classes('w-full justify-end'):
                ui.button('Copy prompt', icon='content_copy', on_click=lambda: ui.notify('Copied')) \
                    .props('flat').on('click', js_handler=copy_js(sent))
    dialog.on_value_change(lambda e: _delete(dialog) if not e.value else None)
    dialog.open()


def _delete(dialog: ui.dialog) -> None:
    try:
        dialog.delete()
    except ValueError:  # the page is already gone
        pass


def _summary(prompt: Prompt, message: ChatMessage | None, time: Callable[[datetime], str]) -> None:
    sent_to = f'for {prompt.connection}' if prompt.connection else 'with no chat connection chosen, so not sent'
    ui.label(f'Built at {time(prompt.created_at)}, {sent_to}.').classes('text-sm muted')
    r = prompt.report
    if r is None:  # saved by the first version
        ui.label(f'About {prompt.tokens:,} tokens.').classes('text-sm muted')
    else:
        reply = f'{r.reply_tokens:,} for the reply' + ('' if r.reply_known else ' (the connection sets no maximum)')
        ui.label(f'About {r.tokens:,} tokens, of {r.available:,} available: a context size of '
                 f'{r.context_size:,}, minus {reply} and a {round(MARGIN * 100)}% margin.') \
            .classes('text-sm muted').mark('prompt-size')
        if r.extend == 'instruction':
            ui.label('Sent to extend the AI\'s last reply: with a hidden instruction at the end, which the chat '
                     'doesn\'t show.').classes('text-sm').mark('prompt-extend')
        elif r.extend == 'prefill':
            ui.label('Sent to extend the AI\'s last reply by prefill: the reply comes last, as the start of the '
                     'model\'s own message, and it wrote the rest.').classes('text-sm').mark('prompt-extend')
        if r.continue_scene:
            ui.label('Sent to continue the scene: with a hidden instruction at the end, in your place, which the '
                     'chat doesn\'t show.').classes('text-sm').mark('prompt-continue')
        if not r.context_known:
            ui.label(f'The connection doesn\'t give its context size, so {r.context_size:,} tokens were assumed. '
                     'Set it in the connection\'s Model section.').classes('text-sm')
        if r.missing_preset:
            ui.label(f'The prompt "{r.missing_preset}" chosen in Settings › Chat was deleted: no instructions were '
                     'sent. Choose another there.').classes('text-sm text-negative').mark('missing-preset')
        elif not r.preset and not any(part.kind in ('instructions', 'final') for part in prompt.parts):
            ui.label('No prompt is chosen in Settings › Chat: no instructions were sent.').classes('text-sm') \
                .mark('no-preset')
        if r.modules:
            ui.label(f'With the {"module" if len(r.modules) == 1 else "modules"} of the prompt on in this chat: '
                     + ', '.join(f'"{name}"' for name in r.modules) + '.').classes('text-sm').mark('prompt-modules')
        if r.messages_left_out:
            kept = f' The room for lore and memories, {r.reserved:,} tokens, counts in full, used or not, so this ' \
                   'changes only as the chat grows.' if r.reserved else ''
            ui.label(f'{plural(r.messages_left_out, "older message")} of {r.messages_total} left out to fit.{kept}') \
                .classes('text-sm').mark('left-out')
        if r.examples_sent < r.examples_total:
            ui.label((f'{r.examples_sent} of {r.examples_total} example dialogues sent: the others make'
                      if r.examples_sent else
                      f'The character\'s {plural(r.examples_total, "example dialogue")} left out: they make')
                     + ' room for the chat (Settings › Chat).').classes('text-sm').mark('examples-left-out')
        if r.over_limit:
            ui.label('Too long even with only the last message: the model may refuse it. Shorten the '
                     'instructions or descriptions, or lower the lore budget.').classes('text-sm text-negative')
        for name in r.missing_books:
            ui.label(f'Lore book "{name}" is missing: it was deleted, or its file was renamed.') \
                .classes('text-sm text-negative')
    if message and message.edited_at and message.edited_at > prompt.created_at:
        ui.label('You edited this message afterwards: this is the prompt as it was before.') \
            .classes('text-sm text-negative')


# --- the chart ------------------------------------------------------------------------------

def _chart(shares: list[Share], open_section: Callable[[str], None]) -> None:
    """A pie of the parts by kind, clockwise from the top in the order they're sent, and its key."""
    shown = [share for share in shares if share.tokens]
    total = sum(share.tokens for share in shown)
    with ui.column().classes('panel gap-2').mark('prompt-chart'):
        with ui.column().classes('gap-0'):
            ui.label(f'About {total:,} tokens').classes('panel-title')
            ui.label('By kind of part, in the order they\'re sent. Click one to see it.').classes('text-sm muted')
        if not shown:
            ui.label('The prompt is empty.').classes('text-sm muted')
            return
        ui.html(pie_svg(shown, total), sanitize=False).classes('w-full') \
            .on('click', lambda e: open_section(e.args),
                js_handler='(e) => { const key = e.target.dataset && e.target.dataset.share; if (key) emit(key); }')
        with ui.column().classes('prompt-legend gap-0').mark('prompt-legend'):
            for share in shown:
                with ui.element('div').classes('legend-row w-full').props('tabindex=0 role=button') \
                        .mark(f'legend-{share.key}') as row:
                    ui.element('span').classes('share-swatch').style(f'background: var(--share-{share.key})')
                    ui.label(share.name).classes('legend-name')
                    ui.label(f'{share.tokens:,}').classes('figure')
                    ui.label(percent(share.tokens, total)).classes('figure muted')
                for event in ('click', 'keydown.enter'):
                    row.on(event, lambda _, key=share.key: open_section(key))


def pie_svg(shares: list[Share], total: int) -> str:
    """The pie, as SVG: a slice per kind of part (none for those without tokens), each saying what it is when
    pointed at, and the whole described for screen readers."""
    center, radius = 100, 98
    slices, angle = [], 0.0
    for share in shares:
        tip = f'<title>{escape(share.name)}: {share.tokens:,} tokens, {percent(share.tokens, total)}</title>'
        look = f'class="slice" data-share="{share.key}" style="fill: var(--share-{share.key})"'
        if share.tokens >= total:  # (all of it: an arc can't go the whole way round)
            slices.append(f'<circle {look} cx="{center}" cy="{center}" r="{radius}">{tip}</circle>')
            continue
        sweep = 2 * math.pi * share.tokens / total
        x0, y0 = center + radius * math.sin(angle), center - radius * math.cos(angle)
        angle += sweep
        x1, y1 = center + radius * math.sin(angle), center - radius * math.cos(angle)
        path = (f'M{center},{center} L{x0:.2f},{y0:.2f} '
                f'A{radius},{radius} 0 {1 if sweep > math.pi else 0} 1 {x1:.2f},{y1:.2f} Z')
        slices.append(f'<path {look} d="{path}">{tip}</path>')
    described = '; '.join(f'{share.name} {percent(share.tokens, total)}' for share in shares)
    return (f'<svg class="prompt-pie" viewBox="0 0 200 200" role="img" '
            f'aria-label="{escape(f"What the prompt is made of: {described}")}">{"".join(slices)}</svg>')


# --- the sections -----------------------------------------------------------------------------

class _Section:
    """A folding section, drawn when first opened."""

    def __init__(self, key: str, title: str, caption: str, draw: Callable[[], None], *, opened: Opened,
                 colored: bool = True) -> None:
        self.key, self._draw, self._drawn, self._opened = key, draw, False, opened
        self.expansion = ui.expansion(value=opened.is_open(key)).classes('panel fold prompt-section') \
            .mark(f'section-{key}', *(['prompt-lore'] if key == 'lore' else ['prompt-memories'] if key == 'memories'
                                      else []))
        with self.expansion.add_slot('header'), ui.item_section().classes('min-w-0'):
            with ui.row().classes('items-center no-wrap gap-2 min-w-0'):
                if colored:
                    ui.element('span').classes('share-swatch').style(f'background: var(--share-{key})')
                ui.label(title).classes('fold-title')
            ui.label(caption).classes('fold-caption')
        self.expansion.on_value_change(self._changed)
        if self.expansion.value:
            self._fill()

    def show(self) -> None:
        """Open it, and bring it into view."""
        self.expansion.set_value(True)
        self._fill()
        ui.run_javascript(f'getHtmlElement({self.expansion.id})?.scrollIntoView({{block: "start", '
                          f'behavior: "smooth"}})')

    def _changed(self, e) -> None:
        self._opened.set(self.key, e.value)
        if e.value:
            self._fill()

    def _fill(self) -> None:
        if self._drawn:
            return
        self._drawn = True
        with self.expansion, ui.column().classes('w-full gap-3'):
            self._draw()


def _sections(prompt: Prompt, shares: list[Share], sections: dict[str, _Section]) -> None:
    r = prompt.report
    total = sum(share.tokens for share in shares)
    listed = list(shares)
    if r is not None:  # lore and memories have a section even with nothing in: to say why
        rank = list(SHARES)
        for key, shown in (('lore', True), ('memories', bool(r.memory_budget))):
            if shown and all(share.key != key for share in listed):
                later = [i for i, share in enumerate(listed) if rank.index(share.key) > rank.index(key)]
                listed.insert(later[0] if later else len(listed), Share(key))
    opened = Opened('rp_prompt_view')

    def amount(share: Share) -> str:
        return f'about {share.tokens:,} tokens, {percent(share.tokens, total)}'

    for share in listed:
        title, caption, draw = SHARES[share.key], amount(share).capitalize(), lambda s=share: _blocks(s)
        if share.key == 'instructions' and r is not None and r.preset:
            caption += f' · from "{r.preset}"'
        elif share.key in ('modules', 'final-modules'):
            caption += f' · {plural(len(share.parts), "module")}'
        elif share.key == 'character':
            draw = lambda s=share: _character(s)  # noqa: E731
        elif share.key == 'examples' and r is not None:
            caption += f' · {r.examples_sent} of {r.examples_total} sent'
            draw = lambda s=share: _examples(s, r)  # noqa: E731
        elif share.key == 'lore' and r is not None:
            used = [u for u in r.lore if u.status == 'used']
            caption = 'No lore books in this chat' if not r.books else \
                f'{plural(len(used), "entry")}, {amount(share)}' if used else 'No entry came in'
            draw = lambda s=share: _lore(r, s)  # noqa: E731
        elif share.key == 'memories' and r is not None:
            used = [u for u in r.memories if u.status == 'used']
            caption = f'{len(used)} used, {amount(share)}' if used else \
                'None came in' if r.memory_count else 'No memories yet'
            draw = lambda s=share: _memories(r, s)  # noqa: E731
        elif share.key == 'messages':
            count = sum(part.kind == 'message' for _, part in share.parts)
            caption = f'{plural(count, "message")}, {amount(share)}'
            if r is not None and r.messages_left_out:
                caption += f' · {r.messages_left_out} older left out'
            draw = lambda s=share: _messages(prompt, s)  # noqa: E731
        elif share.key == 'hidden':
            draw = lambda s=share: _hidden(s)  # noqa: E731
        sections[share.key] = _Section(share.key, title, caption, draw, opened=opened)

    sent = prompt.messages()
    _Section('sent', 'As sent', f'{plural(len(sent), "message")}: the parts of one role in a row make one',
             lambda: _sent(prompt, sent), opened=opened, colored=False)


def _blocks(share: Share) -> None:
    for index, part in share.parts:
        _part(index, part)


def _part(index: int, part: PromptPart) -> None:
    label = f'{part.source} · {part.role}' if part.source else ROLE_LABELS.get(part.role, part.role)
    _block(label, part.tokens, part.content, f'prompt-part-{index}')


def _character(share: Share) -> None:
    ui.label('What the AI is told about the character is their scenario. Their description is only for you: '
             'it\'s never sent.').classes('text-sm muted')
    _blocks(share)


def _examples(share: Share, r: PromptReport) -> None:
    for use in r.lore:  # (their text is in the lore's section)
        if use.status == 'used' and use.placement.endswith('the example dialogues'):
            ui.label(f'The lore entry "{use.entry}" goes {use.placement.split(" the ")[0]} them (see Lore).') \
                .classes('text-sm muted')
    _blocks(share)


def _texts(share: Share) -> dict[str, list[tuple[int, PromptPart]]]:
    """The parts by source, in order (two entries can have the same name)."""
    texts: dict[str, list[tuple[int, PromptPart]]] = {}
    for index, part in share.parts:
        texts.setdefault(part.source, []).append((index, part))
    return texts


def _entry(texts: dict[str, list[tuple[int, PromptPart]]], source: str, reason: str, tokens: int) -> None:
    """Why an entry came in, about how many tokens it takes in the prompt (as the chart counts them), and its
    text."""
    found = texts[source].pop(0) if texts.get(source) else None
    ui.label(f'{reason}, about {found[1].tokens if found else tokens:,} tokens.').classes('text-sm muted')
    if found:
        ui.label(found[1].content).classes('prompt-text').mark(f'prompt-part-{found[0]}')


def _lore(r: PromptReport, share: Share) -> None:
    texts = _texts(share)
    if r.books:
        ui.label(f'From {", ".join(r.books)}. Budget {r.lore_budget:,} tokens.').classes('text-sm muted')
    else:
        ui.label('Neither the character nor the persona has lore books.').classes('text-sm muted')
    if r.books and not r.lore:
        ui.label('No entry triggered: none of their keywords is in the last messages.').classes('text-sm muted')
    for use in r.lore:
        with ui.column().classes('w-full gap-1').mark('lore-use'):
            ui.label(f'{use.book} › {use.entry}').classes('text-sm text-weight-medium')
            if use.status == 'used':
                _entry(texts, f'Lore: {use.book} › {use.entry}', f'{_sentence(use.reason)}. Placed {use.placement}',
                       use.tokens)
            else:
                reason = LORE_STATUS[use.status] + (f': {use.note}' if use.note else '')
                ui.label(f'{_sentence(use.reason)}. {reason}.').classes('text-sm text-negative')
    for note in r.lore_notes:
        ui.label(note).classes('text-sm muted mt-1')
    _meaning_note(r)


def _memories(r: PromptReport, share: Share) -> None:
    texts = _texts(share)
    ui.label(f'{plural(r.memory_count, "memory")} in this chat. Budget {r.memory_budget:,} tokens.') \
        .classes('text-sm muted')
    if not r.memory_count:
        ui.label('No memories yet in this chat.').classes('text-sm muted')
    elif not r.memories:
        ui.label('None came into play: none of their keywords is in the last messages.').classes('text-sm muted')
    _meaning_note(r)
    for use in r.memories:
        with ui.column().classes('w-full gap-1').mark('memory-use'):
            ui.label(use.entry).classes('text-sm text-weight-medium')
            if use.status == 'used':
                _entry(texts, f'Memory: {use.entry}', _sentence(use.reason), use.tokens)
            else:
                reason = LORE_STATUS[use.status].replace('lore budget', 'memory budget')
                ui.label(f'{_sentence(use.reason)}. {reason}.').classes('text-sm text-negative')


def _messages(prompt: Prompt, share: Share) -> None:
    """The chat, with where lore entries placed among the messages go (their text: in the lore's section)."""
    first = share.parts[0][0] if share.parts else len(prompt.parts)
    mine = {index for index, _ in share.parts}
    for index, part in enumerate(prompt.parts):
        if index in mine:
            _part(index, part)
        elif index > first and part.kind == 'lore':
            ui.label(f'Here: the lore entry "{part.source.split(" › ")[-1]}", as {part.role} (see Lore).') \
                .classes('text-sm muted').mark('lore-here')


def _hidden(share: Share) -> None:
    ui.label('Sent last, as you. The chat doesn\'t show it.').classes('text-sm muted')
    _blocks(share)


def _sent(prompt: Prompt, sent) -> None:
    if prompt.system_in_chat == 'user':
        ui.label('Instructions inside the chat are sent as user messages, as the connection asks.') \
            .classes('text-sm muted')
    for index, m in enumerate(sent):
        _block(m.role, estimate(m.content) + MESSAGE_OVERHEAD, m.content, f'prompt-message-{index}')


def _sentence(text: str) -> str:
    """Capital first letter, the rest untouched (str.capitalize would lower names in quotes)."""
    return text[:1].upper() + text[1:]


def _block(label: str, tokens: int, text: str, marker: str) -> None:
    with ui.column().classes('w-full gap-1').mark(marker):
        with ui.row().classes('w-full items-baseline no-wrap gap-2'):
            ui.label(label).classes('prompt-role')
            ui.label(f'{tokens:,} tokens').classes('text-xs muted whitespace-nowrap')
        ui.label(text).classes('prompt-text')


def _meaning_note(r: PromptReport) -> None:
    """Vector search was on, but couldn't be used: why (in both sections: it's about both)."""
    if r.meaning_note:
        ui.label(r.meaning_note).classes('text-sm text-warning mt-1').mark('meaning-note')
