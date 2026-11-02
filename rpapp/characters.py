"""Characters: who you chat with.

    {
      "id": "seraphina-3f9a2c",
      "title": "Seraphina (forest guardian)",  <- only shown in this app
      "name": "Seraphina",                     <- used in the chat
      "description": "...",                    <- what they're about: only for you, never sent
      "scenario": "...",                       <- all the AI is told about them and the story
      "first_messages": ["...", "..."],        <- how a chat can open; at least one
      "example_dialogues": ["...", "..."],     <- short exchanges showing how they speak and act
      "image": "3f9a2c81d0b4e5a7.webp",        <- a file in data/characters/images/
      "lorebooks": [{"id": "Eldoria", "name": "Eldoria"}]
    }

Title, name, picture, description and lore books come from profiles.py,
shared with personas.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field

from .profiles import Profile, ProfileStore


class Character(Profile):
    title: str = 'New character'
    scenario: str = ''
    first_messages: list[str] = Field(default_factory=lambda: [''], min_length=1)
    # Each one a few lines like "{{user}}: ..." / "{{char}}: ...". Sent as examples of style, not as
    # events of the story; while the whole chat fits, by default (see prompts.py).
    example_dialogues: list[str] = Field(default_factory=list)


def new_character(title: str) -> Character:
    return Character(title=title, name=title, created_at=datetime.now(timezone.utc))


class CharacterStore(ProfileStore[Character]):
    """Characters in <data_dir>/characters/, their pictures in <data_dir>/characters/images/."""

    def __init__(self, data_dir: str | Path) -> None:
        super().__init__(Path(data_dir) / 'characters', Character)
