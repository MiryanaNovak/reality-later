"""Shared cards: browsing the cards people publish (rpapp/publishing/), publishing yours, and Settings ›
Sharing cards.

    /shared               other people's cards: the latest version of each, newest first. Searched by words
                          (from their start) in names, or descriptions too; filtered by publisher name, tags,
                          tags excluded. The filters are in the page's address (list_page())
    /published            your publications: those of your keys (the one in use, and those it replaced), each
                          one's state; the withdrawn ones apart
    /shared/{event_id}    one publication: its description, details, publisher and versions; blocking.
                          Someone else's: adding it to your characters (downloading it), and what became
                          of it. One of yours: how its file is shared, where its announcement arrived,
                          sending it again, stopping sharing, withdrawing

A downloaded card's character says where it came from, on its page (character_note()): withdrawn since,
or a newer version out.

Publishing starts from a character's ⋮ menu (publish_dialog(), from characters_page.py): the card's name,
description, tags, rating and language, then the card file exported as for Export (with the card's UUID
in it, so that publishing it again makes a new version). The first time, a wizard comes first: what
publishing means (agreed to), the publisher key (made or imported, and backed up: there's no recovering
it), how the file is shared (the port, the router). Settings › Sharing cards has all of it too.

Looking for new cards runs in the background (Sharing.discover()): when the page opens, if the last look
is an hour old, or with its button; the page shows how far it is, and what it found once done.

Everything shown comes from strangers: it's shown as text, never as HTML, and nothing is loaded from
elsewhere (no pictures: those come with the files, later).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from nicegui import ui

from ..cards import SafeguardError, check_shared_card, megabytes, png_text_chunks
from ..publishing import runtime_problem, sentence
from ..publishing import announcements as fmt
from ..publishing.announcements import PublicationDraft, Rejected
from ..publishing.catalog import CardQuery, CatalogEntry, Delivery, normalize_relay_url
from ..publishing.identity import Identity, IdentityError
from ..publishing.nostr import to_npub
from ..publishing.seeder import DEFAULT_PORT, ROUTER_WAIT, NetworkStatus, TorrentStatus
from ..publishing.service import DEFAULT_RELAYS, Published, Sharing, SharingError, retry_at, torrent_problem
from ..settings import Settings, SettingsStore
from .form import Form, keep_typed
from .list_view import Link, Row, plural, render_row
from .widgets import browser_languages, confirm, not_found, page_bar, panel, time_ago

if TYPE_CHECKING:
    from ..characters import Character, CharacterStore
    from ..publishing.announcements import Publication
    from ..publishing.catalog import Source
    from ..publishing.service import Fetch

log = logging.getLogger(__name__)

PAGE = 50
# The notice before publishing (the wizard's first step): raised when it changes in a way that matters, so
# that it's shown, and agreed to, again (settings.sharing.publishing_accepted).
NOTICE_VERSION = 1
WIZARD_STEPS = {'notice': 'Before you publish', 'key': 'Your publisher key', 'sharing': 'Sharing its file',
                'card': 'Your card'}
KEY_FILE = 'reality-later-publisher-key.txt'      # the backup's name, saved as a file
KEY_PLACE = 'certificates/nostr-key.txt, in the settings folder'


def short_key(public_key: str) -> str:
    """A publisher's key, short enough to show next to their name: npub1abcd…wxyz."""
    npub = to_npub(public_key)
    return f'{npub[:10]}…{npub[-4:]}'


def size_text(size: int) -> str:
    return f'{size / 1024 / 1024:.1f} MB' if size >= 1024 * 1024 else f'{max(1, round(size / 1024))} KB'


LANGUAGES = {'en': 'English', 'fr': 'French', 'es': 'Spanish', 'de': 'German', 'it': 'Italian', 'pt': 'Portuguese',
             'nl': 'Dutch', 'pl': 'Polish', 'ru': 'Russian', 'uk': 'Ukrainian', 'ja': 'Japanese', 'zh': 'Chinese',
             'ko': 'Korean', 'ar': 'Arabic', 'tr': 'Turkish', 'sv': 'Swedish', 'id': 'Indonesian', 'vi': 'Vietnamese'}


def language_text(code: str | None) -> str:
    return 'Not said' if not code else LANGUAGES.get(code, code.upper())


def format_text(spec: str) -> str:
    """'chara_card_v2' → 'Character card V2' (another format: as it's written)."""
    return f'Character card {spec[len("chara_card_"):].upper()}' if spec.startswith('chara_card_') else spec


def date_text(seconds: int) -> str:
    """A date and time, on this computer's clock: '6 Nov 2026, 23:55'."""
    when = _when(seconds).astimezone()
    return f'{when.day} {when:%b %Y, %H:%M}'


def _when(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, timezone.utc)


def rejected_text(e: Rejected) -> str:
    """Why a card can't be published, in plain words (the format's rules: docs/card-announcements.md)."""
    return {
        'empty:title': 'Give the card a name.',
        'too_long:title': f'The card\'s name is too long: {fmt.MAX_TITLE} characters at most.',
        'empty:publisher': 'Give your publisher name.',
        'too_long:publisher': f'Your publisher name is too long: {fmt.MAX_PUBLISHER} characters at most.',
        'too_long:description': f'The description is too long: {fmt.MAX_DESCRIPTION_BYTES // 1024} KB at most '
                                f'(about {fmt.MAX_DESCRIPTION_BYTES // 1000},000 letters).',
        'file_too_big': f'The card\'s file is too big to share: {megabytes(fmt.MAX_FILE_BYTES)} at most.',
        'control_character': 'The name or the description has invisible control characters: remove them.',
        'too_large': 'Its announcement would be too big: shorten the description, or remove tags.',
    }.get(e.code, f'It can\'t be published ({e}).')


DELIVERY = {  # state: (icon, color, what it means)
    'accepted': ('check_circle', 'positive', 'Arrived'),
    'held': ('pause_circle', 'grey', 'Held: this version of the app only publishes to relays on this computer or its network'),
    'pending': ('schedule', 'warning', 'Not reached yet'),
    'refused': ('block', 'negative', 'Refused'),
}


def delivery_text(d: Delivery, *, retried: bool = True, now: float | None = None) -> str:
    """What became of a delivery, in words, and when it's tried again by itself if it is (`retried`: its
    relay is still written to)."""
    text = DELIVERY.get(d.state, ('', '', d.state))[2]
    if d.message and d.state in ('pending', 'refused'):
        text = f'{text}: {d.message.rstrip(". ")}'
    if retried and (at := retry_at(d)) is not None:
        text += f'. Tried again by itself {_in(at - (time.time() if now is None else now))}.'
    return text


def _in(seconds: float) -> str:
    """'in a moment', 'in a minute', 'in 25 minutes', 'in an hour', 'in 6 hours'."""
    if seconds < 30:
        return 'in a moment'
    if seconds < 90:
        return 'in a minute'
    if seconds < 3300:
        return f'in {round(seconds / 60)} minutes'
    if seconds < 5400:
        return 'in an hour'
    return f'in {round(seconds / 3600)} hours'


def delivery_summary(deliveries: Iterable[Delivery]) -> str:
    """'arrived on 2 relays, 1 not reached yet (tried again by itself), refused by 1, 6 held (public relays:
    this version only publishes to local ones)'."""
    deliveries = list(deliveries)
    if not deliveries:
        return 'no relay to send it to (Settings › Sharing cards)'
    counts = Counter(d.state for d in deliveries)
    later = sum(d.may_arrive for d in deliveries)
    parts = []
    if counts['accepted']:
        parts.append(f'arrived on {plural(counts["accepted"], "relay")}')
    if later:
        parts.append(f'{plural(later, "relay")} not reached yet (tried again by itself)')
    if refused := counts['pending'] + counts['refused'] - later:
        parts.append(f'refused by {plural(refused, "relay")}')
    if counts['held']:
        parts.append(f'{counts["held"]} held (public relays: this version only publishes to local ones)')
    return ', '.join(parts)


def save_key_file(key: Identity) -> None:
    """The key's backup, as a file the browser saves (its secret half: shown only when asked)."""
    ui.download.content(f'{key.nsec()}\n'.encode('ascii'), KEY_FILE, 'text/plain')


def fetch_text(fetch: Fetch) -> str:
    """How far a download is, in words."""
    if fetch.stage == 'looking':
        return 'Looking for people sharing it…' + (f' ({_people(fetch.peers)} found)' if fetch.peers else '')
    if fetch.stage == 'downloading':
        return f'Downloading: {int(fetch.progress * 100)}%' + (f', from {_people(fetch.peers)}' if fetch.peers else '')
    return 'Adding it to your characters…'


def _fetch_key(fetch: Fetch | None) -> tuple | None:
    """What a page shows of a download: when it changes, the page is drawn again."""
    return (fetch.stage, round(fetch.progress, 2), fetch.peers, fetch.note, fetch.character_id) if fetch else None


def _listed(text: str) -> list[str]:
    """'a, b,c' → ['a', 'b', 'c'] (tags in a page's address)."""
    return [t.strip() for t in text.split(',') if t.strip()]


def _people(n: int) -> str:
    return '1 person' if n == 1 else f'{n} people'


def network_lines(st: NetworkStatus, *, now: float | None = None) -> list[tuple[str, str, str]]:
    """How others reach this computer, in words: (icon, color, text) lines for Settings › Sharing cards."""
    now = time.time() if now is None else now
    port = st.wanted_port or DEFAULT_PORT
    if not st.started:
        return [('radio_button_unchecked', 'grey', 'Not listening yet: the port opens once there\'s a card to share '
                                                   'or to download.')]
    if not st.port:
        return [('error_outline', 'negative', f'Can\'t listen on port {port}: {st.listen_error or "not yet"}. '
                                              f'Another program may use it: choose another one.')]
    lines = [('check_circle', 'positive', f'Listening on port {st.port} (TCP and UDP).'
              + (f' {DEFAULT_PORT} was taken.' if not st.wanted_port and st.port != DEFAULT_PORT else ''))]
    if not st.port_mapping:
        lines.append(('info', 'primary', f'The router isn\'t asked: open port {st.port} (TCP and UDP) in its settings, '
                                         f'to this computer.'))
    elif opened := sorted({(t, p) for t, p, external in st.mapped}):
        externals = sorted({external for _, _, external in st.mapped})
        how = ', '.join(sorted({t for t, _ in opened}))
        on = f'port {externals[0]}' if len(externals) == 1 else 'ports ' + ', '.join(map(str, externals))
        lines.append(('check_circle', 'positive', f'The router opened it ({how}): {on} on the internet side.'))
    elif st.mapping_errors:
        said = '; '.join(f'{t}: {e}' for t, e in st.mapping_errors)
        lines.append(('error_outline', 'negative', f'The router didn\'t open it ({said}). You can open port {st.port} '
                                                   f'(TCP and UDP) in its settings yourself.'))
    elif st.asking_since is not None and now - st.asking_since < ROUTER_WAIT:
        lines.append(('hourglass_empty', 'primary', 'Asking the router to open it…'))
    else:
        lines.append(('help_outline', 'warning', f'No answer from the router: it may not offer UPnP or NAT-PMP, or '
                                                 f'have them off. You can open port {st.port} (TCP and UDP) in its '
                                                 f'settings yourself.'))
    if st.reached_at is not None:
        lines.append(('public', 'positive', f'Reachable from the internet: someone connected from outside '
                                            f'{time_ago(_when(int(st.reached_at)))} ({st.reached_by}).'))
    else:
        lines.append(('help_outline', 'grey', 'Not confirmed yet: nobody from the internet has connected since the '
                                              'app started. That can simply mean nobody wanted your cards yet.'))
    where = f' on {st.external_address}' if st.external_address else ''
    lines.append(('travel_explore', 'grey', f'To check it yourself, while the app shares a card: a port checking '
                                            f'website should find port {st.port} open{where}.'))
    return lines


