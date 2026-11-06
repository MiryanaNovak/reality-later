"""Discovery (rpapp/publishing/discovery.py) and the relay client (relay.py), against relays of the tests'
own (fake_relay.py)."""
import asyncio
import json
import socket
from pathlib import Path

import pytest

pytest.importorskip('coincurve', reason='coincurve (sharing cards) is installed for Python 3.13 and earlier')

from fake_relay import FakeRelay  # noqa: E402

from rpapp.publishing import announcements as fmt  # noqa: E402
from rpapp.publishing import nostr, torrents  # noqa: E402
from rpapp.publishing.announcements import PublicationDraft  # noqa: E402
from rpapp.publishing.catalog import Catalog, CardQuery  # noqa: E402
from rpapp.publishing.discovery import MARGIN, Discovery  # noqa: E402
from rpapp.publishing.relay import Relay, RelayError  # noqa: E402

VECTORS = json.loads((Path(__file__).parent / 'card_announcements.json').read_text(encoding='utf-8'))
NOW = VECTORS['now']
KEY1 = bytes.fromhex(VECTORS['keys']['publisher_1']['secret'])
PUB1 = nostr.public_key(KEY1)
T0 = 1767225600


def valid() -> list[dict]:
    return [v['event'] for v in VECTORS['valid']]


def cards(n: int, *, secret: bytes = KEY1, start: int = T0, step: int = 60) -> list[dict]:
    """n publications, one card each, `step` seconds apart."""
    return [fmt.build_publication(PublicationDraft(publisher_name='Marie', card_name=f'Card {i}'),
                                  torrents.card_file(f'{start} {i}'.encode()), card_uuid=fmt.new_card_uuid(),
                                  secret=secret, created_at=start + i * step).event.to_dict() for i in range(n)]


@pytest.fixture
def catalog(tmp_path):
    with Catalog(tmp_path / 'catalog.sqlite3') as c:
        yield c


def discovery(catalog, **options) -> Discovery:
    return Discovery(catalog, clock=lambda: NOW, **options)


def known(catalog) -> set[str]:
    return {e.publication.event_id for e in catalog.cards(CardQuery(include_versions=True, limit=500)).items}


# --- discovering ------------------------------------------------------------------------------------

async def test_a_first_discovery_then_whats_new(catalog):
    async with FakeRelay() as relay:
        invalid = [v['event'] for v in VECTORS['invalid'] if v['name'] in (
            'bad_id', 'wrong_kind', 'not_a_card', 'missing_schema', 'future_schema', 'missing_uuid')]
        relay.add(*valid(), VECTORS['retraction']['event'], *invalid)
        catalog.add_relay(relay.url)
        progress = []
        result = await discovery(catalog).discover(on_progress=lambda url, n: progress.append((url, n)))
        [r] = result.relays
        assert r.success and not r.truncated and not result.incomplete and r.error is None
        assert (r.added, r.withdrawals) == (len(valid()), 1)
        assert result.added == len(valid()) and result.withdrawn == (VECTORS['retraction']['withdraws'][0],)
        assert r.rejected == 4  # (two of the invalid ones aren't cards: the relay doesn't send them)
        withdrawn = set(VECTORS['retraction']['withdraws'])
        assert known(catalog) == {e['id'] for e in valid()} - withdrawn
        assert all(catalog.get(i).withdrawn for i in withdrawn)
        assert progress and progress[-1] == (relay.url, r.received)
        assert all('since' not in q for q in relay.queries)  # (the first time: everything)
        assert {q['kinds'][0] for q in relay.queries} == {2003, 5}
        assert catalog.relay(relay.url).checkpoint == NOW and catalog.relay(relay.url).last_error is None

        # Next time: only from 24 hours before the last one, and what's new
        relay.queries.clear()
        relay.add(*cards(2, start=NOW - 3600))
        again = await discovery(catalog).discover()
        assert again.added == 2 and again.relays[0].received == 3  # (and one dated 15 minutes ahead, again)
        assert {q['since'] for q in relay.queries} == {NOW - MARGIN}


