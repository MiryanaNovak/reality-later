"""Small building blocks shared by all pages."""
from __future__ import annotations

import functools
import weakref
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Literal

from nicegui import Client, events, ui

from .. import APP_NAME

from .form import text_stats  # noqa: F401  (kept importable from here)
from .list_view import slug


Audience = Literal['ai', 'you']
AUDIENCES: dict[str, tuple[str, str]] = {  # icon, text
    'ai': ('smart_toy', 'Sent to the AI'),
    'you': ('person', 'Only for you'),
}


@contextmanager
def panel(title: str, caption: str = '', *, audience: Audience | None = None) -> Iterator[None]:
    """A group of fields that's always open. `audience`: a tag saying whether the AI gets what's in it."""
    with ui.column().classes('panel gap-3'):
        with ui.column().classes('gap-0'):
            if audience:
                with ui.row().classes('items-center gap-x-2 gap-y-1'):
                    ui.label(title).classes('panel-title')
                    audience_tag(audience)
            else:
                ui.label(title).classes('panel-title')
            if caption:
                ui.label(caption).classes('muted text-sm')
        yield


def audience_legend(ai: str, you: str) -> None:
    """Two lines for the top of an editor: what's sent to the AI, and what's only for you."""
    with ui.element('div').classes('audience-legend').mark('audience-legend'):
        audience_tag('ai')
        ui.label(ai).classes('text-sm')
        audience_tag('you')
        ui.label(you).classes('text-sm')


def audience_tag(audience: Audience) -> ui.row:
    """A small tag: "Sent to the AI" or "Only for you"."""
    icon, text = AUDIENCES[audience]
    with ui.row().classes(f'audience audience-{audience} items-center no-wrap gap-1') \
            .mark(f'audience-{audience}') as tag:
        ui.icon(icon)
        ui.label(text)
    return tag


@contextmanager
def folding_panel(title: str, caption: str = '', *, opened: bool = False) -> Iterator[None]:
    """A group of fields that folds away; closed until you open it, unless `opened`."""
    with ui.expansion(title, caption=caption or None, value=opened).classes('panel fold') as expansion:
        # Text areas that grow with their text can't measure it while folded away: have them do it on opening.
        expansion.on('after-show', js_handler=f'() => getElement({expansion.id}).$el.querySelectorAll("textarea")'
                                              '.forEach(t => t.dispatchEvent(new Event("input")))')
        with ui.column().classes('w-full gap-3'):
            yield


async def confirm(title: str, text: str, action_label: str, *, danger: bool = True, warning: str = '') -> bool:
    """Ask a yes/no question in a dialog. `warning`, if given, is an extra line in bold red."""
    with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes('dialog-card').mark('confirm-dialog'):
        ui.label(title).classes('panel-title')
        if text:
            ui.label(text).classes('muted')
        if warning:
            ui.label(warning).classes('text-negative text-weight-bold').mark('confirm-warning')
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Cancel', on_click=lambda: dialog.submit(False)).props('flat')
            ui.button(action_label, color='negative' if danger else 'primary', on_click=lambda: dialog.submit(True))
    result = await dialog
    try:
        dialog.delete()
    except (ValueError, KeyError):  # the browser tab was closed while the dialog was open
        pass
    return bool(result)


async def ask_name(title: str, value: str, *, label: str = 'Name', action_label: str = 'Rename',
                   max_length: int | None = None) -> str | None:
    """Ask for a name in a dialog, starting from `value` (selected, to type over it). Return becomes the
    button. Returns the name, stripped, or None if cancelled. An empty name isn't accepted."""
    with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes('dialog-card').mark('name-dialog'):
        ui.label(title).classes('panel-title')
        field = ui.input(label, value=value).classes('w-full').props('autofocus').mark('name-input')
        if max_length:
            field.props(f'maxlength={max_length}')
        # select it all on opening, so typing replaces it
        field.on('focus', js_handler='(e) => e.target.select()')
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Cancel', on_click=lambda: dialog.submit(None)).props('flat')
            ui.button(action_label, on_click=lambda: done()).mark('name-save')

    def done() -> None:
        name = (field.value or '').strip()
        if name:
            dialog.submit(name)
        else:
            field.props('error error-message="Give it a name"')

    field.on('keydown.enter', done)
    result = await dialog
    try:
        dialog.delete()
    except (ValueError, KeyError):  # the browser tab was closed while the dialog was open
        pass
    return result if isinstance(result, str) and result else None


# -- the top bar ---------------------------------------------------------------------------
#
# The page's title lives in the bar at the top of the window, which stays put while the page scrolls,
# with what goes with it: a back arrow, a second line, buttons, a ⋮ menu. The app's layout makes an
# empty slot in the bar (bar_slot) and empties it before each page is drawn (clearing_bar), so a page
# fills it with page_bar() or page_header(), and a page that doesn't leaves no stale title behind.

