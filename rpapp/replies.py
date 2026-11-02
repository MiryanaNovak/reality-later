"""Getting the AI's reply to your messages.

When you send a message, the chat page asks Replies to start one. It runs in
the background, one at a time per conversation, so it carries on if you leave
the page or your phone drops the connection: the reply is saved to the
conversation either way, and the chat page shows it when you're back.

    1. build the prompt (prompts.py) and save it under the id of the message replied to
    2. send it to the chat connection chosen in Settings
    3. add the reply to the conversation, and save its details (model, tokens, cost,
       thinking) in data/conversations/replies/<conversation id>/<reply id>.json

Another version of the last reply works the same way, from the same point in
the chat: the new text is added to the reply's versions (see conversations.py)
and its details to the same file, one entry per version.

Extending the last reply makes a version too: the version shown, plus what the
model adds to it (see extended()). The connection says how it's asked for: a
hidden instruction, or prefill (see prompts.py). Its prompt is saved with its
details, since it isn't the one sent after your message.

Continuing the scene: the AI writes its next message without one from you, with
a hidden instruction in your place (see prompts.py). Its prompt is saved with its
details too. Another version of such a message is asked for the same way: a
message of the AI right after another one (or first in the chat, if it isn't the
character's first message) can only have been written like that. When your
message is the last one, continuing the scene is simply replying to it.

Stopping cancels the request: providers that support it stop generating (and
charging) too, since the reply is streamed underneath.

Memories (see memory.py) are written by the same kind of background job,
right after a reply when old messages have left the prompt, or when you ask.
Only one job runs at a time per conversation: the chat page waits for the
reply, then for the memories.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .connections import Connection, GenerationParams, ModelError, Reply, Usage
from .conversations import ChatMessage, Conversation, ConversationStore
from .lorebooks import LoreBook
from .memory import (
    DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE, MemoryCallError, apply_changes, batches, memory_prompt, parse_changes,
    unread,
)
from .presets import PromptPreset, chat_preset
from .profiles import ProfileStore
from .prompts import ExtendMethod, Prompt, build_prompt, lore_books_for
from .settings import Settings, SettingsStore, chat_connection
from .storage import JsonStore, StoredModel

log = logging.getLogger(__name__)


class ReplyError(Exception):
    """No reply: the message says why, for the chat page."""


class ReplyDetails(BaseModel):
    """What there is to know about one AI reply, beyond its text."""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    connection: str = ''      # 'DeepSeek (deepseek/deepseek-chat)'
    model: str = ''           # the model that answered, as the server reports it
    provider: str = ''        # OpenRouter: the company that ran it
    finish_reason: str = ''   # 'stop', 'length' (cut off), 'content_filter'...
    request_id: str = ''
    usage: Usage = Field(default_factory=Usage)
    reasoning: str = ''       # the model's thinking, if it shows it
    seconds: float = 0        # from sending the prompt to the end of the reply
    reply_to: str = ''        # id of the message replied to: its prompt is the one that was sent
    prompt_estimate: int = 0  # the prompt's estimated size, to compare with usage.prompt_tokens
    max_tokens: int | None = None  # the max reply length at the time
    # An extension: the version it extends (counted from 0), how it was asked for, and the prompt sent
    # (the usage and the times are the extension's alone)
    extends: int | None = None
    extend_method: ExtendMethod = ''
    continues: bool = False   # written to continue the scene (the prompt is kept here too)
    prompt: Prompt | None = None


class ReplyRecord(StoredModel):
    """The file kept for a reply: the details of each of its versions, in order."""
    name: str = Field('', exclude=True)  # from StoredModel; unused
    versions: list[ReplyDetails | None] = Field(default_factory=list)

    @model_validator(mode='before')
    @classmethod
    def _upgrade(cls, data: Any) -> Any:
        """The first version of the file held one reply's details, at the top level."""
        if isinstance(data, dict) and 'versions' not in data and 'usage' in data:
            return {'id': data.get('id', ''), 'versions': [data]}
        return data

    def get(self, index: int) -> ReplyDetails | None:
        return self.versions[index] if 0 <= index < len(self.versions) else None

    def put(self, index: int, details: ReplyDetails) -> None:
        self.versions = [*self.versions, *[None] * (index + 1 - len(self.versions))]
        self.versions[index] = details


