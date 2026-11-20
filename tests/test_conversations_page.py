"""The Conversations page: grouped by character or by persona (kept for the device), and its search."""
import asyncio

from nicegui import ui
from test_conversations import in_order, png, setup, started  # noqa: F401  (the fixture)

from rpapp.characters import new_character
from rpapp.personas import new_persona


def one(user, marker):
    """The element with this marker, shown or not."""
    [element] = [e for e in user.client.layout.descendants() if marker in e._markers]
    return element


async def typed(search, text: str) -> None:
    search.set_value(text)
    await asyncio.sleep(0.05)  # (the list is redrawn in the background)


def cast(conversations, characters, personas):
    """Seraphina (with a picture) and Kestrel; Mira and Wren. Seraphina's chats are the most recent ones."""
    seraphina, kestrel = new_character('Seraphina'), new_character('Kestrel')
    seraphina.image = characters.images.add(png())
    mira, wren = new_persona('Mira'), new_persona('Wren')
    for profile, store in ((seraphina, characters), (kestrel, characters), (mira, personas), (wren, personas)):
        store.save(profile)
    chats = {
        'k-mira': started(conversations, kestrel, mira, hours=1),
        's-wren': started(conversations, seraphina, wren, hours=2),
        's-mira': started(conversations, seraphina, mira, hours=3),
    }
    return (seraphina, kestrel), (mira, wren), chats


async def test_by_character_or_by_persona(setup, monkeypatch):  # noqa: F811
    user, conversations, characters, personas = setup
    (seraphina, kestrel), (mira, wren), chats = cast(conversations, characters, personas)
    import rpapp.ui.opened as opened
    scripts = []
    monkeypatch.setattr(opened.ui, 'run_javascript', lambda code, **_: scripts.append(code))

    await user.open('/conversations')
    await user.should_see(marker='conversations-by')
    assert one(user, 'conversations-by').value == 'character'  # (as before, by default)
    assert in_order(user, ui.expansion, 'group-') == [f'group-{seraphina.id}', f'group-{kestrel.id}']
    with user.scope(marker=f'row-{chats["s-wren"].id}'):
        await user.should_see('As Wren.')
        await user.should_see('WR')  # (Wren's initials: no picture)

    one(user, 'conversations-by').set_value('persona')
    await user.should_see(marker=f'group-{wren.id}')
    assert in_order(user, ui.expansion, 'group-') == [f'group-{mira.id}', f'group-{wren.id}']  # (latest first)
    with user.scope(marker=f'group-{mira.id}'):
        await user.should_see('Mira')
        await user.should_see('2 conversations')
    assert in_order(user, ui.item, 'row-') == [f'row-{chats["s-mira"].id}', f'row-{chats["k-mira"].id}',
                                               f'row-{chats["s-wren"].id}']
    with user.scope(marker=f'row-{chats["s-mira"].id}'):
        await user.should_see('With Seraphina.')
        [image] = user.find(ui.image).elements
        assert image.source == f'/media/characters/{seraphina.image}'  # (the character's picture)
    with user.scope(marker=f'row-{chats["k-mira"].id}'):
        await user.should_see('With Kestrel.')
        await user.should_see('KE')

    assert any('document.cookie = "rp_conversations_by=persona; path=/;' in code for code in scripts)  # (the device)

    user.find(marker=f'row-{chats["s-wren"].id}').click()  # (kept on this tab: to a chat and back)
    await user.should_see(marker='chat-input')
    user.find(marker='bar-back').click()
    await user.should_see(marker=f'group-{wren.id}')
    assert one(user, 'conversations-by').value == 'persona'


async def test_the_device_keeps_it(setup):  # noqa: F811
    user, conversations, characters, personas = setup
    (seraphina, _), (mira, wren), chats = cast(conversations, characters, personas)
    user.http_client.cookies.set('rp_conversations_by', 'persona')
    await user.open('/conversations')
    await user.should_see(marker=f'group-{mira.id}')
    assert one(user, 'conversations-by').value == 'persona'
    user.http_client.cookies.set('rp_conversations_by', 'nonsense')
    await user.open('/conversations')
    await user.should_see(marker=f'group-{seraphina.id}')  # (anything else: by character)


