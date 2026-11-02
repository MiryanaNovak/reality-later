"""Personas: the model, image storage, and the interface."""
import asyncio
import io
import json

import pytest
from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import user_simulation
from PIL import Image

from rpapp.lorebooks import lore_book_store, new_lore_book
from rpapp.personas import Persona, PersonaStore, new_persona
from rpapp.ui.app import build_root


def png(width: int = 600, height: int = 600, color=(180, 40, 90)) -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (width, height), color).save(out, 'PNG')
    return out.getvalue()


def upload(name: str, data: bytes) -> SmallFileUpload:
    return SmallFileUpload(name=name, content_type='image/png', _data=data)


async def until(condition, timeout: float = 2.0) -> None:
    """Uploads are handled in the background: wait for their effect."""
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError('condition not met in time')


# --- the model -------------------------------------------------------------------

def test_saved_file_and_label(tmp_path):
    store = PersonaStore(tmp_path)
    persona = new_persona('Mira (dark fantasy)')
    persona.name = 'Mira'
    persona.pronouns = 'she/her'
    persona.add_lorebook('eldoria', 'Eldoria')
    store.save(persona)
    data = json.loads(store.path_for(persona.id).read_text())
    assert list(data)[:3] == ['id', 'title', 'name']
    assert data['lorebooks'] == [{'id': 'eldoria', 'name': 'Eldoria'}]
    assert persona.id.startswith('mira-dark-fantasy-') and persona.label == 'Mira (dark fantasy)'
    assert persona.pronoun_forms.reflexive == 'herself'
    copy = store.duplicate(persona)
    assert copy.title == 'Mira (dark fantasy) (copy)' and copy.name == 'Mira'


def test_lore_book_references():
    persona = Persona()
    persona.add_lorebook('a', 'Book A')
    persona.add_lorebook('a', 'Book A')  # no duplicates
    persona.add_lorebook('b', 'Book B')
    assert [r.name for r in persona.missing_lorebooks(lambda book_id: book_id == 'a')] == ['Book B']
    persona.replace_lorebook('b', 'b2', 'Book B')
    assert [(r.id, r.name) for r in persona.lorebooks] == [('a', 'Book A'), ('b2', 'Book B')]
    persona.remove_lorebook('a')
    assert [r.id for r in persona.lorebooks] == ['b2']


def test_images_are_shrunk_shared_and_cleaned_up(tmp_path):
    store = PersonaStore(tmp_path)
    name = store.images.add(png(2400, 2400))
    assert name.endswith('.webp') and Image.open(store.images.path(name)).size == (1024, 1024)
    assert store.images.add(png(2400, 2400)) == name  # same picture, same file

    first = new_persona('First')
    first.image = name
    store.save(first)
    second = store.duplicate(first)
    store.delete(first.id)
    assert store.images.path(name) is not None  # the copy still uses it
    store.delete(second.id)
    assert store.images.path(name) is None

    with pytest.raises(ValueError, match="isn't an image"):
        store.images.add(b'definitely not a picture')


# --- the interface ---------------------------------------------------------------

@pytest.fixture
async def setup(tmp_path):
    async with user_simulation(build_root(tmp_path)) as user:
        yield user, PersonaStore(tmp_path), lore_book_store(tmp_path)


def by_label(user, kind):
    return {e.props.get('label'): e for e in user.find(kind).elements}


async def test_grid_create_and_edit(setup):
    user, store, _ = setup
    await user.open('/')
    user.find(marker='nav-personas').click()
    await user.should_see('No personas yet')

    user.find('New').click()
    with user.scope(marker='new-dialog'):
        await user.should_see('Title')
        by_label(user, ui.input)['Title'].set_value('Mira, wandering healer')
        user.find(kind=ui.button, content='Create').click()
    await user.should_see('Persona. Changes save automatically.')
    [persona] = store.list()

    inputs = by_label(user, ui.input)
    inputs['Name'].set_value('Mira')
    selects = by_label(user, ui.select)
    selects['Gender'].set_value('female')
    selects['Pronouns'].set_value('she/her')
    by_label(user, ui.textarea)['Description'].set_value('A healer who walks the old roads.')
    await user.should_see('33 characters, about 10 tokens')
    # Each part says who gets it.
    await user.should_see('Name, Gender, Pronouns, Description, Lore books')
    await user.should_see('Title, Picture')
    assert len(user.find(marker='audience-ai').elements) == 3   # the summary, Description, Lore books
    assert len(user.find(marker='audience-you').elements) == 1  # the summary
    await user.should_see(marker='gender-pronouns-hint')

    saved = store.load(persona.id)
    assert (saved.name, saved.gender, saved.pronouns) == ('Mira', 'female', 'she/her')
    assert saved.description == 'A healer who walks the old roads.'

    await user.open('/personas')
    await user.should_see('Mira, she/her')  # the card's subtitle
    user.find(marker=f'row-{persona.id}').click()
    await user.should_see('Persona. Changes save automatically.')


