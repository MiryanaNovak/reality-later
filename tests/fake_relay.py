"""A Nostr relay for the tests (NIP-01): keeps the events it's given, answers queries the way common
relays do (newest first, at most `limit`, then EOSE), and can misbehave on demand."""
from __future__ import annotations

import json

from websockets.asyncio.server import serve


class FakeRelay:
    def __init__(self, *, max_limit: int = 500, misbehave: str | None = None, port: int = 0) -> None:
        self.port = port                   # 0: any free one (a given one: a relay down, then up again)
        self.events: dict[str, dict] = {}
        self.queries: list[dict] = []      # each filter asked for, in order
        self.max_limit = max_limit         # what it gives at most per filter, whatever the limit asked
        # 'closed': refuses queries (CLOSED); 'silent': never says it's done (no EOSE); 'hangup': closes the
        # connection; 'junk': sends nonsense first; 'ignore_until': answers as if there were no until;
        # 'exclusive_until': leaves until's own second out; 'duplicate_false': answers false to a duplicate
        self.misbehave = misbehave
        self.refuse_events: str | None = None  # a reason to refuse what's published
        self.connections = 0                   # how many connections it had
        self._server = None
        self.url = ''

    async def __aenter__(self) -> FakeRelay:
        self._server = await serve(self._serve, '127.0.0.1', self.port)
        self.url = f'ws://127.0.0.1:{self._server.sockets[0].getsockname()[1]}'
        return self

    async def __aexit__(self, *_) -> None:
        self._server.close()
        await self._server.wait_closed()

    def add(self, *events: dict) -> None:
        for event in events:
            self.events[event['id']] = event

    def matching(self, flt: dict) -> list[dict]:
        found = [e for e in self.events.values() if _matches(e, flt, self.misbehave)]
        found.sort(key=lambda e: (-e['created_at'], e['id']))
        return found[:min(flt.get('limit', self.max_limit), self.max_limit)]

    async def _serve(self, ws) -> None:
        self.connections += 1
        async for text in ws:
            message = json.loads(text)
            if message[0] == 'REQ':
                sub, filters = message[1], message[2:]
                self.queries += filters
                if self.misbehave == 'hangup':
                    await ws.close()
                    return
                if self.misbehave == 'closed':
                    await ws.send(json.dumps(['CLOSED', sub, 'auth-required: we only serve members']))
                    continue
                if self.misbehave == 'junk':
                    for junk in ('not json', '{"an": "object"}', '["EVENT"]', json.dumps(['EVENT', 'other-sub', {}]),
                                 json.dumps(['EVENT', sub, 'not an event']), json.dumps(['NOTICE', 'hello'])):
                        await ws.send(junk)
                sent = {}
                for flt in filters:
                    for event in self.matching(flt):
                        sent.setdefault(event['id'], event)
                for event in sent.values():
                    await ws.send(json.dumps(['EVENT', sub, event], ensure_ascii=False))
                if self.misbehave != 'silent':
                    await ws.send(json.dumps(['EOSE', sub]))
            elif message[0] == 'EVENT':
                event = message[1]
                if self.refuse_events:
                    await ws.send(json.dumps(['OK', event['id'], False, self.refuse_events]))
                elif event['id'] in self.events:
                    await ws.send(json.dumps(['OK', event['id'], self.misbehave != 'duplicate_false',
                                              'duplicate: already have this event']))
                else:
                    self.add(event)
                    await ws.send(json.dumps(['OK', event['id'], True, '']))


def _matches(event: dict, flt: dict, misbehave: str | None) -> bool:
    if 'ids' in flt and event['id'] not in flt['ids']:
        return False
    if 'authors' in flt and event['pubkey'] not in flt['authors']:
        return False
    if 'kinds' in flt and event['kind'] not in flt['kinds']:
        return False
    if 'since' in flt and event['created_at'] < flt['since']:
        return False
    if 'until' in flt and misbehave != 'ignore_until' and event['created_at'] > flt['until']:
        return False
    if 'until' in flt and misbehave == 'exclusive_until' and event['created_at'] == flt['until']:
        return False
    for key, values in flt.items():
        if key.startswith('#') and len(key) == 2:
            if not any(t[0] == key[1] and len(t) > 1 and t[1] in values for t in event['tags']):
                return False
    return True
