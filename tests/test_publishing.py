"""Card sharing, without the network: the announcements' format (docs/card-announcements.md) against its
test vectors (tests/card_announcements.json), Nostr's basics, the torrent, and the publisher's key."""
import base64
import json
import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip('coincurve', reason='coincurve (sharing cards) is installed for Python 3.13 and earlier')

from rpapp import publishing  # noqa: E402
from rpapp.publishing import announcements as fmt  # noqa: E402
from rpapp.publishing import identity, nostr, torrents  # noqa: E402
from rpapp.publishing.announcements import PublicationDraft, Rejected  # noqa: E402

VECTORS = json.loads((Path(__file__).parent / 'card_announcements.json').read_text(encoding='utf-8'))
NOW, LIMIT = VECTORS['now'], VECTORS['size_limit']
KEY1 = bytes.fromhex(VECTORS['keys']['publisher_1']['secret'])
KEY2 = bytes.fromhex(VECTORS['keys']['publisher_2']['secret'])
FILE = base64.b64decode(VECTORS['file']['base64'])
ZERO = bytes(32)  # (the vectors' signatures: no extra randomness, so they can be made again)


def vector(name: str) -> dict:
    return next(v for v in VECTORS['valid'] if v['name'] == name)


def as_expected(p: fmt.Publication) -> dict:
    """A publication, in the vectors' words."""
    return {'publisher_key': p.publisher_id, 'card_uuid': p.card_uuid, 'created_at': p.created_at,
            'published_name': p.published_name, 'title': p.card_name, 'description': p.card_description,
            'sha256': p.card_sha256, 'infohash': p.torrent_infohash, 'file_name': p.file_name, 'size': p.card_size,
            'spec': p.card_format, 'rating': p.content_rating, 'language': p.language, 'tags': list(p.card_tags),
            'trackers': list(p.trackers)}


# --- the vectors ------------------------------------------------------------------------------------

@pytest.mark.parametrize('item', VECTORS['valid'], ids=lambda v: v['name'])
def test_valid_publications(item):
    publication = fmt.check_publication(item['event'], now=NOW, size_limit=LIMIT)
    assert as_expected(publication) == item['expected']
    assert publication.event_id == item['event']['id'] and publication.schema_version == 1
    assert nostr.serialize(item['event']['pubkey'], item['event']['created_at'], item['event']['kind'],
                           item['event']['tags'], item['event']['content']) == item['serialized']
    assert json.loads(publication.event.to_json()) == item['event']  # (kept as received)


@pytest.mark.parametrize('item', VECTORS['invalid'], ids=lambda v: v['name'])
def test_invalid_publications(item):
    with pytest.raises(Rejected) as rejected:
        fmt.check_publication(item['event'], now=NOW, size_limit=LIMIT)
    assert rejected.value.code == item['error']


def test_the_withdrawal():
    w = fmt.check_withdrawal(VECTORS['retraction']['event'], now=NOW)
    assert list(w.withdrawn) == VECTORS['retraction']['withdraws'] and w.reason == VECTORS['retraction']['reason']
    assert w.publisher_id == VECTORS['keys']['publisher_1']['public']


def test_the_file_and_its_torrent():
    card = torrents.card_file(FILE)
    assert (card.sha256, card.size, card.extension, card.name) == \
        (VECTORS['file']['sha256'], VECTORS['file']['size'], 'png', VECTORS['file']['name'])
    assert card.info.hex() == VECTORS['torrent']['info_bencoded_hex']
    assert card.infohash == VECTORS['torrent']['infohash']
    assert card.metainfo() == b'd4:info' + card.info + b'e'
    assert torrents.magnet(card.infohash, card.name, ['udp://tracker.opentrackr.org:1337/announce']) == (
        f'magnet:?xt=urn:btih:{card.infohash}&dn={card.name}'
        '&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce')  # (as in the document)
    assert torrents.extension_of(b'PK\x03\x04...') == 'charx' and torrents.extension_of(b'{"spec": ""}') == 'json'
    big = torrents.card_file(os.urandom(3 * torrents.PIECE_LENGTH + 5))
    assert len(torrents.info_dictionary(b'x' * (3 * torrents.PIECE_LENGTH + 5), 'n')['pieces']) == 4 * 20
    assert len(big.infohash) == 40


