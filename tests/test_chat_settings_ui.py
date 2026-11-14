"""A character's own connection and prompt (its page's "Model and prompt"), and a chat's (⋮ › Settings), with
NiceGUI's simulated user (rpapp/ui/chat_settings.py)."""
from types import SimpleNamespace

import pytest
from conftest import FakeConnection
from nicegui.testing import user_simulation

from rpapp.characters import CharacterStore, new_character
from rpapp.connections import connection_store
from rpapp.conversations import ConversationStore
from rpapp.personas import PersonaStore, new_persona
from rpapp.presets import PromptPreset, preset_store
from rpapp.settings import ConnectionRef, PresetRef, PromptChoice, SettingsStore
from rpapp.ui.app import build_root


def ref(connection) -> ConnectionRef:
    return ConnectionRef(id=connection.id, name=connection.name)


@pytest.fixture
async def app(tmp_path, fake_model):
    connections = connection_store(tmp_path)
    fake, other = FakeConnection(name='Fake', model='fake/model'), FakeConnection(name='Other', model='other/model')
    for c in (fake, other):
        connections.save(c)
    presets = preset_store(tmp_path)
    noir = PromptPreset(name='Noir', instructions='Short sentences.')
    presets.save(noir)
    settings = SettingsStore(tmp_path)
    s = settings.load()
    s.chat.connection = ref(fake)
    settings.save(s)
    characters, personas = CharacterStore(tmp_path), PersonaStore(tmp_path)
    seraphina, mira = new_character('Seraphina'), new_persona('Mira')
    seraphina.first_messages = ['Hello, {{user}}.']
    characters.save(seraphina)
    personas.save(mira)
    conversations = ConversationStore(tmp_path)
    first, second = conversations.start(seraphina, mira), conversations.start(seraphina, mira)
    async with user_simulation(build_root(tmp_path)) as user:
        yield SimpleNamespace(user=user, fake=fake, other=other, noir=noir, seraphina=seraphina, first=first,
                              second=second, characters=characters, conversations=conversations)


def select(user, marker):
    [element] = user.find(marker=marker).elements
    return element


async def test_on_the_characters_page(app):
    user, c = app.user, app.characters
    await user.open(f'/characters/{app.seraphina.id}')
    await user.should_see('Model and prompt')
    connection = select(user, 'character-connection')
    assert connection.value == '' and connection.options[''] == 'Same as in Settings: Fake'
    assert list(connection.options.values())[1:] == ['Fake', 'Other']
    await user.should_see('Fake model, model fake/model.')  # (the one used: Settings')
    connection.set_value(app.other.id)
    await user.should_see('Fake model, model other/model.')
    assert c.load(app.seraphina.id).connection == ref(app.other)

    prompt = select(user, 'character-prompt')
    assert prompt.value == '' and prompt.options[''] == 'Same as in Settings: no instructions'
    prompt.set_value(app.noir.id)
    await user.should_see('Edit this prompt')
    assert c.load(app.seraphina.id).prompt == PromptChoice(preset=PresetRef(id=app.noir.id, name='Noir'))
    prompt.set_value('-none-')
    assert c.load(app.seraphina.id).prompt == PromptChoice(preset=None)  # (none at all, for this character)

    connection.set_value('')  # (back to the default)
    prompt.set_value('')
    saved = c.load(app.seraphina.id)
    assert saved.connection is None and saved.prompt is None


async def test_a_missing_connection_on_the_characters_page(app):
    app.seraphina.connection = ConnectionRef(id='gone-123456', name='Old Claude')
    app.characters.save(app.seraphina)
    await app.user.open(f'/characters/{app.seraphina.id}')
    assert select(app.user, 'character-connection').options['gone-123456'] == 'Old Claude (missing)'
    await app.user.should_see(marker='chat-connection-missing')


async def test_in_a_chat(app, fake_model):
    user, conversations = app.user, app.conversations
    app.seraphina.connection = ref(app.other)  # (Seraphina's chats use Other)
    app.characters.save(app.seraphina)
    await user.open(f'/conversations/{app.first.id}')
    user.find(marker='menu-settings').click()
    await user.should_see(marker='chat-settings-dialog')
    await user.should_see('For this chat only: other chats, with Seraphina too, keep theirs.')
    connection = select(user, 'chat-connection')
    assert connection.value == '' and connection.options[''] == 'Same as for Seraphina: Other'
    await user.should_see('Chosen for Seraphina: their chats use it, unless one has its own.')
    prompt = select(user, 'chat-prompt')
    assert prompt.options[''] == 'Same as in Settings: no instructions'
    await user.should_see('From Settings › Chat: what chats use, unless their character or the chat has its own.')

    connection.set_value(app.fake.id)  # (this chat only)
    await user.should_see('Chosen for this chat only.')
    prompt.set_value(app.noir.id)
    saved = conversations.load(app.first.id)
    assert saved.connection == ref(app.fake) and saved.prompt.preset.id == app.noir.id
    other = conversations.load(app.second.id)
    assert other.connection is None and other.prompt is None  # (another chat with Seraphina: as it was)

    user.find(marker='chat-settings-done').click()
    await user.should_not_see(marker='chat-settings-dialog')
    user.find(marker='chat-input').type('Where am I?')
    user.find(marker='send').click()  # (the page saves the chat: what was chosen stays)
    await user.should_see('Rest now.')
    saved = conversations.load(app.first.id)
    assert saved.connection == ref(app.fake) and saved.prompt.preset.id == app.noir.id

    user.find(marker='menu-settings').click()
    await user.should_see(marker='chat-settings-dialog')
    connection = select(user, 'chat-connection')
    assert connection.value == app.fake.id
    connection.set_value('')  # (back to the default: Seraphina's)
    await user.should_see('Chosen for Seraphina: their chats use it, unless one has its own.')
    assert conversations.load(app.first.id).connection is None


async def test_a_chats_choice_made_elsewhere_stays(app):
    """Chosen from another device meanwhile: this page, saving the chat, keeps it."""
    user, conversations = app.user, app.conversations
    await user.open(f'/conversations/{app.first.id}')
    await user.should_see('Hello, Mira.')
    conversations.choose(app.first.id, connection=ref(app.other), prompt=None)
    user.find(marker='chat-input').type('Hi')
    user.find(marker='send').click()
    await user.should_see('Rest now.')
    assert conversations.load(app.first.id).connection == ref(app.other)
