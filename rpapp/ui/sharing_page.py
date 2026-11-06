"""Shared cards: browsing the cards people publish (rpapp/publishing/), and Settings › Sharing cards.

    /shared               the catalog: the latest version of each card, newest first, with filters;
                          ?publisher=<key> one publisher's, ?tag=<tag> one tag's
    /shared/{event_id}    one publication: its description, details, publisher and versions; blocking

Looking for new cards runs in the background (Sharing.discover()): when the page opens, if the last look
is an hour old, or with its button; the page shows how far it is, and what it found once done.

Everything shown comes from strangers: it's shown as text, never as HTML, and nothing is loaded from
elsewhere (no pictures: those come with the files, later).
"""
from __future__ import annotations

from datetime import datetime, timezone

from nicegui import ui

from ..publishing import runtime_problem
from ..publishing.catalog import CardQuery, CatalogEntry, normalize_relay_url
from ..publishing.nostr import to_npub
from ..publishing.service import DEFAULT_RELAYS, Sharing
from ..settings import Settings, SettingsStore
from .form import Form
from .list_view import Row, plural, render_row
from .widgets import confirm, not_found, page_bar, panel, time_ago

PAGE = 50


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


class SharingSection:
    def __init__(self, sharing: Sharing | None, settings: SettingsStore, *, problem: str = '') -> None:
        self.sharing = sharing      # None: sharing can't run here, and `problem` says why
        self.settings = settings
        self.problem = problem

    # --- the catalog ------------------------------------------------------------------------------

    async def list_page(self, publisher: str = '', tag: str = '') -> None:
        page_bar('Shared cards', actions=self._look_button if self.sharing else None)
        if self.sharing is None:
            self._unavailable()
            return
        sharing = self.sharing
        try:
            settings = self.settings.load()
        except ValueError:
            settings = Settings()
        shown: list[CatalogEntry] = []
        cursor: list[str | None] = [None]

        self._status()

        # The filters
        with ui.column().classes('w-full gap-2'):
            search = ui.input(placeholder='Search by name').props('clearable outlined dense debounce=300') \
                .classes('w-full').mark('shared-search')
            with search.add_slot('prepend'):
                ui.icon('search')
            with ui.row().classes('w-full items-center no-wrap gap-3'):
                tags = ui.input_chips('Tags', value=[tag] if tag else [], new_value_mode='add-unique') \
                    .props('outlined dense clearable hide-bottom-space').classes('grow').mark('shared-tags')
                nsfw = ui.switch('Show NSFW', value=settings.sharing.show_nsfw).mark('shared-nsfw')
            if publisher:
                who = await sharing.call(sharing.catalog.publisher, publisher)
                with ui.row().classes('items-center gap-2').mark('shared-publisher-filter'):
                    ui.label('Only the cards of').classes('text-sm muted')
                    ui.chip(f'{who.current_name if who else "a publisher"} · {short_key(publisher)}', icon='person',
                            removable=True, on_value_change=lambda: ui.navigate.to('/shared')) \
                        .props('dense outline')

        def query(cursor_: str | None) -> CardQuery:
            return CardQuery(card_name=search.value or None, tags=tuple(tags.value or ()),
                             publisher_id=publisher or None, content_rating=None if nsfw.value else 'sfw',
                             exclude_tags=tuple(settings.sharing.hidden_tags), limit=PAGE, cursor=cursor_)

        @ui.refreshable
        def results() -> None:
            if not shown:
                with ui.column().classes('panel gap-1').mark('shared-empty'):
                    if search.value or tags.value or publisher:
                        ui.label('No card matches').classes('panel-title')
                    else:
                        ui.label('No shared cards yet').classes('panel-title')
                        ui.label('Cards people publish show here, once found on the relays (Settings › Sharing '
                                 'cards).').classes('muted')
                return
            with ui.list().props('separator').classes('panel item-list').mark('shared-list'):
                for entry in shown:
                    render_row(entry, self._row(entry), key=entry.publication.event_id,
                               on_open=lambda e: ui.navigate.to(f'/shared/{e.publication.event_id}'))
            if cursor[0]:
                ui.button('Show more', on_click=more).props('flat').mark('shared-more')

        async def load(more_: bool = False) -> None:
            page = await sharing.call(sharing.catalog.cards, query(cursor[0] if more_ else None))
            if not more_:
                shown.clear()
            shown.extend(page.items)
            cursor[0] = page.next_cursor
            results.refresh()

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

        page = await sharing.call(sharing.catalog.cards, query(None))
        shown.extend(page.items)
        cursor[0] = page.next_cursor
        results()
        search.on_value_change(load)
        tags.on_value_change(load)
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
    def _row(entry: CatalogEntry) -> Row:
        p = entry.publication
        description = ' '.join(p.card_description.split())
        nsfw = p.content_rating == 'nsfw'
        note = f'{entry.publisher_name} · {time_ago(_when(p.created_at))}'
        if p.card_tags:
            note += ' · ' + ' '.join(f'#{t}' for t in p.card_tags[:6])
        return Row(title=p.card_name, subtitle=description, note=note,
                   icon='explicit' if nsfw else 'style', icon_color='negative' if nsfw else 'primary',
                   icon_tooltip='Rated NSFW by its publisher' if nsfw else '', side=size_text(p.card_size))

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

        async def block() -> None:
            name = entry.publisher_name
            if not await confirm(f'Block {name}?', 'All their cards leave your catalog, and theirs won\'t show '
                                 'again. You can unblock them in Settings › Sharing cards.', 'Block'):
                return
            gone = await sharing.block(p.publisher_id)
            ui.notify(f'Blocked {name}: {plural(gone, "publication")} removed')
            ui.navigate.to('/shared')

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
            ui.label('Downloading shared cards isn\'t there yet.').classes('text-sm muted')

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

    def _unavailable(self) -> None:
        with ui.column().classes('panel gap-1').mark('shared-unavailable'):
            ui.label('Sharing cards isn\'t available here').classes('panel-title')
            ui.label(self.problem or f'It needs Python 3.13 or earlier: {runtime_problem()}.') \
                .classes('muted break-words')

    # --- Settings › Sharing cards -----------------------------------------------------------------

    def settings_panel(self, settings: Settings, form: Form) -> None:
        """What to show, the relays, and the blocked publishers."""
        form.switch(settings.sharing, 'show_nsfw', 'Show NSFW cards').mark('sharing-nsfw')
        ui.label('Cards their publisher rated NSFW. It\'s their own rating: nobody checks it.') \
            .classes('text-sm muted -mt-3 ml-12')
        form.chips(settings.sharing, 'hidden_tags', 'Tags to hide',
                   hint='Cards with any of these tags aren\'t shown. Press Enter after each.') \
            .mark('sharing-hidden-tags')
        if self.sharing is None:
            ui.label(f'Sharing cards isn\'t available here. {self.problem or runtime_problem()}') \
                .classes('muted break-words').mark('sharing-unavailable')
            return
        sharing = self.sharing

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

        async def add_defaults(urls: list[str]) -> None:
            for url in urls:
                await sharing.call(sharing.catalog.add_relay, url)
            relays.refresh()

        async def add() -> None:
            try:
                url = normalize_relay_url(new.value or '')
            except ValueError:
                new.props('error error-message="A relay\'s address starts with wss://"')
                return
            await sharing.call(sharing.catalog.add_relay, url)
            new.props(remove='error error-message')
            new.value = ''
            relays.refresh()

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