# --- building: the vectors made again ---------------------------------------------------------------

EXAMPLE = PublicationDraft(publisher_name='Marie', card_name='Reika Shichijo',
                           card_description=vector('example')['event']['content'],
                           card_tags=('anime', 'university', 'roleplay'), language='en')
TRACKERS = ['udp://tracker.opentrackr.org:1337/announce']
UUID = vector('example')['expected']['card_uuid']


def test_building_makes_the_vectors_again():
    card = torrents.card_file(FILE)
    example = fmt.build_publication(EXAMPLE, card, card_uuid=UUID, secret=KEY1, trackers=TRACKERS,
                                    created_at=vector('example')['event']['created_at'], aux=ZERO)
    assert example.event.to_dict() == vector('example')['event']  # same tags, same order: same id and signature

    newer = replace(EXAMPLE, publisher_name='Marie L.',
                    card_description=vector('new_version')['event']['content'],
                    card_tags=('anime', 'university', 'roleplay', 'rivals'))
    new_version = fmt.build_publication(newer, card, card_uuid=UUID, secret=KEY1, trackers=TRACKERS,
                                        created_at=vector('new_version')['event']['created_at'], aux=ZERO)
    assert new_version.event.to_dict() == vector('new_version')['event']

    minimal = PublicationDraft(publisher_name='Marie', card_name='Reika (alt)', content_rating='nsfw')
    other = fmt.build_publication(minimal, card, card_uuid=UUID, secret=KEY2,
                                  created_at=vector('other_publisher')['event']['created_at'], aux=ZERO)
    assert other.event.to_dict() == vector('other_publisher')['event']

    withdrawal = fmt.build_withdrawal([example.event_id], secret=KEY1, reason='Published by mistake.',
                                      created_at=VECTORS['retraction']['event']['created_at'], aux=ZERO)
    assert withdrawal.event.to_dict() == VECTORS['retraction']['event']


def test_signatures_are_random():
    card = torrents.card_file(FILE)
    a, b = (fmt.build_publication(EXAMPLE, card, card_uuid=UUID, secret=KEY1, created_at=NOW) for _ in range(2))
    assert a.event_id == b.event_id and a.event.sig != b.event.sig  # (the same event: two valid signatures)
    assert a.event.has_valid_signature() and b.event.has_valid_signature()


def test_what_building_tidies():
    card = torrents.card_file(FILE)
    draft = PublicationDraft(publisher_name='  Mariée ', card_name='Café\r\n',
                             card_description='One.\r\nTwo.\rThree.  ',
                             card_tags=('Slice of Life', '#Anime', 'anime', fmt.MARKER), language='fr-CA')
    p = fmt.build_publication(draft, card, card_uuid=fmt.new_card_uuid(), secret=KEY1, created_at=NOW,
                              trackers=['udp://a.example:80/announce', 'ftp://nope', 'udp://a.example:80/announce'])
    assert p.published_name == 'Mariée' and p.card_name == 'Café'  # (NFC, spaces at the ends removed)
    assert p.card_description == 'One.\nTwo.\nThree.'
    assert p.card_tags == ('slice-of-life', 'anime') and p.language == 'fr'
    assert p.trackers == ('udp://a.example:80/announce',)
    assert fmt.language_code('en_US') == 'en' and fmt.language_code('yue') is None and fmt.language_code(None) is None


@pytest.mark.parametrize('change, code', [
    (dict(card_name='R' * 201), 'too_long:title'),
    (dict(card_name='   '), 'empty:title'),
    (dict(publisher_name=''), 'empty:publisher'),
    (dict(card_format='Chara Card V3'), 'bad_spec'),
    (dict(content_rating='pg-13'), 'bad_rating'),
    (dict(card_description='é' * 8193), 'too_long:description'),
    (dict(card_description='Bad\x07bell'), 'control_character'),
])
def test_building_refuses_what_checking_would(change, code):
    with pytest.raises(Rejected) as rejected:
        fmt.build_publication(replace(EXAMPLE, **change), torrents.card_file(FILE), card_uuid=UUID, secret=KEY1,
                              created_at=NOW)
    assert rejected.value.code == code