async def test_pages(catalog):
    events = cards(7) + cards(2, start=T0 + 120, step=0)  # (three in the same second, across two pages)
    async with FakeRelay() as relay:
        relay.add(*events)
        catalog.add_relay(relay.url)
        result = await discovery(catalog, page_size=3).discover()
        assert result.added == 9 and not result.incomplete and known(catalog) == {e['id'] for e in events}
        until = [q.get('until') for q in relay.queries if q['kinds'] == [2003]]
        assert until[0] is None and until[1:] == sorted(until[1:], reverse=True)  # (going back in time)


async def test_a_relay_leaving_untils_second_out(catalog):
    """Some relays answer until as if it were "before" (NIP-01: "up to, included"): nothing missed either."""
    events = cards(7) + cards(2, start=T0 + 120, step=0) + cards(3, start=T0 + 2000, step=0)
    async with FakeRelay(misbehave='exclusive_until') as relay:
        relay.add(*events)
        result = await discovery(catalog, page_size=3).discover(relays=[relay.url])
        assert not result.incomplete and result.added == 12 and known(catalog) == {e['id'] for e in events}


async def test_a_second_fuller_than_a_page(catalog):
    """More events in one second than a page holds: a page of them, then on from the second before."""
    older = cards(4)
    crowded = cards(5, start=T0 + 3000, step=0)
    async with FakeRelay() as relay:
        relay.add(*older, *crowded)
        result = await discovery(catalog, page_size=3).discover(relays=[relay.url])
        assert not result.incomplete and result.added == 3 + 4
        assert {e['id'] for e in older} <= known(catalog)  # (not stuck in that second)


async def test_a_relay_giving_less_than_asked(catalog):
    async with FakeRelay(max_limit=2) as relay:
        relay.add(*cards(9))
        catalog.add_relay(relay.url)
        result = await discovery(catalog, page_size=5).discover()
        assert result.added == 9 and not result.incomplete


async def test_too_much_at_once(catalog):
    """At most max_pages pages per relay: the rest is its gap, got by the next discoveries."""
    events = cards(10)
    async with FakeRelay() as relay:
        relay.add(*events)
        catalog.add_relay(relay.url)
        d = discovery(catalog, page_size=3, max_pages=2)
        first = await d.discover()
        assert first.relays[0].truncated and first.incomplete and first.added == 5  # (3, then 2 new + 1 again)
        gap = catalog.relay(relay.url).gap
        assert gap is not None and gap[0] == 0
        while catalog.relay(relay.url).gap is not None:
            await d.discover()
        assert known(catalog) == {e['id'] for e in events}
        assert catalog.relay(relay.url).checkpoint == NOW


async def test_relays_that_fail(catalog):
    with socket.socket() as s:  # (a port nobody listens on)
        s.bind(('127.0.0.1', 0))
        closed = f'ws://127.0.0.1:{s.getsockname()[1]}'
    async with FakeRelay() as good, FakeRelay(misbehave='closed') as refusing, \
            FakeRelay(misbehave='hangup') as hanging_up, FakeRelay(misbehave='silent') as silent:
        for relay in (good, refusing, hanging_up, silent):
            relay.add(*valid())
            catalog.add_relay(relay.url)
        catalog.add_relay(closed)
        result = await discovery(catalog, timeout=0.5).discover()
        by_url = {r.relay_url: r for r in result.relays}
        assert by_url[good.url].success and result.added == len(valid())  # (the others don't hold it back)
        assert 'auth-required' in by_url[refusing.url].error and 'connect' in by_url[closed].error
        assert 'broke' in by_url[hanging_up.url].error and 'answer' in by_url[silent.url].error
        assert result.incomplete
        for url in (refusing.url, closed, hanging_up.url, silent.url):
            state = catalog.relay(url)
            assert state.checkpoint is None and state.last_error == by_url[url].error  # (nothing saved as done)
        assert catalog.relay(good.url).checkpoint == NOW