@dataclass(eq=False)
class Job:
    """One reply (or one memory update) being written, or that was."""
    conversation_id: str
    reply_to: str                        # id of the message replied to (memories: the last message then)
                                         # ('' for the first message of a chat, when continuing the scene)
    version_of: str = ''                 # for another version of a reply: that reply's id
    extend: bool = False                 # that version: the one shown, extended
    continue_scene: bool = False         # no message of yours to reply to: a hidden instruction in your place
    kind: Literal['reply', 'memory'] = 'reply'
    manual: bool = False                 # memories: asked for from the chat's menu
    reading: list[str] = field(default_factory=list)  # memories: ids of the messages to read
    task: asyncio.Task | None = None
    finished: bool = False
    message: ChatMessage | None = None   # the reply, once added to the conversation
    details: ReplyDetails | None = None
    summary: str = ''                    # memories: '2 new, 1 changed' ('' for nothing new)
    progress: str = ''                   # memories: '2 / 3' while reading in batches
    error: str = ''
    stopped: bool = False
    _listeners: list[Callable[[Job], None]] = field(default_factory=list)
    _progress_listeners: list[Callable[[Job], None]] = field(default_factory=list)

    def on_done(self, listener: Callable[[Job], None]) -> None:
        """Call `listener(job)` when it's over (right away if it already is)."""
        if self.finished:
            listener(self)
        else:
            self._listeners.append(listener)

    def on_progress(self, listener: Callable[[Job], None]) -> None:
        self._progress_listeners.append(listener)

    def _set_progress(self, text: str) -> None:
        self.progress = text
        for listener in self._progress_listeners:
            try:
                listener(self)
            except Exception:
                log.exception('A progress listener failed')


