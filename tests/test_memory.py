"""Memories: reading the AI's answer, applying and undoing updates, and when they're generated."""
import asyncio
import json

import pytest
from conftest import FakeConnection
from test_chat import choose
from nicegui import ui
from nicegui.testing import user_simulation

from rpapp.characters import CharacterStore, new_character
from rpapp.connections import connection_store
from rpapp.conversations import ChatMessage, ConversationStore
from rpapp.lorebooks import new_lore_book
from rpapp.memory import (
    MemoryCallError, MemoryChange, Memories, add_memory, apply_changes, batches, memory_prompt, parse_changes,
    unread,
)
from rpapp.personas import PersonaStore, new_persona
from rpapp.settings import ConnectionRef, SettingsStore
from rpapp.ui.app import build_root


# --- the AI's answer -------------------------------------------------------------------------

def test_parse_changes():
    answer = 'Here you go:\n```json\n[{"action": "add", "title": "Brann", "keys": "Brann", "text": "A ferryman."},' \
             ' {"action": "explode"}, {"action": "remove", "id": 2}]\n```'
    changes = parse_changes(answer)
    assert [c.action for c in changes] == ['add', 'remove']  # the odd one is skipped
    assert changes[0].keys == ['Brann']
    assert parse_changes('[]') == []
    for bad in ('Nothing to add.', '[{"action": "add", "text": "x"', '[{"action": "fly"}]'):
        with pytest.raises(MemoryCallError):
            parse_changes(bad)


def test_apply_changes():
    book = new_lore_book('Memories')
    apply_changes(book, [MemoryChange(action='add', title='Brann', keys=['Brann', 'brann', ' ferry '],
                                      text='A gruff ferryman.'),
                         MemoryChange(action='add', title='Sera', keys=['Sera'], text='Bond.', always=True),
                         MemoryChange(action='add', title='Empty', keys=['x'], text='  ')])
    assert [(e.comment, e.key, e.constant) for e in book.sorted_entries()] == [
        ('Brann', ['Brann', 'ferry'], False), ('Sera', ['Sera'], True)]
    apply_changes(book, [MemoryChange(action='update', id=0, text='Brann owes Mira a favour.'),
                         MemoryChange(action='remove', id=1),
                         MemoryChange(action='update', id=9, text='unknown: skipped')])
    assert [(e.comment, e.content) for e in book.sorted_entries()] == [('Brann', 'Brann owes Mira a favour.')]


def test_memory_prompt():
    book = new_lore_book('Memories')
    empty = memory_prompt(instructions='', char='Seraphina', user='Mira', character='Guardian of {{user}}.',
                          persona='', book=book, lines=['Mira: Hi.'])
    assert empty[0].role == 'system' and 'You keep the memory of a long roleplay between Seraphina and Mira' \
        in empty[0].content and 'Answer with a JSON array of changes' in empty[0].content
    assert '# Seraphina and the story\nGuardian of Mira.' in empty[1].content and '(none yet)' in empty[1].content
    assert empty[1].content.endswith('# Messages to remember\nMira: Hi.')
    book.add_entry(comment='Brann', key=['Brann'], content='A ferryman.', constant=True)
    listed = memory_prompt(instructions='Be brief.', char='S', user='M', character='', persona='', book=book,
                           lines=[])
    assert listed[0].content.startswith('Be brief.')
    assert '[id 0] Brann (keys: Brann) [always]\nA ferryman.' in listed[1].content


def test_unread_and_batches():
    messages = [ChatMessage(id=f'm{i}', role='user', content='word ' * 700) for i in range(5)]  # 1000 tokens each
    memories = Memories(id='c')
    assert [m.id for m in unread(messages, memories, until=3)] == ['m0', 'm1', 'm2']
    memories.read_until = 'm1'
    assert [m.id for m in unread(messages, memories, until=3)] == ['m2']
    assert unread(messages, memories, until=1) == []
    groups = batches([(m, m.content) for m in messages], limit=2500)
    assert [[m.id for m, _ in g] for g in groups] == [['m0', 'm1'], ['m2', 'm3'], ['m4']]


# --- applying and undoing --------------------------------------------------------------------

