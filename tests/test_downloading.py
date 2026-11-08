"""Downloading shared cards (rpapp/publishing/service.py, seeder.py): from another BitTorrent session on this
computer (no DHT, no trackers reached), with a relay of the tests' own for the announcements."""
import asyncio
import hashlib
import os
from dataclasses import replace

import pytest

pytest.importorskip('coincurve', reason='coincurve (sharing cards) is installed for Python 3.13 and earlier')
pytest.importorskip('libtorrent', reason='libtorrent (sharing files) is installed for Python 3.13 and earlier')

from fake_relay import FakeRelay  # noqa: E402

from rpapp.cards import CardError, card_data, card_png  # noqa: E402
from rpapp.characters import new_character  # noqa: E402
from rpapp.publishing import announcements as fmt  # noqa: E402
from rpapp.publishing import nostr, torrents  # noqa: E402
from rpapp.publishing.announcements import PublicationDraft  # noqa: E402
from rpapp.publishing.seeder import TorrentSession  # noqa: E402
from rpapp.publishing.service import Sharing  # noqa: E402

DRAFT = PublicationDraft(publisher_name='Marie', card_name='Aria the Wanderer', card_tags=('fantasy',))


def session(*peers: Sharing) -> TorrentSession:
    s = TorrentSession(listen='127.0.0.1:0', dht=False, local_discovery=False, port_mapping=False, trackers=(),
                       peers=tuple(('127.0.0.1', p.session.port) for p in peers))
    s.find_timeout = s.stall_timeout = 10
    # (TCP: on one computer, uTP to a session whose peer vanished can wait for libtorrent's 15 s timeout)
    s.settings.update(enable_outgoing_utp=False, enable_incoming_utp=False)
    return s


def aria_card(scenario='Aria sings.', *, padding=150_000) -> bytes:
    aria = new_character('Aria')
    aria.scenario = scenario
    return card_png(card_data(aria), None) + os.urandom(padding)  # (several pieces: data after the PNG's end)


class Importer:
    """The app's: adds a card file as a character. Here, it keeps what it's given."""
    def __init__(self, fail: Exception | None = None) -> None:
        self.added: list[tuple[str, bytes]] = []
        self.fail = fail

    def __call__(self, file_name: str, content: bytes, languages=()) -> tuple[str, str, str]:
        if self.fail:
            raise self.fail
        self.added.append((file_name, content))
        return f'aria-{len(self.added)}', f'Imported "Aria" ({len(self.added)})', f'fingerprint {len(self.added)}'


@pytest.fixture
async def relay():
    async with FakeRelay() as fake:
        yield fake


@pytest.fixture
async def marie(tmp_path, relay):
    """The publisher: shares what she publishes (her session started)."""
    s = Sharing(tmp_path / 'marie', default_relays=[relay.url], session=session())
    s.create_identity()
    s.session.start()
    yield s
    await s.close()


@pytest.fixture
async def juno(tmp_path, relay, marie):
    """Who downloads: finds Marie's session (the tests' stand-in for the DHT and trackers)."""
    s = Sharing(tmp_path / 'juno', default_relays=[relay.url], session=session(marie))
    s.importer = Importer()
    yield s
    await s.close()


async def published(marie, juno, content=None, draft=DRAFT, uuid=None):
    p = (await marie.publish(draft, content or aria_card(), card_uuid=uuid or fmt.new_card_uuid())).publication
    juno.catalog.receive(p.event.to_dict())  # (as discovery would)
    return p


async def downloaded(juno, event_id):
    fetch = juno.download(event_id)
    await fetch.task
    return fetch


# --- downloading ---------------------------------------------------------------------------------------

async def test_downloading_a_card(marie, juno, tmp_path):
    content = aria_card()
    p = await published(marie, juno, content)
    seen = set()
    fetch = juno.download(p.event_id)
    assert juno.download(p.event_id) is fetch  # (running: the same one)
    while fetch.running:
        seen.add(fetch.stage)
        await asyncio.sleep(0.02)
    assert (fetch.stage, fetch.error, fetch.character_id) == ('done', '', 'aria-1')
    assert 'looking' in seen and fetch.message == 'Imported "Aria" (1)'
    assert juno.importer.added == [(p.file_name, content)]  # (the file announced, checked)
    record = juno.catalog.download(p.event_id)
    assert (record.character_id, record.infohash, record.file_name) == ('aria-1', p.torrent_infohash, p.file_name)
    assert record.fingerprint == 'fingerprint 1'  # (what the character was as added: the importer's)

    # Shared from here in turn: someone else gets it from Juno alone, Marie gone
    kept = tmp_path / 'juno' / 'sharing' / 'files' / p.file_name
    assert kept.read_bytes() == content and juno.sharing_status(juno.catalog.get(p.event_id)).state == 'seeding'
    assert not any((tmp_path / 'juno' / 'sharing' / 'downloading').iterdir())
    await marie.close()
    cass = Sharing(tmp_path / 'cass', session=session(juno))
    cass.importer = Importer()
    cass.catalog.receive(p.event.to_dict())
    try:
        got = await downloaded(cass, p.event_id)
        assert (got.stage, got.error) == ('done', '')
        assert cass.importer.added == [(p.file_name, content)]
    finally:
        await cass.close()


