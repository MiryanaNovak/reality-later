"""The memory of a chat: what the AI keeps of messages that no longer fit in the prompt.

Memories are short notes (people, places, events, promises...) with keywords.
They come into the chat prompt when their keywords appear, like lore book
entries: inside, a chat's memories are a lore book of its own, kept with the
conversation (data/conversations/memories/<conversation id>.json) rather than
among your lore books, and never called one in the app.

How memories are written
------------------------
- Automatically, after a reply, when old messages have left the prompt: the
  memory connection reads those it hasn't read yet.
- When you ask, from the chat's menu: it reads every message it hasn't read yet,
  except the AI's last reply while < > can still change it.

What it writes is a proposed update ("pending"): you can review and edit it,
and it's applied when you post your next message. Long stretches are read in
batches, each seeing the memories as the previous one left them.

Going back
----------
Applying an update records the memories as they were before it, and which
messages it read. Deleting messages undoes every update that read any of them,
so the memories are back to what they were before those messages existed. A
pending update that read deleted messages is dropped. Editing a message
changes nothing.

Forking
-------
A fork of the chat (conversations.py) ends with one of the AI's replies, and its memories are exactly as
they were right after it (Memories.as_of): with the update written after it, before the chat went on,
still pending, as it was then. For that, the moments the chat goes on past a message (you post one, or
the AI continues the scene: Memories.moving_on) are recorded:
- each applied update says after which message it was applied;
- your own changes to the memories in use (on the Memories page, without an update pending) are recorded
  there as a step of their own (undone with the updates before them, like now);
- when the AI continues the scene with an update pending, the update as it is then is kept with it (it
  can still grow with the next reply).
Updates from files from before that were applied with your messages, whose times place them.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .connections import Message
from .lorebooks import LoreBook, LoreEntry
from .macros import fill
from .storage import JsonStore, StoredModel
from .tokens import estimate

if TYPE_CHECKING:
    from .conversations import ChatMessage

DEFAULT_TEMPERATURE = 0.3    # memory wants consistent, factual notes rather than creativity
DEFAULT_MAX_TOKENS = 2_000   # room for the changes (a chat reply's max length could cut them off)

DEFAULT_INSTRUCTIONS = """\
You keep the memory of a long roleplay between {{char}} and {{user}}. The oldest messages are about to \
leave the story's context: record what must not be forgotten, as short memory entries.

Record lasting facts, not a retelling of events:
- people met: who they are, how they relate to {{char}} and {{user}}, their attitude;
- places: what they are like, what happened there;
- key events and their consequences;
- promises, debts, secrets, plans and goals;
- possessions, injuries and other lasting changes;
- facts about the world that came up in the story;
- how {{char}} and {{user}} feel about each other, and how that has changed.

Don't record what the scenario, {{user}}'s description and the lore books already say.
Update an entry rather than adding one that repeats it, and remove what is no longer true.
Write each entry in a few sentences (60 words at most), in the third person, with names rather than \
pronouns, in the language of the story.
Give each entry 2 to 5 specific keywords, in the language of the story: names, places, unusual words. \
Avoid common words: they would bring the entry back all the time."""

ANSWER_FORMAT = """\
Answer with a JSON array of changes to the memory, and nothing else. Each change is one of:
  {"action": "add", "title": "...", "keys": ["...", "..."], "text": "...", "always": false}
  {"action": "update", "id": 3, "title": "...", "keys": ["..."], "text": "...", "always": false}
  {"action": "remove", "id": 7}
For "update", give the entry's id and only what changes. Use the ids of the entries under \
"Memory so far".
"always": true is for the very few entries that belong in every prompt, like how {{char}} and {{user}} \
feel about each other. The others come back when one of their keys appears in the chat.
If nothing needs to change, answer []."""

MATERIAL_FORMAT = """\
# {char} and the story
{character}

# {user}, played by the user
{persona}

# Already in the lore books (the entries these messages bring in)
{lore}

# Memory so far
{memories}