def proposal(memories: Memories, *titles: str):
    book = memories.editable.model_copy(deep=True)
    for title in titles:
        add_memory(book, taken=memories.taken, comment=title, key=[title], content=f'About {title}.')
    return book


def test_updates_are_applied_then_undone_when_their_messages_go():
    memories = Memories(id='c')
    memories.propose('m0', 'm3', proposal(memories, 'Brann'))
    assert memories.changes().summary == '1 new' and memories.last_read == 'm3' and memories.read_until == ''
    memories.propose('m4', 'm5', proposal(memories, 'Oona'))  # generated again before your next message
    assert memories.pending.first == 'm0' and memories.changes().summary == '2 new'
    assert memories.apply_pending()
    assert [e.comment for e in memories.book.sorted_entries()] == ['Brann', 'Oona'] and memories.read_until == 'm5'
    assert not memories.apply_pending()

    memories.propose('m6', 'm9', proposal(memories, 'Ford'))
    memories.apply_pending()
    memories.propose('m10', 'm12', proposal(memories, 'Storm'))  # pending

    assert not memories.forget({'m13', 'm14'})  # messages it hadn't read: nothing to undo
    assert memories.forget({'m8', 'm9', 'm10', 'm11', 'm12', 'm13'})
    assert memories.pending is None
    assert [e.comment for e in memories.book.sorted_entries()] == ['Brann', 'Oona'] and memories.read_until == 'm5'
    memories.forget({f'm{i}' for i in range(14)})
    assert memories.book.entries == {} and memories.read_until == '' and memories.history == []


def test_changes_compare_with_the_memories_in_use():
    memories = Memories(id='c')
    memories.propose('m0', 'm1', proposal(memories, 'Brann', 'Oona'))
    memories.apply_pending()
    book = memories.book.model_copy(deep=True)
    book.entries['0'].content = 'Brann moved away.'
    book.remove_entry(1)
    add_memory(book, taken=memories.taken, comment='Ford', key=['ford'], content='A ford.')
    memories.propose('m2', 'm3', book)
    changes = memories.changes()
    assert (changes.added, changes.changed, changes.removed) == (['2'], ['0'], ['1'])
    assert changes.summary == '1 new, 1 changed, 1 removed'


# --- in the app --------------------------------------------------------------------------------

def make_chat(tmp_path, *, context=600, scenario=''):
    """Seraphina and Mira, the fake model as chat connection with a small context, and a started chat."""
    characters, personas, conversations = CharacterStore(tmp_path), PersonaStore(tmp_path), ConversationStore(tmp_path)
    (tmp_path / 'presets').mkdir()  # no prompt, not even the starter one: the context here is tiny
    character, persona = new_character('Seraphina'), new_persona('Mira')
    character.first_messages = ['*Seraphina kneels beside {{user}}.*']
    character.scenario = scenario
    character.description = 'Just for the list.'  # only for the user: never sent, to memory calls either
    characters.save(character)
    personas.save(persona)
    conn = FakeConnection(name='Fake', model='fake/model', context_window=context)
    conn.params.max_tokens = 100
    connection_store(tmp_path).save(conn)
    settings = SettingsStore(tmp_path)
    s = settings.load()
    s.chat.connection = ConnectionRef(id=conn.id, name='Fake')
    s.memory.budget_percent = 50
    settings.save(s)
    return conversations, conversations.start(character, persona)


ADD_BRANN = json.dumps([{'action': 'add', 'title': 'Brann the ferryman', 'keys': ['Brann'],
                         'text': 'Brann, a gruff ferryman, owes Mira a favour.'}])


def the(user, marker):
    return user.find(marker=marker).elements.pop()


async def send(user, text: str) -> None:
    user.find(marker='chat-input').type(text)
    user.find(marker='send').click()
    await settled(user)


async def settled(user) -> None:
    for _ in range(150):
        await asyncio.sleep(0.03)
        if the(user, 'chat-input').enabled:
            return
    raise AssertionError('still busy')


LONG = 'We talked for a long time about the river, the ferry and the old roads. ' * 4  # about 85 tokens


