"""Shared by the Personas and Characters sections: a grid of pictures, and an editor.

The editor always has:
  - Identity: the picture (framed: see frame_dialog), Title, Name, plus any fields the section adds,
  - the section's own panels,
  - Lore books: add, remove, open; missing books shown in red, with Relink when a same-named book exists.

A section subclasses ProfileSection and overrides what it needs:

    class CharactersSection(ProfileSection[Character]):
        def card_subtitle(self, character): ...      # the line under the title in the grid
        def card(self, character): ...               # or the whole card (start from super().card())
        def prepare_cards(self): ...                 # runs before the grid is drawn (to count things once)
        def identity_fields(self, character, form): ...  # extra fields next to the picture
        def panels(self, character, form): ...       # its own panels (description, scenario, ...)

Deleting a profile used by conversations warns about them in the confirm dialog.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, Literal

from nicegui import events, ui

from ..lorebooks import LoreBook
from ..profiles import Frame, P, ProfileStore, default_frame
from ..storage import JsonStore
from .collection_page import Collection, CollectionPage
from .form import Form
from .frame_editor import FrameEditor
from .list_view import Row, initials, plural
from .media import serve_images
from .widgets import Audience, MenuItems, file_picker, not_found, panel

if TYPE_CHECKING:
    from ..conversations import ConversationStore


class ProfileSection(Generic[P]):
    # Texts a subclass can change
    title_hint = 'Only shown in this app'
    name_hint = 'Used in the chat'
    lore_books_caption = 'Their entries can come into play in chats.'
    lore_books_audience: Audience | None = None  # tag the Lore books panel "Sent to the AI"

    def __init__(self, store: ProfileStore[P], lore_books: JsonStore[LoreBook], *,
                 title: str, noun: str, path: str, create: Callable[[str], P],
                 empty_text: str, title_placeholder: str = '',
                 conversations: ConversationStore | None = None,
                 conversation_field: Literal['character', 'persona'] = 'character',
                 import_file: Callable[[str, bytes], tuple[P, str]] | None = None, import_accept: str = '',
                 export_file: Callable[[P], tuple[bytes, str, str]] | None = None) -> None:
        """
        :param title: page title, e.g. 'Characters'
        :param noun: one item in lower case, e.g. 'character'
        :param path: where the grid lives, e.g. '/characters'; an item opens at <path>/<id>
        :param create: makes a new, unsaved item from its title
        :param conversations: where conversations are, to warn before deleting a profile they use
        :param conversation_field: which side of a conversation these profiles are
        :param import_file: reads a file into a saved item (see Collection.import_file); None: no Import button
        :param import_accept: the files the Import button offers, e.g. '.png,.json'
        :param export_file: writes an item to a file (see Collection.export_file); None: no Export
        """
        self.store = store
        self.lore_books = lore_books
        self.noun = noun
        self.conversations = conversations
        self.conversation_field = conversation_field
        self.image_url = serve_images(path.strip('/'), store.images)
        self.collection = Collection(
            title=title, noun=noun, path=path, store=store,
            row=self.card,
            items=self._list_items,
            delete_warning=self._delete_warning,
            create=lambda label, kind: create(label or f'New {noun}'),
            layout='grid', importable=import_file is not None, exportable=export_file is not None,
            export_file=export_file,
            import_file=import_file, import_accept=import_accept or '.json,application/json',
            import_max_size=100_000_000,
            label_name='Title', name_placeholder=title_placeholder,
            search=lambda item: f'{item.title} {item.name} {item.description}',
            empty_text=empty_text,
        )
        self.pages = CollectionPage(self.collection)

    # -- what subclasses override ------------------------------------------------------

    def card_subtitle(self, item: P) -> str:
        return item.name

    def prepare_cards(self) -> None:
        """Runs once before the grid is drawn: count things here rather than once per card."""

    def item_menu(self, item: P) -> MenuItems:
        """More of the editor page's ⋮ menu (before Delete)."""
        return ()

    def intro(self, item: P) -> None:
        """Under the page's title, before the panels: say what the parts are for."""

    def identity_fields(self, item: P, form: Form) -> None:
        """Extra fields under Title and Name."""

    def panels(self, item: P, form: Form) -> None:
        """The section's own panels, between Identity and Lore books."""

    # -- pages -----------------------------------------------------------------------

    def list_page(self) -> None:
        self.pages.list_page()

    def _list_items(self) -> list[P]:
        items = self.store.list()
        self.prepare_cards()
        return items

    def _delete_warning(self, item: P) -> str:
        if self.conversations is None:
            return ''
        count = len(self.conversations.using(self.conversation_field, item.id))
        if not count:
            return ''
        return f'It is used by {plural(count, "conversation")}. They will be kept, with this {self.noun} shown as missing.'

    def card(self, item: P) -> Row:
        missing = item.missing_lorebooks(self.lore_books.exists)
        return Row(
            title=item.title,
            subtitle=self.card_subtitle(item),
            image=self.image_url(item.image),
            warning=f'{plural(len(missing), "lore book")} missing' if missing else '',
        )

    def edit_page(self, item_id: str) -> None:
        try:
            item = self.store.get(item_id)
        except ValueError:
            item = None
        if item is None:
            not_found(self.noun.capitalize(), self.collection.path)
            return

        def changed() -> None:
            self.store.save_soon(item)
            title.text = item.title or 'Untitled'
            if not item.image:
                picture.refresh()  # the initials follow the title while there's no picture

        title = self.pages.item_header(item, f'{self.noun.capitalize()}. Changes save automatically.',
                                       menu=self.item_menu(item))
        self.intro(item)
        form = Form(on_change=changed)

        with panel('Identity'):
            with ui.element('div').classes('profile-identity'):
                with ui.column().classes('items-center gap-2'):
                    picture = self._picture_editor(item)
                with ui.column().classes('gap-3 min-w-0'):
                    form.text(item, 'title', 'Title', hint=self.title_hint)
                    form.text(item, 'name', 'Name', hint=self.name_hint)
                    self.identity_fields(item, form)

        self.panels(item, form)

        with panel('Lore books', self.lore_books_caption, audience=self.lore_books_audience):
            self._lore_book_editor(item)

    # -- for other pages ---------------------------------------------------------------

    def links(self, items: list[P], on_click: Callable[[P], Any] | None = None) -> None:
        """A compact list of items with their picture. Clicking one opens its editor, or calls `on_click`."""
        def clicked(item: P) -> Any:
            return on_click(item) if on_click else ui.navigate.to(self.collection.item_path(item))

        with ui.list().props('separator bordered').classes('w-full rounded-borders'):
            for item in items:
                with ui.item(on_click=lambda i=item: clicked(i)).mark(f'link-{item.id}'):
                    with ui.item_section().props('avatar'):
                        with ui.element('div').classes('profile-thumb'):
                            if item.image:
                                ui.image(self.image_url(item.image)).props('ratio=1 fit=cover no-spinner') \
                                    .classes('w-full h-full')
                            else:
                                ui.label(initials(item.title))
                    with ui.item_section().classes('min-w-0'):
                        ui.item_label(item.title)
                        if item.name and item.name != item.title:
                            ui.item_label(item.name).props('caption')

    # -- shared parts of the editor ----------------------------------------------------

    def _picture_editor(self, item: P) -> ui.refreshable:
        async def handle_file(e: events.UploadEventArguments) -> None:
            try:
                self.store.set_picture(item, await e.file.read())
            except ValueError as err:
                ui.notify(str(err), type='negative')
                return
            self.store.save(item)  # right away, so the old picture's files can go
            picture.refresh()
            await reframe(new=True)

        async def reframe(new: bool = False) -> None:
            frame = await self.frame_dialog(item, new=new)
            if frame is not None:
                self.store.frame_picture(item, frame)
                self.store.save(item)
                picture.refresh()

        def remove() -> None:
            self.store.remove_picture(item)
            self.store.save(item)
            picture.refresh()

        picker = file_picker(handle_file, accept='image/*')

        @ui.refreshable
        def picture() -> None:
            with ui.element('div').classes('profile-picture'):
                if item.image:
                    ui.image(self.image_url(item.image)) \
                        .props(f'ratio=1 fit=cover alt="Picture of {item.title.replace(chr(34), "")}"') \
                        .classes('w-full h-full')
                else:
                    ui.label(initials(item.title)).classes('grid-card-initials')
            with ui.row().classes('gap-1 justify-center'):
                ui.button('Change picture' if item.image else 'Add picture', icon='image').props('outline dense') \
                    .classes('px-2').on('click', js_handler=picker.js_open)
                if item.image:
                    ui.button(icon='crop', on_click=lambda: reframe()) \
                        .props('flat round dense aria-label="Reframe the picture"').tooltip('Reframe') \
                        .mark('reframe-picture')
                    ui.button(icon='delete_outline', on_click=remove) \
                        .props('flat round dense aria-label="Remove picture"').tooltip('Remove picture') \
                        .mark('remove-picture')

        picture()
        return picture

    async def frame_dialog(self, item: P, *, new: bool = False) -> Frame | None:
        """Ask which square of the picture to show; None if the dialog is closed without saving."""
        full = item.image_full or item.image
        if not full or self.store.images.path(full) is None:
            return None
        frame = item.image_frame or default_frame(*self.store.images.size(full))
        with ui.context.client.layout, ui.dialog() as dialog, \
                ui.card().classes('dialog-card items-center').mark('frame-dialog'):
            ui.label('Frame the new picture' if new else 'Reframe the picture').classes('panel-title self-start')
            ui.label('Drag to move it; zoom with the slider, the mouse wheel or two fingers. The square is what '
                     'shows everywhere; the whole picture is kept.').classes('text-sm muted self-start')
            editor = FrameEditor(self.image_url(full), frame).mark('frame-editor')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Keep as it is' if new else 'Cancel', on_click=lambda: dialog.submit(None)) \
                    .props('flat').mark('frame-cancel')
                ui.button('Save', icon='check', on_click=lambda: dialog.submit(editor.frame)).mark('frame-save')
        result = await dialog
        try:
            dialog.delete()
        except (ValueError, KeyError):  # the browser tab was closed meanwhile
            pass
        return result

    def _lore_book_editor(self, item: P) -> None:
        def save() -> None:
            self.store.save_soon(item)
            books.refresh()

        def add(book: LoreBook) -> None:
            item.add_lorebook(book.id, book.name)
            save()

        def remove(book_id: str) -> None:
            item.remove_lorebook(book_id)
            save()

        def relink(old_id: str, book: LoreBook) -> None:
            item.replace_lorebook(old_id, book.id, book.name)
            save()
            ui.notify(f'"{book.name}" is linked again', type='positive')

        @ui.refreshable
        def books() -> None:
            available = {book.id: book for book in self.lore_books.list()}
            by_name = {book.name: book for book in available.values()}

            # Keep the remembered names up to date while the books exist.
            renamed = False
            for ref in item.lorebooks:
                book = available.get(ref.id)
                if book and book.name != ref.name:
                    ref.name, renamed = book.name, True
            if renamed:
                self.store.save_soon(item)

            if item.lorebooks:
                with ui.list().props('separator bordered').classes('w-full rounded-borders'):
                    for ref in item.lorebooks:
                        book = available.get(ref.id)
                        with ui.item().mark(f'lorebook-{ref.id}'):
                            with ui.item_section().props('avatar'):
                                if book:
                                    ui.icon('auto_stories', color='primary')
                                else:
                                    ui.icon('error_outline', color='negative')
                            with ui.item_section().classes('min-w-0'):
                                if book:
                                    ui.item_label(book.name)
                                    ui.item_label(plural(len(book.entries), 'entry')).props('caption')
                                else:
                                    ui.item_label(f'Lore book "{ref.name}" is missing').classes('text-negative')
                                    replacement = by_name.get(ref.name)
                                    ui.item_label(
                                        'A lore book with this name exists: relink to use it.' if replacement else
                                        'It was deleted, or its file was renamed. Remove it, or import it again.'
                                    ).props('caption')
                            with ui.item_section().props('side'):
                                with ui.row().classes('no-wrap gap-0 items-center'):
                                    if book:
                                        ui.button(icon='open_in_new',
                                                  on_click=lambda b=book: ui.navigate.to(f'/lorebooks/{b.id}')) \
                                            .props('flat round dense aria-label="Open"').tooltip('Open')
                                    elif replacement := by_name.get(ref.name):
                                        ui.button('Relink', on_click=lambda r=ref, b=replacement: relink(r.id, b)) \
                                            .props('flat dense')
                                    ui.button(icon='close', on_click=lambda r=ref: remove(r.id)) \
                                        .props('flat round dense aria-label="Remove"').tooltip('Remove') \
                                        .mark('remove-lorebook')
            else:
                ui.label('No lore books yet.').classes('muted')

            choices = {book.id: book.name for book in available.values()
                       if not any(ref.id == book.id for ref in item.lorebooks)}
            if choices:
                ui.select(choices, label='Add a lore book',
                          on_change=lambda e: add(available[e.value]) if e.value else None) \
                    .classes('w-full').props('outlined')
            elif not available:
                with ui.row().classes('items-center gap-1'):
                    ui.label('You have no lore books yet.').classes('text-sm muted')
                    ui.link('Create one', '/lorebooks').classes('text-sm')

        books()