_bars: weakref.WeakKeyDictionary[Client, ui.element] = weakref.WeakKeyDictionary()

MenuItems = Sequence[tuple[str, Callable[[], Any], bool]]  # (label, handler, danger)


def bar_slot() -> ui.element:
    """Call in the top bar, once: the place where pages put their title."""
    slot = ui.element('div').classes('app-bar').mark('app-bar')
    _bars[ui.context.client] = slot
    return slot


def clearing_bar(page: Callable[..., Any]) -> Callable[..., Any]:
    """`page`, which empties the top bar first (its parameters are kept: NiceGUI reads them)."""
    @functools.wraps(page)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if (slot := _bars.get(ui.context.client)) is not None:
            slot.clear()
        ui.page_title(APP_NAME)
        return page(*args, **kwargs)
    return wrapper


def page_bar(title: str, *, subtitle: str = '', back: str | None = None, menu: MenuItems = (),
             actions: Callable[[], None] | None = None) -> ui.label:
    """Put the page's title in the top bar, with an optional back arrow, second line, buttons (`actions`
    builds them) and ⋮ menu (items: label, handler, danger). Returns the title label, to update it.

    Outside the app's layout (a page built on its own, in some tests), it goes at the top of the page.
    """
    slot = _bars.get(ui.context.client)
    if slot is not None:
        slot.clear()
        ui.page_title(title if title == APP_NAME else f'{title} · {APP_NAME}')
    with slot if slot is not None else ui.element('div').classes('app-bar w-full'):
        if back:
            ui.button(icon='arrow_back', on_click=lambda: ui.navigate.to(back)) \
                .props('flat round dense aria-label="Back"').mark('bar-back')
        with ui.column().classes('app-bar-titles'):
            title_label = ui.label(title).classes('app-bar-title').props('role=heading aria-level=1') \
                .mark('page-title')
            if subtitle:
                ui.label(subtitle).classes('app-bar-subtitle').props['title'] = subtitle  # all of it, on hover
        if actions:
            with ui.row().classes('app-bar-actions'):
                actions()
        if menu:
            with ui.button(icon='more_vert').props('flat round dense aria-label="Actions"').mark('bar-menu'):
                with ui.menu():
                    for label, handler, danger in menu:
                        ui.menu_item(label, handler).mark(f'menu-{slug(label)}') \
                            .classes('text-negative' if danger else '')
    return title_label


def page_header(title: str, caption: str = '', *, back: str | None = None, menu: MenuItems = (),
                subtitle: str = '') -> ui.label:
    """The page's title in the top bar (see page_bar), and `caption` as the first line of the page.

    Returns the title label so the page can update it.
    """
    title_label = page_bar(title, subtitle=subtitle, back=back, menu=menu)
    if caption:
        ui.label(caption).classes('muted text-sm').mark('page-caption')
    return title_label



def time_ago(when: datetime, now: datetime | None = None) -> str:
    """'just now', '5 minutes ago', '3 hours ago', 'yesterday', '4 days ago', or a date ('2 Oct 2026')."""
    now = now or datetime.now(timezone.utc)
    seconds = (now - when).total_seconds()
    if seconds < 60:
        return 'just now'
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f'{minutes} minute{"s" if minutes > 1 else ""} ago'
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f'{hours} hour{"s" if hours > 1 else ""} ago'
    days = int(seconds // 86400)
    if days == 1:
        return 'yesterday'
    if days < 7:
        return f'{days} days ago'
    local = when.astimezone()
    return f'{local.day} {local:%b %Y}'


def not_found(what: str, back: str) -> None:
    page_bar(f'{what} not found', back=back)
    ui.label('It may have been deleted, or its file renamed.').classes('muted')
    ui.button('Back', icon='arrow_back', on_click=lambda: ui.navigate.to(back)).props('outline')


class FilePicker(ui.upload):
    """An invisible uploader. Open its file picker from any button:

        picker = file_picker(handle_file, accept='image/*')
        ui.button('Choose image').on('click', js_handler=picker.js_open)

    The picker has to open in the browser, during the click itself: phones
    refuse to open it later, after a round trip to the server.
    """

    @property
    def js_open(self) -> str:
        return f'() => getElement({self.id}).$refs.qRef.pickFiles()'


def file_picker(on_file: Callable[[events.UploadEventArguments], Awaitable[Any] | Any], *,
                accept: str, max_size: int = 20_000_000) -> FilePicker:
    async def handle(e: events.UploadEventArguments) -> None:
        picker.reset()  # so the same file can be picked again
        result = on_file(e)
        if isinstance(result, Awaitable):
            await result

    picker = FilePicker(on_upload=handle, auto_upload=True, max_file_size=max_size,
                        on_rejected=lambda: ui.notify('That file is too big.', type='negative'))
    picker.props(f'accept="{accept}"').classes('offscreen')
    return picker