async def test_a_relay_sending_nonsense(catalog):
    async with FakeRelay(misbehave='junk') as relay:
        relay.add(*valid())
        result = await discovery(catalog).discover(relays=[relay.url])  # (a relay the catalog doesn't keep)
        assert result.added == len(valid()) and result.relays[0].success
        assert catalog.relay(relay.url) is None  # (nothing saved about it)


async def test_a_relay_ignoring_until(catalog):
    async with FakeRelay(misbehave='ignore_until') as relay:
        relay.add(*cards(8))
        result = await discovery(catalog, page_size=3).discover(relays=[relay.url])
        assert result.relays[0].success and len(relay.queries) < 10  # (it stops rather than going round)


async def test_the_same_from_two_relays(catalog):
    async with FakeRelay() as a, FakeRelay() as b:
        a.add(*valid())
        b.add(*valid())
        result = await discovery(catalog).discover(relays=[a.url, b.url, a.url + '/'])
        assert len(result.relays) == 2 and result.added == len(valid())  # (counted once)


async def test_a_full_discovery(catalog):
    async with FakeRelay() as relay:
        catalog.add_relay(relay.url)
        await discovery(catalog).discover()
        relay.add(*cards(3, start=T0))  # (older than the checkpoint: a normal discovery wouldn't ask)
        assert (await discovery(catalog).discover()).added == 0
        assert (await discovery(catalog).discover(full=True)).added == 3


async def test_one_publishers_after_unblocking(catalog):
    other = nostr.new_secret()
    async with FakeRelay() as relay:
        relay.add(*cards(3), *cards(2, secret=other))
        catalog.add_relay(relay.url)
        d = discovery(catalog)
        await d.discover()
        catalog.block([PUB1])
        catalog.unblock([PUB1])
        assert (await d.discover()).added == 0  # (already sent: a normal discovery doesn't ask again)
        checkpoint = catalog.relay(relay.url).checkpoint
        result = await d.discover_publisher(PUB1)
        assert result.added == 3 and all(q['authors'] == [PUB1] for q in relay.queries[-2:])
        assert catalog.relay(relay.url).checkpoint == checkpoint


async def test_a_blocked_publisher_is_refused(catalog):
    async with FakeRelay() as relay:
        relay.add(*cards(2))
        catalog.block([PUB1])
        result = await discovery(catalog).discover(relays=[relay.url])
        assert result.added == 0 and result.relays[0].rejected == 2


async def test_cancelling_saves_nothing(catalog):
    async with FakeRelay(misbehave='silent') as relay:
        relay.add(*valid())
        catalog.add_relay(relay.url)
        task = asyncio.create_task(discovery(catalog, timeout=30).discover())
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert catalog.relay(relay.url).checkpoint is None


# --- the relay client -------------------------------------------------------------------------------

async def test_publishing_to_a_relay():
    event = nostr.Event.from_dict(valid()[0])
    async with FakeRelay() as fake:
        async with Relay(fake.url) as relay:
            assert await relay.publish(event) == (True, '')
            accepted, said = await relay.publish(event)
            assert accepted and said.startswith('duplicate:')
            fake.misbehave = 'duplicate_false'  # (some relays say false, for one they have already)
            assert (await relay.publish(event))[0]
            fake.misbehave = None
            fake.refuse_events = 'blocked: not on the list'
            other = nostr.Event.from_dict(valid()[1])
            assert await relay.publish(other) == (False, 'blocked: not on the list')
            assert await relay.query([{'ids': [event.id]}]) == [valid()[0]]


async def test_relay_addresses(catalog):
    for bad in ('https://relay.example.com', 'relay.example.com', 'wss://'):
        with pytest.raises(ValueError, match='relay'):
            catalog.add_relay(bad)
    info = catalog.add_relay('WSS://Relay.Example.com/', write=False)
    assert info.url == 'wss://relay.example.com' and info.read and not info.write
    assert catalog.add_relay('wss://relay.example.com', read=False).read is False  # (changed, not added twice)
    assert len(catalog.relays()) == 1
    catalog.remove_relay('wss://relay.example.com/')
    assert catalog.relays() == ()
    with pytest.raises(RelayError, match='connect'):
        async with Relay('ws://127.0.0.1:9', timeout=0.5):
            pass
