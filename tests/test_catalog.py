"""The catalog of shared cards (rpapp/publishing/catalog.py): what's kept, worked out, found and blocked."""
import json
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip('coincurve', reason='coincurve (sharing cards) is installed for Python 3.13 and earlier')

from rpapp.publishing import announcements as fmt  # noqa: E402
from rpapp.publishing import nostr, torrents  # noqa: E402
from rpapp.publishing.announcements import PublicationDraft  # noqa: E402
from rpapp.publishing.catalog import (MIGRATIONS, Catalog, CatalogError, CardQuery, PublisherQuery,  # noqa: E402
                                      fold)

VECTORS = json.loads((Path(__file__).parent / 'card_announcements.json').read_text(encoding='utf-8'))
NOW, LIMIT = VECTORS['now'], VECTORS['size_limit']
KEY1 = bytes.fromhex(VECTORS['keys']['publisher_1']['secret'])
KEY2 = bytes.fromhex(VECTORS['keys']['publisher_2']['secret'])
PUB1, PUB2 = nostr.public_key(KEY1), nostr.public_key(KEY2)
T0 = 1767225600  # 2026-01-01


def event(name: str) -> dict:
    return next(v['event'] for v in VECTORS['valid'] if v['name'] == name)


@pytest.fixture
def catalog(tmp_path):
    with Catalog(tmp_path / 'sharing' / 'catalog.sqlite3') as c:
        yield c


def publish(catalog, secret, *, at, uuid=None, title='A card', name='Marie', tags=(), rating='sfw', language='en',
            content=None, description='', card_format='chara_card_v2'):
    """A publication made and received; returns it."""
    content = content or f'{title} {at} {uuid}'.encode()
    p = fmt.build_publication(PublicationDraft(publisher_name=name, card_name=title, card_description=description,
                                               card_tags=tags, content_rating=rating, language=language,
                                               card_format=card_format),
                              torrents.card_file(content), card_uuid=uuid or fmt.new_card_uuid(), secret=secret,
                              created_at=at)
    assert catalog.receive(p.event.to_dict(), now=NOW).status == 'added'
    return p


def ids(page) -> list[str]:
    return [e.publication.event_id for e in page.items]


# --- receiving --------------------------------------------------------------------------------------

def test_receiving_the_vectors(catalog):
    got = catalog.receive_all([v['event'] for v in VECTORS['valid']], now=NOW, size_limit=LIMIT)
    assert [r.status for r in got] == ['added'] * len(VECTORS['valid'])
    again = catalog.receive_all([v['event'] for v in VECTORS['valid']], now=NOW)  # (from another relay)
    assert [r.status for r in again] == ['duplicate'] * len(VECTORS['valid'])
    bad = catalog.receive_all([v['event'] for v in VECTORS['invalid']], now=NOW, size_limit=LIMIT)
    assert [(r.status, r.code) for r in bad] == [('rejected', v['error']) for v in VECTORS['invalid']]
    assert bad[0].event_id == VECTORS['invalid'][0]['event']['id']
    entry = catalog.get(event('example')['id'])
    expected = next(v['expected'] for v in VECTORS['valid'] if v['name'] == 'example')
    assert entry.publication.card_tags == tuple(expected['tags'])
    assert entry.publication.trackers == tuple(expected['trackers'])
    assert json.loads(entry.publication.event.to_json()) == event('example')  # (kept as received)
    assert catalog.receive('not an event').code == 'malformed' and catalog.receive({'kind': 2003}).code == 'malformed'


def test_versions_and_names(catalog):
    catalog.receive_all([event('example'), event('new_version'), event('other_publisher')], now=NOW)
    uuid = '550e8400-e29b-41d4-a716-446655440000'
    first, second = catalog.versions(PUB1, uuid)
    assert second.publication.event_id == event('example')['id']
    assert first.publication.event_id == event('new_version')['id']
    assert first.is_latest and not second.is_latest
    assert first.publisher_name == second.publisher_name == 'Marie L.'  # (the current name, on every version)
    assert second.publication.published_name == 'Marie'                  # (the one it was published with)

    # The same UUID under another key: another card, and another publisher of the same name
    [other] = catalog.versions(PUB2, uuid)
    assert other.is_latest and other.publisher_name == 'Marie'
    cards = catalog.cards()
    assert ids(cards) == [event('other_publisher')['id'], event('new_version')['id']] and cards.next_cursor is None

    me = catalog.publisher(PUB1)
    assert (me.current_name, me.previous_names, me.cards) == ('Marie L.', ('Marie',), 1)
    assert me.npub.startswith('npub1') and me.last_published_at == event('new_version')['created_at']
    assert catalog.publisher('0' * 64) is None


