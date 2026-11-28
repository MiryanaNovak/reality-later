"""The Prompts section: saved prompts, each with the instructions sent with every reply.

    /prompts               the list (each with its description): create, duplicate, export, delete, import
    /prompts/<prompt id>   one prompt

The editor: Identity (its name and description, only for you), always open; then, folding away (open or not as
last left on this device, Instructions open until then): Instructions (and final instructions), Hidden
instructions, and Modules, added and deleted there: each with a name, whether it's on by default, instructions,
final instructions and memory instructions, added to the prompt's in the chats where it's on (presets.py). Each
chat turns them on or off in its settings (chat_settings.py).

Settings › Chat chooses the one chats use; the list and the editor say which one it is, and
the editor can make it the one. (In the code they're presets: see presets.py.)
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from nicegui import ui

from ..presets import (CONTINUE_INSTRUCTION, EXTEND_INSTRUCTION, STARTER_FINAL, STARTER_INSTRUCTIONS, PromptModule,
                       PromptPreset, new_preset)
from ..settings import PresetRef, SettingsStore
from ..storage import JsonStore
from .collection_page import Collection, CollectionPage
from .form import Form
from .list_view import Row
from .opened import Opened
from .widgets import Audience, audience_legend, confirm, folding_panel, not_found, panel

MACROS_HINT = 'Write {{char}} for the character\'s name and {{user}} for yours; {{sub}}, {{obj}}, {{poss}}, ' \
              '{{poss_p}} and {{ref}} for your persona\'s pronouns (she, her, her, hers, herself); {{format}} for ' \
              'your message format (Settings › Message format).'


class PresetsSection:
    def __init__(self, store: JsonStore[PromptPreset], settings: SettingsStore) -> None:
        self.settings = settings
        self.collection = Collection(
            title='Prompts', noun='prompt', path='/prompts', store=store,
            row=self._row, create=lambda name, kind: new_preset(name),
            search=lambda p: ' '.join([p.name, p.description, p.instructions, p.final_instructions,
                                       p.extend_instruction, p.continue_instruction,
                                       *(f'{m.name} {m.instructions} {m.final_instructions}' for m in p.modules)]),
            delete_warning=self._delete_warning,
            empty_text='A prompt holds the instructions sent with every reply: the tone and the rules of the chat, '
                       'and what to write now. Keep several to compare them, and choose the one chats use in '
                       'Settings › Chat.',
            name_placeholder='e.g. Slow-burn romance',
        )
        self.pages = CollectionPage(self.collection)
        self.opened = Opened('rp_prompt_sections', default={'instructions'})  # (the parts open: opened.py)

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
        return Row(title=preset.name, subtitle=preset.description.strip() or 'No description yet', icon='description',
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
        audience_legend('Instructions, final instructions, hidden instructions, modules', 'Name, description')
        form = Form(on_change=changed)
        with panel('Identity', audience='you'):
            form.text(preset, 'name', 'Name', hint='How it\'s listed').mark('preset-name')
            form.textarea(preset, 'description', 'Description', hint='What it\'s for: under its name, in the list') \
                .mark('preset-description')

        with self._part('instructions', 'Instructions', 'The tone and the rules of the chat, and what to write now',
                        audience='ai', about=f'Sent with every reply, in every chat. {MACROS_HINT}'):
            # stack-label keeps the labels on top, so the examples show while the fields are empty
            form.long_text(preset, 'instructions', 'Instructions', min_height='14rem',
                           placeholder=f'For example: {STARTER_INSTRUCTIONS}').props('stack-label')
            ui.label('At the start of the prompt, before the character, your persona and the scenario: '
                     'the tone and the rules of the chat.').classes('text-sm muted -mt-2')
            form.long_text(preset, 'final_instructions', 'Final instructions', min_height='6rem',
                           placeholder=f'For example: {STARTER_FINAL}').props('stack-label')
            ui.label('At the very end of the prompt, right after the chat. Models follow what comes last '
                     'the most: keep it short, about what to write now.').classes('text-sm muted -mt-2')

        with self._part('hidden', 'Hidden instructions', 'To extend the last reply, and to continue the scene',
                        audience='ai', about='Sent only when you ask for them, at the very end of the prompt. '
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

        with self._part('modules', 'Modules', 'Instructions to add, that each chat turns on or off', audience='ai',
                        about='In the chats where a module is on, its instructions come after the prompt\'s '
                              'instructions, its final instructions after the final ones, and its memory instructions '
                              'after the memory instructions (Settings › Memory) when memories are written. Those '
                              'left empty are left out. Each chat turns modules on or off in its settings (its ⋮ '
                              'menu › Settings); until it does, they\'re as set here.'):
            self._modules(preset, form, changed)

    @contextmanager
    def _part(self, key: str, title: str, short: str, *, about: str = '',
              audience: Audience | None = None) -> Iterator[None]:
        """One of the editor's parts that fold away, open or not as last left on this device."""
        with folding_panel(title, short, opened=self.opened.is_open(key), audience=audience,
                           about=about) as expansion:
            expansion.mark(f'part-{key}')
            expansion.on_value_change(lambda e: self.opened.set(key, bool(e.value)))
            yield

    def _modules(self, preset: PromptPreset, form: Form, changed) -> None:
        """The prompt's modules, each with its name, instructions and final instructions; Add a module."""
        new: set[str] = set()  # (the one just added: its name is ready to type)

        def add() -> None:
            new.add(preset.add_module().id)
            changed()
            modules.refresh()

        async def delete(module: PromptModule) -> None:
            if (module.instructions.strip() or module.final_instructions.strip()) and not await confirm(
                    f'Delete the module "{module.name or "Untitled"}"?', 'Its instructions will be lost.', 'Delete'):
                return
            preset.remove_module(module.id)
            changed()
            modules.refresh()

        @ui.refreshable
        def modules() -> None:
            if not preset.modules:
                ui.label('No modules yet.').classes('muted text-sm').mark('no-modules')
            for module in preset.modules:
                with ui.column().classes('module-box w-full gap-3').mark(f'module-{module.id}'):
                    with ui.row().classes('w-full items-center no-wrap gap-2'):
                        name = form.text(module, 'name', 'Name').classes('grow').mark('module-name')
                        if module.id in new:  # (its name selected: typing replaces "New module")
                            name.props('autofocus').on('focus', js_handler='(e) => e.target.select()')
                        ui.button(icon='delete_outline', on_click=lambda m=module: delete(m)) \
                            .props('flat round dense aria-label="Delete this module"').tooltip('Delete this module') \
                            .mark('module-delete')
                    form.switch(module, 'on_by_default', 'On by default').mark('module-on-by-default')
                    ui.label('In the chats that haven\'t turned it on or off in their settings.') \
                        .classes('text-sm muted -mt-3')
                    form.long_text(module, 'instructions', 'Instructions', min_height='6rem') \
                        .props('stack-label').mark('module-instructions')
                    form.long_text(module, 'final_instructions', 'Final instructions', min_height='4rem') \
                        .props('stack-label').mark('module-final-instructions')
                    form.long_text(module, 'memory_instructions', 'Memory instructions', min_height='4rem') \
                        .props('stack-label').mark('module-memory-instructions')
            new.clear()
            ui.button('Add a module', icon='add', on_click=add).props('outline').mark('module-add')

        modules()
