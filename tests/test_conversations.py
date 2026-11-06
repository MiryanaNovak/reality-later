"""Conversations: the model, the tree page, and starting a chat from a character card."""
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from nicegui import ui
from nicegui.testing import user_simulation
from PIL import Image

from rpapp.characters import CharacterStore, new_character
from rpapp.conversations import ConversationStore
from rpapp.personas import PersonaStore, new_persona
from rpapp.ui.app import build_root

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def png() -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (300, 300), (60, 64, 90)).save(out, 'PNG')
    return out.getvalue()


def started(store, character, persona, *, hours: float):
    """A conversation last updated `hours` after T0 (so tests control the order)."""
    conversation = store.start(character, persona)
    conversation.updated_at = T0 + timedelta(hours=hours)
    store.save(conversation)
    return conversation


# --- the model -------------------------------------------------------------------

def test_saved_file_and_lookups(tmp_path):
    store = ConversationStore(tmp_path)
    seraphina, kestrel, mira = new_character('Seraphina'), new_character('Kestrel'), new_persona('Mira')
    a = started(store, seraphina, mira, hours=1)
    b = started(store, seraphina, mira, hours=5)
    started(store, kestrel, mira, hours=3)

    data = json.loads(store.path_for(a.id).read_text(encoding='utf-8'))
    assert list(data) == ['id', 'title', 'format_version', 'character', 'persona', 'created_at', 'updated_at',
                          'messages']
    assert data['character'] == {'id': seraphina.id, 'title': 'Seraphina'} and data['title'].startswith('Chat of ')

    assert [c.id for c in store.using('character', seraphina.id)] == [b.id, a.id]  # most recent first
    assert store.count_by('character') == {seraphina.id: 2, kestrel.id: 1}
    assert store.count_by('persona') == {mira.id: 3}

    before = b.updated_at
    b.touch()
    assert b.updated_at > before


# --- the interface ---------------------------------------------------------------

@pytest.fixture
async def setup(tmp_path):
    stores = ConversationStore(tmp_path), CharacterStore(tmp_path), PersonaStore(tmp_path)
    async with user_simulation(build_root(tmp_path)) as user:
        yield (user, *stores)


def in_order(user, kind, prefix: str) -> list[str]:
    """Markers of elements of `kind`, in page order."""
    found = []
    for element in user.client.layout.descendants():
        if isinstance(element, kind):
            found += [m for m in element._markers if m.startswith(prefix)]
    return found


async def test_new_chat_from_a_character_card(setup):
    user, conversations, characters, personas = setup
    seraphina = new_character('Seraphina (forest guardian)')
    seraphina.description = 'Guardian of the glade.\nGentle, but fierce when needed.'
    characters.save(seraphina)
    mira = new_persona('Mira')
    personas.save(mira)

    await user.open('/characters')
    with user.scope(marker=f'row-{seraphina.id}'):
        await user.should_see('Guardian of the glade.\nGentle, but fierce when needed.')  # in full
        await user.should_see('No conversations yet')
        user.find(marker='new-chat').click()
    await user.should_see(marker='persona-picker')
    with user.scope(marker='persona-picker'):
        user.find(marker=f'link-{mira.id}').click()
    await user.should_see(marker='chat-input')

    [conversation] = conversations.list()
    assert (conversation.character.id, conversation.persona.id) == (seraphina.id, mira.id)
    await user.should_see('Seraphina (forest guardian), with you as Mira')


async def test_new_chat_without_personas(setup):
    user, conversations, characters, _ = setup
    seraphina = new_character('Seraphina')
    characters.save(seraphina)
    await user.open('/characters')
    user.find(marker='new-chat').click()
    await user.should_see('You need a persona first')
    with user.scope(marker='persona-picker'):
        user.find(kind=ui.button, content='Create a persona').click()
    await user.should_see('No personas yet')
    assert conversations.list() == []


async def test_tree_order_and_opening_a_chat(setup):
    user, conversations, characters, personas = setup
    seraphina, kestrel, hale = new_character('Seraphina'), new_character('Kestrel'), new_character('Hale')
    for c in (seraphina, kestrel, hale):
        characters.save(c)
    mira = new_persona('Mira')
    personas.save(mira)
    s_old = started(conversations, seraphina, mira, hours=1)
    k = started(conversations, kestrel, mira, hours=3)
    s_new = started(conversations, seraphina, mira, hours=5)  # the most recent: Seraphina first

    await user.open('/conversations')
    await user.should_see('Seraphina')
    assert in_order(user, ui.expansion, 'group-') == [f'group-{seraphina.id}', f'group-{kestrel.id}']
    assert in_order(user, ui.item, 'row-') == [f'row-{s_new.id}', f'row-{s_old.id}', f'row-{k.id}']
    await user.should_not_see('Hale')  # no conversation, so no group

    user.find(marker=f'row-{k.id}').click()
    await user.should_see(marker='chat-input')


async def test_conversation_count_link_focuses_the_character(setup):
    user, conversations, characters, personas = setup
    seraphina, kestrel = new_character('Seraphina'), new_character('Kestrel')
    characters.save(seraphina)
    characters.save(kestrel)
    mira = new_persona('Mira')
    personas.save(mira)
    started(conversations, seraphina, mira, hours=5)
    started(conversations, kestrel, mira, hours=1)
    started(conversations, kestrel, mira, hours=2)

    await user.open('/characters')
    with user.scope(marker=f'row-{kestrel.id}'):
        await user.should_see('2 conversations')
        user.find(marker='conversations-link').click()
    await user.should_see(marker=f'group-{kestrel.id}')
    groups = {m: e for e in user.find(ui.expansion).elements for m in e._markers if m.startswith('group-')}
    assert groups[f'group-{kestrel.id}'].value is True and 'tree-focus' in groups[f'group-{kestrel.id}'].classes
    assert groups[f'group-{seraphina.id}'].value is False


