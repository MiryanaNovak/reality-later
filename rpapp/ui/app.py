"""The app shell: header with the menu button, side menu, and the routes.

To add a section: write its page function(s), add a line to SECTIONS for the
menu, and add its routes in `root()`. For a list of saved objects, start from
Collection / CollectionPage (see lorebooks_page.py); for objects with a picture,
title and name, from ProfileSection (see characters_page.py).
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from nicegui import app, ui

from .. import APP_NAME
from ..access import Access
from ..config import Config
from ..connections import connection_store
from ..conversations import ConversationStore
from ..home import HomeStore
from ..characters import CharacterStore
from ..lorebooks import lore_book_store
from ..launcher import ServerInfo
from ..personas import PersonaStore
from ..presets import preset_store, set_up_presets
from ..replies import Replies
from ..settings import SettingsStore
from . import appearance, media, theme
from .characters_page import CharactersSection
from .computer_page import ComputerPage, on_this_computer
from .connections_page import ConnectionsSection
from .conversations_page import ConversationsSection
from .devices_page import watch_requests
from .home import HomePage
from .list_view import slug
from .lorebooks_page import LoreBooksSection
from .personas_page import PersonasSection
from .presets_page import PresetsSection
from .settings_page import SettingsSection
from .widgets import bar_slot, clearing_bar

# Menu entries: (path, label, icon). Icons: https://fonts.google.com/icons
SECTIONS = [
    ('/', 'Home', 'home'),
    ('/conversations', 'Conversations', 'forum'),
    ('/characters', 'Characters', 'theater_comedy'),
    ('/personas', 'Personas', 'face'),
    ('/lorebooks', 'Lore books', 'auto_stories'),
    ('/prompts', 'Prompts', 'description'),
    ('/connections', 'Connections', 'cable'),
    ('/settings', 'Settings', 'settings'),
]
COMPUTER = ('/computer', 'This computer', 'computer')  # only on the computer the app runs on


def build_root(data_dir: str | Path, *, access: Access | None = None,
               server: ServerInfo | None = None) -> Callable[[], None]:
    """Return the function NiceGUI calls to build the page for every browser tab.

    `access`: who may use the app (see access.py); None when pairing isn't set up (tests).
    `server`: how the app runs, for the "This computer" page (the defaults if not given: tests).
    """
    theme.setup()
    server = server or ServerInfo(Config(folder=Path(data_dir)), None, Path(data_dir))
    computer = ComputerPage(server, access)
    connections = ConnectionsSection(connection_store(data_dir))
    lore_books = lore_book_store(data_dir)
    conversation_store = ConversationStore(data_dir)
    personas = PersonasSection(PersonaStore(data_dir), lore_books, conversation_store)
    characters = CharactersSection(CharacterStore(data_dir), lore_books, conversation_store, personas)
    settings_store = SettingsStore(data_dir)
    set_up_presets(data_dir, settings_store)  # the first time: the instructions from Settings become a prompt
    presets = PresetsSection(preset_store(data_dir), settings_store)
    replies = Replies(conversation_store, characters=characters.store, personas=personas.store,
                      lore_books=lore_books, settings=settings_store, connections=connections.collection.store,
                      presets=presets.collection.store)
    conversations = ConversationsSection(conversation_store, characters, personas, replies)
    lorebooks = LoreBooksSection(lore_books, users=[characters, personas])
    settings = SettingsSection(settings_store, connections.collection.store, access, presets.collection.store)
    home = HomePage(connections=connections.collection.store, settings=settings_store, home=HomeStore(data_dir),
                    conversations=conversation_store, characters=characters, personas=personas,
                    access=access, server=server)
    app.on_shutdown(replies.stop_all)
    app.on_shutdown(settings_store.flush)
    app.on_shutdown(conversation_store.memories.flush)
    for store in (connections.collection.store, lorebooks.store, personas.store, characters.store,
                  conversation_store, presets.collection.store):
        app.on_shutdown(store.flush)  # write edits still waiting to be saved

    def accent() -> str:
        try:
            return settings_store.load().appearance.accent
        except ValueError:  # a damaged settings file: the Settings page says so
            return theme.DEFAULT_ACCENT

    def root() -> None:
        media.ensure_route()
        theme.add_head(accent())
        appearance.apply_look(appearance.device_look())  # light or dark, and text size, of this device
        ui.page_title(APP_NAME)
        watch_requests(access)  # on the computer itself: a notice when a device asks to join

        # The top bar: the menu button, then the page's title and what goes with it (see widgets.page_bar)
        with ui.header(bordered=False).classes('app-header items-center no-wrap gap-1 px-2'):
            ui.button(icon='menu', on_click=lambda: drawer.toggle()) \
                .props('flat round aria-label="Menu"')
            bar_slot()

        with ui.left_drawer().classes('app-drawer pt-3').props('width=240') as drawer:
            nav_items: dict[str, ui.item] = {}
            for path, label, icon in SECTIONS + ([COMPUTER] if on_this_computer() else []):
                with ui.item(on_click=lambda p=path: go(p)).classes('nav-item').mark(f'nav-{slug(label)}') as item:
                    with ui.item_section().props('avatar').classes('min-w-0 pr-4'):
                        ui.icon(icon, size='sm')
                    ui.item_section(label)
                nav_items[path] = item

        def go(path: str) -> None:
            ui.navigate.to(path)
            # On narrow screens the menu covers the page, so close it after choosing.
            ui.run_javascript(f'if (window.innerWidth < 1024) getElement({drawer.id}).hide()')

        def mark_active(current: str) -> None:
            current = current.split('?')[0].split('#')[0].rstrip('/') or '/'
            for path, item in nav_items.items():
                active = current == path or (path != '/' and current.startswith(path + '/'))
                item.props(add='active' if active else None, remove=None if active else 'active')

        router = ui.context.client.sub_pages_router
        router.on_path_changed(mark_active)
        mark_active(router.current_path)

        pages = {
            '/': home.page,
            '/prompts': presets.list_page,
            '/prompts/{preset_id}': presets.edit_page,
            '/connections': connections.list_page,
            '/connections/{conn_id}': connections.edit_page,
            '/lorebooks': lorebooks.list_page,
            '/lorebooks/{book_id}': lorebooks.book_page,
            '/lorebooks/{book_id}/entries/{uid}': lorebooks.entry_page,
            '/personas': personas.list_page,
            '/personas/{item_id}': personas.edit_page,
            '/characters': characters.list_page,
            '/characters/{item_id}': characters.edit_page,
            '/conversations': conversations.list_page,
            '/conversations/{conversation_id}': conversations.chat_page,
            '/conversations/{conversation_id}/memories': conversations.memories_page,
            '/settings': settings.page,
            '/computer': computer.page,
        }
        with ui.column().classes('app-content'):
            ui.sub_pages({path: clearing_bar(page) for path, page in pages.items()}).classes('w-full')

    return root
