"""Publishing from the app's pages: the publisher key in Settings, a character's Publish dialog, and the page of
a card of yours (rpapp/ui/sharing_page.py), with a relay of the tests' own and BitTorrent on 127.0.0.1 only."""
import asyncio

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

pytest.importorskip('coincurve', reason='coincurve (sharing cards) is installed for Python 3.13 and earlier')
pytest.importorskip('libtorrent', reason='libtorrent (sharing files) is installed for Python 3.13 and earlier')

from fake_relay import FakeRelay  # noqa: E402

from rpapp.cards import read_card  # noqa: E402
from rpapp.characters import CharacterStore, new_character  # noqa: E402
from rpapp.publishing import announcements as fmt  # noqa: E402
from rpapp.publishing import identity, nostr  # noqa: E402
from rpapp.publishing.announcements import PublicationDraft  # noqa: E402
from rpapp.publishing.seeder import TorrentSession  # noqa: E402
from rpapp.publishing.service import Sharing  # noqa: E402
from rpapp.settings import SettingsStore  # noqa: E402
from rpapp.ui.app import build_root  # noqa: E402

SLOW = 50  # (should_see's tries, 0.1 s apart: for what goes through the relay)


def local_sharing(tmp_path, relay) -> Sharing:
    return Sharing(tmp_path, key_file=identity.key_path(tmp_path), default_relays=[relay.url],
                   session=TorrentSession(listen='127.0.0.1:0', dht=False, local_discovery=False, port_mapping=False))


@pytest.fixture
async def relay():
    async with FakeRelay() as fake:
        yield fake


@pytest.fixture
async def sharing(tmp_path, relay):
    return local_sharing(tmp_path, relay)


@pytest.fixture
async def user(tmp_path, sharing):
    async with user_simulation(build_root(tmp_path, sharing=sharing)) as user:
        yield user


def value_of(user, marker):
    return user.find(marker=marker).elements.pop().value


def set_value(user, marker, value):
    user.find(marker=marker).elements.pop().set_value(value)


