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
from rpapp.publishing import identity, nostr, torrents  # noqa: E402
from rpapp.publishing.announcements import PublicationDraft  # noqa: E402
from rpapp.publishing.seeder import TorrentSession  # noqa: E402
from rpapp.publishing.service import Sharing  # noqa: E402
from rpapp.settings import SettingsStore  # noqa: E402
from rpapp.ui.app import build_root  # noqa: E402
from rpapp.ui.sharing_page import KEY_FILE, NOTICE_VERSION  # noqa: E402

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
    user.find(marker='key-backup-file').click()
    assert (await user.download.next()).content == f'{me.nsec()}\n'.encode()
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


def accept_notice(tmp_path):
    """As if the wizard had been through once already: the card's form only."""
    store = SettingsStore(tmp_path)
    settings = store.load()
    settings.sharing.publishing_accepted = NOTICE_VERSION
    store.save(settings)


async def open_publishing(user, character):
    await user.open(f'/characters/{character.id}')
    user.find(marker='menu-publish-to-shared-cards').click()
    await user.should_see(marker='publish-dialog')


async def through_the_wizard(user, *, make_key=True):
    await user.should_see(marker='wizard-notice')
    user.find(marker='wizard-agree').click()
    await user.should_see(marker='wizard-key')
    if make_key:
        user.find(marker='wizard-make-key').click()
        await user.should_see(marker='wizard-nsec')
    set_value(user, 'wizard-saved', True)
    user.find(marker='wizard-key-next').click()
    await user.should_see(marker='wizard-sharing')
    user.find(marker='wizard-sharing-next').click()
    await user.should_see(marker='publish-name')


# --- publishing a character --------------------------------------------------------------------------

async def test_publishing_a_character(user, tmp_path, sharing, relay):
    store, character = aria(tmp_path)
    await open_publishing(user, character)
    await through_the_wizard(user)  # (the first time: what it means, the key made, how it's shared)
    await user.should_see('Step 4 of 4: Your card')
    assert value_of(user, 'publish-name') == 'Aria the Wanderer'
    assert value_of(user, 'publish-description') == 'A wandering bard.'
    await user.should_not_see('Everything in the card becomes public')  # (said by the wizard's first step)

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
    await user.should_see(marker='own-sharing', retries=SLOW)  # (its page)
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
    kept = (tmp_path / 'sharing' / 'files' / p.file_name).read_bytes()
    card = read_card(p.file_name, kept)  # (the card file: as Export makes it, with its UUID)
    assert card.character.shared_uuid == saved.shared_uuid and card.character.name == 'Aria'

    # Again, unchanged: nothing new (and no wizard). Then changed: a new version, from what was announced
    await open_publishing(user, character)
    await user.should_see(marker='publish-new-version')
    await user.should_not_see(marker='wizard-card')
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
    accept_notice(tmp_path)
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


# --- the wizard, the first time --------------------------------------------------------------------------

async def test_cancelling_changes_nothing(user, tmp_path, sharing):
    store, character = aria(tmp_path)
    await open_publishing(user, character)
    await user.should_see('Step 1 of 4: Before you publish')
    await user.should_see(marker='wizard-rules')
    user.find(marker='wizard-cancel').click()  # (at the notice)
    await user.should_not_see(marker='publish-dialog')

    await open_publishing(user, character)
    user.find(marker='wizard-agree').click()
    await user.should_see(marker='wizard-key')
    user.find(marker='wizard-make-key').click()
    await user.should_see(marker='wizard-nsec')
    set_value(user, 'wizard-saved', True)
    user.find(marker='wizard-key-next').click()
    await user.should_see(marker='wizard-sharing')
    set_value(user, 'wizard-port', 7000)
    set_value(user, 'wizard-port-mapping', False)
    user.find(marker='wizard-cancel').click()  # (at the port: nothing kept but the key, made)
    await user.should_not_see(marker='publish-dialog')
    settings = SettingsStore(tmp_path).load().sharing
    assert (settings.port, settings.port_mapping, settings.publishing_accepted) == (None, True, 0)
    assert sharing.session.wanted_port is None and sharing.identity() is not None

    await open_publishing(user, character)  # (not through yet: again, the key there already)
    await user.should_see(marker='wizard-notice')
    user.find(marker='wizard-agree').click()
    await user.should_see('Your publisher key. Its public half, which identifies you:')
    await user.should_see(sharing.identity().npub)


