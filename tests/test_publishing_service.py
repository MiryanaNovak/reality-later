"""Publishing and sharing files (rpapp/publishing/service.py, seeder.py), with a relay of the tests' own and
BitTorrent sessions on this computer only (no DHT, no router)."""
import asyncio
import hashlib
import os
import socket
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip('coincurve', reason='coincurve (sharing cards) is installed for Python 3.13 and earlier')
pytest.importorskip('libtorrent', reason='libtorrent (sharing files) is installed for Python 3.13 and earlier')

from fake_relay import FakeRelay  # noqa: E402

from rpapp.cards import card_data, card_png  # noqa: E402
from rpapp.characters import new_character  # noqa: E402
from rpapp.publishing import announcements as fmt  # noqa: E402
from rpapp.publishing import nostr  # noqa: E402
from rpapp.publishing.announcements import PublicationDraft, Rejected  # noqa: E402
from rpapp.publishing.seeder import TorrentSession  # noqa: E402
from rpapp.publishing import service  # noqa: E402
from rpapp.publishing.catalog import Delivery  # noqa: E402
from rpapp.publishing.service import HELD, RETRY_AFTER, Sharing, SharingError, is_local, retry_at  # noqa: E402

DRAFT = PublicationDraft(publisher_name='Marie', card_name='Aria the Wanderer', card_description='A wandering bard.',
                         card_tags=('fantasy',), language='en')


def local_session() -> TorrentSession:
    return TorrentSession(listen='127.0.0.1:0', dht=False, local_discovery=False, port_mapping=False)


def aria_card(scenario: str = 'Aria sings.') -> bytes:
    aria = new_character('Aria')
    aria.scenario = scenario
    return card_png(card_data(aria), None)


@pytest.fixture
async def relay():
    async with FakeRelay() as fake:
        yield fake


@pytest.fixture
async def sharing(tmp_path, relay):
    s = Sharing(tmp_path, default_relays=[relay.url], session=local_session())
    s.create_identity()
    yield s
    await s.close()


# --- publishing ---------------------------------------------------------------------------------------

async def test_publishing_a_card(sharing, relay, tmp_path):
    uuid = fmt.new_card_uuid()
    content = aria_card()
    published = await sharing.publish(DRAFT, content, card_uuid=uuid)
    p = published.publication
    assert published.action == 'new_card' and p.card_uuid == uuid and p.publisher_id == sharing.identity().public_key
    assert [(d.relay_url, d.state, d.attempts) for d in published.deliveries] == [(relay.url, 'accepted', 1)]
    assert relay.events[p.event_id]['content'] == 'A wandering bard.'  # (on the relay)
    kept = tmp_path / 'sharing' / 'files' / p.file_name
    assert kept.read_bytes() == content and p.file_name == f'{hashlib.sha256(content).hexdigest()}.png'
    entry = sharing.catalog.get(p.event_id)
    assert entry is not None and entry.is_latest  # (in the catalog, as any other)
    status = sharing.sharing_status(entry)
    assert status.state == 'seeding' and status.infohash == p.torrent_infohash
    assert p.trackers  # (the default ones, in the announcement)


async def test_the_same_again_then_a_new_version(sharing, relay):
    uuid = fmt.new_card_uuid()
    first = await sharing.publish(DRAFT, aria_card(), card_uuid=uuid)
    again = await sharing.publish(DRAFT, aria_card(), card_uuid=uuid)  # (unchanged)
    assert again.action == 'resumed' and again.publication.event_id == first.publication.event_id
    assert [d.attempts for d in again.deliveries] == [1]  # (it had arrived: not sent again)

    changed = await sharing.publish(DRAFT, aria_card('Aria sings, and fights.'), card_uuid=uuid)
    assert changed.action == 'new_version' and changed.publication.event_id != first.publication.event_id
    versions = sharing.catalog.versions(sharing.identity().public_key, uuid)
    assert [v.publication.event_id for v in versions] == [changed.publication.event_id, first.publication.event_id]
    renamed = await sharing.publish(replace(DRAFT, publisher_name='Marie L.'), aria_card('Aria sings, and fights.'),
                                    card_uuid=uuid)
    assert renamed.action == 'new_version'  # (the published name is part of what's signed)
    assert len(relay.events) == 3


