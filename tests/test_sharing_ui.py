"""The shared cards pages (rpapp/ui/sharing_page.py), with a relay of the tests' own (fake_relay.py)."""
import asyncio
import time

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

pytest.importorskip('coincurve', reason='coincurve (sharing cards) is installed for Python 3.13 and earlier')

from fake_relay import FakeRelay  # noqa: E402

from rpapp.publishing import announcements as fmt  # noqa: E402
from rpapp.publishing import identity, nostr, torrents  # noqa: E402
from rpapp.publishing.announcements import PublicationDraft  # noqa: E402
from rpapp.publishing.catalog import Catalog  # noqa: E402
from rpapp.settings import SettingsStore  # noqa: E402
from rpapp.ui.app import build_root  # noqa: E402

MARIE, JUNO = nostr.new_secret(), nostr.new_secret()
T0 = 1767225600
SLOW = 50  # (should_see's tries, 0.1 s apart: for what comes from the relay)


def card(secret, title, *, at, uuid=None, name='Marie', rating='sfw', tags=(), description='', content=None):
    return fmt.build_publication(
        PublicationDraft(publisher_name=name, card_name=title, card_description=description, card_tags=tags,
                         content_rating=rating, language='en'),
        torrents.card_file(content or f'{title} {at}'.encode()), card_uuid=uuid or fmt.new_card_uuid(),
        secret=secret, created_at=at)


ARIA_UUID = fmt.new_card_uuid()
ARIA_1 = card(MARIE, 'Aria the Wanderer', at=T0, uuid=ARIA_UUID, tags=('fantasy', 'travel'),
              description='A wandering bard.\nShe sings of old roads.')
ARIA_2 = card(MARIE, 'Aria the Wanderer', at=T0 + 3600, uuid=ARIA_UUID, name='Marie L.', tags=('fantasy', 'travel'),
              description='A wandering bard, now with a lute.')
BRAM = card(MARIE, 'Bram after Dark', at=T0 + 100, rating='nsfw', tags=('romance',))
CASS = card(JUNO, 'Cass of the Station', at=T0 + 200, name='Juno', tags=('sci-fi', 'futuristic'))
ALL = [ARIA_1, ARIA_2, BRAM, CASS]


@pytest.fixture
async def relay():
    async with FakeRelay() as fake:
        fake.add(*(p.event.to_dict() for p in ALL))
        yield fake


@pytest.fixture
async def user(tmp_path, relay):
    async with user_simulation(build_root(tmp_path, sharing_relays=[relay.url])) as user:
        yield user


