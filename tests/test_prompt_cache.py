"""What keeps the start of the prompt the same from one reply to the next, for the providers that cache it:
the example dialogues before the lore, the chat's room counting lore and memories in full (so the cut moves
only as the chat grows, and memories come at a steady pace), and OpenRouter keeping each chat on one
provider (session_id)."""
from nicegui import ui
from nicegui.testing import user_simulation
from test_memory import ADD_BRANN, LONG, make_chat
from test_prompts import cast, chat

from rpapp.connections import (
    SESSION, GenerationParams, Message, OpenAICompatibleConnection, OpenRouterConnection, session_for,
)
from rpapp.lorebooks import new_lore_book
from rpapp.prompts import build_prompt
from rpapp.settings import Settings
from rpapp.tokens import estimate
from rpapp.ui.app import build_root
from rpapp.ui.prompt_view import show_prompt

HELLO = [Message(role='user', content='Hello')]


def connection() -> OpenRouterConnection:
    """2,500 tokens for the prompt: 625 for lore (25%), 375 for memories (15%)."""
    conn = OpenRouterConnection(name='Small', model='m', context_window=3_000)
    conn.params.max_tokens = 200
    return conn


def forest_book():
    book = new_lore_book('Eldoria')
    book.add_entry(comment='Forest', key=['forest'], content='The forest is old and full of eyes. ' * 8, position=1)
    return book


def forest_memories():
    memories = new_lore_book('Memories')
    memories.add_entry(comment='The fox', key=['forest'], content='Mira followed a silver fox into the trees. ' * 3)
    return memories


def growing(word: str, *, books=(), memories=None, length: int = 90):
    """As the chat grows, reply after reply: (messages left out, example dialogues sent, lore, memory tokens).
    `word` is in every third message (with scan depth 2, entries keyed on it come and go)."""
    character, persona = cast()
    character.example_dialogues = [f'{{{{char}}}}: Example {i}. ' + 'words ' * 60 for i in range(3)]
    texts = [f'We walk on through the {word if i % 3 == 0 else "garden"}. ' + 'blah ' * 40 for i in range(length)]
    steps = []
    for n in range(1, length + 1):
        r = build_prompt(chat(*texts[:n]), character=character, persona=persona, settings=Settings(),
                         connection=connection(), books=list(books), memories=memories).report
        steps.append((r.messages_left_out, r.examples_sent, r.lore_tokens, r.memory_tokens))
    return steps


# --- 3: the example dialogues before the lore ---------------------------------------------------

def test_lore_coming_and_going_leaves_what_is_before_it_alone():
    character, persona = cast()
    character.example_dialogues = ['{{char}}: Rest now.']
    book = forest_book()
    book.add_entry(comment='Rule', constant=True, content='Magic has a price.', position=0)

    def parts(*texts):
        prompt = build_prompt(chat(*texts), character=character, persona=persona, settings=Settings(), books=[book])
        return [(p.kind, p.source, p.content) for p in prompt.parts]

    quiet, forest = parts('Welcome.', 'Hello.'), parts('Welcome.', 'Is this forest old?')
    assert [kind for kind, _, _ in forest][:4] == ['scenario', 'examples', 'lore', 'lore']
    assert quiet[:3] == forest[:3]  # (the scenario, the examples, and the lore always in: the same, cached)
    assert ('lore', 'Lore: Eldoria › Forest') not in [(k, s) for k, s, _ in quiet]


# --- 4: the chat's room, with lore and memories counted in full ----------------------------------

def test_the_cut_moves_only_as_the_chat_grows():
    plain = growing('garden', books=[forest_book()], memories=forest_memories())  # (no entry ever comes in)
    both = growing('forest', books=[forest_book()], memories=forest_memories())
    assert {tokens for _, _, tokens, _ in plain} == {0} == {tokens for _, _, _, tokens in plain}
    assert len({tokens for _, _, tokens, _ in both}) > 1 and len({tokens for _, _, _, tokens in both}) > 1  # (came,
    # and went)
    cut = [left_out for left_out, *_ in plain]
    assert [left_out for left_out, *_ in both] == cut  # (whatever came in)
    assert cut == sorted(cut) and cut[-1] > 0  # (never back: the start of the chat stays left out)
    moves = [n for n in range(1, len(cut)) if cut[n] > cut[n - 1]]
    gaps = [b - a for a, b in zip(moves, moves[1:])]
    assert len(moves) >= 4 and max(gaps) - min(gaps) <= 1  # (memories, from what's left out: as regular)

    examples = [sent for _, sent, *_ in plain]
    assert [sent for _, sent, *_ in both] == examples == sorted(examples, reverse=True)  # (they leave for good)
    assert examples[0] == 3 and examples[-1] == 0


