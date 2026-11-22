"""A prompt's modules (presets.py): instructions added to the prompt's in the chats where they're on (on by default,
or not; each chat turns them on or off in its settings), in replies' prompts and memory calls."""
import json
from types import SimpleNamespace

import pytest
from conftest import FakeConnection
from nicegui import ui
from nicegui.testing import user_simulation
from test_chat import send
from test_memory import ADD_BRANN, LONG, make_chat

from rpapp.characters import CharacterStore, new_character
from rpapp.connections import connection_store
from rpapp.conversations import Conversation, ConversationStore, ProfileRef
from rpapp.lorebooks import new_lore_book
from rpapp.memory import memory_prompt
from rpapp.personas import PersonaStore, new_persona
from rpapp.presets import PromptModule, PromptPreset, preset_store
from rpapp.prompts import build_prompt
from rpapp.settings import ConnectionRef, PresetRef, PromptChoice, Settings, SettingsStore
from rpapp.ui.app import build_root


def the(user, marker):
    """The element with this marker, shown or not."""
    [element] = [e for e in user.client.layout.descendants() if marker in e._markers]
    return element


def found(user, marker) -> bool:
    return any(marker in e._markers for e in user.client.layout.descendants())


def noir() -> PromptPreset:
    """A prompt with three modules: Combat (on by default), Romance (off), and Blank (nothing in it)."""
    preset = PromptPreset(name='Noir', instructions='Short sentences, {{char}}.', final_instructions='One paragraph.')
    preset.modules = [
        PromptModule(id='combat01', name='Combat', instructions='Fights are fast, {{user}}.',
                     final_instructions='End on a blow.', memory_instructions='Note who is hurt.'),
        PromptModule(id='romance1', name='Romance', on_by_default=False, instructions='Slow burn.',
                     final_instructions='  ', memory_instructions='Note {{poss}} feelings.'),
        PromptModule(id='blank001', name='Blank', instructions=' \n ', final_instructions='', memory_instructions=''),
    ]
    return preset


# --- the model --------------------------------------------------------------------------------

def test_on_by_default_or_as_the_chat_chose():
    preset = noir()
    assert [m.name for m in preset.modules_on({})] == ['Combat', 'Blank']
    assert [m.name for m in preset.modules_on({'combat01': False, 'romance1': True})] == ['Romance', 'Blank']
    assert [m.name for m in preset.modules_on({'gone-one': True})] == ['Combat', 'Blank']  # (another prompt's)
    old = PromptPreset.model_validate({'id': 'p-1', 'name': 'P', 'modules': [{'id': 'm1', 'name': 'M',
                                                                               'instructions': 'x'}]})
    assert old.modules[0].on_by_default is True and old.modules[0].memory_instructions == ''  # (saved before)
    assert Conversation.model_validate({'id': 'c', 'character': {'id': 'a', 'title': 'A'},
                                        'persona': {'id': 'b', 'title': 'B'}}).modules == {}


def prompt_of(chosen: dict[str, bool]):
    character, persona = new_character('Seraphina'), new_persona('Mira')
    persona.pronouns = 'she/her'
    conversation = Conversation(title='t', character=ProfileRef.to(character), persona=ProfileRef.to(persona),
                                modules=chosen)
    conversation.add('user', 'Hi.')
    return build_prompt(conversation, character=character, persona=persona, settings=Settings(), preset=noir())


def test_in_the_prompt():
    prompt = prompt_of({})
    parts = [(p.kind, p.source, p.content) for p in prompt.parts if p.kind in ('instructions', 'final')]
    assert parts == [
        ('instructions', 'Instructions (Noir)', 'Short sentences, Seraphina.'),
        ('instructions', 'Instructions of the module "Combat"', 'Fights are fast, Mira.'),  # (right after: appended)
        ('final', 'Final instructions (Noir)', 'One paragraph.'),
        ('final', 'Final instructions of the module "Combat"', 'End on a blow.'),
    ]  # (Blank, all spaces: left out)
    assert prompt.report.modules == ['Combat', 'Blank']
    sent = prompt.messages()
    assert sent[0].content.startswith('Short sentences, Seraphina.\n\nFights are fast, Mira.\n\n')  # (one message)
    assert sent[-1].content == 'One paragraph.\n\nEnd on a blow.'

    turned = prompt_of({'combat01': False, 'romance1': True})
    kept = [p.content for p in turned.parts if p.kind in ('instructions', 'final')]
    assert kept == ['Short sentences, Seraphina.', 'Slow burn.', 'One paragraph.']  # (its final: only spaces)


def test_in_memory_calls():
    call = memory_prompt(instructions='', char='Seraphina', user='Mira', character='', persona='',
                         book=new_lore_book('M'), lines=['Mira: Hi.'], pronouns=('she', 'her', 'her', 'hers', 'herself'),
                         modules=['Note who is hurt.', '  ', 'Note {{poss}} feelings.'])
    system = call[0].content
    assert 'unusual words. Avoid common words: they would bring the entry back all the time.\n\nNote who is hurt.' \
           '\n\nNote her feelings.\n\nAnswer with a JSON array' in system  # (after the instructions, before the format)


async def test_memory_calls_of_a_chat(tmp_path, fake_model):
    conversations, conversation = make_chat(tmp_path, context=250)
    presets = preset_store(tmp_path)
    preset = noir()
    presets.save(preset)
    settings = SettingsStore(tmp_path)
    s = settings.load()
    s.chat.preset = PresetRef(id=preset.id, name=preset.name)
    settings.save(s)
    conversations.choose(conversation.id, connection=None, prompt=None, modules={'romance1': True})
    fake_model.memory_answers = [ADD_BRANN]
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await send(user, 'Hello.')
        await send(user, LONG + 'Brann helped me cross.')
        await user.should_see(marker='memory-review')  # (written)
    [call] = fake_model.memory_calls
    assert 'Note who is hurt.\n\nNote their feelings.' in call[0].content  # (Combat by default, Romance chosen)
    assert 'Fights are fast' in fake_model.calls[-1][0].content and 'Slow burn.' in fake_model.calls[-1][0].content


