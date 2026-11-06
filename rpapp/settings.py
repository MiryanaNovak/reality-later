"""App settings, in one file: data/settings.json.

    {
      "id": "settings",
      "format_version": 1,
      "chat": {
        "connection": {"id": "deepseek-for-adventures-b447ec", "name": "DeepSeek for adventures"},
        "preset": {"id": "default-9c41d2", "name": "Default"},
        "example_dialogues": "make_room",
        "return_key_desktop": "send",
        "return_key_mobile": "new_line"
      },
      "lore": {"scan_depth": 2, "budget_percent": 25, "recursive": true, ...},
      "memory": {"enabled": true, "connection": null, "instructions": "", ...},
      "appearance": {"accent": "jade"}
    }

Settings are grouped by topic, one sub-object per section of the Settings
page. The chat connection is referenced by id with its name
remembered, like lore books: if it's deleted, the page can say which one.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from .connections import Connection
from .storage import JsonStore, StoredModel

SETTINGS_ID = 'settings'


class ConnectionRef(BaseModel):
    """A link to a connection: its id, plus its name as it was last seen."""
    id: str
    name: str


class PresetRef(BaseModel):
    """A link to a saved prompt (presets.py): its id, plus its name as it was last seen."""
    id: str
    name: str


class ChatSettings(BaseModel):
    model_config = ConfigDict(extra='ignore', validate_assignment=True)

    connection: ConnectionRef | None = None  # the connection chats use; None = not chosen yet
    preset: PresetRef | None = None          # the prompt chats use (see presets.py); None = no instructions
    # Before prompts had their own page, the instructions were here. Still read, so that presets.py can
    # turn them into a prompt on the first start, but no longer written. ("prompt" in the very first version.)
    instructions: str = Field('', validation_alias=AliasChoices('instructions', 'prompt'), exclude=True)
    final_instructions: str = Field('', exclude=True)
    # Characters' example dialogues: 'make_room' sends them while the whole chat fits, then leaves them out,
    # the last ones first, to make room for it; 'always' sends them all, always.
    example_dialogues: Literal['make_room', 'always'] = 'make_room'
    # What Return does in the chat's writing field: on computers, and on phones and tablets (devices used by
    # touch). Shift+Return always makes a new line, Ctrl+Return always sends (see ui/chat_page.py).
    return_key_desktop: Literal['send', 'new_line'] = 'send'
    return_key_mobile: Literal['send', 'new_line'] = 'new_line'


class LoreSettings(BaseModel):
    """How lore book entries are picked; entries can override the first three."""
    model_config = ConfigDict(extra='ignore', validate_assignment=True)

    scan_depth: int = Field(2, ge=0, le=1000)      # how many of the last messages are scanned for keywords
    case_sensitive: bool = False
    whole_words: bool = True                       # (plurals included, see lore.key_matches)
    recursive: bool = True                         # entries that go in can trigger others
    budget_percent: int = Field(25, ge=1, le=100)  # share of the prompt's space lore may take


class MemorySettings(BaseModel):
    """How each chat's memory is kept (see memory.py)."""
    model_config = ConfigDict(extra='ignore', validate_assignment=True)

    enabled: bool = True
    connection: ConnectionRef | None = None             # None = the chat connection
    instructions: str = ''                              # '' = memory.DEFAULT_INSTRUCTIONS
    budget_percent: int = Field(15, ge=1, le=100)       # share of the prompt's space memory entries may take
    temperature: float | None = Field(None, ge=0, le=2)  # None = memory.DEFAULT_TEMPERATURE
    max_tokens: int | None = Field(None, ge=1)          # None = memory.DEFAULT_MAX_TOKENS


class VectorSettings(BaseModel):
    """Vector search (see vectors.py): memories and lore entries picked by meaning too, not only by keywords."""
    model_config = ConfigDict(extra='ignore', validate_assignment=True)

    memories: bool = False
    lore: bool = False
    model: str = 'all-minilm-l6-v2'                      # a key of embedding_models.json
    source: str = 'huggingface'                         # where it's downloaded from (embeddings.SOURCES)
    thresholds: dict[str, float] = Field(default_factory=dict)  # closeness needed, per model (else its default)
    max_entries: int = Field(3, ge=1, le=20)            # brought in by meaning alone, at most, per reply

    def threshold(self, model_key: str, default: float) -> float:
        return self.thresholds.get(model_key, default)


class AppearanceSettings(BaseModel):
    """How the app looks on every device. (Light or dark, and the text size, are per device: ui/appearance.py.)"""
    model_config = ConfigDict(extra='ignore', validate_assignment=True)

    accent: str = 'jade'  # one of ui/theme.py's ACCENTS (an unknown one shows as the default)


class Settings(StoredModel):
    id: str = SETTINGS_ID
    name: str = Field('', exclude=True)  # from StoredModel; settings have no name
    chat: ChatSettings = Field(default_factory=ChatSettings)
    lore: LoreSettings = Field(default_factory=LoreSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    vectors: VectorSettings = Field(default_factory=VectorSettings)
    appearance: AppearanceSettings = Field(default_factory=AppearanceSettings)


class SettingsStore:
    """Reads and writes data/settings.json (atomically, like every other file of the app)."""

    def __init__(self, data_dir: str | Path) -> None:
        self._store = JsonStore(Path(data_dir), Settings.model_validate)
        self.path = self._store.path_for(SETTINGS_ID)

    def load(self) -> Settings:
        """The saved settings, or the defaults if there's no file yet.

        Raises ValueError if the file exists but can't be read: the caller should
        say so rather than save over it, so hand edits are never lost.
        """
        return self._store.get(SETTINGS_ID) or Settings()

    def save(self, settings: Settings) -> None:
        self._store.save(settings)

    def save_soon(self, settings: Settings) -> None:
        self._store.save_soon(settings)

    def flush(self) -> None:
        self._store.flush()


def chat_connection(settings: Settings, find: Callable[[str], Connection | None]) -> Connection | None:
    """The connection chats should use, or None if none is chosen or it was deleted.

    `find` looks a connection up by id (e.g. a connection store's get()).
    """
    ref = settings.chat.connection
    return find(ref.id) if ref else None