def test_the_newest_wins_whatever_comes_first(catalog):
    newer = event('new_version')
    catalog.receive(newer, now=NOW)
    catalog.receive(event('example'), now=NOW)  # (older, received after)
    assert [e.publication.event_id for e in catalog.cards().items] == [newer['id']]
    assert catalog.publisher(PUB1).current_name == 'Marie L.'


def test_equal_dates_the_smallest_id_wins(catalog):
    uuid = fmt.new_card_uuid()
    a = publish(catalog, KEY1, at=T0, uuid=uuid, title='One', name='First')
    b = publish(catalog, KEY1, at=T0, uuid=uuid, title='Two', name='Second')
    smallest = min(a, b, key=lambda p: p.event_id)
    assert [e.publication.event_id for e in catalog.cards().items] == [smallest.event_id]
    assert catalog.publisher(PUB1).current_name == smallest.published_name


# --- withdrawals --------------------------------------------------------------------------------------

def test_a_withdrawal(catalog):
    catalog.receive_all([event('example'), event('new_version')], now=NOW)
    w = fmt.build_withdrawal([event('new_version')['id']], secret=KEY1, reason='Wrong name.', created_at=NOW)
    got = catalog.receive(w.event.to_dict(), now=NOW)
    assert got.status == 'withdrawal' and got.withdrawn == (event('new_version')['id'],)  # (to stop seeding it)
    assert catalog.receive(w.event.to_dict(), now=NOW).status == 'duplicate'

    # Not listed, nor a version; the one before is the latest again, with its name
    assert ids(catalog.cards()) == [event('example')['id']]
    assert catalog.cards().items[0].is_latest and catalog.cards().items[0].publisher_name == 'Marie'
    me = catalog.publisher(PUB1)
    assert (me.current_name, me.previous_names) == ('Marie', ())  # ("Marie L." was only in what's withdrawn)
    uuid = catalog.cards().items[0].publication.card_uuid
    assert [e.publication.event_id for e in catalog.versions(PUB1, uuid)] == [event('example')['id']]

    # Those who have it: when, and why
    entry = catalog.get(event('new_version')['id'])
    assert entry.withdrawn.reason == 'Wrong name.' and entry.withdrawn.at == NOW and not entry.is_latest
    everything = catalog.versions(PUB1, entry.publication.card_uuid, include_withdrawn=True)
    assert [e.withdrawn is not None for e in everything] == [True, False]
    assert catalog.cards(CardQuery(publisher_name='marie l')).items == ()


def test_words_from_their_start(catalog):
    k2, k3 = nostr.new_secret(), nostr.new_secret()
    sera = publish(catalog, KEY1, at=T0 + 10, title='Seraphina, forest guardian', name='Marie',
                   description='A dryad who guards the Elderwood. Speaks with owls.')
    luc = publish(catalog, k2, at=T0 + 20, title='Jean-Luc', name='Juno Starlight', description='A pilot.')
    ryu = publish(catalog, k3, at=T0 + 30, title='龍王の娘', name='Ren', description='海の底の城。')

    def find(**query):
        return {e.publication.event_id for e in catalog.cards(CardQuery(**query)).items}

    assert find(card_name='sera') == find(card_name='SÉRA') == {sera.event_id}
    assert find(card_name='phina') == set()  # (not in the middle of a word)
    assert find(card_name='guard sera') == {sera.event_id}  # (every word, in any order)
    assert find(card_name='sera pilot') == set()
    assert find(card_name='luc') == {luc.event_id}  # (punctuation separates words)
    assert find(card_name='娘') == find(card_name='王 龍') == {ryu.event_id}  # (no spaces: each letter a word)

    assert find(card_name='owls') == set()  # (in the description: only when asked)
    assert find(card_name='owls', in_description=True) == {sera.event_id}
    assert find(card_name='seraph elder', in_description=True) == {sera.event_id}  # (the name and the description)
    assert find(card_name='城', in_description=True) == {ryu.event_id}

    assert find(publisher_name='star') == {luc.event_id} and find(publisher_name='light') == set()
    assert find(publisher_name='juno star') == {luc.event_id}
    renamed = publish(catalog, k2, at=T0 + 40, title='Kai', name='J. S.')  # (a name before: still found by it)
    assert find(publisher_name='starlight') == {luc.event_id, renamed.event_id}
    assert [p.publisher_id for p in catalog.publishers(PublisherQuery(name='juno')).items] == [nostr.public_key(k2)]

    assert find(publishers=(PUB1, nostr.public_key(k3))) == {sera.event_id, ryu.event_id}
    assert find(exclude_publishers=(PUB1, nostr.public_key(k3))) == {luc.event_id, renamed.event_id}


