"""The Home page: what to set up next, and where you left off.

    /

From top to bottom:
- Tips, each with its own button and a Dismiss button (a dismissed tip never comes back,
  unless you ask at the bottom of the page: see home.py):
    no connection yet                    -> add one (Connections)
    none chosen for chats, or the one chosen for memories was deleted -> choose (Settings)
    no device ever paired                -> pair one (This computer); only on a network, with pairing on
- Recent conversations: the last ones you chatted in, as cards (the character's picture, with
  your persona's in the corner, and Continue), at most two rows; View all opens Conversations.
- Recently added characters: as on the Characters page, at most two rows; View all opens it.

"At most two rows" is CSS: the grids always hold up to MAX_CARDS cards, and the stylesheet
hides those past the second row for the width at hand (see theme.py, .home-grid).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from nicegui import ui

from ..access import Access
from ..connections import Connection
from ..conversations import Conversation, ConversationStore
from ..home import HomeStore
from ..launcher import ServerInfo
from ..settings import SettingsStore
from ..storage import JsonStore
from .characters_page import CharactersSection
from .list_view import initials, render_card
from .personas_page import PersonasSection
from .widgets import page_bar, time_ago

MAX_CARDS = 10  # enough for two rows at the widest the page gets


@dataclass
class Tip:
    id: str
    icon: str
    text: str
    action: str
    path: str


class HomePage:
    def __init__(self, *, connections: JsonStore[Connection], settings: SettingsStore, home: HomeStore,
                 conversations: ConversationStore, characters: CharactersSection, personas: PersonasSection,
                 access: Access | None = None, server: ServerInfo | None = None) -> None:
        self.connections = connections
        self.settings = settings
        self.home = home
        self.conversations = conversations
        self.characters = characters
        self.personas = personas
        self.access = access
        self.server = server

    def page(self) -> None:
        page_bar('Home')

        @ui.refreshable
        def tips() -> None:
            self._tips(tips.refresh)

        @ui.refreshable
        def sections() -> None:
            self._conversations()
            self._characters(sections.refresh)

        tips()
        sections()

    # -- tips --------------------------------------------------------------------------------

    def current_tips(self) -> list[Tip]:
        """The tips that apply now, dismissed or not."""
        found: list[Tip] = []
        connections = {c.id: c for c in self.connections.list()}
        if not connections:
            found.append(Tip('create-connection', 'cable',
                             'To chat, the app needs a connection to an AI model: an online service like '
                             'OpenRouter, or a model running on your computer.', 'Add a connection', '/connections'))
        else:
            try:
                settings = self.settings.load()
            except ValueError:  # a damaged settings file: the Settings page says so
                settings = None
            if settings is not None:
                chat = settings.chat.connection
                memory = settings.memory.connection if settings.memory.enabled else None
                if chat is None or chat.id not in connections:
                    found.append(Tip('choose-connection', 'tune',
                                     'Choose the connection chats use. Memories use it too, unless you pick '
                                     'another for them.', 'Choose it', '/settings'))
                elif memory is not None and memory.id not in connections:
                    found.append(Tip('choose-connection', 'tune',
                                     f'The connection chosen for memories, "{memory.name}", was deleted: '
                                     'choose another.', 'Choose it', '/settings'))
        access = self.access
        if access is not None and access.enabled and not access.devices.ever_paired \
                and (self.server is None or self.server.network_urls()):
            found.append(Tip('pair-device', 'smartphone',
                             'Use the app from your phone or another computer too: pair it once, by scanning '
                             'a code.', 'Pair a device', '/computer'))
        return found

    def _tips(self, refresh: Callable[[], None]) -> None:
        current = self.current_tips()
        dismissed = self.home.dismissed()
        shown = [tip for tip in current if tip.id not in dismissed]
        hidden = {tip.id for tip in current if tip.id in dismissed}

        def dismiss(tip: Tip) -> None:
            self.home.dismiss(tip.id)
            refresh()

        def restore() -> None:
            self.home.restore(hidden)
            refresh()

        if shown:
            with ui.column().classes('w-full gap-2').mark('home-tips'):
                for tip in shown:
                    with ui.element('div').classes('home-tip').mark(f'tip-{tip.id}'):
                        ui.icon(tip.icon).classes('home-tip-icon')
                        ui.label(tip.text).classes('home-tip-text')
                        with ui.element('div').classes('home-tip-actions'):
                            ui.button(tip.action, on_click=lambda t=tip: ui.navigate.to(t.path)) \
                                .props('unelevated dense').classes('px-3').mark('tip-action')
                            ui.button('Dismiss', on_click=lambda t=tip: dismiss(t)) \
                                .props('flat dense').classes('px-2').mark('tip-dismiss')
        if hidden:
            with ui.row().classes('items-center gap-1 -mt-2').mark('hidden-tips'):
                ui.label(f'{len(hidden)} dismissed tip{"s" if len(hidden) > 1 else ""} still '
                         f'appl{"y" if len(hidden) > 1 else "ies"}.').classes('text-xs muted')
                ui.button('Show again', on_click=restore).props('flat dense no-caps size=sm') \
                    .classes('text-xs').mark('show-tips')

    # -- recent conversations ----------------------------------------------------------------

    def _conversations(self) -> None:
        recent = sorted(self.conversations.list(), key=lambda c: c.updated_at, reverse=True)[:MAX_CARDS]
        self._section('Recent conversations', '/conversations', 'conversations')
        if not recent:
            has_characters = bool(self.characters.store.list())
            ui.label('No conversations yet. ' + ('Start one with New chat, on a character below.' if has_characters
                                                 else 'Add a character first, then start a chat with them.')) \
                .classes('muted -mt-2').mark('no-conversations')
            return
        with ui.element('div').classes('home-grid').mark('recent-conversations'):
            with ui.element('div').classes('item-grid'):
                for conversation in recent:
                    self._conversation_card(conversation)

    def _conversation_card(self, conversation: Conversation) -> None:
        character = self.characters.store.get(conversation.character.id)
        persona = self.personas.store.get(conversation.persona.id)
        character_title = character.title if character else conversation.character.title
        persona_title = persona.title if persona else conversation.persona.title

        def open_chat() -> None:
            ui.navigate.to(f'/conversations/{conversation.id}')

        with ui.element('div').classes('grid-card').props('tabindex=0') \
                .mark(f'conversation-{conversation.id}') as card:
            card.on('click', open_chat).on('keydown.enter', open_chat)
            with ui.element('div').classes('grid-card-image conversation-image'):
                _picture(self.characters.image_url(character.image) if character and character.image else '',
                         character_title, 'grid-card-initials')
                with ui.element('div').classes('conversation-persona').tooltip(f'You, as {persona_title}'):
                    _picture(self.personas.image_url(persona.image) if persona and persona.image else '',
                             persona_title, '')
            with ui.column().classes('w-full min-w-0 gap-0 px-3 pt-2'):
                ui.label(character_title).classes('grid-card-title')
                ui.label(f'As {persona_title}').classes('text-sm muted truncate w-full')
                when = time_ago(conversation.updated_at)
                ui.label(when[:1].upper() + when[1:]).classes('text-xs muted')
            excerpt = _last_message(conversation, (character.name if character else '') or character_title,
                                    (persona.name if persona else '') or persona_title)
            ui.label(excerpt).classes('grid-card-body conversation-excerpt')
            ui.button('Continue', icon='chat', on_click=open_chat).props('outline dense') \
                .classes('grid-card-button').on('click.stop', js_handler='() => {}').mark('continue')

    # -- recently added characters -----------------------------------------------------------

    def _characters(self, refresh: Callable[[], None]) -> None:
        store = self.characters.store
        recent = sorted(store.list(), key=store.added_at, reverse=True)[:MAX_CARDS]
        self._section('Recently added characters', '/characters', 'characters')
        if not recent:
            ui.label('No characters yet. Create one, or import a character card, on the Characters page.') \
                .classes('muted -mt-2').mark('no-characters')
            return
        self.characters.prepare_cards()
        actions = self.characters.pages.actions(refresh)
        path = self.characters.collection.item_path
        with ui.element('div').classes('home-grid').mark('recent-characters'):
            with ui.element('div').classes('item-grid'):
                for character in recent:
                    render_card(character, self.characters.card(character), key=character.id,
                                on_open=lambda c: ui.navigate.to(path(c)), actions=actions)

    # -- shared ---------------------------------------------------------------------------

    @staticmethod
    def _section(title: str, path: str, marker: str) -> None:
        with ui.row().classes('w-full items-center justify-between no-wrap mt-2'):
            ui.label(title).classes('section-title')
            ui.button('View all', on_click=lambda: ui.navigate.to(path)) \
                .props('flat dense no-caps icon-right=arrow_forward').mark(f'view-all-{marker}')


def _picture(url: str, title: str, initials_class: str) -> None:
    if url:
        ui.image(url).props(f'ratio=1 fit=cover no-spinner alt="{title.replace(chr(34), "")}"') \
            .classes('w-full h-full')
    else:
        ui.label(initials(title)).classes(initials_class)


def _last_message(conversation: Conversation, char: str, user: str) -> str:
    """The last message, as one plain line: 'Mira: Where am I?'."""
    if not conversation.messages:
        return ''
    message = conversation.messages[-1]
    text = re.sub(r'(?is)<(script|style)\b.*?</\1\s*>', ' ', message.content)  # code and styles: not words
    text = re.sub(r'<[^>]*>?', ' ', text)  # HTML tags, even a cut-off one
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)  # Markdown pictures
    text = re.sub(r'[*_~`#>]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    speaker = user if message.role == 'user' else char
    return f'{speaker}: {text[:200]}' if text else ''
