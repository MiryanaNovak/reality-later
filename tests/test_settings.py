"""Settings: the file, and the Settings page's Chat, Lore books and Memory sections."""
import json

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

from rpapp.connections import OpenAICompatibleConnection, OpenRouterConnection, connection_store
from rpapp.memory import ANSWER_FORMAT, DEFAULT_INSTRUCTIONS
from rpapp.presets import preset_store
from rpapp.settings import ConnectionRef, PresetRef, SettingsStore, chat_connection
from rpapp.ui.app import build_root


def test_defaults_file_and_lookup(tmp_path):
    store = SettingsStore(tmp_path)
    settings = store.load()
    assert settings.chat.connection is None and settings.chat.instructions == ''
    assert settings.lore.scan_depth == 2 and settings.lore.budget_percent == 25 and settings.lore.recursive
    assert not store.path.exists()  # nothing written until something changes

    connections = connection_store(tmp_path)
    conn = OpenRouterConnection(name='DeepSeek')
    connections.save(conn)
    settings.chat.connection = ConnectionRef(id=conn.id, name=conn.name)
    settings.chat.instructions = 'You are {{char}}.'
    store.save(settings)

    assert store.path == tmp_path / 'settings.json'
    assert json.loads(store.path.read_text(encoding='utf-8')) == {
        'id': 'settings', 'format_version': 1,
        'chat': {'connection': {'id': conn.id, 'name': 'DeepSeek'}, 'preset': None,
                 'example_dialogues': 'make_room', 'return_key_desktop': 'send',
                 'return_key_mobile': 'new_line'},  # (the instructions now live in prompts: presets.py)
        'lore': {'scan_depth': 2, 'case_sensitive': False, 'whole_words': True, 'recursive': True,
                 'budget_percent': 25},
        'memory': {'enabled': True, 'connection': None, 'instructions': '', 'budget_percent': 15,
                   'temperature': None, 'max_tokens': None},
        'vectors': {'memories': False, 'lore': False, 'model': 'all-minilm-l6-v2', 'source': 'huggingface',
                    'thresholds': {}, 'max_entries': 3},  # (vector search: off until it's turned on)
        'sharing': {'show_nsfw': False, 'hidden_tags': []},
        'appearance': {'accent': 'jade'},
    }
    assert chat_connection(store.load(), connections.get).id == conn.id
    connections.delete(conn.id)
    assert chat_connection(store.load(), connections.get) is None


def test_first_version_file(tmp_path):
    store = SettingsStore(tmp_path)
    store.path.write_text('{"id": "settings", "chat": {"prompt": "You are {{char}}."}}', encoding='utf-8')
    assert store.load().chat.instructions == 'You are {{char}}.'  # "prompt" was its first name


@pytest.fixture
async def setup(tmp_path):
    connections = connection_store(tmp_path)
    async with user_simulation(build_root(tmp_path)) as user:
        yield user, SettingsStore(tmp_path), connections


def by_label(user, kind):
    return {e.props.get('label'): e for e in user.find(kind).elements}


async def test_choose_connection_and_write_prompt(setup):
    user, store, connections = setup
    deepseek = OpenRouterConnection(name='DeepSeek', model='deepseek/deepseek-chat')
    local = OpenAICompatibleConnection(name='LM Studio')
    connections.save(deepseek)
    connections.save(local)

    await user.open('/')
    user.find(marker='nav-settings').click()
    await user.should_see('Chat')
    select = by_label(user, ui.select)['Chat connection']
    assert select.value == ''  # none chosen yet
    select.set_value(deepseek.id)
    await user.should_see('OpenRouter, model deepseek/deepseek-chat.')
    await user.should_see('Context size unknown')
    prompts = by_label(user, ui.select)['Prompt']
    [starter] = preset_store(store.path.parent).list()  # made on the first start
    assert prompts.value == starter.id and starter.name == 'Default'
    prompts.set_value('')  # none: no instructions

    saved = store.load()  # reading writes the pending (delayed) save first
    assert saved.chat.connection == ConnectionRef(id=deepseek.id, name='DeepSeek')
    assert saved.chat.preset is None
    prompts.set_value(starter.id)
    await user.should_see('Edit this prompt')
    assert store.load().chat.preset == PresetRef(id=starter.id, name='Default')

    select.set_value('')
    assert store.load().chat.connection is None


async def test_missing_connection_and_rename(setup):
    user, store, connections = setup
    conn = OpenRouterConnection(name='Old name')
    connections.save(conn)
    settings = store.load()
    settings.chat.connection = ConnectionRef(id=conn.id, name=conn.name)
    store.save(settings)

    conn.name = 'New name'
    connections.save(conn)
    await user.open('/settings')
    await user.should_see('New name')
    assert store.load().chat.connection.name == 'New name'  # the remembered name follows renames

    connections.delete(conn.id)
    await user.open('/settings')
    await user.should_see('Connection "New name" is missing')
    assert by_label(user, ui.select)['Chat connection'].options[conn.id] == 'New name (missing)'


async def test_no_connections_yet(setup):
    user, *_ = setup
    await user.open('/settings')
    await user.should_see('You have no connections yet')
    assert 'Chat connection' not in by_label(user, ui.select)


async def test_broken_file_is_left_alone(setup, tmp_path):
    user, store, _ = setup
    store.path.write_text('{"chat": {"prompt": ["not", "text"]}}', encoding='utf-8')
    await user.open('/settings')
    await user.should_see("The settings file can't be read")
    await user.should_not_see(kind=ui.textarea)
    assert store.path.read_text(encoding='utf-8') == '{"chat": {"prompt": ["not", "text"]}}'


