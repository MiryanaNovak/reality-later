"""Folders of personas, to keep them in order: nothing else (the AI never knows of them). In
data/persona-folders.json:

    {"id": "persona-folders", "format_version": 1,
     "folders": [{"id": "fantasy-3f9a2c", "name": "Fantasy", "personas": ["mira-abc51b", "wren-77e0d2"]}]}

A persona is in one folder at most, or in none; folders hold no folders. Deleting a folder keeps its
personas: they're just in none any more. The folders say which personas they hold (the personas' own files
don't change), so a persona deleted meanwhile is simply left out (and forgotten at the next change).

Kept apart from the personas' folder, where every file is a persona (personas.py).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from pydantic import BaseModel, Field

from .storage import JsonStore, StoredModel, new_id

FOLDERS_ID = 'persona-folders'
NAME_MAX = 80  # characters


class PersonaFolder(BaseModel):
    id: str
    name: str
    personas: list[str] = Field(default_factory=list)  # their ids, in no particular order


class PersonaFolders(StoredModel):
    id: str = FOLDERS_ID
    name: str = Field('', exclude=True)  # from StoredModel; unused
    folders: list[PersonaFolder] = Field(default_factory=list)


class FolderError(ValueError):
    """A folder name that can't be used, or a folder that's gone: the message says which."""


def folder_name(name: str) -> str:
    """The name as kept: spaces at the ends removed, runs of spaces made one. FolderError if it's empty."""
    name = ' '.join(name.split())
    if not name:
        raise FolderError('Give the folder a name.')
    if len(name) > NAME_MAX:
        raise FolderError(f'The name is too long: {NAME_MAX} characters at most.')
    return name


class PersonaFolderStore:
    def __init__(self, data_dir: str | Path, *, exists: Callable[[str], bool] | None = None) -> None:
        """`exists`: whether a persona still exists (PersonaStore.exists), to forget those deleted."""
        self._store = JsonStore(Path(data_dir), PersonaFolders.model_validate)
        self.path = self._store.path_for(FOLDERS_ID)
        self._exists = exists

    def _load(self) -> PersonaFolders:
        try:
            return self._store.get(FOLDERS_ID) or PersonaFolders()
        except ValueError:  # (a damaged file: no folders, until it's written again)
            return PersonaFolders()

    def _save(self, data: PersonaFolders) -> None:
        if self._exists is not None:  # (the personas deleted since: forgotten)
            for folder in data.folders:
                folder.personas = [p for p in folder.personas if self._exists(p)]
        self._store.save(data)

    # --- reading ------------------------------------------------------------------------------------

    def list(self) -> list[PersonaFolder]:
        """The folders, by name."""
        return sorted(self._load().folders, key=lambda f: (f.name.casefold(), f.id))

    def get(self, folder_id: str) -> PersonaFolder | None:
        return next((f for f in self._load().folders if f.id == folder_id), None)

    def folder_of(self, persona_id: str) -> PersonaFolder | None:
        return next((f for f in self.list() if persona_id in f.personas), None)

    def by_persona(self) -> dict[str, PersonaFolder]:
        """Each persona in a folder, with its folder."""
        return {persona_id: folder for folder in self.list() for persona_id in folder.personas}

    # --- changing -----------------------------------------------------------------------------------

    def create(self, name: str) -> PersonaFolder:
        """A new, empty folder. FolderError if the name is empty, too long, or another folder's."""
        name = folder_name(name)
        data = self._load()
        self._check_free(data, name)
        folder = PersonaFolder(id=new_id(name), name=name)
        data.folders.append(folder)
        self._save(data)
        return folder

    def rename(self, folder_id: str, name: str) -> PersonaFolder:
        name = folder_name(name)
        data = self._load()
        folder = self._find(data, folder_id)
        self._check_free(data, name, but=folder_id)
        folder.name = name
        self._save(data)
        return folder

    def delete(self, folder_id: str) -> int:
        """The folder goes; its personas stay, in no folder. How many it held (that still exist)."""
        data = self._load()
        folder = self._find(data, folder_id)
        held = len([p for p in folder.personas if self._exists is None or self._exists(p)])
        data.folders = [f for f in data.folders if f.id != folder_id]
        self._save(data)
        return held

    def put(self, persona_id: str, folder_id: str | None) -> None:
        """Put the persona in this folder (out of any other), or in none (None)."""
        data = self._load()
        target = self._find(data, folder_id) if folder_id else None
        for folder in data.folders:
            folder.personas = [p for p in folder.personas if p != persona_id]
        if target is not None:
            target.personas.append(persona_id)
        self._save(data)

    def forget(self, persona_ids: Iterable[str]) -> None:
        """Personas deleted: out of their folders."""
        gone = set(persona_ids)
        data = self._load()
        if any(gone & set(f.personas) for f in data.folders):
            for folder in data.folders:
                folder.personas = [p for p in folder.personas if p not in gone]
            self._save(data)

    @staticmethod
    def _find(data: PersonaFolders, folder_id: str) -> PersonaFolder:
        folder = next((f for f in data.folders if f.id == folder_id), None)
        if folder is None:
            raise FolderError('This folder was deleted meanwhile.')
        return folder

    @staticmethod
    def _check_free(data: PersonaFolders, name: str, *, but: str = '') -> None:
        taken = next((f for f in data.folders if f.name.casefold() == name.casefold() and f.id != but), None)
        if taken is not None:
            raise FolderError(f'There\'s already a folder named "{taken.name}".')
