"""Conversations: a chat between one of your personas and a character.

    data/conversations/chat-of-30-oct-2026-22-50-3f9a2c.json
    {
      "id": "chat-of-30-oct-2026-22-50-3f9a2c",
      "title": "Chat of 30 Oct 2026, 22:50",
      "character": {"id": "seraphina-bd14eb", "title": "Seraphina (forest guardian)"},
      "persona": {"id": "mira-abc51b", "title": "Mira, wandering healer"},
      "created_at": "2026-10-30T21:50:12.345678Z",
      "updated_at": "2026-10-30T21:50:12.345678Z",
      "connection": null,                        <- its own (⋮ › Settings), instead of its character's or
      "prompt": null,                               Settings' (see chat_setup.py; as on characters.py)
      "messages": [
        {"id": "a1b2c3d4e5f6", "role": "assistant", "greeting": true,
         "content": "*Seraphina kneels beside you...*",
         "alternatives": ["*Seraphina kneels...*", "*The door creaks...*"], "alternative": 0, ...},
        {"id": "0f1e2d3c4b5a", "role": "user", "content": "Where am I?", ...}
      ]
    }

The title is the date and time the chat started, until you rename it (ConversationStore.rename).

The character and persona are referenced by id, with their title remembered,
like lore books: if one is deleted, the conversation can still say which.

The first message is the character's greeting. While it's the only message,
you can switch between the character's first messages (its "alternatives");
after that it's fixed. It can't be deleted.

The AI's last reply can have alternatives too: other replies to the same
message, asked for with > under it. They're kept until you reply, then the one
shown stays; the others are kept in the file but no longer offered. Placeholders like {{user}} are
filled in when the conversation starts, so messages are stored as shown.

What goes with a message but is too big for the conversation file is stored
apart, one file per message, so the conversation file stays small however
long the chat gets:

    data/conversations/prompts/<conversation id>/<message id>.json   the prompt sent after one of
                                                                    your messages (prompts.py)
    data/conversations/replies/<conversation id>/<message id>.json   the details of an AI reply, one per
                                                                    version: model, tokens, cost, thinking
                                                                    (replies.py)

and a chat's memories, with what's needed to undo them (memory.py):

    data/conversations/memories/<conversation id>.json
"""
from __future__ import annotations

import secrets
import shutil
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from .macros import fill
from .profiles import Profile
from .settings import ConnectionRef, PromptChoice
from .storage import JsonStore, StoredModel

if TYPE_CHECKING:
    from .characters import Character


def now() -> datetime:
    return datetime.now(timezone.utc)


class ProfileRef(BaseModel):
    """A link to a character or persona: its id, plus its title as it was last seen."""
    id: str
    title: str

    @classmethod
    def to(cls, profile: Profile) -> ProfileRef:
        return cls(id=profile.id, title=profile.title)


TITLE_MAX = 200  # characters


def new_message_id() -> str:
    return secrets.token_hex(6)


class ChatMessage(BaseModel):
    id: str = Field(default_factory=new_message_id)
    role: Literal['user', 'assistant', 'system']
    content: str
    created_at: datetime = Field(default_factory=now)
    edited_at: datetime | None = None
    greeting: bool = False  # the character's first message: can't be deleted
    # Versions to choose from: the character's first messages for the greeting, the replies written so far
    # for an AI reply. Empty when there's only one (the content).
    alternatives: list[str] = Field(default_factory=list)
    alternative: int = 0    # which of them is shown

    @property
    def versions(self) -> int:
        """How many versions there are to choose from (1 without alternatives)."""
        return len(self.alternatives) or 1


