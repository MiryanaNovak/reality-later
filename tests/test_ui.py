"""Clicks through the interface with NiceGUI's simulated user (no browser needed)."""
import json

import pytest
from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import user_simulation

from rpapp.connections import GenerationParams, OpenAICompatibleConnection, OpenRouterConnection, connection_store
from rpapp.ui.app import build_root


@pytest.fixture
async def setup(tmp_path):
    async with user_simulation(build_root(tmp_path)) as user:
        yield user, connection_store(tmp_path)


def by_label(user, kind):
    return {e.props.get('label'): e for e in user.find(kind).elements}


def upload_file(name: str, content: bytes) -> SmallFileUpload:
    return SmallFileUpload(name=name, content_type='application/json', _data=content)


async def test_menu_and_landing_page(setup):
    user, _ = setup
    await user.open('/')
    await user.should_see(marker='tip-create-connection')
    user.find(marker='nav-connections').click()
    await user.should_see('No connections yet')
    user.find(marker='nav-home').click()
    await user.should_see('Recent conversations')


async def test_create_then_edit_saves_automatically(setup):
    user, store = setup
    await user.open('/connections')
    user.find('New').click()
    with user.scope(marker='new-dialog'):
        by_label(user, ui.input)['Name'].set_value('My DeepSeek')
        user.find('Create').click()
    await user.should_see('OpenRouter connection')

    [conn] = store.list()
    assert isinstance(conn, OpenRouterConnection) and conn.name == 'My DeepSeek'

    inputs = by_label(user, ui.input)
    inputs['API key'].set_value('sk-or-123')
    inputs['Model id'].set_value('deepseek/deepseek-chat')
    numbers = by_label(user, ui.number)
    numbers['Temperature'].set_value(0.85)
    numbers['Max reply length (tokens)'].set_value(300.0)
    numbers['Top P'].set_value(5)  # invalid: error shown under the field, not saved
    assert numbers['Top P'].error == 'Input should be less than or equal to 1'
    numbers['Top P'].set_value(0)
    assert numbers['Top P'].error == 'Input should be greater than 0'
    by_label(user, ui.input_chips)['Stop sequences'].set_value(['\nUser:'])

    saved = store.load(conn.id)
    assert saved.api_key == 'sk-or-123' and saved.model == 'deepseek/deepseek-chat'
    assert saved.params.temperature == 0.85 and saved.params.max_tokens == 300 and saved.params.top_p is None
    assert saved.params.stop == ['\nUser:']

    numbers['Top P'].set_value(0.9)
    assert numbers['Top P'].error is None and store.load(conn.id).params.top_p == 0.9

    selects = by_label(user, ui.select)
    selects['Data policy'].set_value('deny')
    selects['Reasoning effort'].set_value('low')
    selects['Allowed precisions'].set_value(['fp8', 'bf16'])
    saved = store.load(conn.id)
    assert saved.provider.data_collection == 'deny' and saved.reasoning_effort == 'low'
    assert saved.provider.quantizations == ['fp8', 'bf16']
    selects['Reasoning effort'].set_value('')
    assert store.load(conn.id).reasoning_effort is None
    selects['Instructions inside the chat'].set_value('user')
    numbers['Context size (tokens)'].set_value(64000)
    saved = store.load(conn.id)
    assert saved.system_in_chat == 'user' and saved.context_window == 64000

    inputs['Name'].set_value('Renamed')
    await user.should_see('Renamed')
    assert store.load(conn.id).name == 'Renamed'


async def test_local_server_editor_has_its_own_fields(setup):
    user, store = setup
    conn = OpenAICompatibleConnection(name='Laptop')
    store.save(conn)
    await user.open(f'/connections/{conn.id}')
    await user.should_see('OpenAI-compatible server connection')
    await user.should_not_see('Provider routing')
    user.find('Ollama').click()
    assert store.load(conn.id).base_url == 'http://localhost:11434/v1'


