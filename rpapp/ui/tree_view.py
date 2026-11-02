"""A reusable two-level tree: groups that fold open onto their items.

    TreeView(
        groups=lambda: [TreeGroup(key='seraphina', row=Row(title='Seraphina', ...), items=[...]), ...],
        key=lambda conversation: conversation.id,
        row=lambda conversation: Row(title=conversation.title, ...),
        on_open=open_chat,
        actions=[Action('Delete', delete, danger=True)],
        focus='seraphina',   # this group starts open, highlighted and scrolled into view
    )

Groups and items are drawn with the same Row description as ListView (see list_view.py).
Groups show in the order given; which ones are open is kept when the tree is refreshed.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from nicegui import ui

from .list_view import Action, Row, render_row, thumbnail

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
                 empty_text: str = '') -> None:
        self._groups, self._key, self._row, self._on_open = groups, key, row, on_open
        self._actions = list(actions)
        self._focus = focus
        self._open: set[str] = {focus} if focus else set()
        self._empty_title, self._empty_text = empty_title, empty_text
        self._render()

    def refresh(self) -> None:
        self._render.refresh()

    def _set_open(self, key: str, is_open: bool) -> None:
        if is_open:
            self._open.add(key)
        else:
            self._open.discard(key)

    @ui.refreshable_method
    def _render(self) -> None:
        groups = list(self._groups())
        if not groups:
            with ui.column().classes('panel gap-1'):
                ui.label(self._empty_title).classes('panel-title')
                if self._empty_text:
                    ui.label(self._empty_text).classes('muted')
            return

        focused = None
        with ui.column().classes('w-full gap-3'):
            for group in groups:
                is_focus = group.key == self._focus
                expansion = ui.expansion(value=group.key in self._open) \
                    .classes('panel fold tree-group' + (' tree-focus' if is_focus else '')) \
                    .mark(f'group-{group.key}')
                expansion.on_value_change(lambda e, k=group.key: self._set_open(k, e.value))
                with expansion.add_slot('header'):
                    self._group_header(group.row)
                with expansion:
                    with ui.list().props('separator').classes('tree-items'):
                        for item in group.items:
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
            thumbnail(row.title, row.image)
            with ui.column().classes('gap-0 min-w-0'):
                ui.label(row.title).classes('tree-group-title')
                if row.subtitle:
                    ui.label(row.subtitle).classes('text-sm muted')
                if row.warning:
                    ui.label(row.warning).classes('text-sm text-negative')