async def test_where_it_goes(sharing, relay):
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        closed = f'ws://127.0.0.1:{s.getsockname()[1]}'
    sharing.catalog.add_relay('wss://relay.example.com')  # (public: held while the format is a draft)
    sharing.catalog.add_relay(closed)                     # (local, but nobody answers: pending)
    sharing.catalog.add_relay('ws://192.168.1.20:7777', write=False)  # (read only: not sent to)
    published = await sharing.publish(DRAFT, aria_card(), card_uuid=fmt.new_card_uuid())
    states = {d.relay_url: (d.state, d.attempts) for d in published.deliveries}
    assert states == {relay.url: ('accepted', 1), 'wss://relay.example.com': ('held', 0), closed: ('pending', 1)}
    assert next(d for d in published.deliveries if d.state == 'held').message == HELD

    relay.refuse_events = 'blocked: members only'
    other = await sharing.publish(DRAFT, aria_card('Another.'), card_uuid=fmt.new_card_uuid())
    refused = next(d for d in other.deliveries if d.relay_url == relay.url)
    assert (refused.state, refused.message) == ('refused', 'blocked: members only')
    relay.refuse_events = None
    again = await sharing.send_again(other.publication.event_id)
    assert next(d for d in again if d.relay_url == relay.url).state == 'accepted'


def torrents_file(content: bytes):
    from rpapp.publishing import torrents
    return torrents.card_file(content)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_when_its_tried_again():
    def d(state, message='', attempts=1):
        return Delivery('ws://127.0.0.1:7777', state, attempts, 1000, message)
    assert retry_at(d('pending')) == 1000 + RETRY_AFTER[0]
    assert [retry_at(d('pending', attempts=n)) - 1000 for n in (2, 3, 4, 5, 9)] == \
        [*RETRY_AFTER[1:], RETRY_AFTER[-1]]  # (longer each time, then every last one)
    assert retry_at(d('refused', 'rate-limited: slow down')) and retry_at(d('refused', 'error: database busy'))
    for state, message in (('refused', 'blocked: members only'), ('refused', 'invalid: bad tag'), ('held', HELD),
                           ('accepted', '')):
        assert retry_at(d(state, message)) is None  # (refused for good, held, or there)


async def test_sent_again_by_itself(sharing, relay):
    """What didn't arrive but may yet is sent again when it's due: to a relay that couldn't be reached, or
    that said to try later; not to one that refused it for good, nor to a public one while the format is a
    draft. A publication withdrawn since isn't: its withdrawal is."""
    port = free_port()
    down = f'ws://127.0.0.1:{port}'
    sharing.catalog.add_relay(down)
    sharing.catalog.add_relay('wss://relay.example.com')  # (held)
    relay.refuse_events = 'rate-limited: slow down'
    p = (await sharing.publish(DRAFT, aria_card(), card_uuid=fmt.new_card_uuid())).publication
    assert {d.relay_url: d.state for d in sharing.catalog.deliveries(p.event_id)} == \
        {relay.url: 'refused', down: 'pending', 'wss://relay.example.com': 'held'}
    assert await sharing.retry_deliveries() == 0  # (not due yet: a minute after)

    relay.refuse_events = None
    async with FakeRelay(port=port) as back:  # (up again)
        assert await sharing.retry_deliveries(now=time.time() + RETRY_AFTER[0]) == 2  # (to both relays)
        assert {d.relay_url: (d.state, d.attempts) for d in sharing.catalog.deliveries(p.event_id)} == \
            {relay.url: ('accepted', 2), down: ('accepted', 2), 'wss://relay.example.com': ('held', 0)}
        assert p.event_id in back.events
    sharing.catalog.remove_relay(down)  # (not written to: not tried again)

    relay.refuse_events = 'blocked: members only'
    blocked = (await sharing.publish(DRAFT, aria_card('Blocked.'), card_uuid=fmt.new_card_uuid())).publication
    relay.refuse_events = None
    assert await sharing.retry_deliveries(now=time.time() + 10 ** 6) == 0
    assert {d.relay_url: d.state for d in sharing.catalog.deliveries(blocked.event_id)}[relay.url] == 'refused'

    relay.refuse_events = 'error: database busy'
    gone = (await sharing.publish(DRAFT, aria_card('Gone.'), card_uuid=fmt.new_card_uuid())).publication
    sent = {d.relay_url: d.state for d in await sharing.withdraw([gone.event_id])}
    assert sent == {relay.url: 'refused', 'wss://relay.example.com': 'held'}
    relay.refuse_events = None
    assert await sharing.retry_deliveries(now=time.time() + 10 ** 6) == 1  # (its withdrawal: not it)
    withdrawal_id = sharing.catalog.get(gone.event_id).withdrawn.withdrawal_id
    assert {d.relay_url: d.state for d in sharing.catalog.deliveries(withdrawal_id)} == \
        {relay.url: 'accepted', 'wss://relay.example.com': 'held'}
    assert gone.event_id not in relay.events
    assert {d.relay_url: d.state for d in sharing.catalog.deliveries(gone.event_id)}[relay.url] == 'refused'


