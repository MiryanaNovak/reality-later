"""Characters: who you chat with.

    {
      "id": "seraphina-3f9a2c",
      "title": "Seraphina (forest guardian)",  <- only shown in this app
      "name": "Seraphina",                     <- used in the chat
      "tags": ["fantasy", "forest"],           <- only for you (and in its card): normalized as shared cards' are
      "summary": "...",                        <- what they're about, in a sentence or two, plain text
      "description": "...",                    <- as long as one likes: Markdown, basic HTML, its own pictures
      "scenario": "...",                       <- all the AI is told about them and the story
      "first_messages": ["...", "..."],        <- how a chat can open; at least one
      "example_dialogues": ["...", "..."],     <- short exchanges showing how they speak and act
      "image": "3f9a2c81d0b4e5a7.webp",        <- a file in data/characters/images/
      "lorebooks": [{"id": "Eldoria", "name": "Eldoria"}],
      "connection": {"id": "claude-1a2b3c", "name": "Claude"},   <- its chats' own, instead of Settings' (null:
      "prompt": {"preset": {"id": "noir-4d5e6f", "name": "Noir"}}    Settings'); "prompt": {"preset": null}: none
    }

Title, name, picture, description and lore books come from profiles.py,
shared with personas. The summary and the description are only for people,
never sent to the AI: descriptions.py. Which connection and prompt a chat uses:
chat_setup.py.

Version 1 had one text for people, the short one: it's the summary now (the
description starts empty).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .descriptions import pictures_in
from .profiles import Profile, ProfileStore
from .publishing.announcements import normalize_tags
from .settings import ConnectionRef, PromptChoice


class Reformatted(BaseModel):
    """A text as it was before it was reformatted (reformat.py), to undo it."""
    field: Literal['first_messages', 'example_dialogues']
    index: int
    before: str
    after: str  # (undone only while it's still this)


class Character(Profile):
    format_version: int = 2
    title: str = 'New character'
    # For people only: on its card in the list, and announced with it when it's published (descriptions.py).
    # The description (Profile's) is the long one.
    summary: str = ''
    tags: list[str] = Field(default_factory=list)  # as shared cards have them: lowercase, 20 at most
    scenario: str = ''
    first_messages: list[str] = Field(default_factory=lambda: [''], min_length=1)
    # Each one a few lines like "{{user}}: ..." / "{{char}}: ...". Sent as examples of style, not as
    # events of the story; while the whole chat fits, by default (see prompts.py).
    example_dialogues: list[str] = Field(default_factory=list)
    # Its UUID as a shared card (rpapp/publishing/): given when it's first published, kept in the cards
    # exported, and read back on import, so that publishing it again, here or elsewhere, makes a new version
    # of the same card ('' until then).
    shared_uuid: str = ''
    # The connection and the prompt its chats use, instead of Settings › Chat's (None: those); a chat can have
    # its own still (chat_setup.py). Not in the cards exported: they're this app's, and this computer's.
    connection: ConnectionRef | None = None
    prompt: PromptChoice | None = None
    # Reformatting its first messages and example dialogues to your message format (reformat.py): the texts as
    # they were before the last time, to undo it; and, imported from a card, whether to offer it on its editor
    # (until it's done or declined). Not in the cards exported either.
    reformatted: list[Reformatted] = Field(default_factory=list)
    reformat_offer: bool = False

    @model_validator(mode='before')
    @classmethod
    def _upgrade(cls, data: Any) -> Any:
        """Version 1's description was the short one: it's the summary now."""
        if isinstance(data, dict) and isinstance(data.get('format_version'), int) and data['format_version'] < 2 \
                and 'summary' not in data:
            data = {**data, 'summary': data.get('description') or '', 'description': '', 'format_version': 2}
        return data

    @field_validator('tags')
    @classmethod
    def _normalized(cls, tags: list[str]) -> list[str]:
        return list(normalize_tags(tags))

    def pictures(self) -> set[str]:
        """Its picture's files, and those its description shows."""
        return super().pictures() | pictures_in(self.description)


def new_character(title: str) -> Character:
    return Character(title=title, name=title, created_at=datetime.now(timezone.utc))


class CharacterStore(ProfileStore[Character]):
    """Characters in <data_dir>/characters/, their pictures in <data_dir>/characters/images/."""

    def __init__(self, data_dir: str | Path) -> None:
        super().__init__(Path(data_dir) / 'characters', Character)

    def duplicate(self, item: Character, label: str | None = None) -> Character:
        """A copy is a character of its own: published, it's another card, not a new version of the
        original's (no shared UUID)."""
        if not item.shared_uuid:
            return super().duplicate(item, label)
        return super().duplicate(item.model_copy(update={'shared_uuid': ''}), label or f'{item.label} (copy)')
