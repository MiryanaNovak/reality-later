"""The prompt sent to the model for a reply, and where each one is kept.

A prompt is a list of labelled parts, made by blocks in this order (LAYOUT):

    instructions  the instructions of the prompt chosen in Settings › Chat system
    scenario      About {{char}} and the story: the character's         system
                  scenario, all the AI is told about them
    persona       About {{user}}: gender, pronouns, description          system
    lore          lore entries placed before/after the character        system
    examples      the character's example dialogues, with the lore      system
                  entries placed before/after the example messages
    memory        the chat's memories whose keywords appear (memory.py) system
    history       the chat; the oldest messages are left out when it    user / assistant
                  doesn't fit. Lore entries placed "at a depth" go in
                  among the messages, that many messages from the end.
    final         the prompt's final instructions                        system
    hidden        a hidden instruction, sent as you, never in the chat:  user
                  to extend the last reply, or to continue the scene
                  (the prompt's, or presets.EXTEND/CONTINUE_INSTRUCTION)

A character's description is only for you (it's shown in the list of characters):
it's never sent. Their scenario says who they are and what the story is.

Background first, what to do now last: models pay the most attention to the
end of a prompt. What never changes comes first too, so the providers that
cache the start of a prompt (DeepSeek, among others) can reuse it from one
reply to the next. That's why lore, which comes and goes with keywords, sits
after the scenario rather than before the character as in SillyTavern.

Fitting in the context window (sizes are estimates, see tokens.py):

    space for the prompt = context size - reply length - 10% margin
    lore and memories get up to their share of that space (Settings),
    the fixed parts, lore and memories come first, then the chat, newest messages first.

When messages have to be left out, they go in chunks of about a fifth of the
chat's space rather than one per reply, so the start of the prompt stays the
same (and cached) for several replies in a row.

Example dialogues show the character's voice, which matters most early on: by
default (Settings › Chat), they're sent while the whole chat fits along with
them, and make room for it as it grows, the last ones first. Once a message has
to be left out, none are sent. As the chat only grows, they leave for good
rather than coming and going (which would change the start of the prompt each
time). With "always", they're sent like the other fixed parts.

Extending the AI's last reply (the chat ends with it), one of two ways, the connection says which:
- 'instruction': the hidden instruction goes last (as you: a chat ending with the model's own message
  would be continued as is, or refused);
- 'prefill': the reply moves to the very end, after the final instructions, as the unfinished start of
  the model's message (trailing spaces trimmed: some providers refuse them). The model writes the rest.

Continuing the scene (the AI writes its next message, without one from you): the hidden instruction
goes last, in your place. Afterwards, the chat has two messages of the AI in a row: they're sent as one
(see below), since some models refuse two in a row.

When it's sent (Prompt.messages()), parts of the same role in a row become one
message, and system parts inside the chat become user messages if the
connection asks for it. If the chat opens with the character, a short
placeholder from you goes first: some models (Claude, Gemini) require it.

Each prompt is saved in a file of its own when it's built, under the id of
the message it follows (see conversations.py):

    data/conversations/prompts/<conversation id>/<message id>.json
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, model_validator

from .connections import Message
from .lore import Activation, LoreResult, activate
from .lorebooks import AT_DEPTH, LoreBook
from .macros import fill
from .personas import GENDERS, Persona
from .presets import CONTINUE_INSTRUCTION, EXTEND_INSTRUCTION
from .settings import Settings
from .storage import JsonStore, StoredModel
from .tokens import MESSAGE_OVERHEAD, estimate

if TYPE_CHECKING:
    from .characters import Character
    from .connections import Connection
    from .conversations import ChatMessage, Conversation
    from .presets import PromptPreset

LAYOUT = ('instructions', 'scenario', 'persona', 'lore', 'examples', 'memory', 'history', 'final', 'hidden')
ExtendMethod = Literal['', 'instruction', 'prefill']  # '' = a reply, not an extension

DEFAULT_CONTEXT = 16_384  # tokens, when the connection doesn't say
DEFAULT_REPLY = 1_024     # tokens kept for the reply, when the connection sets no maximum
MARGIN = 0.10             # of the context size, for the estimates' errors
TRIM_STEP = 0.20          # of the chat's space: how much is left out at a time

START_TEXT = '[Start of the chat]'
TRIMMED_TEXT = '[Earlier messages are left out]'

Role = Literal['system', 'user', 'assistant']
LORE_ROLES: dict[int, Role] = {0: 'system', 1: 'user', 2: 'assistant'}

# Where lore placed "with the background" goes, by SillyTavern position. Positions for things this
# app doesn't have yet (the author's note) join the others, with a note.
BACKGROUND_ORDER = {0: 0, 1: 1, 2: 2, 3: 3}
BACKGROUND_NOTES = {2: "there's no author's note yet", 3: "there's no author's note yet"}
BEFORE_EXAMPLES, AFTER_EXAMPLES = 5, 6  # SillyTavern's positions around the example messages

EXAMPLES_TEXT = 'Examples of how {char} speaks and acts. They show the style only: they aren\'t part of the story.'


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- what a prompt is made of ---------------------------------------------------------

class PromptPart(BaseModel):
    role: Role
    content: str
    kind: str = ''    # the block it comes from: 'instructions', 'lore', 'message', 'start'... ('' in old prompts)
    source: str = ''  # what it is, for the prompt view: 'Instructions', 'Lore: Eldoria › The forest', 'Mira'

    @property
    def tokens(self) -> int:
        return estimate(self.content) + MESSAGE_OVERHEAD


class LoreUse(BaseModel):
    """A lore entry that triggered, for the prompt view."""
    book: str
    entry: str
    uid: int
    reason: str                  # 'keyword "forest" in the chat'
    status: Literal['used', 'over_budget', 'skipped'] = 'used'
    placement: str = ''          # 'with the background', 'in the chat, 2 messages from the end'
    note: str = ''               # why it was skipped
    tokens: int = 0


class PromptReport(BaseModel):
    """How the prompt was put together: sizes, what was left out, which lore went in."""
    context_size: int
    context_known: bool          # False: the connection doesn't say, DEFAULT_CONTEXT was used
    reply_tokens: int
    reply_known: bool
    available: int               # space for the prompt
    tokens: int                  # the prompt's estimated size
    messages_total: int
    messages_left_out: int = 0
    lore_budget: int = 0
    lore_tokens: int = 0
    books: list[str] = Field(default_factory=list)          # names of the lore books in play
    missing_books: list[str] = Field(default_factory=list)  # remembered names of deleted ones
    lore: list[LoreUse] = Field(default_factory=list)
    lore_notes: list[str] = Field(default_factory=list)     # book features not applied yet
    memory_budget: int = 0       # 0: memories are off
    memory_tokens: int = 0
    memory_count: int = 0        # how many memories the chat has
    memories: list[LoreUse] = Field(default_factory=list)
    preset: str = ''             # the name of the prompt (presets.py) the instructions come from
    missing_preset: str = ''     # the remembered name of the one chosen, if it was deleted
    examples_total: int = 0      # the character's example dialogues
    examples_sent: int = 0       # how many went in (the first ones)
    examples_tokens: int = 0
    extend: ExtendMethod = ''    # to extend the last reply: how (see the notes at the top)
    continue_scene: bool = False  # to continue the scene, with a hidden instruction in your place

    @property
    def over_limit(self) -> bool:
        return self.tokens > self.available


class Prompt(StoredModel):
    """A prompt as built when a message was sent."""
    name: str = Field('', exclude=True)  # from StoredModel; unused
    format_version: int = 2
    created_at: datetime = Field(default_factory=_now)
    connection: str = ''  # the chat connection at the time, e.g. 'DeepSeek (deepseek/deepseek-chat)'; '' if none
    system_in_chat: Literal['system', 'user'] = 'system'  # how system parts inside the chat are sent
    parts: list[PromptPart]
    report: PromptReport | None = None  # None in prompts saved by the first version

    @model_validator(mode='before')
    @classmethod
    def _upgrade(cls, data: Any) -> Any:
        """Version 1 saved the messages as sent, without labels."""
        if isinstance(data, dict) and 'parts' not in data and 'messages' in data:
            data = {**data, 'parts': [{'role': m['role'], 'content': m['content']} for m in data['messages']]}
        return data

    def messages(self) -> list[Message]:
        """The messages actually sent to the model."""
        return to_messages(self.parts, self.system_in_chat)

    @property
    def tokens(self) -> int:
        return sum(part.tokens for part in self.parts)


def to_messages(parts: list[PromptPart], system_in_chat: str = 'system') -> list[Message]:
    """Parts as messages: same-role parts in a row are joined, system parts inside the chat become
    user messages when `system_in_chat` is 'user'."""
    messages: list[Message] = []
    in_chat = False
    for part in parts:
        role = part.role
        if role != 'system':
            in_chat = True
        elif in_chat and system_in_chat == 'user':
            role = 'user'
        if messages and messages[-1].role == role:
            messages[-1] = Message(role=role, content=f'{messages[-1].content}\n\n{part.content}')
        else:
            messages.append(Message(role=role, content=part.content))
    return messages


# --- building one ------------------------------------------------------------------------

def build_prompt(conversation: Conversation, *, character: Character | None, persona: Persona | None,
                 settings: Settings, connection: Connection | None = None,
                 books: list[LoreBook] = (), missing_books: list[str] = (),
                 memories: LoreBook | None = None, preset: PromptPreset | None = None,
                 extend: ExtendMethod = '', continue_scene: bool = False) -> Prompt:
    """The prompt for the model's next reply in this conversation.

    `character` and `persona` can be None if they were deleted: their remembered titles stand in for
    their names. `books` are the lore books in play (see lore_books_for), `memories` the chat's memories,
    `preset` the prompt chosen in Settings › Chat, with the instructions (None: none chosen, or deleted).
    `extend`: to extend the last message, a reply of the AI, rather than reply to it (see the notes at the top).
    `continue_scene`: for the AI's next message without one from you (the chat may end with the AI's, or be empty).
    """
    char = (character.name or character.title) if character else conversation.character.title
    user = (persona.name or persona.title) if persona else conversation.persona.title

    def filled(text: str) -> str:
        return fill(text, char=char, user=user).strip()

    def speaker(message: ChatMessage) -> str:
        return {'user': user, 'assistant': char}.get(message.role, 'System')

    blocks: dict[str, list[PromptPart]] = {name: [] for name in LAYOUT}

    def add(block: str, content: str, source: str) -> None:
        if content.strip():
            blocks[block].append(PromptPart(role='system', content=content, kind=block, source=source))

    # -- the fixed parts
    chat = settings.chat
    label = f' ({preset.name})' if preset is not None else ''
    add('instructions', filled(preset.instructions) if preset else '', f'Instructions{label}')
    if character and character.scenario.strip():  # (their description is only for you: never sent)
        add('scenario', f'About {char} and the story:\n{filled(character.scenario)}', 'Scenario')
    if persona:
        about = [line for line in (
            f'Gender: {GENDERS[persona.gender].lower()}' if persona.gender in GENDERS and persona.gender else '',
            f'Pronouns: {persona.pronouns}' if persona.pronouns else '',
            filled(persona.description),
        ) if line]
        if about:
            add('persona', f'About {user}, played by the user:\n' + '\n'.join(about), f'About {user}')
    add('final', filled(preset.final_instructions) if preset else '', f'Final instructions{label}')
    if extend == 'instruction':
        text = preset.extend_text if preset else EXTEND_INSTRUCTION
        blocks['hidden'] = [PromptPart(role='user', content=filled(text), kind='extend',
                                       source=f'Hidden instruction: extend the reply{label}')]
    elif continue_scene:
        text = preset.continue_text if preset else CONTINUE_INSTRUCTION
        blocks['hidden'] = [PromptPart(role='user', content=filled(text), kind='continue',
                                       source=f'Hidden instruction: continue the scene{label}')]

    # -- the space there is
    context_known = bool(connection and connection.context_window)
    context = connection.context_window if context_known else DEFAULT_CONTEXT
    reply_known = bool(connection and connection.params.max_tokens)
    reply = connection.params.max_tokens if reply_known else DEFAULT_REPLY
    available = max(0, context - reply - math.ceil(context * MARGIN))
    fixed = sum(part.tokens for parts in blocks.values() for part in parts)

    # -- lore, and the chat's memories (a lore book of their own, with their own budget)
    scan = dict(chat=[(speaker(m), m.content) for m in conversation.messages], settings=settings.lore,
                char=char, user=user,
                texts={'scenario': character.scenario if character else '',
                       'persona': persona.description if persona else ''})
    lore_budget = available * settings.lore.budget_percent // 100
    lore = activate(list(books), budget=lore_budget, **scan)

    def lore_parts(position: int | None = None) -> list[PromptPart]:
        """The lore with the background (position None), or at one position around the examples."""
        chosen = [a for a in lore.used if a.entry.position == position] if position is not None else \
            [a for a in lore.used if a.entry.position not in (AT_DEPTH, BEFORE_EXAMPLES, AFTER_EXAMPLES)]
        return [PromptPart(role='system', content=a.content, kind='lore', source=_lore_label(a))
                for a in sorted(chosen, key=lambda a: (BACKGROUND_ORDER.get(a.entry.position, 9), a.entry.order))]

    blocks['lore'] = lore_parts()

    use_memory = settings.memory.enabled and memories is not None and memories.entries
    memory_budget = available * settings.memory.budget_percent // 100 if settings.memory.enabled else 0
    remembered = activate([memories], budget=memory_budget, **scan) if use_memory else LoreResult()
    blocks['memory'] = [PromptPart(role='system', content=a.content, kind='memory',
                                   source=f'Memory: {a.entry.title}')
                        for a in sorted(remembered.used, key=lambda a: a.entry.uid)]

    # -- the example dialogues, and the chat
    dialogues = [filled(d) for d in character.example_dialogues if d.strip()] if character else []
    messages = conversation.messages
    costs = [estimate(m.content) + MESSAGE_OVERHEAD for m in messages]
    room = available - fixed - lore.tokens - remembered.tokens - MESSAGE_OVERHEAD  # overhead: the placeholder
    if chat.example_dialogues == 'always':
        sent = len(dialogues)
    else:  # while the whole chat fits along with them; the last ones leave first
        sent = len(dialogues)
        while sent and _examples(dialogues[:sent], char).tokens > room - sum(costs):
            sent -= 1
    examples = _examples(dialogues[:sent], char) if sent else None
    room -= examples.tokens if examples else 0
    blocks['examples'] = [*lore_parts(BEFORE_EXAMPLES), *([examples] if examples else []),
                          *lore_parts(AFTER_EXAMPLES)]
    start = first_kept(costs, room)
    kept = messages[start:]
    slots: dict[int, list[PromptPart]] = defaultdict(list)  # lore at a depth, by the message it goes before
    for a in sorted((a for a in lore.used if a.entry.position == AT_DEPTH), key=lambda a: a.entry.order):
        slots[max(0, len(kept) - max(0, a.entry.depth))].append(PromptPart(
            role=LORE_ROLES.get(a.entry.role or 0, 'system'), content=a.content, kind='lore',
            source=_lore_label(a)))
    history: list[PromptPart] = []
    for index, message in enumerate(kept):
        history += slots.get(index, [])
        history.append(PromptPart(role=message.role, content=message.content, kind='message',
                                  source=speaker(message)))
    history += slots.get(len(kept), [])
    first = next((part for part in history if part.role != 'system'), None)
    if first is not None and first.role == 'assistant':
        history.insert(0, PromptPart(role='user', content=TRIMMED_TEXT if start else START_TEXT, kind='start',
                                     source='Placeholder: some models need the chat to start with you'))
    blocks['history'] = history

    parts = [part for name in LAYOUT for part in blocks[name]]
    if extend == 'prefill' and kept and kept[-1].role == 'assistant':
        unfinished = next(part for part in reversed(parts) if part.kind == 'message')
        parts.remove(unfinished)
        parts.append(unfinished.model_copy(update={'content': unfinished.content.rstrip(),
                                                   'source': f'{unfinished.source}, to go on with (prefill)'}))
    report = PromptReport(
        context_size=context, context_known=context_known, reply_tokens=reply, reply_known=reply_known,
        available=available, tokens=sum(p.tokens for p in parts),
        messages_total=len(messages), messages_left_out=start,
        lore_budget=lore_budget, lore_tokens=lore.tokens,
        books=[b.name for b in books], missing_books=list(missing_books),
        lore=[_lore_use(a, examples=bool(examples)) for a in lore.activations], lore_notes=lore.notes,
        memory_budget=memory_budget, memory_tokens=remembered.tokens,
        memory_count=len(memories.entries) if memories else 0,
        memories=[_lore_use(a) for a in remembered.activations],
        examples_total=len(dialogues), examples_sent=sent, examples_tokens=examples.tokens if examples else 0,
        preset=preset.name if preset else '',
        missing_preset=chat.preset.name if chat.preset is not None and preset is None else '',
        extend=extend, continue_scene=continue_scene and not extend,
    )
    return Prompt(
        connection=f'{connection.name} ({connection.model or "no model chosen"})' if connection else '',
        system_in_chat=connection.system_in_chat if connection else 'system',
        parts=parts, report=report,
    )


def first_kept(costs: list[int], room: int) -> int:
    """Index of the first message that goes in, given each message's cost and the room for the chat.

    The newest messages go in, and the last one always does. When some must be left out, the cut
    moves in steps of TRIM_STEP of the room, so it stays put for several replies in a row.
    """
    total = sum(costs)
    if total <= room or not costs:
        return 0
    step = max(1, int(room * TRIM_STEP))
    threshold = math.ceil((total - room) / step) * step  # leave out at least this much, from the start
    left_out = 0
    for index, cost in enumerate(costs):
        if left_out >= threshold:
            return min(index, len(costs) - 1)
        left_out += cost
    return len(costs) - 1


def _examples(dialogues: list[str], char: str) -> PromptPart:
    numbered = [f'[Example {i}]\n{text}' for i, text in enumerate(dialogues, 1)] if len(dialogues) > 1 else dialogues
    return PromptPart(role='system', content='\n\n'.join([EXAMPLES_TEXT.format(char=char), *numbered]),
                      kind='examples', source='Example dialogues')


def _lore_label(a: Activation) -> str:
    return f'Lore: {a.book.name} › {a.entry.title}'


def _lore_use(a: Activation, *, examples: bool = False) -> LoreUse:
    entry = a.entry
    if entry.position == AT_DEPTH:
        role = LORE_ROLES.get(entry.role or 0, 'system')
        where = 'after the last message' if entry.depth <= 0 else \
            f'{entry.depth} message{"s" if entry.depth > 1 else ""} from the end'
        placement = f'in the chat, {where}, as {role}'
    elif entry.position in (BEFORE_EXAMPLES, AFTER_EXAMPLES):
        side = 'before' if entry.position == BEFORE_EXAMPLES else 'after'
        placement = f'{side} the example dialogues' if examples else \
            'with the background (no example dialogues are sent)'
    else:
        note = BACKGROUND_NOTES.get(entry.position)
        placement = 'with the background' + (f' ({note})' if note else '')
    return LoreUse(book=a.book.name, entry=entry.title, uid=entry.uid, reason=a.reason, status=a.status,
                   placement=placement if a.status == 'used' else '', note=a.note, tokens=a.tokens)


def lore_books_for(character: Character | None, persona: Persona | None,
                   store: JsonStore[LoreBook]) -> tuple[list[LoreBook], list[str]]:
    """The lore books in play (the character's, then the persona's, each once), and the names of missing ones."""
    refs = [*(character.lorebooks if character else []), *(persona.lorebooks if persona else [])]
    books, missing, seen = [], [], set()
    for ref in refs:
        if ref.id in seen:
            continue
        seen.add(ref.id)
        try:
            book = store.get(ref.id)
        except ValueError:  # a broken file: treated as missing
            book = None
        if book is None:
            missing.append(ref.name)
        else:
            books.append(book)
    return books, missing
