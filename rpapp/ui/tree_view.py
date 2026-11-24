"""A reusable two-level tree: groups that fold open onto their items.

    TreeView(
        groups=lambda: [TreeGroup(key='seraphina', row=Row(title='Seraphina', ...), items=[...]), ...],
        key=lambda conversation: conversation.id,
        row=lambda conversation: Row(title=conversation.title, ...),
        on_open=open_chat,
        actions=[Action('Delete', delete, danger=True)],
        focus='seraphina',   # this group starts open, highlighted and scrolled into view
        search=lambda conversation: conversation.title,   # optional
        noun='conversation',
    )

Groups and items are drawn with the same Row description as ListView (see list_view.py).
Groups show in the order given; which ones are open is kept when the tree is refreshed, and, with `opened`
(opened.py), while you go to another page and back (for the browser tab) and in a cookie (for the device).
A row's or a group's picture can open a page of its own (Row.thumb_link): a click on it doesn't open the row,
or fold the group.

With `search`, a search box shows above, as on ListView: once there are `search_from` items. While something's
written in it, only the items whose text has it show, in their groups, open ("1 of 3 conversations"); the
groups with none don't show. Emptied, the groups are as they were.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Generic, TypeVar

from nicegui import ui

from .list_view import Action, Row, plural, render_row, thumb_link, thumbnail
from .opened import Opened

T = TypeVar('T')


@dataclass
class TreeGroup(Generic[T]):
    key: str
    row: Row                        # how the group itself looks (title, subtitle, image, warning)
    items: list[T] = field(default_factory=list)


class TreeView(Generic[T]):
    def __init__(self, *,
                 groups: Callable[[], Sequence[TreeGroup[T]]],
                 key: Callable[[T], str],
                 row: Callable[[T], Row],
                 on_open: Callable[[T], Any] | None = None,
                 actions: Sequence[Action[T]] = (),
                 focus: str = '',
                 empty_title: str = 'Nothing here yet',
                 empty_text: str = '',
                 search: Callable[[T], str] | None = None,
                 search_from: int = 8,
                 noun: str = 'item',
                 opened: Opened | None = None) -> None:
        self._groups, self._key, self._row, self._on_open = groups, key, row, on_open
        self._actions = list(actions)
        self._focus = focus
        self._opened = opened
        self._open: set[str] = opened.keys() if opened is not None else set()  # (opened's own: kept as it changes)
        if focus:
            self._set_open(focus, True)
        self._empty_title, self._empty_text = empty_title, empty_text
        self._search, self._search_from, self._noun = search, search_from, noun
        with ui.column().classes('w-full gap-3'):
            self.search_box = ui.input(placeholder='Search').props('clearable outlined dense debounce=250') \
                .classes('w-full').mark(f'{noun}-search')
            with self.search_box.add_slot('prepend'):
                ui.icon('search')
            self.search_box.on_value_change(self.refresh)
            self._render()

    def focus(self, key: str) -> None:
        """Open, highlight and bring into view this group from the next refresh on ('': none)."""
        self._focus = key
        if key:
            self._set_open(key, True)

    def refresh(self) -> None:
        self._render.refresh()

    def _set_open(self, key: str, is_open: bool) -> None:
        if self._opened is not None:
            self._opened.set(key, is_open)  # (the same set as self._open, and the cookie)
        elif is_open:
            self._open.add(key)
        else:
            self._open.discard(key)

    @ui.refreshable_method
    def _render(self) -> None:
        groups = list(self._groups())
        query = (self.search_box.value or '').strip().lower()
        count = sum(len(group.items) for group in groups)
        self.search_box.set_visibility(self._search is not None and (count >= self._search_from or bool(query)))
        if not groups:
            with ui.column().classes('panel gap-1'):
                ui.label(self._empty_title).classes('panel-title')
                if self._empty_text:
                    ui.label(self._empty_text).classes('muted')
            return
        if query and self._search:  # (only what's found, in its group, open)
            found = [(group, [i for i in group.items if query in self._search(i).lower()]) for group in groups]
            shown = [(group, items) for group, items in found if items]
            if not shown:
                with ui.column().classes('panel gap-1').mark('tree-nothing'):
                    ui.label(f'Nothing matches “{query}”').classes('panel-title')
                return
        else:
            query, shown = '', [(group, group.items) for group in groups]

        focused = None
        with ui.column().classes('w-full gap-3'):
            for group, items in shown:
                is_focus = group.key == self._focus and not query
                expansion = ui.expansion(value=bool(query) or group.key in self._open) \
                    .classes('panel fold tree-group' + (' tree-focus' if is_focus else '')) \
                    .mark(f'group-{group.key}')
                if not query:  # (opened while searching: as they were, once it's emptied)
                    expansion.on_value_change(lambda e, k=group.key: self._set_open(k, e.value))
                row = group.row
                if len(items) < len(group.items):
                    row = replace(row, subtitle=f'{len(items)} of {plural(len(group.items), self._noun)}')
                with expansion.add_slot('header'):
                    self._group_header(row)
                with expansion:
                    with ui.list().props('separator').classes('tree-items'):
                        for item in items:
                            render_row(item, self._row(item), key=self._key(item), on_open=self._on_open,
                                       actions=self._actions)
                if is_focus:
                    focused = expansion

        if focused is not None:  # once the page is drawn in the browser, bring the group into view
            ui.timer(0.3, lambda: ui.run_javascript(
                f'getHtmlElement({focused.id})?.scrollIntoView({{block: "start", behavior: "smooth"}})'), once=True)

    @staticmethod
    def _group_header(row: Row) -> None:
        with ui.row().classes('w-full items-center no-wrap gap-3 min-w-0'):
            picture = thumbnail(row.title, row.image)  # (its full size, whatever the title: theme.py's .profile-thumb)
            if row.thumb_link:  # (a click on it: not the group folding)
                thumb_link(picture, row.thumb_link, row.thumb_link_label).mark('thumb-link', 'group-picture')
            with ui.column().classes('gap-0 min-w-0 grow'):
                title = ui.label(row.title).classes('tree-group-title truncate w-full')  # (one line, then …)
                title.props['title'] = row.title  # (in full, on hover)
                if row.subtitle:
                    ui.label(row.subtitle).classes('text-sm muted')
                if row.warning:
                    ui.label(row.warning).classes('text-sm text-negative')
