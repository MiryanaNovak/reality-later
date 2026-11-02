"""Extending the AI's last reply: the two ways of asking, how the text is joined, and the versions it makes."""
import json

import httpx
import pytest
from nicegui.testing import user_simulation

from rpapp.connections import Message, OpenAICompatibleConnection, OpenRouterConnection, Reply, connection_store
from rpapp.conversations import Conversation, ConversationStore, ProfileRef
from rpapp.presets import EXTEND_INSTRUCTION, new_preset, preset_store
from rpapp.prompts import build_prompt
from rpapp.replies import extended
from rpapp.settings import Settings, SettingsStore
from rpapp.ui.app import build_root
from test_chat import mira, save_cast, seraphina, settled, the
from test_connections import chunk, fake_server, sse

FIRST = '*Seraphina smiles.* Rest now.'  # the fake model's reply


# --- joining ------------------------------------------------------------------------------------

@pytest.mark.parametrize('base, addition, joined', [
    ('"Safe by day, but at', ' night they hunt."', '"Safe by day, but at night they hunt."'),
    ('She waits.\n\n', '\n\nThen she stands.', 'She waits.\n\nThen she stands.'),  # its spaces, as they come
    ('He nods.', 'She smiles.', 'He nods. She smiles.'),  # a space it clearly forgot
    ('The forest is da', 'rk tonight.', 'The forest is dark tonight.'),  # mid-word
])
def test_prefill_goes_on_as_is(base, addition, joined):
    assert extended(base, addition, prefill=True) == joined


@pytest.mark.parametrize('base, addition, joined', [
    ('"Safe by day, but at', 'night they hunt."', '"Safe by day, but at night they hunt."'),  # mid-sentence
    ('*She turns away.*', '"Come," she says.', '*She turns away.*\n\n"Come," she says.'),  # after a sentence
    ('"Hello,"', 'she whispers.', '"Hello," she whispers.'),  # a lowercase word goes on
    ('He was almost there', ', but not quite.', 'He was almost there, but not quite.'),  # punctuation
    ('She sits by the fire, listening to the rain.', 'listening to the rain. Then she stands.',
     'She sits by the fire, listening to the rain.\n\nThen she stands.'),  # the repeated end goes
    ('She sits by the fire.', 'She sits by the fire. Then she stands.',
     'She sits by the fire.\n\nThen she stands.'),  # so does the whole reply, repeated
    ('"Rest now," she says, and', 'and then she leaves.', '"Rest now," she says, and then she leaves.'),
    ('It was', 'was', 'It was'),  # nothing new
])
def test_instruction_answers_are_joined(base, addition, joined):
    assert extended(base, addition, prefill=False) == joined


# --- the prompt ---------------------------------------------------------------------------------

def chat_to_extend() -> tuple[Conversation, dict]:
    character, persona = seraphina('Hello, Mira.'), mira()
    c = Conversation(title='t', character=ProfileRef.to(character), persona=ProfileRef.to(persona))
    c.add('assistant', 'Hello, Mira.')
    c.add('user', 'Where am I?')
    c.add('assistant', 'In my glade. You were  \n')
    preset = new_preset('Careful')
    preset.final_instructions = 'Write only what {{char}} says.'
    return c, dict(character=character, persona=persona, settings=Settings(), preset=preset)


def test_extend_prompt_with_an_instruction():
    c, kw = chat_to_extend()
    prompt = build_prompt(c, **kw, extend='instruction')
    assert [(p.kind, p.role) for p in prompt.parts][-4:] == [
        ('message', 'user'), ('message', 'assistant'), ('final', 'system'), ('extend', 'user')]
    assert prompt.parts[-1].content == EXTEND_INSTRUCTION and prompt.report.extend == 'instruction'
    assert prompt.parts[-1].source == 'Hidden instruction: extend the reply (Careful)'
    assert prompt.messages()[-1] == Message(role='user', content=EXTEND_INSTRUCTION)

    kw['preset'].extend_instruction = 'Go on, {{char}}.'  # the prompt's own
    assert build_prompt(c, **kw, extend='instruction').parts[-1].content == 'Go on, Seraphina.'