def test_someones_cards(catalog):
    k2 = nostr.new_secret()
    uuid = fmt.new_card_uuid()
    first = publish(catalog, KEY1, at=T0 + 10, uuid=uuid, title='Aria')
    second = publish(catalog, KEY1, at=T0 + 20, uuid=uuid, title='Aria')
    gone = publish(catalog, KEY1, at=T0 + 30, title='Bram')
    other = publish(catalog, k2, at=T0 + 40, title='Old key')
    publish(catalog, nostr.new_secret(), at=T0 + 50, title='Someone else')
    catalog.receive(fmt.build_withdrawal([gone.event_id], secret=KEY1, created_at=T0 + 60).event.to_dict(), now=NOW)
    catalog.receive(fmt.build_withdrawal([second.event_id], secret=KEY1, created_at=T0 + 60).event.to_dict(), now=NOW)

    live, withdrawn = catalog.published_by([PUB1, nostr.public_key(k2)])
    assert [e.publication.event_id for e in live] == [other.event_id, first.event_id]  # (Aria: its version left)
    assert [e.publication.event_id for e in withdrawn] == [gone.event_id] and withdrawn[0].withdrawn
    assert catalog.published_by([]) == ((), ())


def test_a_catalog_from_before_word_search(tmp_path):
    """A catalog made before migration 6: what's searched is worked out from what it has."""
    import sqlite3
    path = tmp_path / 'old.sqlite3'
    with Catalog(path) as c:
        p = publish(c, KEY1, at=T0, title='Le Café des Ombres', name='Zoë', description='Une barista fantôme.')
    with sqlite3.connect(path) as db:  # (as it was: version 5, without what came after)
        db.execute('DROP TABLE publication_text')
        db.execute('DROP TABLE publication_sources')
        db.execute('DROP TABLE downloads')
        db.executescript(MIGRATIONS[4])  # (the downloads of version 5)
        db.execute('PRAGMA user_version = 5')
    with Catalog(path) as c:
        assert [e.publication.event_id for e in c.cards(CardQuery(card_name='cafe omb')).items] == [p.event_id]
        assert len(c.cards(CardQuery(card_name='fantome', in_description=True)).items) == 1
        assert len(c.cards(CardQuery(publisher_name='zoe')).items) == 1


def test_downloads_from_before_fingerprints(tmp_path):
    """A catalog made before migration 8 (one download per publication, no fingerprint): its downloads kept;
    added again, a card's both characters come from it."""
    path = tmp_path / 'old.sqlite3'
    with Catalog(path):
        pass
    event_id, infohash = 'a' * 64, 'b' * 40
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE downloads')
        db.executescript(MIGRATIONS[4])
        db.execute('INSERT INTO downloads VALUES (?, ?, ?, ?, ?)', (event_id, infohash, 'aria.png', 'aria-1', T0))
        db.execute('PRAGMA user_version = 7')
    with Catalog(path) as c:
        [old] = c.downloads()
        assert (old.event_id, old.character_id, old.downloaded_at, old.fingerprint) == (event_id, 'aria-1', T0, '')
        c.record_download(event_id, infohash, 'aria.png', 'aria-2', 'its fingerprint')
        assert [d.character_id for d in c.downloads()] == ['aria-2', 'aria-1']
        assert (c.download(event_id).character_id, c.download(event_id).fingerprint) == ('aria-2', 'its fingerprint')
        assert c.downloads(character_id='aria-1') == (old,) and c.downloaded([event_id]) == {event_id}