# --- choosing them ----------------------------------------------------------------------------

@pytest.fixture
async def app(tmp_path, fake_model):
    connections = connection_store(tmp_path)
    fake = FakeConnection(name='Fake', model='fake/model')
    connections.save(fake)
    presets = preset_store(tmp_path)
    with_modules, plain = noir(), PromptPreset(name='Plain', instructions='Be kind.')
    presets.save(with_modules)
    presets.save(plain)
    settings = SettingsStore(tmp_path)
    s = settings.load()
    s.chat.connection = ConnectionRef(id=fake.id, name=fake.name)
    s.chat.preset = PresetRef(id=with_modules.id, name=with_modules.name)
    settings.save(s)
    characters, personas = CharacterStore(tmp_path), PersonaStore(tmp_path)
    seraphina, mira = new_character('Seraphina'), new_persona('Mira')
    seraphina.first_messages = ['Hello, {{user}}.']
    characters.save(seraphina)
    personas.save(mira)
    conversations = ConversationStore(tmp_path)
    first = conversations.start(seraphina, mira)
    async with user_simulation(build_root(tmp_path)) as user:
        yield SimpleNamespace(user=user, presets=presets, noir=with_modules, plain=plain, seraphina=seraphina,
                              mira=mira, first=first, conversations=conversations)


async def test_in_the_chats_settings(app):
    user, conversations = app.user, app.conversations
    await user.open(f'/conversations/{app.first.id}')
    user.find(marker='menu-settings').click()
    await user.should_see(marker='chat-modules')
    await user.should_see('Of the prompt "Noir": those on add their instructions. For this chat only.')
    assert [the(user, f'chat-module-{i}').value for i in ('combat01', 'romance1', 'blank001')] == [True, False, True]
    the(user, 'chat-module-romance1').set_value(True)
    the(user, 'chat-module-combat01').set_value(False)
    assert conversations.load(app.first.id).modules == {'romance1': True, 'combat01': False}

    the(user, 'chat-prompt').set_value(app.plain.id)  # (a prompt without modules: none to switch)
    await user.should_not_see(marker='chat-modules')
    the(user, 'chat-prompt').set_value('')  # (back to Settings': Noir, as they were)
    await user.should_see(marker='chat-modules')
    assert the(user, 'chat-module-combat01').value is False

    user.find(marker='chat-settings-done').click()
    user.find(marker='chat-input').type('Hi')
    user.find(marker='send').click()  # (the page saves the chat: they stay)
    await user.should_see('Rest now.')
    assert conversations.load(app.first.id).modules == {'romance1': True, 'combat01': False}


async def test_a_fork_keeps_its_own(app):
    conversations = app.conversations
    chat = conversations.load(app.first.id)
    chat.add('user', 'Hi.')
    reply = chat.add('assistant', 'Hello.')
    conversations.save(chat)
    conversations.choose(chat.id, connection=None, prompt=None, modules={'romance1': True})
    fork = conversations.fork(chat.id, reply.id)
    assert conversations.load(fork.id).modules == {'romance1': True}  # (copied)
    conversations.choose(fork.id, connection=None, prompt=None, modules={'romance1': False})
    assert conversations.load(chat.id).modules == {'romance1': True}  # (the fork's change: not the original's)
    conversations.choose(chat.id, connection=None, prompt=None, modules={'combat01': False})
    assert conversations.load(fork.id).modules == {'romance1': False}  # (and the other way round)


async def test_before_a_new_chat(app):
    user, conversations = app.user, app.conversations
    await user.open('/characters')
    user.find(marker='new-chat').click()
    await user.should_see(marker='persona-picker')
    user.find(marker=f'link-{app.mira.id}').click()
    await user.should_see(marker='chat-settings-dialog')
    await user.should_see('New chat with Seraphina')
    assert len(conversations.list()) == 1  # (not started yet)
    the(user, 'chat-module-romance1').set_value(True)
    the(user, 'chat-connection')  # (the connection and prompt too)
    user.find(marker='chat-settings-start').click()
    await user.should_see(marker='chat-input')
    [new] = [c for c in conversations.list() if c.id != app.first.id]
    assert new.modules == {'romance1': True} and new.messages[0].content == 'Hello, Mira.'


async def test_in_the_prompts_editor(app):
    user, presets = app.user, app.presets
    await user.open(f'/prompts/{app.noir.id}')
    the(user, 'part-modules').set_value(True)
    with user.scope(marker='part-modules'):
        await user.should_see('Sent to the AI')
    with user.scope(marker='module-romance1'):
        assert the(user, 'module-romance1').visible
        [switch] = [e for e in the(user, 'module-romance1').descendants() if 'module-on-by-default' in e._markers]
        [memory] = [e for e in the(user, 'module-romance1').descendants()
                    if 'module-memory-instructions' in e._markers]
    assert switch.value is False and memory.value == 'Note {{poss}} feelings.'
    switch.set_value(True)
    memory.set_value('Note the glances.')
    presets.flush()
    saved = presets.load(app.noir.id).modules[1]
    assert (saved.on_by_default, saved.memory_instructions) == (True, 'Note the glances.')
    assert json.loads(presets.path_for(app.noir.id).read_text(encoding='utf-8'))['modules'][1]['on_by_default'] is True
    assert not found(user, 'no-modules')
    assert isinstance(the(user, 'module-add'), ui.button)
