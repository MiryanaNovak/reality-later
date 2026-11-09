"""Whether a newer version is out (rpapp/updates.py): GitHub's latest release, asked once a day, what's kept
of it, and the notice dismissed. GitHub is played by httpx's MockTransport: nothing goes on the network."""
import asyncio
import json

import httpx
import pytest

from rpapp import __version__, updates
from rpapp.updates import AFTER_FAILURE, EVERY, RELEASES, Updates, is_newer, version_key

T0 = 1_780_000_000


def release(tag: str = 'v0.3.0', **extra) -> dict:
    """GitHub's answer about a release (the part the app reads, and some it doesn't)."""
    return {'tag_name': tag, 'name': f'Reality Later {tag[1:]}', 'draft': False, 'prerelease': False,
            'html_url': f'https://github.com/MiryanaNovak/reality-later/releases/tag/{tag}',
            'published_at': '2026-11-20T10:00:00Z', 'body': 'What\'s new…', 'assets': [], **extra}


class GitHub:
    """Answers like GitHub's API (one answer after another, the last one again), and keeps the requests."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def found(data: dict, etag: str = '"abc"') -> httpx.Response:
    return httpx.Response(200, json=data, headers={'ETag': etag})


def updates_with(tmp_path, github: GitHub, *, current: str = '0.2.0', now: list[float] | None = None) -> Updates:
    clock = now or [float(T0)]
    return Updates(tmp_path, current=current, transport=github.transport, clock=lambda: clock[0])


def test_versions():
    assert version_key('v0.3.0') == version_key('0.3.0') == version_key('0.3') == version_key('V0.3.0.0')
    assert is_newer('0.10.0', '0.9.0') and is_newer('v1.0.0', '0.99') and is_newer('0.2.1', '0.2')
    assert not is_newer('0.2.0', '0.2') and not is_newer('0.1.0', '0.2.0')
    assert is_newer('1.0.0', '1.0.0-rc1') and is_newer('1.0.0-rc2', '1.0.0-rc1') and is_newer('1.0.0-rc1', '0.9')
    for not_one in ('latest', '', 'v', '1..2', 'version 2', '1.0 beta'):
        assert version_key(not_one) is None and not is_newer(not_one, '0.1') and not is_newer('9.9', not_one)
    assert version_key(__version__) is not None  # (this app's own)


async def test_a_newer_release(tmp_path):
    github = GitHub(found(release('v0.3.0')))
    up = updates_with(tmp_path, github)
    assert up.available() is None and up.next_check() == 0  # (never asked: now)
    state = await up.check()
    assert state.error == '' and state.checked_at == state.tried_at == T0 and state.etag == '"abc"'
    got = up.available()
    assert (got.version, got.tag, got.url) == ('0.3.0', 'v0.3.0', release()['html_url'])
    assert got.name == 'Reality Later 0.3.0' and got.published_at == '2026-11-20T10:00:00Z'
    assert up.next_check() == T0 + EVERY  # (then once a day)

    [request] = github.requests
    assert str(request.url) == updates.LATEST
    assert request.headers['user-agent'] == 'Reality-Later/0.2.0'  # (GitHub wants a name: only the app's)
    assert request.headers['accept'] == 'application/vnd.github+json' and 'if-none-match' not in request.headers
    saved = json.loads((tmp_path / 'updates.json').read_text(encoding='utf-8'))
    assert saved['latest']['version'] == '0.3.0' and saved['dismissed'] == ''
    assert Updates(tmp_path, current='0.2.0').available().version == '0.3.0'  # (kept: after a restart too)


async def test_asked_again_github_says_unchanged(tmp_path):
    now = [float(T0)]
    github = GitHub(found(release('v0.3.0')), httpx.Response(304))
    up = updates_with(tmp_path, github, now=now)
    await up.check()
    now[0] += EVERY
    state = await up.check()
    assert github.requests[1].headers['if-none-match'] == '"abc"'  # (an answer that doesn't count against the limit)
    assert state.checked_at == T0 + EVERY and state.error == '' and up.available().version == '0.3.0'


async def test_none_newer(tmp_path):
    for current in ('0.3.0', '0.3.1', '1.0'):
        up = updates_with(tmp_path, GitHub(found(release('v0.3.0'))), current=current)
        await up.check()
        assert up.newer() is None and up.available() is None


async def test_no_release_yet(tmp_path):
    github = GitHub(found(release('v0.3.0')), httpx.Response(404, json={'message': 'Not Found'}))
    up = updates_with(tmp_path, github)
    await up.check()
    state = await up.check()  # (a release deleted, or the repository gone private: nothing to say)
    assert state.latest is None and state.etag == '' and state.error == '' and up.available() is None


@pytest.mark.parametrize('answer, error', [
    (httpx.ConnectError('nope'), 'GitHub couldn\'t be reached (no connection)'),
    (httpx.ReadTimeout('slow'), 'GitHub couldn\'t be reached (it took too long to answer)'),
    (httpx.Response(403, headers={'x-ratelimit-remaining': '0'}),
     'GitHub asked to wait: too many requests from your network'),
    (httpx.Response(429), 'GitHub asked to wait: too many requests from your network'),
    (httpx.Response(403), 'GitHub answered with an error (403)'),
    (httpx.Response(502), 'GitHub answered with an error (502)'),
    (httpx.Response(200, text='<html>'), 'GitHub\'s answer couldn\'t be read'),
    (httpx.Response(200, json={'message': 'Moved'}), 'GitHub\'s answer couldn\'t be read'),
])
async def test_when_it_fails(tmp_path, answer, error):
    now = [float(T0)]
    up = updates_with(tmp_path, GitHub(found(release('v0.3.0')), answer), now=now)
    await up.check()
    now[0] += EVERY
    state = await up.check()
    assert state.error == error and state.tried_at == T0 + EVERY and state.checked_at == T0
    assert up.available().version == '0.3.0'  # (what was known stays)
    assert up.next_check() == T0 + EVERY + AFTER_FAILURE  # (tried again an hour later)


async def test_releases_not_told_about(tmp_path):
    for data in (release('v0.3.0', draft=True), release('v0.3.0', prerelease=True), release('nightly')):
        up = updates_with(tmp_path, GitHub(found(data)))
        state = await up.check()
        assert state.latest is None and state.error == '' and up.available() is None
    up = updates_with(tmp_path, GitHub(found(release('v0.3.0', html_url='https://example.com/get-it'))))
    await up.check()
    assert up.available().url == RELEASES  # (only ever a link to GitHub)


async def test_dismissed_until_a_newer_one(tmp_path):
    github = GitHub(found(release('v0.3.0')), found(release('v0.3.0')), found(release('v0.4.0'), '"def"'))
    up = updates_with(tmp_path, github)
    await up.check()
    up.dismiss()
    assert up.available() is None and up.newer().version == '0.3.0'  # (Settings › Updates still says it's out)
    assert Updates(tmp_path, current='0.2.0').available() is None  # (after a restart too)
    await up.check()
    assert up.available() is None  # (the same release again)
    await up.check()
    assert up.available().version == '0.4.0'  # (a newer one: told about)
    up.dismiss()
    assert up.state.dismissed == '0.4.0' and up.available() is None


async def test_turned_off(tmp_path):
    up = updates_with(tmp_path, GitHub(found(release('v0.3.0'))))
    await up.check()
    up.set_notify(False)
    assert up.available() is None and up.newer().version == '0.3.0'
    up.set_notify(True)
    assert up.available().version == '0.3.0'


def test_a_clock_set_back(tmp_path):
    now = [float(T0)]
    up = updates_with(tmp_path, GitHub(found(release())), now=now)
    up._save(up.state.model_copy(update={'tried_at': T0 + 3 * EVERY, 'checked_at': T0 + 3 * EVERY}))
    assert up.next_check() == 0  # (asked "in the future": asked again now)


def test_a_damaged_file(tmp_path):
    (tmp_path / 'updates.json').write_text('{not json', encoding='utf-8')
    up = Updates(tmp_path, current='0.2.0')
    assert up.available() is None and up.state.latest is None


async def test_in_the_background(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, 'FIRST_CHECK', 0.05)
    github = GitHub(found(release('v0.3.0')))
    up = Updates(tmp_path, current='0.2.0', transport=github.transport)
    up.start()
    try:
        for _ in range(100):
            if github.requests:
                break
            await asyncio.sleep(0.02)
        assert up.available().version == '0.3.0'
        await asyncio.sleep(0.2)
        assert len(github.requests) == 1  # (not again before tomorrow)
    finally:
        await up.stop()
    assert up._task is None


async def test_not_asked_when_off(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, 'FIRST_CHECK', 0.05)
    github = GitHub(found(release('v0.3.0')))
    up = Updates(tmp_path, current='0.2.0', transport=github.transport)
    up.set_notify(False)
    up.start()
    try:
        await asyncio.sleep(0.3)
        assert github.requests == []
        up.set_notify(True)  # (turned on: asked right away, as it's time)
        for _ in range(100):
            if github.requests:
                break
            await asyncio.sleep(0.02)
        assert len(github.requests) == 1
    finally:
        await up.stop()
