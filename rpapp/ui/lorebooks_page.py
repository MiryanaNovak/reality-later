"""The Lore Books section: the list of books (shared code), a book's page, an entry's page.

    /lorebooks                          list of books
    /lorebooks/<book id>                the book: name and its list of entries
    /lorebooks/<book id>/entries/<uid>  one entry, all its settings
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import TYPE_CHECKING

from nicegui import ui

from ..lorebooks import (
    AT_DEPTH, OUTLET, POSITION_SHORT, POSITIONS, ROLES, SELECTIVE_LOGIC, STRATEGIES, TRIGGERS, LoreBook, LoreEntry,
    new_lore_book,
)
from ..storage import JsonStore
from .collection_page import Collection, CollectionPage
from .form import Form
from .list_view import Action, ListView, Row, plural, slug
from .widgets import confirm, folding_panel, not_found, page_header, panel

if TYPE_CHECKING:
    from .profile_page import ProfileSection

STRATEGY_ICONS = {  # SillyTavern's colors: blue circle (always), green circle (keywords), chain link (meaning)
    'both': ('join_inner', 'teal-6'),
    'keywords': ('circle', 'green-6'),
    'meaning': ('link', 'purple-5'),
    'constant': ('circle', 'blue-6'),
}


def _entry_row(entry: LoreEntry) -> Row:
    keys = ', '.join(entry.key)
    preview = ' '.join(entry.content.split())[:160]
    subtitle = (keys or preview) if entry.comment.strip() else preview
    position = f'@D{entry.depth}' if entry.position == AT_DEPTH else POSITION_SHORT.get(entry.position, '?')
    if entry.disable:
        icon, color, tooltip = 'do_not_disturb_on', 'grey-6', 'Disabled'
    else:
        (icon, color), tooltip = STRATEGY_ICONS[entry.strategy], STRATEGIES[entry.strategy]
    return Row(title=entry.title, subtitle=subtitle, icon=icon, icon_color=color, icon_tooltip=tooltip,
               side=f'{position}, order {entry.order:g}', dimmed=entry.disable)


def _join(parts: list[str]) -> str:
    """['a', 'b', 'c'] -> 'a, b and c'"""
    return ' and '.join(parts) if len(parts) < 3 else ', '.join(parts[:-1]) + ' and ' + parts[-1]


class LoreBooksSection:
    def __init__(self, store: JsonStore[LoreBook], users: Sequence[ProfileSection] = ()) -> None:
        """
        :param users: the sections whose items can link to lore books (characters, personas),
            to show where each book is used
        """
        self.users = list(users)
        self._usage: list[Counter[str]] = []  # per user section, counted while listing the books
        self.collection = Collection(
            title='Lore books', noun='lore book', path='/lorebooks', store=store,
            row=self._book_row,
            items=self._list_books,
            delete_warning=self._delete_warning,
            create=lambda name, kind: new_lore_book(name or 'New lore book'),
            export=lambda book: book.to_sillytavern(),
            empty_text='A lore book holds facts about your world. Each entry is added to the prompt '
                       'when its keywords come up in the chat. Create one, or import a SillyTavern lore book.',
            name_placeholder='e.g. Eldoria',
        )
        self.pages = CollectionPage(self.collection)
        self.store = store

    def list_page(self) -> None:
        self.pages.list_page()

    # -- where books are used ----------------------------------------------------------

    def _list_books(self) -> list[LoreBook]:
        books = self.store.list()
        self._usage = [user.store.lorebook_usage() for user in self.users]  # one pass, not one per book
        return books

    def _book_row(self, book: LoreBook) -> Row:
        counts = [(usage[book.id], user.noun) for usage, user in zip(self._usage, self.users)]
        return Row(title=book.name, subtitle=plural(len(book.entries), 'entry'), icon='auto_stories',
                   note=self._usage_text(counts))

    def _usage_text(self, counts: list[tuple[int, str]]) -> str:
        if not counts:
            return ''
        if not any(n for n, _ in counts):
            return f'Not used by any {" or ".join(noun for _, noun in counts)}'
        return 'Used by ' + _join([plural(n, noun) for n, noun in counts])

    def _delete_warning(self, book: LoreBook) -> str:
        counts = [(len(user.store.using_lorebook(book.id)), user.noun) for user in self.users]
        if not any(n for n, _ in counts):
            return ''
        return f'It is used by {_join([plural(n, noun) for n, noun in counts])}. They will show it as missing.'

    def _load(self, book_id: str) -> LoreBook | None:
        try:
            return self.store.get(book_id)
        except ValueError:
            return None

    # -- a book ------------------------------------------------------------------

    def book_page(self, book_id: str) -> None:
        book = self._load(book_id)
        if book is None:
            not_found('Lore book', self.collection.path)
            return
        book_path = self.collection.item_path(book)

        def changed() -> None:
            self.store.save_soon(book)
            title.text = book.name or 'Untitled'
            count.text = plural(len(book.entries), 'entry')

        def open_entry(entry: LoreEntry) -> None:
            ui.navigate.to(f'{book_path}/entries/{entry.uid}')

        def add_entry() -> None:
            entry = book.add_entry()
            self.store.save(book)
            open_entry(entry)

        def toggle(entry: LoreEntry) -> None:
            entry.disable = not entry.disable
            changed()
            view.refresh()

        def duplicate(entry: LoreEntry) -> None:
            copy = book.duplicate_entry(entry)
            changed()
            view.refresh()
            ui.notify(f'Duplicated as "{copy.title}"', type='positive')

        async def delete(entry: LoreEntry) -> None:
            if await confirm(f'Delete "{entry.title}"?', 'This can\'t be undone.', 'Delete'):
                book.remove_entry(entry.uid)
                changed()
                view.refresh()
                ui.notify(f'Deleted "{entry.title}"')

        title = self.pages.item_header(book, 'Lore book, in SillyTavern\'s format. Changes save automatically.')
        with panel('Book'):
            Form(on_change=changed).text(book, 'name', 'Name',
                                         hint='Also the file name when you export, which SillyTavern uses as its name')

        for user in self.users:
            items = user.store.using_lorebook(book.id)
            title_text = f'{user.collection.title} using this book'
            with folding_panel(title_text, plural(len(items), user.noun) if items else 'None'):
                with ui.column().classes('w-full gap-2').mark(f'used-by-{slug(user.collection.title)}'):
                    if items:
                        user.links(items)
                    else:
                        ui.label(f'No {user.noun} uses this lore book. You can add it from a {user.noun}\'s page.') \
                            .classes('muted text-sm')

        with ui.row().classes('w-full items-end justify-between gap-3 mt-2'):
            with ui.column().classes('gap-0'):
                ui.label('Entries').classes('section-title')
                count = ui.label(plural(len(book.entries), 'entry')).classes('muted text-sm')
            ui.button('New entry', icon='add', on_click=add_entry)

        view = ListView(
            items=book.sorted_entries,
            key=lambda entry: str(entry.uid),
            row=_entry_row,
            on_open=open_entry,
            actions=[
                Action(lambda entry: 'Enable' if entry.disable else 'Disable', toggle, marker='toggle'),
                Action('Duplicate', duplicate),
                Action('Delete', delete, danger=True),
            ],
            search=lambda entry: ' '.join([entry.comment, *entry.key, *entry.keysecondary, entry.content]),
            empty_title='No entries yet',
            empty_text='Each entry is a piece of lore, with the keywords that bring it into the chat.',
        )

    # -- an entry ----------------------------------------------------------------

    def entry_page(self, book_id: str, uid: str) -> None:
        book = self._load(book_id)
        entry = book.entry(uid) if book else None
        if book is None or entry is None:
            not_found('Entry', self.collection.item_path(book) if book else self.collection.path)
            return
        book_path = self.collection.item_path(book)

        def changed() -> None:
            self.store.save_soon(book)
            title.text = entry.title

        def duplicate() -> None:
            copy = book.duplicate_entry(entry)
            self.store.save(book)
            ui.navigate.to(f'{book_path}/entries/{copy.uid}')
            ui.notify(f'Duplicated as "{copy.title}"', type='positive')

        async def delete() -> None:
            if await confirm(f'Delete "{entry.title}"?', 'This can\'t be undone.', 'Delete'):
                book.remove_entry(entry.uid)
                self.store.save(book)
                ui.navigate.to(book_path)
                ui.notify(f'Deleted "{entry.title}"')

        title = page_header(entry.title, f'Entry in {book.name}. Changes save automatically.', back=book_path,
                            menu=[('Duplicate', duplicate, False), ('Delete', delete, True)])
        entry_editor(entry, Form(on_change=changed))


def entry_editor(entry: LoreEntry, form: Form) -> None:
    """All the settings of one entry."""
    with panel('Entry'):
        form.switch(entry, 'disable', 'Enabled', invert=True)
        form.text(entry, 'comment', 'Title', hint='For you only, never sent to the model')
        form.choice('When to include it', STRATEGIES, get=lambda: entry.strategy, set=entry.set_strategy,
                    hint='By meaning: when vector search is on for lore books (Settings › Vector search). '
                         'Without it, entries come in by their keywords only.').mark('entry-strategy')

    with panel('Keywords', 'The entry is added when one of these appears in the recent messages. '
                           'End one with * to allow any ending ("duel*" finds "dueling"). '
                           'Write /like this/ for a regular expression.'):
        form.chips(entry, 'key', 'Keywords', hint='Press Enter after each')
        form.select(entry, 'selectiveLogic', 'Optional filter', SELECTIVE_LOGIC)
        form.chips(entry, 'keysecondary', 'Filter keywords', hint='Leave empty to use the keywords alone')

    with panel('Content', 'The text added to the prompt. Titles and keywords are not sent.'):
        form.long_text(entry, 'content', 'Content')

    with panel('Placement'):
        role: ui.select

        def set_position(value: int) -> None:
            entry.set_position(value)  # also sets the role: System at depth, none elsewhere
            if entry.role is not None:
                role.value = entry.role

        form.choice('Position in the prompt', POSITIONS, get=lambda: entry.position, set=set_position)
        with ui.element('div').classes('field-grid').bind_visibility_from(entry, 'position', lambda p: p == AT_DEPTH):
            form.number(entry, 'depth', 'Depth', integer=True, min=0, step=1, optional=False,
                        hint='0 is right after the last message')
            role = form.choice('Sent as', ROLES, get=lambda: 0 if entry.role is None else entry.role,
                               set=lambda value: setattr(entry, 'role', value))
        form.text(entry, 'outletName', 'Outlet name', hint='Used by the {{outlet::name}} macro') \
            .bind_visibility_from(entry, 'position', lambda p: p == OUTLET)
        with ui.element('div').classes('field-grid'):
            form.number(entry, 'order', 'Order', integer=True, step=1, optional=False,
                        hint='Higher is later in the prompt, with more weight')
            form.number(entry, 'probability', 'Trigger chance (%)', integer=True, min=0, max=100, step=5,
                        optional=False)
        form.switch(entry, 'useProbability', 'Use the trigger chance')

    with folding_panel('Matching', 'How and where keywords are looked for'):
        form.number(entry, 'scanDepth', 'Messages to scan', integer=True, min=0, max=1000,
                    placeholder='Global setting', hint='Empty uses the global setting')
        with ui.element('div').classes('field-grid'):
            form.tristate(entry, 'caseSensitive', 'Case-sensitive')
            form.tristate(entry, 'matchWholeWords', 'Match whole words')
        ui.label('Also look in').classes('text-sm muted mt-1')
        for attr, label in [('matchCharacterDescription', 'Character description (in this app: the scenario)'),
                            ('matchCharacterPersonality', 'Character personality (in this app: the scenario)'),
                            ('matchScenario', 'Scenario'),
                            ('matchPersonaDescription', 'Persona description'),
                            ('matchCharacterDepthPrompt', "Character's depth prompt"),
                            ('matchCreatorNotes', "Creator's notes")]:
            form.switch(entry, attr, label)

    with folding_panel('Inclusion group', 'When several entries of one group trigger, only one is used'):
        form.text(entry, 'group', 'Groups', hint='Separate several groups with commas')
        form.number(entry, 'groupWeight', 'Group weight', integer=True, min=0, step=10, optional=False,
                    hint='Higher is more likely to be picked')
        form.switch(entry, 'groupOverride', 'Prioritize: the highest order wins instead of a random pick')
        form.tristate(entry, 'useGroupScoring', 'Group scoring',
                      hint='Keep only the entries matching the most keywords')

    with folding_panel('Timing', 'Counted in messages. Empty or 0 means off.'):
        with ui.element('div').classes('field-grid'):
            form.number(entry, 'sticky', 'Sticky', integer=True, min=0, placeholder='Off',
                        hint='Stays active this long after triggering')
            form.number(entry, 'cooldown', 'Cooldown', integer=True, min=0, placeholder='Off',
                        hint="Can't trigger again this soon")
            form.number(entry, 'delay', 'Delay', integer=True, min=0, placeholder='Off',
                        hint='Needs at least this many messages')

    with folding_panel('Recursion and budget'):
        form.switch(entry, 'excludeRecursion', "Non-recursable: other entries can't trigger this one")
        form.switch(entry, 'preventRecursion', "Prevent further recursion: this one can't trigger others")
        _delay_until_recursion(entry, form)
        form.switch(entry, 'ignoreBudget', 'Ignore the token budget')

    with folding_panel('Filters and automation'):
        form.multi_select(entry, 'triggers', 'Only for these generation types', TRIGGERS,
                          hint='Empty means all')
        form.text(entry, 'automationId', 'Automation ID',
                  hint='Runs the Quick Reply script with this ID when triggered')
        if entry.characterFilter and (entry.characterFilter.names or entry.characterFilter.tags):
            ui.label('This entry has a character filter set in SillyTavern. It is kept as it is.') \
                .classes('text-sm muted')


def _delay_until_recursion(entry: LoreEntry, form: Form) -> None:
    """SillyTavern stores this as false/0 (off), true/1 (on) or a number (on, at that recursion level)."""
    def set_on(on: bool) -> None:
        entry.delayUntilRecursion = (entry.delayUntilRecursion or True) if on else False
        level.set_visibility(on)

    def get_level() -> int | None:
        value = entry.delayUntilRecursion
        return value if type(value) is int and value > 1 else None

    def set_level(value: float | None) -> None:
        entry.delayUntilRecursion = True if value is None or int(value) <= 1 else int(value)

    form.bind(ui.switch('Delay until recursion: only other entries can trigger it'),
              get=lambda: bool(entry.delayUntilRecursion), set=set_on)
    level = form.bind(ui.number('Recursion level', min=1, step=1, precision=0, placeholder='1')
                      .props('clearable'), get=get_level, set=set_level,
                      hint='Lower levels are tried first. Empty means 1')
    level.set_visibility(bool(entry.delayUntilRecursion))
