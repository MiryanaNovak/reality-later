"""Card sharing, for the app: its catalog, discovery in the background, and what the pages need.

One per data folder, made by the app at startup (ui/app.py), if sharing can run on this computer
(publishing.runtime_problem()). Its catalog is <data folder>/sharing/catalog.sqlite3; a new one starts with
DEFAULT_RELAYS, which the user can change in Settings.

Discovery runs as a background task, one at a time: pages ask for one (discover()), show its progress
(progress, running), and go on with what's there meanwhile. The catalog's calls run in a thread (call()):
the pages don't wait on the database either.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

from .catalog import Catalog
from .discovery import Discovery, DiscoveryResult

log = logging.getLogger(__name__)

# Tried from Paris in November 2026 (tools/try_relays.py): they answer, keep torrents, accept publishing,
# and delete what's withdrawn. Free, from different operators: none of them is needed, several are better.
DEFAULT_RELAYS = (
    'wss://relay.damus.io',
    'wss://relay.primal.net',
    'wss://relay.nostr.net',
    'wss://nostr.oxtr.dev',
    'wss://offchain.pub',
    'wss://nostr.mom',
)
DUE_AFTER = 3600  # seconds: opening the shared cards looks for new ones when the last look is older

T = TypeVar('T')


class Sharing:
    def __init__(self, data_dir: str | Path, *, default_relays: Iterable[str] = DEFAULT_RELAYS) -> None:
        self.catalog = Catalog(Path(data_dir) / 'sharing' / 'catalog.sqlite3')
        if self.catalog.created:
            for url in default_relays:
                self.catalog.add_relay(url)
        self.discovery = Discovery(self.catalog)
        self.progress: dict[str, int] = {}       # events received from each relay, by the discovery running
        self.result: DiscoveryResult | None = None
        self.error = ''                          # the last discovery's, if it failed as a whole
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self._task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()

    async def call(self, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """A catalog call (or any that waits on the disk), in a thread."""
        return await asyncio.to_thread(function, *args, **kwargs)

    # --- discovery --------------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def due(self, *, now: float | None = None) -> bool:
        """Whether it's time to look for new cards: never looked since the app started, or not for a while."""
        now = time.time() if now is None else now
        return not self.running and (self.finished_at is None or now - self.finished_at > DUE_AFTER)

    def discover(self, *, full: bool = False) -> asyncio.Task:
        """Look for new cards on the relays, in the background; the one already running, if there's one."""
        if not self.running:
            self.progress = {}
            self.started_at, self.error = time.time(), ''
            self._task = asyncio.create_task(self._discover(full))
        return self._task

    async def _discover(self, full: bool) -> DiscoveryResult | None:
        try:
            self.result = await self.discovery.discover(full=full, on_progress=self._progressed)
            return self.result
        except Exception as e:  # (a bug, or the disk: said on the page rather than lost)
            log.exception('Looking for shared cards failed')
            self.error = str(e) or type(e).__name__
            return None
        finally:
            self.finished_at = time.time()

    def _progressed(self, url: str, received: int) -> None:
        self.progress[url] = received

    # --- blocking ---------------------------------------------------------------------------------

    async def block(self, publisher_id: str) -> int:
        """Block a publisher: their cards leave the catalog. How many publications went."""
        return len((await self.call(self.catalog.block, [publisher_id])).purged)

    async def unblock(self, publisher_id: str) -> None:
        """Unblock a publisher, and get their cards again from the relays, in the background."""
        await self.call(self.catalog.unblock, [publisher_id])
        task = asyncio.create_task(self.discovery.discover_publisher(publisher_id))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # --- the end ----------------------------------------------------------------------------------

    async def close(self) -> None:
        """When the app stops: discovery stops (what it got is kept), and the catalog is closed."""
        for task in [self._task, *self._background]:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:  # (cancelled, or failed: either way, over)
                    pass
        self.catalog.close()
