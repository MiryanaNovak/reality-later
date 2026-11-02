"""Dialogue in chat messages: what's in quotation marks is marked, and colored readably."""
import pytest
from nicegui.elements.markdown import prepare_content
from nicegui.testing import user_simulation

from rpapp.characters import CharacterStore, new_character
from rpapp.conversations import ConversationStore
from rpapp.dialogue import mark_dialogue
from rpapp.personas import PersonaStore, new_persona
from rpapp.ui import theme
from rpapp.ui.app import build_root
from rpapp.ui.chat_page import MARKDOWN, message_html

S, E = '<span class="dialogue">', '</span>'


def shown(markdown: str) -> str:
    return message_html(prepare_content(markdown, ' '.join(MARKDOWN))).strip()


@pytest.mark.parametrize('markdown, html', [
    ('*She looks up.* "You came," she says.',
     f'<p><em>She looks up.</em> {S}"You came,"{E} she says.</p>'),
    # across italics, and in them
    ('"I *really* mean it."', f'<p>{S}"I {E}<em>{S}really{E}</em>{S} mean it."{E}</p>'),
    ('*"Come here," she whispers.*', f'<p><em>{S}"Come here,"{E} she whispers.</em></p>'),
    # other quotation marks
    ('“Hi” «Salut» „Hallo“ „Cześć” 「やあ」 『ねえ』',
     f'<p>{S}“Hi”{E} {S}«Salut»{E} {S}„Hallo“{E} {S}„Cześć”{E} {S}「やあ」{E} {S}『ねえ』{E}</p>'),
    # a quote in a quote stays in it
    ('"She said “no” to me."', f'<p>{S}"She said “no” to me."{E}</p>'),
    # no closing mark: to the end of the paragraph, or of the line, and not past it
    ('"A long speech.\n\n"Its end," she says.',
     f'<p>{S}"A long speech.{E}</p>\n\n<p>{S}"Its end,"{E} she says.</p>'),
    ('"Forgot to close\n"Next" ok', f'<p>{S}"Forgot to close{E}<br>\n{S}"Next"{E} ok</p>'),
    # not quotes
    ('He is 5\'10" tall, she isn\'t.', '<p>He is 5\'10" tall, she isn\'t.</p>'),
    ('Type `say "hi"` now.', '<p>Type <code>say "hi"</code> now.</p>'),
    ("‘British,’ she said.", "<p>‘British,’ she said.</p>"),
    # code in a quote: the quote goes on after it
    ('"Run `ls`, then go."', f'<p>{S}"Run {E}<code>ls</code>{S}, then go."{E}</p>'),
    # text stays escaped, attributes untouched
    ('"a < b & c"', f'<p>{S}"a &lt; b &amp; c"{E}</p>'),
    ('![a "b"](https://x/p.png) [say "hi"](https://x)',
     f'<p><img src="https://x/p.png" alt="a &quot;b&quot;"> '
     f'<a href="https://x" rel="noopener noreferrer nofollow">say {S}"hi"{E}</a></p>'),
    ('No dialogue here.', '<p>No dialogue here.</p>'),
])
def test_dialogue_is_marked(markdown, html):
    assert shown(markdown) == html


def test_cells_and_items_stop_quotes():
    assert mark_dialogue('<ul><li>"one</li><li>two</li></ul>') == f'<ul><li>{S}"one{E}</li><li>two</li></ul>'
    assert mark_dialogue('<table><tr><td>"a</td><td>b</td></tr></table>') == \
        f'<table><tr><td>{S}"a{E}</td><td>b</td></tr></table>'


def test_a_message_cannot_fake_the_mark():
    # Its own classes are removed when it's cleaned: only the app's spans are left
    assert shown('<span class="dialogue">x</span> <b class="fixed">y</b>') == '<p><span>x</span> <b>y</b></p>'


@pytest.mark.parametrize('accent', theme.ACCENTS)
def test_dialogue_reads_on_both_bubbles(accent):
    _, light, dark = theme.ACCENTS[accent]
    css = theme.accent_css(accent)
    for color, surface, page, alpha in ((light, theme.LIGHT_SURFACE, theme.LIGHT_PAGE, theme.LIGHT_TINT),
                                        (dark, theme.DARK_SURFACE, theme.DARK_PAGE, theme.DARK_TINT)):
        dialogue = theme.readable(color, surface, theme.tinted(color, alpha, page))
        assert f'--dialogue: {dialogue};' in css
        assert theme.contrast(dialogue, surface) >= 4.5  # the character's bubble
        assert theme.contrast(dialogue, theme.tinted(color, alpha, page)) >= 4.5  # yours


def test_readable_changes_only_what_it_must():
    assert theme.readable('#2B5CB8', '#FFFFFF') == '#2B5CB8'  # already fine
    assert theme.readable('#1F7A70', '#FFFFFF', theme.tinted('#1F7A70', .10, '#F3F5F7')) == '#1D746A'
    assert theme.contrast('#000000', '#FFFFFF') == pytest.approx(21)


async def test_the_chat_shows_dialogue(tmp_path):
    characters, personas = CharacterStore(tmp_path), PersonaStore(tmp_path)
    character, persona = new_character('Hale'), new_persona('Mira')
    characters.save(character)
    personas.save(persona)
    store = ConversationStore(tmp_path)
    conversation = store.start(character, persona)
    conversation.add('assistant', '*He waves.* "Ahoy there."')
    store.save(conversation)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await user.should_see('Ahoy')
        [message] = [e for e in user.find('Ahoy').elements if 'innerHTML' in e.props]
        assert f'{S}"Ahoy there."{E}' in message.props['innerHTML']
