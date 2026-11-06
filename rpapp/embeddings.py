"""Turning text into vectors ("embeddings") on this computer, for vector search (see vectors.py).

A vector is a list of numbers standing for what a text means: texts about the same thing get vectors
pointing the same way, even without a word in common. Their closeness (cosine similarity: from about
0, unrelated, to 1, the same) is what vector search compares.

The models
----------
embedding_models.json, beside this file: small models from Hugging Face, as ONNX files (run with
onnxruntime) with their tokenizer (Hugging Face's tokenizers library), each with its exact version and
the size and SHA-256 of each of its files (recorded by tools/pin_models.py). A model is downloaded once,
from Settings › Vector search, into the models folder (paths.Places.models), from one of SOURCES: every
file must have its recorded SHA-256, whichever source it came from, or the download is refused.

Long texts
----------
These models read so many tokens (word pieces) of a text, and ignore the rest: a long text is cut into
pieces of at most PIECE_TOKENS tokens, each turned into a vector of its own (pieces()).

onnxruntime, tokenizers and numpy are imported when a model is first used: the app starts without them,
and vector search says why it can't run if they're missing (runtime_problem()).
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import httpx
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)

CATALOG = Path(__file__).parent / 'embedding_models.json'
INSTALLED = 'installed.json'   # in a model's folder, once its files are checked: their SHA-256
PIECE_TOKENS = 256             # the longest piece of text turned into one vector (see the notes at the top)
OVERLAP_TOKENS = 32            # pieces overlap by this much, for what's said across their boundary
BATCH = 16                     # pieces run through the model at once
CHUNK = 1 << 16                # bytes read at a time when downloading


@dataclass(frozen=True)
class Source:
    name: str
    url: str  # files are at <url>/<repo>/resolve/<revision>/<path>, as on Hugging Face


SOURCES = {
    'huggingface': Source('Hugging Face', 'https://huggingface.co'),
    'hf-mirror': Source('HF-Mirror (where Hugging Face is out of reach)', 'https://hf-mirror.com'),
}


class ModelFile(BaseModel):
    path: str
    size: int = 0     # bytes (0: not recorded yet)
    sha256: str = ''  # ('': not recorded yet)


class EmbeddingModel(BaseModel):
    key: str
    name: str
    languages: str
    multilingual: bool
    about: str = ''
    license: str
    repo: str                       # on Hugging Face
    revision: str = ''              # the exact version (a commit): '' until pinned
    files: list[ModelFile]
    pooling: Literal['mean', 'cls']  # how the token vectors make the text's: their mean, or the first one's
    max_tokens: int
    query_prefix: str = ''          # some models want "query: " before what's searched for...
    passage_prefix: str = ''        # ... and "passage: " before what's searched in
    threshold: float = Field(ge=0, le=1)  # the default closeness needed (each model has its own scale)
    approx_mb: int                  # for the settings, until the sizes are recorded

    @property
    def pinned(self) -> bool:
        """Whether its version and files are recorded: it can be downloaded."""
        return bool(self.revision) and all(f.sha256 and f.size for f in self.files)

    @property
    def megabytes(self) -> int:
        recorded = sum(f.size for f in self.files)
        return round(recorded / 1_000_000) if recorded else self.approx_mb

    @property
    def label(self) -> str:
        return f'{self.name} · {self.languages} · {self.megabytes} MB'

    def file(self, suffix: str) -> ModelFile:
        return next(f for f in self.files if f.path.endswith(suffix))


def load_catalog(path: Path = CATALOG) -> dict[str, EmbeddingModel]:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return {m.key: m for m in (EmbeddingModel.model_validate(item) for item in data['models'])}


@functools.cache
def catalog() -> dict[str, EmbeddingModel]:
    return load_catalog()


def runtime_problem() -> str:
    """Why models can't run on this computer ('' if they can): a missing library."""
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except Exception as e:  # (ImportError, or a library that fails to load)
        return f'a library it needs is missing ({e})'
    return ''


# --- installing ---------------------------------------------------------------------------------

class DownloadError(Exception):
    """The download failed or was refused: the message says why."""


def model_folder(models_dir: Path, model: EmbeddingModel) -> Path:
    return Path(models_dir) / model.key


def is_installed(models_dir: Path, model: EmbeddingModel) -> bool:
    """Whether its files are there, checked, and the ones recorded now (a new version isn't installed)."""
    folder = model_folder(models_dir, model)
    try:
        checked = json.loads((folder / INSTALLED).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return model.pinned and checked == {f.path: f.sha256 for f in model.files} and \
        all((folder / f.path).is_file() and (folder / f.path).stat().st_size == f.size for f in model.files)


def uninstall(models_dir: Path, model: EmbeddingModel) -> None:
    for folder in (model_folder(models_dir, model), _partial(models_dir, model)):
        shutil.rmtree(folder, ignore_errors=True)


def _partial(models_dir: Path, model: EmbeddingModel) -> Path:
    return Path(models_dir) / f'.{model.key}.partial'


async def download(model: EmbeddingModel, models_dir: Path, source: str = 'huggingface', *,
                   on_progress: Callable[[int, int], None] | None = None,
                   client: httpx.AsyncClient | None = None, base_url: str | None = None) -> None:
    """Download the model's files and check them; then it's installed. `on_progress(done, total)` in bytes.

    Raises DownloadError. Files go to a hidden folder first: an interrupted download installs nothing.
    `base_url`: instead of the source's (tests).
    """
    if not model.pinned:
        raise DownloadError(f'{model.name} can\'t be downloaded yet: its files\' fingerprints aren\'t recorded '
                            '(tools/pin_models.py)')
    site = base_url or SOURCES[source].url
    partial = _partial(models_dir, model)
    shutil.rmtree(partial, ignore_errors=True)
    total, done = sum(f.size for f in model.files), 0
    owned = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(30, read=120), follow_redirects=True)
    try:
        for f in model.files:
            target = partial / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            url = f'{site.rstrip("/")}/{model.repo}/resolve/{model.revision}/{f.path}'
            try:
                async with client.stream('GET', url) as answer:
                    if answer.status_code != 200:
                        raise DownloadError(f'{f.path}: the server answered {answer.status_code} ({url})')
                    with open(target, 'wb') as out:
                        async for chunk in answer.aiter_bytes(CHUNK):
                            out.write(chunk)
                            digest.update(chunk)
                            done += len(chunk)
                            if on_progress:
                                on_progress(done, total)
            except httpx.HTTPError as e:
                raise DownloadError(f'{f.path}: {e or type(e).__name__} ({url})') from None
            if digest.hexdigest() != f.sha256:
                raise DownloadError(f'{f.path} isn\'t the expected file (its SHA-256 differs): refused')
        (partial / INSTALLED).write_text(json.dumps({f.path: f.sha256 for f in model.files}, indent=2),
                                         encoding='utf-8')
        folder = model_folder(models_dir, model)
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(partial, folder)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    finally:
        if owned:
            await client.aclose()


