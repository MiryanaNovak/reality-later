"""Nostr's basics: events (NIP-01), their id and their signature (BIP-340, with coincurve), and keys
written as npub / nsec (NIP-19).

coincurve is only imported when signing or checking a signature.

An event's id is the SHA-256 of [0, pubkey, created_at, kind, tags, content] written as JSON without
spaces, in UTF-8 as is. Python's json writes that for any text without control characters other than
tab, line feed and carriage return: the card format refuses the others (docs/card-announcements.md,
"Text"), because apps disagree on how to write them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any

HEX = re.compile(r'[0-9a-f]*')


class Malformed(ValueError):
    """Not an event: the message says what's wrong."""


@dataclass(frozen=True, slots=True)
class Event:
    """A signed Nostr event, as received or made. Checking its id and signature is up to the caller
    (has_valid_id, has_valid_signature): from_dict only checks its shape."""
    id: str
    pubkey: str
    created_at: int
    kind: int
    tags: tuple[tuple[str, ...], ...]
    content: str
    sig: str

    @classmethod
    def from_dict(cls, data: Any) -> Event:
        """The event in a relay's message (parsed JSON). Malformed if it doesn't have an event's shape:
        lowercase hex id, pubkey and sig of the right lengths, whole numbers, lists of strings."""
        if not isinstance(data, dict):
            raise Malformed('not an object')
        for name, length in (('id', 64), ('pubkey', 64), ('sig', 128)):
            if not _is_hex(data.get(name), length):
                raise Malformed(f'{name}: not {length} lowercase hex digits')
        for name in ('created_at', 'kind'):
            value = data.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise Malformed(f'{name}: not a whole number')
        tags = data.get('tags')
        if not isinstance(tags, list) or not all(isinstance(t, list) and t and all(isinstance(s, str) for s in t)
                                                 for t in tags):
            raise Malformed('tags: not a list of lists of strings')
        content = data.get('content')
        if not isinstance(content, str):
            raise Malformed('content: not a string')
        if not all(_is_utf8(s) for s in [content, *(s for t in tags for s in t)]):
            raise Malformed('text that isn\'t valid Unicode')  # (a lone surrogate, written \\ud800 in JSON)
        return cls(id=data['id'], pubkey=data['pubkey'], created_at=data['created_at'], kind=data['kind'],
                   tags=tuple(tuple(t) for t in tags), content=content, sig=data['sig'])

    @classmethod
    def from_json(cls, text: str) -> Event:
        try:
            return cls.from_dict(json.loads(text))
        except (json.JSONDecodeError, RecursionError) as e:
            raise Malformed(f'not JSON ({e})') from None

    def to_dict(self) -> dict:
        return {'id': self.id, 'pubkey': self.pubkey, 'created_at': self.created_at, 'kind': self.kind,
                'tags': [list(t) for t in self.tags], 'content': self.content, 'sig': self.sig}

    def to_json(self) -> str:
        """Compact, as sent to relays and kept in the catalog."""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(',', ':'))

    def size(self) -> int:
        """Bytes, as compact JSON in UTF-8."""
        return len(self.to_json().encode('utf-8'))

    def tag_values(self, name: str) -> list[tuple[str, ...]]:
        """The values of each tag of that name (what follows the name), in order."""
        return [t[1:] for t in self.tags if t[0] == name]

    def computed_id(self) -> str:
        return event_id(self.pubkey, self.created_at, self.kind, self.tags, self.content)

    def has_valid_id(self) -> bool:
        return self.computed_id() == self.id

    def has_valid_signature(self) -> bool:
        """Its signature is its pubkey's, for its id. (Its id must be checked too: has_valid_id.)"""
        try:
            return _curve().PublicKeyXOnly(bytes.fromhex(self.pubkey)).verify(bytes.fromhex(self.sig),
                                                                              bytes.fromhex(self.id))
        except ValueError:  # (a pubkey that isn't a point of the curve)
            return False


def serialize(pubkey: str, created_at: int, kind: int, tags, content: str) -> str:
    """NIP-01's serialization, which the id is the hash of."""
    return json.dumps([0, pubkey, created_at, kind, [list(t) for t in tags], content],
                      ensure_ascii=False, separators=(',', ':'))


def event_id(pubkey: str, created_at: int, kind: int, tags, content: str) -> str:
    return hashlib.sha256(serialize(pubkey, created_at, kind, tags, content).encode('utf-8')).hexdigest()


