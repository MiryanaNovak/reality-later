"""Discovery: getting the publications and withdrawals the relays have into the catalog
(docs/card-announcements.md, "Finding them").

    discovery = Discovery(catalog)
    result = await discovery.discover()               # what's new on each relay (all of it, the first time)
    result = await discovery.discover(full=True)      # everything each relay has, again
    result = await discovery.discover_publisher(key)  # all of one publisher's (after unblocking them)

Each relay on its own, at the same time: one that fails or is slow doesn't hold back the others, and only
its own progress is lost. For each, newest first, a page at a time (500 events), going back in time with
`until`, until a page brings nothing new:

- The **checkpoint** is when the relay's last complete discovery started; the next one asks from 24 hours
  before it, for what reached the relay late. It's only saved once the discovery reached its end.
- A discovery stops after MAX_PAGES pages per relay and kind of event (publications, withdrawals: each
  its own, so that a flood of one doesn't hold back the other): what's older is left for the next ones,
  as the relay's **gap**, got right after what's new.
- Nothing is downloaded: only the announcements (the files come when asked, later).

The network is asyncio's; the catalog's calls (checking the events, saving them) run in a thread: the app
isn't held up by either.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .announcements import MAX_FILE_BYTES, KIND_CARD, KIND_WITHDRAWAL, MARKER
from .catalog import Catalog, Received, SyncPoint, normalize_relay_url
from .relay import TIMEOUT, Relay, RelayError

PAGE = 500                # events asked for at a time (strfry's default limit)
MAX_PAGES = 20            # per relay and kind of event, per discovery: 10,000 events
MARGIN = 24 * 3600        # seconds before the checkpoint asked again: for what reached the relay late

CARDS = {'kinds': [KIND_CARD], '#t': [MARKER]}
WITHDRAWALS = {'kinds': [KIND_WITHDRAWAL], '#k': [str(KIND_CARD)]}
ASKED = {'cards': CARDS, 'withdrawals': WITHDRAWALS}

Progress = Callable[[str, int], None]  # (relay, events received from it so far)


@dataclass(frozen=True, slots=True)
class RelayResult:
    relay_url: str
    success: bool                  # it went to its end (or as far as allowed: truncated)
    received: int = 0              # events it sent
    added: int = 0                 # publications new to the catalog
    withdrawals: int = 0           # withdrawals new to the catalog
    rejected: int = 0              # events refused (invalid, blocked publisher...)
    truncated: bool = False        # more is left on it: for the next discovery
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    relays: tuple[RelayResult, ...]
    added: int                     # publications new to the catalog, all relays together
    withdrawn: tuple[str, ...]     # publications withdrawn by what came: they're no longer to be seeded

    @property
    def incomplete(self) -> bool:
        """A relay failed, or has more to give."""
        return any(not r.success or r.truncated for r in self.relays)


@dataclass
class _Tally:
    received: int = 0
    added: int = 0
    withdrawals: int = 0
    rejected: int = 0
    withdrawn: list[str] = field(default_factory=list)

    def count(self, results: list[Received]) -> None:
        self.received += len(results)
        for r in results:
            self.added += r.status == 'added'
            self.withdrawals += r.status == 'withdrawal'
            self.rejected += r.status == 'rejected'
            self.withdrawn += r.withdrawn


@dataclass(frozen=True, slots=True)
class _Run:
    complete: bool                 # it reached the end
    oldest: int | None             # how far back it got (its last `until`)
    pages: int                     # pages it took


class Discovery:
    def __init__(self, catalog: Catalog, *, page_size: int = PAGE, max_pages: int = MAX_PAGES,
                 timeout: float = TIMEOUT, size_limit: int = MAX_FILE_BYTES,
                 clock: Callable[[], float] = time.time) -> None:
        self.catalog = catalog
        self.page_size = page_size
        self.max_pages = max_pages
        self.timeout = timeout
        self.size_limit = size_limit
        self.clock = clock

    async def discover(self, *, full: bool = False, relays: Iterable[str] | None = None,
                       on_progress: Progress | None = None) -> DiscoveryResult:
        """What's new on each relay the catalog reads from (or on `relays`). `full`: everything they have,
        not just what's new since last time."""
        if relays is None:
            urls = [r.url for r in await asyncio.to_thread(self.catalog.relays) if r.read]
        else:
            urls = list(dict.fromkeys(normalize_relay_url(u) for u in relays))
        tallies = [_Tally() for _ in urls]
        results = await asyncio.gather(*(self._sync(url, full=full, tally=t, on_progress=on_progress)
                                          for url, t in zip(urls, tallies)))
        return _result(results, tallies)

    async def discover_publisher(self, publisher_id: str, *, relays: Iterable[str] | None = None,
                                 on_progress: Progress | None = None) -> DiscoveryResult:
        """Everything one publisher published (and withdrew), from each relay: after unblocking them, say.
        The relays' checkpoints don't change."""
        if relays is None:
            urls = [r.url for r in await asyncio.to_thread(self.catalog.relays) if r.read]
        else:
            urls = list(dict.fromkeys(normalize_relay_url(u) for u in relays))
        filters = [{**CARDS, 'authors': [publisher_id]}, {**WITHDRAWALS, 'authors': [publisher_id]}]
        tallies = [_Tally() for _ in urls]

        async def one(url: str, tally: _Tally) -> RelayResult:
            truncated = False
            try:
                async with Relay(url, timeout=self.timeout) as relay:
                    for flt in filters:
                        run = await self._pages(relay, url, flt, since=None, until=None, pages=self.max_pages,
                                                tally=tally, on_progress=on_progress)
                        truncated |= not run.complete
            except RelayError as e:
                return _relay_result(url, tally, error=str(e))
            return _relay_result(url, tally, truncated=truncated)

        return _result(await asyncio.gather(*(one(u, t) for u, t in zip(urls, tallies))), tallies)

    # --- one relay ------------------------------------------------------------------------------------

    async def _sync(self, url: str, *, full: bool, tally: _Tally, on_progress: Progress | None) -> RelayResult:
        state = await asyncio.to_thread(self.catalog.relay, url)  # (None: a relay the catalog doesn't keep)
        started = int(self.clock())
        points: dict[str, SyncPoint] = {}
        try:
            async with Relay(url, timeout=self.timeout) as relay:
                for what, flt in ASKED.items():  # (each its own pages: a flood of one doesn't stop the other)
                    point = None if full or state is None else state.point(what)
                    points[what] = await self._sync_one(relay, url, flt, point, started, tally, on_progress)
        except RelayError as e:
            if state is not None:
                await asyncio.to_thread(self.catalog.sync_failed, url, str(e), at=started)
            return _relay_result(url, tally, error=str(e))
        if state is not None:
            await asyncio.to_thread(self.catalog.save_sync, url, points, at=started)
        return _relay_result(url, tally, truncated=any(p.gap for p in points.values()))

    async def _sync_one(self, relay: Relay, url: str, flt: dict, point: SyncPoint | None, started: int,
                        tally: _Tally, on_progress: Progress | None) -> SyncPoint:
        """One kind of event from a relay: what's new since `point` (everything, without one), then what an
        earlier discovery left (its gap), in max_pages pages at most. Where it is then."""
        since = None if point is None else max(0, point.checkpoint - MARGIN)
        run = await self._pages(relay, url, flt, since=since, until=None, pages=self.max_pages, tally=tally,
                                on_progress=on_progress)
        pages = self.max_pages - run.pages
        gap = None if run.complete else (since or 0, started if run.oldest is None else run.oldest)
        old = point.gap if point is not None else None
        if old is not None:
            if pages > 0:
                left = await self._pages(relay, url, flt, since=old[0], until=old[1], pages=pages, tally=tally,
                                         on_progress=on_progress)
                old = None if left.complete else (old[0], old[1] if left.oldest is None else left.oldest)
            gap = _merge(gap, old)
        return SyncPoint(checkpoint=started, gap=gap)

    async def _pages(self, relay: Relay, url: str, flt: dict, *, since: int | None, until: int | None, pages: int,
                     tally: _Tally, on_progress: Progress | None) -> _Run:
        """One filter's events, newest first, page after page going back in time, from `until` (None: now)
        down to `since` (None: the beginning), in at most `pages` pages.

        Each page asks up to its oldest event's second, included (several events can share a second, on
        either side of a page's end): that page's newest events come again, recognized. Complete when a page
        brings nothing new. Two things relays do are taken into account:
        - some leave `until`'s own second out (NIP-01 includes it): seen when a page doesn't have the events
          of that second it should; from then on, they're asked up to the second after;
        - a second with more events than a page holds would give the same page again and again: the next
          page asks from the second before (what that second held beyond a page is missed: NIP-01 has no
          other way to go through it)."""
        seen: set[str] = set()
        used = 0
        exclusive = False     # this relay's `until` leaves its own second out
        boundary = False      # the last page ended within `until`'s second: some of its events were seen
        while used < pages:
            query = {**flt, 'limit': self.page_size}
            if since is not None:
                query['since'] = since
            if until is not None:
                query['until'] = until + 1 if exclusive else until
            events = await relay.query([query], max_events=self.page_size)
            used += 1
            dates = [e['created_at'] for e in events if type(e.get('created_at')) is int]
            if boundary and not exclusive and until not in dates:
                exclusive = True  # (the same page again, its second included)
                continue
            if not dates:
                return _Run(True, until, used)
            new = [e for e in events if isinstance(e.get('id'), str) and e['id'] not in seen]
            if new:
                seen.update(e['id'] for e in new)
                tally.count(await asyncio.to_thread(self.catalog.receive_all, new, now=int(self.clock()),
                                                    size_limit=self.size_limit))
                if on_progress is not None:
                    on_progress(url, tally.received)
            lowest = min(dates)
            if until is not None and lowest > until:  # (a relay ignoring `until`: no further)
                return _Run(True, until, used)
            if lowest == until:   # (a whole page in one second: past it)
                lowest, boundary = until - 1, False
            elif not new:         # (the end)
                return _Run(True, until, used)
            else:
                boundary = True
            if since is not None and lowest < since or lowest < 0:
                return _Run(True, lowest, used)
            until = lowest
        return _Run(False, until, used)


def _merge(a: tuple[int, int] | None, b: tuple[int, int] | None) -> tuple[int, int] | None:
    """Two gaps as one, covering both (what's between them comes again: the catalog knows it already)."""
    if a is None or b is None:
        return a or b
    return min(a[0], b[0]), max(a[1], b[1])


def _relay_result(url: str, tally: _Tally, *, truncated: bool = False, error: str | None = None) -> RelayResult:
    return RelayResult(relay_url=url, success=error is None, received=tally.received, added=tally.added,
                       withdrawals=tally.withdrawals, rejected=tally.rejected, truncated=truncated, error=error)


def _result(results: list[RelayResult], tallies: list[_Tally]) -> DiscoveryResult:
    return DiscoveryResult(relays=tuple(results), added=sum(r.added for r in results),
                           withdrawn=tuple(dict.fromkeys(i for t in tallies for i in t.withdrawn)))