class Replies:
    def __init__(self, conversations: ConversationStore, *, characters: ProfileStore, personas: ProfileStore,
                 lore_books: JsonStore[LoreBook], settings: SettingsStore,
                 connections: JsonStore[Connection], presets: JsonStore[PromptPreset] | None = None) -> None:
        self.conversations = conversations
        self.presets = presets  # the saved prompts (None: no instructions at all, in some tests)
        self.characters = characters
        self.personas = personas
        self.lore_books = lore_books
        self.settings = settings
        self.connections = connections
        self._jobs: dict[str, Job] = {}  # the latest job of each conversation, running or finished

    def running(self, conversation_id: str) -> Job | None:
        job = self._jobs.get(conversation_id)
        return job if job and not job.finished else None

    def last(self, conversation_id: str) -> Job | None:
        """The latest job for this conversation, running or finished (since the app started)."""
        return self._jobs.get(conversation_id)

    def start(self, conversation_id: str, *, version_of: str = '', extend: bool = False,
              continue_scene: bool = False) -> Job:
        """Start a reply to the conversation's last message (or return the one already running).

        With `version_of` (the id of the last message, a reply), write another version of it instead;
        with `extend` too, that version is the one shown, extended. With `continue_scene`, the AI's next
        message without one from you (a plain reply if yours is the last one).
        """
        if job := self.running(conversation_id):
            return job
        conversation = self.conversations.get(conversation_id)
        messages = conversation.messages if conversation else []
        extend = extend and bool(version_of)
        if version_of:
            index = next((i for i, m in enumerate(messages) if m.id == version_of), 0)
            reply_to = messages[index - 1].id if index > 0 else ''
            continue_scene = not extend and continues_scene(messages, index)
        else:
            reply_to = messages[-1].id if messages else ''
            continue_scene = continue_scene and not (messages and messages[-1].role == 'user')
        job = Job(conversation_id, reply_to=reply_to, version_of=version_of, extend=extend,
                  continue_scene=continue_scene)
        self._jobs[conversation_id] = job
        job.task = asyncio.create_task(self._run(job), name=f'reply to {conversation_id}')
        job.task.add_done_callback(lambda task: self._finish(job, task))
        return job

    def start_memory(self, conversation_id: str, *, manual: bool) -> Job | None:
        """Start writing memories from the messages they haven't read yet (None if there are none).

        Automatically (`manual` False): the messages that have left the prompt. When asked: all of them,
        except the AI's last reply while it can still be changed.
        """
        if job := self.running(conversation_id):
            return job
        conversation = self.conversations.get(conversation_id)
        if conversation is None:
            return None
        to_read = self.to_remember(conversation, manual=manual)
        if not to_read:
            return None
        job = Job(conversation_id, reply_to=conversation.messages[-1].id, kind='memory', manual=manual,
                  reading=[m.id for m in to_read])
        self._jobs[conversation_id] = job
        job.task = asyncio.create_task(self._run(job), name=f'memories of {conversation_id}')
        job.task.add_done_callback(lambda task: self._finish(job, task))
        return job

    def to_remember(self, conversation: Conversation, *, manual: bool) -> list[ChatMessage]:
        """The messages the memories should read now (see start_memory)."""
        settings = self.current_settings()
        if not settings.memory.enabled:
            return []
        memories = self.conversations.memories.get(conversation.id)
        messages = conversation.messages
        if manual:
            until = len(messages) - 1 if conversation.can_change_reply else len(messages)
        else:
            until = self._prompt(conversation, settings).report.messages_left_out
        return unread(messages, memories, until=until)

    def stop(self, conversation_id: str) -> None:
        if (job := self.running(conversation_id)) and job.task:
            job.task.cancel()

    def stop_all(self) -> None:
        """Cancel every running reply (when the app shuts down)."""
        for job in list(self._jobs.values()):
            if not job.finished and job.task:
                job.task.cancel()

    # -- the work ----------------------------------------------------------------------

    async def _run(self, job: Job) -> None:
        try:
            await (self._remember(job) if job.kind == 'memory' else self._reply(job))
        except asyncio.CancelledError:
            job.stopped = True  # not raised again: this task is ours and ends here
        except (ReplyError, ModelError, MemoryCallError) as e:
            job.error = str(e)
        except Exception as e:  # a bug: say so rather than spin forever
            log.exception('Writing a reply failed')
            job.error = f'Something went wrong: {e}'

    def _finish(self, job: Job, task: asyncio.Task) -> None:
        """When the task is over, however it ended (even if stopped before it began)."""
        if task.cancelled():
            job.stopped = True
        job.finished = True
        if job.kind == 'reply' and job.message and not job.version_of:
            try:  # old messages may have left the prompt: write memories (before telling the page)
                self.start_memory(job.conversation_id, manual=False)
            except Exception:
                log.exception('Starting memories failed')
        for listener in job._listeners:
            try:
                listener(job)
            except Exception:
                log.exception('A reply listener failed')
        job._listeners.clear()

    def current_settings(self) -> Settings:
        try:
            return self.settings.load()
        except ValueError:
            return Settings()  # the Settings page says the file is broken

    def _prompt(self, conversation: Conversation, settings: Settings, *, extend: ExtendMethod = '',
                continue_scene: bool = False):
        """The chat prompt for the conversation as it is (or to extend its last message, or continue the scene)."""
        character = self.characters.get(conversation.character.id)
        persona = self.personas.get(conversation.persona.id)
        books, missing = lore_books_for(character, persona, self.lore_books)
        return build_prompt(conversation, character=character, persona=persona, settings=settings,
                            connection=chat_connection(settings, self.connections.get), books=books,
                            missing_books=missing, memories=self.conversations.memories.get(conversation.id).book,
                            preset=chat_preset(settings.chat.preset, self.presets) if self.presets else None,
                            extend=extend, continue_scene=continue_scene)

    async def _reply(self, job: Job) -> None:
        conversation = self._still_there(job)
        settings = self.current_settings()
        connection = chat_connection(settings, self.connections.get)
        character = self.characters.get(conversation.character.id)
        extend: ExtendMethod = (connection.extend_method if connection else 'instruction') if job.extend else ''
        if extend:  # the chat up to the reply, which goes on
            extended_version = conversation.messages[-1].alternative if conversation.messages[-1].alternatives else 0
            base = conversation.messages[-1].content
        elif job.version_of:  # the chat up to the message replied to
            conversation = conversation.model_copy(update={'messages': conversation.messages[:-1]})
        continuing = job.continue_scene and not extend
        prompt = self._prompt(conversation, settings, extend=extend, continue_scene=continuing)
        if not (extend or continuing):  # (those go with the details, below: they don't follow a message of yours)
            self.conversations.prompts.save(conversation.id, job.reply_to, prompt)
        if connection is None:
            ref = settings.chat.connection
            raise ReplyError(f'The chat connection "{ref.name}" is missing: choose another one in Settings.' if ref
                             else 'No chat connection is chosen: choose one in Settings.')

        started = time.monotonic()
        reply = await connection.generate(prompt.messages(), continue_last=extend == 'prefill')
        seconds = time.monotonic() - started
        char = (character.name or character.title) if character else conversation.character.title
        text = clean(reply.text, char)
        if not text:
            raise ReplyError(empty_reply_reason(reply))
        if extend:
            text = extended(base, reply.text if extend == 'prefill' else text, prefill=extend == 'prefill')
            if text == base.rstrip():
                raise ReplyError('The model only repeated the reply, and added nothing to it. Try again.')

        conversation = self._still_there(job, while_writing=True)  # it may have changed meanwhile
        if job.version_of:
            job.message = conversation.add_version(job.version_of, text)
        else:
            job.message = conversation.add('assistant', text)
        self.conversations.save(conversation)
        job.details = ReplyDetails(
            connection=prompt.connection, model=reply.model, provider=reply.provider,
            finish_reason=reply.finish_reason, request_id=reply.request_id, usage=reply.usage,
            reasoning=reply.reasoning.strip(), seconds=round(seconds, 2), reply_to=job.reply_to,
            prompt_estimate=prompt.tokens, max_tokens=connection.params.max_tokens,
            **(dict(extends=extended_version, extend_method=extend, prompt=prompt) if extend else {}),
            **(dict(continues=True, prompt=prompt) if continuing else {}),
        )
        record = self.conversations.replies.get(conversation.id, job.message.id) or ReplyRecord()
        record.put(job.message.alternative if job.message.alternatives else 0, job.details)
        self.conversations.replies.save(conversation.id, job.message.id, record)

    def _still_there(self, job: Job, *, while_writing: bool = False):
        when = ' while the reply was written' if while_writing else ''
        conversation = self.conversations.get(job.conversation_id)
        if conversation is None:
            raise ReplyError(f'The conversation was deleted{when}.')
        ids = [m.id for m in conversation.messages]
        expected = [i for i in (job.reply_to, job.version_of) if i]
        if job.reply_to:
            same = ids[-len(expected):] == expected
        else:  # the chat's first message, when continuing the scene (or extending what that wrote)
            same = (job.continue_scene or job.extend) and ids == expected
        if not same:
            raise ReplyError(f'The chat changed{when}: there\'s nothing to reply to.')
        return conversation

    async def _remember(self, job: Job) -> None:
        conversation = self.conversations.get(job.conversation_id)
        if conversation is None:
            raise MemoryCallError('The conversation was deleted.')
        by_id = {m.id: m for m in conversation.messages}
        if any(i not in by_id for i in job.reading):
            raise MemoryCallError('The chat changed: some of the messages to read were deleted.')
        to_read = [by_id[i] for i in job.reading]
        settings = self.current_settings()
        memory = settings.memory
        ref = memory.connection or settings.chat.connection
        connection = self.connections.get(ref.id) if ref else None
        if connection is None:
            raise MemoryCallError(f'The connection "{ref.name}" is missing: choose another one in Settings.' if ref
                                  else 'No chat connection is chosen: choose one in Settings.')
        connection = connection.model_copy(deep=True)
        connection.params.stop = []  # stop sequences are for chat replies
        params = GenerationParams(
            temperature=memory.temperature if memory.temperature is not None else DEFAULT_TEMPERATURE,
            max_tokens=memory.max_tokens or DEFAULT_MAX_TOKENS)

        character = self.characters.get(conversation.character.id)
        persona = self.personas.get(conversation.persona.id)
        char = (character.name or character.title) if character else conversation.character.title
        user = (persona.name or persona.title) if persona else conversation.persona.title
        names = {'user': user, 'assistant': char}

        memories = self.conversations.memories.get(conversation.id)
        last_read = memories.last_read
        working = memories.editable.model_copy(deep=True)
        groups = batches([(m, f'{names.get(m.role, "System")}: {m.content}') for m in to_read])
        for number, group in enumerate(groups, start=1):
            if len(groups) > 1:
                job._set_progress(f'{number} / {len(groups)}')
            messages = memory_prompt(
                instructions=memory.instructions, char=char, user=user,
                character=character.scenario if character else '',  # (the description isn't for the AI)
                persona=persona.description if persona else '', book=working, lines=[line for _, line in group])
            reply = await connection.generate(messages, params)
            try:
                changes = parse_changes(reply.text)
            except MemoryCallError:
                if reply.finish_reason == 'length':
                    raise MemoryCallError('The answer was cut off at the max reply length: raise it in '
                                          'Settings › Memory.') from None
                raise
            apply_changes(working, changes, taken=memories.taken)

        memories = self.conversations.memories.get(conversation.id)  # again: it may have changed meanwhile
        if memories.last_read != last_read:
            raise MemoryCallError('The memories changed while they were written: try again.')
        memories.propose(first=to_read[0].id, last=to_read[-1].id, proposed=working)
        self.conversations.memories.save(memories)
        job.summary = memories.changes().summary

    def details(self, conversation_id: str, message: ChatMessage) -> ReplyDetails | None:
        """The details of the version of this reply that's shown."""
        record = self.conversations.replies.get(conversation_id, message.id)
        return record.get(message.alternative if message.alternatives else 0) if record else None