async def until(condition, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError('never happened')


async def test_a_relay_added_gets_your_cards(sharing, relay):
    """Your cards not withdrawn (each one's latest version), and your withdrawals, go to a relay added, in
    the background, on one connection; not someone else's cards, nor the cards withdrawn."""
    uuid = fmt.new_card_uuid()
    first = (await sharing.publish(DRAFT, aria_card(), card_uuid=uuid)).publication
    latest = (await sharing.publish(DRAFT, aria_card('Aria sings, and fights.'), card_uuid=uuid)).publication
    other = (await sharing.publish(DRAFT, aria_card('Bram.'), card_uuid=fmt.new_card_uuid())).publication
    gone = (await sharing.publish(DRAFT, aria_card('Gone.'), card_uuid=fmt.new_card_uuid())).publication
    await sharing.withdraw([gone.event_id])
    withdrawal_id = sharing.catalog.get(gone.event_id).withdrawn.withdrawal_id
    theirs = fmt.build_publication(DRAFT, torrents_file(b'theirs'), card_uuid=fmt.new_card_uuid(),
                                   secret=nostr.new_secret())
    sharing.catalog.receive(theirs.event.to_dict())

    async with FakeRelay() as new:
        assert await sharing.add_relay(new.url) == 2
        def arrived(event_id):
            return {d.relay_url: d.state for d in sharing.catalog.deliveries(event_id)}.get(new.url) == 'accepted'
        await until(lambda: all(arrived(i) for i in (latest.event_id, other.event_id, withdrawal_id)))
        assert set(new.events) == {latest.event_id, other.event_id, withdrawal_id}
        assert first.event_id not in new.events and gone.event_id not in new.events
        assert new.connections == 1  # (all of them on one connection)
        assert await sharing.add_relay(new.url) == 0  # (added again: there already)

    assert await sharing.add_relay('wss://relay.example.com') == 0  # (public: held while it's a draft)
    await until(lambda: 'wss://relay.example.com' in
                {d.relay_url for d in sharing.catalog.deliveries(latest.event_id)})
    assert {d.relay_url: d.state for d in sharing.catalog.deliveries(latest.event_id)}['wss://relay.example.com'] \
        == 'held'


async def test_a_relay_added_without_a_key(tmp_path, relay):
    s = Sharing(tmp_path, default_relays=[relay.url], session=local_session())
    try:
        assert await s.add_relay('ws://127.0.0.1:7777') == 0  # (nothing published from here)
        assert 'ws://127.0.0.1:7777' in {r.url for r in s.catalog.relays()}
    finally:
        await s.close()


async def test_tried_again_while_the_app_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'RETRY_AFTER', (0,))
    monkeypatch.setattr(service, 'RETRY_CHECK', 0.05)
    port = free_port()
    s = Sharing(tmp_path, default_relays=[f'ws://127.0.0.1:{port}'], session=local_session())
    s.create_identity()
    try:
        p = (await s.publish(DRAFT, aria_card(), card_uuid=fmt.new_card_uuid())).publication
        assert s.catalog.deliveries(p.event_id)[0].state == 'pending'
        await s.resume_sharing()  # (as when the app starts)
        async with FakeRelay(port=port) as back:
            await until(lambda: s.catalog.deliveries(p.event_id)[0].state == 'accepted')  # (its answer, kept)
            assert p.event_id in back.events
    finally:
        await s.close()


async def test_beyond_the_safeguards(sharing, relay, tmp_path):
    """A card others' apps wouldn't add (its picture too big, its file too big) isn't published: said why,
    and nothing sent, nor shared."""
    from test_shared_card_safeguards import png_claiming
    with pytest.raises(SharingError) as e:
        await sharing.publish(DRAFT, png_claiming(20_000, 20_000), card_uuid=fmt.new_card_uuid())
    assert str(e.value) == ('It can\'t be published: its picture is 20,000 × 20,000 pixels (400 megapixels), and a '
                            'shared card\'s is 50 megapixels at most.')
    with pytest.raises(SharingError, match='It can\'t be published: its card can\'t be read'):
        await sharing.publish(DRAFT, b'\x89PNG\r\n\x1a\nx', card_uuid=fmt.new_card_uuid())
    assert relay.events == {} and sharing.session.sharing() == ()
    files = tmp_path / 'sharing' / 'files'
    assert not files.exists() or not any(files.iterdir())


