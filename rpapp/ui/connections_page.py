"""The Connections section: the list page (shared code) and the editor page."""
from __future__ import annotations

import time

from nicegui import ui

from ..connections import CONNECTION_TYPES, Connection, Message, ModelError
from ..storage import JsonStore
from .collection_page import Collection, CollectionPage
from .connection_editors import build_editor, kind_icon
from .form import Form
from .list_view import Row
from .widgets import not_found, panel

TEST_PROMPT = 'Say hello in five words or fewer.'


def _row(conn: Connection) -> Row:
    kind_name = type(conn).display_name
    return Row(title=conn.name, subtitle=conn.model or 'No model chosen yet',
               icon=kind_icon(conn), icon_tooltip=kind_name, side=kind_name)


def _create(name: str, kind: str | None) -> Connection:
    cls = CONNECTION_TYPES[kind or 'openrouter']
    return cls(name=name or cls.display_name)


class ConnectionsSection:
    def __init__(self, store: JsonStore[Connection]) -> None:
        self.collection = Collection(
            title='Connections', noun='connection', path='/connections', store=store,
            row=_row, create=_create,
            kinds={kind: cls.display_name for kind, cls in CONNECTION_TYPES.items()},
            default_kind='openrouter',
            search=lambda conn: f'{conn.name} {conn.model}',
            empty_text='A connection tells the app which AI model to use and how to reach it. '
                       'Create one, or import a connection file you saved earlier.',
            name_placeholder='e.g. DeepSeek for adventures',
        )

    def list_page(self) -> None:
        CollectionPage(self.collection).list_page()

    def edit_page(self, conn_id: str) -> None:
        store = self.collection.store
        try:
            conn = store.get(conn_id)
        except ValueError:
            conn = None
        if conn is None:
            not_found('Connection', self.collection.path)
            return

        def save() -> None:
            try:
                store.save(conn)
            except OSError as err:
                ui.notify(f'Could not save: {err}', type='negative')
                return
            title.text = conn.name or 'Untitled'

        title = CollectionPage(self.collection).item_header(
            conn, f'{type(conn).display_name} connection. Changes save automatically.')
        build_editor(conn, Form(on_change=save))
        _test_panel(conn)


def _test_panel(conn: Connection) -> None:
    async def run_test() -> None:
        button.disable()
        spinner.set_visibility(True)
        result.text = ''
        result.classes(remove='text-negative')
        started = time.monotonic()
        try:
            reply = await conn.complete([Message(role='user', content=TEST_PROMPT)])
        except ModelError as err:
            result.text = f'It didn\'t work. {err}'
            result.classes(add='text-negative')
        else:
            reply = ' '.join(reply.split())[:300] or '(empty reply)'
            result.text = f'Replied in {time.monotonic() - started:.1f} s: {reply}'
        finally:
            button.enable()
            spinner.set_visibility(False)

    with panel('Test', f'Sends “{TEST_PROMPT}” with the settings above.'):
        with ui.row().classes('items-center gap-3'):
            button = ui.button('Send test message', icon='send', on_click=run_test)
            spinner = ui.spinner(size='1.5em')
            spinner.set_visibility(False)
        result = ui.label().classes('text-sm break-words w-full')
