"""Hardening: what chat messages may contain, the headers every page gets."""
import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from nicegui.testing import user_simulation

from rpapp.access import Access, AccessGuard, SecurityHeaders
from rpapp.characters import CharacterStore, new_character
from rpapp.conversations import ConversationStore
from rpapp.devices import DeviceRegistry
from rpapp.personas import PersonaStore, new_persona
from rpapp.safe_html import chat_html
from rpapp.ui.app import build_root


# --- chat messages ----------------------------------------------------------------------------------

@pytest.mark.parametrize('html, kept, gone', [
    ('<p>Hi <img src=x onerror="alert(1)"></p><script>alert(2)</script>', '<p>Hi <img src="x"></p>',
     ['onerror', 'script', 'alert']),
    ('<a href="javascript:alert(1)">x</a>', '>x</a>', ['javascript']),
    ('<style>body{background:red}</style><div style="position:fixed" class="fixed inset-0">Fake</div>',
     '<div>Fake</div>', ['style', 'class', 'background']),
    ('<form action="https://evil.example"><input name="key"><button>Send</button></form>', 'Send',
     ['form', 'input', 'button', 'evil']),
    ('<iframe src="https://x"></iframe><svg onload="alert(1)"></svg><object data="x"></object>', '',
     ['iframe', 'svg', 'object']),
    ('<img src="data:text/html,x"><a href="data:text/html,x">d</a>', '>d</a>', ['data:']),
])
def test_chat_text_keeps_only_formatting(html, kept, gone):
    cleaned = chat_html(html)
    assert kept in cleaned
    for word in gone:
        assert word not in cleaned


def test_formatting_survives():
    html = ('<p><em>She smiles.</em> <strong>"Hi."</strong></p><ul><li>one</li></ul><blockquote>q</blockquote>'
            '<details><summary>Spoiler</summary>Hidden</details><img src="https://x/a.png" alt="A">'
            '<img src="data:image/png;base64,AAAA">')
    assert chat_html(html) == html
    link = chat_html('<a href="https://example.com">site</a>')
    assert link == '<a href="https://example.com" rel="noopener noreferrer nofollow">site</a>'


async def test_the_chat_shows_messages_cleaned(tmp_path):
    characters, personas = CharacterStore(tmp_path), PersonaStore(tmp_path)
    character, persona = new_character('Hale'), new_persona('Mira')
    characters.save(character)
    personas.save(persona)
    store = ConversationStore(tmp_path)
    conversation = store.start(character, persona)
    conversation.add('assistant', '*Ahoy.* <style>body{display:none}</style>'
                                  '<div style="position:fixed;inset:0">Enter your API key</div>')
    store.save(conversation)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await user.should_see('Ahoy')
        [shown] = [e for e in user.find('Ahoy').elements if 'innerHTML' in e.props]
        assert 'style' not in shown.props['innerHTML'] and '<em>Ahoy.</em>' in shown.props['innerHTML']
        assert shown.props['sanitize'] is True  # and DOMPurify in the browser, on top


# --- headers --------------------------------------------------------------------------------------

async def test_every_response_gets_the_headers(tmp_path):
    app = FastAPI()

    @app.get('/')
    def home():
        return PlainTextResponse('the app')

    @app.get('/own')
    def own():
        return PlainTextResponse('framed on purpose', headers={'X-Frame-Options': 'SAMEORIGIN'})

    access = Access(DeviceRegistry(tmp_path))
    app.add_middleware(AccessGuard, access=access)
    app.add_middleware(SecurityHeaders)
    transport = httpx.ASGITransport(app=app, client=('127.0.0.1', 5000))
    async with httpx.AsyncClient(transport=transport, base_url='http://localhost:8091') as http:
        page = await http.get('/')
        assert page.headers['x-frame-options'] == 'DENY'
        assert page.headers['content-security-policy'] == "frame-ancestors 'none'"
        assert page.headers['referrer-policy'] == 'same-origin'
        assert page.headers['x-content-type-options'] == 'nosniff'
        assert (await http.get('/own')).headers['x-frame-options'] == 'SAMEORIGIN'  # a response's own wins
    stranger = httpx.ASGITransport(app=app, client=('192.168.1.40', 5000))
    async with httpx.AsyncClient(transport=stranger, base_url='http://192.168.1.20:8090') as http:
        refused = await http.get('/', headers={'accept': 'text/html'})
        assert refused.status_code == 303 and refused.headers['x-frame-options'] == 'DENY'  # refusals too