async def test_again(marie, juno, tmp_path):
    p = await published(marie, juno)
    await downloaded(juno, p.event_id)
    again = await downloaded(juno, p.event_id)  # (the file kept here: not downloaded again)
    assert (again.stage, again.character_id) == ('done', 'aria-2')
    assert juno.catalog.download(p.event_id).character_id == 'aria-2'  # (the latest one it became)
    assert [d.character_id for d in juno.catalog.downloads(character_id='aria-1')] == ['aria-1']  # (still from it)
    assert juno.catalog.downloaded([p.event_id, '0' * 64]) == {p.event_id}


async def test_without_sharing_downloads(marie, juno, tmp_path):
    files = tmp_path / 'juno' / 'sharing' / 'files'
    first = await published(marie, juno)
    await downloaded(juno, first.event_id)
    assert juno.session.sharing() == (first.torrent_infohash,)

    assert await juno.set_share_downloads(False) == 1  # (stopped: its file goes, the character stays)
    assert juno.session.sharing() == () and not (files / first.file_name).exists()
    assert juno.catalog.download(first.event_id) is not None

    second = await published(marie, juno, aria_card('Another.'))
    assert (await downloaded(juno, second.event_id)).stage == 'done'
    assert juno.session.sharing() == () and not (files / second.file_name).exists()
    assert not any((tmp_path / 'juno' / 'sharing' / 'downloading').iterdir())
    await juno.set_share_downloads(True)
    assert juno.share_downloads


async def test_withdrawn_no_longer_shared(marie, juno, relay, tmp_path):
    p = await published(marie, juno)
    await downloaded(juno, p.event_id)
    await marie.withdraw([p.event_id], reason='Replaced.')
    await juno.discover()  # (finds the withdrawal on the relay)
    assert juno.catalog.get(p.event_id).withdrawn.reason == 'Replaced.'
    assert juno.session.sharing() == ()
    assert not (tmp_path / 'juno' / 'sharing' / 'files' / p.file_name).exists()
    assert juno.catalog.download(p.event_id).character_id == 'aria-1'  # (kept: Juno has the character)
    failed = await downloaded(juno, p.event_id)
    assert (failed.stage, failed.error) == ('failed', 'Its publisher withdrew it: it can no longer be downloaded.')


async def test_blocked_no_longer_shared(marie, juno, tmp_path):
    p = await published(marie, juno)
    await downloaded(juno, p.event_id)
    await juno.block(p.publisher_id)
    assert juno.session.sharing() == () and not (tmp_path / 'juno' / 'sharing' / 'files' / p.file_name).exists()
    assert juno.catalog.download(p.event_id) is not None


async def test_withdrawn_while_the_app_was_off(marie, juno, tmp_path):
    p = await published(marie, juno)
    await downloaded(juno, p.event_id)
    await juno.close()
    withdrawal = fmt.build_withdrawal([p.event_id], secret=marie.identity().secret)
    later = Sharing(tmp_path / 'juno', session=session(marie))
    try:
        later.catalog.receive(withdrawal.event.to_dict())
        await later.resume_sharing()
        assert later.session.sharing() == () and not later.session.started
    finally:
        await later.close()


# --- what isn't downloaded ---------------------------------------------------------------------------

async def test_not_the_file_announced(marie, juno, tmp_path):
    """An announcement whose torrent is another file's: its description doesn't match, nothing is downloaded."""
    real = await published(marie, juno, aria_card('The real one.'))
    other = torrents.card_file(aria_card('Announced.'))
    lie = fmt.build_publication(replace(DRAFT, card_name='Lie'), replace(other, infohash=real.torrent_infohash),
                                card_uuid=fmt.new_card_uuid(), secret=nostr.new_secret())
    juno.catalog.receive(lie.event.to_dict())
    failed = await downloaded(juno, lie.event_id)
    assert failed.stage == 'failed' and 'isn\'t the file announced' in failed.error
    assert juno.importer.added == [] and juno.catalog.download(lie.event_id) is None
    assert not any((tmp_path / 'juno' / 'sharing' / 'downloading').iterdir())


async def test_nobody_shares_it(juno, tmp_path):
    lonely = fmt.build_publication(DRAFT, torrents.card_file(aria_card()), card_uuid=fmt.new_card_uuid(),
                                   secret=nostr.new_secret())
    juno.catalog.receive(lonely.event.to_dict())
    juno.session.find_timeout = 1
    failed = await downloaded(juno, lonely.event_id)
    assert failed.stage == 'failed' and failed.error.startswith('Nobody sharing it was found')


async def test_not_a_card(marie, juno, tmp_path):
    juno.importer = Importer(fail=CardError('This file isn\'t a character card.'))
    p = await published(marie, juno)
    failed = await downloaded(juno, p.event_id)
    assert (failed.stage, failed.error) == ('failed', 'It can\'t be added as a character: This file isn\'t a '
                                                      'character card.')
    assert juno.catalog.download(p.event_id) is None and juno.session.sharing() == ()
    assert not (tmp_path / 'juno' / 'sharing' / 'files' / p.file_name).exists()


