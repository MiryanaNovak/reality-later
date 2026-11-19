"""Markdown is shown with MarkdownView (widgets.py), not ui.markdown, whose text, in the browser, waits for a
stylesheet: when a browser never finished loading it, new chat messages showed only their name and time."""
from pathlib import Path

from nicegui import ui
from nicegui.testing import user_simulation
from test_chat import send, setup  # noqa: F401  (the fixture)
from test_character_page import one, page  # noqa: F401  (the fixture)

import rpapp
from rpapp.ui.widgets import MarkdownView


def test_no_ui_markdown_left():
    sources = Path(rpapp.__file__).parent.rglob('*.py')
    assert [p.name for p in sources if 'ui.markdown(' in p.read_text(encoding='utf-8')] == []


async def test_rendered_here_cleaned_twice(tmp_path):
    def clean(html: str) -> str:
        return html.replace('<script>', '')

    def root() -> None:
        view = MarkdownView('Some *words*', extras=['break-on-newline'], clean=clean).mark('view')
        ui.button('Change', on_click=lambda: view.set_content('Now **this**.<script>')).mark('change')

    async with user_simulation(root) as user:
        await user.open('/')
        [view] = user.find(marker='view').elements
        assert view.props['innerHTML'] == '<p>Some <em>words</em></p>\n'  # (ready as it's drawn: no stylesheet)
        assert view.props['sanitize'] is True and 'nicegui-markdown' in view.classes  # (DOMPurify in the browser)
        assert 'resource-name' not in view.props
        user.find(marker='change').click()
        assert view.props['innerHTML'] == '<p>Now <strong>this</strong>.</p>\n'  # (Markdown too, cleaned)


async def test_chat_messages(setup):  # noqa: F811
    user, conversations, conversation = setup
    await send(user, 'Hello, *you*.')
    views = [e for e in user.client.layout.descendants() if isinstance(e, MarkdownView)]
    assert not any(isinstance(e, ui.markdown) for e in user.client.layout.descendants())
    assert [v.props['innerHTML'] for v in views][-2:] == [
        '<p>Hello, <em>you</em>.</p>\n', '<p><em>Seraphina smiles.</em> Rest now.</p>\n']


async def test_descriptions(page):  # noqa: F811
    user, characters, _, sera, _, _ = page
    sera.description = 'Her spell:\n\n```python\nprint("light")\n```'
    characters.save(sera)
    for path in (f'/characters/{sera.id}', f'/characters/{sera.id}/edit'):  # (as it shows, and its preview)
        await user.open(path)
        await user.should_see(marker='character-description' if path.endswith(sera.id) else 'part-summary')
        view = one(user, 'character-description' if path.endswith(sera.id) else 'description-preview')
        assert isinstance(view, MarkdownView) and '<pre><code>print("light")' in view.props['innerHTML']
        assert not any(isinstance(e, ui.markdown) for e in user.client.layout.descendants())
