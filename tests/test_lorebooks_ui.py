"""Clicks through the Lore Books pages with NiceGUI's simulated user."""
import json
from pathlib import Path

import pytest
from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import user_simulation

from rpapp.lorebooks import lore_book_store, new_lore_book
from rpapp.ui.app import build_root

ELDORIA_FILE = Path(__file__).parent / 'eldoria_sillytavern.json'


@pytest.fixture
async def setup(tmp_path):
    async with user_simulation(build_root(tmp_path)) as user:
        yield user, lore_book_store(tmp_path)


def by_label(user, kind):
    return {e.props.get('label'): e for e in user.find(kind).elements}


async def import_eldoria(user):
    await user.open('/lorebooks')
    upload = user.find(ui.upload).elements.pop()
    await upload.handle_uploads([SmallFileUpload(name='Eldoria.json', content_type='application/json',
                                                 _data=ELDORIA_FILE.read_bytes())])
    await user.should_see('Imported "Eldoria"')


async def test_menu_and_empty_list(setup):
    user, _ = setup
    await user.open('/')
    user.find(marker='nav-lore-books').click()
    await user.should_see('No lore books yet')


async def test_import_open_export(setup):
    user, store = setup
    original = ELDORIA_FILE.read_bytes()
    await import_eldoria(user)
    [book] = store.list()
    await user.should_see('4 entries')
    assert ELDORIA_FILE.read_bytes() == original

    user.find(marker=f'row-{book.id}').click()
    await user.should_see('shadowfang')
    await user.should_see('glade')

    user.find(marker='menu-export').click()
    response = await user.download.next()
    exported = json.loads(response.content)
    assert exported == {'name': 'Eldoria', 'entries': json.loads(original)['entries']}


async def test_export_from_list(setup):
    user, store = setup
    store.save(new_lore_book('Small'))
    await user.open('/lorebooks')
    user.find(marker='menu-export').click()
    exported = json.loads((await user.download.next()).content)
    assert exported == {'name': 'Small', 'entries': {}}


async def test_create_book_add_and_edit_entry(setup):
    user, store = setup
    await user.open('/lorebooks')
    user.find('New').click()
    with user.scope(marker='new-dialog'):
        by_label(user, ui.input)['Name'].set_value('Rivermoor')
        user.find(kind=ui.button, content='Create').click()
    await user.should_see('No entries yet')
    [book] = store.list()
    assert book.name == 'Rivermoor'

    user.find('New entry').click()
    await user.should_see('Entry in Rivermoor')
    by_label(user, ui.input)['Title'].set_value('The ferryman')
    by_label(user, ui.input_chips)['Keywords'].set_value(['ferry', 'ferryman'])
    by_label(user, ui.textarea)['Content'].set_value('Old Brannoc runs the ferry across the Moor river.')
    await user.should_see('49 characters, about 14 tokens')

    selects = by_label(user, ui.select)
    selects['Position in the prompt'].set_value(4)  # at depth: the role appears, set to System
    await user.should_see('Sent as')
    selects = by_label(user, ui.select)
    assert selects['Sent as'].value == 0
    selects['Sent as'].set_value(1)
    selects['Case-sensitive'].set_value(True)
    selects['When to include it'].set_value('constant')
    by_label(user, ui.number)['Sticky'].set_value(3)

    entry = store.load(book.id).entry(0)  # reading writes the pending (delayed) save first
    assert entry.comment == 'The ferryman' and entry.key == ['ferry', 'ferryman']
    assert entry.content.startswith('Old Brannoc') and entry.position == 4 and entry.role == 1
    assert entry.caseSensitive is True and entry.constant is True and entry.sticky == 3

    selects['Position in the prompt'].set_value(1)
    assert store.load(book.id).entry(0).role is None

    user.find(marker='menu-duplicate').click()
    await user.should_see('The ferryman (copy)')
    assert len(store.load(book.id).entries) == 2


async def test_entry_actions_on_book_page(setup):
    user, store = setup
    book = new_lore_book('Book')
    book.add_entry(comment='First')
    book.add_entry(comment='Second')
    store.save(book)
    await user.open(f'/lorebooks/{book.id}')

    with user.scope(marker='row-0'):
        user.find(marker='menu-toggle').click()
    await user.should_see('Disabled')  # the icon's tooltip
    assert store.load(book.id).entry(0).disable is True

    with user.scope(marker='row-1'):
        user.find(marker='menu-duplicate').click()
    await user.should_see(marker='row-2')  # the list has been redrawn with the copy

    with user.scope(marker='row-0'):
        user.find(marker='menu-delete').click()
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Delete').click()
    await user.should_see('Deleted "First"')
    titles = [e.title for e in store.load(book.id).sorted_entries()]
    assert titles == ['Second', 'Second (copy)']


async def test_search_entries(setup):
    user, store = setup
    book = new_lore_book('Big')
    for i in range(30):
        book.add_entry(comment=f'Entry {i}', key=[f'word{i}'], content='dragon' if i == 7 else 'nothing')
    store.save(book)
    await user.open(f'/lorebooks/{book.id}')
    search = next(e for e in user.find(ui.input).elements if e.props.get('placeholder') == 'Search')
    search.set_value('dragon')
    await user.should_see('Entry 7')
    await user.should_not_see('Entry 8')


async def test_rename_book_and_duplicate_delete_from_list(setup):
    user, store = setup
    book = new_lore_book('Old name')
    store.save(book)
    await user.open(f'/lorebooks/{book.id}')
    by_label(user, ui.input)['Name'].set_value('New name')
    assert store.load(book.id).name == 'New name'

    await user.open('/lorebooks')
    user.find(marker='menu-duplicate').click()
    await user.should_see('Duplicated as "New name (copy)"')
    copy = next(b for b in store.list() if b.id != book.id)
    await user.should_see(marker=f'row-{copy.id}')
    with user.scope(marker=f'row-{copy.id}'):
        user.find(marker='menu-delete').click()
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Delete').click()
    await user.should_not_see(marker=f'row-{copy.id}')
    assert [b.id for b in store.list()] == [book.id]


async def test_importing_a_connection_file_as_lore_book_fails(setup):
    user, store = setup
    await user.open('/lorebooks')
    upload = user.find(ui.upload).elements.pop()
    await upload.handle_uploads([SmallFileUpload(name='conn.json', content_type='application/json',
                                                 _data=b'{"kind": "openrouter", "name": "x"}')])
    await user.should_see('Could not import conn.json: entries: Field required')
    assert store.list() == []


async def test_missing_book_and_entry(setup):
    user, store = setup
    await user.open('/lorebooks/nope')
    await user.should_see('Lore book not found')
    book = new_lore_book('B')
    store.save(book)
    await user.open(f'/lorebooks/{book.id}/entries/42')
    await user.should_see('Entry not found')