async def wait_for(condition, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError('never happened')


# --- the catalog ------------------------------------------------------------------------------------

async def test_looking_when_opened_and_what_shows(user):
    await user.open('/')
    user.find(marker='nav-shared-cards').click()
    await user.should_see('Aria the Wanderer', retries=SLOW)       # (found on the relay as the page opened)
    await user.should_see('Cass of the Station', retries=SLOW)
    await user.should_see('A wandering bard, now with a lute.', retries=SLOW)  # (its latest version only)
    await user.should_not_see('Bram after Dark')     # (NSFW: hidden by default)
    await user.should_see('Marie L.', retries=SLOW)                # (the current name)
    await user.should_see('4 new publications', retries=SLOW)  # (Bram's too, and Aria's first version)


async def test_nsfw_cards_when_asked(user, tmp_path):
    await user.open('/shared')
    await user.should_see('Aria the Wanderer', retries=SLOW)
    user.find(marker='shared-nsfw').elements.pop().set_value(True)
    await user.should_see('Bram after Dark', retries=SLOW)
    assert SettingsStore(tmp_path).load().sharing.show_nsfw  # (remembered)


async def test_searching_and_tags(user):
    await user.open('/shared')
    await user.should_see('Cass of the Station', retries=SLOW)
    user.find(marker='shared-search').type('station')
    await user.should_see('Cass of the Station', retries=SLOW)
    await user.should_not_see('Aria the Wanderer')
    await user.open('/shared?tag=fantasy')
    await user.should_see('Aria the Wanderer', retries=SLOW)
    await user.should_not_see('Cass of the Station')


def value(user, marker):
    return user.find(marker=marker).elements.pop().value


def set_value(user, marker, new):
    user.find(marker=marker).elements.pop().set_value(new)


async def test_words_from_their_start(user):
    await user.open('/shared')
    await user.should_see('Aria the Wanderer', retries=SLOW)
    search = user.find(marker='shared-search')
    search.type('wand')
    await user.should_see('Aria the Wanderer', retries=SLOW)
    await user.should_not_see('Cass of the Station')
    search.clear().type('ander')  # (not at the start of a word)
    await user.should_see('No card matches', retries=SLOW)
    search.clear().type('lute')  # (in the description: when asked)
    await user.should_see('No card matches', retries=SLOW)
    set_value(user, 'shared-where', 'all')
    await user.should_see('Aria the Wanderer', retries=SLOW)
    assert user.find(marker='shared-filters').elements.pop().props['caption'] == ''  # (a switch: not a filter)


async def test_by_publisher_and_excluded_tags(user):
    await user.open('/shared')
    await user.should_see('Cass of the Station', retries=SLOW)
    set_value(user, 'shared-by', 'jun')  # (their name: from its start)
    await user.should_see('Cass of the Station', retries=SLOW)
    await user.should_not_see('Aria the Wanderer')
    await user.should_see(f'Juno ({nostr.to_npub(CASS.publisher_id)[:10]}', retries=SLOW)  # (and their key)
    set_value(user, 'shared-by', 'marie')  # (a name before: "Marie L." now)
    await user.should_see('Aria the Wanderer', retries=SLOW)
    await user.should_not_see('Cass of the Station')
    set_value(user, 'shared-by', '')
    set_value(user, 'shared-exclude', ['Sci-Fi'])
    await user.should_see('Aria the Wanderer', retries=SLOW)
    await user.should_not_see('Cass of the Station')


async def test_filters_from_the_address(user):
    await user.open('/shared?q=wand&where=all&tags=fantasy,travel&exclude=romance&by=mar')
    await user.should_see('Aria the Wanderer', retries=SLOW)
    assert (value(user, 'shared-search'), value(user, 'shared-where'), value(user, 'shared-by')) == \
        ('wand', 'all', 'mar')
    assert value(user, 'shared-tags') == ['fantasy', 'travel'] and value(user, 'shared-exclude') == ['romance']
    filters = user.find(marker='shared-filters').elements.pop()
    assert filters.value and filters.props['caption'] == '3 filters on'  # (opened: some are on)


async def test_what_counts_as_a_filter(user):
    """The fields filled in: the publisher, the tags, those excluded. Not the switches (where the words are
    searched, NSFW shown). What each field takes: said in it, when it's clicked."""
    await user.open('/shared')
    await user.should_see('Aria the Wanderer', retries=SLOW)
    filters = user.find(marker='shared-filters').elements.pop()
    set_value(user, 'shared-nsfw', True)
    set_value(user, 'shared-where', 'all')
    await user.should_see('Bram after Dark', retries=SLOW)  # (NSFW, shown)
    assert filters.props['caption'] == ''
    set_value(user, 'shared-tags', ['fantasy'])
    await user.should_not_see('Cass of the Station', retries=SLOW)
    assert filters.props['caption'] == '1 filter on'
    set_value(user, 'shared-exclude', ['travel'])
    set_value(user, 'shared-by', 'mar')
    await user.should_see('No card matches', retries=SLOW)
    assert filters.props['caption'] == '3 filters on'

    for marker, text in (('shared-tags', 'Cards with any of these'), ('shared-exclude', 'Cards with none of these'),
                         ('shared-by', 'Their name, now or before')):
        field = user.find(marker=marker).elements.pop()
        assert text in (field.props.get('placeholder'), getattr(field, 'placeholder', None))
    labels = [e.text for e in user.find(ui.label).elements]
    assert not any(t.startswith('Cards with') for t in labels)  # (not under the fields)


async def test_yours_apart(user, tmp_path):
    key_file = identity.key_path(tmp_path)  # (the app's key: Marie's)
    identity.import_identity(key_file, nostr.to_nsec(MARIE))
    await user.open('/shared')
    await user.should_see('Cass of the Station', retries=SLOW)
    await user.should_not_see('Aria the Wanderer')  # (yours: not among the others')

    await user.open('/published')
    await user.should_see(marker='published-list', retries=SLOW)
    await user.should_see('Aria the Wanderer', retries=SLOW)
    await user.should_see('Bram after Dark', retries=SLOW)  # (NSFW: yours all the same)
    await user.should_see('Its file isn\'t shared from this computer, not sent to relays from this computer',
                          retries=SLOW)
    await user.should_not_see('Cass of the Station')

    withdrawal = fmt.build_withdrawal([BRAM.event_id], secret=MARIE, reason='Too dark.')
    with Catalog(tmp_path / 'sharing' / 'catalog.sqlite3') as catalog:
        catalog.receive(withdrawal.event.to_dict())
    identity.import_identity(key_file, nostr.to_nsec(nostr.new_secret()), replace=True)  # (another key since)
    await user.open('/published')
    await user.should_see(marker='published-withdrawn', retries=SLOW)
    await user.should_see('Withdrawn just now: Too dark.', retries=SLOW)
    await user.should_see('with a key you used before', retries=SLOW)  # (Marie's: still yours)

    # Yours: on their own page. Gone to from within the app (as by "All their cards"): opened afresh, NiceGUI's
    # simulated user can send the page's redirect to the page before, when the new one is built before it's told
    # it's on it (its navigate goes to "the user's page", not the page that asks, as the app's own does)
    await user.open('/shared')
    await user.should_see(marker='shared-search', retries=SLOW)
    user.navigate.to(f'/shared?publisher={ARIA_2.publisher_id}')
    await user.should_see(marker='published-list', retries=SLOW)
    await user.open(f'/shared/{ARIA_2.event_id}')
    await user.should_see('Published with a key you used before', retries=SLOW)
    await user.should_see(marker='own-withdraw', retries=SLOW)
    await user.should_not_see(marker='menu-block-this-publisher')


async def test_nothing_published(user):
    await user.open('/')
    user.find(marker='nav-your-publications').click()
    await user.should_see('Nothing published yet', retries=SLOW)


async def test_hidden_tags(user, tmp_path):
    store = SettingsStore(tmp_path)
    settings = store.load()
    settings.sharing.hidden_tags = ['Futuristic']
    store.save(settings)
    await user.open('/shared')
    await user.should_see('Aria the Wanderer', retries=SLOW)
    await user.should_not_see('Cass of the Station')


async def test_looking_again(user, relay):
    await user.open('/shared')
    await user.should_see('Aria the Wanderer', retries=SLOW)
    relay.add(card(JUNO, 'Dove of the Harbour', at=int(time.time()), name='Juno').event.to_dict())  # (new)
    user.find(marker='shared-look').click()
    await user.should_see('Dove of the Harbour', retries=SLOW)


async def test_pages_of_cards(tmp_path):
    many = [card(JUNO, f'Card number {i:02}', at=T0 + i, name='Juno') for i in range(60)]
    async with FakeRelay() as relay:
        relay.add(*(p.event.to_dict() for p in many))
        async with user_simulation(build_root(tmp_path, sharing_relays=[relay.url])) as user:
            await user.open('/shared')
            await user.should_see('Card number 59', retries=SLOW)
            await user.should_not_see('Card number 05')  # (the first 50: the newest)
            user.find(marker='shared-more').click()
            await user.should_see('Card number 05', retries=SLOW)


async def test_no_relays(tmp_path):
    async with user_simulation(build_root(tmp_path, sharing_relays=())) as user:
        await user.open('/shared')
        await user.should_see('No shared cards yet', retries=SLOW)
        await user.should_see('No relays to look on', retries=SLOW)


# --- one card ---------------------------------------------------------------------------------------

async def test_a_cards_page(user):
    await user.open('/shared')
    await user.should_see('Aria the Wanderer', retries=SLOW)
    user.find(marker=f'row-{ARIA_2.event_id}').click()
    await user.should_see('A wandering bard, now with a lute.', retries=SLOW)
    await user.should_see('Marie L.', retries=SLOW)
    await user.should_see('Also known as: Marie', retries=SLOW)
    await user.should_see(nostr.to_npub(ARIA_2.publisher_id), retries=SLOW)
    await user.should_see('Character card V2', retries=SLOW)
    await user.should_see(marker='shared-versions', retries=SLOW)
    await user.should_see('(this one)', retries=SLOW)

    user.find(marker=f'shared-version-{ARIA_1.event_id}').click()  # the older version
    await user.should_see('An older version of this card.', retries=SLOW)
    await user.should_see('She sings of old roads.', retries=SLOW)
    await user.should_see('Published this as "Marie".', retries=SLOW)
    user.find(marker='shared-tag-fantasy').click()
    await user.should_see(marker='shared-tags', retries=SLOW)


async def test_a_withdrawn_card(user, relay, tmp_path):
    withdrawal = fmt.build_withdrawal([CASS.event_id], secret=JUNO, reason='Published by mistake.', created_at=T0 + 999)
    relay.add(withdrawal.event.to_dict())
    await user.open('/shared')
    await user.should_see('Aria the Wanderer', retries=SLOW)
    await user.should_not_see('Cass of the Station')
    await user.open(f'/shared/{CASS.event_id}')  # (for those who have it)
    await user.should_see('Withdrawn by its publisher', retries=SLOW)
    await user.should_see('Their reason: Published by mistake.', retries=SLOW)


async def test_an_unknown_card(user):
    await user.open('/shared/' + '0' * 64)
    await user.should_see('Shared card not found', retries=SLOW)


async def test_blocking_and_unblocking(user, tmp_path):
    await user.open('/shared')
    await user.should_see('Cass of the Station', retries=SLOW)
    await user.open(f'/shared/{CASS.event_id}')
    await user.should_see('Cass of the Station', retries=SLOW)
    user.find(marker='menu-block-this-publisher').click()
    await user.should_see(marker='confirm-dialog', retries=SLOW)
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Block').click()
    await user.should_see('Blocked Juno', retries=SLOW)
    await user.should_see('Aria the Wanderer', retries=SLOW)
    await user.should_not_see('Cass of the Station')

    await user.open('/settings')
    await user.should_see('Juno', retries=SLOW)  # (blocked, with the name they had)
    user.find(marker=f'unblock-{CASS.publisher_id}').click()
    await user.should_see('Unblocked Juno', retries=SLOW)
    with Catalog(tmp_path / 'sharing' / 'catalog.sqlite3') as catalog:  # (their cards back from the relay)
        await wait_for(lambda: catalog.get(CASS.event_id) is not None)


# --- Settings › Sharing cards -------------------------------------------------------------------------

async def test_the_relays_in_settings(user, relay, tmp_path):
    await user.open('/settings')
    await user.should_see(relay.url)
    new = user.find(marker='sharing-new-relay')
    new.type('https://not.a.relay')
    user.find(marker='sharing-add-relay').click()
    await user.should_see('A relay\'s address starts with wss://')
    new.clear().type('wss://Relay.Example.org/')
    user.find(marker='sharing-add-relay').click()
    await user.should_see('wss://relay.example.org', retries=SLOW)
    user.find(marker=f'relay-remove-{relay.url}').click()
    await user.should_not_see(relay.url)
    with Catalog(tmp_path / 'sharing' / 'catalog.sqlite3') as catalog:
        assert [r.url for r in catalog.relays()] == ['wss://relay.example.org']
    user.find(marker='sharing-defaults').click()
    await user.should_see('wss://relay.damus.io', retries=SLOW)
    await user.should_see('None. To block one, open one of their cards.', retries=SLOW)


async def test_when_sharing_cant_run(tmp_path, monkeypatch):
    monkeypatch.setattr('rpapp.ui.app.sharing_problem', lambda: 'a library it needs is missing (coincurve)')
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open('/shared')
        await user.should_see('Sharing cards isn\'t available here')
        await user.should_see('A library it needs is missing (coincurve).', retries=SLOW)
        await user.should_not_see('Python 3.13')  # (not the reason, on this Python: a library missing, or broken)
        await user.open('/settings')
        await user.should_see(marker='sharing-unavailable')  # (what the packages' smoke test looks for)
        assert 'isn\'t available here' in user.find(marker='sharing-unavailable').elements.pop().text