def test_building_refuses_a_file_too_big():
    with pytest.raises(Rejected, match='file_too_big'):
        fmt.build_publication(EXAMPLE, torrents.card_file(FILE), card_uuid=UUID, secret=KEY1, size_limit=1000)
    with pytest.raises(Rejected, match='bad_file'):  # (an empty file)
        fmt.build_publication(EXAMPLE, torrents.card_file(b''), card_uuid=UUID, secret=KEY1)


def test_new_card_uuids():
    a, b = fmt.new_card_uuid(), fmt.new_card_uuid()
    assert a != b and fmt._UUID4.fullmatch(a)


# --- checking: beyond the vectors -------------------------------------------------------------------

def test_malformed_events():
    good = vector('example')['event']
    for broken in (None, [], {**good, 'id': good['id'].upper()}, {**good, 'created_at': True},
                   {**good, 'created_at': -1}, {**good, 'kind': '2003'}, {**good, 'tags': [['t'], []]},
                   {**good, 'tags': 'title'}, {**good, 'content': None}, {**good, 'sig': good['sig'][:-2]},
                   {**good, 'content': 'lone \ud800 surrogate'}):
        with pytest.raises(Rejected, match='malformed'):
            fmt.check_publication(broken, now=NOW)
    with pytest.raises(nostr.Malformed):
        nostr.Event.from_json('{not json')


def test_blocked_publishers_and_the_clock():
    good = vector('example')['event']
    with pytest.raises(Rejected, match='blocked'):
        fmt.check_publication(good, now=NOW, blocked={good['pubkey']})
    assert fmt.check_publication(good).event_id == good['id']  # (now, by the clock: dated in the past)
    with pytest.raises(Rejected, match='from_the_future'):
        fmt.check_publication(good, now=good['created_at'] - 901)


def test_a_pubkey_off_the_curve():
    event = nostr.Event.from_dict(vector('example')['event'])
    off = replace(event, pubkey='f' * 64)  # (not the x of any point: no key at all)
    off = replace(off, id=off.computed_id())
    assert not off.has_valid_signature()
    with pytest.raises(Rejected, match='bad_signature'):
        fmt.check_publication(off, now=NOW)


def test_withdrawals():
    card = torrents.card_file(FILE)
    p = fmt.build_publication(EXAMPLE, card, card_uuid=UUID, secret=KEY1, created_at=NOW)
    w = fmt.build_withdrawal([p.event_id, p.event_id], secret=KEY1, reason='x' * 1500, created_at=NOW)
    assert w.withdrawn == (p.event_id,) and len(w.reason) == fmt.MAX_REASON
    nothing = nostr.sign(KEY1, kind=5, tags=[['k', '2003'], ['e', 'not-an-id']], content='', created_at=NOW)
    with pytest.raises(Rejected, match='nothing_withdrawn'):
        fmt.check_withdrawal(nothing, now=NOW)
    with pytest.raises(Rejected, match='wrong_kind'):
        fmt.check_withdrawal(p.event, now=NOW)
    with pytest.raises(Rejected, match='wrong_kind'):
        fmt.check_publication(w.event, now=NOW)


# --- Nostr's keys -----------------------------------------------------------------------------------

def test_npub_and_nsec_as_nip19_writes_them():
    npub = 'npub10elfcs4fr0l0r8af98jlmgdh9c8tcxjvz9qkw038js35mp4dma8qzvjptg'
    nsec = 'nsec1vl029mgpspedva04g90vltkh6fvh240zqtv9k0t9af8935ke9laqsnlfe5'
    assert nostr.from_bech32(npub, 'npub').hex() == '7e7e9c42a91bfef19fa929e5fda1b72e0ebc1a4c1141673e2794234d86addf4e'
    assert nostr.from_bech32(nsec, 'nsec').hex() == '67dea2ed018072d675f5415ecfaed7d2597555e202d85b3d65ea4e58d2d92ffa'
    assert nostr.to_npub('7e7e9c42a91bfef19fa929e5fda1b72e0ebc1a4c1141673e2794234d86addf4e') == npub
    assert nostr.to_nsec(bytes.fromhex('67dea2ed018072d675f5415ecfaed7d2597555e202d85b3d65ea4e58d2d92ffa')) == nsec
    assert nostr.from_bech32(nsec.upper(), 'nsec') == nostr.from_bech32(nsec, 'nsec')  # (all capitals: fine)
    for bad, why in ((nsec[:-1] + ('q' if nsec[-1] != 'q' else 'p'), 'checksum'), (npub, 'not an nsec'),
                     (nsec[:10] + nsec[10:].upper(), 'mixed case'), ('nsec1b', 'not bech32')):
        with pytest.raises(ValueError, match=why):
            nostr.from_bech32(bad, 'nsec')