def test_a_withdrawal_before_its_publication(catalog):
    w = fmt.build_withdrawal([event('example')['id']], secret=KEY1, created_at=NOW)
    assert catalog.receive(w.event, now=NOW).withdrawn == ()  # (nothing known yet)
    assert catalog.receive(event('example'), now=NOW).status == 'added'
    assert catalog.cards().items == () and catalog.get(event('example')['id']).withdrawn.reason == ''


def test_only_the_publisher_withdraws(catalog):
    catalog.receive(event('example'), now=NOW)
    theirs = fmt.build_withdrawal([event('example')['id']], secret=KEY2, created_at=NOW)
    assert catalog.receive(theirs.event, now=NOW).withdrawn == ()
    assert ids(catalog.cards()) == [event('example')['id']] and catalog.get(event('example')['id']).withdrawn is None


def test_the_vectors_withdrawal(catalog):
    withdrawal = next(w for w in VECTORS['withdrawals'] if w['name'] == 'example_withdrawn')
    catalog.receive_all([event('example'), event('new_version'), withdrawal['event']], now=NOW)
    assert catalog.get(event('example')['id']).withdrawn.reason == withdrawal['reason']
    assert ids(catalog.cards()) == [event('new_version')['id']]


@pytest.mark.parametrize('item', VECTORS['withdrawals'], ids=lambda v: v['name'])
def test_what_the_vectors_withdrawals_withdraw(catalog, item):
    catalog.receive_all([v['event'] for v in VECTORS['valid']], now=NOW)
    [got] = catalog.receive_all([item['event']], now=NOW)  # (what they withdraw of the valid publications)
    assert got.status == 'withdrawal' and list(got.withdrawn) == item['withdraws']
    assert {v['event']['id'] for v in VECTORS['valid'] if catalog.get(v['event']['id']).withdrawn} == \
        set(item['withdraws'])


@pytest.mark.parametrize('item', VECTORS['invalid_withdrawals'], ids=lambda v: v['name'])
def test_the_vectors_invalid_withdrawals(catalog, item):
    catalog.receive_all([v['event'] for v in VECTORS['valid']], now=NOW)
    before = ids(catalog.cards())
    assert catalog.receive(item['event'], now=NOW).code == item['error'] and ids(catalog.cards()) == before


# --- blocking ---------------------------------------------------------------------------------------

def test_blocking(catalog, tmp_path):
    catalog.receive_all([event('example'), event('new_version'), event('other_publisher')], now=NOW)
    w = fmt.build_withdrawal([event('example')['id']], secret=KEY1, created_at=NOW)
    catalog.receive(w.event, now=NOW)
    result = catalog.block([PUB1, PUB1])
    assert result.blocked == (PUB1,) and set(result.purged) == {event('example')['id'], event('new_version')['id']}
    assert ids(catalog.cards()) == [event('other_publisher')['id']] and catalog.get(event('example')['id']) is None
    assert catalog.publisher(PUB1) is None and catalog.blocked() == (PUB1,) and catalog.is_blocked(PUB1)
    assert catalog.receive(event('example'), now=NOW).code == 'blocked'
    assert catalog.receive(w.event, now=NOW).code == 'blocked'
    assert catalog.block([]).purged == ()

    with Catalog(catalog.path) as again:  # (kept)
        assert again.is_blocked(PUB1) and again.receive(event('new_version'), now=NOW).code == 'blocked'
    catalog.unblock([PUB1])
    assert not catalog.is_blocked(PUB1) and catalog.blocked() == ()
    assert catalog.receive(event('example'), now=NOW).status == 'added'  # (the withdrawal was deleted too)
    assert ids(catalog.cards()) == [event('other_publisher')['id'], event('example')['id']]


# --- finding ----------------------------------------------------------------------------------------

