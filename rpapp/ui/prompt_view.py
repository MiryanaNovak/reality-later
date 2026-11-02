"""The dialog showing the prompt sent after one of your messages (read-only).

- what it was built for, its size against the space there is, and what was left out;
- the lore entries that triggered, why, and where they went (or why they didn't);
- the prompt itself, part by part with where each part comes from, or as sent
  (same-role parts joined into messages).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from nicegui import ui

from ..conversations import ChatMessage
from ..prompts import MARGIN, Prompt, PromptReport
from ..tokens import MESSAGE_OVERHEAD, estimate
from .list_view import plural
from .widgets import folding_panel

ROLE_LABELS = {'system': 'Instructions (system)', 'user': 'You (user)', 'assistant': 'Character (assistant)'}
LORE_STATUS = {'over_budget': 'Left out: over the lore budget', 'skipped': 'Left out'}


def show_prompt(prompt: Prompt | None, message: ChatMessage | None, *, time: Callable[[datetime], str],
                copy_js: Callable[[str], str], title: str = 'Prompt for this message') -> None:
    """`message` is the message the prompt follows (to say if it was edited since)."""
    with ui.context.client.layout, ui.dialog() as dialog, \
            ui.card().classes('dialog-card prompt-card').mark('prompt-dialog'):
        with ui.row().classes('w-full items-center no-wrap'):
            ui.label(title).classes('panel-title')
            ui.space()
            ui.button(icon='close', on_click=dialog.close).props('flat round dense aria-label="Close"')
        if prompt is None:
            ui.label('No prompt was saved for this message.').classes('muted')
        else:
            with ui.column().classes('w-full gap-3 prompt-parts'):
                _summary(prompt, message, time)
                if prompt.report:
                    _lore(prompt.report)
                    if prompt.report.memory_budget:
                        _memories(prompt.report)
                _parts(prompt)
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
        if r.messages_left_out:
            ui.label(f'{plural(r.messages_left_out, "older message")} of {r.messages_total} left out to fit.') \
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


def _lore(r: PromptReport) -> None:
    used = [u for u in r.lore if u.status == 'used']
    if not r.books:
        title, caption = 'Lore', 'No lore books in this chat'
    else:
        title = f'Lore: {plural(len(used), "entry")}' + (f', about {r.lore_tokens:,} tokens' if used else '')
        caption = f'From {", ".join(r.books)}. Budget {r.lore_budget:,} tokens.'
    with ui.column().classes('w-full').mark('prompt-lore'), folding_panel(title, caption, opened=bool(r.lore)):
        if not r.books:
            ui.label('Neither the character nor the persona has lore books.').classes('text-sm muted')
        elif not r.lore:
            ui.label('No entry triggered: none of their keywords is in the last messages.').classes('text-sm muted')
        for use in r.lore:
            with ui.column().classes('w-full gap-0').mark('lore-use'):
                ui.label(f'{use.book} › {use.entry}').classes('text-sm text-weight-medium')
                if use.status == 'used':
                    ui.label(f'{_sentence(use.reason)}. Placed {use.placement}, about {use.tokens:,} tokens.') \
                        .classes('text-sm muted')
                else:
                    reason = LORE_STATUS[use.status] + (f': {use.note}' if use.note else '')
                    ui.label(f'{_sentence(use.reason)}. {reason}.').classes('text-sm text-negative')
        for note in r.lore_notes:
            ui.label(note).classes('text-sm muted mt-1')


def _memories(r: PromptReport) -> None:
    used = [u for u in r.memories if u.status == 'used']
    title = f'Memories: {len(used)} used' + (f', about {r.memory_tokens:,} tokens' if used else '')
    caption = f'{plural(r.memory_count, "memory")} in this chat. Budget {r.memory_budget:,} tokens.'
    with ui.column().classes('w-full').mark('prompt-memories'), folding_panel(title, caption, opened=bool(used)):
        if not r.memory_count:
            ui.label('No memories yet in this chat.').classes('text-sm muted')
        elif not r.memories:
            ui.label('None came into play: none of their keywords is in the last messages.').classes('text-sm muted')
        for use in r.memories:
            with ui.column().classes('w-full gap-0').mark('memory-use'):
                ui.label(use.entry).classes('text-sm text-weight-medium')
                if use.status == 'used':
                    ui.label(f'{_sentence(use.reason)}, about {use.tokens:,} tokens.').classes('text-sm muted')
                else:
                    reason = LORE_STATUS[use.status].replace('lore budget', 'memory budget')
                    ui.label(f'{_sentence(use.reason)}. {reason}.').classes('text-sm text-negative')


def _sentence(text: str) -> str:
    """Capital first letter, the rest untouched (str.capitalize would lower names in quotes)."""
    return text[:1].upper() + text[1:]


def _parts(prompt: Prompt) -> None:
    @ui.refreshable
    def body(view: str) -> None:
        if view == 'parts':
            for i, part in enumerate(prompt.parts):
                label = f'{part.source} · {part.role}' if part.source else ROLE_LABELS.get(part.role, part.role)
                _block(label, part.tokens, part.content, f'prompt-part-{i}')
        else:
            for i, m in enumerate(prompt.messages()):
                _block(m.role, estimate(m.content) + MESSAGE_OVERHEAD, m.content, f'prompt-message-{i}')

    with ui.row().classes('w-full items-center no-wrap mt-1'):
        ui.label('The prompt').classes('panel-title')
        ui.space()
        ui.toggle({'parts': 'Parts', 'sent': 'As sent'}, value='parts', on_change=lambda e: body.refresh(e.value)) \
            .props('dense no-caps unelevated toggle-color=primary').mark('prompt-view')
    if prompt.system_in_chat == 'user':
        ui.label('Instructions inside the chat are sent as user messages, as the connection asks.') \
            .classes('text-sm muted')
    with ui.column().classes('w-full gap-3'):
        body('parts')


def _block(label: str, tokens: int, text: str, marker: str) -> None:
    with ui.column().classes('w-full gap-1').mark(marker):
        with ui.row().classes('w-full items-baseline no-wrap gap-2'):
            ui.label(label).classes('prompt-role')
            ui.label(f'{tokens:,} tokens').classes('text-xs muted')
        ui.label(text).classes('prompt-text')
