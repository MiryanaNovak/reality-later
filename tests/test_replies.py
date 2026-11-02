"""The reply service on its own: what happens around the model call."""
import asyncio

from conftest import FakeConnection

from rpapp.characters import CharacterStore, new_character
from rpapp.connections import Reply, connection_store
from rpapp.conversations import ConversationStore
from rpapp.lorebooks import lore_book_store
from rpapp.personas import PersonaStore, new_persona
from rpapp.replies import Replies, empty_reply_reason
from rpapp.settings import ConnectionRef, SettingsStore


def make(tmp_path, connection=True):
    characters, personas, conversations = CharacterStore(tmp_path), PersonaStore(tmp_path), ConversationStore(tmp_path)
    character, persona = new_character('Seraphina'), new_persona('Mira')
    character.first_messages = ['Hello, {{user}}.']
    characters.save(character)
    personas.save(persona)
    connections = connection_store(tmp_path)
    settings = SettingsStore(tmp_path)
    if connection:
        conn = FakeConnection(name='Fake', model='fake/model')
        connections.save(conn)
        s = settings.load()
        s.chat.connection = ConnectionRef(id=conn.id, name='Fake')
        settings.save(s)
    replies = Replies(conversations, characters=characters, personas=personas, lore_books=lore_book_store(tmp_path),
                      settings=settings, connections=connections)
    conversation = conversations.start(character, persona)
    conversation.add('user', 'Where am I?')
    conversations.save(conversation)
    return replies, conversations, conversation


async def finished(job):
    done = asyncio.Event()
    job.on_done(lambda _: done.set())
    await asyncio.wait_for(done.wait(), 2)
    return job


async def test_reply_is_added_with_its_details(tmp_path, fake_model):
    replies, conversations, conversation = make(tmp_path)
    job = await finished(replies.start(conversation.id))
    assert not job.error and job.message.content == '*Seraphina smiles.* Rest now.'
    saved = conversations.load(conversation.id)
    assert saved.messages[-1].id == job.message.id
    assert replies.details(conversation.id, job.message).connection == 'Fake (fake/model)'
    assert replies.running(conversation.id) is None and replies.last(conversation.id) is job


async def test_one_reply_at_a_time(tmp_path, fake_model):
    replies, _, conversation = make(tmp_path)
    fake_model.gate = asyncio.Event()
    first = replies.start(conversation.id)
    assert replies.start(conversation.id) is first
    fake_model.gate.set()
    await finished(first)
    assert len(fake_model.calls) == 1


async def test_reply_dropped_if_the_chat_changed_meanwhile(tmp_path, fake_model):
    replies, conversations, conversation = make(tmp_path)
    fake_model.gate = asyncio.Event()
    job = replies.start(conversation.id)
    await asyncio.sleep(0.05)
    c = conversations.load(conversation.id)  # e.g. from another tab: the message is deleted
    c.delete_from(c.messages[-1].id)
    conversations.save(c)
    fake_model.gate.set()
    await finished(job)
    assert job.error == "The chat changed while the reply was written: there's nothing to reply to."
    assert len(conversations.load(conversation.id).messages) == 1


async def test_stop(tmp_path, fake_model):
    replies, conversations, conversation = make(tmp_path)
    fake_model.gate = asyncio.Event()
    job = replies.start(conversation.id)
    await asyncio.sleep(0.05)
    replies.stop(conversation.id)
    await finished(job)
    assert job.stopped and not job.error and len(conversations.load(conversation.id).messages) == 2


async def test_missing_connection(tmp_path, fake_model):
    replies, _, conversation = make(tmp_path, connection=False)
    s = replies.settings.load()
    s.chat.connection = ConnectionRef(id='gone-123456', name='Old DeepSeek')
    replies.settings.save(s)
    job = await finished(replies.start(conversation.id))
    assert job.error == 'The chat connection "Old DeepSeek" is missing: choose another one in Settings.'


async def test_empty_replies(tmp_path, fake_model):
    replies, _, conversation = make(tmp_path)
    fake_model.replies = [Reply(text='  ', reasoning='Let me think...', finish_reason='length')]
    job = await finished(replies.start(conversation.id))
    assert job.error.startswith('The model spent the whole max reply length thinking')
    assert empty_reply_reason(Reply(finish_reason='content_filter')).startswith("The provider's content filter")
    assert empty_reply_reason(Reply()) == 'The model sent back an empty reply. Try again, or try another model.'



async def test_another_version(tmp_path, fake_model):
    replies, conversations, conversation = make(tmp_path)
    first = await finished(replies.start(conversation.id))
    fake_model.replies = [Reply(text='Another.')]
    job = await finished(replies.start(conversation.id, version_of=first.message.id))
    assert not job.error and job.reply_to == first.reply_to
    assert fake_model.calls[1] == fake_model.calls[0]  # same prompt: the first version isn't in it
    saved = conversations.load(conversation.id)
    reply = saved.messages[-1]
    assert len(saved.messages) == 3 and reply.alternatives == ['*Seraphina smiles.* Rest now.', 'Another.']
    assert replies.details(conversation.id, reply).created_at == job.details.created_at  # the version shown
    reply.alternative = 0
    assert replies.details(conversation.id, reply).created_at == first.details.created_at


async def test_another_version_dropped_if_you_replied_meanwhile(tmp_path, fake_model):
    replies, conversations, conversation = make(tmp_path)
    first = await finished(replies.start(conversation.id))
    fake_model.gate = asyncio.Event()
    job = replies.start(conversation.id, version_of=first.message.id)
    await asyncio.sleep(0.05)
    c = conversations.load(conversation.id)
    c.add('user', 'Thanks.')
    conversations.save(c)
    fake_model.gate.set()
    await finished(job)
    assert job.error.startswith('The chat changed while the reply was written')
    assert conversations.load(conversation.id).messages[-2].alternatives == []
