"""The Characters section, built on the shared profile page (profile_page.py).

    /characters                 grid of characters, each with its conversation count and a New chat button;
                                Import reads a character card (PNG, JSON or CharX, V1 to V3: see cards.py),
                                Export (in each character's ⋮ menu) writes one as a PNG (V2)
    /characters/<character id>  one character: picture, identity, description, scenario,
                                first messages, example dialogues, lore books; each part says
                                whether the AI gets it. Its ⋮ menu: Publish to shared cards
                                (sharing_page.py), when sharing cards can run here
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from typing import TYPE_CHECKING

from nicegui import ui

from ..cards import card_data, card_png, read_card, save_card
from ..characters import Character, CharacterStore, new_character
from ..conversations import ConversationStore
from ..lorebooks import LoreBook
from ..personas import Persona
from ..storage import JsonStore
from .collection_page import safe_file_name
from .form import Form, text_stats
from .list_view import Link, Row, initials, plural
from .profile_page import ProfileSection
from .widgets import MenuItems, audience_legend, browser_languages, confirm, panel

if TYPE_CHECKING:
    from .sharing_page import SharingSection

MACROS_HINT = 'Write {{char}} for the character\'s name and {{user}} for yours.'


class CharactersSection(ProfileSection[Character]):
    title_hint = 'Only for you: how the character is listed in this app'
    name_hint = 'Sent to the AI: the character\'s name in the chat. {{char}} stands for it.'
    lore_books_caption = ('Background the AI gets only when it matters: an entry is sent when one of its keywords '
                          'comes up in a chat with this character (or every time, for entries set so).')
    lore_books_audience = 'ai'

    def __init__(self, store: CharacterStore, lore_books: JsonStore[LoreBook],
                 conversations: ConversationStore, personas: ProfileSection[Persona], *,
                 sharing: SharingSection | None = None) -> None:
        super().__init__(
            store, lore_books, title='Characters', noun='character', path='/characters', create=new_character,
            title_placeholder='e.g. Seraphina, forest guardian',
            empty_text='A character is who you chat with: a name, a picture, a description for you, a '
                       'scenario telling the AI who they are and what the story is, and how the chat opens.',
            conversations=conversations, conversation_field='character',
            import_file=self.import_card, import_accept='.png,.json,.charx,image/png,application/json',
            export_file=self.export_card,
        )
        self.conversations: ConversationStore = conversations
        self.personas = personas
        self.sharing = sharing  # publishing them as shared cards (None, or its .sharing None: not here)
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
        """The character as a V2 character card in a PNG, with their picture and lore books (see cards.py)."""
        books = [book for ref in character.lorebooks if (book := self._lore_book(ref.id)) is not None]
        whole = character.image_full or character.image  # the whole picture, not the framed square
        path = self.store.images.path(whole) if whole else None
        picture = path.read_bytes() if path is not None and path.is_file() else None
        content = card_png(card_data(character, books), picture, initials=initials(character.title))
        return content, f'{safe_file_name(character.title)}.png', 'image/png'

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
        row.body = character.description.strip()
        row.button = Link('New chat', icon='add_comment', marker='new-chat',
                          handler=lambda: self.new_chat(character))
        return row

    async def new_chat(self, character: Character) -> None:
        """Ask which persona to play, then start the conversation and open it."""
        personas = self.personas.store.list()
        with ui.context.client.layout, ui.dialog() as dialog, \
                ui.card().classes('dialog-card').mark('persona-picker'):
            ui.label(f'New chat with {character.title}').classes('panel-title')
            if personas:
                ui.label('Who will you be? Choose a persona.').classes('muted')
                with ui.column().classes('w-full browse-list'):
                    self.personas.links(personas, on_click=dialog.submit)
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

    def intro(self, character: Character) -> None:
        audience_legend('Name, Scenario, First messages, Example dialogues, Lore books',
                        'Title, Picture, Description')
        if self.sharing is not None and self.sharing.sharing is not None:
            # From a shared card? Said once the catalog answered (withdrawn since, a newer version...)
            with ui.column().classes('w-full gap-2'):
                ui.timer(0.01, lambda: self.sharing.character_note(
                    character, fingerprint=self.card_fingerprint, export=lambda c: self.export_card(c)[0],
                    publish=lambda: self.publish(character)), once=True)

    def panels(self, character: Character, form: Form) -> None:
        with panel('Description', 'What the character is about, in a sentence or two, to help you pick them: it\'s '
                                  'shown in the list of characters. The AI never sees it.', audience='you'):
            form.long_text(character, 'description', 'Description', min_height='4rem', tokens=False,
                           placeholder='e.g. A centuries-old guardian who protects travellers lost in her forest.')

        with panel('Scenario', 'Everything the AI knows about the character and the story: who {{char}} is (looks, '
                               'personality, way of speaking, past), and the situation the chat starts in (where, '
                               'when, what is going on). The AI reads it before every reply. '
                               f'{MACROS_HINT}', audience='ai'):
            form.long_text(character, 'scenario', 'Scenario', min_height='12rem',
                           placeholder='e.g. {{char}} is the guardian of the Eldwood, kind but wary of strangers. '
                                       'She speaks softly, in short sentences.\n\n{{user}} wakes up wounded in her '
                                       'glade, with no memory of how they got there.')

        with panel('First messages', 'How {{char}} opens a chat: the first message, written as the character. It '
                                     'becomes part of the chat, which the AI reads like the rest. With several, '
                                     f'you can switch between them at the start of a chat. {MACROS_HINT}',
                   audience='ai'):
            self._text_list(character, form, 'first_messages', item='Message', marker='message', minimum=1,
                            placeholder='*She looks up as you step into the glade.* ...',
                            add_label='Add a first message')

        with panel('Example dialogues', 'Short exchanges showing how {{char}} speaks and acts, written as lines '
                                        'like "{{user}}: …" and "{{char}}: …". The AI picks up their style, and '
                                        'knows they aren\'t part of the story. By default, they\'re sent while the '
                                        'whole chat fits; as it grows, they make room for it, the last ones first '
                                        '(Settings › Chat).', audience='ai'):
            self._text_list(character, form, 'example_dialogues', item='Example', marker='example', minimum=0,
                            placeholder='{{user}}: Who are you?\n{{char}}: *She tilts her head.* "The one who keeps '
                                        'this forest safe. And you are bleeding on my moss."',
                            add_label='Add an example dialogue', min_height='5rem')

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