async def test_the_group_to_open(setup):  # noqa: F811
    """From a chat, back: its character's group, or its persona's, as they're grouped."""
    user, conversations, characters, personas = setup
    (seraphina, kestrel), (mira, wren), chats = cast(conversations, characters, personas)
    await user.open(f'/conversations/{chats["k-mira"].id}')
    await user.should_see(marker='chat-input')
    user.find(marker='bar-back').click()
    await user.should_see(marker=f'group-{kestrel.id}')
    assert one(user, f'group-{kestrel.id}').value is True and 'tree-focus' in one(user, f'group-{kestrel.id}').classes
    assert one(user, f'group-{seraphina.id}').value is False

    one(user, 'conversations-by').set_value('persona')
    await user.should_see(marker=f'group-{mira.id}')
    assert one(user, f'group-{mira.id}').value is True and 'tree-focus' in one(user, f'group-{mira.id}').classes
    assert one(user, f'group-{wren.id}').value is False

    user.http_client.cookies.set('rp_conversations_by', 'persona')
    await user.open(f'/conversations?character={kestrel.id}')  # (a character's link, grouped by persona)
    await user.should_see(marker=f'group-{mira.id}')
    assert one(user, f'group-{mira.id}').value is False


async def test_missing_profiles_by_persona(setup):  # noqa: F811
    user, conversations, characters, personas = setup
    (seraphina, _), (mira, wren), chats = cast(conversations, characters, personas)
    characters.delete(seraphina.id)
    personas.delete(wren.id)
    user.http_client.cookies.set('rp_conversations_by', 'persona')
    await user.open(f'/conversations?persona={wren.id}')
    with user.scope(marker=f'group-{wren.id}'):
        await user.should_see('Wren')
        await user.should_see('This persona was deleted')
        await user.should_see('Character "Seraphina" is missing')


async def test_search(setup):  # noqa: F811
    user, conversations, characters, personas = setup
    (seraphina, kestrel), (mira, wren), chats = cast(conversations, characters, personas)
    await user.open('/conversations')
    assert not one(user, 'conversation-search').visible  # (from 8 conversations on, as on the Characters page)

    for hours in range(4, 9):
        started(conversations, kestrel, wren, hours=hours)
    conversations.rename(chats['s-mira'].id, 'The storm at the ford')
    await user.open('/conversations')
    search = one(user, 'conversation-search')
    assert search.visible
    one(user, f'group-{kestrel.id}').set_value(True)  # (opened by hand: kept once the search is emptied)

    await typed(search, 'storm')  # (its title)
    await user.should_see('The storm at the ford')
    assert in_order(user, ui.item, 'row-') == [f'row-{chats["s-mira"].id}']
    assert in_order(user, ui.expansion, 'group-') == [f'group-{seraphina.id}']
    assert one(user, f'group-{seraphina.id}').value is True  # (open, to see it)
    with user.scope(marker=f'group-{seraphina.id}'):
        await user.should_see('1 of 2 conversations')

    await typed(search, 'MIRA')  # (its persona, whatever the case)
    await user.should_see(marker=f'group-{kestrel.id}')
    assert in_order(user, ui.item, 'row-') == [f'row-{chats["k-mira"].id}', f'row-{chats["s-mira"].id}']

    one(user, 'conversations-by').set_value('persona')  # (the search stays, by persona)
    await user.should_see(marker=f'group-{mira.id}')
    assert in_order(user, ui.expansion, 'group-') == [f'group-{mira.id}']
    with user.scope(marker=f'group-{mira.id}'):
        await user.should_see('2 conversations, last updated')  # (all of the group's: as it is)

    await typed(search, 'kestrel')  # (its character)
    await user.should_see(marker=f'group-{wren.id}')
    with user.scope(marker=f'group-{wren.id}'):
        await user.should_see('5 of 6 conversations')

    await typed(search, 'nobody')
    await user.should_see('Nothing matches “nobody”')

    one(user, 'conversations-by').set_value('character')
    await typed(search, '')  # (emptied: the groups as they were)
    await user.should_see(marker=f'group-{seraphina.id}')
    assert one(user, f'group-{kestrel.id}').value is True and one(user, f'group-{seraphina.id}').value is False
