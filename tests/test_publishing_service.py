"""Publishing and sharing files (rpapp/publishing/service.py, seeder.py), with a relay of the tests' own and
BitTorrent sessions on this computer only (no DHT, no router)."""
import asyncio
import hashlib
import os
import socket
import tempfile
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
from rpapp.publishing.service import HELD, Sharing, SharingError, is_local  # noqa: E402

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
    kept = tmp_path / 'sharing' / 'published' / p.file_name
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
        assert not (tmp_path / 'sharing' / 'published').exists()  # (nothing kept for nothing)
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
    assert not (tmp_path / 'sharing' / 'published' / p.file_name).exists()
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


async def test_stopping_and_a_file_two_publications_share(sharing, tmp_path):
    content = aria_card()
    a = await sharing.publish(DRAFT, content, card_uuid=fmt.new_card_uuid())
    b = await sharing.publish(PublicationDraft(publisher_name='Marie', card_name='Aria (copy)'), content,
                              card_uuid=fmt.new_card_uuid())
    assert a.publication.torrent_infohash == b.publication.torrent_infohash  # (the same file: one torrent)
    kept = tmp_path / 'sharing' / 'published' / a.publication.file_name
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

    (tmp_path / 'sharing' / 'published' / a.publication.file_name).write_bytes(b'changed')
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
