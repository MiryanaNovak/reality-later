"""Folders of personas, and of characters, to keep them in order: nothing else (the AI never knows of them).
In data/persona-folders.json and data/character-folders.json:

    {"id": "persona-folders", "format_version": 1,
     "folders": [{"id": "fantasy-3f9a2c", "name": "Fantasy", "members": ["mira-abc51b", "wren-77e0d2"]}]}

    folders = FolderStore(data_dir, 'personas', exists=PersonaStore(data_dir).exists)

One is in one folder at most, or in none; folders hold no folders. Deleting a folder keeps what it holds:
just in no folder any more. The folders say what they hold (the personas' and characters' own files don't
change), so one deleted meanwhile is simply left out (and forgotten at the next change).

Kept apart from the personas' and characters' folders, where every file is one of them.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field

from .storage import JsonStore, StoredModel, new_id

Kind = Literal['personas', 'characters']
FILES: dict[str, str] = {'personas': 'persona-folders', 'characters': 'character-folders'}  # (their ids, and names)
NAME_MAX = 80  # characters


class Folder(BaseModel):
    id: str
    name: str
    # Their ids, in no particular order ("personas" in the first version's files: read the same)
    members: list[str] = Field(default_factory=list, validation_alias=AliasChoices('members', 'personas'))


class Folders(StoredModel):
    id: str = FILES['personas']
    name: str = Field('', exclude=True)  # from StoredModel; unused
    folders: list[Folder] = Field(default_factory=list)


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


class FolderStore:
    def __init__(self, data_dir: str | Path, kind: Kind, *, exists: Callable[[str], bool] | None = None) -> None:
        """`kind`: what the folders hold. `exists`: whether one of them still exists (its store's exists), to
        forget those deleted."""
        self._id = FILES[kind]
        self._store = JsonStore(Path(data_dir), Folders.model_validate)
        self.path = self._store.path_for(self._id)
        self._exists = exists

    def _load(self) -> Folders:
        try:
            return self._store.get(self._id) or Folders(id=self._id)
        except ValueError:  # (a damaged file: no folders, until it's written again)
            return Folders(id=self._id)

    def _save(self, data: Folders) -> None:
        if self._exists is not None:  # (those deleted since: forgotten)
            for folder in data.folders:
                folder.members = [m for m in folder.members if self._exists(m)]
        self._store.save(data)

    # --- reading ------------------------------------------------------------------------------------

    def list(self) -> list[Folder]:
        """The folders, by name."""
        return sorted(self._load().folders, key=lambda f: (f.name.casefold(), f.id))

    def get(self, folder_id: str) -> Folder | None:
        return next((f for f in self._load().folders if f.id == folder_id), None)

    def folder_of(self, member_id: str) -> Folder | None:
        return next((f for f in self.list() if member_id in f.members), None)

    def by_member(self) -> dict[str, Folder]:
        """Each one in a folder, with its folder."""
        return {member_id: folder for folder in self.list() for member_id in folder.members}

    # --- changing -----------------------------------------------------------------------------------

    def create(self, name: str) -> Folder:
        """A new, empty folder. FolderError if the name is empty, too long, or another folder's."""
        name = folder_name(name)
        data = self._load()
        self._check_free(data, name)
        folder = Folder(id=new_id(name), name=name)
        data.folders.append(folder)
        self._save(data)
        return folder

    def rename(self, folder_id: str, name: str) -> Folder:
        name = folder_name(name)
        data = self._load()
        folder = self._find(data, folder_id)
        self._check_free(data, name, but=folder_id)
        folder.name = name
        self._save(data)
        return folder

    def delete(self, folder_id: str) -> int:
        """The folder goes; what it holds stays, in no folder. How many it held (that still exist)."""
        data = self._load()
        folder = self._find(data, folder_id)
        held = len([p for p in folder.members if self._exists is None or self._exists(p)])
        data.folders = [f for f in data.folders if f.id != folder_id]
        self._save(data)
        return held

    def put(self, member_id: str, folder_id: str | None) -> None:
        """Put one in this folder (out of any other), or in none (None)."""
        data = self._load()
        target = self._find(data, folder_id) if folder_id else None
        for folder in data.folders:
            folder.members = [p for p in folder.members if p != member_id]
        if target is not None:
            target.members.append(member_id)
        self._save(data)

    def forget(self, member_ids: Iterable[str]) -> None:
        """Those deleted: out of their folders."""
        gone = set(member_ids)
        data = self._load()
        if any(gone & set(f.members) for f in data.folders):
            for folder in data.folders:
                folder.members = [p for p in folder.members if p not in gone]
            self._save(data)

    @staticmethod
    def _find(data: Folders, folder_id: str) -> Folder:
        folder = next((f for f in data.folders if f.id == folder_id), None)
        if folder is None:
            raise FolderError('This folder was deleted meanwhile.')
        return folder

    @staticmethod
    def _check_free(data: Folders, name: str, *, but: str = '') -> None:
        taken = next((f for f in data.folders if f.name.casefold() == name.casefold() and f.id != but), None)
        if taken is not None:
            raise FolderError(f'There\'s already a folder named "{taken.name}".')
