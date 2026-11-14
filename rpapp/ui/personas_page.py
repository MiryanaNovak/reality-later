"""The Personas section, built on the shared profile page (profile_page.py).

    /personas               the folders (persona_folders.py), each folding open onto its personas' cards,
                            then the personas in none; New folder, New, and a search through them all
    /personas/<persona id>  one persona: picture, identity, description, lore books; each part says
                            whether the AI gets it

Folders are only for keeping personas in order: the AI never knows of them. A folder's ⋮ menu renames or
deletes it (its personas stay, in no folder); a persona's puts it in a folder, or takes it out. The new
chat's persona picker (picker()) shows them the same way.
"""
from __future__ import annotations

import json
import re
import weakref
from collections.abc import Callable
from typing import Any

from nicegui import ui

from ..conversations import ConversationStore
from ..lorebooks import LoreBook
from ..persona_folders import NAME_MAX, FolderError, PersonaFolder, PersonaFolderStore
from ..personas import GENDERS, PRONOUNS, Persona, PersonaStore, new_persona
from ..storage import JsonStore
from .form import Form, error_text
from .list_view import Action, actions_menu, plural, render_card
from .profile_page import ProfileSection
from .widgets import ask_name, audience_legend, confirm, page_bar, panel

PRONOUN_LABELS = {key: label for key, (label, _) in PRONOUNS.items()}
# The folders open on the Personas page: kept while you go to a persona and back (for the browser tab), and
# in a cookie (for the device), as appearance.py keeps the look
OPEN_COOKIE = 'rp_persona_folders'
COOKIE_DAYS = 400
_opened: weakref.WeakKeyDictionary[Any, set[str]] = weakref.WeakKeyDictionary()


