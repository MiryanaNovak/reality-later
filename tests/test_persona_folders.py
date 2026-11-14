"""Folders of personas (rpapp/persona_folders.py), and the Personas page and new chat's picker that show them
(rpapp/ui/personas_page.py), with NiceGUI's simulated user."""
import json

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

from rpapp.characters import CharacterStore, new_character
from rpapp.conversations import ConversationStore
from rpapp.persona_folders import NAME_MAX, FolderError, PersonaFolderStore
from rpapp.personas import PersonaStore, new_persona
from rpapp.ui.app import build_root


@pytest.fixture
def personas(tmp_path):
    store = PersonaStore(tmp_path)
    made = {}
    for title in ('Ash, ranger', 'Bram, blacksmith', 'Lune, star pilot', 'Nell, detective'):
        persona = new_persona(title)
        store.save(persona)
        made[title.split(',')[0]] = persona
    return store, made


def folders_of(tmp_path, store) -> PersonaFolderStore:
    return PersonaFolderStore(tmp_path, exists=store.exists)


# --- the folders ----------------------------------------------------------------------------------

def test_making_and_naming_folders(tmp_path, personas):
    folders = folders_of(tmp_path, personas[0])
    sci, fantasy = folders.create('  Sci-fi '), folders.create('Fantasy   worlds')
    assert [f.name for f in folders.list()] == ['Fantasy worlds', 'Sci-fi']  # (by name; spaces tidied)
    for bad, said in (('   ', 'Give the folder a name.'), ('sci-FI', 'There\'s already a folder named "Sci-fi".'),
                      ('x' * (NAME_MAX + 1), f'The name is too long: {NAME_MAX} characters at most.')):
        with pytest.raises(FolderError, match=said.replace('(', r'\(').replace('.', r'\.')):
            folders.create(bad)
    folders.rename(sci.id, 'Science fiction')
    folders.rename(fantasy.id, 'FANTASY worlds')  # (its own name, otherwise written: fine)
    with pytest.raises(FolderError, match='already a folder named "FANTASY worlds"'):
        folders.rename(sci.id, 'fantasy worlds')
    assert [f.name for f in folders.list()] == ['FANTASY worlds', 'Science fiction']
    saved = json.loads((tmp_path / 'persona-folders.json').read_text(encoding='utf-8'))
    assert saved['id'] == 'persona-folders' and len(saved['folders']) == 2
    with pytest.raises(FolderError, match='deleted meanwhile'):
        folders.rename('gone-123456', 'Anything')


def test_a_persona_in_one_folder_at_most(tmp_path, personas):
    store, p = personas
    folders = folders_of(tmp_path, store)
    fantasy, sci = folders.create('Fantasy'), folders.create('Sci-fi')
    folders.put(p['Ash'].id, fantasy.id)
    folders.put(p['Bram'].id, fantasy.id)
    folders.put(p['Ash'].id, sci.id)  # (moved: out of Fantasy)
    assert folders.get(fantasy.id).personas == [p['Bram'].id] and folders.get(sci.id).personas == [p['Ash'].id]
    assert folders.folder_of(p['Ash'].id).name == 'Sci-fi' and folders.folder_of(p['Nell'].id) is None
    folders.put(p['Ash'].id, None)  # (in none)
    assert folders.folder_of(p['Ash'].id) is None
    assert set(folders.by_persona()) == {p['Bram'].id}


def test_deleting_a_folder_keeps_its_personas(tmp_path, personas):
    store, p = personas
    folders = folders_of(tmp_path, store)
    fantasy = folders.create('Fantasy')
    folders.put(p['Ash'].id, fantasy.id)
    folders.put(p['Bram'].id, fantasy.id)
    assert folders.delete(fantasy.id) == 2
    assert folders.list() == [] and {x.id for x in store.list()} == {x.id for x in p.values()}


def test_deleted_personas_are_forgotten(tmp_path, personas):
    store, p = personas
    folders = folders_of(tmp_path, store)
    fantasy = folders.create('Fantasy')
    for name in ('Ash', 'Bram'):
        folders.put(p[name].id, fantasy.id)
    store.delete(p['Ash'].id)
    folders.forget([p['Ash'].id])
    assert folders.get(fantasy.id).personas == [p['Bram'].id]
    store.delete(p['Bram'].id)  # (deleted elsewhere: forgotten at the next change)
    folders.create('Sci-fi')
    assert folders.get(fantasy.id).personas == []


def test_a_damaged_file(tmp_path, personas):
    (tmp_path / 'persona-folders.json').write_text('{oops', encoding='utf-8')
    assert folders_of(tmp_path, personas[0]).list() == []


# --- the Personas page --------------------------------------------------------------------------

def inside(user, outer: str, inner: str) -> bool:
    """Whether the element marked `inner` is in the one marked `outer`."""
    [element] = user.find(marker=outer).elements
    return any(inner in e._markers for e in element.descendants())


@pytest.fixture
async def page(tmp_path, personas):
    store, made = personas
    async with user_simulation(build_root(tmp_path)) as user:
        yield user, folders_of(tmp_path, store), made


