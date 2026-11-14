"""Which connection and which prompt a chat uses: its own, if it has one (chosen in the chat: ⋮ › Settings),
else its character's (on the character's page), else Settings › Chat's. The connection and the prompt each
on its own: a chat can have its own prompt, and use its character's connection.

    setup = chat_setup(settings, character, conversation)
    setup.connection, setup.connection_from   # a ConnectionRef (None: none chosen); 'chat', 'character', 'settings'
    setup.preset, setup.preset_from           # a PresetRef (None: no instructions); the same
    chat_setup(settings, character)           # what the character's chats use without their own

Only references: whether what they name still exists is the caller's to find out (a connection deleted
since: missing_connection() says where to choose another).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .settings import ConnectionRef, PresetRef, Settings

if TYPE_CHECKING:
    from .characters import Character
    from .conversations import Conversation

Source = Literal['chat', 'character', 'settings']


@dataclass(frozen=True)
class ChatSetup:
    connection: ConnectionRef | None   # None: none chosen (in Settings, there may be none yet)
    connection_from: Source
    preset: PresetRef | None           # None: no instructions
    preset_from: Source


def chat_setup(settings: Settings, character: Character | None = None,
               conversation: Conversation | None = None) -> ChatSetup:
    """What the conversation uses (without one: what its character's chats use without their own)."""
    if conversation is not None and conversation.connection is not None:
        connection, connection_from = conversation.connection, 'chat'
    elif character is not None and character.connection is not None:
        connection, connection_from = character.connection, 'character'
    else:
        connection, connection_from = settings.chat.connection, 'settings'
    if conversation is not None and conversation.prompt is not None:
        preset, preset_from = conversation.prompt.preset, 'chat'
    elif character is not None and character.prompt is not None:
        preset, preset_from = character.prompt.preset, 'character'
    else:
        preset, preset_from = settings.chat.preset, 'settings'
    return ChatSetup(connection, connection_from, preset, preset_from)


def missing_connection(setup: ChatSetup, character: str) -> str:
    """Why there's no connection to reply with, and where to choose one. `character`: the character's title."""
    ref = setup.connection
    if ref is None:
        return 'No chat connection is chosen: choose one in Settings.'
    if setup.connection_from == 'chat':
        return (f'The connection "{ref.name}" chosen for this chat is missing: choose another one in its settings '
                '(⋮ › Settings).')
    if setup.connection_from == 'character':
        return f'The connection "{ref.name}" chosen for {character} is missing: choose another one on their page.'
    return f'The chat connection "{ref.name}" is missing: choose another one in Settings.'