def continues_scene(messages: list[ChatMessage], index: int) -> bool:
    """True if the AI's message at `index` was written to continue the scene: it follows another of the
    AI's, or opens the chat without being the character's first message."""
    if not 0 <= index < len(messages):
        return False
    message = messages[index]
    if message.role != 'assistant' or message.greeting:
        return False
    return index == 0 or messages[index - 1].role == 'assistant'


def clean(text: str, char: str) -> str:
    """The reply as it goes into the chat: trimmed, without a leading "Name:" some models add."""
    text = text.strip()
    prefix = f'{char}:'
    if char and text[:len(prefix)].lower() == prefix.lower():
        text = text[len(prefix):].lstrip()
    return text


# How an extension is joined to the reply (instruction method: the model's answer is a new message)
ATTACHED = set(',.;:!?…)]}”’»」』')     # starts the extension: no space before it
SENTENCE_END = set('.!?…"”’»」』*_~)]')  # ends the reply: a sentence (or an action, a line of dialogue) is over
MIN_OVERLAP = 12                       # characters: shorter repeats of the reply's end may be a coincidence


def extended(base: str, addition: str, *, prefill: bool) -> str:
    """The reply `base`, with what the model added to it.

    Prefill: the model wrote the rest of its own message, so the addition goes on as is, spaces
    included (one is added only where it clearly forgot it, after the end of a sentence).
    Instruction: the model wrote a new message. What it repeated of the reply's end is dropped, then
    it's joined with nothing before punctuation, a space mid-sentence (or before a lowercase word),
    and a new paragraph after a finished sentence.
    """
    base = base.rstrip()
    if prefill:
        addition = addition.rstrip()
        if base and addition and not addition[0].isspace() and base[-1] in SENTENCE_END and addition[0].isalnum():
            return f'{base} {addition}'
        return base + addition
    addition = _without_repeat(base, addition.strip())
    if not addition or not base:
        return base + addition
    if addition[0] in ATTACHED:
        separator = ''
    elif base[-1] not in SENTENCE_END or addition[0].islower():
        separator = ' '
    else:
        separator = '\n\n'
    return base + separator + addition


