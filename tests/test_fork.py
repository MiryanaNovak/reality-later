"""Forking a chat at one of the AI's replies (⋯ › Fork from here): a new chat, a copy of it up to and including
that reply, with the memories exactly as they were right after it (conversations.py, memory.py)."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from nicegui.testing import user_simulation
from test_chat import choose, send, the
from test_memory import ADD_BRANN, LONG, make_chat, proposal

from rpapp.conversations import ChatMessage
from rpapp.memory import Memories
from rpapp.prompts import Prompt
from rpapp.replies import ReplyRecord
from rpapp.ui.app import build_root
from rpapp.ui.chat_page import BUSY_TEXT


def chat(*ids: str) -> list[ChatMessage]:
    """Messages: 'g' the greeting, 'u…' yours, 'a…' the AI's."""
    return [ChatMessage(id=i, role='user' if i.startswith('u') else 'assistant', content=i, greeting=i == 'g')
            for i in ids]


def names(book) -> list[str]:
    return [e.comment for e in book.sorted_entries()]


# --- the memories as they were ----------------------------------------------------------------

def test_the_update_written_after_the_reply_is_pending_in_its_fork():
    m, messages = Memories(id='c'), chat('g', 'u1', 'a1')
    m.propose('g', 'u1', proposal(m, 'Brann'))                       # written after a1
    m.moving_on('a1', posting=True)                                  # you post u2: applied
    messages += chat('u2', 'a2')
    m.propose('a1', 'u2', proposal(m, 'Oona'))                       # written after a2
    m.pending.proposed.entries['1'].content = 'Oona, as you reviewed her.'
    m.moving_on('a2', posting=True)
    messages += chat('u3', 'a3')
    m.propose('a2', 'u3', proposal(m, 'Ford'))                       # still pending

    at_a1 = m.as_of('a1', messages, new_id='f1')
    assert at_a1.id == 'f1' and at_a1.book.entries == {} and at_a1.read_until == '' and at_a1.history == []
    assert names(at_a1.pending.proposed) == ['Brann'] and (at_a1.pending.first, at_a1.pending.last) == ('g', 'u1')

    at_a2 = m.as_of('a2', messages, new_id='f2')
    assert names(at_a2.book) == ['Brann'] and at_a2.read_until == 'u1' and len(at_a2.history) == 1
    assert names(at_a2.pending.proposed) == ['Brann', 'Oona']
    assert at_a2.pending.proposed.entries['1'].content == 'Oona, as you reviewed her.'

    at_a3 = m.as_of('a3', messages, new_id='f3')  # (the last message: as they are now)
    assert at_a3.model_dump(exclude={'id'}) == m.model_dump(exclude={'id'})

    # A fork goes on as its original did.
    at_a1.moving_on('a1', posting=True)
    assert names(at_a1.book) == ['Brann'] and at_a1.read_until == 'u1' and at_a1.pending is None
    assert at_a1.forget({'u1', 'a1'}) and at_a1.book.entries == {}
    assert names(m.book) == ['Brann', 'Oona']  # (the original's are its own)


def test_your_own_changes_stay_where_you_made_them():
    m, messages = Memories(id='c'), chat('g', 'u1', 'a1')
    m.propose('g', 'u1', proposal(m, 'Brann'))
    m.moving_on('a1', posting=True)
    messages += chat('u2', 'a2')
    m.book.entries['0'].content = 'Brann moved to the coast.'  # on the Memories page, while a2 is the last message
    assert m.moving_on('a2', posting=True) and m.history[-1].yours
    messages += chat('u3', 'a3')
    m.book.entries['0'].content = 'Brann came back.'           # while a3 is the last one

    assert m.as_of('a1', messages, new_id='f').pending.proposed.entries['0'].content == 'About Brann.'
    at_a2 = m.as_of('a2', messages, new_id='f')
    assert at_a2.book.entries['0'].content == 'Brann moved to the coast.' and at_a2.pending is None
    assert m.as_of('a3', messages, new_id='f').book.entries['0'].content == 'Brann came back.'

    # Undone with the update before them, when its messages are deleted.
    assert not m.forget({'u3', 'a3'})
    assert m.forget({'u1', 'a1', 'u2', 'a2', 'u3', 'a3'}) and m.history == [] and m.book.entries == {}


