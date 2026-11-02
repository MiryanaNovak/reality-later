"""Characters: the model and the interface. The shared profile parts (picture, lore books)
are tested in depth in test_personas.py; here we check they work for characters too."""
import asyncio
import io
import json

import pytest
from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import user_simulation
from PIL import Image
from pydantic import ValidationError

from rpapp.characters import Character, CharacterStore, new_character
from rpapp.lorebooks import lore_book_store, new_lore_book
from rpapp.personas import PersonaStore
from rpapp.ui.app import build_root


def png() -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (500, 500), (30, 90, 60)).save(out, 'PNG')
    return out.getvalue()


async def until(condition, timeout: float = 2.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError('condition not met in time')


# --- the model -------------------------------------------------------------------

def test_saved_file(tmp_path):
    store = CharacterStore(tmp_path)
    character = new_character('Seraphina (forest guardian)')
    character.name = 'Seraphina'
    character.first_messages = ['*She smiles.* Welcome, {{user}}.', 'Another opening.']
    store.save(character)
    data = json.loads(store.path_for(character.id).read_text())
    assert list(data)[:3] == ['id', 'title', 'name']
    assert data['first_messages'] == ['*She smiles.* Welcome, {{user}}.', 'Another opening.']
    assert store.path_for(character.id).parent.name == 'characters'


def test_at_least_one_first_message():
    assert Character().first_messages == ['']
    with pytest.raises(ValidationError):
        Character(first_messages=[])
    character = Character()
    with pytest.raises(ValidationError):
        character.first_messages = []


def test_pictures_are_kept_apart_from_personas(tmp_path):
    characters, personas = CharacterStore(tmp_path), PersonaStore(tmp_path)
    assert characters.images.folder != personas.images.folder


# --- the interface ---------------------------------------------------------------

@pytest.fixture
async def setup(tmp_path):
    async with user_simulation(build_root(tmp_path)) as user:
        yield user, CharacterStore(tmp_path), lore_book_store(tmp_path)


def by_label(user, kind):
    return {e.props.get('label'): e for e in user.find(kind).elements}


def message_box(user, index: int) -> ui.textarea:
    with user.scope(marker=f'message-{index}'):
        return user.find(ui.textarea).elements.pop()


async def test_create_and_edit(setup):
    user, store, lore = setup
    eldoria = new_lore_book('Eldoria')
    lore.save(eldoria)

    await user.open('/')
    user.find(marker='nav-characters').click()
    await user.should_see('A character is who you chat with')  # the Characters page's empty grid
    user.find('New').click()
    with user.scope(marker='new-dialog'):
        by_label(user, ui.input)['Title'].set_value('Seraphina (forest guardian)')
        user.find(kind=ui.button, content='Create').click()
    await user.should_see('Character. Changes save automatically.')
    [character] = store.list()

    by_label(user, ui.input)['Name'].set_value('Seraphina')
    areas = by_label(user, ui.textarea)
    areas['Description'].set_value('Guardian of the glade. Kind, but fierce when needed.')
    areas['Scenario'].set_value('{{user}} wakes up wounded in Seraphina\'s glade.')
    await user.should_see('47 characters, about 14 tokens')
    await user.should_see('52 characters')  # the description: no tokens, it's never sent
    # Each part says who gets it.
    await user.should_see(marker='audience-legend')
    await user.should_see('Name, Scenario, First messages, Example dialogues, Lore books')
    await user.should_see('Title, Picture, Description')
    assert len(user.find(marker='audience-ai').elements) == 5   # intro, Scenario, First and example, Lore
    assert len(user.find(marker='audience-you').elements) == 2  # intro, Description
    await user.should_see('The AI never sees it.')
    message_box(user, 0).set_value('*She smiles.* "You\'re awake."')
    by_label(user, ui.select)['Add a lore book'].set_value(eldoria.id)
    await user.should_see(marker=f'lorebook-{eldoria.id}')

    saved = store.load(character.id)
    assert saved.name == 'Seraphina' and saved.description.startswith('Guardian')
    assert saved.scenario.startswith('{{user}}') and saved.first_messages == ['*She smiles.* "You\'re awake."']
    assert [r.name for r in saved.lorebooks] == ['Eldoria']

    await user.open('/characters')
    await user.should_see('Guardian of the glade. Kind, but fierce when needed.')  # card subtitle


async def test_first_messages_add_reorder_delete(setup):
    user, store, _ = setup
    character = new_character('Hale')
    character.first_messages = ['First']
    store.save(character)
    await user.open(f'/characters/{character.id}')
    await user.should_not_see(marker='delete-message')  # the only message can't be deleted

    user.find('Add a first message').click()
    await user.should_see(marker='message-1')
    message_box(user, 1).set_value('Second')
    assert store.load(character.id).first_messages == ['First', 'Second']

    with user.scope(marker='message-1'):
        user.find(marker='move-up').click()
    await until(lambda: store.load(character.id).first_messages == ['Second', 'First'])
    await until(lambda: message_box(user, 0).value == 'Second')  # the list has been redrawn

    with user.scope(marker='message-0'):
        user.find(marker='delete-message').click()  # it has text: asks first
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Delete').click()
    await until(lambda: store.load(character.id).first_messages == ['First'])
    await user.should_not_see(marker='delete-message')


async def test_example_dialogues(setup):
    user, store, _ = setup
    character = new_character('Hale')
    store.save(character)
    assert character.example_dialogues == []  # none to start with
    await user.open(f'/characters/{character.id}')
    await user.should_see('Example dialogues')
    await user.should_not_see(marker='example-0')

    user.find(marker='add-example').click()
    await user.should_see(marker='example-0')
    with user.scope(marker='example-0'):
        user.find(ui.textarea).elements.pop().set_value('{{user}}: Hi.\n{{char}}: *nods*')
    await until(lambda: store.load(character.id).example_dialogues == ['{{user}}: Hi.\n{{char}}: *nods*'])

    with user.scope(marker='example-0'):
        user.find(marker='delete-example').click()  # unlike first messages, the last one can go
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Delete').click()
    await until(lambda: store.load(character.id).example_dialogues == [])
    await user.should_not_see(marker='example-0')


async def test_picture_and_missing_lore_book(setup):
    user, store, lore = setup
    book = new_lore_book('Ashfall Isles')
    lore.save(book)
    character = new_character('Wren')
    character.add_lorebook(book.id, book.name)
    store.save(character)
    lore.delete(book.id)

    await user.open(f'/characters/{character.id}')
    await user.should_see('Lore book "Ashfall Isles" is missing')
    picker = user.find(ui.upload).elements.pop()
    await picker.handle_uploads([SmallFileUpload(name='wren.png', content_type='image/png', _data=png())])
    await until(lambda: store.load(character.id).image != '')
    image = store.load(character.id).image
    assert (await user.http_client.get(f'/media/characters/{image}')).status_code == 200

    await user.open('/characters')
    await user.should_see('1 lore book missing')
