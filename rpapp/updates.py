"""Whether a newer version of the app is out: its latest release on GitHub, asked about once a day while the
app runs, for the notice at the top of every page (ui/update_notice.py). Dismissed, the notice comes back
only for a newer release still. Settings › Updates turns it all off.

    updates = Updates(data_dir)             # what's known, kept in data/updates.json
    updates.start(); await updates.stop()   # the checks, in the background (main.py: not in the tests)
    updates.available()                     # the release to tell about (None: none newer, dismissed, or off)
    updates.dismiss()
    await updates.check()                   # now (Settings › Updates › Check now)

What's asked: GitHub's API for the repository's latest release (neither a draft nor a pre-release). GitHub
sees this computer's internet address, and that the request comes from this app (it wants a name: the
app's, with its version); nothing else is sent. Asked again with the answer's ETag, GitHub only says
whether it changed, which doesn't count against its limit (60 requests an hour from a network). When it
can't be reached, it's asked again an hour later.

A release's version is its tag without the v (v0.3.0: 0.3.0), compared number by number with this app's
(rpapp.__version__, which the release workflows make sure is its tag's: packaging/check_version.py); with
a suffix (0.3.0-rc1), it comes before the same numbers without one.

Kept apart from settings.json, like the Home page's tips (home.py), so that a Settings page open in another
tab, which saves the settings it loaded, can't bring back a notice just dismissed.
"""
from __future__ import annotations

import asyncio
import logging
import platform
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

from . import APP_NAME, PROJECT_URL, __version__
from .paths import in_flatpak
from .storage import JsonStore, StoredModel

log = logging.getLogger(__name__)

REPOSITORY = PROJECT_URL.removeprefix('https://github.com/')  # ('MiryanaNovak/reality-later')
LATEST = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
RELEASES = f'{PROJECT_URL}/releases'
UPDATES_ID = 'updates'

FIRST_CHECK = 60          # seconds after the app starts (once it's up), if it's time
EVERY = 24 * 3600         # then once a day
AFTER_FAILURE = 3600      # an hour after GitHub couldn't be reached, or asked to wait
NAP = 600                 # the background task looks at the time at least this often (a clock set back...)
TIMEOUT = 20.0            # seconds, for GitHub to answer


class Release(BaseModel):
    """A release on GitHub, as its latest one was when last asked."""
    model_config = ConfigDict(extra='ignore', frozen=True)

    version: str             # its tag's: '0.3.0'
    tag: str                 # 'v0.3.0'
    url: str                 # its page: what's new in it, and its files
    name: str = ''           # its title, if it has one
    published_at: str = ''   # ISO 8601, as GitHub writes it


class UpdateState(StoredModel):
    """data/updates.json."""
    id: str = UPDATES_ID
    name: str = Field('', exclude=True)   # from StoredModel; unused
    latest: Release | None = None         # GitHub's latest release, when it last answered (None: none yet)
    etag: str = ''                        # that answer's: GitHub then only says whether it changed
    checked_at: int | None = None         # when GitHub last answered (Unix seconds)
    tried_at: int | None = None           # when it was last asked
    error: str = ''                       # why that failed ('': it didn't)
    dismissed: str = ''                   # the version whose notice was dismissed


def version_key(text: str) -> tuple | None:
    """'0.3.0', 'v0.3.0' → something to compare versions with (None: not a version). Trailing zeros don't
    count (1.0 is 1.0.0); a suffix (-rc1, b2...) comes before the same numbers without one."""
    found = re.fullmatch(r'[vV]?(\d{1,9}(?:\.\d{1,9}){0,9})([-.+_]?[0-9A-Za-z][0-9A-Za-z.+_-]*)?', text.strip())
    if found is None:
        return None
    numbers = [int(n) for n in found.group(1).split('.')]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    suffix = found.group(2) or ''
    return tuple(numbers), 0 if suffix else 1, suffix


def is_newer(version: str, than: str) -> bool:
    a, b = version_key(version), version_key(than)
    return a is not None and b is not None and a > b


def how_to_update() -> str:
    """What to get from the release's page, for the way this copy of the app was installed (the files the
    release workflows attach: .github/workflows)."""
    if in_flatpak():
        machine = platform.machine()
        file = f'reality-later-{machine}.flatpak' if machine in ('x86_64', 'aarch64') else 'the .flatpak file'
        return f'Download {file} from its page and open it: it installs over this version, and your data stays.'
    if getattr(sys, 'frozen', False) and sys.platform == 'win32':
        return 'Download reality-later-setup.exe from its page and run it: it installs over this version, and ' \
               'your data stays.'
    return 'Its page says what\'s new.'