async def test_new_folder_and_putting_a_persona_in_it(page):
    user, folders, p = page
    await user.open('/personas')
    await user.should_see('Ash, ranger')
    user.find(marker='new-folder').click()
    await user.should_see(marker='folder-dialog')
    user.find(marker='folder-name').type('Fantasy')
    user.find(marker='folder-create').click()
    await user.should_not_see(marker='folder-dialog')
    [fantasy] = folders.list()
    await user.should_see(marker=f'folder-{fantasy.id}')
    await user.should_see('This folder is empty: put personas in it from their ⋮ menu.')  # (open, to fill)

    with user.scope(marker=f'row-{p["Ash"].id}'):
        user.find(marker='menu-add-to-folder').click()
    await user.should_see(marker='folder-picker')
    user.find(marker=f'pick-{fantasy.id}').click()
    await user.should_see('"Ash, ranger" is in Fantasy now')
    assert folders.folder_of(p['Ash'].id).id == fantasy.id
    await user.should_see('1 persona')
    assert inside(user, f'folder-{fantasy.id}', f'row-{p["Ash"].id}')
    assert not inside(user, f'folder-{fantasy.id}', f'row-{p["Nell"].id}')  # (below the folders)

    with user.scope(marker=f'row-{p["Ash"].id}'):  # (in a folder: moved, or taken out)
        await user.should_see(marker='menu-move-to-folder')
        await user.should_not_see(marker='menu-add-to-folder')
        user.find(marker='menu-remove-from-folder').click()
    await user.should_see('"Ash, ranger" is in no folder now')
    assert folders.folder_of(p['Ash'].id) is None
    await user.should_see('Empty')


async def test_a_name_already_taken(page):
    user, folders, _ = page
    folders.create('Fantasy')
    await user.open('/personas')
    user.find(marker='new-folder').click()
    await user.should_see(marker='folder-dialog')
    user.find(marker='folder-name').type('fantasy')
    user.find(marker='folder-create').click()
    await user.should_see(marker='folder-dialog')  # (still open, saying why)
    [field] = user.find(marker='folder-name').elements
    assert field.error == 'There\'s already a folder named "Fantasy".'
    assert len(folders.list()) == 1


async def test_a_new_folder_from_the_picker(page):
    user, folders, p = page
    await user.open('/personas')
    with user.scope(marker=f'row-{p["Nell"].id}'):
        user.find(marker='menu-add-to-folder').click()
    await user.should_see('There\'s no folder yet: make one.')
    user.find(marker='picker-folder-name').type('Noir')
    user.find(marker='picker-create').click()
    await user.should_see('"Nell, detective" is in Noir now')
    [noir] = folders.list()
    assert noir.name == 'Noir' and noir.personas == [p['Nell'].id]


async def test_renaming_and_deleting_a_folder(page):
    user, folders, p = page
    fantasy = folders.create('Fantasy')
    folders.put(p['Ash'].id, fantasy.id)
    folders.put(p['Bram'].id, fantasy.id)
    await user.open('/personas')
    with user.scope(marker=f'folder-{fantasy.id}'):
        user.find(marker='menu-rename-folder').click()
    await user.should_see(marker='name-dialog')
    [field] = user.find(marker='name-input').elements
    field.set_value('High fantasy')
    user.find(marker='name-save').click()
    await user.should_not_see(marker='name-dialog')
    await user.should_see('High fantasy')
    assert folders.get(fantasy.id).name == 'High fantasy'

    with user.scope(marker=f'folder-{fantasy.id}'):
        user.find(marker='menu-delete-folder').click()
    await user.should_see('Delete the folder "High fantasy"?')
    await user.should_see('Its 2 personas stay: in no folder any more.')
    user.find(kind=ui.button, content='Delete').click()
    await user.should_see('Deleted the folder "High fantasy"')
    await user.should_not_see(marker=f'folder-{fantasy.id}')
    assert folders.list() == []
    await user.should_see('Ash, ranger')  # (kept, in no folder)
    await user.should_see('Bram, blacksmith')


async def test_a_copy_stays_in_its_folder(page, tmp_path):
    user, folders, p = page
    fantasy = folders.create('Fantasy')
    folders.put(p['Ash'].id, fantasy.id)
    await user.open('/personas')
    with user.scope(marker=f'row-{p["Ash"].id}'):
        user.find(marker='menu-duplicate').click()
    await user.should_see('Duplicated as')
    assert len(folders.get(fantasy.id).personas) == 2


async def test_searching_through_all(page):
    user, folders, p = page
    for name in ('Extra one', 'Extra two', 'Extra three', 'Extra four'):  # (8 personas: the search shows)
        PersonaStore(folders.path.parent).save(new_persona(name))
    fantasy = folders.create('Fantasy')
    folders.put(p['Ash'].id, fantasy.id)
    await user.open('/personas')
    user.find(marker='persona-search').type('ranger')
    await user.should_see('In Fantasy')  # (found in its folder)
    await user.should_not_see('Nell, detective')


async def test_the_new_chats_picker(page, tmp_path):
    user, folders, p = page
    fantasy, empty = folders.create('Fantasy'), folders.create('Empty one')
    folders.put(p['Ash'].id, fantasy.id)
    seraphina = new_character('Seraphina')
    CharacterStore(tmp_path).save(seraphina)
    await user.open('/characters')
    user.find(marker='new-chat').click()
    await user.should_see(marker='persona-picker')
    await user.should_see(marker=f'picker-folder-{fantasy.id}')
    await user.should_not_see(marker=f'picker-folder-{empty.id}')  # (nothing to choose in it)
    assert inside(user, f'picker-folder-{fantasy.id}', f'link-{p["Ash"].id}')
    assert not inside(user, f'picker-folder-{fantasy.id}', f'link-{p["Nell"].id}')  # (in none: after them)
    user.find(marker=f'link-{p["Ash"].id}').click()
    await user.should_see(marker='chat-input')  # (the chat, started)
    [conversation] = ConversationStore(tmp_path).list()
    assert conversation.persona.id == p['Ash'].id
