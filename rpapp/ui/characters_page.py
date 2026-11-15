"""The Characters section, built on the shared profile page (profile_page.py).

    /characters                 their folders (folders_page.py), each folding open onto its characters' cards,
                                then the characters in none. Each card is as high as the others, whatever
                                it holds (the golden ratio's: theme.py): its conversation count, its summary
                                cut to fit, a New chat button at the bottom (whose persona picker shows the
                                personas' folders);
                                Import reads a character card (PNG, JSON or CharX, V1 to V3: see cards.py),
                                Export (in each character's ⋮ menu) writes one as a PNG (V2)
    /characters/<character id>  one character: picture, title, name and tags; then, each folding away
                                (open or not as last left on this device), its summary, description,
                                scenario, first messages, example dialogues, lore books, and the
                                connection and prompt its chats use (chat_settings.py); each part says
                                whether the AI gets it. Its ⋮ menu: Publish to shared cards
                                (sharing_page.py), when sharing cards can run here

The summary is plain text, as much as a shared card's announcement holds; the description, Markdown with
basic HTML and pictures of its own (descriptions.py): written, or seen as it shows (Write, Preview). Too big
a card to be published (its pictures, mostly) is said at the top of the page.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import events, ui

from ..cards import card_data, card_png, card_png_size, megabytes, read_card, save_card
from ..characters import Character, CharacterStore, new_character
from ..conversations import ConversationStore
from ..descriptions import (MAX_SUMMARY_BYTES, inlining_growth, pictures_in, utf8_size, with_inlined_pictures,
                            with_picture_urls)
from ..folders import FolderStore
from ..lorebooks import LoreBook
from ..personas import Persona
from ..publishing.announcements import MAX_CARD_TAGS, MAX_FILE_BYTES
from ..safe_html import description_html
from ..storage import JsonStore
from .collection_page import safe_file_name
from .form import Form, keep_typed, merged_values, text_stats
from .list_view import Link, Row, initials, plural
from .chat_settings import ChatChoices, character_panel
from .folders_page import folder_links
from .profile_page import ProfileSection
from .widgets import Dialog, MenuItems, audience_legend, browser_languages, confirm, file_picker

if TYPE_CHECKING:
    from .sharing_page import SharingSection

log = logging.getLogger(__name__)

MACROS_HINT = 'Write {{char}} for the character\'s name and {{user}} for yours.'
DESCRIPTION_MARKDOWN = ['break-on-newline', 'fenced-code-blocks', 'tables', 'strike']
# Links in a description open in a new tab (the app stays where it is)
NEW_TAB = ('(e) => { const a = e.target.closest("a[href]"); if (a) { e.preventDefault(); '
           'window.open(a.href, "_blank", "noopener,noreferrer"); } }')
SUMMARY_TOO_LONG = (f'Too long: {MAX_SUMMARY_BYTES // 1024} KB at most, as shared cards hold (about '
                    f'{MAX_SUMMARY_BYTES // 1000},000 letters; fewer in scripts like Chinese or Japanese). Shorten '
                    f'it, or move the rest to the Description.')


class CharactersSection(ProfileSection[Character]):
    title_hint = 'Only for you: how the character is listed in this app'
    name_hint = 'Sent to the AI: the character\'s name in the chat. {{char}} stands for it.'
    lore_books_caption = ('Background the AI gets only when it matters: an entry is sent when one of its keywords '
                          'comes up in a chat with this character (or every time, for entries set so).')
    lore_books_short = 'Background the AI gets when it matters'
    lore_books_audience = 'ai'
    foldable = True
    fold_default = {'summary', 'description', 'scenario', 'first-messages'}

    def __init__(self, store: CharacterStore, lore_books: JsonStore[LoreBook],
                 conversations: ConversationStore, personas: ProfileSection[Persona], *,
                 sharing: SharingSection | None = None) -> None:
        super().__init__(
            store, lore_books, title='Characters', noun='character', path='/characters', create=new_character,
            title_placeholder='e.g. Seraphina, forest guardian',
            empty_text='A character is who you chat with: a name, a picture, a summary and a description for '
                       'you, a scenario telling the AI who they are and what the story is, and how the chat opens.',
            conversations=conversations, conversation_field='character',
            import_file=self.import_card, import_accept='.png,.json,.charx,image/png,application/json',
            export_file=self.export_card,
        )
        self.conversations: ConversationStore = conversations
        self.personas = personas
        self.sharing = sharing  # publishing them as shared cards (None, or its .sharing None: not here)
        self.chat_choices: ChatChoices | None = None  # the connections and prompts (build_root's; None: no panel)
        self.folders = FolderStore(store.folder.parent, 'characters', exists=store.exists)
        self._counts: Counter[str] = Counter()

    # -- importing a character card --------------------------------------------------------

    def import_card(self, file_name: str, content: bytes, *,
                    languages: Iterable[str] | None = None) -> tuple[Character, str]:
        """A SillyTavern-style character card (PNG, JSON or CharX; V1 to V3), saved as a new character.
        `languages`: the user's, best first (the browser's if not given)."""
        card = read_card(file_name, content, languages=browser_languages() if languages is None else list(languages))
        imported = save_card(card, self.store, self.lore_books)
        said = f'Imported "{imported.character.title}" ({card.version} card)'
        if imported.book is not None:
            said += (f', linked to your lore book "{imported.book.name}", which has the same entries'
                     if imported.book_reused else f', with its lore book "{imported.book.name}"')
        return imported.character, said + '.'

    def export_card(self, character: Character) -> tuple[bytes, str, str]:
        """The character as a V2 character card in a PNG, with their picture, lore books, and their description's
        pictures written into it (see cards.py)."""
        content = card_png(self._card(character), self._picture(character), initials=initials(character.title))
        return content, f'{safe_file_name(character.title)}.png', 'image/png'

    def card_size(self, character: Character) -> int:
        """The size of the card file Export (and publishing) makes, in bytes, without making it (its description's
        pictures aren't even read: their files' sizes are enough)."""
        def size(name: str) -> int | None:
            path = self.store.images.path(name)
            try:
                return path.stat().st_size if path is not None else None
            except OSError:
                return None
        return card_png_size(self._card(character, inlined=False), self._picture(character),
                             initials=initials(character.title),
                             growth=inlining_growth(character.description.strip(), size))

    def _card(self, character: Character, *, inlined: bool = True) -> dict:
        books = [book for ref in character.lorebooks if (book := self._lore_book(ref.id)) is not None]
        return card_data(character, books, pictures=(lambda text: with_inlined_pictures(text, self._picture_file))
                         if inlined else None)

    def _picture(self, character: Character) -> bytes | None:
        whole = character.image_full or character.image  # the whole picture, not the framed square
        return self._picture_file(whole) if whole else None

    def _picture_file(self, name: str) -> bytes | None:
        path = self.store.images.path(name)
        try:
            return path.read_bytes() if path is not None else None
        except OSError:
            return None

    # -- publishing it as a shared card -----------------------------------------------------

    def item_menu(self, character: Character) -> MenuItems:
        if self.sharing is None or self.sharing.sharing is None:
            return ()
        return [('Publish to shared cards', lambda: self.publish(character), False)]

    def import_shared(self, file_name: str, content: bytes, languages: Iterable[str]) -> tuple[str, str, str]:
        """A shared card downloaded (publishing/service.py's importer): added as a new character, like Import;
        its card's fingerprint then, to tell whether it's edited since."""
        character, said = self.import_card(file_name, content, languages=languages)
        return character.id, said, self.card_fingerprint(character)

    async def publish(self, character: Character) -> None:
        """The card file is the one Export makes (with the card's UUID in it); the character is saved with that
        UUID when it's first published."""
        await self.sharing.publish_dialog(character, lambda c: self.export_card(c)[0], self.store.save,
                                          fingerprint=self.card_fingerprint)

    def card_fingerprint(self, character: Character) -> str:
        """What its card holds, as a fingerprint: the card's data (its lore books in it) and its picture (by
        its file's name, made from its content). Kept when it's published (sharing_page.py), to tell whether
        it changed since: unlike the card's file, it doesn't change when the app's libraries draw or compress
        the PNG differently."""
        books = [book for ref in character.lorebooks if (book := self._lore_book(ref.id)) is not None]
        whole = character.image_full or character.image
        held = {'card': card_data(character, books), 'picture': whole or '',
                'initials': '' if whole else initials(character.title)}
        return hashlib.sha256(json.dumps(held, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

    def _lore_book(self, book_id: str) -> LoreBook | None:
        try:
            return self.lore_books.get(book_id)
        except ValueError:  # a damaged file: left out
            return None

    # -- the grid ------------------------------------------------------------------------

    def prepare_cards(self) -> None:
        self._counts = self.conversations.count_by('character')

    def card(self, character: Character) -> Row:
        row = super().card(character)
        count = self._counts.get(character.id, 0)
        row.subtitle = ''
        if count:
            row.link = Link(plural(count, 'conversation'), icon='forum', marker='conversations-link',
                            handler=lambda: ui.navigate.to(f'/conversations?character={character.id}'))
        else:
            row.note = 'No conversations yet'
        row.body = character.summary.strip()
        row.golden = True  # (all as high: the summary cut where the card ends)
        row.button = Link('New chat', icon='add_comment', marker='new-chat',
                          handler=lambda: self.new_chat(character))
        return row

    async def new_chat(self, character: Character) -> None:
        """Ask which persona to play, then start the conversation and open it."""
        personas = self.personas.store.list()
        with ui.context.client.layout, Dialog() as dialog, \
                ui.card().classes('dialog-card').mark('persona-picker'):
            ui.label(f'New chat with {character.title}').classes('panel-title')
            if personas:
                ui.label('Who will you be? Choose a persona.').classes('muted')
                with ui.column().classes('w-full browse-list'):
                    folder_links(self.personas, on_click=dialog.submit)  # (in their folders, if they have any)
            else:
                ui.label('You need a persona first: who you are in the chat.').classes('muted')
                ui.button('Create a persona', icon='face', on_click=lambda: dialog.submit('create'))
            with ui.row().classes('w-full justify-end'):
                ui.button('Cancel', on_click=lambda: dialog.submit(None)).props('flat')
        choice = await dialog
        try:
            dialog.delete()
        except (ValueError, KeyError):  # the browser tab was closed meanwhile
            return
        if choice == 'create':
            ui.navigate.to('/personas')
        elif isinstance(choice, Persona):
            conversation = self.conversations.start(character, choice)
            ui.navigate.to(f'/conversations/{conversation.id}')

    # -- the editor ----------------------------------------------------------------------

    def search_text(self, character: Character) -> str:
        return ' '.join((character.title, character.name, character.summary, *character.tags))

    def intro(self, character: Character) -> None:
        audience_legend('Name, Scenario, First messages, Example dialogues, Lore books',
                        'Title, Picture, Tags, Summary, Description')
        self._size_warning(character)
        if self.sharing is not None and self.sharing.sharing is not None:
            # From a shared card? Said once the catalog answered (withdrawn since, a newer version...)
            with ui.column().classes('w-full gap-2'):
                ui.timer(0.01, lambda: self.sharing.character_note(
                    character, fingerprint=self.card_fingerprint, export=lambda c: self.export_card(c)[0],
                    publish=lambda: self.publish(character)), once=True)

    def identity_fields(self, character: Character, form: Form) -> None:
        def put(values: list[str] | None) -> None:
            character.tags = merged_values([], values or [], ',')  # ("fantasy, elf": two, whichever event came first)
            if list(tags.value or []) != character.tags:  # as they're kept: lowercase, - for spaces
                tags.value = list(character.tags)

        tags = form.bind(keep_typed(ui.input_chips('Tags', new_value_mode='add-unique'), split=','),
                         get=lambda: list(character.tags), set=put,
                         hint=f'Only for you, and in its card: Enter after each, or commas between them. '
                              f'{MAX_CARD_TAGS} at most, e.g. fantasy, romance, slow-burn.')
        tags.props('clearable').mark('character-tags')

    def panels(self, character: Character, form: Form) -> None:
        with self.part('summary', 'Summary', short='A sentence or two, on its card', audience='you',
                       about='What the character is about, to help you pick them: shown on their card in the '
                             'list, and announced with it when you publish it. Plain text: formatting and '
                             'pictures go in the Description. The AI never sees it.'):
            self._summary_editor(character, form)

        with self.part('description', 'Description', short='As long as you like, with formatting and pictures',
                       audience='you',
                       about='All you want to say about the character, for people: their story, art, tips for '
                             'playing them, links. It goes in their card, as its creator\'s notes. The AI never '
                             'sees it.'):
            self._description_editor(character, form)

        with self.part('scenario', 'Scenario', short='Who they are, and what the story is', audience='ai',
                       about='Everything the AI knows about the character and the story: who {{char}} is (looks, '
                             'personality, way of speaking, past), and the situation the chat starts in (where, '
                             'when, what is going on). The AI reads it before every reply. '
                             f'{MACROS_HINT}'):
            form.long_text(character, 'scenario', 'Scenario', min_height='12rem',
                           placeholder='e.g. {{char}} is the guardian of the Eldwood, kind but wary of strangers. '
                                       'She speaks softly, in short sentences.\n\n{{user}} wakes up wounded in her '
                                       'glade, with no memory of how they got there.')

        with self.part('first-messages', 'First messages', short='How {{char}} opens a chat', audience='ai',
                       about='How {{char}} opens a chat: the first message, written as the character. It '
                             'becomes part of the chat, which the AI reads like the rest. With several, '
                             f'you can switch between them at the start of a chat. {MACROS_HINT}'):
            self._text_list(character, form, 'first_messages', item='Message', marker='message', minimum=1,
                            placeholder='*She looks up as you step into the glade.* ...',
                            add_label='Add a first message')

        with self.part('example-dialogues', 'Example dialogues', short='How {{char}} speaks and acts',
                       audience='ai',
                       about='Short exchanges showing how {{char}} speaks and acts, written as lines like '
                             '"{{user}}: …" and "{{char}}: …". The AI picks up their style, and knows they '
                             'aren\'t part of the story. By default, they\'re sent while the whole chat fits; as '
                             'it grows, they make room for it, the last ones first (Settings › Chat).'):
            self._text_list(character, form, 'example_dialogues', item='Example', marker='example', minimum=0,
                            placeholder='{{user}}: Who are you?\n{{char}}: *She tilts her head.* "The one who keeps '
                                        'this forest safe. And you are bleeding on my moss."',
                            add_label='Add an example dialogue', min_height='5rem')

    def last_panels(self, character: Character, form: Form) -> None:
        if self.chat_choices is None:
            return
        with self.part('model-and-prompt', 'Model and prompt', short='The connection and prompt its chats use',
                       about='The connection (the AI model) and the prompt chats with this character use. By '
                             'default, those of Settings › Chat. A chat can still have its own (its ⋮ menu › '
                             'Settings).'):
            character_panel(character, form, self.chat_choices)

    # -- the summary, the description --------------------------------------------------------------------

    def _summary_editor(self, character: Character, form: Form) -> None:
        def stats(text: str) -> str:
            return f'{len(text):,} characters · {utf8_size(text) / 1024:.1f} of {MAX_SUMMARY_BYTES // 1024} KB'

        def put(value: str | None) -> None:
            if utf8_size(value or '') > MAX_SUMMARY_BYTES:
                raise ValueError(SUMMARY_TOO_LONG)
            character.summary = value or ''

        field = form.bind(ui.textarea('Summary', placeholder='e.g. A centuries-old guardian who protects travellers '
                                                             'lost in her forest.'), get=lambda: character.summary,
                          set=put)
        field.props(f'autogrow maxlength={MAX_SUMMARY_BYTES} input-style="min-height: 4rem"').mark('summary')
        if utf8_size(character.summary) > MAX_SUMMARY_BYTES:  # (from before: said until it's shortened)
            field.error = SUMMARY_TOO_LONG
        count = ui.label(stats(character.summary)).classes('muted text-sm -mt-2').mark('summary-stats')
        field.on_value_change(lambda e: count.set_text(stats(e.value or '')))

    def _description_editor(self, character: Character, form: Form) -> None:
        caret: list[int | None] = [None]  # where the text area was left (a picture goes there)

        def pictures_line(text: str) -> str:
            found = len(pictures_in(text))
            return f'{len(text):,} characters' + (f', {plural(found, "picture")}' if found else '')

        async def add_picture(e: events.UploadEventArguments) -> None:
            try:
                name = await asyncio.to_thread(self.store.images.add, await e.file.read())
            except ValueError as err:
                ui.notify(str(err), type='negative')
                return
            alt = re.sub(r'[\[\]\r\n]+', ' ', Path(e.file.name or '').stem).strip()[:80]
            text = character.description
            at = caret[0] if caret[0] is not None and 0 <= caret[0] <= len(text) else len(text)
            editor.value, caret[0] = inserted(text, at, f'![{alt}](picture:{name})')
            show()
            ui.notify('Picture inserted.', type='positive')

        def show() -> None:
            writing = mode.value == 'write'
            for element in (editor, stats, help_):
                element.set_visibility(writing)
            preview.set_visibility(not writing and bool(character.description.strip()))
            empty.set_visibility(not writing and not character.description.strip())
            if not writing:
                preview.set_content(with_picture_urls(character.description, self._picture_url))

        picker = file_picker(add_picture, accept='image/*').mark('description-upload')
        with ui.row().classes('w-full items-center justify-between gap-2'):
            mode = ui.toggle({'write': 'Write', 'preview': 'Preview'},
                             value='preview' if character.description.strip() else 'write',
                             on_change=show).props('dense no-caps unelevated toggle-color=primary') \
                .mark('description-mode')
            ui.button('Insert a picture', icon='add_photo_alternate').props('outline dense').classes('px-2') \
                .on('click', js_handler=picker.js_open).mark('description-insert-picture')
        editor = form.textarea(character, 'description', 'Description',
                               placeholder='e.g. # Seraphina\n\nThe last guardian of the **Eldwood**...')
        editor.props('input-style="min-height: 12rem"').mark('description')
        editor.on('blur', lambda e: caret.__setitem__(0, e.args if isinstance(e.args, int) else None),
                  js_handler='(e) => emit(e && e.target && typeof e.target.selectionStart === "number" '
                             '? e.target.selectionStart : null)')
        stats = ui.label(pictures_line(character.description)).classes('muted text-sm -mt-2') \
            .mark('description-stats')
        editor.on_value_change(lambda e: stats.set_text(pictures_line(e.value or '')))
        help_ = ui.label('Markdown: **bold**, *italics*, # a heading, - a list, > a quote, [a link](https://…). '
                         'Basic HTML too: <b>, <i>, <u>, <s>, <br>, <sub>, <sup>, <img width="…">. Only its own '
                         'pictures show, those inserted here: pictures from websites never do.') \
            .classes('text-xs muted').mark('description-help')
        preview = ui.markdown('', extras=DESCRIPTION_MARKDOWN, sanitize=self._description_html) \
            .props('sanitize').classes('description-view w-full').mark('description-preview') \
            .on('click', js_handler=NEW_TAB)
        empty = ui.label('Nothing yet: write it in Write.').classes('muted').mark('description-empty')
        show()

    def _picture_url(self, name: str) -> str | None:
        return self.image_url(name) if self.store.images.path(name) is not None else None

    def _description_html(self, html: str) -> str:
        return description_html(html, pictures=self.image_path)

    def _size_warning(self, character: Character) -> None:
        """Said at the top while the card would be too big to be published: checked when the page opens, and
        again a moment after each change (in the background: its pictures are read for it)."""
        with ui.row().classes('size-warning no-wrap').mark('too-big') as box:
            ui.icon('error_outline').classes('size-warning-icon')
            text = ui.label('').classes('min-w-0')
        box.set_visibility(False)
        checked: dict[str, object] = {'as': None, 'busy': False}

        async def check() -> None:
            now = character.model_dump_json()
            if now == checked['as'] or checked['busy']:
                return
            checked['as'], checked['busy'] = now, True
            try:
                size, pictures = await asyncio.to_thread(self._sizes, character)
            except Exception:  # (a damaged lore book, the disk...: publishing says, if it fails)
                log.exception('The size of %s\'s card can\'t be told', character.id)
                return
            finally:
                checked['busy'] = False
            if box.is_deleted:
                return
            text.text = (f'Too big to be published: its card would be {megabytes(size)}, and a shared card\'s is '
                         f'{megabytes(MAX_FILE_BYTES)} at most. ' +
                         ('Its description\'s pictures take the most room: remove some.' if pictures * 2 > size else
                          'Shorten its texts or its lore books.'))
            box.set_visibility(size > MAX_FILE_BYTES)

        ui.timer(2.0, check)

    def _sizes(self, character: Character) -> tuple[int, int]:
        """The card's size, and about how much of it its description's pictures take: in base64 in its JSON, itself
        in base64 in the PNG (16/9 of their files' size)."""
        pictures = 0
        for name in pictures_in(character.description):
            path = self.store.images.path(name)
            pictures += path.stat().st_size * 16 // 9 if path is not None else 0
        return self.card_size(character), pictures

    def _text_list(self, character: Character, form: Form, attr: str, *, item: str, marker: str, minimum: int,
                   placeholder: str, add_label: str, min_height: str = '7rem') -> None:
        """An editable list of texts (first messages, example dialogues): add, move up, delete.

        `item` names one ('Message'), `marker` marks them ('message' -> message-0, delete-message),
        `minimum` is how many must stay.
        """
        def texts() -> list[str]:
            return getattr(character, attr)

        def put(values: list[str]) -> None:
            setattr(character, attr, values)

        def changed() -> None:
            if form.on_change:
                form.on_change()
            entries.refresh()

        def set_text(index: int, text: str) -> None:
            values = list(texts())
            values[index] = text or ''
            put(values)

        def add() -> None:
            put([*texts(), ''])
            changed()

        def move_up(index: int) -> None:
            values = list(texts())
            values[index - 1], values[index] = values[index], values[index - 1]
            put(values)
            changed()

        async def remove(index: int) -> None:
            if texts()[index].strip() and not await confirm(
                    f'Delete {item.lower()} {index + 1}?', 'Its text will be lost.', 'Delete'):
                return
            put([t for i, t in enumerate(texts()) if i != index])
            changed()

        @ui.refreshable
        def entries() -> None:
            count = len(texts())
            for index in range(count):
                with ui.column().classes('w-full gap-1').mark(f'{marker}-{index}'):
                    with ui.row().classes('w-full items-center justify-between no-wrap'):
                        ui.label(f'{item} {index + 1}' if count > 1 else item).classes('text-sm font-medium')
                        with ui.row().classes('gap-0 no-wrap'):
                            if index > 0:
                                ui.button(icon='arrow_upward', on_click=lambda i=index: move_up(i)) \
                                    .props('flat round dense aria-label="Move up"').tooltip('Move up') \
                                    .mark('move-up')
                            if count > minimum:
                                ui.button(icon='delete_outline', on_click=lambda i=index: remove(i)) \
                                    .props(f'flat round dense aria-label="Delete {item.lower()}"') \
                                    .tooltip(f'Delete {item.lower()}').mark(f'delete-{marker}')
                    text = form.bind(ui.textarea(placeholder=placeholder)
                                     .props(f'autogrow outlined input-style="min-height: {min_height}"'),
                                     get=lambda i=index: texts()[i],
                                     set=lambda value, i=index: set_text(i, value))
                    stats = ui.label(text_stats(texts()[index])).classes('muted text-sm')
                    text.on_value_change(lambda e, s=stats: s.set_text(text_stats(e.value or '')))
            ui.button(add_label, icon='add', on_click=add).props('outline').mark(f'add-{marker}')

        entries()


def inserted(text: str, at: int, piece: str) -> tuple[str, int]:
    """`piece` put in `text` at `at`, as a paragraph of its own (a blank line before and after it, unless it's at
    the start or the end); the text, and where the piece ends in it."""
    before, after = text[:at], text[at:]
    lead = '' if not before.strip() else '\n' * max(0, 2 - (len(before) - len(before.rstrip('\n'))))
    trail = '' if not after.strip() else '\n' * max(0, 2 - (len(after) - len(after.lstrip('\n'))))
    return f'{before}{lead}{piece}{trail}{after}', len(before) + len(lead) + len(piece)