def sharing_text(status: TorrentStatus | str | None, *, withdrawn: bool) -> tuple[str, str]:
    """How a publication's file is shared from here: (icon, text); a problem's icon is 'error_outline'."""
    if isinstance(status, str):
        return 'error_outline', status
    if status is None:
        return 'cloud_off', ('No longer shared: it was withdrawn.' if withdrawn else
                             'Not shared from this computer. To share it again, publish its character again.')
    if status.state == 'error':
        return 'error_outline', f'Sharing its file failed: {status.error}'
    if status.state != 'seeding':
        return 'hourglass_empty', 'Getting ready to share its file…'
    peers = 'nobody downloading it now' if not status.peers else f'{_people(status.peers)} connected'
    sent = size_text(status.uploaded) if status.uploaded else 'nothing'
    return 'cloud_upload', f'Shared from this computer while the app runs: {peers}, {sent} sent since the app started.'


async def changed_since(character: Character, p: Publication, source: Source | None, *,
                        fingerprint: Callable[[Character], str] | None, export: Callable[[Character], bytes] | None,
                        known: dict[tuple[str, str], bool] | None = None) -> bool | None:
    """Whether the card a character makes now differs from the one published (`p`): by the card's fingerprint
    kept when it was published (`source`), or else, published before that was kept, by the card's file (the
    one that would be published now, against the one that was). None: can't tell. `known`: the answers so far
    for the files, by what the character was then (its fingerprint), so that a card isn't drawn again (a PNG,
    its picture compressed: a while) as long as the character doesn't change."""
    try:
        if source is not None and fingerprint is not None:
            return fingerprint(character) != source.fingerprint
        if export is None:
            return None
        key = (p.event_id, fingerprint(character) if fingerprint is not None else character.model_dump_json())
        if known is not None and key in known:
            return known[key]
        content = await asyncio.to_thread(export, character)
    except Exception:  # (a damaged picture...: can't tell)
        log.warning('Can\'t tell whether %s changed since it was published', character.id, exc_info=True)
        return None
    answer = hashlib.sha256(content).hexdigest() != p.card_sha256
    if known is not None:
        known[key] = answer
    return answer


