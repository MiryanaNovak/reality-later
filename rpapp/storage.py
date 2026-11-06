"""File-based persistence: one JSON file per object, one folder per object type.

    data/
      connections/
        openrouter-deepseek-3f9a2c.json
        local-lm-studio-81b0de.json
      characters/          (later, same mechanism)
      chats/               (later)

One file per object keeps things easy to inspect, diff, back up, delete or
duplicate by hand, and a broken file only affects that one object.

To store a new kind of object:
  1. subclass StoredModel and add your fields,
  2. create a JsonStore(folder, YourClass.model_validate).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

log = logging.getLogger(__name__)

_ID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')


def new_id(name: str) -> str:
    """Readable id made from the name: 'My Claude' -> 'my-claude-3f9a2c'.

    The id never changes afterwards, so renaming an object doesn't break the
    other objects that refer to it (a chat pointing at its connection, say).
    """
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:40] or 'item'
    return f'{slug}-{secrets.token_hex(3)}'


class StoredModel(BaseModel):
    """Base class for everything the app saves to disk.

    Pydantic does the serialization and validates what it loads, so a typo in a
    hand-edited file gives a clear error instead of a crash later on.

    Changing the file format later: bump `format_version` in the subclass and
    add a `@model_validator(mode='before')` that upgrades old dicts.
    """
    model_config = ConfigDict(
        extra='ignore',            # unknown keys in a file are dropped instead of failing
        validate_assignment=True,  # `obj.field = bad_value` raises right away
    )

    # The field that names the object in lists, menus and new ids ('name' unless a subclass says otherwise).
    label_field: ClassVar[str] = 'name'

    id: str = ''
    name: str = 'Untitled'
    format_version: int = 1

    @property
    def label(self) -> str:
        return str(getattr(self, self.label_field) or '')

    def model_post_init(self, context: Any, /) -> None:
        if not self.id:
            self.id = new_id(self.label)


T = TypeVar('T', bound=StoredModel)

_PENDING: dict[Path, dict[str, tuple[asyncio.TimerHandle, Any]]] = {}
_ON_SAVED: dict[Path, dict[str, Callable[[Any], None]]] = {}
_ON_DELETED: dict[Path, dict[str, Callable[[str], None]]] = {}


def _call(hooks: dict[str, Callable[[Any], None]], arg: Any) -> None:
    for name, hook in list(hooks.items()):
        try:
            hook(arg)
        except Exception:
            log.exception('The %s hook failed', name)


class JsonStore(Generic[T]):
    """Reads and writes one folder of JSON files, one file per object.

    `loader` turns a dict back into an object. Usually that's
    `SomeModel.model_validate`; for a family of classes (like connections) it's
    a function that picks the right class from the dict.

    Reads go to disk every time, so edits made by hand while the app runs show
    up on the next read. That's plenty fast for hundreds of small files.
    """

    def __init__(self, folder: str | Path, loader: Callable[[dict], T]) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._loader = loader
        # Saves waiting to happen (see save_soon), shared by every store of the same folder.
        self._pending: dict[str, tuple[asyncio.TimerHandle, T]] = _PENDING.setdefault(self.folder.resolve(), {})
        # Called after an object is saved, or deleted (by id), whichever store of the folder did it.
        # By name: registering the same one again replaces it. (Vector search uses them: vectors.py.)
        self.on_saved: dict[str, Callable[[T], None]] = _ON_SAVED.setdefault(self.folder.resolve(), {})
        self.on_deleted: dict[str, Callable[[str], None]] = _ON_DELETED.setdefault(self.folder.resolve(), {})

    def path_for(self, item_id: str) -> Path:
        if not _ID_RE.fullmatch(item_id):
            raise ValueError(f'Invalid id {item_id!r}')
        return self.folder / f'{item_id}.json'

    def load(self, item_id: str) -> T:
        """Load one object. Raises FileNotFoundError or ValueError (bad content)."""
        self.flush()
        return self._load_file(self.path_for(item_id))

    def get(self, item_id: str) -> T | None:
        """Like load(), but returns None if the file doesn't exist."""
        try:
            return self.load(item_id)
        except FileNotFoundError:
            return None

    def list(self) -> list[T]:
        """All objects in the folder, sorted by label. Unreadable files are skipped and logged."""
        self.flush()
        items = []
        for path in sorted(self.folder.glob('*.json')):
            try:
                items.append(self._load_file(path))
            except (OSError, ValueError) as e:  # pydantic's ValidationError is a ValueError
                log.warning('Skipping %s: %s', path, e)
        return sorted(items, key=lambda item: item.label.lower())

    def save(self, item: T) -> Path:
        """Write the object to <folder>/<id>.json.

        The write is atomic: the data goes to a temporary file that then replaces
        the old one, so a crash mid-save never leaves a half-written file.
        Files are created readable by your user only (they can hold API keys).
        """
        path = self.path_for(item.id)
        self._cancel_pending(item.id)
        data = item.model_dump(mode='json')
        # Put the identifying keys first so the files are easy to scan.
        data = {k: data.pop(k) for k in ('kind', 'id', 'title', 'name') if k in data} | data
        text = json.dumps(data, indent=2, ensure_ascii=False) + '\n'

        fd, tmp = tempfile.mkstemp(dir=self.folder, prefix=f'.{item.id}.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        _call(self.on_saved, item)
        return path

    def delete(self, item_id: str) -> None:
        self._cancel_pending(item_id)
        self.path_for(item_id).unlink(missing_ok=True)
        _call(self.on_deleted, item_id)

    def exists(self, item_id: str) -> bool:
        return _ID_RE.fullmatch(item_id) is not None and (item_id in self._pending or self.path_for(item_id).exists())

    # -- delayed saving, for objects edited keystroke by keystroke ------------------

    def save_soon(self, item: T, delay: float = 0.5) -> None:
        """Save after `delay` seconds without further calls for the same object.

        Typing in a big lore book then rewrites its file once per pause instead
        of once per keystroke. Every read from this store first writes pending
        saves, so nothing reads stale data. Call flush() before the app exits.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # not inside the app (a script, a test): save right away
            self.save(item)
            return
        self._cancel_pending(item.id)
        self._pending[item.id] = (loop.call_later(delay, self._save_pending, item.id), item)

    def flush(self) -> None:
        """Write all pending saves now."""
        for item_id in list(self._pending):
            self._save_pending(item_id)

    def _save_pending(self, item_id: str) -> None:
        handle, item = self._pending.pop(item_id, (None, None))
        if item is None:
            return
        handle.cancel()
        try:
            self.save(item)
        except OSError:
            log.exception('Could not save %s', item_id)

    def _cancel_pending(self, item_id: str) -> None:
        handle, _ = self._pending.pop(item_id, (None, None))
        if handle is not None:
            handle.cancel()

    def duplicate(self, item: T, label: str | None = None) -> T:
        """Save a copy of `item` under a new id and return it."""
        self.flush()
        label = label or f'{item.label} (copy)'
        update = {'id': new_id(label), item.label_field: label}
        if 'created_at' in type(item).model_fields and getattr(item, 'created_at', None) is not None:
            update['created_at'] = datetime.now(timezone.utc)  # a copy is added now, not when its original was
        copy = item.model_copy(update=update, deep=True)
        self.save(copy)
        return copy

    def import_data(self, data: dict, *, file_name: str = '') -> T:
        """Validate a dict (from an uploaded file, say) and save it as a new object.

        Keeps the id from the data, or else from the file name, when no saved
        object uses it yet; otherwise picks a new id. An import never overwrites
        anything. Raises ValueError if the data isn't valid.
        """
        if not isinstance(data, dict):
            raise ValueError('expected a JSON object at the top level')
        label_field = getattr(self._loader, '__self__', StoredModel).label_field  # Model.model_validate -> Model
        if not data.get(label_field) and file_name:
            data = {**data, label_field: Path(file_name).stem}  # e.g. SillyTavern files, named by their file
        candidates = [str(data.get('id') or ''), Path(file_name).stem]
        free = [c for c in candidates if _ID_RE.fullmatch(c) and not self.exists(c)]
        data = {**data, 'id': free[0] if free else new_id(str(data.get(label_field) or 'imported'))}
        item = self._loader(data)
        self.save(item)
        return item

    def _load_file(self, path: Path) -> T:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('expected a JSON object at the top level')
        # The file name is the source of truth for the id, so copying
        # a file to a new name is a quick way to duplicate an object.
        data['id'] = path.stem
        return self._loader(data)