def test_extend_prompt_by_prefill():
    c, kw = chat_to_extend()
    prompt = build_prompt(c, **kw, extend='prefill')
    assert [(p.kind, p.role) for p in prompt.parts][-3:] == [
        ('message', 'user'), ('final', 'system'), ('message', 'assistant')]
    last = prompt.parts[-1]
    assert last.content == 'In my glade. You were' and last.source == 'Seraphina, to go on with (prefill)'
    assert prompt.messages()[-1] == Message(role='assistant', content='In my glade. You were')
    assert not any(p.kind == 'extend' for p in prompt.parts)
    assert build_prompt(c, **kw).report.extend == ''  # (a reply, as before)


async def test_local_servers_are_told_to_go_on():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, content=sse(chunk(' more'), 'data: [DONE]'))

    messages = [Message(role='user', content='Hi'), Message(role='assistant', content='Hello')]
    async with fake_server(handler) as client:
        await OpenAICompatibleConnection().generate(messages, client=client, continue_last=True)
        await OpenAICompatibleConnection().generate(messages, client=client)
        await OpenRouterConnection(api_key='k').generate(messages, client=client, continue_last=True)
    assert bodies[0]['continue_final_message'] is True and bodies[0]['add_generation_prompt'] is False
    assert 'continue_final_message' not in bodies[1]
    assert 'continue_final_message' not in bodies[2]  # OpenRouter: the model's message last is enough


# --- in the chat --------------------------------------------------------------------------------

@pytest.fixture
async def chat(tmp_path, fake_model):
    character, persona = save_cast(tmp_path, '*{{char}} kneels beside {{user}}.*')
    store = ConversationStore(tmp_path)
    conversation = store.start(character, persona)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        user.find(marker='chat-input').type('Where am I?')
        user.find(marker='send').click()
        await settled(user)
        yield user, store, store.load(conversation.id), tmp_path


async def extend(user) -> None:
    user.find(marker='chat-input').type('')
    user.find(marker='send').click()
    user.find(marker='extend-reply').click()
    await settled(user)


def reply_of(store, conversation):
    return store.load(conversation.id).messages[-1]


async def test_each_extension_is_a_new_version(chat, fake_model):
    user, store, conversation, _ = chat
    question = conversation.messages[1]
    fake_model.replies = [Reply(text='She pulls the blanket up.'), Reply(text='she hums, softly.')]

    await extend(user)
    reply = reply_of(store, conversation)
    assert reply.content == f'{FIRST}\n\nShe pulls the blanket up.'
    assert reply.alternatives == [FIRST, reply.content] and reply.alternative == 1
    await user.should_see('2 / 2')
    await user.should_see('She pulls the blanket up.')
    await user.should_not_see(EXTEND_INSTRUCTION)  # hidden
    assert fake_model.calls[-1][-1] == Message(role='user', content=EXTEND_INSTRUCTION)
    assert fake_model.calls[-1][-2] == Message(role='assistant', content=FIRST)
    assert fake_model.continued[-1] is False

    user.find(marker='reply-previous').click()  # back to the reply as it was
    await user.should_see('1 / 2')
    assert reply_of(store, conversation).content == FIRST
    await extend(user)  # extends version 1 again
    reply = reply_of(store, conversation)
    assert reply.alternatives == [FIRST, f'{FIRST}\n\nShe pulls the blanket up.', f'{FIRST} she hums, softly.']
    assert reply.alternative == 2
    await user.should_see('3 / 3')
    for count in ('2 / 3', '1 / 3'):
        user.find(marker='reply-previous').click()
        await user.should_see(count)

    # Each version has its details; an extension's prompt is kept with them, not over the reply's
    record = store.replies.get(conversation.id, reply.id)
    assert [v.extends for v in record.versions] == [None, 0, 0]
    assert record.versions[2].prompt.parts[-1].kind == 'extend'
    assert store.prompts.get(conversation.id, question.id).report.extend == ''
    assert len(store.load(conversation.id).messages) == 3  # no memories started, no new message