async def test_the_key_must_be_backed_up(user, tmp_path, sharing):
    store, character = aria(tmp_path)
    await open_publishing(user, character)
    user.find(marker='wizard-agree').click()
    await user.should_see(marker='wizard-make-key')
    user.find(marker='wizard-make-key').click()
    await user.should_see('Your key is made.')
    await user.should_see(marker='wizard-backup-warning')
    me = sharing.identity()
    assert value_of(user, 'wizard-nsec') == me.nsec()
    next_button = user.find(marker='wizard-key-next').elements.pop()
    assert not next_button.enabled  # (not before it's said to be saved)
    user.find(marker='wizard-save-file').click()
    assert (await user.download.next()).content == f'{me.nsec()}\n'.encode()
    set_value(user, 'wizard-saved', True)
    assert next_button.enabled
    user.find(marker='wizard-back').click()  # (back to the notice, and on again)
    await user.should_see(marker='wizard-agree')
    user.find(marker='wizard-agree').click()
    await user.should_see(marker='wizard-saved')
    assert not user.find(marker='wizard-key-next').elements.pop().enabled
    assert KEY_FILE.endswith('.txt')


async def test_importing_a_key(user, tmp_path, sharing):
    store, character = aria(tmp_path)
    await open_publishing(user, character)
    user.find(marker='wizard-agree').click()
    await user.should_see(marker='wizard-import-input')
    user.find(marker='wizard-import-input').type('npub1nope')
    user.find(marker='wizard-import').click()
    await user.should_see('That\'s a public key (npub)')
    secret = nostr.new_secret()
    user.find(marker='wizard-import-input').clear().type(nostr.to_nsec(secret))
    user.find(marker='wizard-import').click()
    await user.should_see(nostr.to_npub(nostr.public_key(secret)))
    assert sharing.identity().secret == secret


async def test_a_damaged_key_when_publishing(user, tmp_path, sharing):
    sharing.key_file.parent.mkdir(parents=True)
    sharing.key_file.write_text('not a key', encoding='utf-8')
    accept_notice(tmp_path)  # (through before: the key's step only)
    store, character = aria(tmp_path)
    await open_publishing(user, character)
    await user.should_see('Step 1 of 2: Your publisher key')
    await user.should_see(marker='wizard-key-problem')
    secret = nostr.new_secret()
    user.find(marker='wizard-import-input').type(nostr.to_nsec(secret))
    user.find(marker='wizard-import').click()
    await user.should_see(marker='wizard-saved')
    assert sharing.identity().secret == secret
    set_value(user, 'wizard-saved', True)
    user.find(marker='wizard-key-next').click()
    await user.should_see('Step 2 of 2: Your card')  # (the port's step: seen already)


async def test_the_port_kept_once_through(user, tmp_path, sharing):
    store, character = aria(tmp_path)
    await open_publishing(user, character)
    user.find(marker='wizard-agree').click()
    await user.should_see(marker='wizard-make-key')
    user.find(marker='wizard-make-key').click()
    await user.should_see(marker='wizard-saved')
    set_value(user, 'wizard-saved', True)
    user.find(marker='wizard-key-next').click()
    await user.should_see('Step 3 of 4: Sharing its file')
    await user.should_see('nobody can download it')
    set_value(user, 'wizard-port', 80)  # (refused)
    user.find(marker='wizard-sharing-next').click()
    await user.should_see('A port from 1024 to 65535, or empty')
    set_value(user, 'wizard-port', 7000)
    set_value(user, 'wizard-port-mapping', False)
    user.find(marker='wizard-sharing-next').click()
    await user.should_see(marker='publish-name')
    settings = SettingsStore(tmp_path).load().sharing
    assert (settings.port, settings.port_mapping, settings.publishing_accepted) == (7000, False, NOTICE_VERSION)
    assert sharing.session.wanted_port == 7000 and sharing.session.settings['enable_upnp'] is False

    user.find(marker='wizard-cancel').click()  # (the card not published: but the wizard is through)
    await open_publishing(user, character)
    await user.should_see(marker='publish-name')
    await user.should_not_see('Step 1 of')


