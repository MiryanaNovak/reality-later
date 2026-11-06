"""A card file's torrent, made the same way every time (docs/card-announcements.md, "The file and its
torrent"): BitTorrent v1, one file named <sha256>.<extension>, 64 KiB pieces, and nothing else in its
info dictionary. The same file then has the same infohash, whoever publishes it and however often.

Pure Python: libtorrent (which seeds and downloads, later) is given the result, and makes exactly the
same (the test vectors were checked with it).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import quote

PIECE_LENGTH = 65536
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
ZIP_SIGNATURE = b'PK\x03\x04'


@dataclass(frozen=True, slots=True)
class CardFile:
    """A card file, and its torrent."""
    sha256: str
    size: int
    extension: str      # 'png', 'json' or 'charx'
    info: bytes         # the torrent's info dictionary, bencoded
    infohash: str       # its SHA-1: the v1 infohash, 40 lowercase hex digits

    @property
    def name(self) -> str:
        """Its name in the torrent and in the announcement: <sha256>.<extension>."""
        return f'{self.sha256}.{self.extension}'

    def metainfo(self) -> bytes:
        """The .torrent file's contents: just the info dictionary (trackers are in the announcement)."""
        return b'd4:info' + self.info + b'e'


def card_file(content: bytes) -> CardFile:
    sha256 = hashlib.sha256(content).hexdigest()
    extension = extension_of(content)
    info = bencode(info_dictionary(content, f'{sha256}.{extension}'))
    return CardFile(sha256=sha256, size=len(content), extension=extension, info=info,
                    infohash=hashlib.sha1(info).hexdigest())


def extension_of(content: bytes) -> str:
    """What kind of card file it is, from its first bytes (as rpapp/cards.py reads them)."""
    if content.startswith(PNG_SIGNATURE):
        return 'png'
    if content.startswith(ZIP_SIGNATURE):
        return 'charx'
    return 'json'


def info_dictionary(content: bytes, name: str) -> dict:
    pieces = b''.join(hashlib.sha1(content[i:i + PIECE_LENGTH]).digest()
                      for i in range(0, len(content), PIECE_LENGTH))
    return {'length': len(content), 'name': name, 'piece length': PIECE_LENGTH, 'pieces': pieces}


def magnet(infohash: str, name: str, trackers=()) -> str:
    """A magnet link, for any BitTorrent program."""
    return f'magnet:?xt=urn:btih:{infohash}&dn={quote(name, safe="")}' + \
        ''.join(f'&tr={quote(t, safe="")}' for t in trackers)


def bencode(value) -> bytes:
    """BitTorrent's encoding: whole numbers, strings (UTF-8) and bytes, lists, and dictionaries (their keys
    sorted, as bytes)."""
    if isinstance(value, bool):
        raise TypeError('bencode: no booleans')
    if isinstance(value, int):
        return b'i%de' % value
    if isinstance(value, str):
        value = value.encode('utf-8')
    if isinstance(value, bytes):
        return b'%d:%s' % (len(value), value)
    if isinstance(value, (list, tuple)):
        return b'l' + b''.join(bencode(v) for v in value) + b'e'
    if isinstance(value, dict):
        items = sorted((k.encode('utf-8') if isinstance(k, str) else k, v) for k, v in value.items())
        return b'd' + b''.join(bencode(k) + bencode(v) for k, v in items) + b'e'
    raise TypeError(f'bencode: can\'t encode {type(value).__name__}')
