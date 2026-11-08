"""Downloading from the pages (rpapp/ui/sharing_page.py, characters_page.py): a card's page adds it to your
characters; its character says where it came from. Marie publishes from another BitTorrent session on this
computer, which the app's finds (the tests' stand-in for the DHT and trackers)."""
import asyncio

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

pytest.importorskip('coincurve', reason='coincurve (sharing cards) is installed for Python 3.13 and earlier')
pytest.importorskip('libtorrent', reason='libtorrent (sharing files) is installed for Python 3.13 and earlier')

from fake_relay import FakeRelay  # noqa: E402
from test_downloading import session  # noqa: E402

from rpapp.cards import card_data, card_png  # noqa: E402
from rpapp.characters import CharacterStore, new_character  # noqa: E402
from rpapp.publishing import announcements as fmt  # noqa: E402
from rpapp.publishing import identity, nostr, torrents  # noqa: E402
from rpapp.publishing.announcements import PublicationDraft  # noqa: E402
from rpapp.publishing.service import Sharing  # noqa: E402
from rpapp.settings import SettingsStore  # noqa: E402
from rpapp.ui.app import build_root  # noqa: E402

SLOW = 100  # (should_see's tries, 0.1 s apart: what goes through BitTorrent)
DRAFT = PublicationDraft(publisher_name='Marie', card_name='Aria the Wanderer', card_tags=('fantasy',))


def aria_card(scenario='{{char}} sings of old roads.') -> bytes:
    aria = new_character('Aria')
    aria.description = 'A wandering bard.'
    aria.scenario = scenario
    return card_png(card_data(aria), None)


@pytest.fixture
async def relay():
    async with FakeRelay() as fake:
        yield fake


@pytest.fixture
async def marie(tmp_path, relay):
    s = Sharing(tmp_path / 'marie', default_relays=[relay.url], session=session())
    s.create_identity()
    s.session.start()
    yield s
    await s.close()


@pytest.fixture
async def mine(tmp_path, relay, marie):
    """This app's sharing: finds Marie's session."""
    return Sharing(tmp_path, key_file=identity.key_path(tmp_path), default_relays=[relay.url], session=session(marie))


@pytest.fixture
async def user(tmp_path, mine):
    async with user_simulation(build_root(tmp_path, sharing=mine)) as user:
        yield user


async def published(marie, mine, content=None, uuid=None):
    p = (await marie.publish(DRAFT, content or aria_card(), card_uuid=uuid or fmt.new_card_uuid())).publication
    mine.catalog.receive(p.event.to_dict())  # (as discovery would)
    return p


async def test_adding_a_shared_card(user, marie, mine, tmp_path):
    p = await published(marie, mine)
    await user.open(f'/shared/{p.event_id}')
    await user.should_see('Add it to your characters')
    await user.should_see('Those it comes from see this computer\'s internet address.')
    user.find(marker='shared-add').click()
    await user.should_see('Added as', retries=SLOW)
    await user.should_see('Shared from this computer while the app runs', retries=SLOW)
    [aria] = CharacterStore(tmp_path).list()
    assert (aria.title, aria.scenario) == ('Aria', '{{char}} sings of old roads.')
    assert mine.catalog.download(p.event_id).character_id == aria.id

    # Again: the file is here already, so it's over at once; the page says so (not "Looking…" for ever)
    user.find(marker='shared-add').click()
    for _ in range(100):
        if len(CharacterStore(tmp_path).list()) == 2:
            break
        await asyncio.sleep(0.05)
    assert len(CharacterStore(tmp_path).list()) == 2
    await asyncio.sleep(1)  # (the page's look at the download, twice)
    await user.should_not_see(marker='shared-fetching')
    again = mine.catalog.download(p.event_id).character_id
    assert again != aria.id
    await user.should_see('Added as')

    user.find(marker='shared-character-link').click()  # (its character says where it came from)
    await user.should_see(marker='character-shared-from', retries=SLOW)
    await user.should_see('"Aria the Wanderer" by Marie', retries=SLOW)

    await user.open('/shared')
    await user.should_see('downloaded', retries=SLOW)  # (in the list too)


async def test_withdrawn_or_newer_since(user, marie, mine, tmp_path):
    uuid = fmt.new_card_uuid()
    first = await published(marie, mine, uuid=uuid)
    fetch = mine.download(first.event_id)
    await fetch.task
    character_id = fetch.character_id

    second = await published(marie, mine, aria_card('{{char}} sings, and fights.'), uuid=uuid)
    await user.open(f'/characters/{character_id}')
    await user.should_see(marker='character-shared-newer', retries=SLOW)
    user.find(kind=ui.link, content='see it').click()
    await user.should_see('Add it to your characters', retries=SLOW)  # (the new version's page)

    withdrawal = fmt.build_withdrawal([first.event_id, second.event_id], secret=marie.identity().secret,
                                      reason='Replaced by a better card.')
    mine.catalog.receive(withdrawal.event.to_dict())
    await mine.resume_sharing()  # (as after a discovery: the files of withdrawn cards no longer shared)
    assert mine.session.sharing() == ()
    await user.open(f'/characters/{character_id}')
    await user.should_see(marker='character-shared-withdrawn', retries=SLOW)
    await user.should_see('Their reason: Replaced by a better card.', retries=SLOW)
    await user.open(f'/shared/{first.event_id}')
    await user.should_see('Withdrawn by its publisher', retries=SLOW)
    await user.should_see('Added as', retries=SLOW)  # (you have it, and keep it)
    await user.should_see('No longer shared: it was withdrawn.', retries=SLOW)
    await user.should_not_see(marker='shared-add')


