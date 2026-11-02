"""The dialog showing the details of an AI reply: who wrote it, what it cost, how it ended.

- the model that actually answered and, on OpenRouter, the company that ran it
  (OpenRouter can route to another provider, or another model with openrouter/auto);
- how long it took, and whether it finished or was cut off at the max reply length;
- the real token counts against the app's estimate, how much of the prompt came
  from the provider's cache (cheaper), and the cost;
- the model's thinking, for models that show it;
- a link to the prompt it was written from;
- for an extension (a version that's another one, extended): which, and how it was asked for;
- for a message written to continue the scene: that it was.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from nicegui import ui

from ..conversations import ChatMessage
from ..replies import ReplyDetails
from .widgets import folding_panel

FINISH = {
    'stop': 'It finished on its own.',
    'content_filter': 'The provider\'s content filter cut it short.',
    'tool_calls': 'It ended asking to use a tool, which the app doesn\'t do.',
    'error': 'It ended with an error at the provider.',
}


def show_reply_details(details: ReplyDetails | None, message: ChatMessage, *, time: Callable[[datetime], str],
                       open_prompt: Callable[[], None] | None, version: str = '') -> None:
    """`open_prompt`: shows the prompt the reply was written from (None if it's gone).
    `version`: which version of the reply this is ('Version 2 of 3'), if there are several."""
    with ui.context.client.layout, ui.dialog() as dialog, \
            ui.card().classes('dialog-card prompt-card').mark('reply-dialog'):
        with ui.row().classes('w-full items-center no-wrap'):
            ui.label('About this reply').classes('panel-title')
            if version:
                ui.label(version).classes('text-sm muted').mark('reply-version')
            ui.space()
            ui.button(icon='close', on_click=dialog.close).props('flat round dense aria-label="Close"')
        if details is None:
            ui.label('There are no details for this reply.').classes('muted')
        else:
            with ui.column().classes('w-full gap-3 prompt-parts'):
                _facts(details, message, time)
                if details.reasoning:
                    with folding_panel('The model\'s thinking', f'{len(details.reasoning):,} characters'):
                        ui.label(details.reasoning).classes('prompt-text').mark('reply-thinking')
            if open_prompt:
                def see_prompt() -> None:
                    dialog.close()
                    open_prompt()
                with ui.row().classes('w-full justify-end'):
                    ui.button('See the prompt', icon='article', on_click=see_prompt).props('flat') \
                        .mark('reply-prompt')
    dialog.on_value_change(lambda e: _delete(dialog) if not e.value else None)
    dialog.open()


def _delete(dialog: ui.dialog) -> None:
    try:
        dialog.delete()
    except ValueError:  # the page is already gone
        pass


def _facts(d: ReplyDetails, message: ChatMessage, time: Callable[[datetime], str]) -> None:
    who = d.model or 'the model'
    if d.provider:
        who += f', run by {d.provider}'
    via = f', through the connection {d.connection}' if d.connection else ''
    if d.continues:
        ui.label('Written to continue the scene, with a hidden instruction in your place.').classes('text-sm') \
            .mark('reply-continues')
    if d.extends is not None:
        how = 'by prefill: the model wrote the rest of its own message' if d.extend_method == 'prefill' else \
            'with a hidden instruction'
        ui.label(f'Version {d.extends + 1}, extended {how}. What follows is about the extension alone.') \
            .classes('text-sm').mark('reply-extends')
    ui.label(f'Written by {who}{via}, at {time(d.created_at)}, in {_seconds(d.seconds)}.').classes('text-sm')

    if d.finish_reason == 'length':
        limit = f' ({d.max_tokens:,} tokens)' if d.max_tokens else ''
        ui.label(f'It was cut off: it reached the max reply length{limit}. Raise it in the connection, '
                 'or ask the character to go on.').classes('text-sm text-negative').mark('reply-cut-off')
    elif d.finish_reason:
        ui.label(FINISH.get(d.finish_reason, f'It ended with "{d.finish_reason}".')).classes('text-sm')
    if message.edited_at and message.edited_at > d.created_at:
        ui.label('You edited this reply afterwards.').classes('text-sm muted')

    u = d.usage
    rows = []
    if u.prompt_tokens is not None:
        prompt = f'{u.prompt_tokens:,} tokens'
        if d.prompt_estimate:
            prompt += f' (estimated {d.prompt_estimate:,})'
        if u.cached_tokens:
            prompt += f', {u.cached_tokens:,} of them from the cache'
        rows.append(('Prompt', prompt))
    if u.completion_tokens is not None:
        reply = f'{u.completion_tokens:,} tokens'
        if u.reasoning_tokens:
            reply += f', {u.reasoning_tokens:,} of them thinking'
        rows.append(('Reply', reply))
    if u.cost is not None:
        rows.append(('Cost', _money(u.cost)))
    if rows:
        with ui.element('div').classes('reply-facts').mark('reply-usage'):
            for label, value in rows:
                ui.label(label).classes('muted')
                ui.label(value)
    else:
        ui.label('The server didn\'t report token counts.').classes('text-sm muted')
    if d.request_id:
        ui.label(f'Request id: {d.request_id}').classes('text-xs muted select-all break-all')


def _seconds(seconds: float) -> str:
    if seconds < 60:
        return f'{seconds:.1f} s'
    return f'{int(seconds // 60)} min {round(seconds % 60):02d} s'


def _money(cost: float) -> str:
    """'$0.00042', '$0.013', '$1.25'; '$0 (free)'."""
    if cost == 0:
        return '$0 (free)'
    if cost >= 0.1:
        return f'${cost:,.2f}'
    return '$' + f'{cost:.6f}'.rstrip('0')
