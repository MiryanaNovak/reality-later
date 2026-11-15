"""Dialogs (widgets.Dialog): a click on the shade around one, right as it opens, doesn't close it. It's the
second click of a double click on what opened it (or a mouse button that bounces): the dialog seemed to open
and close by itself."""
import asyncio

from nicegui import ui
from nicegui.testing import user_simulation

from rpapp.ui import widgets
from rpapp.ui.widgets import Dialog, ask_name


async def test_steady_as_it_opens_then_as_usual(monkeypatch):
    monkeypatch.setattr(Dialog, 'STEADY', 0.2)
    answers = []

    def root():
        async def rename():
            answers.append(await ask_name('Rename the folder', 'Fantasy'))
        ui.button('Rename', on_click=rename).mark('rename')

    async with user_simulation(root) as user:
        await user.open('/')
        user.find(marker='rename').click()
        await user.should_see(marker='name-dialog')
        [dialog] = [e for e in user.client.layout.descendants() if isinstance(e, Dialog)]
        assert dialog.value and 'no-backdrop-dismiss' in dialog.props and 'no-shake' in dialog.props
        await asyncio.sleep(0.4)
        assert 'no-backdrop-dismiss' not in dialog.props and 'no-shake' not in dialog.props  # (as usual since)
        dialog.set_value(False)  # (the shade clicked now, or Escape: closed)
        await asyncio.sleep(0.1)
        assert answers == [None]


async def test_persistent_dialogs_and_ones_opened_again():
    def root():
        ui.button('Open', on_click=lambda: opened.open()).mark('open')
        with Dialog().props('persistent') as persistent:
            ui.label('Persistent')
        with Dialog() as opened:
            ui.label('Again')
        root.dialogs = persistent, opened
        persistent.open()

    async with user_simulation(root) as user:
        await user.open('/')
        persistent, opened = root.dialogs
        assert persistent.value and 'no-backdrop-dismiss' not in persistent.props  # (nothing to do)
        user.find(marker='open').click()
        assert opened.value and 'no-backdrop-dismiss' in opened.props
        await asyncio.sleep(Dialog.STEADY + 0.2)
        assert 'no-backdrop-dismiss' not in opened.props
        opened.close()
        user.find(marker='open').click()  # (each time it opens)
        assert 'no-backdrop-dismiss' in opened.props


def test_every_dialog_of_the_app_is_one():
    import re
    from pathlib import Path
    made = re.compile(r'ui\.dialog\([^)]*\)\s*(as\b|\.|$)', re.MULTILINE)  # (made, not named in a note)
    sources = Path(widgets.__file__).parent.glob('*.py')
    assert not [path.name for path in sources if made.search(path.read_text(encoding='utf-8'))]
    assert made.search('with ui.dialog() as dialog:') and made.search('d = ui.dialog().props("x")')
