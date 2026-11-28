"""The review of a reformatting (reformat.py): a character's first messages and/or example dialogues sent to the
model, each answer shown before → after as it comes, and those you keep applied together.

    review(character, ['first_messages'], choices=..., on_apply=redraw)

Only the texts that need it are sent (reformat.to_reformat); the others say they follow your format already. An
answer that changed more than the marks isn't offered (it says what changed, and shows it folded away). Applying
keeps what the texts were in the character (Character.reformatted), to undo it: undo().
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from nicegui import background_tasks, ui

from ..characters import Character, Reformatted
from ..reformat import AT_ONCE, Kind, Outcome, format_connection, reformat, rules_of, to_reformat
from .chat_settings import ChatChoices
from .list_view import plural
from .widgets import Dialog

ITEM = {'first_messages': 'First message', 'example_dialogues': 'Example dialogue'}
WHAT = {'first_messages': 'first messages', 'example_dialogues': 'example dialogues'}


@dataclass
class _Text:
    kind: Kind
    index: int
    before: str
    outcome: Outcome | None = None  # (None: on its way)
    keep: bool = True               # applied, if it changed
    view: Callable[[], None] = field(default=lambda: None)     # (redraws it)
    on_keep: Callable[[], None] = field(default=lambda: None)  # (its Apply box changed)

    @property
    def label(self) -> str:
        return f'{ITEM[self.kind]} {self.index + 1}'


def review(character: Character, kinds: Sequence[Kind], *, choices: ChatChoices,
           on_apply: Callable[[list[Kind]], None]) -> None:
    """Open the review. `on_apply(kinds)`: once texts of these kinds were changed in `character` (to save it and
    redraw them)."""
    settings = choices.settings()
    connection, problem = format_connection(settings, character, choices.connections)
    rules = rules_of(settings)
    texts: list[_Text] = []
    for kind in kinds:
        values = getattr(character, kind)
        sent = set(to_reformat(values, settings))
        texts += [_Text(kind, i, text, outcome=None if i in sent else Outcome('same'))
                  for i, text in enumerate(values) if text.strip()]
    todo = [t for t in texts if t.outcome is None]
    tasks: list[asyncio.Task] = []
    title = 'Reformat ' + ' and '.join(WHAT[k] for k in kinds)

    with ui.context.client.layout, Dialog() as dialog, \
            ui.card().classes('dialog-card prompt-card').mark('reformat-dialog'):
        with ui.row().classes('w-full items-center no-wrap'):
            ui.label(title).classes('panel-title')
            ui.space()
            ui.button(icon='close', on_click=dialog.close).props('flat round dense aria-label="Close"')
        with ui.column().classes('w-full gap-3 prompt-parts'):
            if not texts:
                ui.label(f'There are no {" or ".join(WHAT[k] for k in kinds)} to reformat.').classes('muted')
            elif not todo:
                ui.label('They all follow your message format already.').classes('muted').mark('reformat-none')
            elif problem:
                ui.label(problem).classes('text-negative').mark('reformat-problem')
            else:
                ui.label('Only the formatting marks are meant to change: each answer is checked, and offered only if '
                         'its words are the text\'s. Choose which to apply.').classes('text-sm muted')
                for text in texts:
                    _text_view(text)
        with ui.row().classes('w-full items-center justify-end gap-2'):
            progress = ui.label('').classes('text-sm muted mr-auto').mark('reformat-progress')
            ui.button('Cancel', on_click=dialog.close).props('flat')
            apply_button = ui.button('Apply', on_click=lambda: apply()).mark('reformat-apply')

    def update() -> None:
        done = sum(t.outcome is not None for t in todo)
        chosen = [t for t in texts if t.outcome and t.outcome.status == 'changed' and t.keep]
        progress.set_text(f'Reformatting: {done} of {len(todo)} done…' if done < len(todo) and not problem else '')
        apply_button.set_text(f'Apply {len(chosen)}' if chosen else 'Apply')
        apply_button.set_enabled(bool(chosen) and done == len(todo))

    def apply() -> None:
        changed: list[Kind] = []
        undo_list = [r for r in character.reformatted if r.field not in kinds]  # (another list's: kept)
        for t in texts:
            if not (t.outcome and t.outcome.status == 'changed' and t.keep):
                continue
            values = list(getattr(character, t.kind))
            if t.index >= len(values) or values[t.index] != t.before:  # (changed meanwhile: left alone)
                continue
            values[t.index] = t.outcome.text
            setattr(character, t.kind, values)
            undo_list.append(Reformatted(field=t.kind, index=t.index, before=t.before, after=t.outcome.text))
            if t.kind not in changed:
                changed.append(t.kind)
        character.reformatted = undo_list
        character.reformat_offer = False
        if changed:
            on_apply(changed)
            count = sum(r.field in changed for r in undo_list)
            ui.notify(f'{plural(count, "text")} reformatted. Undo is in their section.', type='positive')
        dialog.close()  # (last: closed, it's deleted, and with it what the code here runs in)

    async def one(text: _Text, limit: asyncio.Semaphore) -> None:
        async with limit:
            text.outcome = await reformat(text.before, text.kind, connection=connection, rules=rules)
        if not dialog.is_deleted:
            text.view()
            update()

    def closed() -> None:
        for task in tasks:
            task.cancel()
        dialog.delete()

    for t in texts:
        t.on_keep = update
    dialog.on_value_change(lambda e: closed() if not e.value else None)
    update()
    dialog.open()
    if todo and not problem:
        limit = asyncio.Semaphore(AT_ONCE)
        tasks += [background_tasks.create(one(t, limit), name=f'reformat {t.label}') for t in todo]


def _text_view(text: _Text) -> None:
    @ui.refreshable
    def view() -> None:
        outcome = text.outcome
        with ui.column().classes('w-full gap-2 reformat-text').mark(f'reformat-{text.kind}-{text.index}'):
            with ui.row().classes('w-full items-center no-wrap gap-2'):
                ui.label(text.label).classes('text-weight-medium')
                ui.space()
                if outcome is None:
                    ui.spinner(size='1.2em')
                    ui.label('Reformatting…').classes('text-sm muted')
                elif outcome.status == 'changed':
                    ui.checkbox('Apply', value=text.keep, on_change=lambda e: _keep(text, e.value)) \
                        .props('dense').mark('reformat-keep')
            if outcome is None:
                return
            if outcome.status == 'same':
                ui.label('Follows your message format already.').classes('text-sm muted').mark('reformat-same')
            elif outcome.status == 'failed':
                ui.label(f'Couldn\'t: {outcome.note}.').classes('text-sm text-negative').mark('reformat-failed')
            elif outcome.status == 'refused':
                ui.label(f'Not offered: {outcome.note}.').classes('text-sm text-negative').mark('reformat-refused')
                with ui.expansion('Its answer').classes('w-full text-sm').props('dense'):
                    ui.label(outcome.text).classes('prompt-text')
            else:
                with ui.element('div').classes('reformat-compare'):
                    with ui.column().classes('gap-1 min-w-0'):
                        ui.label('Before').classes('prompt-role')
                        ui.label(text.before).classes('prompt-text')
                    with ui.column().classes('gap-1 min-w-0'):
                        ui.label('After').classes('prompt-role')
                        ui.label(outcome.text).classes('prompt-text').mark('reformat-after')

    text.view = view.refresh
    view()


def _keep(text: _Text, value: bool) -> None:
    text.keep = bool(value)
    text.on_keep()


def undo(character: Character, kind: Kind) -> tuple[int, int]:
    """Put back the texts of this kind as they were before they were reformatted, those still as reformatted:
    (how many were put back, how many were changed since and left as they are)."""
    back = left = 0
    values = list(getattr(character, kind))
    for r in character.reformatted:
        if r.field != kind:
            continue
        if r.index < len(values) and values[r.index] == r.after:
            values[r.index] = r.before
            back += 1
        else:
            left += 1
    setattr(character, kind, values)
    character.reformatted = [r for r in character.reformatted if r.field != kind]
    return back, left
