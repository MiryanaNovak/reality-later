"""The app shell: header with the menu button, side menu, and the routes.

To add a section: write its page function(s), add a line to SECTIONS for the
menu, and add its routes in `root()`. For a list of saved objects, start from
Collection / CollectionPage (see lorebooks_page.py); for objects with a picture,
title and name, from ProfileSection (see characters_page.py).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

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
from ..publishing import runtime_problem as sharing_problem
from ..replies import Replies
from ..vectors import Vectorizer
from ..settings import SettingsStore, SharingSettings
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
from .sharing_page import SharingSection
from .widgets import bar_slot, clearing_bar

if TYPE_CHECKING:
    from ..publishing.service import Sharing

# Menu entries: (path, label, icon). Icons: https://fonts.google.com/icons
SECTIONS = [
    ('/', 'Home', 'home'),
    ('/conversations', 'Conversations', 'forum'),
    ('/characters', 'Characters', 'theater_comedy'),
    ('/shared', 'Shared cards', 'travel_explore'),
    ('/published', 'Your publications', 'cloud_upload'),
    ('/personas', 'Personas', 'face'),
    ('/lorebooks', 'Lore books', 'auto_stories'),
    ('/prompts', 'Prompts', 'description'),
    ('/connections', 'Connections', 'cable'),
    ('/settings', 'Settings', 'settings'),
]
COMPUTER = ('/computer', 'This computer', 'computer')  # only on the computer the app runs on


def build_root(data_dir: str | Path, *, access: Access | None = None,
               server: ServerInfo | None = None, models: str | Path | None = None,
               sharing_relays: Iterable[str] | None = None, sharing: Sharing | None = None) -> Callable[[], None]:
    """Return the function NiceGUI calls to build the page for every browser tab.

    `access`: who may use the app (see access.py); None when pairing isn't set up (tests).
    `server`: how the app runs, for the "This computer" page (the defaults if not given: tests).
    `models`: the folder of vector search's models (paths.Places.models; <data_dir>/models if not given).
    `sharing_relays`: the relays a new sharing catalog starts with (publishing/service.py's if not given).
    `sharing`: card sharing as the tests make it (BitTorrent on 127.0.0.1 only); made here if not given, with
    the publisher key in the settings folder's certificates/ (publishing/identity.py).
    """
    theme.setup()
    server = server or ServerInfo(Config(folder=Path(data_dir)), None, Path(data_dir))
    computer = ComputerPage(server, access)
    connections = ConnectionsSection(connection_store(data_dir))
    lore_books = lore_book_store(data_dir)
    conversation_store = ConversationStore(data_dir)
    personas = PersonasSection(PersonaStore(data_dir), lore_books, conversation_store)
    settings_store = SettingsStore(data_dir)
    shared = SharingSection(sharing, settings_store) if sharing is not None else \
        _sharing(data_dir, settings_store, sharing_relays, key_folder=server.config.folder)
    characters = CharactersSection(CharacterStore(data_dir), lore_books, conversation_store, personas,
                                   sharing=shared)
    shared.characters = characters.store
    shared.card_fingerprint = characters.card_fingerprint  # (Your publications: whether a card's character
    shared.card_export = lambda c: characters.export_card(c)[0]  # changed since it was published)
    if shared.sharing is not None:  # (shared cards downloaded become characters, as the Import button does)
        shared.sharing.importer = characters.import_shared
        try:
            shared.sharing.share_downloads = settings_store.load().sharing.share_downloads
        except ValueError:  # (a damaged settings file: the Settings page says so)
            pass
    set_up_presets(data_dir, settings_store)  # the first time: the instructions from Settings become a prompt
    presets = PresetsSection(preset_store(data_dir), settings_store)
    vectors = Vectorizer(data_dir, models or Path(data_dir) / 'models', settings=settings_store,
                         lore_books=lore_books, memories=conversation_store.memories)
    # Saved (edited, imported, undone): vectorized in the background, a moment later (vectors.py)
    lore_books.on_saved['vectors'] = lambda book: vectors.soon('lorebooks', book.id)
    conversation_store.memories.on_saved['vectors'] = lambda memories: vectors.soon('memories', memories.id)
    replies = Replies(conversation_store, characters=characters.store, personas=personas.store,
                      lore_books=lore_books, settings=settings_store, connections=connections.collection.store,
                      presets=presets.collection.store, vectors=vectors)
    conversations = ConversationsSection(conversation_store, characters, personas, replies)
    lorebooks = LoreBooksSection(lore_books, users=[characters, personas])
    settings = SettingsSection(settings_store, connections.collection.store, access, presets.collection.store,
                               vectors=vectors, sharing=shared)
    home = HomePage(connections=connections.collection.store, settings=settings_store, home=HomeStore(data_dir),
                    conversations=conversation_store, characters=characters, personas=personas,
                    access=access, server=server)
    app.on_shutdown(replies.stop_all)
    app.on_shutdown(vectors.stop)
    if shared.sharing is not None:
        app.on_startup(shared.sharing.resume_sharing)  # the files of the cards published from here, shared again
        app.on_shutdown(shared.sharing.close)
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
            '/shared': shared.list_page,
            '/shared/{event_id}': shared.card_page,
            '/published': shared.published_page,
            '/conversations': conversations.list_page,
            '/conversations/{conversation_id}': conversations.chat_page,
            '/conversations/{conversation_id}/memories': conversations.memories_page,
            '/settings': settings.page,
            '/computer': computer.page,
        }
        with ui.column().classes('app-content'):
            ui.sub_pages({path: clearing_bar(page) for path, page in pages.items()}).classes('w-full')

    return root


def _sharing(data_dir: str | Path, settings: SettingsStore, relays: Iterable[str] | None, *,
             key_folder: Path) -> SharingSection:
    """Card sharing (publishing/service.py), or why it can't run here: a missing library, a damaged catalog.
    The publisher key: in `key_folder`'s certificates/ (the settings folder's, with HTTPS's own key)."""
    if problem := sharing_problem():
        from ..publishing import sentence
        return SharingSection(None, settings, problem=sentence(problem))
    from ..publishing.catalog import CatalogError
    from ..publishing.identity import key_path
    from ..publishing.seeder import TorrentSession
    from ..publishing.service import DEFAULT_RELAYS, Sharing
    try:
        chosen = settings.load().sharing
    except ValueError:  # (a damaged settings file: the Settings page says so)
        chosen = SharingSettings()
    try:  # (BitTorrent's session opens its port only once there's something to share or download)
        session = TorrentSession(port=chosen.port, port_mapping=chosen.port_mapping)
        return SharingSection(Sharing(data_dir, key_file=key_path(key_folder), session=session,
                                      default_relays=DEFAULT_RELAYS if relays is None else relays), settings)
    except CatalogError as e:
        return SharingSection(None, settings, problem=str(e))