def _without_repeat(base: str, addition: str) -> str:
    """The addition without the reply's end, or the whole reply, if it starts by repeating it. Mid-sentence,
    a shorter repeat counts too, of up to the last three words ("and" + "and then she leaves")."""
    for size in range(min(len(base), len(addition)), MIN_OVERLAP - 1, -1):
        if base.endswith(addition[:size]):
            return addition[size:].lstrip()
    if base and base[-1] not in SENTENCE_END:
        last, first = base.split()[-3:], addition.split(maxsplit=3)
        for count in range(min(3, len(last), len(first)), 0, -1):
            if [w.lower() for w in last[-count:]] == [w.lower() for w in first[:count]]:
                return addition.split(maxsplit=count)[count] if len(first) > count else ''
    return addition


def empty_reply_reason(reply: Reply) -> str:
    if reply.finish_reason == 'length':
        if reply.reasoning:
            return ('The model spent the whole max reply length thinking, and wrote no reply. Raise the max '
                    'reply length in the connection, or lower its reasoning effort.')
        return 'The model was stopped before writing anything: raise the max reply length in the connection.'
    if reply.finish_reason == 'content_filter':
        return 'The provider\'s content filter blocked the reply. Try again, or try another model.'
    return 'The model sent back an empty reply. Try again, or try another model.'