async def test_public_relays_once_its_final(tmp_path, relay):
    s = Sharing(tmp_path, default_relays=['wss://relay.example.invalid'], session=local_session(),
                public_publishing=True)
    s.create_identity()
    try:
        published = await s.publish(DRAFT, aria_card(), card_uuid=fmt.new_card_uuid())
        assert [d.state for d in published.deliveries] == ['pending']  # (tried: unreachable)
    finally:
        await s.close()


async def test_what_cant_be_published(tmp_path, relay):
    s = Sharing(tmp_path, default_relays=[relay.url], session=local_session())
    try:
        with pytest.raises(SharingError, match='publisher key'):
            await s.publish(DRAFT, aria_card(), card_uuid=fmt.new_card_uuid())
        s.create_identity()
        with pytest.raises(Rejected, match='too_long:title'):
            await s.publish(PublicationDraft(publisher_name='M', card_name='x' * 201), aria_card(),
                            card_uuid=fmt.new_card_uuid())
        assert not (tmp_path / 'sharing' / 'files').exists()  # (nothing kept for nothing)
    finally:
        await s.close()


# --- withdrawing, sharing the files --------------------------------------------------------------------

async def test_withdrawing(sharing, relay, tmp_path):
    published = await sharing.publish(DRAFT, aria_card(), card_uuid=fmt.new_card_uuid())
    p = published.publication
    deliveries = await sharing.withdraw([p.event_id], reason='Published by mistake.')
    assert [d.state for d in deliveries] == ['accepted']
    entry = sharing.catalog.get(p.event_id)
    assert entry.withdrawn.reason == 'Published by mistake.'
    assert sharing.sharing_status(entry) is None  # (no longer shared)
    assert not (tmp_path / 'sharing' / 'files' / p.file_name).exists()
    assert any(e['kind'] == 5 for e in relay.events.values())

    with pytest.raises(SharingError, match='withdrawn'):  # (the relays deleted it: not sent to them again)
        await sharing.send_again(p.event_id)
    assert [d.state for d in await sharing.send_again(entry.withdrawn.withdrawal_id)] == ['accepted']

    others = Sharing(tmp_path / 'other', default_relays=[relay.url], session=local_session())
    others.create_identity()
    others.catalog.receive(p.event.to_dict())
    try:
        with pytest.raises(SharingError, match='your own'):
            await others.withdraw([p.event_id])
    finally:
        await others.close()


async def test_withdrawing_with_a_key_replaced_since(sharing, relay):
    old = sharing.identity()
    published = await sharing.publish(DRAFT, aria_card(), card_uuid=fmt.new_card_uuid())
    sharing.import_identity(nostr.to_nsec(nostr.new_secret()), replace=True)
    assert sharing.own_keys() == (sharing.identity().public_key, old.public_key)
    deliveries = await sharing.withdraw([published.publication.event_id], reason='Old.')
    assert [d.state for d in deliveries] == ['accepted']
    [withdrawal] = [e for e in relay.events.values() if e['kind'] == 5]
    assert withdrawal['pubkey'] == old.public_key  # (signed by the key that published it)


async def test_stopping_and_a_file_two_publications_share(sharing, tmp_path):
    content = aria_card()
    a = await sharing.publish(DRAFT, content, card_uuid=fmt.new_card_uuid())
    b = await sharing.publish(PublicationDraft(publisher_name='Marie', card_name='Aria (copy)'), content,
                              card_uuid=fmt.new_card_uuid())
    assert a.publication.torrent_infohash == b.publication.torrent_infohash  # (the same file: one torrent)
    kept = tmp_path / 'sharing' / 'files' / a.publication.file_name
    await sharing.stop_sharing(a.publication.event_id)
    assert kept.exists() and sharing.session.status(a.publication.torrent_infohash) is not None  # (b needs it)
    await sharing.stop_sharing(b.publication.event_id)
    assert not kept.exists() and sharing.session.status(a.publication.torrent_infohash) is None


