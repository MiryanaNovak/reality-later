"""What personas and characters have in common.

A Profile has a title (only shown in this app), a name (used in chats), a
picture, a description and links to lore books. Persona and Character extend
it with their own fields; ProfileStore saves them with their pictures:

    data/<folder>/<id>.json
    data/<folder>/images/<picture>.webp

A lore book is referenced by its id, and its name is remembered too: if the
book is deleted later, the profile can still say which one is missing.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, TypeVar

from pydantic import BaseModel, Field

from .images import ImageStore
from .storage import JsonStore, StoredModel


class LoreBookRef(BaseModel):
    """A link to a lore book: its id, plus its name as it was last seen."""
    id: str
    name: str


class Frame(BaseModel):
    """The square of a picture that's shown: its left and top edges, as fractions of the picture's width
    and height, and its side, as a fraction of the picture's shorter side (1: as big as it fits)."""
    x: float = Field(0.0, ge=0, le=1)
    y: float = Field(0.0, ge=0, le=1)
    size: float = Field(1.0, gt=0, le=1)

    def box(self, width: int, height: int) -> tuple[int, int, int, int]:
        """The square in pixels (left, top, right, bottom), kept inside the picture."""
        side = max(1, round(self.size * min(width, height)))
        left = min(max(0, round(self.x * width)), width - side)
        top = min(max(0, round(self.y * height)), height - side)
        return left, top, left + side, top + side


def default_frame(width: int, height: int) -> Frame:
    """The biggest square: from the top of a tall picture (that's where faces usually are), centered otherwise."""
    if height > width:
        return Frame(x=0, y=0, size=1)
    return Frame(x=(width - height) / 2 / width, y=0, size=1)


FRAMED_SIZE = 512  # pixels: the framed square shown everywhere (the biggest place shows it at 180 pixels)


class Profile(StoredModel):
    label_field: ClassVar[str] = 'title'

    title: str = 'Untitled'
    name: str = ''
    description: str = ''
    image: str = ''  # file name in the store's image folder: the square shown everywhere; '' = no picture
    image_full: str = ''  # the whole picture, which `image` is a framed square of ('' in files from before:
    image_frame: Frame | None = None  # then `image` is the whole picture, shown from its center)
    created_at: datetime | None = None  # when it was added (None in files from before: see added_at)
    lorebooks: list[LoreBookRef] = Field(default_factory=list)

    def add_lorebook(self, book_id: str, name: str) -> None:
        if not any(ref.id == book_id for ref in self.lorebooks):
            self.lorebooks = [*self.lorebooks, LoreBookRef(id=book_id, name=name)]

    def remove_lorebook(self, book_id: str) -> None:
        self.lorebooks = [ref for ref in self.lorebooks if ref.id != book_id]

    def replace_lorebook(self, old_id: str, new_id: str, name: str) -> None:
        """Point a link at another book (e.g. the same book imported again), keeping its place in the list."""
        self.lorebooks = [LoreBookRef(id=new_id, name=name) if ref.id == old_id else ref for ref in self.lorebooks
                          if not (ref.id == new_id and old_id != new_id)]

    def missing_lorebooks(self, exists: Callable[[str], bool]) -> list[LoreBookRef]:
        return [ref for ref in self.lorebooks if not exists(ref.id)]


P = TypeVar('P', bound=Profile)


class ProfileStore(JsonStore[P]):
    """Profiles of one kind in a folder, with their pictures in its images/ subfolder."""

    def __init__(self, folder: str | Path, model: type[P]) -> None:
        super().__init__(folder, model.model_validate)
        self.images = ImageStore(self.folder / 'images')

    def added_at(self, item: P) -> datetime:
        """When a profile was added: its created_at, or for files from before that, when its file was last saved."""
        if item.created_at is not None:
            return item.created_at
        try:
            return datetime.fromtimestamp(self.path_for(item.id).stat().st_mtime, timezone.utc)
        except OSError:
            return datetime.min.replace(tzinfo=timezone.utc)

    # -- pictures ----------------------------------------------------------------------

    def set_picture(self, item: P, data: bytes) -> None:
        """A new picture for `item` (not saved yet): kept whole, and framed by default (see default_frame).

        Raises ValueError if it isn't an image.
        """
        old = (item.image, item.image_full)
        full = self.images.add(data)
        self._frame(item, full, default_frame(*self.images.size(full)))
        self._release_later(item, *old)

    def frame_picture(self, item: P, frame: Frame) -> None:
        """Show another square of `item`'s picture (not saved yet)."""
        full = item.image_full or item.image
        if not full:
            return
        old = item.image if item.image_full else ''  # (from before framing: the old image becomes the whole one)
        self._frame(item, full, frame)
        self._release_later(item, old)

    def remove_picture(self, item: P) -> None:
        old = (item.image, item.image_full)
        item.image, item.image_full, item.image_frame = '', '', None
        self._release_later(item, *old)

    def _frame(self, item: P, full: str, frame: Frame) -> None:
        item.image = self.images.crop(full, frame.box(*self.images.size(full)), size=FRAMED_SIZE)
        item.image_full, item.image_frame = full, frame

    def _release_later(self, item: P, *names: str) -> None:
        """Delete these files once `item` is saved without them (unless another profile uses them)."""
        self._released = [*getattr(self, '_released', []), *(n for n in names if n)]

    def save(self, item: P) -> Path:
        path = super().save(item)
        released, self._released = getattr(self, '_released', []), []
        for name in released:
            self.release_image(name)
        return path

    def release_image(self, name: str) -> None:
        """Delete a picture file unless a profile of this store still uses it."""
        if name and not any(name in (p.image, p.image_full) for p in self.list()):
            self.images.delete(name)

    def using_lorebook(self, book_id: str) -> list[P]:
        """The profiles that link to this lore book, sorted by title."""
        return [p for p in self.list() if any(ref.id == book_id for ref in p.lorebooks)]

    def lorebook_usage(self) -> Counter[str]:
        """How many profiles link to each lore book: {book id: count}."""
        return Counter(ref.id for p in self.list() for ref in {r.id: r for r in p.lorebooks}.values())

    def delete(self, item_id: str) -> None:
        profile = self.get(item_id)
        super().delete(item_id)
        if profile:
            self.release_image(profile.image)
            self.release_image(profile.image_full)