async def test_duplicate_and_delete_from_the_list(setup):
    user, store = setup
    original = OpenRouterConnection(name='Original', model='x/y', params=GenerationParams(temperature=1.1))
    store.save(original)
    await user.open('/connections')
    user.find(marker='menu-duplicate').click()
    await user.should_see('Original (copy)')
    [copy] = [c for c in store.list() if c.name == 'Original (copy)']
    assert copy.id != original.id and copy.params.temperature == 1.1 and copy.model == 'x/y'
    await user.should_see(marker=f'row-{copy.id}')

    with user.scope(marker=f'row-{copy.id}'):
        user.find(marker='menu-delete').click()
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        await user.should_see('Delete "Original (copy)"?')
        user.find(kind=ui.button, content='Delete').click()
    await user.should_not_see(marker=f'row-{copy.id}')
    assert [c.id for c in store.list()] == [original.id]


async def test_duplicate_and_delete_from_the_editor(setup):
    user, store = setup
    original = OpenRouterConnection(name='Solo')
    store.save(original)
    await user.open(f'/connections/{original.id}')
    user.find(marker='menu-duplicate').click()
    await user.should_see('Solo (copy)')
    [copy] = [c for c in store.list() if c.id != original.id]
    await user.should_see(kind=ui.label, content='Solo (copy)')  # now on the copy's page
    user.find(marker='menu-delete').click()
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Delete').click()
    await user.should_see('Deleted "Solo (copy)"')
    await user.should_see(marker=f'row-{original.id}')  # back on the list
    assert [c.id for c in store.list()] == [original.id]


async def test_cancel_delete_keeps_it(setup):
    user, store = setup
    store.save(OpenRouterConnection(name='Keep me'))
    await user.open('/connections')
    user.find(marker='menu-delete').click()
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        user.find('Cancel').click()
    await user.should_see('Keep me')
    assert len(store.list()) == 1


async def test_import_makes_a_copy_and_never_overwrites(setup, tmp_path):
    user, store = setup
    existing = OpenRouterConnection(name='Kept', model='a/b')
    store.save(existing)
    original_file = tmp_path / 'outside.json'
    data = json.loads(store.path_for(existing.id).read_text(encoding='utf-8')) | {'name': 'From file'}
    original_file.write_text(json.dumps(data), encoding='utf-8')
    before = original_file.read_bytes()

    await user.open('/connections')
    upload = user.find(ui.upload).elements.pop()
    await upload.handle_uploads([upload_file('outside.json', original_file.read_bytes())])
    await user.should_see('Imported "From file"')
    assert sorted(c.name for c in store.list()) == ['From file', 'Kept']
    assert store.load(existing.id).name == 'Kept'  # same id as an existing one: saved under a new id
    assert original_file.read_bytes() == before

    await upload.handle_uploads([upload_file('bad.json', b'{"kind": "openrouter", "params": {"temperature": 7}}')])
    await user.should_see('Could not import bad.json: params.temperature: Input should be less than or equal to 2')
    await upload.handle_uploads([upload_file('junk.json', b'not json')])
    await user.should_see('Could not import junk.json')
    assert len(store.list()) == 2


async def test_import_keeps_a_free_id(setup):
    user, store = setup
    await user.open('/connections')
    upload = user.find(ui.upload).elements.pop()
    await upload.handle_uploads([upload_file('my-local-1a2b3c.json',
                                             b'{"kind": "openai_compatible", "name": "Local", "id": "my-local-1a2b3c"}')])
    await user.should_see('Imported "Local"')
    assert [c.id for c in store.list()] == ['my-local-1a2b3c']


async def test_missing_connection(setup):
    user, _ = setup
    await user.open('/connections/does-not-exist')
    await user.should_see('Connection not found')


async def test_page_titles_live_in_the_top_bar(setup):
    user, store = setup
    conn = OpenAICompatibleConnection(name='Laptop')
    store.save(conn)
    await user.open('/connections')
    with user.scope(marker='app-bar'):
        await user.should_see('Connections')
        await user.should_see(marker='new')  # the page's buttons go there too
    await user.open(f'/connections/{conn.id}')
    with user.scope(marker='app-bar'):
        await user.should_see('Laptop')
        await user.should_see(marker='bar-back')
        await user.should_see(marker='bar-menu')
        await user.should_not_see(marker='new')  # the list's buttons are gone
    await user.should_see('OpenAI-compatible server connection')  # the caption: first line of the page
    await user.open('/settings')
    with user.scope(marker='app-bar'):
        await user.should_see('Settings')
        await user.should_not_see(marker='bar-back')
        await user.should_not_see('Laptop')
    await user.should_not_see('Reality Later')  # the app's name is in the bar on Home only, and in the tab's title