# Messages to remember
{messages}"""

BATCH_TOKENS = 6_000  # about how much chat one memory call reads at most; longer stretches take several calls


# --- what's stored -------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _empty_book() -> LoreBook:
    return LoreBook(id='memories', name='Memories', entries={})


class PendingMemory(BaseModel):
    """A proposed update: the memories as they'll be once it's applied (at your next message)."""
    first: str
    last: str
    proposed: LoreBook
    created_at: datetime = Field(default_factory=_now)
    # For forks: what it was when the AI continued the scene after a message (`after`: that message's id)
    after: str = ''
    earlier: list[PendingMemory] = Field(default_factory=list)


class MemoryStep(BaseModel):
    """An applied update, with what's needed to undo it; or your own changes to the memories in use (`yours`)."""
    first: str                 # ids of the first and last messages it read ('' for yours)
    last: str
    before: dict[str, LoreEntry] = Field(default_factory=dict)  # the memories before it
    before_read_until: str = ''
    applied_at: datetime = Field(default_factory=_now)
    # Where in the chat (for forks): the chat's last message when it was applied ('' in files from before)
    applied_after: str = ''
    yours: bool = False
    earlier: list[PendingMemory] = Field(default_factory=list)  # its pending versions (PendingMemory.earlier)


class Memories(StoredModel):
    """A conversation's memories: <data>/conversations/memories/<conversation id>.json"""
    name: str = Field('', exclude=True)  # from StoredModel; unused
    book: LoreBook = Field(default_factory=_empty_book)  # the memories in use
    read_until: str = ''                  # id of the last message the applied updates read ('' = none yet)
    history: list[MemoryStep] = Field(default_factory=list)
    pending: PendingMemory | None = None
    # The memories in use as the chat last went on (moving_on): your changes since are recorded the next time.
    # None in files from before.
    settled: dict[str, LoreEntry] | None = Field(default_factory=dict)

    @model_validator(mode='before')
    @classmethod
    def _from_before(cls, data: Any) -> Any:
        if isinstance(data, dict) and 'format_version' in data and 'settled' not in data:  # (a file from before)
            data = {**data, 'settled': None}
        return data

    @property
    def editable(self) -> LoreBook:
        """What the Memories page edits: the proposed update if there's one, else the memories in use."""
        return self.pending.proposed if self.pending else self.book

    @property
    def taken(self) -> set[int]:
        """Ids of the memories in use: new memories in an update must not reuse them."""
        return {e.uid for e in self.book.entries.values()}

    @property
    def last_read(self) -> str:
        """Id of the last message read so far, the pending update included."""
        return self.pending.last if self.pending else self.read_until

    def propose(self, first: str, last: str, proposed: LoreBook) -> None:
        """Set (or extend) the pending update."""
        p = self.pending
        self.pending = PendingMemory(first=p.first if p else first, last=last, proposed=proposed,
                                     earlier=p.earlier if p else [])

    def apply_pending(self, *, after: str = '') -> bool:
        """Apply the pending update, if any (returns whether there was one). `after`: the chat's last message now
        (the one your new message answers)."""
        if self.pending is None:
            return False
        p = self.pending
        self.history = [*self.history, MemoryStep(first=p.first, last=p.last, before=_copy(self.book.entries),
                                                  before_read_until=self.read_until, applied_after=after,
                                                  earlier=p.earlier)]
        self.book = p.proposed
        self.read_until = p.last
        self.pending = None
        self.settled = _copy(self.book.entries)
        return True

    def moving_on(self, after: str, *, posting: bool) -> bool:
        """The chat goes on after the message `after` (its id): you post a message (`posting`: the pending update is
        applied), or the AI continues the scene (it stays pending). Records where things were, for forks (see the
        notes at the top). Returns whether anything changed."""
        changed = False
        if self.settled is None:  # (from before: your changes so far can't be told apart)
            self.settled, changed = _copy(self.book.entries), True
        elif self.settled != self.book.entries:  # your own changes since the chat last went on
            self.history = [*self.history, MemoryStep(first='', last='', before=self.settled, yours=True,
                                                      before_read_until=self.read_until, applied_after=after)]
            self.settled, changed = _copy(self.book.entries), True
        if self.pending is not None and posting:
            return self.apply_pending(after=after) or changed
        if self.pending is not None and after:
            p = self.pending
            then = p.model_copy(update={'after': after, 'earlier': []}, deep=True)
            p.earlier = [*(e for e in p.earlier if e.after != after), then]
            changed = True
        return changed

    def forget(self, deleted: set[str]) -> bool:
        """Undo what was learnt from deleted messages (returns whether anything changed). Your own changes made
        after an update that's undone go with it."""
        changed = False
        p = self.pending
        if p and (p.first in deleted or p.last in deleted):
            self.pending, changed = None, True
        elif p and any(e.after in deleted for e in p.earlier):
            p.earlier, changed = [e for e in p.earlier if e.after not in deleted], True
        cut = len(self.history)
        for k in reversed(range(len(self.history))):
            step = self.history[k]
            if step.yours:
                continue
            if step.first not in deleted and step.last not in deleted:
                break
            cut = k
        if cut < len(self.history):
            step = self.history[cut]
            self.history = self.history[:cut]
            self.book = self.book.model_copy(update={'entries': step.before})
            self.read_until = step.before_read_until
            self.settled = _copy(step.before)
            changed = True
        return changed

    def as_of(self, message_id: str, messages: list[ChatMessage], *, new_id: str) -> Memories:
        """The memories as they were right after this message, for a copy of the chat that ends with it (a fork,
        whose id is `new_id`), with the update written after it still pending (see the notes at the top).
        `messages`: the chat's, all of them."""
        ids = [m.id for m in messages]
        at = ids.index(message_id)
        if at == len(ids) - 1:  # (the last one: as they are now)
            return self.model_copy(update={'id': new_id}, deep=True)

        def place(step: MemoryStep) -> int:
            """Where in the chat a step was applied: after which message (its index; -1: before them all)."""
            if step.applied_after in ids:
                return ids.index(step.applied_after)
            earlier = [i for i, m in enumerate(messages) if m.created_at <= step.applied_at]  # (by the times)
            return earlier[-1] if earlier else -1

        steps = self.history
        done = 0  # the steps from before the chat went on after the message, and your changes then
        while done < len(steps) and (place(steps[done]) < at or (steps[done].yours and place(steps[done]) == at)):
            done += 1

        def before(k: int) -> dict[str, LoreEntry]:
            """The memories in use before step k (k = len(steps): after them all, as the chat last went on)."""
            if k < len(steps):
                return steps[k].before
            return self.settled if self.settled is not None else self.book.entries

        def book(entries: dict[str, LoreEntry]) -> LoreBook:
            return self.book.model_copy(update={'entries': _copy(entries)}, deep=True)

        # The update pending then: one applied later, or still pending, as it was when the chat went on after it
        pending = None
        later = next((k for k in range(done, len(steps)) if not steps[k].yours), None)
        if later is not None:
            step = steps[later]
            versions = [*step.earlier, PendingMemory(first=step.first, last=step.last, proposed=book(before(later + 1)),
                                                     after=ids[place(step)] if place(step) >= 0 else '',
                                                     earlier=step.earlier)]
        else:
            versions = [*self.pending.earlier] if self.pending else []
        then = [v for v in versions if v.after == message_id]
        if then:
            pending = then[-1].model_copy(update={'after': '', 'earlier': []}, deep=True)
            pending.earlier = [v.model_copy(deep=True) for v in versions if v.after in ids[:at]]
        entries = before(done)
        return Memories(id=new_id, book=book(entries), read_until=steps[done].before_read_until if done < len(steps)
                        else self.read_until, history=[s.model_copy(deep=True) for s in steps[:done]],
                        pending=pending, settled=_copy(entries))

    def changes(self) -> MemoryChanges:
        """What the pending update changes, compared to the memories in use."""
        if self.pending is None:
            return MemoryChanges()
        old, new = self.book.entries, self.pending.proposed.entries
        return MemoryChanges(
            added=[k for k in new if k not in old],
            changed=[k for k in new if k in old and _gist(new[k]) != _gist(old[k])],
            removed=[k for k in old if k not in new],
        )


