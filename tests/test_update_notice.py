"""The notice above every page when a newer version is out (rpapp/ui/update_notice.py), and Settings › Updates.
GitHub is played by httpx's MockTransport (test_updates.py's)."""
import asyncio
import json

import httpx
import pytest
from nicegui.testing import user_simulation

from rpapp.settings import SettingsStore
from rpapp.ui import update_notice
from rpapp.ui.app import build_root
from rpapp.updates import Updates
from test_updates import GitHub, found, release


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(update_notice, 'TICK', 0.1)  # (each tab looks every 30 s: here, at once)


async def known(tmp_path, *answers, current='0.2.0') -> tuple[Updates, GitHub]:
    """The app's Updates, after asking GitHub once (its first answer)."""
    github = GitHub(*answers)
    updates = Updates(tmp_path, current=current, transport=github.transport)
    await updates.check()
    return updates, github


async def test_on_every_page_until_dismissed(tmp_path):
    updates, _ = await known(tmp_path, found(release('v0.3.0')))
    async with user_simulation(build_root(tmp_path, updates=updates)) as user:
        await user.open('/')
        await user.should_see('Reality Later 0.3.0 is out')
        await user.should_see('You have 0.2.0. Its page says what\'s new.')
        get = user.find(marker='update-get').elements.pop()
        assert get.props['href'] == release('v0.3.0')['html_url'] and get.props['target'] == '_blank'
        for page in ('nav-characters', 'nav-settings', 'nav-conversations'):
            user.find(marker=page).click()
            await user.should_see(marker='update-notice')  # (above every page)
        user.find(marker='update-dismiss').click()
        await user.should_not_see(marker='update-notice')
        await user.open('/characters')
        await user.should_see('Characters')
        await user.should_not_see(marker='update-notice')  # (dismissed: for good)
    assert json.loads((tmp_path / 'updates.json').read_text(encoding='utf-8'))['dismissed'] == '0.3.0'


async def test_back_for_a_newer_release(tmp_path):
    updates, _ = await known(tmp_path, found(release('v0.3.0')), found(release('v0.4.0'), '"new"'))
    updates.dismiss()
    async with user_simulation(build_root(tmp_path, updates=updates)) as user:
        await user.open('/')
        await user.should_see('Recent conversations')
        await user.should_not_see(marker='update-notice')
        await updates.check()  # (the next day: a newer one is out)
        await user.should_see('Reality Later 0.4.0 is out')  # (shown without reloading the page)


async def test_nothing_to_say(tmp_path):
    updates, _ = await known(tmp_path, found(release('v0.3.0')), current='0.3.0')
    async with user_simulation(build_root(tmp_path, updates=updates)) as user:
        await user.open('/')
        await user.should_see('Recent conversations')
        await user.should_not_see(marker='update-notice')


async def test_the_setting(tmp_path):
    updates, _ = await known(tmp_path, found(release('v0.3.0')))
    async with user_simulation(build_root(tmp_path, updates=updates)) as user:
        await user.open('/settings')
        await user.should_see(marker='update-notice')
        user.find('Updates').click()
        await user.should_see('This is version 0.2.0.')
        await user.should_see('0.3.0 is out.')
        user.find(marker='updates-notify').click()  # (off)
        await user.should_not_see(marker='update-notice')
        assert updates.notify is False
    SettingsStore(tmp_path).flush()
    assert json.loads((tmp_path / 'settings.json').read_text(encoding='utf-8'))['updates'] == {'notify': False}


async def test_off_when_the_app_starts(tmp_path):
    updates, _ = await known(tmp_path, found(release('v0.3.0')))
    store = SettingsStore(tmp_path)
    settings = store.load()
    settings.updates.notify = False
    store.save(settings)
    async with user_simulation(build_root(tmp_path, updates=updates)) as user:
        await user.open('/')
        await user.should_see('Recent conversations')
        await user.should_not_see(marker='update-notice')
        assert updates.notify is False


async def test_check_now(tmp_path):
    github = GitHub(httpx.Response(404), found(release('v0.3.0')), httpx.ConnectError('down'))
    updates = Updates(tmp_path, current='0.2.0', transport=github.transport)
    async with user_simulation(build_root(tmp_path, updates=updates)) as user:
        await user.open('/settings')
        user.find('Updates').click()
        await user.should_see('Not checked yet.')
        user.find(marker='updates-check-now').click()
        await user.should_see('It\'s the latest (checked just now).')  # (no release yet: nothing newer)
        user.find(marker='updates-check-now').click()
        await user.should_see('0.3.0 is out.')
        await user.should_see(marker='update-notice')
        updates.dismiss()
        user.find(marker='updates-check-now').click()  # (GitHub can't be reached: what's known stays)
        for _ in range(100):
            if len(github.requests) == 3 and not updates._lock.locked():
                break
            await asyncio.sleep(0.02)
        assert updates.state.error == 'GitHub couldn\'t be reached (no connection)'
        await user.should_see('0.3.0 is out.')
    assert len(github.requests) == 3


async def test_couldnt_check(tmp_path):
    updates, _ = await known(tmp_path, httpx.Response(502))
    async with user_simulation(build_root(tmp_path, updates=updates)) as user:
        await user.open('/settings')
        user.find('Updates').click()
        await user.should_see('Couldn\'t check (just now): GitHub answered with an error (502).')
