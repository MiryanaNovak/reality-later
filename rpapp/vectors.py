"""Vector search: memories and lore book entries picked by meaning too, not only by their keywords.

With it on (Settings › Vector search: for memories, for lore books, or both), an entry also comes into
the prompt when it's close enough in meaning to the last messages, keywords or not: a memory about
Brann the ferryman comes back when the chat talks about "the old man at the river crossing". Keywords
still work as before; entries found by meaning come after theirs, in the same budget (see lore.py).

What's compared
---------------
Each entry's text (its title, then its content) and each of the last messages (as many as keywords
look at: Settings › Lore books, "Messages to scan", or the entry's own) are turned into vectors by a
model running on this computer (embeddings.py). Without the character's and your persona's names: they're
in nearly every message and memory, and would make everything look related (topic_text()). An entry's closeness is its best one to any piece of
those messages: from 0, unrelated, to 1. Close enough (the threshold, per model): it's in.

When texts are vectorized
-------------------------
A text is vectorized once: the vector is kept under its fingerprint (vector_files.py), so a text that
hasn't changed is never done again, whatever happened around it.

- Before every prompt (a reply, another version, extending, continuing the scene): whatever isn't
  done yet, for the lore books in play and the chat's memories (prepare()). The reply waits: the chat
  shows "Vectorizing…" meanwhile (only when there's something to do, or the model to load).
- After memories are generated: the new and changed ones, before you post (with the pending update).
- In the background, a moment after a lore book or memories are saved (edited, imported, undone), and
  when a chat is opened: so that posting rarely has anything left to do (soon()).

A lore book's vectors serve every chat it's in. A memories file keeps the vectors of the memories as
they are, of the pending update, and of the versions kept to undo updates: undoing is free.

If it can't be used (the model isn't downloaded, a library is missing, an error), the reply goes on
with keywords only, and says why (Meaning.note, shown in the chat and the prompt view).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
from collections import OrderedDict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from .embeddings import Embedder, EmbeddingModel, catalog, is_installed, load, model_folder, runtime_problem
from .lore import Closeness, Meaning
from .vector_files import fingerprint, vector_file

if TYPE_CHECKING:
    import numpy as np

    from .conversations import Conversation
    from .lorebooks import LoreBook, LoreEntry
    from .memory import Memories, MemoryStore
    from .settings import Settings, SettingsStore
    from .storage import JsonStore

log = logging.getLogger(__name__)

QUERY_CACHE = 512   # messages whose vectors are kept in memory (a reply needs the last few again)
SOON = 2.0          # seconds after a save before vectorizing in the background (saves come in bursts)

_MACROS = re.compile(r'\{\{char\}\}|<BOT>|\{\{user\}\}|<USER>', re.I)


# --- the texts ------------------------------------------------------------------------------------

def entry_text(entry: LoreEntry) -> str:
    """What's vectorized of an entry: its title, then its content, without {{char}} and {{user}} (names say
    nothing of what it's about, see topic_text(); and the same lore book serves chats with other names)."""
    title, content = (entry.comment or '').strip(), (entry.content or '').strip()
    text = f'{title}\n{content}' if title and content and title not in content else content or title
    return _MACROS.sub('', text)


def topic_text(text: str, names: Iterable[str]) -> str:
    """A message without these names (the character's, your persona's: see the notes at the top)."""
    for name in sorted({n.strip() for n in names if n and n.strip()}, key=len, reverse=True):
        text = re.sub(rf'(?<!\w){re.escape(name)}(?:\'s)?(?!\w)', '', text, flags=re.I)
    return text


def lore_texts(book: LoreBook) -> dict[int, str]:
    """The entries that may come in by meaning: on, not always in anyway, not set to come in only by keyword,
    and not empty. uid -> text."""
    return {e.uid: text for e in book.entries.values()
            if not e.disable and not e.constant and e.onlyBy != 'keywords' and (text := entry_text(e))}


def memory_texts(memories: Memories) -> tuple[dict[int, str], list[str], list[str]]:
    """The memories in use (uid -> text); those of the pending update (vectorized ahead: they're applied
    when you post); and those of the versions kept to undo updates (their vectors are kept, if there)."""
    now = lore_texts(memories.book)
    ahead = list(lore_texts(memories.pending.proposed).values()) if memories.pending else []
    kept = [text for step in memories.history for e in step.before.values() if (text := entry_text(e))]
    return now, ahead, kept


# --- the files ------------------------------------------------------------------------------------

