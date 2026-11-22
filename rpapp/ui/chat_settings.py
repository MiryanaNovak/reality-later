"""Which connection and prompt chats use, chosen for a character (on its page: character_panel) or for one
chat (its ⋮ menu › Settings: chat_settings_dialog), instead of what they would use otherwise (chat_setup.py).

Each dropdown's first choice is that default, and says what it is, on two lines: its name, and where it comes
from ("DeepSeek" / "Same as in Settings", "Claude" / "Same as for Seraphina"), so that a long name isn't cut
off on a phone. Choosing it goes back to it. The prompt can also be none at all ("None: no instructions"),
as in Settings › Chat. The chat's dialog says why each one is used: chosen for the chat, for its character,
or Settings'.

The chat's dialog also has a switch for each module of the prompt it uses (presets.py): on or off for this chat
only (Conversation.modules; the modules it hasn't switched are as their prompt sets them). A fork keeps them, as
its own. A new chat shows the same dialog before it starts (new_chat_settings), after its persona is chosen:
Start the chat saves it with what was chosen there, Cancel leaves no chat behind.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from nicegui import ui

from ..chat_setup import ChatSetup, Source, chat_setup
from ..connections import Connection
from ..settings import ConnectionRef, PresetRef, PromptChoice, Settings, SettingsStore
from ..storage import JsonStore
from .form import NOT_SET, Form
from .widgets import Dialog

if TYPE_CHECKING:
    from ..characters import Character
    from ..conversations import Conversation, ConversationStore
    from ..presets import PromptModule, PromptPreset

DEFAULT = NOT_SET      # the dropdowns' first choice: what would be used anyway
NO_PROMPT = '-none-'   # the prompt's: none at all

# A choice shown on two lines: its name, then (smaller) where it comes from. In its label: "name\nwhere".
_NAME = "props.opt.label.split('\\n')[0]"
_WHERE = "props.opt.label.split('\\n')[1]"
SELECTED_TWO_LINES = f'''
<div class="choice-lines">
  <div class="choice-name">{{{{ {_NAME} }}}}</div>
  <div v-if="props.opt.label.includes('\\n')" class="choice-where">{{{{ {_WHERE} }}}}</div>
</div>'''
OPTION_TWO_LINES = f'''
<q-item v-bind="props.itemProps">
  <q-item-section>
    <q-item-label class="choice-name">{{{{ {_NAME} }}}}</q-item-label>
    <q-item-label v-if="props.opt.label.includes('\\n')" caption>{{{{ {_WHERE} }}}}</q-item-label>
  </q-item-section>
</q-item>'''


def default_choice(name: str, where: str) -> str:
    """The first choice's label: the name of what's used by default, and where that comes from ("in Settings")."""
    return f'{name[:1].upper()}{name[1:]}\nSame as {where}'


def two_lines(select: ui.select) -> ui.select:
    """Show the dropdown's choices, and the one chosen, on two lines when they have two (default_choice)."""
    select.add_slot('selected-item', SELECTED_TWO_LINES)
    select.add_slot('option', OPTION_TWO_LINES)
    return select.classes('two-line-choice')


class ChatChoices:
    """What the choices are made from: the connections, the prompts, and the settings (Settings › Chat)."""

    def __init__(self, connections: JsonStore[Connection], presets: JsonStore[PromptPreset] | None,
                 settings: SettingsStore) -> None:
        self.connections = connections
        self.presets = presets
        self.settings_store = settings

    def settings(self) -> Settings:
        try:
            return self.settings_store.load()
        except ValueError:  # (a damaged settings file: the Settings page says so)
            return Settings()

    def connection_list(self) -> dict[str, Connection]:
        return {c.id: c for c in sorted(self.connections.list(), key=lambda c: c.name.lower())}

    def preset_list(self) -> dict[str, PromptPreset]:
        if self.presets is None:
            return {}
        return {p.id: p for p in sorted(self.presets.list(), key=lambda p: p.name.lower())}


# --- the two dropdowns ----------------------------------------------------------------------------

def connection_name(ref: ConnectionRef | None, found: dict[str, Connection]) -> str:
    if ref is None:
        return 'none chosen'
    return found[ref.id].name if ref.id in found else f'{ref.name} (missing)'


def prompt_name(ref: PresetRef | None, found: dict[str, PromptPreset]) -> str:
    if ref is None:
        return 'no instructions'
    return found[ref.id].name if ref.id in found else f'{ref.name} (missing)'


def _connection_choice(form: Form, *, current: Callable[[], ConnectionRef | None],
                       put: Callable[[ConnectionRef | None], None], found: dict[str, Connection], default: str,
                       hint: str, marker: str) -> ui.select:
    options = {DEFAULT: default} | {c.id: c.name for c in found.values()}
    if (ref := current()) is not None and ref.id not in found:
        options[ref.id] = f'{ref.name} (missing)'

    def choose(value: str) -> None:
        if value == DEFAULT:
            put(None)
        elif value in found:
            put(ConnectionRef(id=value, name=found[value].name))
        # (the missing one, chosen again: kept as it is)

    return two_lines(form.choice('Connection', options, get=lambda: current().id if current() else DEFAULT,
                                 set=choose, hint=hint).mark(marker))