def test_filters(catalog):
    k3 = nostr.new_secret()
    a = publish(catalog, KEY1, at=T0 + 10, title='Reika Shichijo', tags=('anime', 'martial-arts'), language='en')
    b = publish(catalog, KEY1, at=T0 + 20, title='Le Café des Ombres', tags=('fantasy', 'anime'), language='fr',
                rating='nsfw')
    c = publish(catalog, k3, at=T0 + 30, title='Ombre_100%', name='Zoë', tags=('fantasy',), language=None,
                card_format='chara_card_v3')

    def find(**query):
        return {e.publication.event_id for e in catalog.cards(CardQuery(**query)).items}

    assert find() == {a.event_id, b.event_id, c.event_id}
    assert find(card_name='REI') == {a.event_id} and find(card_name='cafe ombres') == {b.event_id}
    assert find(card_name='ombre') == {b.event_id, c.event_id} and find(card_name='_100%') == {c.event_id}
    assert find(card_name='eika') == find(card_name='mbres') == set()  # (from the start of words only)
    assert find(card_name='caf_') == find(card_name='le%ombres') == {b.event_id}  # (_ and %: between words)
    assert find(tags=('Anime',)) == {a.event_id, b.event_id} and find(tags=('anime', 'fantasy')) == find()
    assert find(tags=('anime', 'fantasy'), match_all_tags=True) == {b.event_id}
    assert find(tags=('fantasy',), exclude_tags=('Anime',)) == {c.event_id}  # (normalized, like the others)
    assert find(exclude_tags=('anime', 'martial-arts')) == {c.event_id}
    assert find(exclude_tags=('fantasy',)) == {a.event_id}
    assert find(tags=('anime', 'fantasy'), match_all_tags=True, exclude_tags=('fantasy',)) == set()
    assert find(content_rating='nsfw') == {b.event_id} and find(language='fr') == {b.event_id}
    assert find(card_format='chara_card_v3') == {c.event_id}
    assert find(publisher_id=PUB1) == {a.event_id, b.event_id} and find(publisher_name='zoe') == {c.event_id}
    assert find(published_since=T0 + 20) == {b.event_id, c.event_id}
    assert find(published_until=T0 + 20) == {a.event_id, b.event_id}
    assert find(card_uuid=a.card_uuid) == {a.event_id}
    assert [p.publisher_id for p in catalog.publishers(PublisherQuery(name='ZOË')).items] == [nostr.public_key(k3)]
    assert fold('Zoë Café') == 'zoe cafe'


