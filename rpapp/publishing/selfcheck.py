"""`reality-later check-sharing`: whether card sharing works on this computer. The packaged programs' smoke
test runs it (packaging/smoke_test.py): their libraries must be there, and work, which importing them
doesn't prove (a library built without what it needs on this system fails only when used).

It does what publishing and adding a card do, between two copies of the sharing service on this computer
only: a card is published (checked, signed, sent to a relay, its file shared by BitTorrent), read back from
the relay, then downloaded by the other copy, checked (its SHA-256, its torrent's infohash) and added. In a
temporary folder, with a relay of its own on 127.0.0.1 and BitTorrent on 127.0.0.1 only (no DHT, no
trackers, no router): nothing of yours is used or changed, and nothing leaves this computer.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

Report = Callable[[bool, str], None]  # (whether it worked, what)
TIMEOUT = 60.0  # seconds, for the whole of it


def run(report: Report | None = None) -> bool:
    """The check, from the terminal: each step said as it's done. Whether it all worked."""
    report = report or _print
    try:
        return asyncio.run(asyncio.wait_for(check(report), TIMEOUT))
    except TimeoutError:
        report(False, f'it took more than {TIMEOUT:.0f} seconds')
        return False


async def check(report: Report) -> bool:
    from . import runtime_problem
    from .seeder import torrent_problem
    for problem, what in ((runtime_problem(), 'signing (coincurve) and relays (websockets)'),
                          (torrent_problem(), 'BitTorrent (libtorrent)')):
        report(not problem, f'its libraries load: {what}' + (f': {problem}' if problem else ''))
        if problem:
            return False

    from ..cards import card_data, card_png
    from ..characters import new_character
    from . import announcements as fmt
    from .announcements import PublicationDraft
    from .relay import Relay
    from .service import Sharing

    with tempfile.TemporaryDirectory(prefix='reality-later-check-') as folder:
        async with _LocalRelay() as relay:
            publisher = Sharing(Path(folder) / 'publisher', default_relays=[relay.url], session=_session())
            downloader = None
            try:
                publisher.create_identity()
                publisher.session.start()
                character = new_character('Checking')
                character.scenario = f'A card made to check sharing, {time.time()}.'
                content = card_png(card_data(character), None, initials='CH')
                draft = PublicationDraft(publisher_name='Check', card_name='Checking sharing')
                published = await publisher.publish(draft, content, card_uuid=fmt.new_card_uuid())
                p = published.publication
                arrived = [d.state for d in published.deliveries] == ['accepted']
                report(arrived, 'a card is published: signed, and sent to a relay')
                if not arrived:
                    return False

                async with Relay(relay.url, timeout=10) as reading:
                    events = await reading.query([{'ids': [p.event_id]}])
                read = [e.get('id') for e in events] == [p.event_id]
                report(read, 'it\'s read back from the relay')
                if not read:
                    return False

                downloader = Sharing(Path(folder) / 'downloader', default_relays=[relay.url],
                                     session=_session(peer=publisher.session.port))
                received = downloader.catalog.receive(events[0])
                said = ' '.join(part for part in (received.status, received.code) if part)
                report(received.status == 'added', f'its signature is checked ({said})')
                if received.status != 'added':
                    return False
                added: list[bytes] = []

                def importer(file_name: str, data: bytes, languages: tuple[str, ...]) -> tuple[str, str, str]:
                    added.append(data)
                    return 'checking', 'added', ''
                downloader.importer = importer
                downloader.share_downloads = False
                fetch = downloader.download(p.event_id)
                await fetch.task
                got = fetch.stage == 'done' and added == [content]
                report(got, 'its file is downloaded by BitTorrent, checked and added' +
                       ('' if got else f' ({fetch.error or fetch.stage})'))
                return got
            finally:
                for sharing in (downloader, publisher):
                    if sharing is not None:
                        await sharing.close()


def _session(*, peer: int | None = None):
    """BitTorrent on 127.0.0.1 only: no DHT, no local discovery, no router, no trackers; TCP (on one computer,
    uTP can wait for libtorrent's timeouts)."""
    from .seeder import TorrentSession
    s = TorrentSession(listen='127.0.0.1:0', dht=False, local_discovery=False, port_mapping=False, trackers=(),
                       peers=(('127.0.0.1', peer),) if peer else ())
    s.find_timeout = s.stall_timeout = 20
    s.settings.update(enable_outgoing_utp=False, enable_incoming_utp=False)
    return s


class _LocalRelay:
    """The least of a Nostr relay (NIP-01), on 127.0.0.1: keeps the events it's sent, gives them back."""

    def __init__(self) -> None:
        self.events: dict[str, dict] = {}
        self.url = ''
        self._server = None

    async def __aenter__(self) -> _LocalRelay:
        from websockets.asyncio.server import serve
        self._server = await serve(self._serve, '127.0.0.1', 0)
        self.url = f'ws://127.0.0.1:{self._server.sockets[0].getsockname()[1]}'
        return self

    async def __aexit__(self, *_) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _serve(self, ws) -> None:
        async for text in ws:
            message = json.loads(text)
            if message[0] == 'EVENT':
                event = message[1]
                self.events[event['id']] = event
                await ws.send(json.dumps(['OK', event['id'], True, '']))
            elif message[0] == 'REQ':
                ids = {i for f in message[2:] for i in f.get('ids', ())}
                for event in self.events.values():
                    if event['id'] in ids:
                        await ws.send(json.dumps(['EVENT', message[1], event]))
                await ws.send(json.dumps(['EOSE', message[1]]))


def _print(ok: bool, what: str) -> None:
    print(f'{"ok" if ok else "FAILED"}: {what}', flush=True)