def _prompt_choice(form: Form, *, current: Callable[[], PromptChoice | None],
                   put: Callable[[PromptChoice | None], None], found: dict[str, PromptPreset], default: str,
                   hint: str, marker: str) -> ui.select:
    options = {DEFAULT: default, NO_PROMPT: 'None: no instructions'} | {p.id: p.name for p in found.values()}
    choice = current()
    if choice is not None and choice.preset is not None and choice.preset.id not in found:
        options[choice.preset.id] = f'{choice.preset.name} (missing)'

    def value() -> str:
        chosen = current()
        if chosen is None:
            return DEFAULT
        return chosen.preset.id if chosen.preset is not None else NO_PROMPT

    def choose(picked: str) -> None:
        if picked == DEFAULT:
            put(None)
        elif picked == NO_PROMPT:
            put(PromptChoice(preset=None))
        elif picked in found:
            put(PromptChoice(preset=PresetRef(id=picked, name=found[picked].name)))

    return two_lines(form.choice('Prompt', options, get=value, set=choose, hint=hint).mark(marker))


def _connection_details(ref: ConnectionRef | None, found: dict[str, Connection], *, missing: str) -> None:
    """The connection used: its kind and model, or that it's missing."""
    if ref is None and not found:
        with ui.row().classes('items-center gap-2 text-sm').mark('chat-connection-missing'):
            ui.label('You have no connections yet: chats need one to reach a model.').classes('muted')
            ui.link('Create one', '/connections')
        return
    if ref is None:
        ui.label('No connection is chosen: chats can\'t get replies. Choose one here, or in Settings.') \
            .classes('text-sm text-negative').mark('chat-connection-missing')
        return
    connection = found.get(ref.id)
    if connection is None:
        ui.label(f'The connection "{ref.name}" is missing: it was deleted, or its file was renamed. {missing}') \
            .classes('text-sm text-negative').mark('chat-connection-missing')
        return
    with ui.row().classes('items-center gap-2 text-sm muted'):
        ui.label(f'{type(connection).display_name}, model {connection.model or "not chosen yet"}.')
        ui.link('Edit connection', f'/connections/{connection.id}')


def _prompt_details(ref: PresetRef | None, found: dict[str, PromptPreset], *, missing: str) -> None:
    if ref is None:
        return
    if ref.id not in found:
        ui.label(f'The prompt "{ref.name}" is missing: replies are sent without instructions. {missing}') \
            .classes('text-sm text-negative').mark('chat-prompt-missing')
        return
    ui.link('Edit this prompt', f'/prompts/{ref.id}').classes('text-sm')


# --- on a character's page ------------------------------------------------------------------------

def character_panel(character: Character, form: Form, choices: ChatChoices) -> None:
    """The character's own connection and prompt (inside its "Model and prompt" panel)."""
    connections, presets = choices.connection_list(), choices.preset_list()
    default = chat_setup(choices.settings())  # (Settings')

    def put_connection(ref: ConnectionRef | None) -> None:
        character.connection = ref
        connection_details.refresh()

    def put_prompt(choice: PromptChoice | None) -> None:
        character.prompt = choice
        prompt_details.refresh()

    @ui.refreshable
    def connection_details() -> None:
        _connection_details(character.connection or default.connection, connections,
                            missing='Choose another one here, or in Settings.' if character.connection is None
                            else 'Choose another one.')

    @ui.refreshable
    def prompt_details() -> None:
        ref = character.prompt.preset if character.prompt is not None else default.preset
        _prompt_details(ref, presets, missing='Choose another one.')

    with ui.column().classes('w-full gap-1'):
        _connection_choice(form, current=lambda: character.connection, put=put_connection, found=connections,
                           default=default_choice(connection_name(default.connection, connections), 'in Settings'),
                           hint='The AI model that writes this character\'s replies', marker='character-connection')
        connection_details()
    with ui.column().classes('w-full gap-1'):
        _prompt_choice(form, current=lambda: character.prompt, put=put_prompt, found=presets,
                       default=default_choice(prompt_name(default.preset, presets), 'in Settings'),
                       hint='The instructions sent with every reply', marker='character-prompt')
        prompt_details()


# --- in a chat ------------------------------------------------------------------------------------

def because(source: Source, *, character: str, character_id: str, marker: str) -> None:
    """Why this connection (or prompt) is the one used."""
    with ui.row().classes('items-center gap-x-2 gap-y-0 text-sm muted').mark(marker):
        if source == 'chat':
            ui.label('Chosen for this chat only.')
        elif source == 'character':
            ui.label(f'Chosen for {character}: their chats use it, unless one has its own.')
            ui.link('Their page', f'/characters/{character_id}/edit')
        else:
            ui.label('From Settings › Chat: what chats use, unless their character or the chat has its own.')
            ui.link('Settings', '/settings')


