"""The Memories page of a chat: see and edit what the AI remembers.

    /conversations/<conversation id>/memories

Each memory has a title, keywords, a text, and can be set to be in every prompt.
When memories were just generated, the page shows the memories as they'll be
once that update is applied (when you post your next message): new and changed
ones first, marked as such, and the ones it removes at the end, with Keep it.
Edits then go into the update. Otherwise, edits go straight into the memories.
"""
from __future__ import annotations

from nicegui import ui

from ..conversations import Conversation, ConversationStore
from ..lorebooks import LoreEntry
from ..memory import Memories, add_memory
from ..replies import Replies
from .form import Form
from .widgets import confirm, page_header


class MemoriesPage:
    def __init__(self, conversation: Conversation, *, store: ConversationStore, replies: Replies,
                 char_name: str) -> None:
        self.conversation = conversation
        self.store = store
        self.replies = replies
        self.char_name = char_name
        self.chat_path = f'/conversations/{conversation.id}'

    def build(self) -> None:
        c = self.conversation
        page_header('Memories', subtitle=f'What {self.char_name} remembers of "{c.title}"', back=self.chat_path)
        if self.replies.running(c.id):
            ui.label('A reply or memories are being generated for this chat: come back in a moment.') \
                .classes('muted').mark('memories-busy')
            return
        memories = self.store.memories.get(c.id)
        ui.label('A memory comes back into the prompt when one of its keywords appears in the last messages, '
                 'or every time if it\'s set to be in every prompt.').classes('muted -mt-2')

        @ui.refreshable
        def body() -> None:
            self._body(memories, body.refresh)

        body()

    def _save(self, memories: Memories) -> None:
        self.store.memories.save_soon(memories)

    def _body(self, memories: Memories, refresh) -> None:
        book = memories.editable
        changes = memories.changes()
        if memories.pending:
            with ui.column().classes('panel gap-1').mark('memories-pending'):
                ui.label(f'New memories: {changes.summary or "nothing new"}').classes('panel-title')
                ui.label('Generated from the latest messages. They\'re added when you post your next message: '
                         'until then, you can change them here.').classes('text-sm muted')

        def add() -> None:
            add_memory(book, taken=memories.taken, comment='New memory', key=[], content='')
            self._save(memories)
            refresh()

        flagged = {**{k: 'New' for k in changes.added}, **{k: 'Changed' for k in changes.changed}}
        order = [*[k for k in book.entries if k in flagged], *[k for k in book.entries if k not in flagged]]
        if not order and not changes.removed:
            ui.label('No memories yet. They\'re generated when old messages leave the prompt, or when you ask '
                     'from the chat\'s menu. You can also add your own.').classes('muted').mark('memories-empty')
        for key in order:
            self._card(memories, key, flagged.get(key, ''), refresh)
        ui.button('Add a memory', icon='add', on_click=add).props('outline').mark('memory-add')

        if changes.removed:
            ui.label('Removed by the update').classes('section-title mt-4')
            for key in changes.removed:
                entry = memories.book.entries[key]
                with ui.column().classes('panel gap-1').mark(f'memory-removed-{key}'):
                    with ui.row().classes('w-full items-center no-wrap'):
                        ui.label(entry.comment or 'Untitled').classes('panel-title grow')

                        def keep(key: str = key) -> None:
                            memories.pending.proposed.entries[key] = memories.book.entries[key].model_copy(deep=True)
                            self._save(memories)
                            refresh()
                        ui.button('Keep it', icon='undo', on_click=keep).props('flat dense no-caps') \
                            .mark('memory-keep')
                    ui.label(entry.content).classes('text-sm muted')

    def _card(self, memories: Memories, key: str, flag: str, refresh) -> None:
        entry: LoreEntry = memories.editable.entries[key]
        form = Form(on_change=lambda: self._save(memories))
        with ui.column().classes('panel gap-3').mark(f'memory-{key}'):
            with ui.row().classes('w-full items-center no-wrap gap-2'):
                if flag:
                    ui.badge(flag).props('color=primary rounded').mark('memory-flag')
                form.text(entry, 'comment', 'Title').classes('grow').mark('memory-title')

                async def delete() -> None:
                    if await confirm(f'Delete the memory "{entry.comment or "Untitled"}"?',
                                     'The AI won\'t remember this any more, unless it comes up again.', 'Delete'):
                        memories.editable.remove_entry(key)
                        self._save(memories)
                        refresh()
                ui.button(icon='delete_outline', on_click=delete) \
                    .props('flat round dense aria-label="Delete this memory"').mark('memory-delete')
            form.chips(entry, 'key', 'Keywords', hint='It comes back when one of these appears. Enter after each') \
                .mark('memory-keys')
            form.textarea(entry, 'content', 'What to remember').mark('memory-text')
            form.switch(entry, 'constant', 'In every prompt').mark('memory-always')
            if flag == 'Changed':
                before = memories.book.entries[key]

                def undo() -> None:
                    memories.pending.proposed.entries[key] = before.model_copy(deep=True)
                    self._save(memories)
                    refresh()
                with ui.column().classes('w-full gap-1'):
                    ui.label('Before').classes('text-xs muted text-weight-medium')
                    ui.label(before.content).classes('text-sm muted')
                    ui.button('Undo the change', icon='undo', on_click=undo).props('flat dense no-caps') \
                        .mark('memory-undo')