async def test_the_key_gone_since(user, tmp_path, sharing):
    accept_notice(tmp_path)
    store, character = aria(tmp_path)
    await open_publishing(user, character)
    await user.should_see('Step 1 of 2: Your publisher key')
    user.find(marker='wizard-make-key').click()
    await user.should_see(marker='wizard-saved')
    set_value(user, 'wizard-saved', True)
    user.find(marker='wizard-key-next').click()
    await user.should_see('Step 2 of 2: Your card')


# --- the character's page: published by you, changed since ---------------------------------------------

def scenario_field(user):
    return next(e for e in user.find(ui.textarea).elements if e.props.get('label') == 'Scenario')


async def test_published_by_you_and_changed_since(user, tmp_path, sharing, relay):
    sharing.create_identity()
    accept_notice(tmp_path)
    store, character = aria(tmp_path)
    await open_publishing(user, character)
    await user.should_see(marker='publish-name')
    user.find(marker='publish-publisher').type('Marie')
    set_value(user, 'publish-rating', 'sfw')
    user.find(marker='publish-confirm').click()
    await user.should_see(marker='own-sharing', retries=SLOW)
    [event_id] = relay.events
    assert sharing.catalog.source(event_id).character_id == character.id  # (what it was published from)

    await user.open(f'/characters/{character.id}')
    await user.should_see(marker='character-published', retries=SLOW)
    await user.should_see('"Aria the Wanderer"', retries=SLOW)
    await user.should_see(marker='character-up-to-date', retries=SLOW)

    scenario_field(user).set_value('{{char}} sings, and now she fights too.')  # (edited here)
    await user.should_see(marker='character-changed', retries=SLOW)
    await user.should_see('Changed since: the shared card isn\'t up to date.', retries=SLOW)
    scenario_field(user).set_value('{{char}} sings of old roads.')  # (as it was: up to date again)
    await user.should_see(marker='character-up-to-date', retries=SLOW)

    scenario_field(user).set_value('{{char}} sings, and fights.')
    await user.should_see(marker='character-publish-again', retries=SLOW)
    user.find(marker='character-publish-again').click()  # (straight to the form)
    await user.should_see(marker='publish-new-version', retries=SLOW)


LOOK = 80  # (Your publications looks again every 5 s)


async def test_your_publications_say_what_changed(user, tmp_path, sharing, relay):
    """The list says what a character's page says: changed since it was published (looked at again as the page
    stays open), with a link to it, to publish it again."""
    sharing.create_identity()
    accept_notice(tmp_path)
    store, character = aria(tmp_path)
    await open_publishing(user, character)
    await user.should_see(marker='publish-name')
    user.find(marker='publish-publisher').type('Marie')
    set_value(user, 'publish-rating', 'sfw')
    user.find(marker='publish-confirm').click()
    await user.should_see(marker='own-sharing', retries=SLOW)
    [event_id] = relay.events

    await user.open('/published')
    await user.should_see(marker=f'row-{event_id}', retries=SLOW)
    await user.should_not_see(marker=f'published-changed-{event_id}')

    character = store.get(character.id)
    character.scenario = '{{char}} sings, and now she fights too.'
    store.save(character)  # (edited elsewhere: another tab...)
    await user.should_see(marker=f'published-changed-{event_id}', retries=LOOK)
    await user.should_see('Aria the Wanderer changed since: publish it again')
    row = user.find(marker=f'row-{event_id}').elements.pop()
    assert 'warning' in [e.props.get('name') for e in row.descendants() if isinstance(e, ui.icon)]

    user.find(marker=f'published-changed-{event_id}').click()  # (to its page: "Publish it again" there)
    await user.should_see(marker='character-changed', retries=SLOW)

    character.scenario = '{{char}} sings of old roads.'  # (as it was: up to date again)
    store.save(character)
    await user.open('/published')
    await user.should_see(marker=f'row-{event_id}', retries=SLOW)
    await user.should_not_see(marker=f'published-changed-{event_id}')


