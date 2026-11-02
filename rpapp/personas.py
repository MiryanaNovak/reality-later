"""Personas: identities you can take on when chatting.

    {
      "id": "mira-3f9a2c",
      "title": "Mira (dark fantasy)",      <- only shown in this app
      "name": "Mira",                      <- what the AI calls you
      "gender": "female",
      "pronouns": "she/her",
      "description": "...",
      "image": "3f9a2c81d0b4e5a7.webp",    <- a file in data/personas/images/
      "lorebooks": [{"id": "Eldoria", "name": "Eldoria"}]
    }

What personas share with characters (title, name, picture, description, lore
books) lives in profiles.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from .profiles import LoreBookRef, Profile, ProfileStore

__all__ = ['GENDERS', 'PRONOUNS', 'LoreBookRef', 'Persona', 'PersonaStore', 'Pronouns', 'new_persona']

GENDERS = {
    '': 'Not specified',
    'female': 'Female',
    'male': 'Male',
    'non-binary': 'Non-binary',
    'neutral': 'Neutral',
    'other': 'Other',
}


class Pronouns(NamedTuple):
    subject: str      # she
    object: str       # her
    determiner: str   # her (book)
    possessive: str   # hers
    reflexive: str    # herself


PRONOUNS: dict[str, tuple[str, Pronouns | None]] = {  # key: (label, forms for prompts)
    '': ('Not specified', None),
    'she/her': ('She/her', Pronouns('she', 'her', 'her', 'hers', 'herself')),
    'he/him': ('He/him', Pronouns('he', 'him', 'his', 'his', 'himself')),
    'they/them': ('They/them', Pronouns('they', 'them', 'their', 'theirs', 'themselves')),
    'it/its': ('It/its', Pronouns('it', 'it', 'its', 'its', 'itself')),
    'she/they': ('She/they', Pronouns('she', 'her', 'her', 'hers', 'herself')),
    'he/they': ('He/they', Pronouns('he', 'him', 'his', 'his', 'himself')),
    'xe/xem': ('Xe/xem', Pronouns('xe', 'xem', 'xyr', 'xyrs', 'xemself')),
    'ze/hir': ('Ze/hir', Pronouns('ze', 'hir', 'hir', 'hirs', 'hirself')),
    'any': ('Any pronouns', Pronouns('they', 'them', 'their', 'theirs', 'themselves')),
}


class Persona(Profile):
    """An identity you take on in a chat. Title, name, picture, description and lore books come from Profile."""
    title: str = 'New persona'
    gender: str = ''
    pronouns: str = ''

    @property
    def pronoun_forms(self) -> Pronouns | None:
        """The pronouns' forms (she, her, her, hers, herself) for use in prompts later on."""
        entry = PRONOUNS.get(self.pronouns)
        return entry[1] if entry else None


def new_persona(title: str) -> Persona:
    return Persona(title=title, name=title, created_at=datetime.now(timezone.utc))


class PersonaStore(ProfileStore[Persona]):
    """Personas in <data_dir>/personas/, their pictures in <data_dir>/personas/images/."""

    def __init__(self, data_dir: str | Path) -> None:
        super().__init__(Path(data_dir) / 'personas', Persona)