def sign(secret: bytes, *, kind: int, tags, content: str, created_at: int, aux: bytes | None = None) -> Event:
    """A new event, signed with this secret key. `aux`: the signature's 32 bytes of extra randomness
    (BIP-340); random unless given, as only tests do, to make the same signature again."""
    pubkey = public_key(secret)
    tags = tuple(tuple(t) for t in tags)
    digest = event_id(pubkey, created_at, kind, tags, content)
    sig = _curve().PrivateKey(secret).sign_schnorr(bytes.fromhex(digest),
                                                   aux_randomness=os.urandom(32) if aux is None else aux)
    return Event(id=digest, pubkey=pubkey, created_at=created_at, kind=kind, tags=tags, content=content,
                 sig=sig.hex())


# --- keys -----------------------------------------------------------------------------------------

def new_secret() -> bytes:
    """A new secret key: 32 random bytes that are a valid key (all of them but a negligible few)."""
    while True:
        secret = os.urandom(32)
        if is_valid_secret(secret):
            return secret


def is_valid_secret(secret: bytes) -> bool:
    try:
        _curve().PrivateKey(secret)
        return len(secret) == 32
    except ValueError:
        return False


def public_key(secret: bytes) -> str:
    """The public key of a secret key, as Nostr writes it: 64 hex digits (its x coordinate)."""
    return _curve().PublicKeyXOnly.from_secret(secret).format().hex()


def to_npub(pubkey: str) -> str:
    return bech32_encode('npub', bytes.fromhex(pubkey))


def to_nsec(secret: bytes) -> str:
    return bech32_encode('nsec', secret)


def from_bech32(text: str, prefix: str) -> bytes:
    """The 32 bytes of an npub or nsec (`prefix`). ValueError if it isn't one, or its checksum is wrong."""
    found, data = bech32_decode(text)
    if found != prefix:
        raise ValueError(f'not an {prefix} key')
    if len(data) != 32:
        raise ValueError('not a key of the right length')
    return data


# --- bech32 (BIP-173), as NIP-19 uses it ------------------------------------------------------------

_CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'
_GENERATOR = (0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)


def _polymod(values: list[int]) -> int:
    check = 1
    for value in values:
        top = check >> 25
        check = (check & 0x1ffffff) << 5 ^ value
        for i in range(5):
            check ^= _GENERATOR[i] if (top >> i) & 1 else 0
    return check


def _expand(prefix: str) -> list[int]:
    return [ord(c) >> 5 for c in prefix] + [0] + [ord(c) & 31 for c in prefix]


def _regroup(data: bytes | list[int], source: int, target: int, pad: bool) -> list[int]:
    acc = bits = 0
    out = []
    for value in data:
        acc = (acc << source) | value
        bits += source
        while bits >= target:
            bits -= target
            out.append((acc >> bits) & ((1 << target) - 1))
    if pad and bits:
        out.append((acc << (target - bits)) & ((1 << target) - 1))
    elif not pad and (bits >= source or (acc << (target - bits)) & ((1 << target) - 1)):
        raise ValueError('invalid padding')
    return out


def bech32_encode(prefix: str, data: bytes) -> str:
    words = _regroup(data, 8, 5, True)
    check = _polymod(_expand(prefix) + words + [0] * 6) ^ 1
    return prefix + '1' + ''.join(_CHARSET[w] for w in words + [(check >> 5 * (5 - i)) & 31 for i in range(6)])


def bech32_decode(text: str) -> tuple[str, bytes]:
    text = text.strip()
    if text.lower() != text and text.upper() != text:
        raise ValueError('mixed case')
    text = text.lower()
    split = text.rfind('1')
    if split < 1 or len(text) - split < 7 or len(text) > 1000:
        raise ValueError('not bech32')
    prefix, rest = text[:split], text[split + 1:]
    if any(c not in _CHARSET for c in rest):
        raise ValueError('not bech32')
    words = [_CHARSET.index(c) for c in rest]
    if _polymod(_expand(prefix) + words) != 1:
        raise ValueError('wrong checksum: a character is missing or mistyped')
    return prefix, bytes(_regroup(words[:-6], 5, 8, False))


def _curve():
    """coincurve, imported when first needed: without it (Python 3.14 for now), the rest of the package can
    still be imported, and npub / nsec still written (publishing.runtime_problem() says what's missing)."""
    import coincurve
    return coincurve


def _is_hex(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and HEX.fullmatch(value) is not None


def _is_utf8(text: str) -> bool:
    try:
        text.encode('utf-8')
        return True
    except UnicodeEncodeError:
        return False
