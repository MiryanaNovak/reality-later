"""A Nostr relay for trying card sharing on this computer, or this network, before anything goes public.

    python tools/test_relay.py                         ws://127.0.0.1:7777, events kept in test-relay.jsonl
    python tools/test_relay.py --host 0.0.0.0          reachable from the other computers of the network too
    python tools/test_relay.py --port 7000 --file F    another port, another file

Then, in Settings › Sharing cards, add ws://127.0.0.1:7777 as a relay (or ws://<this computer's address>:7777
from another computer). Until card sharing is released, the app only publishes to relays like this
one, on this computer or its network (publishing/service.py, PUBLIC_PUBLISHING).

It does what relays do (NIP-01): keeps the events sent to it (their signature checked), answers queries
(newest first, `limit` at most 500, `until` including its second), deletes what's withdrawn (NIP-09), and
says OK or why not. Ctrl+C stops it; its events stay in the file, for next time.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from websockets.asyncio.server import serve

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rpapp.publishing.nostr import Event, Malformed  # noqa: E402

MAX_LIMIT = 500


class TestRelay:
    def __init__(self, file: Path) -> None:
        self.file = file
        self.events: dict[str, dict] = {}
        if file.exists():
            for line in file.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    event = json.loads(line)
                    self.events[event['id']] = event
        self.deleted: set[str] = set()

    def keep(self, data: dict) -> tuple[bool, str]:
        try:
            event = Event.from_dict(data)
        except Malformed as e:
            return False, f'invalid: {e}'
        if not event.has_valid_id() or not event.has_valid_signature():
            return False, 'invalid: bad id or signature'
        if event.id in self.events:
            return True, 'duplicate: already have this event'
        if event.id in self.deleted:
            return False, 'blocked: it was deleted by its author'
        if event.kind == 5:  # (NIP-09: the author's own events named go)
            for tag in event.tags:
                if tag[0] == 'e' and len(tag) > 1 and self.events.get(tag[1], {}).get('pubkey') == event.pubkey:
                    del self.events[tag[1]]
                    self.deleted.add(tag[1])
        self.events[event.id] = data
        self._save()
        return True, ''

    def matching(self, flt: dict) -> list[dict]:
        found = [e for e in self.events.values() if _matches(e, flt)]
        found.sort(key=lambda e: (-e['created_at'], e['id']))
        return found[:min(int(flt.get('limit', MAX_LIMIT)), MAX_LIMIT)]

    def _save(self) -> None:
        self.file.write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in self.events.values()),
                             encoding='utf-8')

    async def serve(self, ws) -> None:
        async for text in ws:
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                await ws.send(json.dumps(['NOTICE', 'invalid: not JSON']))
                continue
            if not isinstance(message, list) or not message:
                continue
            if message[0] == 'EVENT' and len(message) > 1 and isinstance(message[1], dict):
                accepted, said = self.keep(message[1])
                await ws.send(json.dumps(['OK', message[1].get('id', ''), accepted, said]))
                print(f'{"kept" if accepted else "refused"}: kind {message[1].get("kind")} '
                      f'{message[1].get("id", "")[:12]} {said}', flush=True)
            elif message[0] == 'REQ' and len(message) > 2:
                sub, sent = message[1], {}
                for flt in message[2:]:
                    for event in self.matching(flt if isinstance(flt, dict) else {}):
                        sent.setdefault(event['id'], event)
                for event in sent.values():
                    await ws.send(json.dumps(['EVENT', sub, event], ensure_ascii=False))
                await ws.send(json.dumps(['EOSE', sub]))


def _matches(event: dict, flt: dict) -> bool:
    if 'ids' in flt and event['id'] not in flt['ids']:
        return False
    if 'authors' in flt and event['pubkey'] not in flt['authors']:
        return False
    if 'kinds' in flt and event['kind'] not in flt['kinds']:
        return False
    if 'since' in flt and event['created_at'] < flt['since']:
        return False
    if 'until' in flt and event['created_at'] > flt['until']:
        return False
    for key, values in flt.items():
        if key.startswith('#') and len(key) == 2:
            if not any(t[0] == key[1] and len(t) > 1 and t[1] in values for t in event['tags']):
                return False
    return True


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=7777)
    parser.add_argument('--file', type=Path, default=Path('test-relay.jsonl'))
    args = parser.parse_args()
    relay = TestRelay(args.file)
    async with serve(relay.serve, args.host, args.port, max_size=512 * 1024):
        print(f'Test relay: ws://{args.host}:{args.port} ({len(relay.events)} events in {args.file}). Ctrl+C stops it.',
              flush=True)
        await asyncio.get_running_loop().create_future()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
