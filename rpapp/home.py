"""What the Home page remembers: the tips you dismissed. In data/home.json:

    {"id": "home", "format_version": 1, "dismissed": ["create-connection", "pair-device"]}

Kept apart from settings.json so that a Settings page open in another tab, which saves
the settings it loaded, can't bring back a tip you just dismissed.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field

from .storage import JsonStore, StoredModel

HOME_ID = 'home'


class HomeState(StoredModel):
    id: str = HOME_ID
    name: str = Field('', exclude=True)  # from StoredModel; unused
    dismissed: list[str] = Field(default_factory=list)  # tips not to show again


class HomeStore:
    def __init__(self, data_dir: str | Path) -> None:
        self._store = JsonStore(Path(data_dir), HomeState.model_validate)

    def load(self) -> HomeState:
        try:
            return self._store.get(HOME_ID) or HomeState()
        except ValueError:  # a damaged file: as if nothing were dismissed (it's only tips)
            return HomeState()

    def dismissed(self) -> set[str]:
        return set(self.load().dismissed)

    def dismiss(self, tip: str) -> None:
        state = self.load()
        if tip not in state.dismissed:
            state.dismissed = [*state.dismissed, tip]
            self._store.save(state)

    def restore(self, tips: set[str]) -> None:
        """Show these tips again."""
        state = self.load()
        state.dismissed = [t for t in state.dismissed if t not in tips]
        self._store.save(state)
