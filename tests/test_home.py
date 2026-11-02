"""The Home page: tips with Dismiss, recent conversations, recently added characters."""
from datetime import datetime, timedelta, timezone

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

from rpapp.access import Access
from rpapp.characters import CharacterStore, new_character
from rpapp.connections import OpenRouterConnection, connection_store
from rpapp.conversations import ConversationStore
from rpapp.devices import DeviceRegistry
from rpapp.home import HomeStore
from rpapp.personas import PersonaStore, new_persona
from rpapp.settings import ConnectionRef, SettingsStore
from rpapp.ui.app import build_root

PHONE = 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36'


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr('rpapp.launcher.network_addresses', lambda listen: ['192.168.1.20'])
    access = Access(DeviceRegistry(tmp_path))
    return tmp_path, access


def tips(user) -> list[str]:
    return sorted(m.removeprefix('tip-') for e in user.find(marker='home-tips').elements
                  for child in e.descendants() for m in child._markers if m.startswith('tip-')
                  and m not in ('tip-action', 'tip-dismiss'))


async def test_tips_follow_what_is_missing(home):
    tmp_path, access = home
    async with user_simulation(build_root(tmp_path, access=access)) as user:
        await user.open('/')
        await user.should_see(marker='home-tips')
        assert tips(user) == ['create-connection', 'pair-device']

        conn = OpenRouterConnection(name='DeepSeek', model='deepseek/deepseek-chat')
        connection_store(tmp_path).save(conn)
        await user.open('/')
        await user.should_see(marker='tip-choose-connection')
        assert tips(user) == ['choose-connection', 'pair-device']  # one at a time: create, then choose

        settings = SettingsStore(tmp_path)
        chosen = settings.load()
        chosen.chat.connection = ConnectionRef(id=conn.id, name=conn.name)
        chosen.memory.connection = ConnectionRef(id='gone', name='Old one')  # deleted since
        settings.save(chosen)
        await user.open('/')
        await user.should_see('The connection chosen for memories, "Old one", was deleted')

        chosen.memory.connection = None  # same as the chat's
        settings.save(chosen)
        link = access.devices.new_link()
        access.devices.use_link(link.code, user_agent=PHONE, address='192.168.1.33')
        await user.open('/')
        await user.should_not_see(marker='home-tips')  # all set

        removed = access.devices.devices[0]
        access.devices.remove(removed.id)
        await user.open('/')
        await user.should_not_see(marker='tip-pair-device')  # a device was paired once: no tip again
        assert DeviceRegistry(tmp_path).ever_paired  # remembered in the file too


async def test_dismissed_tips_stay_dismissed(home):
    tmp_path, access = home
    async with user_simulation(build_root(tmp_path, access=access)) as user:
        await user.open('/')
        with user.scope(marker='tip-create-connection'):
            user.find(marker='tip-dismiss').click()
        await user.should_not_see(marker='tip-create-connection')
        assert HomeStore(tmp_path).dismissed() == {'create-connection'}
        await user.open('/')
        await user.should_see(marker='tip-pair-device')
        await user.should_not_see(marker='tip-create-connection')  # after a reload too

        user.find(marker='show-tips').click()  # changed your mind
        await user.should_see(marker='tip-create-connection')
        assert HomeStore(tmp_path).dismissed() == set()

        with user.scope(marker='tip-create-connection'):
            user.find(marker='tip-action').click()
        await user.should_see('No connections yet')


async def test_no_pairing_tip_without_a_network_or_pairing(tmp_path, monkeypatch):
    monkeypatch.setattr('rpapp.launcher.network_addresses', lambda listen: [])  # listening on 127.0.0.1
    async with user_simulation(build_root(tmp_path, access=Access(DeviceRegistry(tmp_path)))) as user:
        await user.open('/')
        await user.should_see(marker='tip-create-connection')
        await user.should_not_see(marker='tip-pair-device')
    async with user_simulation(build_root(tmp_path, access=Access(DeviceRegistry(tmp_path), enabled=False))) as user:
        await user.open('/')
        await user.should_not_see(marker='tip-pair-device')


async def test_recent_conversations_and_characters(home):
    tmp_path, access = home
    characters, personas, conversations = CharacterStore(tmp_path), PersonaStore(tmp_path), ConversationStore(tmp_path)
    mira = new_persona('Mira, wandering healer')
    mira.name = 'Mira'
    personas.save(mira)
    cast = []
    for i, title in enumerate(['Seraphina', 'Hale', 'Wren']):
        character = new_character(title)
        character.created_at = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(days=i)
        character.description = f'About {title}.'
        characters.save(character)
        cast.append(character)
    old = conversations.start(cast[0], mira)
    old.add('user', '*Waves.* Hello?')
    conversations.save(old)
    new = conversations.start(cast[1], mira)
    new.add('assistant', '**Ahoy**, stowaway.')
    conversations.save(new)
    conversations.rename(new.id, 'The storm')

    async with user_simulation(build_root(tmp_path, access=access)) as user:
        await user.open('/')
        await user.should_see(marker='recent-conversations')
        cards = [m for e in user.find(marker='recent-conversations').elements for c in e.descendants()
                 for m in c._markers if m.startswith('conversation-')]
        assert cards == [f'conversation-{new.id}', f'conversation-{old.id}']  # the latest first
        with user.scope(marker=f'conversation-{new.id}'):
            await user.should_see('The storm')  # its title, renamed
            await user.should_see('Hale')       # who's in it
            await user.should_see('As Mira, wandering healer')
            await user.should_not_see('stowaway')  # not its last message
        with user.scope(marker=f'conversation-{old.id}'):
            await user.should_see(old.title)  # (the date and time it started, until renamed)
        await user.should_see('Recent characters')

        cards = [m for e in user.find(marker='recent-characters').elements for c in e.descendants()
                 for m in c._markers if m.startswith('row-')]
        assert cards == [f'row-{c.id}' for c in reversed(cast)]  # the last added first
        await user.should_see('About Wren.')  # as on the Characters page

        with user.scope(marker=f'conversation-{new.id}'):
            user.find(marker='continue').click()
        await user.should_see(marker='chat-input')

        await user.open('/')
        user.find(marker='view-all-characters').click()
        await user.should_see('Import')  # the Characters page's button
        await user.open('/')
        user.find(marker='view-all-conversations').click()
        await user.should_see(marker=f'row-{new.id}')


async def test_the_app_name_is_the_title(home):
    from rpapp import APP_NAME, TAGLINE
    tmp_path, access = home
    async with user_simulation(build_root(tmp_path, access=access)) as user:
        await user.open('/')
        with user.scope(marker='app-bar'):
            await user.should_see(APP_NAME)
            await user.should_see(TAGLINE)
            await user.should_not_see('Home')
    assert (APP_NAME, TAGLINE) == ('Reality Later', 'The real world can wait.')


async def test_empty_home(home):
    tmp_path, access = home
    async with user_simulation(build_root(tmp_path, access=access)) as user:
        await user.open('/')
        await user.should_see(marker='no-conversations')
        await user.should_see('Add a character first')
        await user.should_see(marker='no-characters')


def test_duplicates_count_as_added_now(tmp_path):
    store = CharacterStore(tmp_path)
    original = new_character('Hale')
    original.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    store.save(original)
    copy = store.duplicate(original)
    assert copy.created_at > original.created_at
    legacy = new_character('Old')
    legacy.created_at = None  # saved before the app recorded it
    store.save(legacy)
    assert store.added_at(legacy) > datetime(2025, 1, 1, tzinfo=timezone.utc)  # its file's time