class SharingSection:
    def __init__(self, sharing: Sharing | None, settings: SettingsStore, *, problem: str = '') -> None:
        self.sharing = sharing      # None: sharing can't run here, and `problem` says why
        self.settings = settings
        self.problem = problem
        self.characters: CharacterStore | None = None  # where downloaded cards become characters (the app's)
        # What a character's card holds (a fingerprint), and the card itself: to tell whether a character
        # changed since it was published (characters_page.py's; the app sets them)
        self.card_fingerprint: Callable[[Character], str] | None = None
        self.card_export: Callable[[Character], bytes] | None = None

    # --- the catalog ------------------------------------------------------------------------------

    async def list_page(self, q: str = '', where: str = '', by: str = '', tags: str = '', exclude: str = '',
                        publisher: str = '', tag: str = '') -> None:
        """Other people's cards (yours: published_page()). The filters are kept in the page's address:
        ?q= words, where=all (in descriptions too), by= a publisher's name, tags= and exclude= (comma
        separated), publisher= a publisher's key ("All their cards"), tag= one tag (a card's chip)."""
        page_bar('Shared cards', actions=self._look_button if self.sharing else None)
        if self.sharing is None:
            self._unavailable()
            return
        sharing = self.sharing
        own = await sharing.call(sharing.own_keys)
        if publisher and publisher in own:  # (yours: on their own page)
            ui.navigate.to('/published')
            return
        try:
            settings = self.settings.load()
        except ValueError:
            settings = Settings()
        shown: list[CatalogEntry] = []
        cursor: list[str | None] = [None]
        have: set[str] = set()  # (those downloaded here)

        self._status()

        # The search, and the filters (folded away unless some are on)
        start_tags = list(dict.fromkeys([*_listed(tags), *([tag] if tag else [])]))
        with ui.column().classes('w-full gap-2'):
            search = ui.input(placeholder='Search', value=q).props('clearable outlined dense debounce=300') \
                .classes('w-full').mark('shared-search')
            with search.add_slot('prepend'):
                ui.icon('search')
            with ui.expansion('Filters', icon='tune').classes('panel fold w-full').mark('shared-filters') as filters:
                with ui.column().classes('w-full gap-3'):
                    where_ = ui.toggle({'name': 'Card names', 'all': 'Names and descriptions'},
                                      value='all' if where == 'all' else 'name').props('no-caps dense') \
                        .mark('shared-where')
                    ui.label('Where the words searched are found: each one at the start of a word ("sera" finds '
                             'Seraphina).').classes('text-sm muted -mt-2')
                    by_ = ui.input('Publisher', value=by, placeholder='Their name, now or before') \
                        .props('outlined dense clearable debounce=300').classes('w-full').mark('shared-by')
                    tags_ = keep_typed(ui.input_chips('Tags', value=start_tags, new_value_mode='add-unique'),
                                       split=',') \
                        .props('outlined dense clearable hide-bottom-space placeholder="Cards with any of these"') \
                        .classes('w-full placeholder-on-focus').mark('shared-tags')
                    exclude_ = keep_typed(ui.input_chips('Excluded tags', value=_listed(exclude),
                                                         new_value_mode='add-unique'), split=',') \
                        .props('outlined dense clearable hide-bottom-space placeholder="Cards with none of these"') \
                        .classes('w-full placeholder-on-focus').mark('shared-exclude')
                    if hidden := settings.sharing.hidden_tags:
                        ui.label(f'Always hidden too (Settings › Sharing cards): {", ".join(hidden)}.') \
                            .classes('text-sm muted -mt-2').mark('shared-exclude-hint')
                    nsfw = ui.switch('Show NSFW', value=settings.sharing.show_nsfw).mark('shared-nsfw')
            if publisher:
                who = await sharing.call(sharing.catalog.publisher, publisher)
                with ui.row().classes('items-center gap-2').mark('shared-publisher-filter'):
                    ui.label('Only the cards of').classes('text-sm muted')
                    ui.chip(f'{who.current_name if who else "a publisher"} · {short_key(publisher)}', icon='person',
                            removable=True, on_value_change=lambda: ui.navigate.to('/shared')) \
                        .props('dense outline')

        def on() -> int:
            """How many filters are on (said on the folded panel): the fields filled in. Not the switches (where
            the words are searched, NSFW shown): settings of the search, always one way or the other."""
            return sum((bool((by_.value or '').strip()), bool(tags_.value), bool(exclude_.value)))

        def query(cursor_: str | None) -> CardQuery:
            return CardQuery(card_name=search.value or None, in_description=where_.value == 'all',
                             publisher_name=(by_.value or '').strip() or None, tags=tuple(tags_.value or ()),
                             publisher_id=publisher or None, exclude_publishers=own,
                             content_rating=None if nsfw.value else 'sfw',
                             exclude_tags=(*settings.sharing.hidden_tags, *(exclude_.value or ())),
                             limit=PAGE, cursor=cursor_)

        def address() -> str:
            """The page's address, its filters in it (the browser's history keeps them)."""
            params = {'q': search.value or '', 'where': 'all' if where_.value == 'all' else '',
                      'by': (by_.value or '').strip(), 'tags': ','.join(tags_.value or ()),
                      'exclude': ','.join(exclude_.value or ()), 'publisher': publisher}
            text = urlencode({k: v for k, v in params.items() if v})
            return f'/shared?{text}' if text else '/shared'

        @ui.refreshable
        def results() -> None:
            if not shown:
                with ui.column().classes('panel gap-1').mark('shared-empty'):
                    if search.value or publisher or on():
                        ui.label('No card matches').classes('panel-title')
                    else:
                        ui.label('No shared cards yet').classes('panel-title')
                        ui.label('Cards people publish show here, once found on the relays (Settings › Sharing '
                                 'cards). Yours are in Your publications.').classes('muted')
                return
            named = bool((by_.value or '').strip())  # (names aren't unique: their keys tell them apart)
            with ui.list().props('separator').classes('panel item-list').mark('shared-list'):
                for entry in shown:
                    render_row(entry, self._row(entry, have=entry.publication.event_id in have, show_key=named),
                               key=entry.publication.event_id,
                               on_open=lambda e: ui.navigate.to(f'/shared/{e.publication.event_id}'))
            if cursor[0]:
                ui.button('Show more', on_click=more).props('flat').mark('shared-more')

        async def fill(more_: bool = False) -> None:
            page = await sharing.call(sharing.catalog.cards, query(cursor[0] if more_ else None))
            if not more_:
                shown.clear()
            shown.extend(page.items)
            have.update(await sharing.call(sharing.catalog.downloaded, [e.publication.event_id for e in page.items]))
            cursor[0] = page.next_cursor
            filters.props(f'caption="{plural(on(), "filter") + " on" if on() else ""}"')

        async def load(more_: bool = False) -> None:
            await fill(more_)
            results.refresh()

        async def changed() -> None:
            ui.navigate.history.replace(address())
            await load()

        async def more() -> None:
            await load(more_=True)

        async def nsfw_changed() -> None:
            try:
                current = self.settings.load()
                current.sharing.show_nsfw = bool(nsfw.value)
                self.settings.save(current)
            except ValueError:  # (a damaged settings file: the Settings page says so)
                pass
            await load()

        await fill()
        results()
        if on() or where_.value == 'all':  # (opened with filters on, or searching descriptions: shown)
            filters.open()
        for field in (search, where_, by_, tags_, exclude_):
            field.on_value_change(changed)
        nsfw.on_value_change(nsfw_changed)

        # Looking for new cards, when it's time; the list again each time a look is over
        seen = {'finished': sharing.finished_at}
        if sharing.due():
            sharing.discover()

        async def watch() -> None:
            if sharing.finished_at != seen['finished'] and not sharing.running:
                seen['finished'] = sharing.finished_at
                await load()
        ui.timer(0.5, watch)

    def _look_button(self) -> None:
        sharing = self.sharing

        def look() -> None:
            sharing.discover()
            ui.notify('Looking for new cards…')

        ui.button(icon='refresh', on_click=look).props('flat round aria-label="Look for new cards"') \
            .tooltip('Look for new cards').mark('shared-look').bind_enabled_from(sharing, 'running', lambda r: not r)

    def _status(self) -> None:
        """How far the looking is, or what the last look found."""
        sharing = self.sharing

        @ui.refreshable
        def status() -> None:
            with ui.row().classes('items-center gap-2 text-sm muted').mark('shared-status'):
                if sharing.running:
                    ui.spinner(size='1.2em')
                    received = sum(sharing.progress.values())
                    ui.label(f'Looking for new cards on the relays… {plural(received, "announcement")} so far')
                elif sharing.error:
                    ui.icon('error_outline', color='negative')
                    ui.label(f'Looking for new cards failed: {sharing.error}').classes('text-negative')
                elif sharing.result is not None and sharing.finished_at:
                    result = sharing.result
                    failed = [r for r in result.relays if not r.success]
                    text = f'Looked {time_ago(_when(int(sharing.finished_at)))}: ' + \
                        ('nothing new' if not result.added else f'{plural(result.added, "new publication")}')
                    if not result.relays:
                        text = 'No relays to look on: add some in Settings › Sharing cards'
                    ui.label(text)
                    if failed:
                        ui.label(f'· {plural(len(failed), "relay")} didn\'t answer (Settings › Sharing cards)') \
                            .classes('text-negative')

        status()
        state = {'key': None}

        def tick() -> None:
            key = (sharing.running, sum(sharing.progress.values()), sharing.finished_at)
            if key != state['key']:
                state['key'] = key
                status.refresh()
        ui.timer(0.5, tick)

    @staticmethod
    def _row(entry: CatalogEntry, *, have: bool = False, show_key: bool = False) -> Row:
        p = entry.publication
        description = ' '.join(p.card_description.split())
        nsfw = p.content_rating == 'nsfw'
        who = f'{entry.publisher_name} ({short_key(p.publisher_id)})' if show_key else entry.publisher_name
        note = f'{who} · {time_ago(_when(p.created_at))}'
        if have:
            note += ' · downloaded'
        if p.card_tags:
            note += ' · ' + ' '.join(f'#{t}' for t in p.card_tags[:6])
        return Row(title=p.card_name, subtitle=description, note=note,
                   icon='explicit' if nsfw else 'style', icon_color='negative' if nsfw else 'primary',
                   icon_tooltip='Rated NSFW by its publisher' if nsfw else '', side=size_text(p.card_size))

    # --- your publications ---------------------------------------------------------------------------

    async def published_page(self) -> None:
        """The cards published with your keys (the one in use, and those it replaced), each one's state: its
        file shared from here, where its announcement arrived; the withdrawn ones apart, at the end."""
        page_bar('Your publications')
        if self.sharing is None:
            self._unavailable()
            return
        sharing = self.sharing
        own = await sharing.call(sharing.own_keys)
        live, withdrawn = await sharing.call(sharing.catalog.published_by, own) if own else ((), ())
        if not live and not withdrawn:
            with ui.column().classes('panel gap-1').mark('published-empty'):
                ui.label('Nothing published yet').classes('panel-title')
                ui.label('To publish a character as a shared card: open it, then ⋮ › Publish to shared cards.') \
                    .classes('muted')
            return
        # Each one's state: (level, text, its character if it changed since: (id, title))
        states: dict[str, tuple[str, str, tuple[str, str] | None]] = {}
        sources = await self._sources(live)
        known: dict[tuple[str, str], bool] = {}  # (cards drawn to compare: changed_since's)

        async def look() -> bool:
            """Each one's state now; whether it changed."""
            now = {}
            for e in (*live, *withdrawn):
                level, text = await self._own_state(e)
                changed = await self._changed_character(e, sources, known)
                now[e.publication.event_id] = ('warning' if changed and level == 'ok' else level, text, changed)
            different = now != states
            states.update(now)
            return different

        @ui.refreshable
        def lists() -> None:
            for entries, marker in ((live, 'published-list'), (withdrawn, 'published-withdrawn')):
                if not entries:
                    continue
                if marker == 'published-withdrawn':
                    ui.label('Withdrawn').classes('step-title mt-2')
                with ui.list().props('separator').classes('panel item-list').mark(marker):
                    for entry in entries:
                        level, state, changed = states[entry.publication.event_id]
                        render_row(entry, self._own_row(entry, level, state, own=own, changed=changed),
                                   key=entry.publication.event_id,
                                   on_open=lambda e: ui.navigate.to(f'/shared/{e.publication.event_id}'))

        await look()
        lists()

        async def tick() -> None:  # (people connected, relays reached...)
            if await look():
                lists.refresh()
        ui.timer(5, tick)

    async def _own_state(self, entry: CatalogEntry) -> tuple[str, str]:
        """One of yours, in a few words: (level: 'ok', 'warning', 'problem' or 'withdrawn', text)."""
        sharing = self.sharing
        p = entry.publication
        if entry.withdrawn is not None:
            reason = f': {entry.withdrawn.reason}' if entry.withdrawn.reason else ''
            return 'withdrawn', f'Withdrawn {time_ago(_when(entry.withdrawn.at))}{reason}'
        level, parts = 'ok', []
        status = sharing.sharing_status(entry)
        if isinstance(status, str):
            level = 'problem'
            parts.append('its file is missing or changed')
        elif status is None:
            level = 'warning'
            parts.append('its file isn\'t shared from this computer')
        elif status.state == 'error':
            level = 'problem'
            parts.append('sharing its file failed')
        else:
            parts.append(f'shared, {_people(status.peers)} connected' if status.peers else 'shared')
        deliveries = await sharing.call(sharing.catalog.deliveries, p.event_id)
        counts = Counter(d.state for d in deliveries)
        if not counts:
            parts.append('not sent to relays from this computer')
        else:
            later = sum(d.may_arrive for d in deliveries)  # (tried again by itself)
            refused = counts['pending'] + counts['refused'] - later
            if counts['accepted']:
                parts.append(f'on {plural(counts["accepted"], "relay")}')
            if later or refused:
                level = 'warning' if level == 'ok' else level
            if later:
                parts.append(f'{plural(later, "relay")} not reached yet')
            if refused:
                parts.append(f'refused by {plural(refused, "relay")}')
            if counts['held']:
                parts.append(f'{counts["held"]} held')
        text = ', '.join(parts)
        return level, text[0].upper() + text[1:]

    async def _sources(self, entries: Iterable[CatalogEntry]) -> dict[str, tuple[str, Source | None]]:
        """The character each of your cards stands for here, as its page says (_published_note): event id →
        (character id, the record of what it was published from, if it was kept). The character it was
        published from, if it's still that card (by its UUID); or else one that is (its card imported here...).
        Only for each card's latest version (published with a key you used before, then with yours: the
        latter). None for the cards published from another computer (your key brought here)."""
        if self.characters is None:
            return {}
        sharing = self.sharing
        newest: dict[str, CatalogEntry] = {}
        for e in entries:
            p = e.publication
            if (n := newest.get(p.card_uuid)) is None or p.created_at > n.publication.created_at:
                newest[p.card_uuid] = e
        found: dict[str, tuple[str, Source | None]] = {}
        by_uuid: dict[str, str] | None = None  # (card UUID → character id: all the characters read, if needed)
        for uuid, e in newest.items():
            p = e.publication
            source = await sharing.call(sharing.catalog.source, p.event_id)
            character = self._character(source.character_id) if source is not None else None
            if character is not None and character.shared_uuid == uuid:
                found[p.event_id] = (character.id, source)
                continue
            if by_uuid is None:
                by_uuid = {}
                for c in self.characters.list():
                    if c.shared_uuid:
                        by_uuid.setdefault(c.shared_uuid, c.id)
            if character_id := by_uuid.get(uuid):
                found[p.event_id] = (character_id, source)
        return found

    async def _changed_character(self, entry: CatalogEntry, sources: dict[str, tuple[str, Source | None]],
                                 known: dict[tuple[str, str], bool]) -> tuple[str, str] | None:
        """One of yours: its character, if it changed since it was published (id, title); None if it didn't,
        or there's none here, or it can't be told."""
        found = sources.get(entry.publication.event_id)
        if found is None or entry.withdrawn is not None:
            return None
        character_id, source = found
        character = self._character(character_id)
        if character is None or character.shared_uuid != entry.publication.card_uuid:  # (deleted since...)
            return None
        changed = await changed_since(character, entry.publication, source, fingerprint=self.card_fingerprint,
                                      export=self.card_export, known=known)
        return (character.id, character.title) if changed else None

    @staticmethod
    def _own_row(entry: CatalogEntry, level: str, state: str, *, own: tuple[str, ...],
                 changed: tuple[str, str] | None = None) -> Row:
        p = entry.publication
        icon, color = {'ok': ('cloud_done', 'positive'), 'warning': ('warning', 'warning'),
                       'problem': ('error_outline', 'negative'),
                       'withdrawn': ('remove_circle_outline', 'grey')}[level]
        note = f'Published {date_text(p.created_at)} as "{p.published_name}"'
        if own and p.publisher_id != own[0]:
            note += ', with a key you used before'
        if p.card_tags:
            note += ' · ' + ' '.join(f'#{t}' for t in p.card_tags[:6])
        link = None
        if changed is not None:  # (to its character's page: what changed, and "Publish it again")
            character_id, title = changed
            link = Link(f'{title} changed since: publish it again', icon='edit_note',
                        handler=lambda: ui.navigate.to(f'/characters/{character_id}'),
                        marker=f'published-changed-{p.event_id}')
        return Row(title=p.card_name, subtitle=state, link=link, note=note, icon=icon, icon_color=color,
                   icon_tooltip={'ok': 'All is well', 'warning': 'Something to look at', 'problem': 'A problem',
                                 'withdrawn': 'Withdrawn'}[level],
                   side=size_text(p.card_size), dimmed=level == 'withdrawn')

    # --- one card ---------------------------------------------------------------------------------

    async def card_page(self, event_id: str) -> None:
        if self.sharing is None:
            page_bar('Shared card', back='/shared')
            self._unavailable()
            return
        sharing = self.sharing
        entry = await sharing.call(sharing.catalog.get, event_id)
        if entry is None:
            not_found('Shared card', '/shared')
            return
        p = entry.publication
        versions = await sharing.call(sharing.catalog.versions, p.publisher_id, p.card_uuid, include_withdrawn=True)
        publisher = await sharing.call(sharing.catalog.publisher, p.publisher_id)
        own = await sharing.call(sharing.own_keys)
        mine = p.publisher_id in own

        async def block() -> None:
            name = entry.publisher_name
            if not await confirm(f'Block {name}?', 'All their cards leave your catalog, and theirs won\'t show '
                                 'again. You can unblock them in Settings › Sharing cards.', 'Block'):
                return
            gone = await sharing.block(p.publisher_id)
            ui.notify(f'Blocked {name}: {plural(gone, "publication")} removed')
            ui.navigate.to('/shared')

        if mine:
            page_bar(p.card_name, subtitle='by you', back='/published',
                     menu=[('Your publications', lambda: ui.navigate.to('/published'), False)])
        else:
            page_bar(p.card_name, subtitle=f'by {entry.publisher_name}', back='/shared',
                     menu=[('All their cards', lambda: ui.navigate.to(f'/shared?publisher={p.publisher_id}'), False),
                           ('Block this publisher', block, True)])

        if entry.withdrawn:
            with ui.column().classes('panel gap-1').mark('shared-withdrawn'):
                ui.label('Withdrawn by its publisher').classes('panel-title text-negative')
                ui.label(f'{time_ago(_when(entry.withdrawn.at))}. It can no longer be downloaded; those who have it '
                         f'keep it.').classes('muted')
                if entry.withdrawn.reason:
                    ui.label(f'Their reason: {entry.withdrawn.reason}').classes('shared-text')
        elif not entry.is_latest:
            with ui.row().classes('items-center gap-2 text-sm').mark('shared-older'):
                ui.icon('history', color='primary')
                ui.label('An older version of this card.').classes('muted')
                latest = next((v for v in versions if v.is_latest), None)
                if latest:
                    ui.link('See the latest', f'/shared/{latest.publication.event_id}')

        if mine:
            await self._own_panel(entry, versions, old_key=bool(own) and p.publisher_id != own[0])
        else:
            await self._get_panel(entry)

        with panel('Description'):
            if p.card_description.strip():
                ui.label(p.card_description).classes('shared-text').mark('shared-description')
            else:
                ui.label('No description.').classes('muted')
            if p.card_tags:
                with ui.row().classes('gap-1'):
                    for t in p.card_tags:
                        ui.chip(f'#{t}', on_click=lambda t=t: ui.navigate.to(f'/shared?tag={t}')) \
                            .props('outline dense clickable').mark(f'shared-tag-{t}')

        with panel('Details'):
            with ui.element('div').classes('field-grid text-sm').mark('shared-details'):
                for label, value in (
                        ('Rated', 'NSFW' if p.content_rating == 'nsfw' else 'SFW'),
                        ('Language', language_text(p.language)),
                        ('Format', format_text(p.card_format)),
                        ('File', f'{p.file_name.rsplit(".", 1)[-1].upper()}, {size_text(p.card_size)}'),
                        ('Published', date_text(p.created_at)),
                ):
                    with ui.column().classes('gap-0'):
                        ui.label(label).classes('muted')
                        ui.label(value)

        with panel('Publisher'):
            ui.label(entry.publisher_name).classes('panel-title -mt-2').mark('shared-publisher-name')
            if p.published_name != entry.publisher_name:
                ui.label(f'Published this as "{p.published_name}".').classes('text-sm muted')
            if publisher and publisher.previous_names:
                ui.label(f'Also known as: {", ".join(publisher.previous_names)}').classes('text-sm muted')
            ui.label(to_npub(p.publisher_id)).classes('fingerprint').mark('shared-npub')
            ui.label('Names aren\'t unique: this key is what tells publishers apart.').classes('text-sm muted')
            if publisher:
                ui.link(f'All their cards ({publisher.cards})', f'/shared?publisher={p.publisher_id}') \
                    .mark('shared-their-cards')

        if len(versions) > 1:
            with panel('Versions', 'Each publication of this card, the newest first'):
                with ui.list().props('separator').classes('w-full').mark('shared-versions'):
                    for v in versions:
                        vp = v.publication
                        label = f'{date_text(vp.created_at)} · as "{vp.published_name}"'
                        state = 'withdrawn' if v.withdrawn else 'latest' if v.is_latest else ''
                        with ui.item(on_click=lambda i=vp.event_id: ui.navigate.to(f'/shared/{i}')) \
                                .mark(f'shared-version-{vp.event_id}'):
                            with ui.item_section():
                                ui.item_label(label + (' (this one)' if vp.event_id == p.event_id else ''))
                            if state:
                                with ui.item_section().props('side'):
                                    ui.label(state).classes('text-sm ' + ('text-negative' if v.withdrawn else 'muted'))

    async def _own_panel(self, entry: CatalogEntry, versions: list[CatalogEntry], *, old_key: bool = False) -> None:
        """One of yours: how its file is shared from here, where its announcement (or its withdrawal) arrived,
        and what can be done about it."""
        sharing = self.sharing
        p = entry.publication
        withdrawn = entry.withdrawn is not None
        event_id = entry.withdrawn.withdrawal_id if withdrawn else p.event_id  # (what was sent last)

        async def reload() -> None:
            await ui.context.client.sub_pages_router.refresh()

        with panel('Your publication', 'Published with a key you used before' if old_key else
                   'Published with your key'):
            @ui.refreshable
            def shared() -> None:
                icon, text = sharing_text(sharing.sharing_status(entry), withdrawn=withdrawn)
                with ui.row().classes('items-start no-wrap gap-2 text-sm').mark('own-sharing'):
                    ui.icon(icon, size='1.3em', color='negative' if icon == 'error_outline' else 'primary')
                    ui.label(text).classes('text-negative break-words' if icon == 'error_outline' else '')
            shared()
            last = {'text': None}

            def tick() -> None:  # (the people connected, what's sent: every few seconds)
                text = sharing_text(sharing.sharing_status(entry), withdrawn=withdrawn)
                if text != last['text']:
                    last['text'] = text
                    shared.refresh()
            ui.timer(3, tick)

            ui.label('Its withdrawal, on the relays' if withdrawn else 'Its announcement, on the relays') \
                .classes('step-title mt-1')

            listed: dict = {'view': None}  # (read first, then drawn at once: see _get_panel)

            async def look() -> bool:
                """Where it was sent, and what came of it, now (tried again by itself since...); whether what's
                said of it changed."""
                listed['deliveries'] = deliveries = await sharing.call(sharing.catalog.deliveries, event_id)
                known = {d.relay_url for d in deliveries}
                relays_ = await sharing.call(sharing.catalog.relays)
                listed['written'] = written = {r.url for r in relays_ if r.write}
                # Where it could still go: a relay that wasn't reached, or one added since
                listed['missing'] = any(d.state in ('pending', 'refused') for d in deliveries) or \
                    any(url not in known for url in written)
                view = (tuple((d.relay_url, d.state, delivery_text(d, retried=d.relay_url in written))
                              for d in deliveries), listed['missing'])
                changed, listed['view'] = view != listed['view'], view
                return changed

            @ui.refreshable
            def relays() -> None:
                deliveries, missing, written = listed['deliveries'], listed['missing'], listed['written']
                if not deliveries:
                    ui.label('Not sent to any relay from this computer.').classes('text-sm muted') \
                        .mark('own-no-deliveries')
                with ui.list().props('separator dense').classes('w-full').mark('own-deliveries'):
                    for d in deliveries:
                        icon, color, _ = DELIVERY.get(d.state, ('help_outline', 'grey', ''))
                        with ui.item().mark(f'delivery-{d.relay_url}'):
                            with ui.item_section().props('avatar').classes('min-w-0'):
                                ui.icon(icon, color=color)
                            with ui.item_section().classes('min-w-0'):
                                ui.item_label(d.relay_url).classes('break-all')
                                ui.item_label(delivery_text(d, retried=d.relay_url in written)) \
                                    .props('caption').classes('break-words')
                if missing:
                    button = ui.button('Send the withdrawal again' if withdrawn else 'Send again', icon='send',
                                       on_click=lambda: send_again(button)).props('flat dense').mark('own-send-again')
                    button.tooltip('To the relays it hasn\'t reached')

            async def send_again(button: ui.button) -> None:
                button.props('loading')
                try:
                    deliveries = await sharing.send_again(event_id)
                except SharingError as e:
                    ui.notify(str(e), type='negative')
                    return
                finally:
                    button.props(remove='loading')
                ui.notify(f'Sent again: {delivery_summary(deliveries)}')
                await look()
                relays.refresh()

            await look()
            relays()

            async def relays_tick() -> None:  # (sent again by itself meanwhile; when it will be next)
                if await look():
                    relays.refresh()
            ui.timer(5, relays_tick)

            async def stop() -> None:
                if not await confirm('Stop sharing its file?', 'Nobody can download it from this computer any '
                                     'more; people who have it may still share it. The card stays published: to '
                                     'share it again, publish its character again.', 'Stop sharing'):
                    return
                await sharing.stop_sharing(p.event_id)
                ui.notify('Its file is no longer shared from this computer')
                shared.refresh()
                stop_button.set_visibility(False)

            async def withdraw() -> None:
                answer = await self._withdraw_dialog(entry, versions)
                if answer is None:
                    return
                ids, reason = answer
                try:
                    deliveries = await sharing.withdraw(ids, reason=reason)
                except SharingError as e:
                    ui.notify(str(e), type='negative')
                    return
                ui.notify(f'Withdrawn: {delivery_summary(deliveries)}')
                await reload()

            with ui.row().classes('gap-2'):
                stop_button = ui.button('Stop sharing its file', icon='cloud_off', on_click=stop) \
                    .props('flat dense').mark('own-stop')
                stop_button.set_visibility(isinstance(sharing.sharing_status(entry), TorrentStatus))
                if not withdrawn:
                    ui.button('Withdraw…', icon='remove_circle_outline', on_click=withdraw) \
                        .props('flat dense color=negative').mark('own-withdraw')

    async def _get_panel(self, entry: CatalogEntry) -> None:
        """Someone else's card: adding it to your characters (downloading it, in the background), how far
        that is, and what became of it."""
        sharing = self.sharing
        p = entry.publication
        withdrawn = entry.withdrawn is not None
        # The download's state the panel shows (drawn again when it changes), and its record here: read first,
        # then the panel drawn at once (a refreshable that waits for it is emptied first: the page jumps)
        shown: dict = {'key': None, 'record': None}

        async def look() -> None:
            shown['record'] = await sharing.downloaded(p.event_id)

        @ui.refreshable
        def body() -> None:
            record = shown['record']
            fetch = sharing.fetches.get(p.event_id)
            shown['key'] = _fetch_key(fetch)
            character = self._character(record.character_id) if record else None
            title = 'In your characters' if record and character else 'Add it to your characters'
            with ui.column().classes('panel gap-2').mark('shared-get'):
                ui.label(title).classes('panel-title')
                if fetch is not None and fetch.running:
                    with ui.row().classes('items-center no-wrap gap-2 text-sm').mark('shared-fetching'):
                        ui.spinner(size='1.2em')
                        ui.label(fetch_text(fetch))
                    if fetch.note:  # (who was found, and why they left: what to look at, if it doesn't come)
                        ui.label(fetch.note).classes('text-sm muted break-words').mark('shared-fetch-note')
                    return
                if fetch is not None and fetch.stage == 'failed':
                    with ui.row().classes('items-start no-wrap gap-2 text-sm').mark('shared-fetch-failed'):
                        ui.icon('error_outline', size='1.3em', color='negative')
                        ui.label(fetch.error).classes('text-negative break-words')
                if record is not None:
                    with ui.row().classes('items-center gap-1 text-sm').mark('shared-yours'):
                        if character is not None:
                            ui.label('Added as')
                            ui.link(character.title or 'Untitled', f'/characters/{character.id}') \
                                .mark('shared-character-link')
                            ui.label(f'· {date_text(record.downloaded_at)}').classes('muted')
                        else:
                            ui.label(f'Added on {date_text(record.downloaded_at)}; that character was deleted '
                                     f'since.').classes('muted')
                    icon, text = sharing_text(sharing.sharing_status(entry), withdrawn=withdrawn)
                    if icon == 'cloud_off' and not withdrawn:
                        text = 'Its file isn\'t shared from this computer (Settings › Sharing cards).'
                    with ui.row().classes('items-start no-wrap gap-2 text-sm').mark('shared-sharing'):
                        ui.icon(icon, size='1.3em', color='negative' if icon == 'error_outline' else 'primary')
                        ui.label(text).classes('break-words')
                if withdrawn:
                    if record is None:
                        ui.label('It was withdrawn: it can no longer be downloaded.').classes('text-sm muted')
                    return
                with ui.row().classes('gap-2'):
                    ui.button('Add it again' if record else 'Add to my characters', icon='download',
                              on_click=add).props('flat dense' if record else '').mark('shared-add')
                    if record is not None and isinstance(sharing.sharing_status(entry), TorrentStatus):
                        ui.button('Stop sharing its file', icon='cloud_off', on_click=stop) \
                            .props('flat dense').mark('shared-stop')
                if record is None:
                    ui.label('Its file comes from those who share it (BitTorrent), and is checked: it must be the '
                             'one announced. Then it\'s a new character of yours. Those it comes from see this '
                             'computer\'s internet address.').classes('text-sm muted')
                elif character is not None:
                    ui.label('Added again, it\'s a new character: the one you have stays as it is.') \
                        .classes('text-sm muted')

        async def add() -> None:
            try:
                sharing.download(p.event_id, languages=browser_languages())
            except SharingError as e:
                ui.notify(str(e), type='negative')
                return
            await look()
            body.refresh()

        async def stop() -> None:
            if not await confirm('Stop sharing its file?', 'Nobody can download it from this computer any more. '
                                 'Your character stays as it is.', 'Stop sharing'):
                return
            await sharing.stop_sharing(p.event_id)
            await look()
            body.refresh()

        await look()
        body()

        async def tick() -> None:  # (how far a download is, and what came of it: as soon as it's over, quick or not)
            if _fetch_key(sharing.fetches.get(p.event_id)) != shown['key']:
                await look()  # (the character it became, once over)
                body.refresh()
        ui.timer(0.5, tick)

    def _character(self, character_id: str) -> Character | None:
        if self.characters is None or not character_id:
            return None
        try:
            return self.characters.get(character_id)
        except ValueError:  # (a damaged file)
            return None

    async def character_note(self, character: Character, *, fingerprint: Callable[[Character], str] | None = None,
                             export: Callable[[Character], bytes] | None = None,
                             publish: Callable[[], object] | None = None) -> None:
        """On a character's page (characters_page.py): the card you published from it, if you did (changed
        since, or not; withdrawn), and the shared card it was downloaded from, if it was (edited by you since, or
        not; withdrawn since, or a newer version out)."""
        if self.sharing is None:
            return
        await self._published_note(character, fingerprint, export, publish)
        await self._downloaded_note(character, fingerprint)

    async def _published_note(self, character: Character, fingerprint: Callable[[Character], str] | None,
                              export: Callable[[Character], bytes] | None,
                              publish: Callable[[], object] | None) -> None:
        """Published by you (with any of your keys), as which card; whether the character changed since, as
        it's edited. Its card's fingerprint then (kept since this was added), or else its file (from before:
        the card that would be published now, against the one that was)."""
        sharing = self.sharing
        if not character.shared_uuid:
            return
        versions = await sharing.call(sharing.own_versions, character.shared_uuid)
        if not versions:
            return
        entry = next((v for v in versions if v.withdrawn is None), versions[0])  # (the latest not withdrawn)
        p = entry.publication
        source = await sharing.call(sharing.catalog.source, p.event_id)
        known: dict[tuple[str, str], bool] = {}

        async def changed() -> bool | None:
            if entry.withdrawn is not None:
                return None
            return await changed_since(character, p, source, fingerprint=fingerprint, export=export, known=known)

        # What the note says: worked out first, then the note drawn at once (not in a refreshable that waits for
        # it: that one is emptied first, and the page jumps), and only when it changes
        shown = {'changed': await changed(), 'character': character.model_dump_json()}

        @ui.refreshable
        def note() -> None:
            with ui.row().classes('items-start no-wrap gap-2 text-sm w-full').mark('character-published'):
                ui.icon('cloud_upload', size='1.3em', color='primary')
                with ui.column().classes('gap-0 min-w-0'):
                    with ui.row().classes('items-center gap-1'):
                        ui.label('Published by you as').classes('muted')
                        ui.link(f'"{p.card_name}"', f'/shared/{p.event_id}').mark('character-published-link')
                        ui.label(f'· {date_text(p.created_at)}').classes('muted')
                    if entry.withdrawn is not None:
                        ui.label(f'You withdrew it {time_ago(_when(entry.withdrawn.at))}.').classes('muted') \
                            .mark('character-published-withdrawn')
                    elif shown['changed']:
                        with ui.row().classes('status-line gap-x-2 gap-y-0').mark('character-changed'):
                            ui.label('Changed since: the shared card isn\'t up to date.') \
                                .classes('text-weight-medium')
                            if publish is not None:
                                ui.button('Publish it again', icon='publish', on_click=publish) \
                                    .props('flat dense').mark('character-publish-again')
                    elif shown['changed'] is False:
                        ui.label('The shared card is up to date.').classes('muted status-line') \
                            .mark('character-up-to-date')

        note()
        if entry.withdrawn is not None:
            return

        async def tick() -> None:  # (edited here: said as soon as it's saved)
            now = character.model_dump_json()
            if now == shown['character']:
                return
            shown['character'] = now
            answer = await changed()
            if answer != shown['changed']:
                shown['changed'] = answer
                note.refresh()
        ui.timer(2, tick)

    async def _downloaded_note(self, character: Character, fingerprint: Callable[[Character], str] | None) -> None:
        """Downloaded from a shared card: which one (withdrawn since, or a newer version out), when, and whether
        you edited the character since, as it's edited (its card's fingerprint as added, against now; not
        known for those added before that was kept)."""
        sharing = self.sharing
        records = await sharing.call(sharing.catalog.downloads, character_id=character.id)
        if not records:
            return
        record = records[0]
        entry = await sharing.call(sharing.catalog.get, record.event_id)
        latest = None
        if entry is not None and entry.withdrawn is None and not entry.is_latest:
            p = entry.publication
            versions = await sharing.call(sharing.catalog.versions, p.publisher_id, p.card_uuid)
            latest = next((v for v in versions if v.is_latest), None)

        def edited() -> bool | None:
            if not record.fingerprint or fingerprint is None:
                return None
            try:
                return fingerprint(character) != record.fingerprint
            except Exception:  # (a damaged lore book...: can't tell)
                log.warning('Can\'t tell whether %s was edited', character.id, exc_info=True)
                return None

        # (worked out first, then drawn at once, and only when it changes: see _published_note)
        shown = {'edited': edited(), 'character': character.model_dump_json()}

        @ui.refreshable
        def since() -> None:
            when = date_text(record.downloaded_at)
            if shown['edited']:
                ui.label(f'Edited by you since you downloaded it ({when}).').mark('character-edited')
            elif shown['edited'] is False:
                ui.label(f'Not edited since you downloaded it ({when}).').classes('muted') \
                    .mark('character-not-edited')
            else:
                ui.label(f'Downloaded on {when}.').classes('muted')

        with ui.row().classes('items-start no-wrap gap-2 text-sm w-full').mark('character-shared-from'):
            ui.icon('travel_explore', size='1.3em', color='primary')
            with ui.column().classes('gap-0 min-w-0'):
                if entry is None:  # (its publisher blocked since)
                    ui.label('From a shared card no longer in your catalog.').classes('muted')
                else:
                    p = entry.publication
                    with ui.row().classes('items-center gap-1'):
                        ui.label('From the shared card').classes('muted')
                        ui.link(f'"{p.card_name}" by {entry.publisher_name}', f'/shared/{p.event_id}')
                since()
                self._shared_since(entry, latest)

        if shown['edited'] is None:
            return

        def tick() -> None:  # (edited here: said as soon as it's saved)
            now = character.model_dump_json()
            if now == shown['character']:
                return
            shown['character'] = now
            answer = edited()
            if answer != shown['edited']:
                shown['edited'] = answer
                since.refresh()
        ui.timer(2, tick)

    @staticmethod
    def _shared_since(entry: CatalogEntry | None, latest: CatalogEntry | None) -> None:
        """What became of the shared card a character was downloaded from: withdrawn, or a newer version."""
        if entry is None:
            return
        if entry.withdrawn is not None:
            reason = f' Their reason: {entry.withdrawn.reason}' if entry.withdrawn.reason else ''
            ui.label(f'Its publisher withdrew it {time_ago(_when(entry.withdrawn.at))}: it can no longer be '
                     f'downloaded. You keep this character.{reason}') \
                .classes('text-negative break-words').mark('character-shared-withdrawn')
        elif latest is not None:
            with ui.row().classes('items-center gap-1').mark('character-shared-newer'):
                ui.label('A newer version is out:').classes('muted')
                ui.link(f'see it ({date_text(latest.publication.created_at)})',
                        f'/shared/{latest.publication.event_id}')

    @staticmethod
    async def _withdraw_dialog(entry: CatalogEntry, versions: list[CatalogEntry]) -> tuple[list[str], str] | None:
        """Which publications to withdraw (this one, or all its versions), and why: None if cancelled."""
        p = entry.publication
        others = [v.publication.event_id for v in versions
                  if v.withdrawn is None and v.publication.event_id != p.event_id]
        with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes('dialog-card') \
                .mark('withdraw-dialog'):
            ui.label(f'Withdraw "{p.card_name}"?').classes('panel-title')
            ui.label('Its file is no longer shared from here, and the relays are asked to delete its '
                     'announcement: people who don\'t have it can no longer download it. Those who have it keep '
                     'it, and are told it was withdrawn.').classes('muted')
            every = ui.checkbox(f'Its older versions too ({len(others)})', value=True).mark('withdraw-all') \
                if others else None
            reason = ui.textarea('Why (optional)', placeholder='e.g. Replaced by a new card').props(
                f'outlined autogrow maxlength={fmt.MAX_REASON} counter').classes('w-full').mark('withdraw-reason')
            ui.label('Shown on its page, to those who have it.').classes('text-sm muted -mt-3')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancel', on_click=lambda: dialog.submit(None)).props('flat')
                ui.button('Withdraw', color='negative', on_click=lambda: dialog.submit(
                    ([p.event_id] + (others if every is not None and every.value else []),
                     (reason.value or '').strip()))).mark('withdraw-confirm')
        answer = await dialog
        try:
            dialog.delete()
        except (ValueError, KeyError):  # the browser tab was closed while the dialog was open
            pass
        return answer if isinstance(answer, tuple) else None

    def _unavailable(self) -> None:
        with ui.column().classes('panel gap-1').mark('shared-unavailable'):
            ui.label('Sharing cards isn\'t available here').classes('panel-title')
            ui.label(self.problem or sentence(runtime_problem())) \
                .classes('muted break-words')

    # --- publishing -------------------------------------------------------------------------------

    def my_key(self) -> Identity | None:
        """This computer's publisher key (None: none yet, or its file can't be read: Settings says so)."""
        if self.sharing is None:
            return None
        try:
            return self.sharing.identity()
        except IdentityError:
            return None

    async def publish_dialog(self, character: Character, export: Callable[[Character], bytes],
                             save: Callable[[Character], None], *,
                             fingerprint: Callable[[Character], str] | None = None) -> Published | None:
        """Publish a character as a shared card (from its editor's ⋮ menu): ask how it's announced, then
        publish its card file (`export`) and open its page. `save` saves the character, given its card UUID
        the first time; `fingerprint`, its card's, kept with the publication (to tell, later, whether it
        changed since: character_note()). What was published, or None.

        The first time (until it's been through once: settings.sharing.publishing_accepted), a wizard comes
        first: what publishing means (agreed to), the publisher key (made or imported, and backed up), how
        the file is shared (the port, the router). Without a key later on: the key's step again."""
        sharing = self.sharing
        try:
            settings = self.settings.load()
        except ValueError:  # (a damaged settings file: the Settings page says so)
            settings = Settings()
        if problem := torrent_problem():
            ui.notify(f'Cards can\'t be shared from this computer: {problem}.', type='negative', multi_line=True)
            return None
        try:  # (beyond the safeguards others' apps check it against: said now, not after the wizard)
            content = await asyncio.to_thread(export, character)
            await asyncio.to_thread(check_shared_card, 'card.png', content, size_limit=fmt.MAX_FILE_BYTES)
        except SafeguardError as e:
            await self._beyond_safeguards(character, content, e)
            return None
        except Exception:  # (can't be told now: publishing says why, if it fails)
            pass
        me, _ = self._key_or_problem()
        if settings.sharing.publishing_accepted < NOTICE_VERSION:
            steps = ['notice', 'key', 'sharing', 'card']
        else:
            steps = ['key', 'card'] if me is None else ['card']
        state = {'step': steps[0], 'made': False}
        fields: dict[str, ui.element] = {}

        with ui.context.client.layout, ui.dialog().props('persistent') as dialog, \
                ui.card().classes('dialog-card publish-card').mark('publish-dialog') as card:
            ui.label(f'Publish "{character.title}" to shared cards').classes('panel-title')

            @ui.refreshable
            async def view() -> None:
                step = state['step']
                if len(steps) > 1:
                    number = steps.index(step) + 1
                    with ui.column().classes('w-full gap-1').mark(f'wizard-{step}'):
                        ui.label(f'Step {number} of {len(steps)}: {WIZARD_STEPS[step]}').classes('text-sm muted')
                        ui.linear_progress(value=number / len(steps), show_value=False, size='4px')
                if step == 'card':
                    await card_step()
                else:
                    {'notice': notice_step, 'key': key_step, 'sharing': sharing_step}[step]()

        def go(step: str) -> None:
            state['step'] = step
            view.refresh()

        def navigation(next_label: str = 'Next', on_next: Callable[[], object] | None = None, *,
                       marker: str = 'wizard-next') -> ui.button | None:
            """Cancel, Back (but on the first step), and the step's own button."""
            index = steps.index(state['step'])
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancel', on_click=lambda: dialog.submit(None)).props('flat').mark('wizard-cancel')
                if index > 0:
                    ui.button('Back', on_click=lambda: go(steps[index - 1])).props('flat').mark('wizard-back')
                if on_next is None:
                    return None
                return ui.button(next_label, on_click=on_next).mark(marker)

        # --- Step 1: what publishing means -------------------------------------------------------------

        def notice_step() -> None:
            ui.label('Publishing doesn\'t go through any company: cards go from computer to computer. That '
                     'doesn\'t make you anonymous:')
            with ui.element('ul').classes('wizard-list text-sm'):
                for text in ('Your publisher key ties together every card you publish, whatever name you give.',
                             'Your internet address (IP) is seen by the relays you publish to, and by everyone who '
                             'downloads your cards from this computer.',
                             'What you publish stays public: withdrawing a card stops new downloads, but those who '
                             'have it keep it, and some relays keep its announcement.'):
                    with ui.element('li'):
                        ui.label(text)
            ui.label('Only publish what you have the right to share: nothing illegal, no one else\'s cards '
                     'without their permission, nothing about real people.').classes('text-weight-bold') \
                .mark('wizard-rules')
            navigation('I agree', lambda: go('key'), marker='wizard-agree')

        # --- Step 2: the publisher key, made or imported, and backed up --------------------------------

        def key_step() -> None:
            key, problem = self._key_or_problem()
            if problem:
                ui.label(problem).classes('text-sm text-negative break-words').mark('wizard-key-problem')
                ui.label('Restore it from its backup: the text that starts with nsec1.').classes('text-sm muted')
                import_field(replace=True)
                navigation()
                return
            if key is None:
                ui.label('Your publisher key signs your cards: it\'s how people know they\'re yours, and only it '
                         'can publish new versions of them, or withdraw them. It\'s kept on this computer, and '
                         'never sent anywhere.')
                ui.button('Make my key', icon='key', on_click=make).mark('wizard-make-key')
                ui.label('Or, if you have one already, its backup (it starts with nsec1):').classes('text-sm muted')
                import_field(replace=False)
                navigation()
                return
            ui.label('Your key is made. Its public half, which identifies you:' if state['made'] else
                     'Your publisher key. Its public half, which identifies you:').classes('text-sm muted')
            ui.label(key.npub).classes('fingerprint').mark('wizard-npub')
            ui.label('Back it up now. There\'s no "forgot my key": lost, it can\'t be recovered, and you would be '
                     'a new publisher, unable to update or withdraw the cards you published.') \
                .classes('text-negative text-weight-bold').mark('wizard-backup-warning')
            nsec = key.nsec()
            ui.input('Your secret key', value=nsec, password=True, password_toggle_button=True) \
                .props('readonly outlined autocomplete=off').classes('w-full').mark('wizard-nsec')
            with ui.row().classes('gap-2'):
                ui.button('Copy', icon='content_copy', on_click=lambda: ui.notify('Copied')).props('flat dense') \
                    .on('click', js_handler=f'() => window.rpCopy({json.dumps(nsec)})').mark('wizard-copy')
                ui.button('Save as a file', icon='save_alt', on_click=lambda: save_key_file(key)) \
                    .props('flat dense').mark('wizard-save-file')
            ui.label('Keep it somewhere safe and private, like a password manager: whoever has it can publish as '
                     'you. Backing up the app\'s data folder doesn\'t include it: it\'s kept with the settings '
                     f'({KEY_PLACE}).').classes('text-sm muted')
            saved = ui.checkbox('I\'ve saved my key somewhere safe').mark('wizard-saved')
            proceed = navigation('Next', lambda: go(steps[steps.index('key') + 1]), marker='wizard-key-next')
            proceed.bind_enabled_from(saved, 'value')

        def make() -> None:
            try:
                sharing.create_identity()
            except (IdentityError, OSError) as e:
                ui.notify(f'The key can\'t be made: {e}', type='negative', multi_line=True)
                return
            state['made'] = True
            view.refresh()

        def import_field(*, replace: bool) -> None:
            with ui.row().classes('w-full items-start no-wrap gap-2'):
                field = ui.input('Its backup', placeholder='nsec1…', password=True, password_toggle_button=True) \
                    .props('outlined autocomplete=off').classes('grow').mark('wizard-import-input')

                def bring() -> None:
                    try:
                        sharing.import_identity(field.value or '', replace=replace)
                    except IdentityError as e:
                        field.props['error'] = True
                        field.props['error-message'] = str(e)
                        field.update()
                        return
                    except OSError as e:
                        ui.notify(f'The key can\'t be saved: {e}', type='negative', multi_line=True)
                        return
                    state['made'] = False
                    view.refresh()

                field.on('keydown.enter', bring)
                ui.button('Import', on_click=bring).props('outline').classes('mt-1').mark('wizard-import')

        # --- Step 3: how the file is shared --------------------------------------------------------------

        def sharing_step() -> None:
            ui.label('When you publish, this computer is the only one that has the card\'s file: others download '
                     'it from here (BitTorrent), and then share it in turn. While no computer that has it is on, '
                     'nobody can download it.')
            ui.label('So its file is shared while the app runs. For others to reach this computer, its port must '
                     'be open on your router: the app asks the router to open it. Leave it so, unless you\'d rather '
                     'open it yourself.').classes('text-sm muted')
            port = ui.number('Port', value=settings.sharing.port, min=1024, max=65535, precision=0,
                             placeholder=f'Automatic ({DEFAULT_PORT})') \
                .props('outlined clearable stack-label').classes('w-full').mark('wizard-port')
            ui.label(f'TCP and UDP. Empty: {DEFAULT_PORT}, or the next free one.').classes('text-sm muted -mt-3')
            mapping = ui.switch('Ask the router to open it (UPnP, NAT-PMP)', value=settings.sharing.port_mapping) \
                .mark('wizard-port-mapping')
            ui.label('Off: open the port yourself in the router\'s settings, to this computer. Without an open '
                     'port, only those whose own port is open can get your cards from you.') \
                .classes('text-sm muted -mt-3 ml-12')
            ui.label('You can change these later, in Settings › Sharing cards.').classes('text-sm muted')

            def done() -> None:
                value = port.value
                if value is not None and (value != int(value) or not 1024 <= value <= 65535):
                    port.props['error'] = True
                    port.props['error-message'] = 'A port from 1024 to 65535, or empty'
                    port.update()
                    return
                chosen = None if value is None else int(value)
                try:  # (kept, with the notice agreed to: next time, the card's form only)
                    current = self.settings.load()
                    current.sharing.port, current.sharing.port_mapping = chosen, bool(mapping.value)
                    current.sharing.publishing_accepted = NOTICE_VERSION
                    self.settings.save(current)
                except ValueError:  # (a damaged settings file: the Settings page says so)
                    pass
                sharing.session.configure(port=chosen, port_mapping=bool(mapping.value))
                go('card')

            navigation('Next', done, marker='wizard-sharing-next')

        # --- Step 4 (or the only one): the card ----------------------------------------------------------

        async def card_step() -> None:
            # Published before (with this key): a new version, starting from what was announced last time
            key, _ = self._key_or_problem()
            before = await sharing.call(sharing.catalog.versions, key.public_key, character.shared_uuid,
                                        include_withdrawn=True) if key and character.shared_uuid else []
            last = before[0].publication if before else None
            if last is not None:
                note = 'withdrawn' if before[0].withdrawn else 'published'
                ui.label(f'You {note} it on {date_text(last.created_at)}: this is a new version of the same '
                         f'card.').classes('text-sm muted').mark('publish-new-version')
            fields['name'] = ui.input('Card name', value=last.card_name if last else character.title) \
                .props(f'outlined maxlength={fmt.MAX_TITLE}').classes('w-full').mark('publish-name')
            fields['publisher'] = ui.input(
                'Your publisher name', value=settings.sharing.publisher_name or (last.published_name if last
                                                                                else '')) \
                .props(f'outlined maxlength={fmt.MAX_PUBLISHER}').classes('w-full').mark('publish-publisher')
            ui.label('Shown with all your cards. Names aren\'t unique: your key tells you apart.') \
                .classes('text-sm muted -mt-3')
            fields['description'] = ui.textarea(
                'Description', value=last.card_description if last else character.description.strip()) \
                .props('outlined autogrow').classes('w-full').mark('publish-description')
            ui.label('What people read before downloading it.').classes('text-sm muted -mt-3')
            fields['tags'] = keep_typed(ui.input_chips('Tags', value=list(last.card_tags) if last else [],
                                                       new_value_mode='add-unique'), split=',') \
                .props('outlined dense clearable hide-bottom-space').classes('w-full').mark('publish-tags')
            ui.label(f'Press Enter after each, or separate them with commas: {fmt.MAX_CARD_TAGS} at most, '
                     f'e.g. fantasy, romance, sci-fi.').classes('text-sm muted -mt-3')
            with ui.row().classes('w-full items-center gap-x-6 gap-y-1'):
                with ui.column().classes('gap-0'):
                    ui.label('Rated').classes('text-sm muted')
                    fields['rating'] = ui.radio({'sfw': 'SFW', 'nsfw': 'NSFW'},
                                                value=last.content_rating if last else None) \
                        .props('inline dense').mark('publish-rating')
                fields['language'] = ui.select({'': 'Not said', **LANGUAGES}, label='Language',
                                               value=(last.language or '') if last else '') \
                    .props('outlined dense').classes('min-w-40').mark('publish-language')
            ui.label('Your own rating: NSFW cards only show to those who choose to see them.') \
                .classes('text-sm muted -mt-3')
            # (what publishing means: said by the wizard's first step, the first time)
            fields['error'] = ui.label('').classes('text-negative text-sm break-words').mark('publish-error')
            fields['error'].set_visibility(False)
            fields['button'] = navigation('Publish', publish, marker='publish-confirm')
            fields['button'].props('icon=publish')

        def failed(text: str) -> None:
            fields['error'].text = text
            fields['error'].set_visibility(True)

        async def publish() -> None:
            name, publisher = (fields['name'].value or '').strip(), (fields['publisher'].value or '').strip()
            rating = fields['rating'].value
            if not name:
                return failed('Give the card a name.')
            if not publisher:
                return failed('Give your publisher name: it\'s shown with your cards.')
            if rating not in fmt.RATINGS:
                return failed('Rate it: SFW or NSFW.')
            draft = PublicationDraft(publisher_name=publisher, card_name=name,
                                     card_description=fields['description'].value or '',
                                     card_tags=tuple(fields['tags'].value or ()), content_rating=rating,
                                     language=fields['language'].value or None)
            given = not character.shared_uuid
            if given:  # (in the card file too: publishing it again, here or elsewhere, makes a new version)
                character.shared_uuid = fmt.new_card_uuid()
            fields['button'].props('loading')
            try:
                content = await asyncio.to_thread(export, character)
                published = await sharing.publish(draft, content, card_uuid=character.shared_uuid)
            except (Rejected, SharingError) as e:
                if given:
                    character.shared_uuid = ''
                return failed(rejected_text(e) if isinstance(e, Rejected) else str(e))
            except Exception as e:  # (the disk, the card's picture...)
                log.exception('Publishing %s failed', character.id)
                if given:
                    character.shared_uuid = ''
                return failed(f'It couldn\'t be published: {e}')
            finally:
                fields['button'].props(remove='loading')
            if given:
                save(character)
            if fingerprint is not None:  # (what it was published from, as it was)
                await sharing.call(sharing.catalog.record_source, published.publication.event_id, character.id,
                                   fingerprint(character))
            try:
                current = self.settings.load()
                if current.sharing.publisher_name != publisher:
                    current.sharing.publisher_name = publisher
                    self.settings.save(current)
            except ValueError:
                pass
            dialog.submit(published)

        with card:  # (now that its steps are all defined)
            await view()
        published = await dialog
        try:
            dialog.delete()
        except (ValueError, KeyError):  # the browser tab was closed while the dialog was open
            return published if isinstance(published, Published) else None
        if not isinstance(published, Published):
            return None
        name, summary = published.publication.card_name, delivery_summary(published.deliveries)
        ui.notify({'new_card': f'Published "{name}": {summary}',
                   'new_version': f'Published a new version of "{name}": {summary}',
                   'resumed': f'"{name}" was already published, unchanged: sent again where it hadn\'t arrived '
                              f'({summary})'}[published.action], type='positive', multi_line=True)
        ui.navigate.to(f'/shared/{published.publication.event_id}')
        return published

    @staticmethod
    async def _beyond_safeguards(character: Character, content: bytes, e: SafeguardError) -> None:
        """Why a character can't be published (its card beyond the safeguards every app checks shared cards
        against: it couldn't be added), and what can be done about it."""
        if e.what == 'file':
            text = len(png_text_chunks(content).get('chara', ''))  # (its texts and lore books, in the file)
            if text * 2 >= len(content):
                what_to_do = (f'Most of it is its texts and lore books ({megabytes(text)}): shorten them, or take '
                              f'a lore book off the character.')
            else:
                what_to_do = 'Most of it is its picture: choose a smaller one.'
        else:
            what_to_do = 'Choose a smaller picture for the character.'
        with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes('dialog-card') \
                .mark('publish-beyond-safeguards'):
            ui.label(f'"{character.title}" can\'t be published').classes('panel-title')
            ui.label(f'Its card is too big to share: {e}. Others\' apps would refuse it, so that a shared card '
                     f'can\'t fill the memory or the disk of those who add it.').classes('break-words')
            ui.label(what_to_do).classes('break-words')
            with ui.row().classes('w-full justify-end'):
                ui.button('OK', on_click=dialog.close).mark('publish-beyond-ok')
        await dialog
        try:
            dialog.delete()
        except (ValueError, KeyError):  # (the browser tab was closed meanwhile)
            pass

    def _key_or_problem(self) -> tuple[Identity | None, str]:
        """This computer's publisher key, if there's one; or why its file can't be read."""
        try:
            return self.sharing.identity(), ''
        except IdentityError as e:
            return None, str(e)

    # --- Settings › Sharing cards -----------------------------------------------------------------

    def _connection(self, settings: Settings, form: Form) -> None:
        """BitTorrent's port: which one, the router asked to open it or not, and how others reach this
        computer, as far as it can tell (updated while the page is open)."""
        sharing = self.sharing
        ui.label('How others reach this computer').classes('step-title mt-2')
        ui.label('Cards go from computer to computer (BitTorrent). This one can always connect to others; for '
                 'others to connect to it too, its port must be open on your router.').classes('text-sm muted')
        with ui.element('div').classes('field-grid'):
            port = form.number(settings.sharing, 'port', 'Port', integer=True, min=1024, max=65535,
                               placeholder=f'Automatic ({DEFAULT_PORT})',
                               hint=f'TCP and UDP. Empty: {DEFAULT_PORT}, or the next free one.') \
                .props('debounce=1000 stack-label').mark('sharing-port')
        mapping = form.switch(settings.sharing, 'port_mapping', 'Ask the router to open it (UPnP, NAT-PMP)') \
            .mark('sharing-port-mapping')
        ui.label('Off: open it yourself in the router\'s settings, to this computer.') \
            .classes('text-sm muted -mt-3 ml-12')

        def apply() -> None:
            sharing.session.configure(port=settings.sharing.port, port_mapping=settings.sharing.port_mapping)
            status.refresh()
        port.on_value_change(apply)
        mapping.on_value_change(apply)

        shown: dict = {'lines': None}

        @ui.refreshable
        def status() -> None:
            lines = network_lines(sharing.session.network()) if not torrent_problem() else \
                [('error_outline', 'negative', f'Cards can\'t be shared from this computer: {torrent_problem()}.')]
            shown['lines'] = lines
            with ui.column().classes('w-full gap-1').mark('sharing-network'):
                for icon, color, text in lines:
                    with ui.row().classes('items-start no-wrap gap-2 text-sm'):
                        ui.icon(icon, size='1.3em', color=color)
                        ui.label(text).classes('break-words')

        status()

        def tick() -> None:  # (what changes: the router's answer, connections from the internet)
            if network_lines(sharing.session.network()) != shown['lines']:
                status.refresh()
        ui.timer(2, tick)

    def _key_block(self) -> None:
        """The publisher key: made or imported; then its public half, its backup, another one."""
        sharing = self.sharing

        @ui.refreshable
        def block() -> None:
            try:
                me, problem = sharing.identity(), ''
            except IdentityError as e:
                me, problem = None, str(e)
            with ui.column().classes('w-full gap-2').mark('sharing-key'):
                if problem:
                    ui.label(problem).classes('text-sm text-negative break-words').mark('sharing-key-problem')
                    ui.button('Restore it from its backup', icon='settings_backup_restore',
                              on_click=lambda: change(replace=True)).props('outline').mark('sharing-restore-key')
                elif me is None:
                    ui.label('Publishing cards needs one: it signs them, so that people know they\'re yours, and '
                             'only it can publish new versions of them, or withdraw them. Looking at cards '
                             'doesn\'t.').classes('text-sm muted')
                    with ui.row().classes('gap-2'):
                        ui.button('Make my key', icon='key', on_click=make).props('outline') \
                            .mark('sharing-make-key')
                        ui.button('Import one', on_click=lambda: change(replace=False)).props('flat') \
                            .mark('sharing-import-key')
                else:
                    ui.label(me.npub).classes('fingerprint').mark('sharing-npub')
                    ui.label('Its public half: it identifies you as a publisher, and isn\'t secret.') \
                        .classes('text-sm muted -mt-1')
                    with ui.row().classes('gap-2'):
                        ui.button('Back up my key', icon='key', on_click=lambda: self._backup_dialog(me)) \
                            .props('outline').mark('sharing-backup-key')
                        ui.button('Your publications', on_click=lambda: ui.navigate.to('/published')) \
                            .props('flat').mark('sharing-your-cards')
                        ui.button('Use another key', on_click=lambda: change(replace=True)).props('flat') \
                            .mark('sharing-replace-key')

        def make() -> None:
            try:
                sharing.create_identity()
            except (IdentityError, OSError) as e:
                ui.notify(f'The key can\'t be made: {e}', type='negative', multi_line=True)
                return
            ui.notify('Your publisher key is made. Back it up: lost, it can\'t be recovered.', type='positive',
                      multi_line=True)
            block.refresh()

        async def change(*, replace: bool) -> None:
            if await self._key_dialog(replace=replace) is not None:
                block.refresh()

        block()

    async def _backup_dialog(self, me: Identity) -> None:
        nsec = me.nsec()
        with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes('dialog-card').mark('key-backup'):
            ui.label('Back up your publisher key').classes('panel-title')
            ui.label('Keep this text somewhere safe and private, like a password manager. With it, you can '
                     'publish as yourself again: after reinstalling, or on another computer (Settings › Sharing '
                     'cards › Use another key). Lost, it can\'t be recovered: you would be a new publisher, unable '
                     'to update or withdraw your cards.').classes('muted')
            ui.label('Whoever has it can publish as you: never give it to anyone.') \
                .classes('text-negative text-weight-bold')
            ui.input('Your secret key', value=nsec, password=True, password_toggle_button=True) \
                .props('readonly outlined autocomplete=off').classes('w-full').mark('key-backup-nsec')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Copy', icon='content_copy', on_click=lambda: ui.notify('Copied')).props('flat') \
                    .on('click', js_handler=f'() => window.rpCopy({json.dumps(nsec)})').mark('key-backup-copy')
                ui.button('Save as a file', icon='save_alt', on_click=lambda: save_key_file(me)).props('flat') \
                    .mark('key-backup-file')
                ui.button('Done', on_click=lambda: dialog.submit(None))
        await dialog
        try:
            dialog.delete()
        except (ValueError, KeyError):  # the browser tab was closed while the dialog was open
            pass

    async def _key_dialog(self, *, replace: bool) -> Identity | None:
        """Import a key from its backup, or (`replace`) make or import another one instead of the current
        one, which is kept in a file beside it. The key now used, or None if cancelled."""
        sharing = self.sharing
        with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes('dialog-card').mark('key-dialog'):
            ui.label('Use another publisher key' if replace else 'Import your publisher key').classes('panel-title')
            if replace:
                ui.label('Your cards are signed with your current key: only it can publish new versions of '
                         'them, or withdraw them. With another key, you are another publisher.').classes('muted')
                ui.label('Back up your current key first, if you may need it again. (Its file is kept too, '
                         'beside the new one.)').classes('text-negative text-weight-bold').mark('key-warning')
            ui.label('Paste the backup of a key: the text that starts with nsec1.').classes('text-sm muted')
            field = ui.input('Its backup', placeholder='nsec1…', password=True, password_toggle_button=True) \
                .props('outlined autocomplete=off').classes('w-full').mark('key-import-input')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancel', on_click=lambda: dialog.submit(None)).props('flat')
                if replace:
                    ui.button('Make a new one', on_click=lambda: done(None)).props('flat').mark('key-make-new')
                ui.button('Import', on_click=lambda: done(field.value or '')).mark('key-import')

        def done(text: str | None) -> None:
            try:
                me = sharing.import_identity(text, replace=replace) if text is not None else \
                    sharing.create_identity(replace=True)
            except IdentityError as e:
                field.props['error'] = True
                field.props['error-message'] = str(e)
                field.update()
                return
            except OSError as e:
                ui.notify(f'The key can\'t be saved: {e}', type='negative', multi_line=True)
                return
            dialog.submit(me)

        field.on('keydown.enter', lambda: done(field.value or ''))
        result = await dialog
        try:
            dialog.delete()
        except (ValueError, KeyError):  # the browser tab was closed while the dialog was open
            pass
        if isinstance(result, Identity):
            ui.notify(f'Your publisher key is now {short_key(result.public_key)}', type='positive')
            return result
        return None

    def settings_panel(self, settings: Settings, form: Form) -> None:
        """What to show, the cards downloaded, your publisher key, the relays, and the blocked publishers."""
        form.switch(settings.sharing, 'show_nsfw', 'Show NSFW cards').mark('sharing-nsfw')
        ui.label('Cards their publisher rated NSFW. It\'s their own rating: nobody checks it.') \
            .classes('text-sm muted -mt-3 ml-12')
        form.chips(settings.sharing, 'hidden_tags', 'Tags to hide', split=',',
                   hint='Cards with any of these tags aren\'t shown. Press Enter after each, or separate them '
                        'with commas.') \
            .mark('sharing-hidden-tags')
        if self.sharing is None:
            ui.label(f'Sharing cards isn\'t available here. {self.problem or runtime_problem()}') \
                .classes('muted break-words').mark('sharing-unavailable')
            return
        sharing = self.sharing

        ui.label('Cards you download').classes('step-title mt-2')
        share = form.switch(settings.sharing, 'share_downloads', 'Share them in turn').mark('sharing-share-downloads')
        ui.label('While the app runs, others can download them from you too (they see this computer\'s internet '
                 'address). It\'s how cards stay available while their publisher is away. Off, their files '
                 'aren\'t kept: your characters stay.').classes('text-sm muted -mt-3 ml-12')

        async def share_changed(e) -> None:
            stopped = await sharing.set_share_downloads(bool(e.value))
            if stopped:
                ui.notify(f'{plural(stopped, "card")} no longer shared from this computer')
        share.on_value_change(share_changed)

        self._connection(settings, form)

        ui.label('Your publisher key').classes('step-title mt-2')
        self._key_block()
        form.text(settings.sharing, 'publisher_name', 'Publisher name',
                  hint='The name your cards are published under. Names aren\'t unique: your key tells you apart.') \
            .props(f'maxlength={fmt.MAX_PUBLISHER}').classes('w-full').mark('sharing-publisher-name')

        ui.label('Relays').classes('step-title mt-2')
        ui.label('The servers where cards are announced and looked for. Several are better: each keeps what it '
                 'wants, for as long as it wants.').classes('text-sm muted')

        @ui.refreshable
        def relays() -> None:
            known = sharing.catalog.relays()  # (a few rows: read at once)
            with ui.list().props('separator dense').classes('w-full').mark('sharing-relays'):
                for relay in known:
                    with ui.item().mark(f'relay-{relay.url}'):
                        with ui.item_section().classes('min-w-0'):
                            ui.item_label(relay.url).classes('break-all')
                            if relay.last_error:
                                ui.item_label(f'Last time: {relay.last_error}').props('caption') \
                                    .classes('text-negative')
                            elif relay.last_success_at:
                                ui.item_label(f'Looked {time_ago(_when(relay.last_success_at))}').props('caption')
                            else:
                                ui.item_label('Not looked at yet').props('caption')
                        with ui.item_section().props('side'):
                            ui.button(icon='close', on_click=lambda u=relay.url: remove(u)) \
                                .props(f'flat round dense aria-label="Remove {relay.url}"') \
                                .mark(f'relay-remove-{relay.url}')
            if not known:
                ui.label('None: no cards can be found or published.').classes('text-sm text-negative')
            missing = [u for u in DEFAULT_RELAYS if u not in {r.url for r in known}]
            if missing:
                ui.button(f'Add the default relays ({len(missing)})', on_click=lambda: add_defaults(missing)) \
                    .props('flat dense').mark('sharing-defaults')

        async def remove(url: str) -> None:
            await sharing.call(sharing.catalog.remove_relay, url)
            relays.refresh()

        def sending(cards: int, relays_: int) -> None:
            if cards:
                ui.notify(f'Your {plural(cards, "card")} {"is" if cards == 1 else "are"} being sent to '
                          f'{"it" if relays_ == 1 else "them"}.')

        async def add_defaults(urls: list[str]) -> None:
            cards = 0
            for url in urls:
                cards = max(cards, await sharing.add_relay(url))
            relays.refresh()
            sending(cards, len(urls))

        async def add() -> None:
            try:
                url = normalize_relay_url(new.value or '')
            except ValueError:
                new.props('error error-message="A relay\'s address starts with wss://"')
                return
            cards = await sharing.add_relay(url)
            new.props(remove='error error-message')
            new.value = ''
            relays.refresh()
            sending(cards, 1)

        relays()
        with ui.row().classes('w-full items-start no-wrap gap-2'):
            new = ui.input('Add a relay', placeholder='wss://relay.example.com').props('outlined dense') \
                .classes('grow').mark('sharing-new-relay')
            new.on('keydown.enter', add)
            ui.button('Add', on_click=add).props('outline').classes('mt-1').mark('sharing-add-relay')

        ui.label('Blocked publishers').classes('step-title mt-2')

        @ui.refreshable
        def blocked() -> None:
            them = sharing.catalog.blocked_publishers()
            if not them:
                ui.label('None. To block one, open one of their cards.').classes('text-sm muted') \
                    .mark('sharing-none-blocked')
                return
            with ui.list().props('separator dense').classes('w-full').mark('sharing-blocked'):
                for person in them:
                    with ui.item():
                        with ui.item_section().classes('min-w-0'):
                            ui.item_label(person.name or 'Unknown name')
                            ui.item_label(f'{short_key(person.public_key)} · blocked '
                                          f'{time_ago(_when(person.blocked_at))}').props('caption')
                        with ui.item_section().props('side'):
                            ui.button('Unblock', on_click=lambda k=person.public_key, n=person.name: unblock(k, n)) \
                                .props('flat dense').mark(f'unblock-{person.public_key}')

        async def unblock(key: str, name: str) -> None:
            await sharing.unblock(key)
            ui.notify(f'Unblocked {name or short_key(key)}: their cards come back from the relays')
            blocked.refresh()

        blocked()