class MemoryChanges(BaseModel):
    added: list[str] = Field(default_factory=list)    # entry keys (uids as text)
    changed: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f'{len(self.added)} new' if self.added else '', f'{len(self.changed)} changed' if self.changed else '',
                 f'{len(self.removed)} removed' if self.removed else '']
        return ', '.join(p for p in parts if p)


def _gist(e: LoreEntry) -> tuple:
    return e.comment, tuple(e.key), e.content, e.constant


def _copy(entries: dict[str, LoreEntry]) -> dict[str, LoreEntry]:
    return {k: e.model_copy(deep=True) for k, e in entries.items()}


class MemoryStore:
    """One memories file per conversation."""

    def __init__(self, folder: str | Path) -> None:
        self._store = JsonStore(Path(folder), Memories.model_validate)
        self.folder = self._store.folder
        self.on_saved = self._store.on_saved  # (see JsonStore: vector search uses it)

    def get(self, conversation_id: str) -> Memories:
        """The conversation's memories (empty ones if there are none yet, or the file is broken)."""
        try:
            found = self._store.get(conversation_id)
        except ValueError:
            found = None
        return found or Memories(id=conversation_id)

    def save(self, memories: Memories) -> None:
        self._store.save(memories)

    def save_soon(self, memories: Memories) -> None:
        self._store.save_soon(memories)

    def flush(self) -> None:
        self._store.flush()

    def delete(self, conversation_id: str) -> None:
        self._store.delete(conversation_id)

    def exists(self, conversation_id: str) -> bool:
        return self._store.exists(conversation_id)

    def moving_on(self, conversation_id: str, after: str, *, posting: bool) -> None:
        """The chat goes on after the message `after`: see Memories.moving_on."""
        if not self.exists(conversation_id):
            return
        memories = self.get(conversation_id)
        if memories.moving_on(after, posting=posting):
            self.save(memories)

    def forget(self, conversation_id: str, deleted_ids: list[str]) -> None:
        memories = self.get(conversation_id)
        if memories.forget(set(deleted_ids)):
            self.save(memories)