async def test_delete_and_missing_profiles(setup):
    user, conversations, characters, personas = setup
    seraphina = new_character('Seraphina')
    characters.save(seraphina)
    mira = new_persona('Mira')
    personas.save(mira)
    first = started(conversations, seraphina, mira, hours=1)
    second = started(conversations, seraphina, mira, hours=2)

    # Deleting a character used by conversations warns, and keeps the conversations.
    await user.open('/characters')
    user.find(marker='menu-delete').click()
    await user.should_see('It is used by 2 conversations. They will be kept, with this character shown as missing.')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Delete').click()
    await user.should_see('Deleted "Seraphina"')
    personas.delete(mira.id)

    await user.open(f'/conversations?character={seraphina.id}')
    await user.should_see('This character was deleted')
    await user.should_see('Persona "Mira" is missing')

    with user.scope(marker=f'row-{first.id}'):
        user.find(marker='menu-delete').click()
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Delete').click()
    await user.should_not_see(marker=f'row-{first.id}')
    assert [c.id for c in conversations.list()] == [second.id]


async def test_renamed_character_title_is_updated(setup):
    user, conversations, characters, personas = setup
    seraphina = new_character('Seraphina')
    characters.save(seraphina)
    mira = new_persona('Mira')
    personas.save(mira)
    conversation = started(conversations, seraphina, mira, hours=1)
    seraphina.title = 'Seraphina, guardian'
    characters.save(seraphina)

    await user.open('/conversations')
    await user.should_see('Seraphina, guardian')
    assert conversations.load(conversation.id).character.title == 'Seraphina, guardian'


async def test_missing_conversation(setup):
    user, *_ = setup
    await user.open('/conversations/nope')
    await user.should_see('Conversation not found')


async def test_rows_show_the_persona_picture(setup):
    user, conversations, characters, personas = setup
    seraphina = new_character('Seraphina')
    characters.save(seraphina)
    mira, wren = new_persona('Mira'), new_persona('Wren (sci-fi)')
    mira.image = personas.images.add(png())
    personas.save(mira)
    personas.save(wren)
    with_picture = started(conversations, seraphina, mira, hours=2)
    without = started(conversations, seraphina, wren, hours=1)

    await user.open(f'/conversations?character={seraphina.id}')
    with user.scope(marker=f'row-{with_picture.id}'):
        [image] = user.find(ui.image).elements
        assert image.source == f'/media/personas/{mira.image}'
    with user.scope(marker=f'row-{without.id}'):
        await user.should_see('WR')  # Wren's initials, not the conversation title's
        await user.should_not_see(kind=ui.image)


async def test_rename_from_the_list_and_the_chat(tmp_path):
    from datetime import timedelta

    from nicegui.testing import user_simulation

    from rpapp.characters import CharacterStore, new_character
    from rpapp.conversations import ConversationStore
    from rpapp.personas import PersonaStore, new_persona
    from rpapp.ui.app import build_root

    characters, personas = CharacterStore(tmp_path), PersonaStore(tmp_path)
    character, persona = new_character('Hale'), new_persona('Mira')
    character.first_messages = ['Ahoy.']
    characters.save(character)
    personas.save(persona)
    store = ConversationStore(tmp_path)
    conversation = store.start(character, persona)
    conversation.updated_at -= timedelta(days=2)
    store.save(conversation)
    updated = store.load(conversation.id).updated_at

    async with user_simulation(build_root(tmp_path)) as user:
        # From the list: the row's ⋮ menu
        await user.open('/conversations')
        user.find(marker=f'group-{character.id}').click()
        with user.scope(marker=f'row-{conversation.id}'):
            user.find(marker='menu-rename').click()
        await user.should_see(marker='name-dialog')
        field = user.find(marker='name-input').elements.pop()
        assert field.value == conversation.title  # starts from the current title
        field.set_value('   ')
        user.find(marker='name-save').click()
        assert 'error' in field.props  # an empty name isn't taken
        field.set_value('  The   storm  ')
        user.find(marker='name-save').click()
        await user.should_see('The storm')
        saved = store.load(conversation.id)
        assert saved.title == 'The storm' and saved.updated_at == updated  # keeps its place among recent chats

        # From the chat: the ⋮ menu in the top bar, and the title there follows
        await user.open(f'/conversations/{conversation.id}')
        with user.scope(marker='app-bar'):
            await user.should_see('The storm')
            user.find(marker='menu-rename').click()
        await user.should_see(marker='name-dialog')
        user.find(marker='name-input').elements.pop().set_value('The harbour')
        user.find(marker='name-input').trigger('keydown.enter')
        with user.scope(marker='app-bar'):
            await user.should_see('The harbour')
        assert store.load(conversation.id).title == 'The harbour'

        # Renamed elsewhere (another device) while the chat is open: the chat doesn't undo it when it saves
        store.rename(conversation.id, 'From the phone')
        user.find(marker='chat-input').type('Hello?')
        user.find(marker='send').click()
        await user.should_see('Hello?')
        assert store.load(conversation.id).title == 'From the phone'


def test_rename_needs_a_name(tmp_path):
    from rpapp.characters import new_character
    from rpapp.conversations import TITLE_MAX, ConversationStore
    from rpapp.personas import new_persona

    store = ConversationStore(tmp_path)
    conversation = store.start(new_character('Hale'), new_persona('Mira'))
    with pytest.raises(ValueError):
        store.rename(conversation.id, ' \n ')
    with pytest.raises(KeyError):
        store.rename('gone', 'Title')
    assert store.rename(conversation.id, 'x' * 500).title == 'x' * TITLE_MAX