async def test_sharing_again_when_the_app_starts(tmp_path, relay, monkeypatch):
    first = Sharing(tmp_path, default_relays=[relay.url], session=local_session())
    first.create_identity()
    a = await first.publish(DRAFT, aria_card(), card_uuid=fmt.new_card_uuid())
    b = await first.publish(PublicationDraft(publisher_name='Marie', card_name='Bram'), aria_card('Bram.'),
                            card_uuid=fmt.new_card_uuid())
    await first.stop_sharing(b.publication.event_id)
    await first.close()

    later = Sharing(tmp_path, session=local_session())
    try:
        await later.resume_sharing()
        assert later.session.sharing() == (a.publication.torrent_infohash,)  # (not the one stopped)
        assert later.sharing_status(later.catalog.get(a.publication.event_id)).state == 'seeding'
    finally:
        await later.close()

    without = Sharing(tmp_path, session=local_session())  # (libtorrent missing: said, not raised)
    monkeypatch.setattr('rpapp.publishing.service.torrent_problem', lambda: 'a library it needs is missing (lt)')
    try:
        await without.resume_sharing()
        problem = without.sharing_status(without.catalog.get(a.publication.event_id))
        assert 'missing (lt)' in problem and not without.session.started
    finally:
        monkeypatch.undo()
        await without.close()

    (tmp_path / 'sharing' / 'files' / a.publication.file_name).write_bytes(b'changed')
    again = Sharing(tmp_path, session=local_session())
    try:
        await again.resume_sharing()
        problem = again.sharing_status(again.catalog.get(a.publication.event_id))
        assert isinstance(problem, str) and 'missing or changed' in problem and again.session.sharing() == ()
    finally:
        await again.close()


async def test_another_computer_gets_the_file(sharing):
    """What's published is really shared: another BitTorrent session gets the file from its infohash."""
    import libtorrent as lt
    content = aria_card() + os.urandom(150_000)  # (more than two pieces)
    published = await sharing.publish(DRAFT, content, card_uuid=fmt.new_card_uuid())
    p = published.publication
    with tempfile.TemporaryDirectory() as folder:
        other = lt.session({'listen_interfaces': '127.0.0.1:0', 'enable_dht': False, 'enable_lsd': False,
                            'enable_upnp': False, 'enable_natpmp': False})
        params = lt.parse_magnet_uri(f'magnet:?xt=urn:btih:{p.torrent_infohash}')
        params.save_path = folder
        handle = other.add_torrent(params)
        handle.connect_peer(('127.0.0.1', sharing.session.port))
        for _ in range(100):
            if handle.status().is_seeding:
                break
            await asyncio.sleep(0.1)
        got = Path(folder) / p.file_name
        assert got.read_bytes() == content
        assert sharing.session.status(p.torrent_infohash).uploaded >= len(content)


def test_local_relays():
    for url in ('ws://127.0.0.1:7777', 'ws://localhost:7777', 'ws://192.168.1.20:7777', 'ws://10.0.0.5',
                'ws://[::1]:7777', 'ws://nas.local:7777', 'ws://[fe80::1]:7777'):
        assert is_local(url), url
    for url in ('wss://relay.damus.io', 'wss://8.8.8.8', 'wss://localhost.example.com'):
        assert not is_local(url), url


async def test_the_key(tmp_path):
    s = Sharing(tmp_path, session=local_session())
    try:
        assert s.identity() is None
        me = s.create_identity()
        assert s.identity() == me and s.key_file == tmp_path / 'sharing' / 'nostr-key.txt'
        other = nostr.new_secret()
        assert s.import_identity(nostr.to_nsec(other), replace=True).secret == other
    finally:
        await s.close()


async def test_a_tracker_that_fails_isnt_a_failure(tmp_path, caplog):
    """Trackers come and go (and the DHT finds peers without them): their errors are logged, not shown."""
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        closed = f'http://127.0.0.1:{s.getsockname()[1]}/announce'
    session = TorrentSession(listen='127.0.0.1:0', dht=False, local_discovery=False, port_mapping=False,
                             trackers=(closed,))
    content = aria_card()
    from rpapp.publishing.torrents import card_file
    file = card_file(content)
    (tmp_path / file.name).write_bytes(content)
    caplog.set_level('DEBUG', logger='rpapp.publishing.seeder')
    try:
        infohash = session.seed(file.metainfo(), tmp_path)
        for _ in range(100):
            if '/announce' in caplog.text:
                break
            session.status(infohash)
            await asyncio.sleep(0.05)
        assert '/announce' in caplog.text  # (it did fail)
        status = session.status(infohash)
        assert status.state == 'seeding' and status.error == ''
    finally:
        session.close()