# --- the memory call ------------------------------------------------------------------------

class MemoryCallError(Exception):
    """The memories couldn't be written: the message says why."""


class MemoryChange(BaseModel):
    """One change the AI asks for (see ANSWER_FORMAT)."""
    model_config = ConfigDict(extra='ignore')

    action: Literal['add', 'update', 'remove']
    id: int | None = None
    title: str | None = None
    keys: list[str] | None = None
    text: str | None = None
    always: bool | None = None

    @field_validator('keys', mode='before')
    @classmethod
    def _keys(cls, value: Any) -> Any:
        return [value] if isinstance(value, str) else value


def unread(messages: list[ChatMessage], memories: Memories, *, until: int) -> list[ChatMessage]:
    """The messages before index `until` that the memories haven't read yet."""
    ids = [m.id for m in messages]
    start = ids.index(memories.last_read) + 1 if memories.last_read in ids else 0
    return messages[start:max(start, until)]


def batches(lines: list[tuple[ChatMessage, str]], limit: int = BATCH_TOKENS) -> list[list[tuple[ChatMessage, str]]]:
    """Split (message, "Name: text") pairs into batches of about `limit` tokens (one message at least each)."""
    result: list[list[tuple[ChatMessage, str]]] = []
    size = limit
    for item in lines:
        cost = estimate(item[1])
        if size + cost > limit and (not result or result[-1]):
            result.append([])
            size = 0
        result[-1].append(item)
        size += cost
    return result