class Conversation(StoredModel):
    label_field: ClassVar[str] = 'title'

    title: str = 'New chat'
    name: str = Field('', exclude=True)  # from StoredModel; a conversation has no name of its own
    character: ProfileRef
    persona: ProfileRef
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    # Its own connection and prompt, instead of its character's or Settings' (None: those). See chat_setup.py.
    connection: ConnectionRef | None = None
    prompt: PromptChoice | None = None
    messages: list[ChatMessage] = Field(default_factory=list)

    def touch(self) -> None:
        """Mark the conversation as just updated (moves it to the top of the list)."""
        self.updated_at = now()

    # -- messages ------------------------------------------------------------------

    def message(self, message_id: str) -> ChatMessage | None:
        return next((m for m in self.messages if m.id == message_id), None)

    def add(self, role: Literal['user', 'assistant'], content: str) -> ChatMessage:
        message = ChatMessage(role=role, content=content)
        self.messages = [*self.messages, message]
        self.touch()
        return message

    def edit(self, message_id: str, content: str) -> ChatMessage:
        message = self._find(message_id)
        message.content = content
        message.edited_at = now()
        if message.alternatives:  # keep the edit if you switch away and back
            message.alternatives[message.alternative] = content
        self.touch()
        return message

    def delete_from(self, message_id: str) -> list[ChatMessage]:
        """Delete this message and every message after it. Returns what was deleted."""
        message = self._find(message_id)
        if message.greeting:
            raise ValueError("The character's first message can't be deleted")
        index = self.messages.index(message)
        deleted = self.messages[index:]
        self.messages = self.messages[:index]
        self.touch()
        return deleted

    @property
    def can_choose_greeting(self) -> bool:
        """True while the greeting is the only message and there's more than one to choose from."""
        return len(self.messages) == 1 and self.messages[0].greeting and len(self.messages[0].alternatives) > 1

    def choose_greeting(self, step: int) -> ChatMessage:
        """Show the next (step=1) or previous (step=-1) first message."""
        if not self.can_choose_greeting:
            raise ValueError('The first message can only be changed before the chat goes on')
        greeting = self.messages[0]
        greeting.alternative = (greeting.alternative + step) % len(greeting.alternatives)
        greeting.content = greeting.alternatives[greeting.alternative]
        return greeting

    @property
    def can_change_reply(self) -> bool:
        """True when the last message is a reply of the AI: you can ask for another, or go back to one."""
        return bool(self.messages) and self.messages[-1].role == 'assistant' and not self.messages[-1].greeting

    def choose_reply(self, index: int) -> ChatMessage:
        """Show the last reply's version `index` (counted from 0)."""
        if not self.can_change_reply:
            raise ValueError('Only the last reply of the AI can be changed')
        reply = self.messages[-1]
        if not 0 <= index < reply.versions:
            raise IndexError(f'There are {reply.versions} versions of this reply')
        if reply.alternatives:
            reply.alternative = index
            reply.content = reply.alternatives[index]
        return reply

    def add_version(self, message_id: str, content: str) -> ChatMessage:
        """Add another version of a reply, and show it."""
        message = self._find(message_id)
        if not message.alternatives:
            message.alternatives = [message.content]
        message.alternatives.append(content)
        message.alternative = len(message.alternatives) - 1
        message.content = content
        message.edited_at = None  # the edit, if any, was of another version
        self.touch()
        return message

    def _find(self, message_id: str) -> ChatMessage:
        message = self.message(message_id)
        if message is None:
            raise KeyError(f'No message {message_id!r} in this conversation')
        return message


Field_ = Literal['character', 'persona']


S = TypeVar('S', bound=StoredModel)


class MessageFiles(Generic[S]):
    """Files kept for some messages, one folder per conversation: <folder>/<conversation id>/<message id>.json."""

    def __init__(self, folder: str | Path, load: Callable[[dict], S]) -> None:
        self.folder = Path(folder)
        self._load = load

    def _store(self, conversation_id: str) -> JsonStore[S]:
        return JsonStore(self.folder / conversation_id, self._load)

    def save(self, conversation_id: str, message_id: str, item: S) -> None:
        self._store(conversation_id).save(item.model_copy(update={'id': message_id}))

    def get(self, conversation_id: str, message_id: str) -> S | None:
        if not (self.folder / conversation_id).is_dir():
            return None
        try:
            return self._store(conversation_id).get(message_id)
        except ValueError:
            return None

    def delete(self, conversation_id: str, message_ids: list[str]) -> None:
        if not (self.folder / conversation_id).is_dir():
            return
        store = self._store(conversation_id)
        for message_id in message_ids:
            store.delete(message_id)

    def delete_conversation(self, conversation_id: str) -> None:
        folder = self.folder / conversation_id
        if folder.is_dir() and folder.resolve().parent == self.folder.resolve():
            shutil.rmtree(folder)


