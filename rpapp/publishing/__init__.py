"""Sharing character cards without a central server: signed announcements on Nostr, the files by
BitTorrent. The announcements' format is docs/card-announcements.md; this package follows it.

Its modules:

    nostr.py          Nostr's basics: events, their id and signature, keys written as npub/nsec
    torrents.py       a card file's torrent, made the same way every time (so, the same infohash)
    announcements.py  the format: building publications and withdrawals, and checking those received
    identity.py       the publisher's key, in a file of its own
    catalog.py        the publications this computer knows of (SQLite): versions, names, withdrawals, blocking;
                      and its own: the files it shares, where its announcements arrived, what it downloaded
    relay.py          talking to one Nostr relay: asking for events, sending ours
    discovery.py      getting the relays' publications and withdrawals into the catalog
    seeder.py         BitTorrent (libtorrent): sharing card files, and downloading them, checked
    service.py        all of it for the app: discovery and downloads in the background, publishing,
                      withdrawing, sharing

It needs coincurve (secp256k1's signatures): packages exist for Python 3.13 and earlier. Without it, the
app works as before, and runtime_problem() says why sharing can't. Publishing and downloading need
libtorrent too (seeder.torrent_problem()); looking at the cards people share doesn't.
"""
from __future__ import annotations

import sys


def runtime_problem() -> str:
    """Why cards can't be shared on this computer ('' if they can): a missing library."""
    try:
        import coincurve  # noqa: F401
    except Exception as e:  # (ImportError, or a library that fails to load)
        return missing_library(e)
    return ''


def missing_library(e: Exception) -> str:
    """A library that didn't load, said in the middle of a sentence: on Python 3.14 and later, there are none of
    them for it yet (see pyproject.toml); otherwise it's missing, or broken (a packaged program without it)."""
    if sys.version_info >= (3, 14):
        return f'it needs Python 3.13 or earlier, for now: a library it needs isn\'t made for this one yet ({e})'
    return f'a library it needs is missing ({e})'


def sentence(text: str) -> str:
    """'a library it needs is missing (...)' → 'A library it needs is missing (...).'"""
    return text[:1].upper() + text[1:].rstrip('.') + '.' if text else ''