async def test_published_before_fingerprints(user, tmp_path, sharing):
    """Published before the fingerprint was kept: the card file that would be published now, against the
    one that was."""
    from rpapp.cards import card_data, card_png
    from rpapp.ui.list_view import initials
    sharing.create_identity()
    store, character = aria(tmp_path)
    character.shared_uuid = fmt.new_card_uuid()
    store.save(character)
    content = card_png(card_data(character, []), None, initials=initials(character.title))  # (as Export makes it)
    published = await sharing.publish(PublicationDraft(publisher_name='Marie', card_name='Aria'), content,
                                      card_uuid=character.shared_uuid)
    event_id = published.publication.event_id
    await user.open(f'/characters/{character.id}')
    await user.should_see(marker='character-up-to-date', retries=SLOW)
    await user.open('/published')
    await user.should_see(marker=f'row-{event_id}', retries=SLOW)
    await user.should_not_see(marker=f'published-changed-{event_id}')

    character.description = 'Changed outside the page.'
    store.save(character)
    await user.open(f'/characters/{character.id}')
    await user.should_see(marker='character-changed', retries=SLOW)
    await user.open('/published')
    await user.should_see(marker=f'published-changed-{event_id}', retries=SLOW)


async def test_the_note_doesnt_jump(user, tmp_path, sharing, monkeypatch):
    """Edited: the note stays as it is while it's worked out again (not emptied, then drawn: the page jumped),
    and it's drawn again only if what it says changes."""
    import rpapp.ui.sharing_page as page
    from rpapp.cards import card_data, card_png
    from rpapp.ui.list_view import initials
    sharing.create_identity()
    store, character = aria(tmp_path)
    character.shared_uuid = fmt.new_card_uuid()
    store.save(character)
    content = card_png(card_data(character, []), None, initials=initials(character.title))
    await sharing.publish(PublicationDraft(publisher_name='Marie', card_name='Aria'), content,
                          card_uuid=character.shared_uuid)
    shown_meanwhile = []
    real = page.changed_since

    def note_shown() -> bool:
        """What it says of the character (changed, or up to date): what went, then came back."""
        for marker in ('character-changed', 'character-up-to-date'):
            try:
                if user.find(marker=marker).elements:
                    return True
            except AssertionError:  # (none)
                pass
        return False

    async def slow(*args, **kwargs):
        answer = await real(*args, **kwargs)
        await asyncio.sleep(0.3)  # (as a big picture would take)
        shown_meanwhile.append(note_shown())
        return answer
    monkeypatch.setattr(page, 'changed_since', slow)

    await user.open(f'/characters/{character.id}')
    await user.should_see(marker='character-up-to-date', retries=SLOW)
    shown_meanwhile.clear()  # (the page as it opened: no note yet, of course)
    scenario_field(user).set_value('{{char}} sings, and now she fights too.')
    await user.should_see(marker='character-changed', retries=SLOW)
    note = user.find(marker='character-changed').elements.pop()
    scenario_field(user).set_value('{{char}} sings, and fights.')  # (still changed: nothing drawn again)
    await until(lambda: len(shown_meanwhile) >= 2)
    await asyncio.sleep(0.2)
    assert shown_meanwhile and all(shown_meanwhile)
    assert user.find(marker='character-changed').elements.pop() is note


async def test_no_character_here(user, tmp_path, sharing):
    """Published from another computer (your key brought here), or its character deleted since: nothing said."""
    sharing.create_identity()
    store, character = aria(tmp_path)
    character.shared_uuid = fmt.new_card_uuid()
    store.save(character)
    elsewhere = await publish(sharing, 'Juno')  # (no character here is that card)
    gone = await sharing.publish(PublicationDraft(publisher_name='Marie', card_name='Aria'), b'\x89PNG\r\n\x1a\nx',
                                 card_uuid=character.shared_uuid)
    sharing.catalog.record_source(gone.publication.event_id, character.id, 'its fingerprint then')
    store.delete(character.id)
    await user.open('/published')
    for p in (elsewhere.publication, gone.publication):
        await user.should_see(marker=f'row-{p.event_id}', retries=SLOW)
        await user.should_not_see(marker=f'published-changed-{p.event_id}')