class PersonasSection(ProfileSection[Persona]):
    title_hint = 'Only for you: how the persona is listed in this app'
    name_hint = 'Sent to the AI: what it calls you in a chat. {{user}} stands for it.'
    lore_books_caption = ('Background the AI gets only when it matters: an entry is sent when one of its keywords '
                          'comes up in a chat where you use this persona (or every time, for entries set so).')
    lore_books_audience = 'ai'

    def __init__(self, store: PersonaStore, lore_books: JsonStore[LoreBook],
                 conversations: ConversationStore | None = None, *,
                 folders: PersonaFolderStore | None = None) -> None:
        super().__init__(
            store, lore_books, title='Personas', noun='persona', path='/personas', create=new_persona,
            title_placeholder='e.g. Mira, wandering healer',
            empty_text='A persona is who you are in a chat: a name, pronouns, a description telling the AI '
                       'who you are, and a picture.',
            conversations=conversations, conversation_field='persona',
        )
        self.folders = folders or PersonaFolderStore(store.folder.parent, exists=store.exists)

    def card_subtitle(self, persona: Persona) -> str:
        pronouns = PRONOUN_LABELS.get(persona.pronouns, persona.pronouns).lower() if persona.pronouns else ''
        return ', '.join(part for part in (persona.name, pronouns) if part)

    def intro(self, persona: Persona) -> None:
        audience_legend('Name, Gender, Pronouns, Description, Lore books', 'Title, Picture')

    def identity_fields(self, persona: Persona, form: Form) -> None:
        with ui.element('div').classes('field-grid'):
            form.choice('Gender', GENDERS, get=lambda: persona.gender,
                        set=lambda value: setattr(persona, 'gender', value or ''))
            form.choice('Pronouns', PRONOUN_LABELS, get=lambda: persona.pronouns,
                        set=lambda value: setattr(persona, 'pronouns', value or ''))
        ui.label('Sent to the AI: so that it writes about you the right way.') \
            .classes('text-xs muted -mt-2 px-3').mark('gender-pronouns-hint')

    def panels(self, persona: Persona, form: Form) -> None:
        with panel('Description', 'Who you are in the story: looks, background, personality, abilities. The AI reads '
                                  'it before every reply, so that it knows who it\'s talking to. Write {{user}} '
                                  'for your name and {{char}} for the character\'s.', audience='ai'):
            form.long_text(persona, 'description', 'Description', min_height='12rem',
                           placeholder='e.g. {{user}} is a wandering healer in her thirties, calm and stubborn, '
                                       'with a satchel of herbs and a scar across one hand.')

    # -- the page: folders, then the personas in none -----------------------------------------------

    def list_page(self) -> None:
        new_dialog = self.pages.new_item_dialog()

        def buttons() -> None:
            ui.button('New folder', icon='create_new_folder', on_click=lambda: self._new_folder(view.refresh)) \
                .props('outline aria-label="New folder"').mark('new-folder')
            ui.button('New', icon='add', on_click=lambda: new_dialog.open()).props('aria-label="New"').mark('new')

        page_bar('Personas', actions=buttons)
        with ui.column().classes('w-full gap-3'):
            search = ui.input(placeholder='Search').props('clearable outlined dense debounce=250') \
                .classes('w-full').mark('persona-search')
            with search.add_slot('prepend'):
                ui.icon('search')

            @ui.refreshable
            def view() -> None:
                self._view(search, view.refresh)

            search.on_value_change(view.refresh)
            view()

    def _view(self, search: ui.input, refresh: Callable[[], None]) -> None:
        opened = _open_folders()
        personas = self._list_items()
        folders = self.folders.list()
        by_id = {p.id: p for p in personas}
        folder_of = {persona_id: folder for folder in folders for persona_id in folder.personas if persona_id in by_id}
        query = (search.value or '').strip().lower()
        search.set_visibility(len(personas) >= 8 or bool(query))

        if query:  # (through them all, folders or not)
            found = [p for p in personas if query in f'{p.title} {p.name} {p.description}'.lower()]
            if not found:
                with ui.column().classes('panel gap-1'):
                    ui.label(f'Nothing matches “{query}”').classes('panel-title')
                return
            self._grid(found, folder_of, refresh, with_folder=True)
            return
        if not personas and not folders:
            with ui.column().classes('panel gap-1'):
                ui.label('No personas yet').classes('panel-title')
                ui.label(self.collection.empty_text).classes('muted')
            return

        for folder in folders:
            members = sorted((by_id[i] for i in folder.personas if i in by_id), key=lambda p: p.label.lower())
            expansion = ui.expansion(value=folder.id in opened).classes('panel fold tree-group persona-folder') \
                .mark(f'folder-{folder.id}')
            expansion.on_value_change(lambda e, f=folder.id: _set_open(f, e.value))
            with expansion.add_slot('header'):
                with ui.row().classes('w-full items-center no-wrap gap-3 min-w-0'):
                    with ui.element('div').classes('profile-thumb'):
                        ui.icon('folder', size='sm')
                    with ui.column().classes('gap-0 min-w-0 grow'):
                        ui.label(folder.name).classes('tree-group-title break-words')
                        ui.label(plural(len(members), 'persona') if members else 'Empty').classes('text-sm muted')
                    actions_menu(folder, [
                        Action('Rename', lambda f: self._rename_folder(f, refresh), marker='rename-folder'),
                        Action('Delete', lambda f: self._delete_folder(f, len(members), refresh), danger=True,
                               marker='delete-folder'),
                    ])
            with expansion:
                if members:
                    self._grid(members, folder_of, refresh)
                else:
                    ui.label('This folder is empty: put personas in it from their ⋮ menu.').classes('muted text-sm')

        unfiled = [p for p in personas if p.id not in folder_of]
        if unfiled:
            self._grid(unfiled, folder_of, refresh)

    def _grid(self, personas: list[Persona], folder_of: dict[str, PersonaFolder], refresh: Callable[[], None], *,
              with_folder: bool = False) -> None:
        with ui.element('div').classes('item-grid'):
            for persona in personas:
                folder = folder_of.get(persona.id)
                row = self.card(persona)
                if with_folder and folder is not None:
                    row.note = f'In {folder.name}'
                render_card(persona, row, key=persona.id, actions=self._persona_actions(folder, refresh),
                            on_open=lambda p: ui.navigate.to(self.collection.item_path(p)))

    def _persona_actions(self, folder: PersonaFolder | None, refresh: Callable[[], None]) -> list[Action[Persona]]:
        def duplicate(persona: Persona) -> None:
            copy = self.pages.duplicate(persona)
            if folder is not None:  # (the copy goes in the same folder)
                self._put(copy.id, folder.id)
            refresh()

        async def delete(persona: Persona) -> None:
            if await self.pages.delete(persona):
                self.folders.forget([persona.id])
                refresh()

        def take_out(persona: Persona) -> None:
            self._put(persona.id, None)
            ui.notify(f'"{persona.title}" is in no folder now')
            refresh()

        in_folder = [Action('Move to another folder', lambda p: self._choose_folder(p, folder, refresh),
                            marker='move-to-folder'),
                     Action('Remove from folder', take_out, marker='remove-from-folder')]
        return [
            Action('Edit', lambda p: ui.navigate.to(self.collection.item_path(p))),
            Action('Duplicate', duplicate),
            *(in_folder if folder is not None else
              [Action('Add to a folder', lambda p: self._choose_folder(p, None, refresh), marker='add-to-folder')]),
            Action('Delete', delete, danger=True),
        ]

    def _put(self, persona_id: str, folder_id: str | None) -> bool:
        try:
            self.folders.put(persona_id, folder_id)
        except FolderError as e:
            ui.notify(str(e), type='warning')
            return False
        if folder_id:
            _set_open(folder_id, True)  # (to see it there)
        return True

    # -- folders --------------------------------------------------------------------------------------

    async def _new_folder(self, refresh: Callable[[], None]) -> PersonaFolder | None:
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
        if isinstance(folder, PersonaFolder):
            _set_open(folder.id, True)
            refresh()
            return folder
        return None

    async def _rename_folder(self, folder: PersonaFolder, refresh: Callable[[], None]) -> None:
        name = await ask_name('Rename the folder', folder.name, max_length=NAME_MAX)
        if not name:
            return
        try:
            self.folders.rename(folder.id, name)
        except FolderError as e:
            ui.notify(error_text(e, location=False), type='warning')
            return
        refresh()

    async def _delete_folder(self, folder: PersonaFolder, members: int, refresh: Callable[[], None]) -> None:
        kept = (f'Its {plural(members, "persona")} {"stays" if members == 1 else "stay"}: in no folder any more.'
                if members else 'It\'s empty.')
        if not await confirm(f'Delete the folder "{folder.name}"?', kept, 'Delete'):
            return
        try:
            self.folders.delete(folder.id)
        except FolderError as e:
            ui.notify(str(e), type='warning')
        _set_open(folder.id, False)
        ui.notify(f'Deleted the folder "{folder.name}"')
        refresh()

    async def _choose_folder(self, persona: Persona, current: PersonaFolder | None,
                             refresh: Callable[[], None]) -> None:
        """Which folder to put the persona in (or a new one, named here)."""
        others = [f for f in self.folders.list() if current is None or f.id != current.id]
        with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes('dialog-card').mark('folder-picker'):
            ui.label(f'Put "{persona.title}" in a folder').classes('panel-title')
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
        if isinstance(chosen, str) and self._put(persona.id, chosen):
            folder = self.folders.get(chosen)
            ui.notify(f'"{persona.title}" is in {folder.name if folder else "the folder"} now')
            refresh()

    # -- for the new chat's persona picker -------------------------------------------------------------

    def picker(self, on_click: Callable[[Persona], Any]) -> None:
        """The personas to choose from: each folder (folded) with its own, then those in none."""
        personas = self.store.list()
        by_id = {p.id: p for p in personas}
        filed: set[str] = set()
        with ui.column().classes('w-full gap-2'):
            for folder in self.folders.list():
                members = sorted((by_id[i] for i in folder.personas if i in by_id), key=lambda p: p.label.lower())
                filed |= {p.id for p in members}
                if not members:
                    continue
                with ui.expansion(folder.name, icon='folder', caption=plural(len(members), 'persona')) \
                        .classes('w-full picker-folder').mark(f'picker-folder-{folder.id}'):
                    self.links(members, on_click=on_click)
            unfiled = [p for p in personas if p.id not in filed]
            if unfiled:
                self.links(unfiled, on_click=on_click)


def _open_folders() -> set[str]:
    """The folders open on this browser tab's Personas page (from the device's cookie, the first time)."""
    client = ui.context.client
    if client not in _opened:
        request = client.request
        value = request.cookies.get(OPEN_COOKIE, '') if request is not None else ''
        _opened[client] = {v for v in value.split('.') if re.fullmatch(r'[a-z0-9-]{1,64}', v)}
    return _opened[client]


def _set_open(folder_id: str, is_open: bool) -> None:
    opened = _open_folders()
    if (folder_id in opened) == is_open:
        return
    (opened.add if is_open else opened.discard)(folder_id)
    cookie = f'{OPEN_COOKIE}={".".join(sorted(opened))}; path=/; max-age={COOKIE_DAYS * 86400}; samesite=lax'
    ui.run_javascript(f'document.cookie = {json.dumps(cookie)}')


def _delete(dialog: ui.dialog) -> None:
    try:
        dialog.delete()
    except (ValueError, KeyError):  # (the browser tab was closed meanwhile)
        pass