# --- running ------------------------------------------------------------------------------------

class Embedder:
    """A model, loaded (from its folder): texts in, vectors out. Thread-safe; slow calls belong in a thread."""

    def __init__(self, folder: Path, model: EmbeddingModel, *, threads: int | None = None) -> None:
        import onnxruntime
        from tokenizers import Tokenizer

        self.model = model
        folder = Path(folder)
        self.tokenizer = Tokenizer.from_file(str(folder / model.file('tokenizer.json').path))
        self.tokenizer.no_padding()
        self.tokenizer.no_truncation()
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads or max(1, min(4, os.cpu_count() or 1))  # (leave some for the rest)
        options.log_severity_level = 3
        self.session = onnxruntime.InferenceSession(str(folder / model.file('.onnx').path), options,
                                                    providers=['CPUExecutionProvider'])
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.piece_tokens = min(PIECE_TOKENS, model.max_tokens) - 2  # (minus the start and end tokens)

    def pieces(self, text: str, *, kind: Literal['query', 'passage'] = 'passage') -> list[str]:
        """The text, cut into pieces the model reads whole (see the notes at the top)."""
        text = text.strip()
        if not text:
            return []
        prefix = self._prefix(kind)
        room = self.piece_tokens - len(self.tokenizer.encode(prefix, add_special_tokens=False).ids)
        offsets = self.tokenizer.encode(text, add_special_tokens=False).offsets
        if len(offsets) <= room:
            return [text]
        pieces, start = [], 0
        while start < len(offsets):
            end = min(start + room, len(offsets))
            pieces.append(text[offsets[start][0]:offsets[end - 1][1]])
            if end == len(offsets):
                break
            start = end - OVERLAP_TOKENS
        return pieces

    def embed(self, texts: list[str], *, kind: Literal['query', 'passage'] = 'passage') -> np.ndarray:
        """One vector per text (of length 1), each read up to the model's limit: see pieces()."""
        import numpy as np

        prefix = self._prefix(kind)
        vectors = []
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))  # (similar lengths together: less padding)
        for start in range(0, len(order), BATCH):
            batch = [prefix + texts[i] for i in order[start:start + BATCH]]
            vectors.append(self._run(batch))
        if not vectors:
            return np.zeros((0, 0), dtype=np.float32)
        result = np.empty((len(texts), vectors[0].shape[1]), dtype=np.float32)
        result[order] = np.concatenate(vectors)
        return result

    def embed_pieces(self, text: str, *, kind: Literal['query', 'passage'] = 'passage') -> np.ndarray:
        """A vector for each piece of the text (none for an empty text)."""
        import numpy as np

        pieces = self.pieces(text, kind=kind)
        return self.embed(pieces, kind=kind) if pieces else np.zeros((0, 0), dtype=np.float32)

    def _prefix(self, kind: str) -> str:
        return self.model.query_prefix if kind == 'query' else self.model.passage_prefix

    def _run(self, batch: list[str]) -> np.ndarray:
        import numpy as np

        limit = self.model.max_tokens
        # Too long for the model: cut, keeping the end token (the tokenizer's own setting would be shared state)
        rows = [e.ids if len(e.ids) <= limit else e.ids[:limit - 1] + e.ids[-1:]
                for e in self.tokenizer.encode_batch(batch)]
        width = max(len(r) for r in rows)
        ids = np.zeros((len(batch), width), dtype=np.int64)
        mask = np.zeros((len(batch), width), dtype=np.int64)
        for row, r in enumerate(rows):
            ids[row, :len(r)] = r
            mask[row, :len(r)] = 1
        feed = {'input_ids': ids, 'attention_mask': mask}
        if 'token_type_ids' in self.inputs:
            feed['token_type_ids'] = np.zeros_like(ids)
        output = self.session.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
        if output.ndim == 3:  # a vector per token: make the text's
            if self.model.pooling == 'cls':
                output = output[:, 0]
            else:
                weights = mask[:, :, None].astype(np.float32)
                output = (output * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-9)
        norms = np.linalg.norm(output, axis=1, keepdims=True)
        return (output / np.maximum(norms, 1e-12)).astype(np.float32)


async def load(folder: Path, model: EmbeddingModel) -> Embedder:
    """The model, loaded in a thread (it takes a moment)."""
    return await asyncio.to_thread(Embedder, folder, model)