def scenario_field(user):
    return next(e for e in user.find(ui.textarea).elements if e.props.get('label') == 'Scenario')


async def test_edited_since(user, marie, mine, tmp_path):
    """Its page says whether you edited it since you downloaded it, as you do; added again, it's another
    character, and the one you had still comes from it. (With a picture: made again here, as a WebP.)"""
    import io

    from PIL import Image
    picture = io.BytesIO()
    Image.new('RGB', (300, 400), (180, 90, 60)).save(picture, 'PNG')
    aria = new_character('Aria')
    aria.description = 'A wandering bard.'
    aria.scenario = '{{char}} sings of old roads.'
    p = await published(marie, mine, card_png(card_data(aria), picture.getvalue()))
    fetch = mine.download(p.event_id)
    await fetch.task
    character_id = fetch.character_id
    assert CharacterStore(tmp_path).get(character_id).image  # (its picture, kept)
    await user.open(f'/characters/{character_id}')
    await user.should_see(marker='character-not-edited', retries=SLOW)  # (as added: nothing changed it since)
    await user.should_see('Not edited since you downloaded it')

    scenario_field(user).set_value('{{char}} sings, and fights.')
    await user.should_see(marker='character-edited', retries=SLOW)
    await user.should_see('Edited by you since you downloaded it')
    scenario_field(user).set_value('{{char}} sings of old roads.')  # (as it was)
    await user.should_see(marker='character-not-edited', retries=SLOW)
    scenario_field(user).set_value('{{char}} sings, and fights.')
    await user.should_see(marker='character-edited', retries=SLOW)

    again = mine.download(p.event_id)
    await again.task
    assert again.character_id not in ('', character_id)
    await user.open(f'/characters/{character_id}')  # (the one edited: still from it, still edited)
    await user.should_see(marker='character-shared-from', retries=SLOW)
    await user.should_see(marker='character-edited', retries=SLOW)
    await user.open(f'/characters/{again.character_id}')
    await user.should_see(marker='character-not-edited', retries=SLOW)


async def test_downloaded_before_fingerprints(user, marie, mine, tmp_path):
    """Added before what it was then was kept: when, but not whether it was edited since."""
    p = await published(marie, mine)
    store = CharacterStore(tmp_path)
    aria = new_character('Aria')
    store.save(aria)
    mine.catalog.record_download(p.event_id, p.torrent_infohash, p.file_name, aria.id)  # (no fingerprint)
    await user.open(f'/characters/{aria.id}')
    await user.should_see(marker='character-shared-from', retries=SLOW)
    await user.should_see('Downloaded on')
    await user.should_not_see(marker='character-edited')
    await user.should_not_see(marker='character-not-edited')


async def test_nobody_shares_it(user, mine):
    lonely = fmt.build_publication(DRAFT, torrents.card_file(aria_card()), card_uuid=fmt.new_card_uuid(),
                                   secret=nostr.new_secret())
    mine.catalog.receive(lonely.event.to_dict())
    mine.session.find_timeout = 1
    await user.open(f'/shared/{lonely.event_id}')
    await user.should_see(marker='shared-add')
    user.find(marker='shared-add').click()
    await user.should_see('Looking for people sharing it', retries=SLOW)
    await user.should_see('Nobody sharing it was found', retries=SLOW)
    await user.should_see(marker='shared-add')  # (to try again)


async def test_not_sharing_downloads(user, marie, mine, tmp_path):
    p = await published(marie, mine)
    await (mine.download(p.event_id)).task
    await user.open('/settings')
    await user.should_see(marker='sharing-share-downloads')
    user.find(marker='sharing-share-downloads').elements.pop().set_value(False)
    await user.should_see('1 card no longer shared from this computer', retries=SLOW)
    assert SettingsStore(tmp_path).load().sharing.share_downloads is False
    assert mine.session.sharing() == () and mine.share_downloads is False

    await user.open(f'/shared/{p.event_id}')
    await user.should_see('Its file isn\'t shared from this computer', retries=SLOW)


async def test_the_setting_when_the_app_starts(tmp_path, relay):
    store = SettingsStore(tmp_path)
    settings = store.load()
    settings.sharing.share_downloads = False
    store.save(settings)
    async with user_simulation(build_root(tmp_path, sharing_relays=[relay.url])) as user:
        await user.open('/settings')
        await user.should_see(marker='sharing-share-downloads')
        assert user.find(marker='sharing-share-downloads').elements.pop().value is False
