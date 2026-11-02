"""Where lore books are used: the list's note, the delete warning, the book page's sections."""
import pytest
from nicegui import ui
from nicegui.testing import user_simulation

from rpapp.characters import CharacterStore, new_character
from rpapp.lorebooks import lore_book_store, new_lore_book
from rpapp.personas import PersonaStore, new_persona
from rpapp.ui.app import build_root


@pytest.fixture
async def setup(tmp_path):
    lore, personas, characters = lore_book_store(tmp_path), PersonaStore(tmp_path), CharacterStore(tmp_path)
    eldoria, unused = new_lore_book('Eldoria'), new_lore_book('Unused')
    lore.save(eldoria)
    lore.save(unused)
    for title in ('Mira', 'Wren'):
        persona = new_persona(title)
        persona.add_lorebook(eldoria.id, eldoria.name)
        personas.save(persona)
    seraphina = new_character('Seraphina')
    seraphina.add_lorebook(eldoria.id, eldoria.name)
    characters.save(seraphina)
    characters.save(new_character('Kestrel'))  # links to nothing
    async with user_simulation(build_root(tmp_path)) as user:
        yield user, lore, eldoria, unused, seraphina


def test_usage_counts(tmp_path):
    personas = PersonaStore(tmp_path)
    persona = new_persona('Mira')
    persona.add_lorebook('a', 'A')
    persona.add_lorebook('b', 'B')
    personas.save(persona)
    assert personas.lorebook_usage() == {'a': 1, 'b': 1}
    assert [p.title for p in personas.using_lorebook('a')] == ['Mira'] and personas.using_lorebook('c') == []


async def test_list_shows_where_each_book_is_used(setup):
    user, _, eldoria, unused, _ = setup
    await user.open('/lorebooks')
    with user.scope(marker=f'row-{eldoria.id}'):
        await user.should_see('Used by 1 character and 2 personas')
    with user.scope(marker=f'row-{unused.id}'):
        await user.should_see('Not used by any character or persona')


async def test_delete_warns_when_the_book_is_used(setup):
    user, lore, eldoria, unused, _ = setup
    await user.open('/lorebooks')
    with user.scope(marker=f'row-{eldoria.id}'):
        user.find(marker='menu-delete').click()
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        warning = user.find(marker='confirm-warning').elements.pop()
        assert warning.text == 'It is used by 1 character and 2 personas. They will show it as missing.'
        assert {'text-negative', 'text-weight-bold'} <= set(warning.classes)
        user.find(kind=ui.button, content='Cancel').click()
    await user.should_not_see(marker='confirm-dialog')

    # An unused book: no warning line.
    with user.scope(marker=f'row-{unused.id}'):
        user.find(marker='menu-delete').click()
    await user.should_see(marker='confirm-dialog')
    await user.should_not_see(marker='confirm-warning')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Delete').click()
    await user.should_see('Deleted "Unused"')
    assert [b.id for b in lore.list()] == [eldoria.id]


async def test_delete_from_the_book_page_warns_too(setup):
    user, _, eldoria, _, _ = setup
    await user.open(f'/lorebooks/{eldoria.id}')
    user.find(marker='menu-delete').click()
    await user.should_see(marker='confirm-warning')


async def test_book_page_lists_characters_and_personas(setup):
    user, _, eldoria, unused, seraphina = setup
    await user.open(f'/lorebooks/{eldoria.id}')
    await user.should_see('Characters using this book')
    await user.should_see('Personas using this book')
    with user.scope(marker='used-by-characters'):
        await user.should_see('Seraphina')
        await user.should_not_see('Kestrel')
    with user.scope(marker='used-by-personas'):
        await user.should_see('Mira')
        await user.should_see('Wren')
    user.find(marker=f'link-{seraphina.id}').click()
    await user.should_see('Character. Changes save automatically.')

    await user.open(f'/lorebooks/{unused.id}')
    with user.scope(marker='used-by-personas'):
        await user.should_see("No persona uses this lore book. You can add it from a persona's page.")
