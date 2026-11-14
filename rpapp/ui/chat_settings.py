"""Which connection and prompt chats use, chosen for a character (on its page: character_panel) or for one
chat (its ⋮ menu › Settings: chat_settings_dialog), instead of what they would use otherwise (chat_setup.py).

Each dropdown's first choice is that default, and says what it is ("Same as in Settings: DeepSeek", "Same as
for Seraphina: Claude"): choosing it goes back to it. The prompt can also be none at all ("None: no
instructions"), as in Settings › Chat. The chat's dialog says why each one is used: chosen for the chat,
for its character, or Settings'.
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

if TYPE_CHECKING:
    from ..characters import Character
    from ..conversations import Conversation, ConversationStore
    from ..presets import PromptPreset

DEFAULT = NOT_SET      # the dropdowns' first choice: what would be used anyway
NO_PROMPT = '-none-'   # the prompt's: none at all


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

    return form.choice('Connection', options, get=lambda: current().id if current() else DEFAULT, set=choose,
                       hint=hint).mark(marker)


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

    return form.choice('Prompt', options, get=value, set=choose, hint=hint).mark(marker)


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
                           default=f'Same as in Settings: {connection_name(default.connection, connections)}',
                           hint='The AI model that writes this character\'s replies', marker='character-connection')
        connection_details()
    with ui.column().classes('w-full gap-1'):
        _prompt_choice(form, current=lambda: character.prompt, put=put_prompt, found=presets,
                       default=f'Same as in Settings: {prompt_name(default.preset, presets)}',
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
            ui.link('Their page', f'/characters/{character_id}')
        else:
            ui.label('From Settings › Chat: what chats use, unless their character or the chat has its own.')
            ui.link('Settings', '/settings')


def chat_settings_dialog(conversation: Conversation, *, store: ConversationStore, choices: ChatChoices,
                         character: Character | None) -> None:
    """The chat's own connection and prompt, in a dialog: what's used, why, and the choice of another one for
    this chat only. Saved as soon as chosen (`conversation`, the page's, follows)."""
    connections, presets = choices.connection_list(), choices.preset_list()
    settings = choices.settings()
    title = character.title if character is not None else conversation.character.title
    character_id = conversation.character.id
    default = chat_setup(settings, character)  # (without its own: its character's, or Settings')

    def where(source: Source) -> str:
        return f'for {title}' if source == 'character' else 'in Settings'

    def setup() -> ChatSetup:
        return chat_setup(settings, character, conversation)

    def save() -> None:
        try:
            store.choose(conversation.id, connection=conversation.connection, prompt=conversation.prompt)
        except KeyError:
            ui.notify('This conversation was deleted.', type='warning')
        connection_why.refresh()
        prompt_why.refresh()

    form = Form(on_change=save)
    with ui.context.client.layout, ui.dialog() as dialog, \
            ui.card().classes('dialog-card chat-settings').mark('chat-settings-dialog'):
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

        with ui.column().classes('w-full gap-1'):
            _connection_choice(form, current=lambda: conversation.connection,
                               put=lambda ref: setattr(conversation, 'connection', ref), found=connections,
                               default=f'Same as {where(default.connection_from)}: '
                                       f'{connection_name(default.connection, connections)}',
                               hint='', marker='chat-connection')
            connection_why()
        with ui.column().classes('w-full gap-1'):
            _prompt_choice(form, current=lambda: conversation.prompt,
                           put=lambda choice: setattr(conversation, 'prompt', choice), found=presets,
                           default=f'Same as {where(default.preset_from)}: {prompt_name(default.preset, presets)}',
                           hint='', marker='chat-prompt')
            prompt_why()
        with ui.row().classes('w-full justify-end'):
            ui.button('Done', on_click=dialog.close).props('unelevated').mark('chat-settings-done')
    dialog.on_value_change(lambda e: _delete(dialog) if not e.value else None)
    dialog.open()


def _delete(dialog: ui.dialog) -> None:
    try:
        dialog.delete()
    except ValueError:  # (the page is already gone)
        pass
