"""The standard page for a list of saved objects (connections, lore books, ...).

Describe the kind of object once with a Collection, and you get:
  - the list page (New, Import, and per item Edit, Duplicate, Export, Delete), as a list or a grid,
  - item_header() for the top of its editor page (back, title, ⋮ Duplicate / Export / Delete).

Import and Export can be switched off per collection.

    LORE_BOOKS = Collection(
        title='Lore books', noun='lore book', path='/lorebooks', store=store,
        row=lambda b: Row(title=b.name, subtitle=f'{len(b.entries)} entries', icon='menu_book'),
        create=lambda name, kind: new_lore_book(name),
    )
    CollectionPage(LORE_BOOKS).list_page()
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from nicegui import events, ui

from ..storage import JsonStore, StoredModel
from .form import error_text
from .list_view import Action, ListView, Row
from .widgets import MenuItems, confirm, file_picker, page_bar, page_header

T = TypeVar('T', bound=StoredModel)


@dataclass
class Collection(Generic[T]):
    title: str                                  # page title, e.g. 'Lore books'
    noun: str                                   # one item in lower case, e.g. 'lore book'
    path: str                                   # list page path; an item opens at <path>/<id>
    store: JsonStore[T]
    row: Callable[[T], Row]                     # how an item appears in the list
    create: Callable[[str, str | None], T]      # (label, kind) -> a new, unsaved item
    kinds: dict[str, str] | None = None         # if set, "New" asks for one of these types
    default_kind: str | None = None
    layout: Literal['list', 'grid'] = 'list'
    importable: bool = True
    # A custom import, instead of reading the store's own JSON: (file name, contents) -> (the saved
    # item, what to say about it). Raises ValueError with a message when the file can't be used.
    import_file: Callable[[str, bytes], tuple[T, str]] | None = None
    import_accept: str = '.json,application/json'
    import_max_size: int = 20_000_000
    exportable: bool = True
    export: Callable[[T], dict] | None = None   # exported JSON (saved as '<label>.json'); default: the saved file
    # A custom export instead: item -> (file contents, file name, media type)
    export_file: Callable[[T], tuple[bytes, str, str]] | None = None
    search: Callable[[T], str] | None = None    # text to search in; default: the label
    items: Callable[[], list[T]] | None = None  # what the list shows; default: store.list
    delete_warning: Callable[[T], str] | None = None  # an extra bold red line when deleting, '' for none
    empty_text: str = ''
    label_name: str = 'Name'                    # what "New" asks for, e.g. 'Name' or 'Title'
    name_placeholder: str = ''

    def item_path(self, item: T) -> str:
        return f'{self.path}/{item.id}'


class CollectionPage(Generic[T]):
    def __init__(self, collection: Collection[T]) -> None:
        self.c = collection

    # -- the list page -----------------------------------------------------------

    def list_page(self) -> None:
        c = self.c

        async def handle_upload(e: events.UploadEventArguments) -> None:
            try:
                if c.import_file is not None:
                    _, said = c.import_file(e.file.name, await e.file.read())
                else:
                    item = c.store.import_data(await e.file.json(), file_name=e.file.name)
                    said = f'Imported "{item.label}".'
            except (ValueError, UnicodeDecodeError) as err:
                ui.notify(f'Could not import {e.file.name}: {error_text(err)}', type='negative', multi_line=True)
                return
            view.refresh()
            ui.notify(said, type='positive', multi_line=True)

        def buttons() -> None:
            if c.importable:
                upload = file_picker(handle_upload, accept=c.import_accept, max_size=c.import_max_size)
                ui.button('Import', icon='upload_file').props('outline aria-label="Import"') \
                    .on('click', js_handler=upload.js_open).mark('import')
            ui.button('New', icon='add', on_click=lambda: new_dialog.open()).props('aria-label="New"').mark('new')

        page_bar(c.title, actions=buttons)

        view = ListView(
            items=c.items or c.store.list,
            key=lambda item: item.id,
            row=c.row,
            on_open=lambda item: ui.navigate.to(c.item_path(item)),
            actions=self.actions(lambda: view.refresh()),
            search=c.search or (lambda item: item.label),
            empty_title=f'No {c.noun}s yet',
            empty_text=c.empty_text,
            layout=c.layout,
        )

        new_dialog = self._new_dialog()

    def new_item_dialog(self) -> ui.dialog:
        """The New dialog (opened by the New button): for a page that draws its own list."""
        return self._new_dialog()

    def _new_dialog(self) -> ui.dialog:
        c = self.c
        with ui.dialog() as dialog, ui.card().classes('dialog-card').mark('new-dialog'):
            ui.label(f'New {c.noun}').classes('panel-title')
            kind = None
            if c.kinds:
                kind = ui.select(c.kinds, label='Type', value=c.default_kind or next(iter(c.kinds))).classes('w-full')
            name = ui.input(c.label_name, placeholder=c.name_placeholder or None).classes('w-full') \
                .on('keydown.enter', lambda: create())
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Create', on_click=lambda: create())

        def create() -> None:
            item = c.create((name.value or '').strip(), kind.value if kind else None)  # create() picks a default name
            c.store.save(item)
            dialog.close()
            ui.navigate.to(c.item_path(item))

        dialog.on_value_change(lambda e: name.set_value('') if e.value else None)
        return dialog

    # -- actions, shared by the list page and the editor page ------------------------

    def actions(self, refresh: Callable[[], Any]) -> list[Action[T]]:
        """The ⋮ menu of an item in a list or grid; `refresh` redraws it after a duplicate or a delete."""
        c = self.c
        return [
            Action('Edit', lambda item: ui.navigate.to(c.item_path(item))),
            Action('Duplicate', lambda item: self.duplicate(item, after=lambda copy: refresh())),
            *([Action('Export', self.export)] if c.exportable else []),
            Action('Delete', lambda item: self.delete(item, after=refresh), danger=True),
        ]

    def duplicate(self, item: T, *, after: Callable[[T], Any] | None = None) -> T:
        copy = self.c.store.duplicate(item)
        ui.notify(f'Duplicated as "{copy.label}"', type='positive')
        if after:
            after(copy)
        return copy

    def export(self, item: T) -> None:
        if self.c.export_file is not None:
            content, name, media_type = self.c.export_file(item)
            ui.download.content(content, name, media_type)
            return
        data = self.c.export(item) if self.c.export else item.model_dump(mode='json')
        ui.download.content((json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode('utf-8'),
                            _file_name(item.label), 'application/json')

    async def delete(self, item: T, *, after: Callable[[], Any] | None = None) -> bool:
        warning = self.c.delete_warning(item) if self.c.delete_warning else ''
        if not await confirm(f'Delete "{item.label}"?',
                             f'Its file is removed from your {self.c.noun}s. This can\'t be undone.', 'Delete',
                             warning=warning):
            return False
        self.c.store.delete(item.id)
        ui.notify(f'Deleted "{item.label}"')
        if after:
            after()
        return True

    # -- editor page header ----------------------------------------------------------

    def item_header(self, item: T, caption: str, *, menu: MenuItems = ()) -> ui.label:
        """Back arrow, title and ⋮ menu for the top of an item's editor page (`menu`: more of it, before
        Delete)."""
        c = self.c

        async def delete() -> None:
            await self.delete(item, after=lambda: ui.navigate.to(c.path))

        return page_header(item.label or 'Untitled', caption, back=c.path, menu=[
            ('Duplicate', lambda: self.duplicate(item, after=lambda copy: ui.navigate.to(c.item_path(copy))), False),
            *([('Export', lambda: self.export(item), False)] if c.exportable else []),
            *menu,
            ('Delete', delete, True),
        ])


def _file_name(name: str, extension: str = 'json') -> str:
    return f'{safe_file_name(name)}.{extension}'


def safe_file_name(name: str) -> str:
    """A name usable as a file name on every system ('export' if nothing is left)."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', name)
    return re.sub(r'\s+', ' ', cleaned).strip(' .') or 'export'