def marker_state(user) -> tuple[str, str, bool, bool]:
    """The dashed line: (id of the message just above it, its text, linked to the bubble, pulsing)."""
    marker = next(e for e in user.client.layout.descendants() if 'memory-marker' in e._markers)  # even hidden
    if not marker.visible:
        return '', '', False, False
    children = marker.parent_slot.children
    above = children[children.index(marker) - 1]
    message_id = next(m for m in above._markers if m.startswith('message-')).removeprefix('message-')
    label = next(e for e in marker.descendants() if isinstance(e, ui.label)).text
    column = marker.parent_slot.parent.parent_slot.parent  # the chat page's column
    return message_id, label, 'memory-threaded' in column.classes, 'memory-pulsing' in column.classes


async def test_memories_after_old_messages_leave_the_prompt(tmp_path, fake_model):
    conversations, conversation = make_chat(tmp_path, context=250)
    fake_model.memory_answers = [ADD_BRANN]
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await send(user, 'Hello.')  # all of it still fits: no memories
        assert fake_model.memory_calls == []
        await user.should_not_see(marker='memory-status')

        assert marker_state(user) == ('', '', False, False)  # nothing read yet: no line

        fake_model.memory_gate = asyncio.Event()
        user.find(marker='chat-input').type(LONG + 'Brann helped me cross.')
        user.find(marker='send').click()
        await user.should_see(marker='memory-writing')  # after the reply
        await user.should_see('Rest now.')
        await user.should_see('Generating memories from 3 messages')
        assert not the(user, 'chat-input').enabled and the(user, 'send').props['icon'] == 'stop'
        first_reply = conversations.load(conversation.id).messages[2].id
        # The line is already where the memories will read up to (the greeting, Hello and the first reply).
        assert marker_state(user) == (first_reply, 'Generating memories up to here…', True, True)
        fake_model.memory_gate.set()
        await settled(user)
        await user.should_see('Memories generated from 3 messages: 1 new.')
        await user.should_not_see(marker='memory-writing')
        assert marker_state(user) == (
            first_reply, 'Memories generated up to here: added with your next message', True, False)
        user.find(marker='memory-show').click()  # scrolls up to the line (in the browser)

        [call] = fake_model.memory_calls
        assert call[1].content.split('# Messages to remember\n')[1].startswith('Seraphina: *Seraphina kneels')
        memories = conversations.memories.get(conversation.id)
        assert memories.book.entries == {} and memories.pending is not None  # not applied yet

        # Review: the update shows, and can be edited.
        user.find(marker='memory-review').click()
        await user.should_see(marker='memories-pending')
        await user.should_see('New memories: 1 new')
        with user.scope(marker='memory-0'):
            await user.should_see('New')
            the(user, 'memory-text').set_value('Brann owes Mira a favour since she set his wrist.')
        conversations.memories.flush()
        assert conversations.memories.get(conversation.id).pending.proposed.entries['0'].content.endswith('wrist.')

        # Posting the next message applies it, before the reply's prompt is built.
        await user.open(f'/conversations/{conversation.id}')
        await user.should_see('Memories generated from 3 messages: 1 new.')
        assert marker_state(user)[0] == first_reply  # the same after reopening the page
        await send(user, 'Where is Brann now?')
        await user.should_not_see('Memories generated from 3 messages: 1 new.')
        memories = conversations.memories.get(conversation.id)
        assert memories.book.entries['0'].comment == 'Brann the ferryman'  # applied
        assert memories.changes().summary == ''  # (more messages left the prompt since: nothing new in them)
        prompt = conversations.prompts.get(conversation.id, conversations.load(conversation.id).messages[-2].id)
        assert any(p.kind == 'memory' and 'set his wrist' in p.content for p in prompt.parts)
        assert prompt.report.memories[0].reason == 'keyword "Brann" in the chat'


