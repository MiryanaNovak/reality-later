"""Where vector search keeps its vectors (see vectors.py), and deleting them with what they belong to.

    <data>/vectors/<model key>/lorebooks/<lore book id>.json
    <data>/vectors/<model key>/memories/<conversation id>.json

Each file: {"model": key, "dims": 384, "vectors": {"<fingerprint of a text>": "<its vectors>"}}, the
vectors of a text as base64 float32, a row per piece of it. A text's fingerprint is its SHA-256: a
vector belongs to a text, not to an entry, so whatever an entry's text becomes (edited, or undone), the
right vector is found if it's there, and made if it isn't.

Only paths and files here (no numpy): the stores delete a lore book's or a conversation's vectors with it.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

KINDS = ('lorebooks', 'memories')


def vectors_dir(data_dir: str | Path) -> Path:
    return Path(data_dir) / 'vectors'


def vector_file(data_dir: str | Path, model_key: str, kind: str, owner: str) -> Path:
    return vectors_dir(data_dir) / model_key / kind / f'{owner}.json'


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]


def delete_vectors(data_dir: str | Path, kind: str, owner: str) -> None:
    """A lore book's (kind 'lorebooks') or a conversation's memories' ('memories'), for every model."""
    for path in vectors_dir(data_dir).glob(f'*/{kind}/{owner}.json'):
        path.unlink(missing_ok=True)


def delete_model_vectors(data_dir: str | Path, model_key: str) -> None:
    shutil.rmtree(vectors_dir(data_dir) / model_key, ignore_errors=True)
