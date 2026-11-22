"""A character's page (/characters/<id>): to look at it, nothing to edit. Its picture, title, tags and summary
(not its name), its description as it shows, its conversations, New chat, and Edit, which opens its editor
(/characters/<id>/edit). A card opens it; the cards' ⋮ › Edit, the editor."""
import io
from datetime import datetime, timedelta, timezone

import pytest
from nicegui import ui
from nicegui.testing import user_simulation
from PIL import Image

from rpapp.characters import CharacterStore, new_character
from rpapp.conversations import ConversationStore
from rpapp.lorebooks import lore_book_store, new_lore_book
from rpapp.personas import PersonaStore, new_persona
from rpapp.ui.app import build_root


def png() -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (300, 200), (30, 90, 60)).save(out, 'PNG')
    return out.getvalue()


def one(user, marker):
    """The element with this marker, shown or not."""
    [element] = [e for e in user.client.layout.descendants() if marker in e._markers]
    return element


@pytest.fixture
async def page(tmp_path):
    characters, personas, conversations = CharacterStore(tmp_path), PersonaStore(tmp_path), ConversationStore(tmp_path)
    sera = new_character('Seraphina, guardian')
    sera.name = 'Sera the Hidden Name'
    sera.summary = 'A guardian.\nKind, but fierce.'
    sera.tags = ['fantasy', 'forest']
    sera.description = f'# Her story\n\nShe **waits**.\n\n![glade](picture:{characters.images.add(png())})'
    characters.set_picture(sera, png())
    characters.save(sera)
    hale = new_character('Hale')
    characters.save(hale)
    mira, nell = new_persona('Mira'), new_persona('Nell')
    personas.save(mira)
    personas.save(nell)
    async with user_simulation(build_root(tmp_path)) as user:
        yield user, characters, conversations, sera, hale, (mira, nell)


async def test_what_it_shows(page):
    user, characters, conversations, sera, hale, _ = page
    await user.open(f'/characters/{sera.id}')
    await user.should_see(marker='character-identity')
    assert one(user, 'character-title').text == 'Seraphina, guardian'
    assert [e.text for e in one(user, 'character-tags').descendants() if isinstance(e, ui.label)] == \
        ['fantasy', 'forest']
    assert one(user, 'character-summary').text == 'A guardian.\nKind, but fierce.'
    await user.should_not_see('Sera the Hidden Name')  # (its name is the AI's: not shown here)
    html = one(user, 'character-description').props['innerHTML']
    assert '<h1>Her story</h1>' in html and '<strong>waits</strong>' in html and 'src="/media/characters/' in html
    fields = [e for e in user.client.layout.descendants() if isinstance(e, (ui.input, ui.textarea)) and e.visible]
    assert not fields  # (nothing to edit)
    await user.should_see('No conversations yet')

    user.find(marker='edit-character').click()  # (its editor)
    await user.should_see('Character. Changes save automatically.')
    await user.should_see(marker='part-summary')
    user.find(marker='bar-back').click()  # (back: its page)
    await user.should_see(marker='character-identity')

    await user.open(f'/characters/{hale.id}')  # (none of it yet)
    await user.should_see('No summary yet.')
    await user.should_see(marker='no-description')
    await user.should_not_see(marker='character-tags')

    await user.open('/characters/no-such-character-1a2b3c')
    await user.should_see('Character not found')


async def test_a_card_opens_it_and_edit_opens_the_editor(page):
    user, characters, conversations, sera, hale, _ = page
    await user.open('/characters')
    await user.should_see(marker=f'row-{sera.id}')
    user.find(marker=f'row-{sera.id}').click()
    await user.should_see(marker='character-identity')
    assert one(user, 'character-title').text == 'Seraphina, guardian'

    await user.open('/characters')
    with user.scope(marker=f'row-{sera.id}'):
        user.find(marker='menu-edit').click()
    await user.should_see('Character. Changes save automatically.')

    await user.open('/characters')  # (a new one: straight to its editor, to fill it)
    user.find('New').click()
    with user.scope(marker='new-dialog'):
        user.find(ui.input).type('Wren')
        user.find(kind=ui.button, content='Create').click()
    await user.should_see('Character. Changes save automatically.')

    user.find(marker='bar-menu').click()  # (a copy: its editor too)
    user.find(marker='menu-duplicate').click()
    await user.should_see('Duplicated as "Wren (copy)"')
    await user.should_see('Character. Changes save automatically.')