def test_the_room_kept_for_lore_and_memories():
    character, persona = cast()

    def kept(settings=None, books=(), memories=None) -> int:
        return build_prompt(chat('Welcome.', 'Hello.'), character=character, persona=persona,
                            settings=settings or Settings(), connection=connection(), books=list(books),
                            memories=memories).report.reserved

    off = Settings()
    off.memory.enabled = False
    assert kept(off) == 0  # (no books, no memories: the chat has it all)
    assert kept() == 375  # (memories on: all their share, even before there are any)
    assert kept(memories=forest_memories()) == 375  # (whether they come in or not)

    small = forest_book()  # (one entry, not in the chat: what the book holds, less than the lore's share)
    entry_tokens = estimate(small.entries['0'].content.strip())
    assert kept(off, [small]) == entry_tokens < 625
    big = new_lore_book('Atlas')
    for i in range(10):
        big.add_entry(comment=f'Place {i}', key=[f'place{i}'], content='A long description of a place. ' * 12)
    assert kept(off, [big]) == 625  # (more than its share: the share)
    big.add_entry(comment='Always', key=['nowhere'], content='Even this. ' * 10, ignoreBudget=True)
    assert kept(off, [big]) == 625 + estimate(('Even this. ' * 10).strip())  # (ignoring the budget: on top)
    for fields in ({'disable': True}, {'position': 7}, {'triggers': ['impersonate']}):
        other = new_lore_book('Never')  # (off, an outlet, or not for replies: never in)
        other.add_entry(comment='x', key=['x'], content='Not this. ' * 30, **fields)
        assert kept(off, [other]) == 0


async def test_the_prompt_view_says_so():
    character, persona = cast()
    texts = ['We walk on. ' + 'blah ' * 40] * 60
    prompt = build_prompt(chat(*texts), character=character, persona=persona, settings=Settings(),
                          connection=connection())
    left_out = prompt.report.messages_left_out
    assert left_out and prompt.report.reserved == 375

    def root() -> None:
        ui.button('Show', on_click=lambda: show_prompt(prompt, None, time=str, copy_js=lambda _: '')).mark('show')

    async with user_simulation(root) as user:
        await user.open('/')
        user.find(marker='show').click()
        await user.should_see(f'{left_out} older messages of 60 left out to fit. The room for lore and memories, '
                              '375 tokens, counts in full, used or not, so this changes only as the chat grows.')


# --- 5: OpenRouter keeping a chat on the provider that has it cached --------------------------

def test_openrouter_gets_the_chats_session():
    conn = OpenRouterConnection(api_key='k', model='m/x')
    assert 'session_id' not in conn._body(HELLO, None)  # (no chat: testing the connection, say)
    token = SESSION.set(session_for('c-1'))
    try:
        assert conn._body(HELLO, GenerationParams())['session_id'] == session_for('c-1')
        assert 'session_id' not in OpenAICompatibleConnection(model='m')._body(HELLO, None)  # (OpenRouter's only)
    finally:
        SESSION.reset(token)
    session = session_for('c-1')
    assert len(session) == 32 and 'c-1' not in session  # (OpenRouter's limit is 256; it says nothing of the chat)
    assert len({session, session_for('c-1', 'memory'), session_for('c-2')}) == 3


async def test_each_chat_its_session_replies_and_memories_apart(tmp_path, fake_model):
    conversations, conversation = make_chat(tmp_path, context=390)  # (as in test_memory: one memory call)
    fake_model.memory_answers = [ADD_BRANN]
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        user.find(marker='chat-input').type('Hello.')
        user.find(marker='send').click()
        await user.should_see('Rest now.')
        user.find(marker='chat-input').type(LONG + 'Brann helped me cross.')
        user.find(marker='send').click()
        await user.should_see(marker='memory-review')
    reply, memory = session_for(conversation.id), session_for(conversation.id, 'memory')
    assert fake_model.sessions == [reply, reply, memory]
    assert SESSION.get() == ''  # (set in each call's task only)