class VectorFile:
    """The vectors of a lore book or a chat's memories, for one model (see vector_files.py)."""

    def __init__(self, path: Path, model: EmbeddingModel) -> None:
        self.path, self.model = path, model

    def load(self) -> dict[str, np.ndarray]:
        import numpy as np

        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('model') != self.model.key:
                return {}
            dims = int(data['dims'])
            return {fp: np.frombuffer(base64.b64decode(b), dtype=np.float32).reshape(-1, dims)
                    for fp, b in data['vectors'].items()}
        except (OSError, ValueError, KeyError, TypeError):
            return {}  # none yet, or broken: made again

    def fingerprints(self) -> set[str]:
        try:
            return set(json.loads(self.path.read_text(encoding='utf-8'))['vectors'])
        except (OSError, ValueError, KeyError, TypeError):
            return set()

    def save(self, vectors: dict[str, np.ndarray]) -> None:
        dims = next((v.shape[1] for v in vectors.values() if v.size), 0)
        data = {'model': self.model.key, 'dims': dims,
                'vectors': {fp: base64.b64encode(v.astype('float32').tobytes()).decode('ascii')
                            for fp, v in sorted(vectors.items())}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f'.{self.path.stem}.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def closeness(entries: dict[tuple[str, int], np.ndarray], messages: list[np.ndarray]) -> dict[tuple[str, int], list[float]]:
    """For each entry, its closeness to each message: the best between any piece of either. (Vectors are of
    length 1: their dot product is their cosine similarity.)"""
    return {key: [float((vectors @ m.T).max()) if vectors.size and m.size else 0.0 for m in messages]
            for key, vectors in entries.items()}


# --- the service ----------------------------------------------------------------------------------

class Vectorizer:
    """Vector search for the app: one per data folder (see the notes at the top)."""

    def __init__(self, data_dir: str | Path, models_dir: str | Path, *, settings: SettingsStore,
                 lore_books: JsonStore[LoreBook], memories: MemoryStore) -> None:
        self.data_dir, self.models_dir = Path(data_dir), Path(models_dir)
        self.settings, self.lore_books, self.memories = settings, lore_books, memories
        self._embedder: Embedder | None = None
        self._loading = asyncio.Lock()
        self._working = asyncio.Lock()             # one vectorizing at a time: it takes the processor
        self._owners: dict[tuple[str, str], asyncio.Lock] = {}
        self._queries: OrderedDict[str, np.ndarray] = OrderedDict()
        self._soon: dict[tuple[str, str], asyncio.Task] = {}

    # -- whether it can run

    def problem(self, settings: Settings) -> str:
        """Why it can't run with these settings ('' if it can)."""
        model = catalog().get(settings.vectors.model)
        if model is None:
            return f'its model ("{settings.vectors.model}") isn\'t one the app knows: choose another one'
        if problem := runtime_problem():
            return problem
        if not is_installed(self.models_dir, model):
            return f'its model ({model.name}) isn\'t downloaded: Settings › Vector search'
        return ''

    def model(self, settings: Settings) -> EmbeddingModel | None:
        return catalog().get(settings.vectors.model)

    def loaded(self, model: EmbeddingModel) -> bool:
        return self._embedder is not None and self._embedder.model == model

    async def embedder(self, model: EmbeddingModel) -> Embedder:
        async with self._loading:
            if not self.loaded(model):
                self._embedder = None
                self._queries.clear()
                self._embedder = await load(model_folder(self.models_dir, model), model)
            return self._embedder

    def unload(self) -> None:
        """Forget the loaded model (it was deleted, or another one chosen)."""
        self._embedder = None
        self._queries.clear()

    # -- vectorizing

    def _file(self, kind: str, owner: str, model: EmbeddingModel) -> VectorFile:
        return VectorFile(vector_file(self.data_dir, model.key, kind, owner), model)

    def missing(self, kind: str, owner: str, model: EmbeddingModel, texts: Iterable[str]) -> int:
        """How many of these texts aren't vectorized yet."""
        have = self._file(kind, owner, model).fingerprints()
        return len({fingerprint(t) for t in texts} - have)

    async def ensure(self, kind: str, owner: str, model: EmbeddingModel, texts: Iterable[str],
                     keep: Iterable[str] = ()) -> dict[str, np.ndarray]:
        """The vectors of these texts, by fingerprint: those missing are made. The file keeps theirs and those
        of `keep` (if it has them) only."""
        wanted = {fingerprint(t): t for t in texts if t.strip()}
        async with self._owners.setdefault((kind, owner), asyncio.Lock()):
            file = self._file(kind, owner, model)
            vectors = file.load()
            todo = {fp: t for fp, t in wanted.items() if fp not in vectors}
            if todo:
                embedder = await self.embedder(model)
                async with self._working:
                    made = await asyncio.to_thread(
                        lambda: {fp: embedder.embed_pieces(t, kind='passage') for fp, t in todo.items()})
                vectors.update(made)
            keeping = set(wanted) | {fingerprint(t) for t in keep}
            kept = {fp: v for fp, v in vectors.items() if fp in keeping}
            if todo or len(kept) != len(vectors):
                file.save(kept)
            return {fp: kept[fp] for fp in wanted}

    async def queries(self, model: EmbeddingModel, texts: list[str]) -> list[np.ndarray]:
        """The vectors of the pieces of each message (kept a while: the next reply needs most of them again)."""
        embedder = await self.embedder(model)
        keys = [fingerprint(f'{model.key}\n{t}') for t in texts]
        todo = {k: t for k, t in zip(keys, texts) if k not in self._queries}
        if todo:
            async with self._working:
                made = await asyncio.to_thread(lambda: {k: embedder.embed_pieces(t, kind='query') for k, t in todo.items()})
            self._queries.update(made)
        for k in keys:
            self._queries.move_to_end(k)
        while len(self._queries) > QUERY_CACHE:
            self._queries.popitem(last=False)
        return [self._queries[k] for k in keys]

    # -- for a prompt

    async def prepare(self, conversation: Conversation, *, books: list[LoreBook], memories: Memories,
                      settings: Settings, names: Iterable[str] = (),
                      busy: Callable[[bool], None] | None = None) -> Meaning | None:
        """Vector search's part in the next prompt (None if it's off, or there's nothing to compare).

        `names`: the character's and your persona's, left out of the messages compared (see topic_text()).
        Vectorizes what isn't yet first: `busy(True)` before, if there's anything to do (or the model to load),
        and `busy(False)` after.
        """
        vs = settings.vectors
        lore_on = vs.lore and any(lore_texts(b) for b in books)
        memory_on = vs.memories and settings.memory.enabled and bool(lore_texts(memories.book))
        if not (lore_on or memory_on):
            return None
        if problem := self.problem(settings):
            return Meaning(note=f'Vector search is on, but {problem}. Memories and lore were picked by their '
                                f'keywords only.')
        model = self.model(settings)
        entries = [e for b in books for e in b.entries.values()] + list(memories.book.entries.values())
        depth = max([settings.lore.scan_depth] + [e.scanDepth for e in entries if e.scanDepth is not None])
        names = list(names)
        messages = [topic_text(m.content, names) for m in conversation.messages[-depth:]] if depth > 0 else []
        if not messages:
            return None
        now, ahead, kept = memory_texts(memories)
        work = not self.loaded(model) \
            or (lore_on and any(self.missing('lorebooks', b.id, model, lore_texts(b).values()) for b in books)) \
            or (memory_on and self.missing('memories', conversation.id, model, [*now.values(), *ahead]) > 0)
        if work and busy:
            busy(True)
        try:
            lore = memory = None
            threshold = vs.threshold(model.key, model.threshold)
            asked = await self.queries(model, messages)
            if lore_on:
                vectors: dict[tuple[str, int], np.ndarray] = {}
                for book in books:
                    texts = lore_texts(book)
                    made = await self.ensure('lorebooks', book.id, model, texts.values())
                    vectors |= {(book.id, uid): made[fingerprint(t)] for uid, t in texts.items()}
                lore = Closeness(closeness(vectors, asked), threshold, vs.max_entries)
            if memory_on:
                made = await self.ensure('memories', conversation.id, model, [*now.values(), *ahead], keep=kept)
                book = memories.book
                memory = Closeness(closeness({(book.id, uid): made[fingerprint(t)] for uid, t in now.items()}, asked),
                                   threshold, vs.max_entries)
            return Meaning(lore=lore, memory=memory)
        except Exception as e:
            log.exception('Vector search failed')
            return Meaning(note=f'Vector search failed ({e or type(e).__name__}). Memories and lore were '
                                f'picked by their keywords only.')
        finally:
            if work and busy:
                busy(False)

    # -- in the background

    async def refresh(self, kind: str, owner: str) -> None:
        """Vectorize what isn't yet of a lore book, or of a chat's memories (if vector search is on for it)."""
        try:
            settings = self.settings.load()
        except ValueError:
            return
        if not (settings.vectors.lore if kind == 'lorebooks' else settings.vectors.memories and settings.memory.enabled):
            return
        if self.problem(settings):
            return
        model = self.model(settings)
        if kind == 'lorebooks':
            try:
                book = self.lore_books.get(owner)
            except ValueError:
                book = None
            if book is not None:
                await self.ensure(kind, owner, model, lore_texts(book).values())
        else:
            now, ahead, kept = memory_texts(self.memories.get(owner))
            await self.ensure(kind, owner, model, [*now.values(), *ahead], keep=kept)

    def soon(self, kind: str, owner: str, delay: float = SOON) -> None:
        """refresh() in a moment, in the background (again later if asked again meanwhile)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # (not in the app: a script, a test)
            return
        if task := self._soon.pop((kind, owner), None):
            task.cancel()
        self._soon[(kind, owner)] = task = loop.create_task(self._later(kind, owner, delay),
                                                            name=f'vectorize {kind} {owner}')
        task.add_done_callback(lambda t: self._soon.pop((kind, owner), None) if self._soon.get((kind, owner)) is t
                               else None)

    async def _later(self, kind: str, owner: str, delay: float) -> None:
        await asyncio.sleep(delay)
        try:
            await self.refresh(kind, owner)
        except Exception:
            log.warning('Vectorizing %s %s in the background failed', kind, owner, exc_info=True)

    def chat_opened(self, conversation_id: str, book_ids: list[str]) -> None:
        for book_id in book_ids:
            self.soon('lorebooks', book_id, delay=0.5)
        self.soon('memories', conversation_id, delay=0.5)

    def stop(self) -> None:
        for task in list(self._soon.values()):
            task.cancel()
