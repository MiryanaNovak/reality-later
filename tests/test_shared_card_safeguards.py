"""The safeguards for shared cards (rpapp/cards.py's check_shared_card): a card from someone else is added,
and one is published, only within them: its file's size, and its picture's (told by its header, before
anything is decoded: a small file can hold a huge picture)."""
import base64
import json
import struct
import time
import zlib

import pytest

from rpapp import cards
from rpapp.cards import (MAX_PICTURE_PIXELS, PNG_SIGNATURE, CardError, SafeguardError, card_data, card_png,
                         check_shared_card, megabytes)
from rpapp.characters import new_character
from rpapp.publishing.announcements import MAX_FILE_BYTES

LIMIT = MAX_FILE_BYTES  # (the format's: 100 MiB)


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


def card_json(**extra) -> dict:
    aria = new_character('Aria')
    aria.scenario = '{{char}} sings.'
    return {**card_data(aria), **extra}


def png_claiming(width: int, height: int) -> bytes:
    """A PNG card whose header says it's that big: a few hundred bytes, whatever the size (its pixels
    would only be made by decoding it)."""
    text = base64.b64encode(json.dumps(card_json()).encode()).decode()
    return (PNG_SIGNATURE + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'tEXt', b'chara\0' + text.encode()) + chunk(b'IDAT', zlib.compress(b'')) + chunk(b'IEND', b''))


def gif_claiming(width: int, height: int) -> bytes:
    """A GIF whose header says it's that big (its one frame too), with almost nothing in it."""
    return (b'GIF89a' + struct.pack('<HH', width, height) + b'\x00\x00\x00'
            + b'\x2c' + struct.pack('<HHHH', 0, 0, width, height) + b'\x00\x02\x02\x44\x01\x00\x3b')


def json_card_with_picture(picture: bytes, kind='gif') -> bytes:
    """A V3 JSON card with its picture inside (a data: URI)."""
    uri = f'data:image/{kind};base64,{base64.b64encode(picture).decode()}'
    data = {'spec': 'chara_card_v3', 'spec_version': '3.0',
            'data': {**card_json()['data'], 'assets': [{'type': 'icon', 'uri': uri, 'name': 'main', 'ext': kind}]}}
    return json.dumps(data).encode()


def test_a_card_within_them():
    aria = new_character('Aria')
    content = card_png(card_data(aria), None, initials='AR')  # (as this app exports one)
    assert check_shared_card('aria.png', content, size_limit=LIMIT).character.name == 'Aria'
    width, height = 10_000, MAX_PICTURE_PIXELS // 10_000  # (just the limit: fine)
    check_shared_card('big.png', png_claiming(width, height), size_limit=LIMIT)


def test_a_file_too_big():
    content = card_png(card_data(new_character('Aria')), None)
    with pytest.raises(SafeguardError) as e:
        check_shared_card('aria.png', content, size_limit=len(content) - 1)
    assert e.value.what == 'file' and str(e.value).startswith('its file is ')
    assert megabytes(LIMIT) == '100 MB' and megabytes(int(23.4 * 1024 * 1024)) == '23.4 MB'


def test_any_card_within_the_format_is_read():
    """What a card file may unpack to (its JSON, a CharX's picture) is no less than a shared file may be: this app
    never refuses for its size a card the format allows."""
    assert cards.MAX_CARD_BYTES >= MAX_FILE_BYTES and cards.MAX_PICTURE_BYTES >= MAX_FILE_BYTES


def test_a_picture_too_big_isnt_decoded():
    started = time.perf_counter()
    with pytest.raises(SafeguardError) as e:
        check_shared_card('huge.png', png_claiming(20_000, 20_000), size_limit=LIMIT)  # (1.6 GB, decoded)
    assert time.perf_counter() - started < 1  # (its header only)
    assert e.value.what == 'picture'
    assert str(e.value) == ('its picture is 20,000 × 20,000 pixels (400 megapixels), and a shared card\'s is 50 '
                            'megapixels at most')
    with pytest.raises(SafeguardError, match='10,000 × 5,001 pixels'):
        check_shared_card('over.png', png_claiming(10_000, 5_001), size_limit=LIMIT)


def test_pictures_inside_other_cards():
    """A JSON card's picture (or a CharX's): told by its header too, whatever its kind."""
    with pytest.raises(SafeguardError, match='8,000 × 7,000 pixels'):
        check_shared_card('aria.json', json_card_with_picture(gif_claiming(8_000, 7_000)), size_limit=LIMIT)
    with pytest.raises(SafeguardError, match='more than .* megapixels'):  # (beyond even Pillow's own limit)
        check_shared_card('aria.json', json_card_with_picture(gif_claiming(65_535, 65_535)), size_limit=LIMIT)
    check_shared_card('aria.json', json_card_with_picture(gif_claiming(400, 600)), size_limit=LIMIT)
    # A picture this app can't read: not one (the card comes without it, as with Import)
    check_shared_card('aria.json', json_card_with_picture(b'not a picture', 'png'), size_limit=LIMIT)


def test_not_a_card():
    with pytest.raises(CardError) as e:
        check_shared_card('x.png', PNG_SIGNATURE + b'x', size_limit=LIMIT)
    assert not isinstance(e.value, SafeguardError)  # (not a card: said as such)