async def until(condition, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError('never happened')


def aria(tmp_path):
    store = CharacterStore(tmp_path)
    character = new_character('Aria the Wanderer')
    character.name = 'Aria'
    character.description = 'A wandering bard.'
    character.scenario = '{{char}} sings of old roads.'
    store.save(character)
    return store, character


# --- the publisher key, in Settings ---------------------------------------------------------------------

async def test_making_and_backing_up_the_key(user, tmp_path):
    await user.open('/settings')
    await user.should_see(marker='sharing-make-key')
    user.find(marker='sharing-make-key').click()
    await user.should_see('Your publisher key is made')
    me = identity.load_identity(identity.key_path(tmp_path))  # (with the HTTPS key, in certificates/)
    await user.should_see(me.npub)

    user.find(marker='sharing-backup-key').click()
    await user.should_see(marker='key-backup')
    assert value_of(user, 'key-backup-nsec') == me.nsec()
    with user.scope(marker='key-backup'):
        user.find(kind=ui.button, content='Done').click()
    await user.should_not_see(marker='key-backup')


async def test_using_another_key(user, tmp_path, sharing):
    first = sharing.create_identity()
    await user.open('/settings')
    await user.should_see(first.npub)
    user.find(marker='sharing-replace-key').click()
    await user.should_see(marker='key-warning')
    user.find(marker='key-import-input').type(first.npub)  # (the public half: not a backup)
    user.find(marker='key-import').click()
    await user.should_see('That\'s a public key (npub)')

    other = nostr.new_secret()
    user.find(marker='key-import-input').clear().type(nostr.to_nsec(other))
    user.find(marker='key-import').click()
    await user.should_see('Your publisher key is now')
    assert sharing.identity().secret == other
    await user.should_see(nostr.to_npub(nostr.public_key(other)))
    kept = [p for p in (tmp_path / 'certificates').iterdir() if p.name.startswith('nostr-key.replaced-')]
    assert len(kept) == 1 and identity.load_identity(kept[0]) == first  # (the one replaced, kept)

    user.find(marker='sharing-replace-key').click()
    await user.should_see(marker='key-make-new')
    user.find(marker='key-make-new').click()
    await user.should_see('Your publisher key is now')
    assert sharing.identity().secret not in (first.secret, other)


async def test_a_damaged_key(user, tmp_path, sharing):
    sharing.key_file.parent.mkdir(parents=True)
    sharing.key_file.write_text('not a key', encoding='utf-8')
    await user.open('/settings')
    await user.should_see(marker='sharing-key-problem')
    user.find(marker='sharing-restore-key').click()
    await user.should_see(marker='key-dialog')
    secret = nostr.new_secret()
    user.find(marker='key-import-input').type(nostr.to_nsec(secret))
    user.find(marker='key-import').click()
    await user.should_see('Your publisher key is now')
    assert sharing.identity().secret == secret
    assert [p.read_text(encoding='utf-8') for p in (tmp_path / 'certificates').iterdir()
            if p.name.startswith('nostr-key.replaced-')] == ['not a key']  # (set aside, not lost)


# --- publishing a character --------------------------------------------------------------------------

async def test_publishing_a_character(user, tmp_path, sharing, relay):
    store, character = aria(tmp_path)
    await user.open(f'/characters/{character.id}')
    user.find(marker='menu-publish-to-shared-cards').click()
    await user.should_see(marker='publish-dialog')
    user.find(marker='publish-make-key').click()  # (no key yet: made here)
    await user.should_see(marker='publish-name')
    assert value_of(user, 'publish-name') == 'Aria the Wanderer'
    assert value_of(user, 'publish-description') == 'A wandering bard.'
    await user.should_see(marker='publish-notice')
    await user.should_see('cards only go to relays on this computer or its network')

    user.find(marker='publish-confirm').click()
    await user.should_see('Give your publisher name')
    user.find(marker='publish-publisher').type('Marie')
    user.find(marker='publish-confirm').click()
    await user.should_see('Rate it: SFW or NSFW.')
    set_value(user, 'publish-rating', 'sfw')
    set_value(user, 'publish-tags', ['Fantasy'])
    tags = user.find(marker='publish-tags')
    tags.trigger('inputValue', 'bard')  # (typed, without Enter: added as the field is left for Publish)
    tags.trigger('focusout')
    set_value(user, 'publish-language', 'en')
    user.find(marker='publish-confirm').click()

    await user.should_see('Published "Aria the Wanderer": arrived on 1 relay', retries=SLOW)
    await user.should_see('Your publication', retries=SLOW)  # (its page)
    await user.should_see('Shared from this computer while the app runs', retries=SLOW)
    await user.should_see(marker=f'delivery-{relay.url}', retries=SLOW)
    await user.should_see('Arrived', retries=SLOW)
    await user.should_see('by you', retries=SLOW)
    await user.should_not_see(marker='menu-block-this-publisher')

    saved = store.get(character.id)
    assert saved.shared_uuid  # (given, and kept: publishing it again makes a new version)
    [event] = relay.events.values()
    p = fmt.read_publication(nostr.Event.from_dict(event))
    assert (p.card_uuid, p.card_name, p.published_name, p.card_tags, p.content_rating, p.language) == \
        (saved.shared_uuid, 'Aria the Wanderer', 'Marie', ('fantasy', 'bard'), 'sfw', 'en')
    assert SettingsStore(tmp_path).load().sharing.publisher_name == 'Marie'  # (next time's)
    kept = (tmp_path / 'sharing' / 'published' / p.file_name).read_bytes()
    card = read_card(p.file_name, kept)  # (the card file: as Export makes it, with its UUID)
    assert card.character.shared_uuid == saved.shared_uuid and card.character.name == 'Aria'

    # Again, unchanged: nothing new. Then changed: a new version, starting from what was announced
    await user.open(f'/characters/{character.id}')
    user.find(marker='menu-publish-to-shared-cards').click()
    await user.should_see(marker='publish-new-version')
    assert value_of(user, 'publish-publisher') == 'Marie' and value_of(user, 'publish-rating') == 'sfw'
    assert value_of(user, 'publish-tags') == ['fantasy', 'bard']
    user.find(marker='publish-confirm').click()
    await user.should_see('was already published, unchanged', retries=SLOW)
    assert len(relay.events) == 1

    saved.scenario = '{{char}} sings, and fights.'
    store.save(saved)
    await user.open(f'/characters/{character.id}')
    user.find(marker='menu-publish-to-shared-cards').click()
    await user.should_see(marker='publish-new-version')
    user.find(marker='publish-confirm').click()
    await user.should_see('Published a new version of "Aria the Wanderer"', retries=SLOW)
    assert len(relay.events) == 2
    assert len(sharing.catalog.versions(sharing.identity().public_key, saved.shared_uuid)) == 2


async def test_what_the_format_refuses(user, tmp_path, sharing):
    sharing.create_identity()
    store, character = aria(tmp_path)
    await user.open(f'/characters/{character.id}')
    user.find(marker='menu-publish-to-shared-cards').click()
    await user.should_see(marker='publish-name')
    user.find(marker='publish-publisher').type('Marie')
    set_value(user, 'publish-rating', 'nsfw')
    set_value(user, 'publish-description', 'é' * (fmt.MAX_DESCRIPTION_BYTES // 2 + 1))
    user.find(marker='publish-confirm').click()
    await user.should_see('The description is too long: 16 KB at most', retries=SLOW)
    assert not store.get(character.id).shared_uuid  # (nothing published: no UUID kept)
    assert character.shared_uuid == ''


async def test_no_publishing_where_sharing_cant_run(tmp_path, monkeypatch):
    monkeypatch.setattr('rpapp.ui.app.sharing_problem', lambda: 'a library it needs is missing (coincurve)')
    store, character = aria(tmp_path)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/characters/{character.id}')
        await user.should_see(marker='menu-export')
        await user.should_not_see(marker='menu-publish-to-shared-cards')


def test_a_copy_is_another_card(tmp_path):
    store, character = aria(tmp_path)
    character.shared_uuid = fmt.new_card_uuid()
    store.save(character)
    copy = store.duplicate(character)
    assert copy.shared_uuid == '' and copy.title == 'Aria the Wanderer (copy)'
    assert store.get(character.id).shared_uuid == character.shared_uuid


# --- the page of a card of yours ---------------------------------------------------------------------

async def publish(sharing, title='Aria the Wanderer', content=b'', *, uuid=None):
    from rpapp.cards import card_data, card_png
    c = new_character(title)
    c.scenario = content.decode() or title
    return await sharing.publish(PublicationDraft(publisher_name='Marie', card_name=title), card_png(card_data(c), None),
                                 card_uuid=uuid or fmt.new_card_uuid())


async def test_sending_again(user, sharing, relay):
    sharing.create_identity()
    relay.refuse_events = 'blocked: members only'
    published = await publish(sharing)
    await user.open(f'/shared/{published.publication.event_id}')
    await user.should_see('Refused: blocked: members only', retries=SLOW)
    relay.refuse_events = None
    user.find(marker='own-send-again').click()
    await user.should_see('Sent again: arrived on 1 relay', retries=SLOW)
    await user.should_see('Arrived', retries=SLOW)
    await user.should_not_see(marker='own-send-again')  # (nowhere left to send it)

    sharing.catalog.add_relay('ws://192.168.1.20:7777')  # (added since: it could go there)
    await user.open(f'/shared/{published.publication.event_id}')
    await user.should_see(marker='own-send-again', retries=SLOW)


async def test_stopping_sharing_the_file(user, sharing, tmp_path):
    sharing.create_identity()
    published = await publish(sharing)
    await user.open(f'/shared/{published.publication.event_id}')
    await user.should_see('Shared from this computer while the app runs', retries=SLOW)
    user.find(marker='own-stop').click()
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Stop sharing').click()
    await user.should_see('Its file is no longer shared from this computer', retries=SLOW)
    await user.should_see('Not shared from this computer. To share it again, publish its character again.',
                          retries=SLOW)
    assert sharing.session.sharing() == ()
    assert not (tmp_path / 'sharing' / 'published' / published.publication.file_name).exists()


async def test_withdrawing(user, sharing, relay):
    sharing.create_identity()
    uuid = fmt.new_card_uuid()
    first = await publish(sharing, uuid=uuid)
    second = await publish(sharing, content=b'Aria sings, and fights.', uuid=uuid)
    await user.open(f'/shared/{second.publication.event_id}')
    await user.should_see('Your publication', retries=SLOW)
    user.find(marker='own-withdraw').click()
    await user.should_see(marker='withdraw-dialog')
    assert value_of(user, 'withdraw-all') is True  # (its older version too)
    user.find(marker='withdraw-reason').type('Replaced by a new card.')
    user.find(marker='withdraw-confirm').click()

    await user.should_see('Withdrawn: arrived on 1 relay', retries=SLOW)
    await user.should_see('Withdrawn by its publisher', retries=SLOW)
    await user.should_see('Their reason: Replaced by a new card.', retries=SLOW)
    await user.should_see('Its withdrawal, on the relays', retries=SLOW)
    await user.should_see('No longer shared: it was withdrawn.', retries=SLOW)
    await user.should_see('Arrived', retries=SLOW)  # (the withdrawal)
    await user.should_not_see(marker='own-withdraw')
    for p in (first.publication, second.publication):
        assert sharing.catalog.get(p.event_id).withdrawn.reason == 'Replaced by a new card.'
    [withdrawal] = [e for e in relay.events.values() if e['kind'] == 5]  # (both, in one withdrawal)
    assert {t[1] for t in withdrawal['tags'] if t[0] == 'e'} == {first.publication.event_id,
                                                                 second.publication.event_id}
    assert sharing.session.sharing() == ()


async def test_someone_elses_card_has_no_panel(user, sharing, relay):
    others = nostr.new_secret()
    from rpapp.publishing import torrents
    theirs = fmt.build_publication(PublicationDraft(publisher_name='Juno', card_name='Cass'),
                                   torrents.card_file(b'Cass'), card_uuid=fmt.new_card_uuid(), secret=others)
    sharing.catalog.receive(theirs.event.to_dict())
    sharing.create_identity()
    await user.open(f'/shared/{theirs.event_id}')
    await user.should_see('by Juno', retries=SLOW)
    await user.should_not_see('Your publication')
    await user.should_see(marker='menu-block-this-publisher')