def test_new_keys():
    secret = nostr.new_secret()
    assert len(secret) == 32 and nostr.is_valid_secret(secret) and len(nostr.public_key(secret)) == 64
    assert not nostr.is_valid_secret(bytes(32)) and not nostr.is_valid_secret(b'\xff' * 32)


# --- the publisher's key, in its file ---------------------------------------------------------------

def test_the_key_file(tmp_path):
    path = identity.key_path(tmp_path)
    assert path == tmp_path / 'certificates' / 'nostr-key.txt' and identity.load_identity(path) is None
    me = identity.create_identity(path)
    assert path.read_text(encoding='utf-8') == me.nsec() + '\n' and me.nsec().startswith('nsec1')
    if os.name != 'nt':  # (Windows: the folder's permissions, your account's)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert identity.load_identity(path) == me and me.npub.startswith('npub1')
    assert me.nsec() not in repr(me) and me.secret.hex() not in repr(me)  # (never printed by accident)
    with pytest.raises(identity.IdentityError, match='already'):
        identity.create_identity(path)
    assert list(path.parent.iterdir()) == [path]  # (no temporary file left)


def test_importing_a_key(tmp_path):
    path = tmp_path / 'nostr-key.txt'
    nsec = 'nsec1vl029mgpspedva04g90vltkh6fvh240zqtv9k0t9af8935ke9laqsnlfe5'
    me = identity.import_identity(path, f'  {nsec}\n')
    assert me.secret.hex() == '67dea2ed018072d675f5415ecfaed7d2597555e202d85b3d65ea4e58d2d92ffa'
    assert identity.import_identity(path, me.secret.hex()) == me  # (the same one again: nothing changes)
    other = KEY2.hex()
    with pytest.raises(identity.IdentityError, match='already have'):
        identity.import_identity(path, other)
    assert identity.import_identity(path, other, replace=True).secret == KEY2
    kept = [p for p in tmp_path.iterdir() if p.name.startswith('nostr-key.replaced-')]
    assert len(kept) == 1 and identity.load_identity(kept[0]) == me  # (the old one, beside it)
    for bad, why in (('npub10elfcs4fr0l0r8af98jlmgdh9c8tcxjvz9qkw038js35mp4dma8qzvjptg', 'public key'),
                     ('hello', 'nsec1'), ('0' * 64, 'valid'), ('z' * 64, 'valid'), (nsec[:-1] + 'q', 'valid')):
        with pytest.raises(identity.IdentityError, match=why):
            identity.import_identity(tmp_path / 'other.txt', bad)


def test_a_damaged_key_file(tmp_path):
    path = tmp_path / 'nostr-key.txt'
    path.write_text('nsec1 lost half of it\n', encoding='utf-8')
    with pytest.raises(identity.IdentityError, match='backup'):
        identity.load_identity(path)
    with pytest.raises(identity.IdentityError, match='already have'):  # (not over it, unless asked)
        identity.import_identity(path, KEY2.hex())
    assert identity.import_identity(path, KEY2.hex(), replace=True).secret == KEY2  # (its backup, restored)
    [kept] = [p for p in tmp_path.iterdir() if p.name.startswith('nostr-key.replaced-')]
    assert kept.read_text(encoding='utf-8') == 'nsec1 lost half of it\n'  # (set aside, not lost)


def test_a_new_key_instead(tmp_path):
    path = tmp_path / 'nostr-key.txt'
    first = identity.create_identity(path)
    second = identity.create_identity(path, replace=True)
    third = identity.create_identity(path, replace=True)  # (within the same second: two kept)
    assert identity.load_identity(path) == third and len({first, second, third}) == 3
    kept = sorted(p for p in tmp_path.iterdir() if p.name.startswith('nostr-key.replaced-'))
    assert {identity.load_identity(p) for p in kept} == {first, second}


def test_runtime_problem():
    assert publishing.runtime_problem() == ''
