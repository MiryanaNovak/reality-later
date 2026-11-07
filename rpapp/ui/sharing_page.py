"""Shared cards: browsing the cards people publish (rpapp/publishing/), publishing yours, and Settings ›
Sharing cards.

    /shared               the catalog: the latest version of each card, newest first, with filters;
                          ?publisher=<key> one publisher's, ?tag=<tag> one tag's
    /shared/{event_id}    one publication: its description, details, publisher and versions; blocking.
                          One of yours: how its file is shared, where its announcement arrived, sending it
                          again, stopping sharing, withdrawing

Publishing starts from a character's ⋮ menu (publish_dialog(), from characters_page.py): the card's name,
description, tags, rating and language, then the card file exported as for Export (with the card's UUID
in it, so that publishing it again makes a new version). Settings › Sharing cards has the publisher key:
made, imported, backed up, replaced.

Looking for new cards runs in the background (Sharing.discover()): when the page opens, if the last look
is an hour old, or with its button; the page shows how far it is, and what it found once done.

Everything shown comes from strangers: it's shown as text, never as HTML, and nothing is loaded from
elsewhere (no pictures: those come with the files, later).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from nicegui import ui

from ..publishing import runtime_problem
from ..publishing import announcements as fmt
from ..publishing.announcements import PublicationDraft, Rejected
from ..publishing.catalog import CardQuery, CatalogEntry, Delivery, normalize_relay_url
from ..publishing.identity import Identity, IdentityError
from ..publishing.nostr import to_npub
from ..publishing.seeder import TorrentStatus
from ..publishing.service import DEFAULT_RELAYS, Published, Sharing, SharingError, torrent_problem
from ..settings import Settings, SettingsStore
from .form import Form
from .list_view import Row, plural, render_row
from .widgets import confirm, not_found, page_bar, panel, time_ago

if TYPE_CHECKING:
    from ..characters import Character

log = logging.getLogger(__name__)

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


def rejected_text(e: Rejected) -> str:
    """Why a card can't be published, in plain words (the format's rules: docs/card-announcements.md)."""
    return {
        'empty:title': 'Give the card a name.',
        'too_long:title': f'The card\'s name is too long: {fmt.MAX_TITLE} characters at most.',
        'empty:publisher': 'Give your publisher name.',
        'too_long:publisher': f'Your publisher name is too long: {fmt.MAX_PUBLISHER} characters at most.',
        'too_long:description': f'The description is too long: {fmt.MAX_DESCRIPTION_BYTES // 1024} KB at most '
                                f'(about {fmt.MAX_DESCRIPTION_BYTES // 1000},000 letters).',
        'file_too_big': f'The card\'s file is too big to share: {fmt.DEFAULT_SIZE_LIMIT // 1024 // 1024} MB at '
                        f'most. Its picture is probably very large: try a smaller one.',
        'control_character': 'The name or the description has invisible control characters: remove them.',
        'too_large': 'Its announcement would be too big: shorten the description, or remove tags.',
    }.get(e.code, f'It can\'t be published ({e}).')


DELIVERY = {  # state: (icon, color, what it means)
    'accepted': ('check_circle', 'positive', 'Arrived'),
    'held': ('pause_circle', 'grey', 'Held: public relays get cards once the format is final'),
    'pending': ('schedule', 'warning', 'Not reached yet'),
    'refused': ('block', 'negative', 'Refused'),
}


def delivery_text(d: Delivery) -> str:
    text = DELIVERY.get(d.state, ('', '', d.state))[2]
    return f'{text}: {d.message}' if d.message and d.state in ('pending', 'refused') else text


def delivery_summary(deliveries: Iterable[Delivery]) -> str:
    """'arrived on 2 relays, 1 not reached, 6 held (public relays, while the format is a draft)'."""
    counts = Counter(d.state for d in deliveries)
    if not counts:
        return 'no relay to send it to (Settings › Sharing cards)'
    parts = []
    if counts['accepted']:
        parts.append(f'arrived on {plural(counts["accepted"], "relay")}')
    if missed := counts['pending'] + counts['refused']:
        parts.append(f'{plural(missed, "relay")} not reached')
    if counts['held']:
        parts.append(f'{counts["held"]} held (public relays, while the format is a draft)')
    return ', '.join(parts)


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
    peers = ('nobody downloading it now' if not status.peers else '1 person connected' if status.peers == 1 else
             f'{status.peers} people connected')
    sent = size_text(status.uploaded) if status.uploaded else 'nothing'
    return 'cloud_upload', f'Shared from this computer while the app runs: {peers}, {sent} sent since the app started.'


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
        me = self.my_key()
        mine = me is not None and me.public_key == p.publisher_id

        async def block() -> None:
            name = entry.publisher_name
            if not await confirm(f'Block {name}?', 'All their cards leave your catalog, and theirs won\'t show '
                                 'again. You can unblock them in Settings › Sharing cards.', 'Block'):
                return
            gone = await sharing.block(p.publisher_id)
            ui.notify(f'Blocked {name}: {plural(gone, "publication")} removed')
            ui.navigate.to('/shared')

        their_cards = ('All your cards' if mine else 'All their cards',
                       lambda: ui.navigate.to(f'/shared?publisher={p.publisher_id}'), False)
        page_bar(p.card_name, subtitle='by you' if mine else f'by {entry.publisher_name}', back='/shared',
                 menu=[their_cards] if mine else [their_cards, ('Block this publisher', block, True)])

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
            await self._own_panel(entry, versions)

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

    async def _own_panel(self, entry: CatalogEntry, versions: list[CatalogEntry]) -> None:
        """One of yours: how its file is shared from here, where its announcement (or its withdrawal) arrived,
        and what can be done about it."""
        sharing = self.sharing
        p = entry.publication
        withdrawn = entry.withdrawn is not None
        event_id = entry.withdrawn.withdrawal_id if withdrawn else p.event_id  # (what was sent last)

        async def reload() -> None:
            await ui.context.client.sub_pages_router.refresh()

        with panel('Your publication', 'Published with your key'):
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

            @ui.refreshable
            async def relays() -> None:
                deliveries = await sharing.call(sharing.catalog.deliveries, event_id)
                known = {d.relay_url for d in deliveries}
                # Where it could still go: a relay that wasn't reached, or one added since
                missing = any(d.state in ('pending', 'refused') for d in deliveries) or \
                    any(r.write and r.url not in known for r in await sharing.call(sharing.catalog.relays))
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
                                ui.item_label(delivery_text(d)).props('caption').classes('break-words')
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
                await relays.refresh()

            await relays()

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
            ui.label(self.problem or f'It needs Python 3.13 or earlier: {runtime_problem()}.') \
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
                             save: Callable[[Character], None]) -> Published | None:
        """Publish a character as a shared card (from its editor's ⋮ menu): ask how it's announced, then
        publish its card file (`export`) and open its page. `save` saves the character, given its card UUID
        the first time. What was published, or None."""
        sharing = self.sharing
        try:
            settings = self.settings.load()
        except ValueError:  # (a damaged settings file: the Settings page says so)
            settings = Settings()
        try:
            sharing.identity()
        except IdentityError as e:
            ui.notify(f'{e} (Settings › Sharing cards)', type='negative', multi_line=True)
            return None
        if problem := torrent_problem():
            ui.notify(f'Cards can\'t be shared from this computer: {problem}.', type='negative', multi_line=True)
            return None

        # Published before (with this key): a new version, starting from what was announced last time
        me = sharing.identity()
        before = await sharing.call(sharing.catalog.versions, me.public_key, character.shared_uuid,
                                    include_withdrawn=True) if me and character.shared_uuid else []
        last = before[0].publication if before else None

        fields: dict[str, ui.element] = {}
        with ui.context.client.layout, ui.dialog().props('persistent') as dialog, \
                ui.card().classes('dialog-card publish-card').mark('publish-dialog') as card:
            ui.label(f'Publish "{character.title}" to shared cards').classes('panel-title')

            @ui.refreshable
            def body() -> None:
                if sharing.identity() is None:
                    no_key()
                else:
                    form()
            def no_key() -> None:
                ui.label('Publishing needs a publisher key: it signs your cards, so that people know they\'re '
                         'yours, and only it can publish new versions of them, or withdraw them.').classes('muted')
                ui.label('It\'s kept on this computer. Back it up afterwards, in Settings › Sharing cards: lost, '
                         'it can\'t be recovered. (A key of yours already? Import it there.)').classes('text-sm muted')
                with ui.row().classes('w-full justify-end gap-2'):
                    ui.button('Cancel', on_click=lambda: dialog.submit(None)).props('flat')
                    ui.button('Make my key', icon='key', on_click=make_key).mark('publish-make-key')

            def make_key() -> None:
                try:
                    sharing.create_identity()
                except (IdentityError, OSError) as e:
                    ui.notify(f'The key can\'t be made: {e}', type='negative', multi_line=True)
                    return
                body.refresh()

            def form() -> None:
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
                fields['tags'] = ui.input_chips('Tags', value=list(last.card_tags) if last else [],
                                                new_value_mode='add-unique') \
                    .props('outlined dense clearable hide-bottom-space').classes('w-full').mark('publish-tags')
                ui.label(f'Press Enter after each: {fmt.MAX_CARD_TAGS} at most, e.g. fantasy, romance, '
                         f'sci-fi.').classes('text-sm muted -mt-3')
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
                with ui.column().classes('publish-notice gap-1 text-sm').mark('publish-notice'):
                    ui.label('Everything in the card becomes public: its picture, scenario, first messages, '
                             'example dialogues and lore books. Anyone can download it; those who have it keep '
                             'it, even if you withdraw it.')
                    ui.label('This computer shares its file while the app runs (BitTorrent): those who '
                             'download it see this computer\'s internet address.')
                    if not sharing.public_publishing:
                        ui.label('While card sharing\'s format is a draft, cards only go to relays on this '
                                 'computer or its network.').classes('muted')
                fields['error'] = ui.label('').classes('text-negative text-sm break-words').mark('publish-error')
                fields['error'].set_visibility(False)
                with ui.row().classes('w-full justify-end gap-2'):
                    ui.button('Cancel', on_click=lambda: dialog.submit(None)).props('flat')
                    fields['button'] = ui.button('Publish', icon='publish', on_click=publish).mark('publish-confirm')

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
            try:
                current = self.settings.load()
                if current.sharing.publisher_name != publisher:
                    current.sharing.publisher_name = publisher
                    self.settings.save(current)
            except ValueError:
                pass
            dialog.submit(published)

        with card:
            body()
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

    # --- Settings › Sharing cards -----------------------------------------------------------------

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
                        ui.button('Your cards', on_click=lambda: ui.navigate.to(f'/shared?publisher={me.public_key}')) \
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
        """What to show, your publisher key, the relays, and the blocked publishers."""
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