def test_pages(catalog):
    made = [publish(catalog, KEY1, at=T0 + i // 2, title=f'Card {i}') for i in range(25)]  # (pairs with equal dates)
    order = [p.event_id for p in sorted(made, key=lambda p: (-p.created_at, p.event_id))]
    seen, cursor = [], None
    while True:
        page = catalog.cards(CardQuery(limit=10, cursor=cursor))
        seen += ids(page)
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    assert seen == order
    assert len(catalog.cards(CardQuery(limit=10_000)).items) == 25  # (at most 500 a page)
    with pytest.raises(ValueError, match='cursor'):
        catalog.cards(CardQuery(cursor='12:not-a-key'))
    pubs = catalog.publishers(PublisherQuery(limit=1))
    assert len(pubs.items) == 1 and pubs.next_cursor is None


def test_publishers_by_pages(catalog):
    keys = [nostr.new_secret() for _ in range(5)]
    for i, key in enumerate(keys):
        publish(catalog, key, at=T0 + i, name=f'P{i}')
    first = catalog.publishers(PublisherQuery(limit=2))
    second = catalog.publishers(PublisherQuery(limit=2, cursor=first.next_cursor))
    third = catalog.publishers(PublisherQuery(limit=2, cursor=second.next_cursor))
    assert [p.current_name for p in first.items + second.items + third.items] == ['P4', 'P3', 'P2', 'P1', 'P0']
    assert third.next_cursor is None


# --- the database -----------------------------------------------------------------------------------

def test_publications_never_change(catalog):
    catalog.receive(event('example'), now=NOW)
    with pytest.raises(sqlite3.IntegrityError, match='never change'):
        catalog._db.execute("UPDATE publications SET published_name = 'Mallory'")


def test_its_version_and_a_newer_one(tmp_path):
    path = tmp_path / 'catalog.sqlite3'
    with Catalog(path) as c:
        c.receive(event('example'), now=NOW)
    db = sqlite3.connect(path)
    assert db.execute('PRAGMA user_version').fetchone()[0] == len(MIGRATIONS)
    assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    db.execute(f'PRAGMA user_version = {len(MIGRATIONS) + 1}')
    db.commit()
    db.close()
    with pytest.raises(CatalogError, match='newer version'):
        Catalog(path)


def test_a_damaged_catalog(tmp_path):
    path = tmp_path / 'catalog.sqlite3'
    path.write_bytes(b'not a database at all' * 100)
    with pytest.raises(CatalogError, match='deleting it starts a new one'):
        Catalog(path)


def test_a_catalog_in_memory():
    with Catalog(':memory:') as c:
        assert c.receive(event('example'), now=NOW).status == 'added'


def test_many_publications_stay_quick(tmp_path):
    """A few thousand publications: a page of cards, and a search, in well under the blink of an eye."""
    import time
    keys = [nostr.new_secret() for _ in range(20)]
    events = []
    for i in range(3000):
        uuid_ = f'{i % 1500:08x}-0000-4000-8000-000000000000'  # (1,500 cards, two versions each)
        p = fmt.build_publication(PublicationDraft(publisher_name=f'P{i % 20}', card_name=f'Card {i}',
                                                   card_tags=(f't{i % 7}',)),
                                  torrents.card_file(str(i).encode()), card_uuid=uuid_, secret=keys[i % 20],
                                  created_at=T0 + i)
        events.append(p.event)
    with Catalog(tmp_path / 'big.sqlite3') as c:
        assert {r.status for r in c.receive_all(events, now=NOW)} == {'added'}
        started = time.perf_counter()
        page = c.cards(CardQuery(limit=100))
        found = c.cards(CardQuery(card_name='card 29', tags=('t3',)))
        publishers = c.publishers()
        took = time.perf_counter() - started
    assert len(page.items) == 100 and all(e.is_latest for e in page.items) and found.items
    assert len(publishers.items) == 20
    assert took < 2, f'{took:.2f} s'  # (about 0.05 s here: generous, for slow machines)


def test_tags(catalog):
    k3 = nostr.new_secret()
    uuid = fmt.new_card_uuid()
    publish(catalog, KEY1, at=T0, uuid=uuid, title='Aria', tags=('fantasy', 'old-tag'))
    publish(catalog, KEY1, at=T0 + 10, uuid=uuid, title='Aria', tags=('fantasy', 'bard'))  # (its latest version)
    publish(catalog, KEY1, at=T0 + 20, title='Bram', tags=('romance', 'fantasy'), rating='nsfw')
    publish(catalog, k3, at=T0 + 30, title='Cass', tags=('sci-fi', 'romance'))
    gone = publish(catalog, k3, at=T0 + 40, title='Dove', tags=('withdrawn-tag',))
    catalog.receive(fmt.build_withdrawal([gone.event_id], secret=k3, created_at=NOW).event, now=NOW)

    def counts(*args, **kwargs):
        return [(t.tag, t.cards) for t in catalog.tags(*args, **kwargs)]

    # Every tag of the cards (their latest version; not what's withdrawn), the most used first
    assert counts() == [('fantasy', 2), ('romance', 2), ('bard', 1), ('sci-fi', 1)]
    assert counts(CardQuery(content_rating='sfw')) == [('bard', 1), ('fantasy', 1), ('romance', 1), ('sci-fi', 1)]
    assert counts(CardQuery(tags=('romance',))) == [('romance', 2), ('fantasy', 1), ('sci-fi', 1)]  # (found with it)
    assert counts(CardQuery(exclude_tags=('sci-fi',))) == [('fantasy', 2), ('bard', 1), ('romance', 1)]
    assert counts(CardQuery(include_versions=True)) == [('fantasy', 3), ('romance', 2), ('bard', 1), ('old-tag', 1),
                                                        ('sci-fi', 1)]
    assert counts(prefix='#RO') == [('romance', 2)] and counts(prefix='s') == [('sci-fi', 1)]
    assert counts(prefix='%') == [] and counts(limit=1) == [('fantasy', 2)]