def chat_settings_dialog(conversation: Conversation, *, store: ConversationStore, choices: ChatChoices,
                         character: Character | None) -> None:
    """The chat's own connection, prompt and modules, in a dialog: what's used, why, and the choice of others for
    this chat only. Saved as soon as chosen (`conversation`, the page's, follows)."""
    def save() -> None:
        try:
            store.choose(conversation.id, connection=conversation.connection, prompt=conversation.prompt,
                         modules=conversation.modules)
        except KeyError:
            ui.notify('This conversation was deleted.', type='warning')

    dialog = _settings_dialog(conversation, choices=choices, character=character, on_change=save, new=False)
    dialog.on_value_change(lambda e: _delete(dialog) if not e.value else None)
    dialog.open()


async def new_chat_settings(conversation: Conversation, *, choices: ChatChoices, character: Character) -> bool:
    """Before a new chat starts (`conversation`, not saved yet): its connection, prompt and modules, in the same
    dialog. True: start it, with what was chosen (in `conversation`); False: cancelled."""
    dialog = _settings_dialog(conversation, choices=choices, character=character, on_change=lambda: None, new=True)
    started = await dialog
    _delete(dialog)
    return started is True


def _settings_dialog(conversation: Conversation, *, choices: ChatChoices, character: Character | None,
                     on_change: Callable[[], None], new: bool) -> Dialog:
    """The dialog of chat_settings_dialog, or (`new`) of new_chat_settings. `on_change`: after each choice, made in
    `conversation`."""
    connections, presets = choices.connection_list(), choices.preset_list()
    settings = choices.settings()
    title = character.title if character is not None else conversation.character.title
    character_id = conversation.character.id
    default = chat_setup(settings, character)  # (without its own: its character's, or Settings')

    def where(source: Source) -> str:
        return f'for {title}' if source == 'character' else 'in Settings'

    def setup() -> ChatSetup:
        return chat_setup(settings, character, conversation)

    def chosen() -> None:  # (a connection or a prompt)
        on_change()
        connection_why.refresh()
        prompt_why.refresh()
        modules.refresh()

    def switched(module: PromptModule, on: bool) -> None:
        conversation.modules = {**conversation.modules, module.id: on}
        on_change()

    form = Form(on_change=chosen)
    with ui.context.client.layout, Dialog() as dialog, \
            ui.card().classes('dialog-card chat-settings').mark('chat-settings-dialog'):
        if new:
            ui.label(f'New chat with {title}').classes('panel-title')
            ui.label('What this chat uses. You can change it later, from its ⋮ menu › Settings.') \
                .classes('muted text-sm')
        else:
            ui.label('Chat settings').classes('panel-title')
            ui.label(f'For this chat only: other chats, with {title} too, keep theirs. Changes apply from the next '
                     'reply.').classes('muted text-sm')

        @ui.refreshable
        def connection_why() -> None:
            used = setup()
            because(used.connection_from, character=title, character_id=character_id, marker='chat-connection-why')
            _connection_details(used.connection, connections, missing='Choose another one.')

        @ui.refreshable
        def prompt_why() -> None:
            used = setup()
            because(used.preset_from, character=title, character_id=character_id, marker='chat-prompt-why')
            _prompt_details(used.preset, presets, missing='Choose another one.')

        @ui.refreshable
        def modules() -> None:
            """A switch for each module of the prompt used."""
            used = setup().preset
            preset = presets.get(used.id) if used is not None else None
            if preset is None or not preset.modules:
                return
            with ui.column().classes('w-full gap-0').mark('chat-modules'):
                ui.label('Modules').classes('text-weight-medium')
                ui.label(f'Of the prompt "{preset.name}": those on add their instructions. For this chat only.') \
                    .classes('text-sm muted')
                for module in preset.modules:
                    ui.switch(module.name or 'Untitled', value=module.is_on(conversation.modules),
                              on_change=lambda e, m=module: switched(m, bool(e.value))).mark(f'chat-module-{module.id}')

        with ui.column().classes('w-full gap-1'):
            _connection_choice(form, current=lambda: conversation.connection,
                               put=lambda ref: setattr(conversation, 'connection', ref), found=connections,
                               default=default_choice(connection_name(default.connection, connections),
                                                      where(default.connection_from)),
                               hint='', marker='chat-connection')
            connection_why()
        with ui.column().classes('w-full gap-1'):
            _prompt_choice(form, current=lambda: conversation.prompt,
                           put=lambda choice: setattr(conversation, 'prompt', choice), found=presets,
                           default=default_choice(prompt_name(default.preset, presets), where(default.preset_from)),
                           hint='', marker='chat-prompt')
            prompt_why()
        modules()
        with ui.row().classes('w-full justify-end gap-2'):
            if new:
                ui.button('Cancel', on_click=lambda: dialog.submit(False)).props('flat').mark('chat-settings-cancel')
                ui.button('Start the chat', icon='add_comment', on_click=lambda: dialog.submit(True)) \
                    .props('unelevated').mark('chat-settings-start')
            else:
                ui.button('Done', on_click=dialog.close).props('unelevated').mark('chat-settings-done')
    return dialog


def _delete(dialog: ui.dialog) -> None:
    try:
        dialog.delete()
    except (ValueError, KeyError):  # (the page is already gone)
        pass
