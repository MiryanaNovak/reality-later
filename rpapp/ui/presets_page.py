"""The Prompts section: saved prompts, each with the instructions sent with every reply.

    /prompts               the list: create, duplicate, export, delete, import
    /prompts/<prompt id>   one prompt: its name, instructions and final instructions

Settings › Chat chooses the one chats use; the list and the editor say which one it is, and
the editor can make it the one. (In the code they're presets: see presets.py.)
"""
from __future__ import annotations

from nicegui import ui

from ..presets import CONTINUE_INSTRUCTION, EXTEND_INSTRUCTION, STARTER_FINAL, STARTER_INSTRUCTIONS, PromptPreset, new_preset
from ..settings import PresetRef, SettingsStore
from ..storage import JsonStore
from .collection_page import Collection, CollectionPage
from .form import Form
from .list_view import Row
from .widgets import not_found, panel

MACROS_HINT = 'Write {{char}} for the character\'s name and {{user}} for yours.'


class PresetsSection:
    def __init__(self, store: JsonStore[PromptPreset], settings: SettingsStore) -> None:
        self.settings = settings
        self.collection = Collection(
            title='Prompts', noun='prompt', path='/prompts', store=store,
            row=self._row, create=lambda name, kind: new_preset(name),
            search=lambda p: f'{p.name} {p.instructions} {p.final_instructions} {p.extend_instruction} '
                                  f'{p.continue_instruction}',
            delete_warning=self._delete_warning,
            empty_text='A prompt holds the instructions sent with every reply: the tone and the rules of the chat, '
                       'and what to write now. Keep several to compare them, and choose the one chats use in '
                       'Settings › Chat.',
            name_placeholder='e.g. Slow-burn romance',
        )
        self.pages = CollectionPage(self.collection)

    # -- which one chats use ----------------------------------------------------------------

    def in_use(self) -> str:
        """The id of the prompt chats use ('' if none)."""
        try:
            ref = self.settings.load().chat.preset
        except ValueError:
            return ''
        return ref.id if ref else ''

    def use(self, preset: PromptPreset) -> None:
        settings = self.settings.load()
        settings.chat.preset = PresetRef(id=preset.id, name=preset.name)
        self.settings.save(settings)

    def _row(self, preset: PromptPreset) -> Row:
        first = next((line.strip() for line in preset.instructions.splitlines() if line.strip()), '')
        return Row(title=preset.name, subtitle=first or 'No instructions yet', icon='description',
                   note='Used for chats' if preset.id == self.in_use() else '')

    def _delete_warning(self, preset: PromptPreset) -> str:
        if preset.id != self.in_use():
            return ''
        return 'Chats use this prompt: until you choose another in Settings › Chat, replies are sent without instructions.'

    # -- pages ------------------------------------------------------------------------------

    def list_page(self) -> None:
        self.pages.list_page()

    def edit_page(self, preset_id: str) -> None:
        store = self.collection.store
        try:
            preset = store.get(preset_id)
        except ValueError:
            preset = None
        if preset is None:
            not_found('Prompt', self.collection.path)
            return

        def changed() -> None:
            store.save_soon(preset)
            title.text = preset.name or 'Untitled'

        title = self.pages.item_header(preset, 'Prompt. Changes save automatically.')

        @ui.refreshable
        def status() -> None:
            if preset.id == self.in_use():
                with ui.row().classes('items-center gap-2').mark('preset-in-use'):
                    ui.icon('check_circle').classes('text-positive')
                    ui.label('Chats use this prompt.').classes('text-sm')
            else:
                with ui.row().classes('items-center gap-2'):
                    ui.label('Chats use another prompt.').classes('text-sm muted')
                    ui.button('Use this one', icon='check', on_click=use).props('outline dense').classes('px-2') \
                        .mark('use-preset')

        def use() -> None:
            store.save(preset)
            self.use(preset)
            ui.notify(f'Chats now use "{preset.name}".', type='positive')
            status.refresh()

        status()
        form = Form(on_change=changed)
        with panel('Prompt', f'Sent with every reply, in every chat. {MACROS_HINT}'):
            form.text(preset, 'name', 'Name', hint='Only for you: how it\'s listed')
            # stack-label keeps the labels on top, so the examples show while the fields are empty
            form.long_text(preset, 'instructions', 'Instructions', min_height='14rem',
                           placeholder=f'For example: {STARTER_INSTRUCTIONS}').props('stack-label')
            ui.label('At the start of the prompt, before the character, your persona and the scenario: '
                     'the tone and the rules of the chat.').classes('text-sm muted -mt-2')
            form.long_text(preset, 'final_instructions', 'Final instructions', min_height='6rem',
                           placeholder=f'For example: {STARTER_FINAL}').props('stack-label')
            ui.label('At the very end of the prompt, right after the chat. Models follow what comes last '
                     'the most: keep it short, about what to write now.').classes('text-sm muted -mt-2')
        with panel('Hidden instructions', 'Sent only when you ask for them, at the very end of the prompt. '
                                          'They never show in the chat.'):
            form.long_text(preset, 'extend_instruction', 'Extend the last reply', min_height='4rem',
                           placeholder=EXTEND_INSTRUCTION).props('stack-label').mark('extend-instruction')
            ui.label('When you ask the AI to extend its last reply (Send, with nothing written), with a connection '
                     'that extends replies with a hidden instruction. Leave it empty for the one shown greyed.') \
                .classes('text-sm muted -mt-2')
            form.long_text(preset, 'continue_instruction', 'Continue the scene', min_height='4rem',
                           placeholder=CONTINUE_INSTRUCTION).props('stack-label').mark('continue-instruction')
            ui.label('When you ask the AI to continue the scene: it stands in for your message, so the AI writes '
                     'its next one. Leave it empty for the one shown greyed.').classes('text-sm muted -mt-2')