async def test_withdrawn_and_not_yours(user, tmp_path, sharing):
    store, character = aria(tmp_path)
    character.shared_uuid = fmt.new_card_uuid()
    store.save(character)
    other = fmt.build_publication(PublicationDraft(publisher_name='Juno', card_name='Aria'),
                                  torrents.card_file(b'theirs'), card_uuid=character.shared_uuid,
                                  secret=nostr.new_secret())
    sharing.catalog.receive(other.event.to_dict())  # (the same card's UUID, someone else's: not yours)
    await user.open(f'/characters/{character.id}')
    await user.should_see('Description', retries=SLOW)
    await user.should_not_see(marker='character-published')

    sharing.create_identity()
    mine = await sharing.publish(PublicationDraft(publisher_name='Marie', card_name='Aria'), b'\x89PNG\r\n\x1a\nx',
                                 card_uuid=character.shared_uuid)
    await sharing.withdraw([mine.publication.event_id])
    await user.open(f'/characters/{character.id}')
    await user.should_see(marker='character-published-withdrawn', retries=SLOW)
    await user.should_not_see(marker='character-changed')


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


async def test_tried_again_by_itself(user, sharing, relay):
    """A relay that couldn't be reached: its page says when it's tried again, and what came of it once it is
    (looked at again as the page stays open); one that refused it for good isn't tried again."""
    import socket
    import time

    from fake_relay import FakeRelay
    from rpapp.publishing.service import RETRY_AFTER
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    down = f'ws://127.0.0.1:{port}'
    sharing.create_identity()
    sharing.catalog.add_relay(down)
    relay.refuse_events = 'blocked: members only'
    published = await publish(sharing)
    relay.refuse_events = None
    await user.open(f'/shared/{published.publication.event_id}')
    await user.should_see(marker=f'delivery-{down}', retries=SLOW)
    await user.should_see('Tried again by itself in a minute.', retries=SLOW)
    await user.should_see('Refused: blocked: members only', retries=SLOW)  # (and nothing more: for good)

    async with FakeRelay(port=port) as back:
        assert await sharing.retry_deliveries(now=time.time() + RETRY_AFTER[0]) == 1
        # (the page looks again by itself, every 5 s; should_not_see tries every 0.05 s)
        await user.should_not_see('Tried again by itself', retries=150)
        assert published.publication.event_id in back.events
    await user.should_see('Refused: blocked: members only')


async def test_a_relay_added_in_settings(user, sharing, relay):
    """Your cards go to a relay added (and it's said); once there, its page says so."""
    sharing.create_identity()
    published = await publish(sharing)
    event_id = published.publication.event_id
    async with FakeRelay() as new:
        await user.open('/settings')
        await user.should_see(marker='sharing-new-relay')
        user.find(marker='sharing-new-relay').type(new.url)
        user.find(marker='sharing-add-relay').click()
        await user.should_see('Your 1 card is being sent to it.', retries=SLOW)
        await until(lambda: any(d.relay_url == new.url and d.state == 'accepted'
                                for d in sharing.catalog.deliveries(event_id)))
        assert event_id in new.events
        await user.open(f'/shared/{event_id}')
        await user.should_see(marker=f'delivery-{new.url}', retries=SLOW)
        await user.should_not_see(marker='own-send-again')  # (nowhere left to send it)


async def test_stopping_sharing_the_file(user, sharing, tmp_path):
    sharing.create_identity()
    published = await publish(sharing)
    await user.open(f'/shared/{published.publication.event_id}')
    await user.should_see('Shared from this computer while the app runs', retries=SLOW)
    await user.should_see(marker='own-stop', retries=SLOW)  # (drawn after the relays: read from the disk first)
    user.find(marker='own-stop').click()
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Stop sharing').click()
    await user.should_see('Its file is no longer shared from this computer', retries=SLOW)
    await user.should_see('Not shared from this computer. To share it again, publish its character again.',
                          retries=SLOW)
    assert sharing.session.sharing() == ()
    assert not (tmp_path / 'sharing' / 'files' / published.publication.file_name).exists()


async def test_withdrawing(user, sharing, relay):
    sharing.create_identity()
    uuid = fmt.new_card_uuid()
    first = await publish(sharing, uuid=uuid)
    second = await publish(sharing, content=b'Aria sings, and fights.', uuid=uuid)
    await user.open(f'/shared/{second.publication.event_id}')
    await user.should_see(marker='own-withdraw', retries=SLOW)  # (drawn after the relays: read from the disk first)
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
    await user.should_not_see(marker='own-sharing')
    await user.should_see(marker='menu-block-this-publisher')
