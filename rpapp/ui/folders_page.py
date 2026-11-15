"""The list page of personas or characters in folders (rpapp/folders.py): only for keeping them in order, the
AI never knows of them.

    FolderPage(section).build()   # section: a ProfileSection with its `folders` (profile_page.py)

  - the folders, by name, each folding open onto its cards, then the cards in no folder;
  - New folder (beside Import and New) asks for its name; a folder's ⋮ menu renames or deletes it (what it
    holds stays, in no folder);
  - a card's ⋮ menu puts it in a folder (or one made there), moves it to another, or takes it out; a copy
    goes in the same folder;
  - the search goes through them all, saying which folder each one found is in.

The folders open are kept while you go to an item and back (for the browser tab), and in a cookie (for the
device: opened.py). folder_links() is the same, folded, for choosing one (the new chat's persona picker).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ..folders import NAME_MAX, Folder, FolderError
from ..profiles import P
from .form import error_text
from .list_view import Action, actions_menu, plural, render_card
from .opened import Opened
from .widgets import ask_name, confirm, page_bar

if TYPE_CHECKING:
    from .profile_page import ProfileSection


class FolderPage:
    def __init__(self, section: ProfileSection[P]) -> None:
        assert section.folders is not None
        self.section = section
        self.folders = section.folders
        self.noun = section.noun
        self.opened = Opened(f'rp_{section.noun}_folders')

    def build(self) -> None:
        pages, collection = self.section.pages, self.section.collection
        new_dialog = pages.new_item_dialog()

        def new_folder_button() -> None:
            ui.button('New folder', icon='create_new_folder', on_click=lambda: self.new_folder(view.refresh)) \
                .props('outline aria-label="New folder"').mark('new-folder')

        page_bar(collection.title, actions=lambda: pages.buttons(lambda: view.refresh(), new_dialog,
                                                                 before_new=new_folder_button))
        with ui.column().classes('w-full gap-3'):
            search = ui.input(placeholder='Search').props('clearable outlined dense debounce=250') \
                .classes('w-full').mark(f'{self.noun}-search')
            with search.add_slot('prepend'):
                ui.icon('search')

            @ui.refreshable
            def view() -> None:
                self._view(search, view.refresh)

            search.on_value_change(view.refresh)
            view()

    # -- the folders and the cards ----------------------------------------------------------------------

    def _view(self, search: ui.input, refresh: Callable[[], None]) -> None:
        opened = self._open()
        items = self.section._list_items()
        folders = self.folders.list()
        known = {item.id for item in items}
        folder_of = {member: folder for folder in folders for member in folder.members if member in known}
        query = (search.value or '').strip().lower()
        search.set_visibility(len(items) >= 8 or bool(query))

        if query:  # (through them all, folders or not)
            text = self.section.collection.search or (lambda item: item.label)
            found = [item for item in items if query in text(item).lower()]
            if not found:
                with ui.column().classes('panel gap-1'):
                    ui.label(f'Nothing matches “{query}”').classes('panel-title')
                return
            self._grid(found, folder_of, refresh, with_folder=True)
            return
        if not items and not folders:
            with ui.column().classes('panel gap-1'):
                ui.label(f'No {self.noun}s yet').classes('panel-title')
                ui.label(self.section.collection.empty_text).classes('muted')
            return

        for folder in folders:
            members = [item for item in items if folder_of.get(item.id) is folder]
            expansion = ui.expansion(value=folder.id in opened).classes('panel fold tree-group item-folder') \
                .mark(f'folder-{folder.id}')
            expansion.on_value_change(lambda e, f=folder.id: self._set_open(f, e.value))
            with expansion.add_slot('header'):
                with ui.row().classes('w-full items-center no-wrap gap-3 min-w-0'):
                    with ui.element('div').classes('profile-thumb'):
                        ui.icon('folder', size='sm')
                    with ui.column().classes('gap-0 min-w-0 grow'):
                        ui.label(folder.name).classes('tree-group-title break-words')
                        ui.label(plural(len(members), self.noun) if members else 'Empty').classes('text-sm muted')
                    actions_menu(folder, [
                        Action('Rename', lambda f: self.rename_folder(f, refresh), marker='rename-folder'),
                        Action('Delete', lambda f, n=len(members): self.delete_folder(f, n, refresh), danger=True,
                               marker='delete-folder'),
                    ])
            with expansion:
                if members:
                    self._grid(members, folder_of, refresh)
                else:
                    ui.label(f'This folder is empty: put {self.noun}s in it from their ⋮ menu.') \
                        .classes('muted text-sm')

        unfiled = [item for item in items if item.id not in folder_of]
        if unfiled:
            self._grid(unfiled, folder_of, refresh)

    def _grid(self, items: list, folder_of: dict[str, Folder], refresh: Callable[[], None], *,
              with_folder: bool = False) -> None:
        section = self.section
        with ui.element('div').classes('item-grid'):
            for item in items:
                folder = folder_of.get(item.id)
                row = section.card(item)
                if with_folder and folder is not None:
                    row.note = f'In {folder.name}'
                render_card(item, row, key=item.id, actions=self._actions(folder, refresh),
                            on_open=lambda i: ui.navigate.to(section.collection.item_path(i)))

    def _actions(self, folder: Folder | None, refresh: Callable[[], None]) -> list[Action]:
        """The card's ⋮ menu: the section's (Edit, Duplicate, Export...), with the folder's before Delete."""
        def take_out(item: Any) -> None:
            self._put(item.id, None)
            ui.notify(f'"{item.label}" is in no folder now')
            refresh()

        if folder is not None:
            extra = [Action('Move to another folder', lambda i: self.choose_folder(i, folder, refresh),
                            marker='move-to-folder'),
                     Action('Remove from folder', take_out, marker='remove-from-folder')]
        else:
            extra = [Action('Add to a folder', lambda i: self.choose_folder(i, None, refresh), marker='add-to-folder')]
        return self.section.pages.actions(
            refresh, extra=extra,
            duplicated=lambda item, copy: self._put(copy.id, folder.id) if folder is not None else None,
            deleted=lambda item: self.folders.forget([item.id]))

    def _put(self, member_id: str, folder_id: str | None) -> bool:
        try:
            self.folders.put(member_id, folder_id)
        except FolderError as e:
            ui.notify(str(e), type='warning')
            return False
        if folder_id:
            self._set_open(folder_id, True)  # (to see it there)
        return True

    # -- folders ------------------------------------------------------------------------------------------

    async def new_folder(self, refresh: Callable[[], None]) -> Folder | None:
        """Ask for the new folder's name, then make it (open, to fill)."""
        with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes('dialog-card').mark('folder-dialog'):
            ui.label('New folder').classes('panel-title')
            name = ui.input('Name', placeholder='e.g. Fantasy').classes('w-full') \
                .props(f'autofocus maxlength={NAME_MAX}').mark('folder-name').on('keydown.enter', lambda: create())
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancel', on_click=lambda: dialog.submit(None)).props('flat')
                ui.button('Create', on_click=lambda: create()).mark('folder-create')

        def create() -> None:
            try:
                dialog.submit(self.folders.create(name.value or ''))
            except FolderError as e:
                name.error = str(e)

        name.on_value_change(lambda: setattr(name, 'error', None))
        folder = await dialog
        _delete(dialog)
        if isinstance(folder, Folder):
            self._set_open(folder.id, True)
            refresh()
            return folder
        return None

    async def rename_folder(self, folder: Folder, refresh: Callable[[], None]) -> None:
        name = await ask_name('Rename the folder', folder.name, max_length=NAME_MAX)
        if not name:
            return
        try:
            self.folders.rename(folder.id, name)
        except FolderError as e:
            ui.notify(error_text(e, location=False), type='warning')
            return
        refresh()

    async def delete_folder(self, folder: Folder, members: int, refresh: Callable[[], None]) -> None:
        kept = (f'Its {plural(members, self.noun)} {"stays" if members == 1 else "stay"}: in no folder any more.'
                if members else 'It\'s empty.')
        if not await confirm(f'Delete the folder "{folder.name}"?', kept, 'Delete'):
            return
        try:
            self.folders.delete(folder.id)
        except FolderError as e:
            ui.notify(str(e), type='warning')
        self._set_open(folder.id, False)
        ui.notify(f'Deleted the folder "{folder.name}"')
        refresh()

    async def choose_folder(self, item: Any, current: Folder | None, refresh: Callable[[], None]) -> None:
        """Which folder to put the item in (or a new one, named here)."""
        others = [f for f in self.folders.list() if current is None or f.id != current.id]
        with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes('dialog-card').mark('folder-picker'):
            ui.label(f'Put "{item.label}" in a folder').classes('panel-title')
            if others:
                with ui.list().props('separator bordered').classes('w-full rounded-borders browse-list'):
                    for folder in others:
                        with ui.item(on_click=lambda f=folder: dialog.submit(f.id)).mark(f'pick-{folder.id}'):
                            with ui.item_section().props('avatar'):
                                ui.icon('folder', color='primary')
                            ui.item_section(folder.name)
            else:
                ui.label(f'There\'s no {"other " if current else ""}folder yet: make one.').classes('muted')
            with ui.row().classes('w-full items-start no-wrap gap-2'):
                name = ui.input('New folder', placeholder='Its name').classes('grow') \
                    .props(f'dense outlined maxlength={NAME_MAX}').mark('picker-folder-name') \
                    .on('keydown.enter', lambda: create())
                ui.button('Create', on_click=lambda: create()).props('outline').classes('mt-1').mark('picker-create')
            with ui.row().classes('w-full justify-end'):
                ui.button('Cancel', on_click=lambda: dialog.submit(None)).props('flat')

        def create() -> None:
            try:
                dialog.submit(self.folders.create(name.value or '').id)
            except FolderError as e:
                name.error = str(e)

        name.on_value_change(lambda: setattr(name, 'error', None))
        chosen = await dialog
        _delete(dialog)
        if isinstance(chosen, str) and self._put(item.id, chosen):
            folder = self.folders.get(chosen)
            ui.notify(f'"{item.label}" is in {folder.name if folder else "the folder"} now')
            refresh()

    # -- which folders are open ---------------------------------------------------------------------------

    def _open(self) -> set[str]:
        """The folders open on this browser tab's page (from the device's cookie, the first time)."""
        return self.opened.keys()

    def _set_open(self, folder_id: str, is_open: bool) -> None:
        self.opened.set(folder_id, is_open)


def folder_links(section: ProfileSection[P], on_click: Callable[[P], Any]) -> None:
    """The section's items to choose from: each folder (folded) with its own, then those in none."""
    items = section.store.list()
    folders = section.folders.list() if section.folders is not None else []
    known = {item.id for item in items}
    filed: set[str] = set()
    with ui.column().classes('w-full gap-2'):
        for folder in folders:
            members = [item for item in items if item.id in folder.members and item.id in known]
            filed |= {item.id for item in members}
            if not members:
                continue
            with ui.expansion(folder.name, icon='folder', caption=plural(len(members), section.noun)) \
                    .classes('w-full picker-folder').mark(f'picker-folder-{folder.id}'):
                section.links(members, on_click=on_click)
        unfiled = [item for item in items if item.id not in filed]
        if unfiled:
            section.links(unfiled, on_click=on_click)


def _delete(dialog: ui.dialog) -> None:
    try:
        dialog.delete()
    except (ValueError, KeyError):  # (the browser tab was closed meanwhile)
        pass