class Updates:
    def __init__(self, data_dir: str | Path, *, current: str = __version__, url: str = LATEST,
                 transport: httpx.AsyncBaseTransport | None = None, clock: Callable[[], float] = time.time) -> None:
        """`current`: this app's version. `url`, `transport`, `clock`: for the tests."""
        self.current = current
        self.url = url
        self.transport = transport
        self.clock = clock
        self.notify = True           # Settings › Updates (build_root gives it; set_notify() changes it)
        self._store = JsonStore(Path(data_dir), UpdateState.model_validate)
        try:
            self._state = self._store.get(UPDATES_ID) or UpdateState()
        except ValueError as e:      # (a damaged file: as if nothing were known yet; rewritten next time)
            log.warning('Starting %s afresh: %s', self._store.path_for(UPDATES_ID), e)
            self._state = UpdateState()
        self._lock = asyncio.Lock()
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

    # --- what's known -------------------------------------------------------------------------------

    @property
    def state(self) -> UpdateState:
        return self._state

    def newer(self) -> Release | None:
        """GitHub's latest release, if it's newer than this app (dismissed or not)."""
        latest = self._state.latest
        return latest if latest is not None and is_newer(latest.version, self.current) else None

    def available(self) -> Release | None:
        """The newer release to tell about: None if there's none, if its notice was dismissed (and there's
        none newer still), or if Settings › Updates says not to."""
        release = self.newer()
        if release is None or not self.notify:
            return None
        if self._state.dismissed and not is_newer(release.version, self._state.dismissed):
            return None
        return release

    def dismiss(self) -> None:
        """No notice for this release any more: only for a newer one."""
        if (release := self.newer()) is not None and self._state.dismissed != release.version:
            self._save(self._state.model_copy(update={'dismissed': release.version}))

    def set_notify(self, on: bool) -> None:
        """Settings › Updates' switch: on, GitHub is asked soon if it's time."""
        self.notify = on
        if on and self._wake is not None:
            self._wake.set()

    def next_check(self) -> float:
        """When GitHub is to be asked next (Unix seconds; 0: now)."""
        state, now = self._state, self.clock()
        if state.tried_at is None or state.tried_at > now:  # (never, or a clock set back since)
            return 0
        if state.error:
            return state.tried_at + AFTER_FAILURE
        return (state.checked_at or state.tried_at) + EVERY

    # --- asking GitHub ------------------------------------------------------------------------------

    async def check(self) -> UpdateState:
        """Ask GitHub now. Never raises: what went wrong is the state's `error`."""
        async with self._lock:
            state, now = self._state, int(self.clock())
            headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                       'User-Agent': f'{APP_NAME.replace(" ", "-")}/{self.current}'}
            if state.etag and state.latest is not None:
                headers['If-None-Match'] = state.etag
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT, transport=self.transport,
                                             follow_redirects=True) as client:
                    response = await client.get(self.url, headers=headers)
            except httpx.HTTPError as e:
                return self._failed(f'GitHub couldn\'t be reached ({_reason(e)})', now)
            if response.status_code == 304:  # (unchanged)
                return self._save(state.model_copy(update={'checked_at': now, 'tried_at': now, 'error': ''}))
            if response.status_code == 404:  # (no release yet)
                return self._save(state.model_copy(update={'latest': None, 'etag': '', 'checked_at': now,
                                                           'tried_at': now, 'error': ''}))
            if response.status_code == 429 or (response.status_code == 403
                                               and response.headers.get('x-ratelimit-remaining') == '0'):
                return self._failed('GitHub asked to wait: too many requests from your network', now)
            if response.status_code != 200:
                return self._failed(f'GitHub answered with an error ({response.status_code})', now)
            try:
                latest = _release(response.json())
            except ValueError:
                return self._failed('GitHub\'s answer couldn\'t be read', now)
            return self._save(state.model_copy(update={'latest': latest, 'etag': response.headers.get('etag', ''),
                                                       'checked_at': now, 'tried_at': now, 'error': ''}))

    def _failed(self, error: str, now: int) -> UpdateState:
        log.info('Checking for a newer version: %s', error)
        return self._save(self._state.model_copy(update={'tried_at': now, 'error': error}))

    def _save(self, state: UpdateState) -> UpdateState:
        self._state = state
        try:
            self._store.save(state)
        except OSError as e:  # (kept in memory meanwhile: a full disk shouldn't stop the app)
            log.warning('Can\'t save %s: %s', self._store.path_for(UPDATES_ID), e)
        return state

    # --- in the background --------------------------------------------------------------------------

    def start(self) -> None:
        """Ask GitHub once a day while the app runs (if Settings › Updates says to): a minute after it
        starts if it's time, then when it's time again."""
        if self._task is None:
            self._wake = asyncio.Event()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        await self._nap(FIRST_CHECK)
        while True:
            if self.notify and self.clock() >= self.next_check():
                await self.check()
            await self._nap(min(NAP, max(1.0, self.next_check() - self.clock())) if self.notify else NAP)

    async def _nap(self, seconds: float) -> None:
        assert self._wake is not None
        try:
            await asyncio.wait_for(self._wake.wait(), seconds)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()


def _release(data: object) -> Release | None:
    """GitHub's latest release, from its answer (None: one whose tag isn't a version). ValueError if it
    isn't an answer about a release."""
    if not isinstance(data, dict) or not isinstance(data.get('tag_name'), str):
        raise ValueError('not a release')
    tag = data['tag_name'].strip()
    if data.get('draft') or data.get('prerelease') or version_key(tag) is None:
        return None
    url = data.get('html_url')
    if not isinstance(url, str) or not url.startswith('https://github.com/'):  # (only ever a link to GitHub)
        url = RELEASES
    name, published = data.get('name'), data.get('published_at')
    return Release(version=tag.removeprefix('v').removeprefix('V'), tag=tag, url=url,
                   name=name.strip() if isinstance(name, str) else '',
                   published_at=published if isinstance(published, str) else '')


def _reason(e: httpx.HTTPError) -> str:
    if isinstance(e, httpx.TimeoutException):
        return 'it took too long to answer'
    if isinstance(e, httpx.ConnectError):
        return 'no connection'
    return str(e) or type(e).__name__
