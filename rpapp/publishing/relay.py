"""Talking to one Nostr relay (NIP-01), over a websocket: asking for events, and sending ours.

    async with Relay('wss://relay.example.com') as relay:
        events = await relay.query([{'kinds': [2003], '#t': [...], 'limit': 500}])  # until the relay says that's all
        accepted, message = await relay.publish(event)

What the relay sends is untrusted: events come back as parsed JSON, for the caller to check (the
catalog does); anything else that isn't what's expected is ignored. A relay that doesn't answer in time,
refuses a query or closes the connection raises RelayError.

Uses the HTTPS proxy of the environment (HTTPS_PROXY), like the rest of the app.
"""
from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from .nostr import Event

TIMEOUT = 20.0                 # seconds: to connect, and for each answer
QUERY_TIME = 3                 # a query's events, all of them: within this many TIMEOUTs
MAX_MESSAGE = 512 * 1024       # bytes: no event of ours is near (48 KiB at most)
USER_AGENT = 'Reality Later'

_ids = itertools.count(1)


class RelayError(Exception):
    """The relay couldn't be reached, or didn't answer as it should: the message says what happened."""


class Relay:
    def __init__(self, url: str, *, timeout: float = TIMEOUT) -> None:
        self.url = url
        self.timeout = timeout
        self._ws: ClientConnection | None = None
        self.notices: list[str] = []   # what it said besides (NOTICE): for the curious

    async def __aenter__(self) -> Relay:
        try:
            self._ws = await asyncio.wait_for(
                connect(self.url, open_timeout=self.timeout, max_size=MAX_MESSAGE, user_agent_header=USER_AGENT,
                        ping_interval=None, compression=None), self.timeout)
        except (OSError, WebSocketException, asyncio.TimeoutError, ValueError) as e:
            raise RelayError(f'can\'t connect ({_say(e)})') from None
        return self

    async def __aexit__(self, *_) -> None:
        if self._ws is not None:
            try:
                await asyncio.wait_for(self._ws.close(), 5)
            except (OSError, WebSocketException, asyncio.TimeoutError):
                pass
            self._ws = None

    async def query(self, filters: list[dict], *, max_events: int = 1000) -> list[dict]:
        """The events matching these filters (NIP-01's REQ), until the relay says it has sent all it has
        (EOSE). At most `max_events` are kept: a relay sending more than asked isn't followed."""
        sub = f'rl{next(_ids)}'
        await self._send(['REQ', sub, *filters])
        events: list[dict] = []
        deadline = asyncio.get_running_loop().time() + QUERY_TIME * self.timeout
        try:
            while True:
                message = await self._receive(deadline)
                if not isinstance(message, list) or len(message) < 2:
                    continue
                kind = message[0]
                if kind == 'EVENT' and message[1] == sub and len(message) >= 3:
                    if isinstance(message[2], dict) and len(events) < max_events:
                        events.append(message[2])
                elif kind == 'EOSE' and message[1] == sub:
                    return events
                elif kind == 'CLOSED' and message[1] == sub:
                    reason = message[2] if len(message) > 2 and isinstance(message[2], str) else ''
                    raise RelayError(f'it refused the query ({reason or "no reason given"})')
                elif kind == 'NOTICE' and isinstance(message[1], str):
                    self.notices.append(message[1][:500])
        finally:
            await self._send(['CLOSE', sub], quiet=True)

    async def publish(self, event: Event) -> tuple[bool, str]:
        """Send an event (EVENT); whether the relay has it now (OK), and what it said. One that already had it
        says so ("duplicate:"): that's accepted too, whether it answers true (as NIP-01 says) or false (as
        some do)."""
        await self._send(['EVENT', event.to_dict()])
        while True:
            message = await self._receive()
            if isinstance(message, list) and len(message) >= 3 and message[0] == 'OK' and message[1] == event.id:
                said = message[3] if len(message) > 3 and isinstance(message[3], str) else ''
                return message[2] is True or said.startswith('duplicate:'), said[:500]
            if isinstance(message, list) and len(message) >= 2 and message[0] == 'NOTICE' \
                    and isinstance(message[1], str):
                self.notices.append(message[1][:500])

    async def _send(self, message: list, *, quiet: bool = False) -> None:
        if self._ws is None:
            raise RelayError('not connected')
        try:
            await asyncio.wait_for(self._ws.send(json.dumps(message, ensure_ascii=False)), self.timeout)
        except (OSError, WebSocketException, asyncio.TimeoutError) as e:
            if not quiet:
                raise RelayError(f'the connection broke ({_say(e)})') from None

    async def _receive(self, deadline: float | None = None) -> Any:
        """The relay's next message, parsed (None if it isn't JSON)."""
        wait = self.timeout
        if deadline is not None:
            wait = min(wait, deadline - asyncio.get_running_loop().time())
        try:
            text = await asyncio.wait_for(self._ws.recv(), max(wait, 0))
        except asyncio.TimeoutError:
            raise RelayError('too slow: no answer in time' if deadline is not None and wait < self.timeout
                             else f'no answer in {self.timeout:g} seconds') from None
        except (OSError, WebSocketException) as e:
            raise RelayError(f'the connection broke ({_say(e)})') from None
        try:
            return json.loads(text)
        except (json.JSONDecodeError, RecursionError, TypeError):
            return None


def _say(error: BaseException) -> str:
    return str(error) or type(error).__name__