async def test_its_conversations(page):
    user, characters, conversations, sera, hale, (mira, nell) = page
    older, newer = conversations.start(sera, mira), conversations.start(sera, nell)
    older.updated_at = datetime.now(timezone.utc) - timedelta(days=2)
    conversations.save(older)
    conversations.rename(newer.id, 'The storm')
    elsewhere = conversations.start(hale, mira)  # (another character's: not here)

    await user.open(f'/characters/{sera.id}')
    await user.should_see(marker=f'row-{newer.id}')
    rows = [m for e in user.client.layout.descendants() for m in e._markers if m.startswith('row-')]
    assert rows == [f'row-{newer.id}', f'row-{older.id}']  # (the latest first)
    await user.should_not_see(marker=f'row-{elsewhere.id}')
    with user.scope(marker=f'row-{newer.id}'):
        await user.should_see('The storm')
        await user.should_see('As Nell.')

    with user.scope(marker=f'row-{older.id}'):  # (its ⋮: as on the Conversations page)
        user.find(marker='menu-rename').click()
    await user.should_see(marker='name-dialog')
    one(user, 'name-input').set_value('At the glade')
    user.find(marker='name-save').click()
    await user.should_not_see(marker='name-dialog')  # (its field said so too: the list, redrawn)
    with user.scope(marker=f'row-{older.id}'):
        await user.should_see('At the glade')
    with user.scope(marker=f'row-{older.id}'):
        user.find(marker='menu-delete').click()
    await user.should_see(marker='confirm-dialog')
    user.find(kind=ui.button, content='Delete').click()
    await user.should_not_see(marker=f'row-{older.id}')
    assert conversations.get(older.id) is None

    user.find(marker=f'row-{newer.id}').click()  # (opens the chat)
    await user.should_see(marker='chat-input')


async def test_new_chat(page):
    user, characters, conversations, sera, hale, (mira, nell) = page
    await user.open(f'/characters/{sera.id}')
    user.find(marker='new-chat').click()
    await user.should_see(marker='persona-picker')
    user.find(marker=f'link-{mira.id}').click()
    await user.should_see(marker='chat-settings-dialog')  # (what it uses, before it starts)
    await user.should_see(f'New chat with {sera.title}')
    user.find(marker='chat-settings-cancel').click()  # (cancelled: no chat)
    await user.should_not_see(marker='chat-settings-dialog')
    assert conversations.list() == []

    user.find(marker='new-chat').click()
    await user.should_see(marker='persona-picker')
    user.find(marker=f'link-{mira.id}').click()
    await user.should_see(marker='chat-settings-dialog')  # (what it uses, before it starts)
    user.find(marker='chat-settings-start').click()
    await user.should_see(marker='chat-input')
    [conversation] = conversations.list()
    assert (conversation.character.id, conversation.persona.id) == (sera.id, mira.id)


async def test_links_to_a_character(page, tmp_path):
    """From a lore book (who uses it): its editor, where lore books are linked. (The chat settings' "Their page",
    to its editor too: test_chat_settings_ui.py; a shared card's, to its page: test_downloading_ui.py.)"""
    user, characters, conversations, sera, hale, _ = page
    book = new_lore_book('Eldoria')
    lore_book_store(tmp_path).save(book)
    sera.add_lorebook(book.id, book.name)
    characters.save(sera)
    await user.open(f'/lorebooks/{book.id}')
    await user.should_see(marker=f'link-{sera.id}')
    user.find(marker=f'link-{sera.id}').click()
    await user.should_see('Character. Changes save automatically.')