def memory_prompt(*, instructions: str, char: str, user: str, character: str, persona: str, book: LoreBook,
                  lines: list[str], lore: list[tuple[str, str]] = (),
                  pronouns: Sequence[str] | None = None, modules: Sequence[str] = ()) -> list[Message]:
    """The messages of one memory call.

    `character` is the character's scenario (all the AI is told about them); `persona`, your persona's description;
    `lore`, the lore book entries the messages bring in, as (title, text), their macros filled in; `pronouns`, your
    persona's (macros.py); `modules`, the memory instructions of the chat's prompt modules on (presets.py): after
    the instructions, before the answer's format (which stays last). Those empty are left out.
    """
    def filled(text: str) -> str:
        return fill(text, char=char, user=user, pronouns=pronouns).strip()

    listing = '\n\n'.join(
        f'[id {e.uid}] {e.comment or "Untitled"} (keys: {", ".join(e.key) or "none"})'
        + (' [always]' if e.constant else '') + f'\n{e.content}'
        for e in book.sorted_entries()
    ) or '(none yet)'
    known = '\n\n'.join(f'{title}\n{text}' if title.strip() else text for title, text in lore if text.strip())
    material = MATERIAL_FORMAT.format(char=char, user=user, character=filled(character) or '(no scenario)',
                                      persona=filled(persona) or '(no description)',
                                      lore=known or '(none for these messages)', memories=listing,
                                      messages='\n\n'.join(lines))
    added = [filled(text) for text in modules if text.strip()]
    system = '\n\n'.join([filled(instructions or DEFAULT_INSTRUCTIONS), *added, filled(ANSWER_FORMAT)])
    return [Message(role='system', content=system), Message(role='user', content=material)]


def parse_changes(text: str) -> list[MemoryChange]:
    """The changes in the AI's answer. Raises MemoryCallError if there's no readable list in it."""
    fenced = re.search(r'```(?:json)?\s*(.*?)```', text, re.DOTALL)
    body = fenced[1] if fenced else text
    start, end = body.find('['), body.rfind(']')
    if start < 0 or end < start:
        raise MemoryCallError('The answer had no list of changes in it.')
    try:
        data = json.loads(body[start:end + 1])
    except json.JSONDecodeError as e:
        raise MemoryCallError(f'The answer\'s list of changes isn\'t valid JSON ({e.msg}).') from None
    if not isinstance(data, list):
        raise MemoryCallError('The answer\'s changes aren\'t a list.')
    changes = []
    for item in data:
        try:
            changes.append(MemoryChange.model_validate(item))
        except ValidationError:
            continue  # one odd change shouldn't spoil the others
    if data and not changes:
        raise MemoryCallError('None of the changes in the answer could be read.')
    return changes


def add_memory(book: LoreBook, *, taken: set[int] = frozenset(), **fields: Any) -> LoreEntry:
    """Add a memory with an id never used by `book` nor in `taken` (the ids of the memories in use), so a
    new memory can't be mistaken for one an update removes."""
    uid = max([*(e.uid for e in book.entries.values()), *taken], default=-1) + 1
    entry = LoreEntry(uid=uid, displayIndex=uid, position=0, **fields)
    book.entries[str(uid)] = entry
    return entry


def apply_changes(book: LoreBook, changes: list[MemoryChange], *, taken: set[int] = frozenset()) -> None:
    """Apply the AI's changes to a memory book (changes about unknown ids are skipped)."""
    for change in changes:
        if change.action == 'add':
            text = (change.text or '').strip()
            if text:
                add_memory(book, taken=taken, comment=(change.title or text[:40]).strip(),
                           key=_clean_keys(change.keys), content=text, constant=bool(change.always))
        elif change.id is not None and (entry := book.entry(change.id)) is not None:
            if change.action == 'remove':
                book.remove_entry(change.id)
                continue
            if change.title is not None:
                entry.comment = change.title.strip()
            if change.keys is not None:
                entry.key = _clean_keys(change.keys)
            if change.text is not None and change.text.strip():
                entry.content = change.text.strip()
            if change.always is not None:
                entry.constant = change.always


def _clean_keys(keys: list[str] | None) -> list[str]:
    seen, result = set(), []
    for key in keys or []:
        key = str(key).strip()
        if key and key.lower() not in seen:
            seen.add(key.lower())
            result.append(key)
    return result
