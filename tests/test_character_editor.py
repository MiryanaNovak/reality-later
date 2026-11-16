"""The character's page: Identity (with its tags), then parts that fold away; the summary, the description
(written, or as it shows, with its own pictures), and the warning when its card is too big to be published."""
import asyncio
import io

import pytest
from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import user_simulation
from PIL import Image

from rpapp.characters import CharacterStore, new_character
from rpapp.descriptions import MAX_SUMMARY_BYTES, pictures_in
from rpapp.lorebooks import lore_book_store, new_lore_book
from rpapp.ui.app import build_root

PARTS = ['summary', 'description', 'scenario', 'first-messages', 'example-dialogues', 'lore-books',
         'model-and-prompt']


def png(color=(30, 90, 60)) -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (300, 200), color).save(out, 'PNG')
    return out.getvalue()


async def until(condition, timeout: float = 3.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError('condition not met in time')


def one(user, marker):
    """The element with this marker, shown or not."""
    [element] = [e for e in user.client.layout.descendants() if marker in e._markers]
    return element


@pytest.fixture
async def page(tmp_path):
    store = CharacterStore(tmp_path)
    sera, hale = new_character('Seraphina'), new_character('Hale')
    for character in (sera, hale):
        store.save(character)
    async with user_simulation(build_root(tmp_path)) as user:
        yield user, store, sera, hale


async def test_the_parts_fold_but_identity(page, tmp_path):
    user, store, sera, hale = page
    await user.open(f'/characters/{sera.id}/edit')
    await user.should_see('Identity')
    opened = {key: one(user, f'part-{key}').value for key in PARTS}
    assert opened == {'summary': True, 'description': True, 'scenario': True, 'first-messages': True,
                      'example-dialogues': False, 'lore-books': False, 'model-and-prompt': False}
    assert not [e for e in user.find(ui.expansion).elements if 'Identity' in str(e.props.get('label'))]

    one(user, 'part-summary').set_value(False)  # (as left: so it is for the next character too)
    one(user, 'part-example-dialogues').set_value(True)
    user.navigate.to(f'/characters/{hale.id}/edit')
    await user.should_see('Hale')
    await until(lambda: one(user, 'part-scenario').value is True)
    assert (one(user, 'part-summary').value, one(user, 'part-example-dialogues').value) == (False, True)

    book = new_lore_book('Ashfall Isles')  # (a missing lore book: its part open, to see it)
    hale.add_lorebook(book.id, book.name)
    store.save(hale)
    user.navigate.to(f'/characters/{sera.id}/edit')
    await user.should_see('Seraphina')
    user.navigate.to(f'/characters/{hale.id}/edit')
    await user.should_see('Lore book "Ashfall Isles" is missing')
    assert one(user, 'part-lore-books').value is True


async def test_the_parts_open_are_kept_for_the_device(page, monkeypatch):
    import rpapp.ui.opened as opened
    ran = []
    monkeypatch.setattr(opened.ui, 'run_javascript', lambda code: ran.append(code))
    user, store, sera, _ = page
    await user.open(f'/characters/{sera.id}/edit')
    await user.should_see('Identity')
    one(user, 'part-description').set_value(False)
    assert ran == ['document.cookie = "rp_character_sections=first-messages.scenario.summary; path=/; '
                   'max-age=34560000; samesite=lax"']


async def test_the_summary(page):
    user, store, sera, _ = page
    await user.open(f'/characters/{sera.id}/edit')
    field = one(user, 'summary')
    field.set_value('A guardian. Kind, but fierce.')
    await user.should_see('29 characters · 0.0 of 16 KB')
    await until(lambda: store.load(sera.id).summary == 'A guardian. Kind, but fierce.')

    field.set_value('é' * (MAX_SUMMARY_BYTES // 2 + 1))  # (more bytes than characters)
    await until(lambda: bool(field.error))
    assert field.error.startswith('Too long: 16 KB at most, as shared cards hold')
    await asyncio.sleep(0.7)
    assert store.load(sera.id).summary == 'A guardian. Kind, but fierce.'  # (not taken)

    await user.open('/characters')
    await user.should_see('A guardian. Kind, but fierce.')  # (on its card)


async def test_tags(page):
    user, store, sera, _ = page
    await user.open(f'/characters/{sera.id}/edit')
    tags = one(user, 'character-tags')
    tags.set_value(['Dark Fantasy', '#Slow Burn'])
    await until(lambda: tags.value == ['dark-fantasy', 'slow-burn'])  # (shown as they're kept)
    await until(lambda: store.load(sera.id).tags == ['dark-fantasy', 'slow-burn'])
    tags.set_value(['dark-fantasy', 'slow-burn', 'Steampunk, Heist'])  # (typed at once, with a comma)
    await until(lambda: store.load(sera.id).tags == ['dark-fantasy', 'slow-burn', 'steampunk', 'heist'])
    assert tags.value == ['dark-fantasy', 'slow-burn', 'steampunk', 'heist']


async def test_the_search_looks_in_summaries_and_tags(tmp_path):
    store = CharacterStore(tmp_path)
    for i in range(7):
        store.save(new_character(f'Extra {i}'))
    wren = new_character('Wren')
    wren.tags = ['heist']
    wren.summary = 'A thief with a heart of gold.'
    store.save(wren)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open('/characters')
        search = one(user, 'character-search')
        search.set_value('heist')
        await user.should_see(marker=f'row-{wren.id}')
        await user.should_not_see('Extra 1')
        search.set_value('heart of gold')
        await user.should_see(marker=f'row-{wren.id}')


async def test_the_description_written_and_as_it_shows(page):
    user, store, sera, hale = page
    name = store.images.add(png())
    sera.description = (f'# Seraphina\n\nThe **last** guardian.\n![The glade](picture:{name})\n'
                        f'![elsewhere](https://example.com/x.png) [Her song](https://example.com/song)')
    store.save(sera)

    await user.open(f'/characters/{sera.id}/edit')
    assert one(user, 'description-mode').value == 'preview'  # (it has one: as it shows)
    preview = one(user, 'description-preview')
    html = preview.props['innerHTML']
    assert '<h1>Seraphina</h1>' in html and '<strong>last</strong>' in html
    assert f'src="/media/characters/{name}"' in html and 'example.com/x.png' not in html
    assert 'href="https://example.com/song"' in html
    assert (await user.http_client.get(f'/media/characters/{name}')).status_code == 200
    assert not one(user, 'description').visible

    one(user, 'description-mode').set_value('write')
    assert one(user, 'description').visible and not preview.visible
    one(user, 'description').set_value('Now *shorter*.')
    one(user, 'description-mode').set_value('preview')
    assert '<em>shorter</em>' in preview.props['innerHTML']
    await until(lambda: store.load(sera.id).description == 'Now *shorter*.')

    await user.open(f'/characters/{hale.id}/edit')  # (none yet: to write it)
    assert one(user, 'description-mode').value == 'write' and one(user, 'description').visible
    one(user, 'description-mode').set_value('preview')
    await user.should_see('Nothing yet: write it in Write.')


async def test_inserting_a_picture(page):
    user, store, sera, _ = page
    sera.description = 'Before.'
    store.save(sera)
    await user.open(f'/characters/{sera.id}/edit')
    picker = one(user, 'description-upload')
    await picker.handle_uploads([SmallFileUpload(name='glade.png', content_type='image/png', _data=png())])
    await until(lambda: pictures_in(store.load(sera.id).description))
    description = store.load(sera.id).description
    [name] = pictures_in(description)
    assert description == f'Before.\n\n![glade](picture:{name})' and store.images.path(name) is not None
    assert f'/media/characters/{name}' in one(user, 'description-preview').props['innerHTML']  # (as it shows)
    one(user, 'description-mode').set_value('write')
    await user.should_see(f'{len(description)} characters, 1 picture')

    await picker.handle_uploads([SmallFileUpload(name='notes.txt', content_type='text/plain', _data=b'Hi')])
    await user.should_see('That file isn\'t an image this app can read')


async def test_a_card_too_big_to_publish_is_said(page, monkeypatch):
    import rpapp.ui.characters_page as characters_page
    user, store, sera, hale = page
    await user.open(f'/characters/{sera.id}/edit')
    await user.should_see('Identity')
    await asyncio.sleep(0.3)
    await user.should_not_see(marker='too-big')

    monkeypatch.setattr(characters_page, 'MAX_FILE_BYTES', 2000)
    noise = io.BytesIO()
    Image.effect_noise((400, 400), 100).convert('RGB').save(noise, 'PNG')  # (big, even as WebP)
    name = store.images.add(noise.getvalue())
    hale.description = f'![a](picture:{name})'
    store.save(hale)
    await user.open(f'/characters/{hale.id}/edit')
    await user.should_see(marker='too-big', retries=40)
    await user.should_see('Too big to be published: its card would be')
    await user.should_see('Its description\'s pictures take the most room: remove some.')
    assert one(user, 'too-big').visible


def test_the_size_said_is_the_cards(tmp_path):
    from rpapp.ui.characters_page import CharactersSection
    store = CharacterStore(tmp_path)
    section = CharactersSection.__new__(CharactersSection)  # (only its store and lore books are used)
    section.store, section.lore_books = store, lore_book_store(tmp_path)
    character = new_character('Seraphina')
    store.set_picture(character, png((200, 10, 10)))
    one = store.images.add(png((1, 2, 3)))
    character.description = f'# Seraphina\n\n![a](picture:{one}) é ![again](picture:{one}) ![gone](picture:{"0" * 16}.webp)'
    character.summary = 'A guardian.'
    assert section.card_size(character) == len(section.export_card(character)[0])
    character.description = ''  # (the summary stands for it then)
    assert section.card_size(character) == len(section.export_card(character)[0])


async def test_the_cards_are_all_as_high(page, tmp_path):
    """Of the golden ratio's height whatever they hold (theme.py): the summary in a box of its own, cut in the
    browser; the title on two lines at most, in full as its tooltip."""
    from rpapp.conversations import ConversationStore
    from rpapp.personas import PersonaStore, new_persona
    user, store, sera, hale = page
    sera.summary = 'A guardian. ' * 40
    store.save(sera)
    mira = new_persona('Mira')
    PersonaStore(tmp_path).save(mira)
    ConversationStore(tmp_path).start(sera, mira)
    await user.open('/characters')
    await user.should_see(marker=f'row-{sera.id}')
    card, empty = one(user, f'row-{sera.id}'), one(user, f'row-{hale.id}')
    assert 'golden' in card.classes and 'golden' in empty.classes
    texts = [e for e in card.descendants() if 'grid-card-text' in e.classes]
    assert len(texts) == 1 and texts[0].text == sera.summary.strip()
    assert any('grid-card-body' in e.classes for e in empty.descendants())  # (even empty: the button stays down)
    [title] = [e for e in card.descendants() if 'grid-card-title' in e.classes]
    assert title.props['title'] == 'Seraphina'
    with user.scope(marker=f'row-{sera.id}'):
        await user.should_see('1 conversation')
    with user.scope(marker=f'row-{hale.id}'):
        await user.should_see('No conversations yet')

    await user.open('/personas')  # (theirs aren't: nothing under their picture but their names)
    await user.should_see(marker=f'row-{mira.id}')
    assert 'golden' not in one(user, f'row-{mira.id}').classes