async def test_return_key_settings(setup):
    user, store, _ = setup
    await user.open('/settings')
    await user.should_see('The Return key, when you write a message')
    desktop, mobile = (user.find(marker=m).elements.pop() for m in ('return-key-desktop', 'return-key-mobile'))
    assert (desktop.value, mobile.value) == ('send', 'new_line')  # the defaults
    desktop.set_value('new_line')
    mobile.set_value('send')
    store.flush()
    chat = store.load().chat
    assert (chat.return_key_desktop, chat.return_key_mobile) == ('new_line', 'send')


async def test_lore_settings(setup):
    user, store, _ = setup
    await user.open('/settings')
    await user.should_see('Lore books')
    numbers = by_label(user, ui.number)
    numbers['Messages to scan'].set_value(4)
    numbers['Lore budget (%)'].set_value(40)
    switches = {e.text: e for e in user.find(ui.switch).elements}
    switches['Entries can trigger other entries'].set_value(False)
    switches['Match whole words'].set_value(False)
    lore = store.load().lore
    assert (lore.scan_depth, lore.budget_percent, lore.recursive, lore.whole_words, lore.case_sensitive) == \
        (4, 40, False, False, False)


async def test_context_size_shown(setup):
    user, store, connections = setup
    conn = OpenRouterConnection(name='DeepSeek', model='deepseek/deepseek-chat', context_window=64000)
    connections.save(conn)
    settings = store.load()
    settings.chat.connection = ConnectionRef(id=conn.id, name=conn.name)
    store.save(settings)
    await user.open('/settings')
    await user.should_see('Context size: 64,000 tokens. Longer chats are trimmed to fit.')


async def test_memory_settings(setup):
    user, store, connections = setup
    cheap = OpenRouterConnection(name='Cheap', model='some/small-model')
    connections.save(cheap)
    await user.open('/settings')
    await user.should_see('Memory')
    selects = by_label(user, ui.select)
    assert selects['Memory connection'].options[''] == 'Same as the chat connection'
    selects['Memory connection'].set_value(cheap.id)
    numbers = by_label(user, ui.number)
    numbers['Temperature'].set_value(0.2)
    numbers['Max reply length (tokens)'].set_value(3000)
    numbers['Memory budget (%)'].set_value(10)
    {e.text: e for e in user.find(ui.switch).elements}['Keep a memory of each chat'].set_value(False)
    memory = store.load().memory
    assert memory.connection == ConnectionRef(id=cheap.id, name='Cheap')
    assert (memory.enabled, memory.temperature, memory.max_tokens, memory.budget_percent) == (False, 0.2, 3000, 10)

    # The fixed part is shown, read-only.
    await user.should_see(marker='memory-format')
    await user.should_see(ANSWER_FORMAT.splitlines()[0])
    await user.should_see(marker='memory-material')


async def test_memory_instructions_default_and_reset(setup):
    user, store, _ = setup
    await user.open('/settings')
    editor = user.find(marker='memory-instructions').elements.pop()
    assert editor.value == DEFAULT_INSTRUCTIONS  # shown until changed
    await user.should_see('The default instructions.')
    await user.should_not_see(marker='memory-reset')

    editor.set_value(DEFAULT_INSTRUCTIONS + '\nAlso track the time of day.')
    await user.should_see('Your own instructions.')
    assert store.load().memory.instructions.endswith('Also track the time of day.')

    user.find(marker='memory-reset').click()
    await user.should_see('The default instructions.')
    assert editor.value == DEFAULT_INSTRUCTIONS
    assert store.load().memory.instructions == ''  # saved as "the default", so improvements to it apply

    editor.set_value('')
    await user.should_see('Empty: the default instructions are used.')
    assert store.load().memory.instructions == ''


async def test_missing_memory_connection(setup):
    user, store, _ = setup
    settings = store.load()
    settings.memory.connection = ConnectionRef(id='gone-123456', name='Old cheap one')
    store.save(settings)
    await user.open('/settings')
    await user.should_see(marker='memory-connection-missing')
    assert by_label(user, ui.select)['Memory connection'].options['gone-123456'] == 'Old cheap one (missing)'


# --- Appearance -----------------------------------------------------------------------------------

async def test_appearance(tmp_path):
    from nicegui import ui
    from nicegui.testing import user_simulation

    from rpapp.ui.app import build_root
    from rpapp.ui.theme import ACCENTS, accent_css
    store = SettingsStore(tmp_path)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open('/settings')
        await user.should_see('Appearance')
        user.find(marker='accent-violet').click()
        await user.should_see(marker='accents')
        assert store.load().appearance.accent == 'violet'  # for every device
        assert accent_css('violet').startswith(f'body {{ --q-primary: {ACCENTS["violet"][1]} !important')
        assert accent_css('nonsense') == accent_css('jade')

        user.find(marker='look-mode').elements.pop().set_value('dark')  # for this device: a cookie
        user.find(marker='look-size').elements.pop().set_value('large')
        await user.should_see(marker='look-mode')
        dark = [e for e in user.client.layout.descendants() if isinstance(e, ui.dark_mode)]
        assert [d.value for d in dark] == [True]  # switched at once


def test_the_device_cookie(monkeypatch):
    from types import SimpleNamespace

    from rpapp.ui import appearance
    for value, expected in (('dark.large', ('dark', 'large')), ('', ('auto', 'normal')),
                            ('purple.huge', ('auto', 'normal')), ('light', ('light', 'normal'))):
        request = SimpleNamespace(cookies={appearance.COOKIE: value})
        monkeypatch.setattr(appearance.ui, 'context', SimpleNamespace(client=SimpleNamespace(request=request)))
        look = appearance.device_look()
        assert (look.mode, look.size) == expected
    assert appearance.Look('dark', 'larger').text_size == '1.25rem'
