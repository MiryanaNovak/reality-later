"""Image files (the pictures of personas and characters), stored next to the JSON files.

    data/personas/images/3f9a2c81d0b4e5a7.webp

Each image gets a name made from its content, so the same picture saved twice
is one file, and a duplicated persona simply shares its picture. An object
stores only that file name.

Uploaded images are shrunk to at most 1024 pixels on their longest side and
saved as WebP, which keeps them small for phones while staying sharp.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIZE = 1024
_NAME_RE = re.compile(r'[0-9a-f]{16,64}\.(webp|png|jpe?g|gif)')


class ImageStore:
    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def add(self, data: bytes) -> str:
        """Store an uploaded image and return its file name. Raises ValueError if it isn't an image."""
        try:
            with Image.open(io.BytesIO(data)) as image:
                image = ImageOps.exif_transpose(image)  # phone photos: apply the rotation they were taken with
                image.thumbnail((MAX_SIZE, MAX_SIZE))
                if image.mode not in ('RGB', 'RGBA'):
                    image = image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
                out = io.BytesIO()
                image.save(out, 'WEBP', quality=88, method=4)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
            raise ValueError('That file isn\'t an image this app can read (try PNG, JPEG or WebP).') from e

        content = out.getvalue()
        name = hashlib.sha256(content).hexdigest()[:16] + '.webp'
        path = self.folder / name
        if not path.exists():
            fd, tmp = tempfile.mkstemp(dir=self.folder, prefix='.upload.', suffix='.tmp')
            with os.fdopen(fd, 'wb') as f:
                f.write(content)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        return name

    def size(self, name: str) -> tuple[int, int]:
        """(width, height) of a stored image. Raises ValueError if it's gone."""
        path = self.path(name)
        if path is None:
            raise ValueError(f'No image {name!r}')
        with Image.open(path) as image:
            return image.size

    def crop(self, name: str, box: tuple[int, int, int, int], *, size: int) -> str:
        """Store the part (left, top, right, bottom) of an image, as a square of `size` pixels; returns its name."""
        path = self.path(name)
        if path is None:
            raise ValueError(f'No image {name!r}')
        with Image.open(path) as image:
            part = image.crop(box)
            if part.width > size:
                part = part.resize((size, size), Image.LANCZOS)
            out = io.BytesIO()
            part.save(out, 'PNG')
        return self.add(out.getvalue())

    def path(self, name: str) -> Path | None:
        """The file for `name`, or None if the name is invalid or the file is gone."""
        if not _NAME_RE.fullmatch(name or ''):
            return None
        path = self.folder / name
        return path if path.is_file() else None

    def delete(self, name: str) -> None:
        if path := self.path(name):
            path.unlink(missing_ok=True)
