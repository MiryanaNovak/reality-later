"""Lore books (SillyTavern "World Info"), in SillyTavern's own JSON format.

A lore book file looks like this (only `entries` is required):

    {
      "name": "Eldoria",                    <- optional; SillyTavern otherwise uses the file name
      "entries": {
        "0": {"uid": 0, "key": ["eldoria", "forest"], "content": "...", "position": 0, ...},
        "1": {...}
      }
    }

Field names are SillyTavern's own (camelCase), so the files stay identical to
theirs. Fields this code doesn't know about, at any level, are kept as they are
and written back unchanged: books made in a newer SillyTavern lose nothing.

Reference: SillyTavern 1.19 public/scripts/world-info.js, and
https://docs.sillytavern.app/usage/worldinfo/
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from .storage import JsonStore, StoredModel

Number = int | float

# --- SillyTavern's enum values, with labels for the UI --------------------------------

POSITIONS = {  # world_info_position
    0: 'Before character definitions',
    1: 'After character definitions',
    5: 'Before example messages',
    6: 'After example messages',
    2: "Top of author's note",
    3: "Bottom of author's note",
    4: 'In the chat, at a depth',
    7: 'Outlet (placed by a macro)',
}
POSITION_SHORT = {0: '↑Char', 1: '↓Char', 2: '↑AN', 3: '↓AN', 4: '@D', 5: '↑EM', 6: '↓EM', 7: 'Outlet'}
AT_DEPTH, OUTLET = 4, 7

ROLES = {0: 'System', 1: 'User', 2: 'Assistant'}  # only used at depth

SELECTIVE_LOGIC = {  # world_info_logic: how the optional filter keys combine with the main keys
    0: 'And any of these',
    3: 'And all of these',
    2: 'And none of these',
    1: 'And not all of these',
}

TRIGGERS = {  # generation types an entry can be limited to (empty = all)
    'normal': 'Normal', 'continue': 'Continue', 'impersonate': 'Impersonate',
    'swipe': 'Swipe', 'regenerate': 'Regenerate', 'quiet': 'Quiet (background)',
}

STRATEGIES = {
    'constant': 'Always included',
    'normal': 'When a keyword appears',
    'vectorized': 'By meaning (Vector Storage)',
}


class CharacterFilter(BaseModel):
    model_config = ConfigDict(extra='allow', validate_assignment=True)

    isExclude: bool = False
    names: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)  # SillyTavern tag ids


class LoreEntry(BaseModel):
    """One entry. Defaults are SillyTavern's defaults for a new entry."""
    model_config = ConfigDict(extra='allow', validate_assignment=True)

    uid: int
    key: list[str] = Field(default_factory=list)           # keywords (plain text or /regex/)
    keysecondary: list[str] = Field(default_factory=list)  # optional filter
    comment: str = ''                                      # title / memo, never sent to the model
    content: str = ''
    constant: bool = False
    vectorized: bool = False
    selective: bool = True
    selectiveLogic: int = 0
    addMemo: bool = True
    order: Number = 100
    position: int = 0
    disable: bool = False
    ignoreBudget: bool = False
    excludeRecursion: bool = False
    preventRecursion: bool = False
    matchPersonaDescription: bool = False
    matchCharacterDescription: bool = False
    matchCharacterPersonality: bool = False
    matchCharacterDepthPrompt: bool = False
    matchScenario: bool = False
    matchCreatorNotes: bool = False
    delayUntilRecursion: bool | int = 0  # false/0 = off, true/1 = on, 2+ = recursion level
    probability: Number = 100
    useProbability: bool = True
    depth: int = 4
    outletName: str = ''
    group: str = ''
    groupOverride: bool = False
    groupWeight: Number = 100
    scanDepth: int | None = None         # None = use the global setting
    caseSensitive: bool | None = None    # None = use the global setting
    matchWholeWords: bool | None = None  # None = use the global setting
    useGroupScoring: bool | None = None  # None = use the global setting
    automationId: str = ''
    role: int | None = None              # only meaningful with position "at depth"
    sticky: int | None = None
    cooldown: int | None = None
    delay: int | None = None
    triggers: list[str] = Field(default_factory=list)
    displayIndex: int | None = None
    characterFilter: CharacterFilter | None = None

    # -- conveniences --------------------------------------------------------------

    @property
    def title(self) -> str:
        """What to call the entry in lists: its memo, else its keywords."""
        return self.comment.strip() or ', '.join(self.key) or 'Untitled entry'

    @property
    def strategy(self) -> str:
        return 'constant' if self.constant else 'vectorized' if self.vectorized else 'normal'

    def set_strategy(self, value: str) -> None:
        self.constant = value == 'constant'
        self.vectorized = value == 'vectorized'

    def set_position(self, value: int) -> None:
        """Change the position the way SillyTavern does: a role only goes with 'at depth'."""
        self.position = value
        if value == AT_DEPTH:
            if self.role is None:
                self.role = 0
        else:
            self.role = None

    @model_serializer(mode='wrap')
    def _leave_out_unset(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # SillyTavern only writes these two when they have a value; do the same.
        data = handler(self)
        for name in ('characterFilter', 'displayIndex'):
            if data.get(name) is None:
                data.pop(name, None)
        return data


class LoreBook(StoredModel):
    model_config = ConfigDict(extra='allow', validate_assignment=True)  # keeps e.g. "originalData"

    entries: dict[str, LoreEntry]

    def sorted_entries(self) -> list[LoreEntry]:
        """Entries in SillyTavern's display order."""
        def sort_key(e: LoreEntry) -> tuple[int, int]:
            return (e.displayIndex if e.displayIndex is not None else e.uid, e.uid)
        return sorted(self.entries.values(), key=sort_key)

    def entry(self, uid: int | str) -> LoreEntry | None:
        return self.entries.get(str(uid))

    def add_entry(self, **fields: Any) -> LoreEntry:
        """Add an entry at the end of the list, with SillyTavern's defaults."""
        uid = next(i for i in range(len(self.entries) + 1) if str(i) not in self.entries)
        indexes = [e.displayIndex if e.displayIndex is not None else e.uid for e in self.entries.values()]
        entry = LoreEntry(**{**fields, 'uid': uid, 'displayIndex': max(indexes, default=-1) + 1})
        self.entries[str(uid)] = entry
        return entry

    def duplicate_entry(self, entry: LoreEntry) -> LoreEntry:
        data = entry.model_dump()
        data.pop('uid', None)
        data.pop('displayIndex', None)
        data['comment'] = f'{entry.comment} (copy)' if entry.comment else ''
        return self.add_entry(**data)

    def remove_entry(self, uid: int | str) -> None:
        self.entries.pop(str(uid), None)

    def to_sillytavern(self) -> dict[str, Any]:
        """The book as SillyTavern stores it: our internal id and version are left out."""
        extras = {k: v for k, v in (self.model_extra or {}).items()}
        return {
            'name': self.name,
            'entries': {key: e.model_dump(mode='json') for key, e in self.entries.items()},
            **extras,
        }


def new_lore_book(name: str) -> LoreBook:
    return LoreBook(name=name, entries={})


def lore_book_store(data_dir) -> JsonStore[LoreBook]:
    """The store for <data_dir>/lorebooks/. Its files are valid SillyTavern lore books too."""
    return JsonStore(Path(data_dir) / 'lorebooks', LoreBook.model_validate)