async def test_extension_details_and_prompt(chat, fake_model):
    user, store, conversation, _ = chat
    fake_model.replies = [Reply(text='She pulls the blanket up.', model='fake/model')]
    await extend(user)
    reply = reply_of(store, conversation)
    with user.scope(marker=f'message-{reply.id}'):
        user.find(marker='menu-details').click()
    with user.scope(marker='reply-dialog'):
        await user.should_see('Version 2 of 2')
        await user.should_see('Version 1, extended with a hidden instruction.')
        user.find(marker='reply-prompt').click()
    with user.scope(marker='prompt-dialog'):
        await user.should_see('Prompt for this extension')
        await user.should_see(marker='prompt-extend')
        await user.should_see('Hidden instruction: extend the reply (Test) · user')


async def test_extend_by_prefill(chat, fake_model):
    user, store, conversation, tmp_path = chat
    connections = connection_store(tmp_path)
    [conn] = connections.list()
    conn.extend_method = 'prefill'
    connections.save(conn)
    fake_model.replies = [Reply(text=' She pulls the blanket up.')]
    await extend(user)
    assert fake_model.continued[-1] is True
    assert fake_model.calls[-1][-1] == Message(role='assistant', content=FIRST)
    assert reply_of(store, conversation).content == f'{FIRST} She pulls the blanket up.'
    record = store.replies.get(conversation.id, reply_of(store, conversation).id)
    assert record.versions[1].extend_method == 'prefill'


async def test_a_failed_extension_changes_nothing(chat, fake_model):
    user, store, conversation, _ = chat
    fake_model.error = 'The model is overloaded'
    await extend(user)
    await user.should_see('Not extended: The model is overloaded')
    reply = reply_of(store, conversation)
    assert reply.content == FIRST and reply.versions == 1
    fake_model.error = ''
    fake_model.replies = [Reply(text=FIRST)]  # repeats itself
    user.find(marker='retry').click()
    await settled(user)
    await user.should_see('Not extended: The model only repeated the reply, and added nothing to it.')
    fake_model.replies = [Reply(text='Then she sleeps.')]
    user.find(marker='retry').click()
    await settled(user)
    assert reply_of(store, conversation).content == f'{FIRST}\n\nThen she sleeps.'
    await user.should_not_see(marker='reply-status')


async def test_extending_shows_under_the_reply(chat, fake_model):
    import asyncio
    user, store, conversation, _ = chat
    fake_model.gate = asyncio.Event()
    user.find(marker='chat-input').type('')
    user.find(marker='send').click()
    user.find(marker='extend-reply').click()
    await user.should_see(marker='extending')
    await user.should_see('Rest now.')  # the reply stays, "Extending…" under it
    assert not the(user, 'chat-input').enabled and the(user, 'send').props['icon'] == 'stop'
    fake_model.gate.set()
    await settled(user)
    await user.should_not_see(marker='extending')


# --- where it's set -----------------------------------------------------------------------------

async def test_the_method_and_the_instruction_are_editable(tmp_path):
    connections = connection_store(tmp_path)
    conn = OpenAICompatibleConnection(name='Laptop')
    connections.save(conn)
    presets = preset_store(tmp_path)
    preset = new_preset('Mine')
    presets.save(preset)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/connections/{conn.id}')
        assert the(user, 'extend-method').value == 'instruction'
        the(user, 'extend-method').set_value('prefill')
        connections.flush()
        assert connections.load(conn.id).extend_method == 'prefill'

        await user.open(f'/prompts/{preset.id}')
        field = the(user, 'extend-instruction')
        assert field.props['placeholder'] == EXTEND_INSTRUCTION  # empty: the default, shown greyed
        field.set_value('Go on.')
        presets.flush()
        assert presets.load(preset.id).extend_instruction == 'Go on.'
    assert SettingsStore(tmp_path).load().chat.connection is None  # (nothing else changed)
