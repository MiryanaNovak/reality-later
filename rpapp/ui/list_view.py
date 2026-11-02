"""A reusable list or grid: one row (or card) per item, a ⋮ menu of actions each, optional search.

It knows nothing about where items come from. You give it:
  - items():  returns the items to show (called again on every refresh),
  - row(item): how an item looks (a Row),
  - actions:   what the ⋮ menu offers.

    view = ListView(
        items=store.list,
        key=lambda c: c.id,
        row=lambda c: Row(title=c.name, subtitle=c.model, icon='hub'),
        on_open=lambda c: ui.navigate.to(f'/connections/{c.id}'),
        actions=[Action('Delete', delete, danger=True)],
        search=lambda c: c.name,
    )
    ...
    view.refresh()   # after the items changed

With layout='grid', items show as square picture cards (Row.image, or the
title's initials when there's no picture) instead of rows.

CollectionPage (collection_page.py) builds on this for whole "list of saved
objects" pages; the lore book page uses it directly for its entries.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from nicegui import ui

T = TypeVar('T')

# For buttons and links inside a clickable row or card: stop the click there, in the browser,
# so it doesn't also open the item. (Their own handler is a separate, normal 'click' listener.)
NO_BUBBLE = '() => {}'


@dataclass
class Link:
    """A clickable text or button inside a row or card (it doesn't open the item)."""
    label: str
    handler: Callable[[], Any]  # may be async
    icon: str = ''
    marker: str = ''            # for tests


@dataclass
class Row:
    """How one item appears in a ListView (as a row, or as a card in a grid)."""
    title: str
    subtitle: str = ''
    icon: str = ''
    icon_color: str = 'primary'
    icon_tooltip: str = ''
    side: str = ''          # short text on the right, hidden on phones (lists only)
    dimmed: bool = False    # e.g. a disabled entry
    image: str = ''         # picture URL (grid cards; in lists only with `thumb`)
    thumb: bool = False     # lists: show the picture, or initials, as a small thumbnail
    thumb_label: str = ''   # whose initials the thumbnail shows without a picture; default: the title
    note: str = ''          # an extra small line, e.g. where the item is used
    warning: str = ''       # a problem to point out, shown in red
    link: Link | None = None    # a clickable line under the title
    body: str = ''          # grid cards: a text shown in full, under the title
    button: Link | None = None  # grid cards: a button at the bottom


@dataclass
class Action(Generic[T]):
    """One entry of a row's ⋮ menu. `label` can depend on the item (e.g. Enable / Disable)."""
    label: str | Callable[[T], str]
    handler: Callable[[T], Any]  # may be async
    danger: bool = False
    marker: str = ''             # for tests; defaults to the label

    def text(self, item: T) -> str:
        return self.label(item) if callable(self.label) else self.label


class ListView(Generic[T]):
    def __init__(self, *,
                 items: Callable[[], Sequence[T]],
                 key: Callable[[T], str],
                 row: Callable[[T], Row],
                 on_open: Callable[[T], Any] | None = None,
                 actions: Sequence[Action[T]] = (),
                 search: Callable[[T], str] | None = None,
                 empty_title: str = 'Nothing here yet',
                 empty_text: str = '',
                 search_from: int = 8,
                 page_size: int = 100,
                 layout: Literal['list', 'grid'] = 'list') -> None:
        """
        :param search: text to search in for each item; the search box shows once there are `search_from` items
        :param page_size: rows shown at first; a "Show more" button reveals the next ones
        :param layout: 'list' for rows, 'grid' for picture cards
        """
        self._items, self._key, self._row, self._on_open = items, key, row, on_open
        self._actions, self._search = list(actions), search
        self._empty_title, self._empty_text = empty_title, empty_text
        self._search_from, self._page_size = search_from, page_size
        self._limit = page_size
        self._layout = layout

        with ui.column().classes('w-full gap-3'):
            self.search_box = ui.input(placeholder='Search').props('clearable outlined dense debounce=250') \
                .classes('w-full')
            with self.search_box.add_slot('prepend'):
                ui.icon('search')
            self.search_box.on_value_change(self._search_changed)
            self._render()

    def refresh(self) -> None:
        self._render.refresh()

    def _search_changed(self) -> None:
        self._limit = self._page_size
        self.refresh()

    def _show_more(self) -> None:
        self._limit += self._page_size
        self.refresh()

    @ui.refreshable_method
    def _render(self) -> None:
        items = list(self._items())
        query = (self.search_box.value or '').strip().lower()
        self.search_box.set_visibility(self._search is not None and (len(items) >= self._search_from or bool(query)))
        if query and self._search:
            items = [item for item in items if query in self._search(item).lower()]

        if not items:
            with ui.column().classes('panel gap-1'):
                if query:
                    ui.label(f'Nothing matches “{query}”').classes('panel-title')
                else:
                    ui.label(self._empty_title).classes('panel-title')
                    if self._empty_text:
                        ui.label(self._empty_text).classes('muted')
            return

        if self._layout == 'grid':
            with ui.element('div').classes('item-grid'):
                for item in items[:self._limit]:
                    self._render_card(item)
        else:
            with ui.list().props('separator').classes('panel item-list'):
                for item in items[:self._limit]:
                    self._render_row(item)
        if len(items) > self._limit:
            with ui.row().classes('w-full items-center justify-between'):
                ui.label(f'Showing {self._limit} of {len(items)}').classes('text-sm muted')
                ui.button('Show more', on_click=self._show_more).props('flat')

    def _render_row(self, item: T) -> None:
        render_row(item, self._row(item), key=self._key(item), on_open=self._on_open, actions=self._actions)

    def _render_card(self, item: T) -> None:
        render_card(item, self._row(item), key=self._key(item), on_open=self._on_open, actions=self._actions)


# -- drawing one item; also used by TreeView ------------------------------------------------

def render_row(item: T, row: Row, *, key: str, on_open: Callable[[T], Any] | None = None,
               actions: Sequence[Action[T]] = ()) -> ui.item:
    open_item = (lambda: on_open(item)) if on_open else None
    with ui.item(on_click=open_item).mark(f'row-{key}').classes('opacity-60' if row.dimmed else '') as element:
        if row.thumb:
            with ui.item_section().props('avatar'):
                thumbnail(row.thumb_label or row.title, row.image)
        elif row.icon:
            with ui.item_section().props('avatar'):
                icon = ui.icon(row.icon, color=row.icon_color)
                if row.icon_tooltip:
                    icon.tooltip(row.icon_tooltip)
        with ui.item_section().classes('min-w-0'):
            ui.item_label(row.title).classes('break-words')
            if row.subtitle:
                ui.item_label(row.subtitle).props('caption lines=2').classes('break-words')
            if row.link:
                _link(row.link)
            if row.note:
                ui.item_label(row.note).props('caption').classes('row-note')
            if row.warning:
                ui.item_label(row.warning).props('caption').classes('text-negative')
        if row.side:
            with ui.item_section().props('side').classes('gt-xs'):
                ui.label(row.side).classes('text-sm muted')
        if actions:
            with ui.item_section().props('side'):
                actions_menu(item, actions)
    return element


def render_card(item: T, row: Row, *, key: str, on_open: Callable[[T], Any] | None = None,
                actions: Sequence[Action[T]] = ()) -> ui.element:
    with ui.element('div').classes('grid-card' + (' opacity-60' if row.dimmed else '')) \
            .mark(f'row-{key}').props('tabindex=0') as card:
        if on_open:
            card.on('click', lambda: on_open(item))
            card.on('keydown.enter', lambda: on_open(item))
        with ui.element('div').classes('grid-card-image'):
            if row.image:
                ui.image(row.image).props(f'ratio=1 fit=cover no-spinner alt="{_attr(row.title)}"') \
                    .classes('w-full h-full')
            else:
                ui.label(initials(row.title)).classes('grid-card-initials')
        with ui.row().classes('w-full items-start no-wrap gap-0 pl-3 pr-1 pt-2'):
            with ui.column().classes('min-w-0 grow gap-0'):
                ui.label(row.title).classes('grid-card-title')
                if row.subtitle:
                    ui.label(row.subtitle).classes('text-sm muted truncate w-full')
                if row.link:
                    _link(row.link)
                if row.note:
                    ui.label(row.note).classes('text-xs muted truncate w-full row-note')
                if row.warning:
                    with ui.row().classes('items-center no-wrap gap-1 text-negative text-xs mt-1'):
                        ui.icon('error_outline', size='1.1em')
                        ui.label(row.warning)
            if actions:
                actions_menu(item, actions)
        if row.body:
            ui.label(row.body).classes('grid-card-body')
        if row.button:
            ui.button(row.button.label, icon=row.button.icon or None, on_click=row.button.handler) \
                .props('outline dense').classes('grid-card-button').on('click.stop', js_handler=NO_BUBBLE) \
                .mark(row.button.marker or f'button-{slug(row.button.label)}')
        else:
            ui.element('div').classes('pb-2')
    return card


def actions_menu(item: T, actions: Sequence[Action[T]]) -> None:
    with ui.button(icon='more_vert').props('flat round dense aria-label="Actions"') \
            .on('click.stop', js_handler=NO_BUBBLE):
        with ui.menu():
            for action in actions:
                text = action.text(item)
                ui.menu_item(text, lambda a=action: a.handler(item)) \
                    .mark(f'menu-{action.marker or slug(text)}') \
                    .classes('text-negative' if action.danger else '')


def thumbnail(title: str, image: str = '') -> ui.element:
    """A small square picture, or the title's initials when there's none."""
    with ui.element('div').classes('profile-thumb') as box:
        if image:
            ui.image(image).props('ratio=1 fit=cover no-spinner').classes('w-full h-full')
        else:
            ui.label(initials(title))
    return box


def _link(link: Link) -> None:
    with ui.row().classes('items-center no-wrap gap-1 card-link') \
            .on('click', link.handler).on('click.stop', js_handler=NO_BUBBLE) \
            .on('keydown.enter', link.handler).on('keydown.enter.stop', js_handler=NO_BUBBLE) \
            .props('tabindex=0 role=link').mark(link.marker or f'link-{slug(link.label)}'):
        if link.icon:
            ui.icon(link.icon, size='1.05em')
        ui.label(link.label)


def initials(title: str) -> str:
    """'Captain Hale' -> 'CH', 'Mira' -> 'MI', 'Wren (sci-fi)' -> 'WR', 'Mira, wandering healer' -> 'MI'."""
    main = re.split(r'[(,\[]', title)[0]
    words = [w for w in re.split(r'[^\w]+', main) if w] or [w for w in re.split(r'[^\w]+', title) if w]
    if not words:
        return '?'
    return (words[0][0] + words[-1][0] if len(words) > 1 else words[0][:2]).upper()


def plural(n: int, word: str) -> str:
    """plural(1, 'entry') -> '1 entry', plural(4, 'entry') -> '4 entries', plural(2, 'book') -> '2 books'."""
    if n == 1:
        return f'{n} {word}'
    return f'{n} {word[:-1]}ies' if word.endswith('y') and word[-2:-1] not in 'aeiou' else f'{n} {word}s'


def _attr(text: str) -> str:
    return text.replace('"', "'")


def slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
