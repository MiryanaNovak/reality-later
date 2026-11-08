"""Try Nostr relays for card sharing: what each one says about itself, whether it keeps torrents (NIP-35),
how it answers, and Reality Later's discovery on it.

    python tools/try_relays.py                  the usual free public relays (CANDIDATES)
    python tools/try_relays.py wss://relay...   these
    python tools/try_relays.py --write ...      and publish a test event to each, then withdraw it

Read-only by default: it only asks. With --write, it also publishes a test torrent (kind 2003), with a key
made for the test and thrown away, tagged reality-later-relay-test (not a card: Reality Later never shows
it), checks the relay kept it, withdraws it (NIP-09), and checks the relay deleted it. Then the same with a
test marked NSFW as adult cards are (rating nsfw, and a NIP-36 content-warning), with another key: some
relays filter adult content, and refuse it, or drop it, or block the key. The test events say they're tests,
and nothing more; they're public meanwhile, and relays that don't honor withdrawals keep them.

Needs Python 3.13 (coincurve) and the app's packages. The relays see your address, as with any client.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rpapp.publishing import announcements as fmt  # noqa: E402
from rpapp.publishing import nostr  # noqa: E402
from rpapp.publishing.catalog import Catalog, normalize_relay_url  # noqa: E402
from rpapp.publishing.discovery import Discovery  # noqa: E402
from rpapp.publishing.relay import Relay, RelayError  # noqa: E402

# Free general relays, from https://d-central.tech/nostr-relay-directory/ (July 2026)
CANDIDATES = [
    'wss://relay.damus.io',
    'wss://nos.lol',
    'wss://relay.primal.net',
    'wss://offchain.pub',
    'wss://relay.nostr.net',
    'wss://nostr.oxtr.dev',
    'wss://nostr-pub.wellorder.net',
    'wss://nostrue.com',
    'wss://nostr.mom',
]
TEST_TAG = 'reality-later-relay-test'
TIMEOUT = 15


async def info(url: str) -> dict | str:
    """NIP-11: what the relay says about itself (its http(s) address, asked for application/nostr+json)."""
    address = 'http' + url[2:]  # (ws → http, wss → https)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                     headers={'User-Agent': 'Reality Later'}) as http:
            answer = await http.get(address, headers={'Accept': 'application/nostr+json'})
        data = answer.json()
        return data if isinstance(data, dict) else 'not a description'
    except (httpx.HTTPError, ValueError) as e:
        return f'none ({type(e).__name__})'


async def try_relay(url: str, *, write: bool) -> dict:
    report: dict = {'url': url, 'step': 'connecting'}
    report['info'] = await info(url)
    started = time.monotonic()
    try:
        async with Relay(url, timeout=TIMEOUT) as relay:
            report['connect'] = f'{time.monotonic() - started:.1f} s'
            report['step'] = 'asking for torrents'
            started = time.monotonic()
            torrents = await relay.query([{'kinds': [fmt.KIND_CARD], 'limit': 100}], max_events=1000)
            report['torrents'] = len(torrents)
            report['answer'] = f'{time.monotonic() - started:.1f} s'
            report['cards'] = len(await relay.query([{'kinds': [fmt.KIND_CARD], '#t': [fmt.MARKER], 'limit': 100}]))
            dates = sorted(e['created_at'] for e in torrents if type(e.get('created_at')) is int)
            if dates:
                report['newest'], report['oldest'] = _date(dates[-1]), _date(dates[0])
                report['max_per_query'] = len(await relay.query([{'kinds': [fmt.KIND_CARD], 'limit': 1000}],
                                                                max_events=5000))
                second = dates[len(dates) // 2]
                same = await relay.query([{'kinds': [fmt.KIND_CARD], 'until': second, 'limit': 100}])
                report['until'] = 'includes its second (NIP-01)' if any(e.get('created_at') == second for e in same) \
                    else 'leaves its second out'
            if write:
                report.update(await write_test(relay, report))
                report.update({f'nsfw_{k}': v for k, v in (await write_test(relay, report, nsfw=True)).items()})
            report['notices'] = relay.notices[:3]
    except RelayError as e:
        report['error'] = f'{e} (while {report["step"]})'
        return report
    # Reality Later's own discovery, read-only, into a catalog thrown away
    with tempfile.TemporaryDirectory() as folder, Catalog(Path(folder) / 'catalog.sqlite3') as catalog:
        result = await Discovery(catalog, timeout=TIMEOUT).discover(relays=[url])
        r = result.relays[0]
        report['discovery'] = (f'{r.added} publications, {r.withdrawals} withdrawals, {r.rejected} refused'
                               if r.success else f'failed: {r.error}')
    return report


async def write_test(relay: Relay, report: dict, *, nsfw: bool = False) -> dict:
    """Publish a test torrent with a key made for it, check it's kept, withdraw it, check it's gone. `nsfw`:
    marked as adult cards are (their rating, and a NIP-36 content-warning: docs/card-announcements.md)."""
    secret = nostr.new_secret()
    now = int(time.time())
    title = 'Reality Later relay test' + (' (marked NSFW, as adult cards are)' if nsfw else '')
    tags = [['t', TEST_TAG], ['title', title], ['x', os.urandom(20).hex()], ['file', 'test.txt', '1'],
            ['alt', 'A relay test, withdrawn right after']]
    if nsfw:
        tags += [['rating', 'nsfw'], ['content-warning', 'nsfw']]
    event = nostr.sign(secret, kind=fmt.KIND_CARD, created_at=now, content='A test of this relay by Reality Later '
                       '(card sharing): withdrawn right after.', tags=tags)
    out: dict = {}
    report['step'] = 'publishing the NSFW-marked test event' if nsfw else 'publishing the test event'
    accepted, said = await relay.publish(event)
    out['write'] = 'accepted' if accepted else f'refused ({said or "no reason given"})'
    if not accepted:
        return out
    await asyncio.sleep(1)
    report['step'] = 'looking for the test event'
    out['kept'] = 'yes' if await relay.query([{'ids': [event.id]}]) else 'no (not found right after)'
    withdrawal = nostr.sign(secret, kind=fmt.KIND_WITHDRAWAL, created_at=now + 1, content='Relay test over.',
                            tags=[['e', event.id], ['k', str(fmt.KIND_CARD)]])
    report['step'] = 'withdrawing the test event'
    accepted, said = await relay.publish(withdrawal)
    if not accepted:
        out['withdrawal'] = f'refused ({said or "no reason given"})'
        return out
    await asyncio.sleep(2)
    out['withdrawal'] = 'honored: deleted' if not await relay.query([{'ids': [event.id]}]) \
        else 'kept the withdrawal, but not deleted (yet)'
    return out


def _date(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%d')


def show(report: dict) -> None:
    print(f'\n== {report["url"]}')
    about = report['info']
    if isinstance(about, dict):
        limits = about.get('limitation') or {}
        software = f'{about.get("software") or "?"} {about.get("version") or ""}'.strip()
        print(f'  says it is:   {about.get("name") or "?"} ({software})')
        nips = about.get('supported_nips') or []
        print(f'  supports:     NIP-09 (withdrawals) {"yes" if 9 in nips else "not said"}, '
              f'NIP-35 (torrents) {"yes" if 35 in nips else "not said"}')
        rules = [f'{k}={limits[k]}' for k in ('auth_required', 'payment_required', 'restricted_writes', 'max_limit',
                                               'max_message_length', 'max_content_length', 'created_at_lower_limit',
                                               'created_at_upper_limit') if k in limits]
        print(f'  its limits:   {", ".join(rules) or "not said"}')
        if about.get('description'):
            print(f'  describes it: {" ".join(str(about["description"]).split())[:300]}')
        for key, label in (('tags', 'tags'), ('relay_countries', 'countries'), ('language_tags', 'languages')):
            if isinstance(about.get(key), list) and about[key]:  # (NIP-11's: tags such as "sfw-only"...)
                print(f'  {label + ":":13} {", ".join(map(str, about[key]))}')
        if 'sfw-only' in (about.get('tags') or []):
            print('  NOTE:         it says it\'s SFW only: adult cards don\'t belong there')
        for key in ('posting_policy', 'terms_of_service'):
            if about.get(key):
                print(f'  {key.replace("_", " ")}: {about[key]}')
    else:
        print(f'  says it is:   {about}')
    if 'error' in report:
        print(f'  CAN\'T USE:    {report["error"]}')
        return
    print(f'  connects in:  {report["connect"]}; answers in {report["answer"]}')
    print(f'  torrents:     {report["torrents"]} (of the 100 newest asked)'
          + (f', from {report["oldest"]} to {report["newest"]}' if 'newest' in report else ''))
    if 'max_per_query' in report:
        print(f'  per query:    gives {report["max_per_query"]} when asked for 1000; `until` {report["until"]}')
    print(f'  cards:        {report["cards"]} (with Reality Later\'s tag)')
    for key, label in (('write', 'publishing'), ('kept', 'kept it'), ('withdrawal', 'withdrawal'),
                       ('nsfw_write', 'NSFW-marked'), ('nsfw_kept', 'kept it'), ('nsfw_withdrawal', 'withdrawal')):
        if key in report:
            print(f'  {label + ":":13} {report[key]}')
    print(f'  discovery:    {report["discovery"]}')
    for notice in report['notices']:
        print(f'  it said:      {notice}')


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('relays', nargs='*', help='relays to try (default: the usual free public ones)')
    parser.add_argument('--write', action='store_true',
                        help='also publish test events (one of them marked NSFW), then withdraw them')
    args = parser.parse_args()
    urls = []
    for url in args.relays or CANDIDATES:
        try:
            urls.append(normalize_relay_url(url))
        except ValueError as e:
            print(e)
    print(f'Trying {len(urls)} relays{" (with test events, one marked NSFW)" if args.write else ""}...')
    reports = await asyncio.gather(*(try_relay(u, write=args.write) for u in urls))
    for report in reports:
        show(report)

    print('\n== In short')
    for r in reports:
        if 'error' in r:
            verdict = f'can\'t use: {r["error"]}'
        else:
            verdict = f'{r["torrents"]} torrents' + (f', write {r["write"]}' if 'write' in r else '') + \
                (f', withdrawal {r["withdrawal"]}' if 'withdrawal' in r else '') + \
                (f'; NSFW-marked: {r["nsfw_write"]}' if 'nsfw_write' in r else '') + \
                (f', kept: {r["nsfw_kept"]}' if 'nsfw_kept' in r else '')
        print(f'  {r["url"]:40} {verdict}')


if __name__ == '__main__':
    asyncio.run(main())