def test_when_the_ai_continued_the_scene():
    m, messages = Memories(id='c'), chat('g', 'u1', 'a1')
    m.propose('g', 'u1', proposal(m, 'Brann'))  # written after a1
    m.moving_on('a1', posting=False)            # the AI continues the scene: it stays pending
    messages += chat('a2')
    m.propose('a1', 'a1', proposal(m, 'Oona'))  # and grows after a2
    assert m.pending.first == 'g' and names(m.pending.proposed) == ['Brann', 'Oona']

    def at(message_id):
        return m.as_of(message_id, messages, new_id='f')

    assert at('a1').book.entries == {} and names(at('a1').pending.proposed) == ['Brann']
    assert at('a1').pending.last == 'u1'
    m.moving_on('a2', posting=True)
    messages += chat('u2', 'a3')
    assert at('a1').book.entries == {} and names(at('a1').pending.proposed) == ['Brann']
    assert at('a2').book.entries == {} and names(at('a2').pending.proposed) == ['Brann', 'Oona']
    assert names(at('a3').book) == ['Brann', 'Oona'] and at('a3').pending is None

    # A fork of the fork, at the reply before.
    fork = at('a2')
    assert names(fork.as_of('a1', messages[:4], new_id='ff').pending.proposed) == ['Brann']


def test_files_from_before_are_placed_by_the_times():
    """Memories saved before forks existed say nothing of where in the chat they were applied: their times do."""
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    messages = chat('g', 'u1', 'a1', 'u2', 'a2', 'u3', 'a3')
    for minute, message in enumerate(messages):
        message.created_at = start + timedelta(minutes=minute)
    m = Memories(id='c')
    m.propose('g', 'u1', proposal(m, 'Brann'))
    m.apply_pending()  # (you posted u2)
    m.propose('a1', 'u2', proposal(m, 'Oona'))
    m.apply_pending()  # (u3)
    data = m.model_dump(mode='json', exclude={'settled'})
    data['format_version'] = 1
    for step, minute in zip(data['history'], (3, 5)):  # just before your messages
        del step['applied_after'], step['yours'], step['earlier']
        step['applied_at'] = (start + timedelta(minutes=minute, seconds=-1)).isoformat()
    old = Memories.model_validate(data)
    assert old.settled is None

    at_a1 = old.as_of('a1', messages, new_id='f')
    assert at_a1.book.entries == {} and names(at_a1.pending.proposed) == ['Brann']
    at_a2 = old.as_of('a2', messages, new_id='f')
    assert names(at_a2.book) == ['Brann'] and names(at_a2.pending.proposed) == ['Brann', 'Oona']
    assert old.moving_on('a3', posting=True) and old.settled is not None and not any(s.yours for s in old.history)


# --- the copy of the chat ---------------------------------------------------------------------

def test_fork_in_the_store(tmp_path):
    conversations, c = make_chat(tmp_path)
    greeting = c.messages[0]
    u1, a1 = c.add('user', 'Hello.'), c.add('assistant', 'Rest now.')
    a1.alternatives, a1.alternative = ['Sleep.', 'Rest now.'], 1
    u2, a2 = c.add('user', 'Again.'), c.add('assistant', 'Still here.')
    conversations.save(c)
    for message in (u1, u2):
        conversations.prompts.save(c.id, message.id, Prompt(parts=[], connection=f'for {message.content}'))
    for message in (a1, a2):
        conversations.replies.save(c.id, message.id, ReplyRecord(versions=[None]))
    memories = conversations.memories.get(c.id)
    memories.propose(greeting.id, u1.id, proposal(memories, 'Brann'))  # written after a1
    memories.moving_on(a1.id, posting=True)                            # applied as you posted u2
    conversations.memories.save(memories)
    vectors = tmp_path / 'vectors' / 'some-model' / 'memories'
    vectors.mkdir(parents=True)
    (vectors / f'{c.id}.json').write_text('{"vectors": {}}', encoding='utf-8')

    fork = conversations.fork(c.id, a1.id)
    saved = conversations.load(fork.id)
    assert saved.title == f'{c.title} (fork)' and saved.id != c.id
    assert [m.id for m in saved.messages] == [greeting.id, u1.id, a1.id]  # (the same ids)
    assert saved.messages[2].alternatives == ['Sleep.', 'Rest now.'] and saved.messages[2].alternative == 1
    assert (saved.character, saved.persona, saved.connection, saved.prompt) == \
        (c.character, c.persona, c.connection, c.prompt)
    assert saved.created_at > c.created_at
    assert conversations.prompts.get(fork.id, u1.id).connection == 'for Hello.'
    assert conversations.prompts.get(fork.id, u2.id) is None
    assert conversations.replies.get(fork.id, a1.id) is not None and conversations.replies.get(fork.id, a2.id) is None
    forked = conversations.memories.get(fork.id)
    assert forked.book.entries == {} and names(forked.pending.proposed) == ['Brann']
    assert names(conversations.memories.get(c.id).book) == ['Brann']
    assert (vectors / f'{fork.id}.json').read_text(encoding='utf-8') == '{"vectors": {}}'
    assert len(conversations.load(c.id).messages) == 5  # (the original as it was)

    assert conversations.fork(c.id, a2.id).title == f'{c.title} (fork 2)'
    assert conversations.fork(fork.id, a1.id).title == f'{c.title} (fork 3)'  # (a fork's fork: no "(fork) (fork)")
    for message in (u1, greeting):
        with pytest.raises(ValueError):
            conversations.fork(c.id, message.id)
    with pytest.raises(KeyError):
        conversations.fork(c.id, 'nope')
    long_title = conversations.rename(c.id, 'x' * 200).title
    assert conversations.fork(c.id, a1.id).title == 'x' * 193 + ' (fork)'
    assert long_title == 'x' * 200

    plain, other = make_chat(tmp_path / 'plain')
    other.add('user', 'Hi.')
    reply = other.add('assistant', 'Hello.')
    plain.save(other)
    fork = plain.fork(other.id, reply.id)
    assert not plain.memories.exists(fork.id)  # (no memories: none made for the fork either)


