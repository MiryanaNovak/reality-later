"""Sharing character cards without a central server: signed announcements on Nostr, the files by
BitTorrent. The announcements' format is docs/card-announcements.md; this package follows it.

So far (no network yet):

    nostr.py          Nostr's basics: events, their id and signature, keys written as npub/nsec
    torrents.py       a card file's torrent, made the same way every time (so, the same infohash)
    announcements.py  the format: building publications and withdrawals, and checking those received
    identity.py       the publisher's key, in a file of its own

It needs coincurve (secp256k1's signatures): packages exist for Python 3.13 and earlier. Without it, the
app works as before, and runtime_problem() says why sharing can't.
"""
from __future__ import annotations


def runtime_problem() -> str:
    """Why cards can't be shared on this computer ('' if they can): a missing library."""
    try:
        import coincurve  # noqa: F401
    except Exception as e:  # (ImportError, or a library that fails to load)
        return f'a library it needs is missing ({e})'
    return ''