async def test_beyond_the_safeguards(marie, juno, tmp_path, monkeypatch):
    """A card whose picture is too big (told by its header: nothing decoded) isn't added: said why, and its
    file deleted. (Marie's app wouldn't publish it: here, it's made to.)"""
    from rpapp.publishing import service
    from test_shared_card_safeguards import png_claiming
    with monkeypatch.context() as m:
        async def anything(content, **_):
            return None
        m.setattr(service, 'shared_card_or_error', anything)
        p = await published(marie, juno, png_claiming(20_000, 20_000))
    failed = await downloaded(juno, p.event_id)
    assert (failed.stage, failed.error) == ('failed', 'It can\'t be added: its picture is 20,000 × 20,000 pixels '
                                                      '(400 megapixels), and a shared card\'s is 50 megapixels at '
                                                      'most.')
    assert juno.importer.added == [] and juno.catalog.download(p.event_id) is None
    assert juno.session.sharing() == ()
    assert not (tmp_path / 'juno' / 'sharing' / 'files' / p.file_name).exists()
    assert not any((tmp_path / 'juno' / 'sharing' / 'downloading').rglob('*.png'))


async def test_closing_stops_downloads(juno, tmp_path):
    lonely = fmt.build_publication(DRAFT, torrents.card_file(aria_card()), card_uuid=fmt.new_card_uuid(),
                                   secret=nostr.new_secret())
    juno.catalog.receive(lonely.event.to_dict())
    fetch = juno.download(lonely.event_id)
    await asyncio.sleep(0.3)
    await juno.close()
    assert (fetch.stage, fetch.error) == ('failed', 'Stopped: the app was closing.')


def test_the_folder_of_files_before(tmp_path):
    """Before downloading, the files published were in sharing/published/: the folder is renamed."""
    old = tmp_path / 'sharing' / 'published'
    old.mkdir(parents=True)
    content = aria_card()
    (old / f'{hashlib.sha256(content).hexdigest()}.png').write_bytes(content)
    s = Sharing(tmp_path, session=session())
    s.catalog.close()
    assert not old.exists() and [p.name for p in s.files.iterdir()] == [f'{hashlib.sha256(content).hexdigest()}.png']


# --- who was found, and why they left ---------------------------------------------------------------------

async def test_who_was_found(tmp_path):
    """Someone found who doesn't share the card: what the download says of them as it looks, and when it
    gives up (what to look at, rather than "nobody" alone)."""
    from rpapp.publishing.seeder import FetchError
    other = TorrentSession(listen='127.0.0.1:0', dht=False, local_discovery=False, port_mapping=False, trackers=())
    other.start()  # (it shares nothing)
    s = TorrentSession(listen='127.0.0.1:0', dht=False, local_discovery=False, port_mapping=False, trackers=(),
                       peers=(('127.0.0.1', other.port),))
    s.settings.update(enable_outgoing_utp=False, enable_incoming_utp=False)
    notes = []
    try:
        with pytest.raises(FetchError) as e:
            await s.fetch('1' * 40, tmp_path, name='x.png', size=10, find_timeout=2,
                          on_progress=lambda now: notes.append(now.note))
    finally:
        s.close()
        other.close()
    said = str(e.value)
    assert said.startswith('Nobody sharing it was found')
    assert 'Found meanwhile, but none of them sent it: 127.0.0.1 (on your network): it closed the connection' in said
    assert any(n.startswith('127.0.0.1 (on your network) left: it closed the connection (it may not share this '
                            'card)') for n in notes)


def test_why_they_left_in_words():
    from types import SimpleNamespace

    from rpapp.publishing.seeder import PeerEvent, _found_meanwhile, _why_gone

    def alert(error='', reason='none'):
        return SimpleNamespace(error=SimpleNamespace(value=lambda: 1 if error else 0, message=lambda: error),
                               reason=f'close_reason_t.{reason}')
    assert _why_gone(alert('Connection refused')) == 'it refused the connection: nothing listens on that port any more'
    assert _why_gone(alert('Connection timed out')) == 'no answer: a firewall may be in the way'
    assert _why_gone(alert('No route to host')) == 'it can\'t be reached from here'
    assert _why_gone(alert(reason='self_connection')) == 'it was this computer itself'
    assert _why_gone(alert(reason='invalid_info_hash')) == 'it doesn\'t share this card'
    assert _why_gone(alert('Something new')) == 'Something new'
    assert PeerEvent('203.0.113.7', 6881, left=False).text == 'Connected to 203.0.113.7, asking for the card…'
    assert PeerEvent('192.168.1.20', 6881, left=True, why='no answer').who == '192.168.1.20 (on your network)'
    assert _found_meanwhile([PeerEvent('192.168.1.20', 6881, left=False)]) == ''  # (connected, never gone: said)