# --- in the chat ------------------------------------------------------------------------------

def menu_items(user, message_id: str) -> set[str]:
    """Open a message's ⋯ menu (made as it opens): what's in it."""
    box = next(e for e in user.client.layout.descendants() if f'message-{message_id}' in e._markers)
    with user.scope(marker=f'message-{message_id}'):
        user.find(marker='actions').click()
    return {m for e in box.descendants() for m in e._markers if m.startswith('menu-')}


async def test_fork_from_the_chat(tmp_path, fake_model):
    conversations, conversation = make_chat(tmp_path, context=390)  # (half the room: kept for memories)
    fake_model.memory_answers = [ADD_BRANN]
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await send(user, 'Hello.')
        await send(user, LONG + 'Brann helped me cross.')
        await user.should_see('Memories generated from 3 messages: 1 new.')  # (written after the second reply)
        await send(user, 'Where is Brann now?')                             # (applied)
        messages = conversations.load(conversation.id).messages
        greeting, hello, r1, _, r2, _, r3 = messages

        assert 'menu-fork' not in menu_items(user, greeting.id)
        assert 'menu-fork' not in menu_items(user, hello.id)
        assert 'menu-fork' in menu_items(user, r1.id)
        with user.scope(marker=f'message-{r2.id}'):
            choose(user, 'menu-fork')
        await user.should_see(f'Forked as "{conversation.title} (fork)"')
        [fork] = [c for c in conversations.list() if c.id != conversation.id]
        assert [m.id for m in fork.messages] == [m.id for m in messages[:5]]
        await user.should_not_see(marker=f'message-{r3.id}')  # (the fork is open)
        await user.should_see(marker=f'message-{r2.id}')
        memories = conversations.memories.get(fork.id)
        assert memories.book.entries == {} and names(memories.pending.proposed) == ['Brann the ferryman']
        await user.should_see('Memories generated from 3 messages: 1 new.')  # (as after that reply)

        await send(user, 'Where did Brann go?')  # the fork goes on: its memories are applied
        assert names(conversations.memories.get(fork.id).book) == ['Brann the ferryman']
        assert len(conversations.load(conversation.id).messages) == 7  # (the original as it was)


async def test_not_while_a_reply_is_on_its_way(tmp_path, fake_model):
    conversations, conversation = make_chat(tmp_path, context=50_000)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await send(user, 'Hello.')
        reply = conversations.load(conversation.id).messages[-1]
        fake_model.gate = asyncio.Event()
        await send(user, 'Again.', wait=False)
        assert not the(user, 'chat-input').enabled  # (replying)
        with user.scope(marker=f'message-{reply.id}'):
            choose(user, 'menu-fork')
        await user.should_see(BUSY_TEXT)
        assert len(conversations.list()) == 1
        fake_model.gate.set()