async def test_picture_upload_replace_remove(setup):
    user, store, _ = setup
    persona = new_persona('Mira')
    store.save(persona)
    await user.open(f'/personas/{persona.id}')
    await user.should_see('MI')  # initials until there's a picture
    picker = user.find(ui.upload).elements.pop()

    await picker.handle_uploads([upload('mira.png', png(color=(10, 120, 200)))])
    await user.should_see(kind=ui.image)
    first = store.load(persona.id).image
    assert store.images.path(first) is not None

    await picker.handle_uploads([upload('mira2.png', png(color=(200, 120, 10)))])
    await until(lambda: store.load(persona.id).image != first)
    second = store.load(persona.id).image
    assert second != first and store.images.path(first) is None  # the old file was unused, so it's gone

    response = await user.http_client.get(f'/media/personas/{second}')
    assert response.status_code == 200 and response.headers['content-type'] == 'image/webp'
    assert (await user.http_client.get('/media/personas/../secrets.json')).status_code == 404

    await picker.handle_uploads([upload('notes.png', b'not an image')])
    await user.should_see("That file isn't an image")
    assert store.load(persona.id).image == second

    user.find(marker='remove-picture').click()
    await user.should_see('Add picture')
    assert store.load(persona.id).image == '' and store.images.path(second) is None


async def test_lore_books_add_remove_and_missing(setup):
    user, store, lore = setup
    eldoria = new_lore_book('Eldoria')
    rivermoor = new_lore_book('Rivermoor')
    lore.save(eldoria)
    lore.save(rivermoor)
    persona = new_persona('Mira')
    store.save(persona)

    await user.open(f'/personas/{persona.id}')
    by_label(user, ui.select)['Add a lore book'].set_value(eldoria.id)
    await user.should_see(marker=f'lorebook-{eldoria.id}')
    by_label(user, ui.select)['Add a lore book'].set_value(rivermoor.id)
    await user.should_see(marker=f'lorebook-{rivermoor.id}')
    assert [r.name for r in store.load(persona.id).lorebooks] == ['Eldoria', 'Rivermoor']

    # A deleted book stays listed, clearly marked, and the grid shows the problem too.
    lore.delete(eldoria.id)
    await user.open(f'/personas/{persona.id}')
    await user.should_see('Lore book "Eldoria" is missing')
    await user.should_see('It was deleted, or its file was renamed')
    await user.open('/personas')
    await user.should_see('1 lore book missing')

    # Imported again (new id, same name): it can be relinked.
    again = new_lore_book('Eldoria')
    lore.save(again)
    await user.open(f'/personas/{persona.id}')
    await user.should_see('A lore book with this name exists')
    with user.scope(marker=f'lorebook-{eldoria.id}'):
        user.find(kind=ui.button, content='Relink').click()
    await user.should_see(marker=f'lorebook-{again.id}')
    assert [r.id for r in store.load(persona.id).lorebooks] == [again.id, rivermoor.id]

    with user.scope(marker=f'lorebook-{rivermoor.id}'):
        user.find(marker='remove-lorebook').click()
    await user.should_not_see(marker=f'lorebook-{rivermoor.id}')
    assert [r.id for r in store.load(persona.id).lorebooks] == [again.id]


async def test_renamed_book_updates_the_remembered_name(setup):
    user, store, lore = setup
    book = new_lore_book('Old name')
    lore.save(book)
    persona = new_persona('Mira')
    persona.add_lorebook(book.id, book.name)
    store.save(persona)
    book.name = 'New name'
    lore.save(book)
    await user.open(f'/personas/{persona.id}')
    await user.should_see('New name')
    assert store.load(persona.id).lorebooks[0].name == 'New name'


async def test_delete_persona_from_grid(setup):
    user, store, _ = setup
    persona = new_persona('Gone soon')
    persona.image = store.images.add(png())
    store.save(persona)
    await user.open('/personas')
    await user.should_not_see(marker='menu-export')  # personas have no import/export
    user.find(marker='menu-delete').click()
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Delete').click()
    await user.should_see('Deleted "Gone soon"')
    assert store.list() == [] and store.images.path(persona.image) is None