async def test_generate_now_and_undo_by_deleting(tmp_path, fake_model):
    conversations, conversation = make_chat(tmp_path, context=50_000, scenario='The guardian of the glade.')
    fake_model.memory_answers = [ADD_BRANN]
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await send(user, 'Brann helped me cross the river.')
        user.find(marker='menu-generate-memories-now').click()
        await settled(user)
        await user.should_see('Memories generated from 2 messages: 1 new.')
        # It read everything but the last reply, which can still change: the line is above that reply.
        [call] = fake_model.memory_calls
        assert call[1].content.endswith('Mira: Brann helped me cross the river.')
        assert '# Seraphina and the story\nThe guardian of the glade.' in call[1].content  # what the AI knows
        assert 'Just for the list' not in call[1].content  # the description is only for you
        mine = conversations.load(conversation.id).messages[1]
        assert marker_state(user)[:3] == (
            mine.id, 'Memories generated up to here: added with your next message', True)

        await send(user, 'Thanks.')  # applies it
        memories = conversations.memories.get(conversation.id)
        assert list(memories.book.entries) == ['0']
        assert marker_state(user) == (mine.id, 'Remembered up to here', False, False)  # it stays

        user.find(marker='menu-generate-memories-now').click()
        await settled(user)
        await user.should_see('Memories checked from 2 messages: nothing new to remember.')
        user.find(marker='menu-generate-memories-now').click()
        await user.should_see('Nothing new to remember')

        # Deleting the messages it learnt from undoes it.
        saved = conversations.load(conversation.id)
        mine = saved.messages[1]
        with user.scope(marker=f'message-{mine.id}'):
            choose(user, 'menu-delete')
        await user.should_see(marker='confirm-dialog')
        with user.scope(marker='confirm-dialog'):
            user.find(kind=ui.button, content='Delete').click()
        await user.should_not_see(marker=f'message-{mine.id}')
        memories = conversations.memories.get(conversation.id)
        assert memories.book.entries == {} and memories.read_until == '' and memories.pending is None
        assert marker_state(user) == ('', '', False, False)  # the line went with them

        # Editing a message changes nothing.
        before = conversations.memories.get(conversation.id).model_dump()
        greeting = conversations.load(conversation.id).messages[0]
        c = conversations.load(conversation.id)
        c.edit(greeting.id, 'Edited.')
        conversations.save(c)
        assert conversations.memories.get(conversation.id).model_dump() == before


async def test_memories_page(tmp_path, fake_model):
    conversations, conversation = make_chat(tmp_path, context=50_000)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}/memories')
        await user.should_see(marker='memories-empty')
        user.find(marker='memory-add').click()
        await user.should_see(marker='memory-0')
        with user.scope(marker='memory-0'):
            the(user, 'memory-title').set_value('The silver amulet')
            the(user, 'memory-keys').set_value(['amulet'])
            the(user, 'memory-text').set_value('Seraphina gave Mira a silver amulet.')
        conversations.memories.flush()
        entry = conversations.memories.get(conversation.id).book.entries['0']
        assert (entry.comment, entry.key, entry.content) == ('The silver amulet', ['amulet'],
                                                             'Seraphina gave Mira a silver amulet.')
        with user.scope(marker='memory-0'):
            user.find(marker='memory-delete').click()
        await user.should_see(marker='confirm-dialog')
        with user.scope(marker='confirm-dialog'):
            user.find(kind=ui.button, content='Delete').click()
        await user.should_see(marker='memories-empty')


async def test_removed_and_changed_memories_can_be_kept(tmp_path, fake_model):
    conversations, conversation = make_chat(tmp_path, context=50_000)
    memories = conversations.memories.get(conversation.id)
    memories.propose('a', 'b', proposal(memories, 'Brann', 'Oona'))
    memories.apply_pending()
    book = memories.book.model_copy(deep=True)
    book.entries['0'].content = 'Brann moved away.'
    book.remove_entry(1)
    memories.propose('c', 'd', book)
    conversations.memories.save(memories)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}/memories')
        await user.should_see('New memories: 1 changed, 1 removed')
        with user.scope(marker='memory-0'):
            await user.should_see('Changed')
            await user.should_see('About Brann.')  # what it was before
            user.find(marker='memory-undo').click()
        await user.should_not_see(marker='memory-undo')
        user.find(marker='memory-keep').click()
        await user.should_not_see(marker='memory-keep')
        conversations.memories.flush()
        assert conversations.memories.get(conversation.id).changes().summary == ''


async def test_deleting_the_conversation_deletes_its_memories(tmp_path, fake_model):
    conversations, conversation = make_chat(tmp_path)
    memories = conversations.memories.get(conversation.id)
    memories.propose('a', 'b', proposal(memories, 'Brann'))
    conversations.memories.save(memories)
    path = conversations.memories.folder / f'{conversation.id}.json'
    assert path.exists()
    conversations.delete(conversation.id)
    assert not path.exists()