class ConversationStore(JsonStore[Conversation]):
    """Conversations in <data_dir>/conversations/."""

    def __init__(self, data_dir: str | Path) -> None:
        super().__init__(Path(data_dir) / 'conversations', Conversation.model_validate)
        from .prompts import Prompt  # here to avoid circular imports
        from .replies import ReplyRecord
        from .memory import MemoryStore
        self.prompts: MessageFiles[Prompt] = MessageFiles(self.folder / 'prompts', Prompt.model_validate)
        self.replies: MessageFiles[ReplyRecord] = MessageFiles(self.folder / 'replies', ReplyRecord.model_validate)
        self.memories = MemoryStore(self.folder / 'memories')

    def delete_message_files(self, conversation_id: str, message_ids: list[str]) -> None:
        """After deleting messages: delete their prompts and reply details, and undo what the memories learnt
        from them."""
        self.prompts.delete(conversation_id, message_ids)
        self.replies.delete(conversation_id, message_ids)
        self.memories.forget(conversation_id, message_ids)

    def start(self, character: Character, persona: Profile) -> Conversation:
        """Create and save a new conversation, opening with the character's first message(s)."""
        started = now().astimezone()  # this computer's time zone, for the title
        conversation = Conversation(
            title=f'Chat of {started.day} {started:%b %Y, %H:%M}',
            character=ProfileRef.to(character),
            persona=ProfileRef.to(persona),
        )
        names = {'char': character.name or character.title, 'user': persona.name or persona.title}
        greetings = [fill(text, **names) for text in getattr(character, 'first_messages', []) if text.strip()]
        if greetings:
            conversation.messages = [ChatMessage(role='assistant', content=greetings[0], greeting=True,
                                                 alternatives=greetings if len(greetings) > 1 else [])]
        self.save(conversation)
        return conversation

    def delete(self, item_id: str) -> None:
        from .vector_files import delete_vectors
        super().delete(item_id)
        self.prompts.delete_conversation(item_id)
        self.replies.delete_conversation(item_id)
        self.memories.delete(item_id)
        delete_vectors(self.folder.parent, 'memories', item_id)  # (vector search: vector_files.py)

    def rename(self, conversation_id: str, title: str) -> Conversation:
        """Give the conversation another title, saved right away. It doesn't count as an update: the chat
        keeps its place among the recent ones. Raises ValueError for an empty title, KeyError if it's gone."""
        title = ' '.join(title.split())[:TITLE_MAX].strip()
        if not title:
            raise ValueError('The conversation needs a name')
        conversation = self.get(conversation_id)
        if conversation is None:
            raise KeyError(f'No conversation {conversation_id!r}')
        conversation.title = title
        self.save(conversation)
        return conversation

    def choose(self, conversation_id: str, *, connection: ConnectionRef | None,
               prompt: PromptChoice | None) -> Conversation:
        """Give the conversation its own connection and prompt (None: its character's, or Settings'), saved
        right away. Like a new title, it doesn't count as an update. KeyError if it's gone."""
        conversation = self.get(conversation_id)
        if conversation is None:
            raise KeyError(f'No conversation {conversation_id!r}')
        conversation.connection, conversation.prompt = connection, prompt
        self.save(conversation)
        return conversation

    def using(self, field: Field_, profile_id: str) -> list[Conversation]:
        """The conversations with this character (or persona), most recently updated first."""
        found = [c for c in self.list() if getattr(c, field).id == profile_id]
        return sorted(found, key=lambda c: c.updated_at, reverse=True)

    def count_by(self, field: Field_) -> Counter[str]:
        """How many conversations each character (or persona) has: {id: count}."""
        return Counter(getattr(c, field).id for c in self.list())
