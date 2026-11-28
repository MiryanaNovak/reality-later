"""Reformatting a character's first messages and example dialogues to your message format (reformat.py, its review:
ui/reformat_view.py): only the marks may change, you choose what's applied, and it can be undone."""
import asyncio
import json

import pytest
from conftest import FakeConnection
from nicegui import ui
from nicegui.testing import user_simulation
from test_prompts import cast, chat

from nicegui.elements.upload_files import SmallFileUpload
from rpapp.characters import CharacterStore, new_character
from rpapp.connections import ModelError, Reply, connection_store
from rpapp.presets import PromptPreset
from rpapp.prompts import build_prompt
from rpapp.reformat import DEFAULT_RULES, INSTRUCTIONS, _unwrapped, check, follows, format_connection, reformat, \
    to_reformat
from rpapp.settings import ConnectionRef, Settings, SettingsStore
from rpapp.ui.app import build_root

PLAIN = 'She looks up as you step into the glade. "You\'re hurt," she says. Who is this?'
STYLED = '*She looks up as you step into the glade.* "You\'re hurt," *she says.* `Who is this?`'
EXAMPLE = '{{user}}: Who are you?\n{{char}}: She tilts her head. The guardian.'
EXAMPLE_STYLED = '{{user}}: "Who are you?"\n{{char}}: *She tilts her head.* "The guardian."'


# --- the rules' structure ------------------------------------------------------------------------------------

def test_what_follows_the_default_rules():
    assert follows(STYLED) and follows(EXAMPLE_STYLED)
    assert follows('*She waits.* \'Meet me at noon.\' **BANG**\n\n"Hello," *she says.*\n<START>')
    assert not follows(PLAIN) and not follows(EXAMPLE)  # (text outside the marks)
    assert not follows('*She says "hi" softly.*')       # (nested)
    assert not follows('*She thinks `odd` and leaves.*')
    assert not follows('*She sighs.*`Odd.`')             # (a * touching a backtick)
    assert not follows('*She walks\nto the door.*')     # (across lines)
    assert to_reformat([STYLED, PLAIN, '  ', EXAMPLE], Settings()) == [1, 3]
    mine = Settings()
    mine.format.rules = '- narration in _underscores_'
    assert to_reformat([STYLED, PLAIN, ''], mine) == [0, 1]  # (only the model can tell: all but the empty one)


# --- checking an answer ---------------------------------------------------------------------------------

def test_only_the_marks_may_change():
    assert check(PLAIN, STYLED) == ''
    assert check('she smiles. "Don\'t go."', '*She smiles.* "Don’t go."') == ''  # (capitals, curly apostrophes)
    assert check(EXAMPLE, EXAMPLE_STYLED) == ''
    assert check(PLAIN, '*She glances up as you enter the glade.* "You\'re hurt." `Who is this?`') == \
        'it changed the words: "looks" became "glances"; "step into" became "enter"; and more'
    assert check('She smiles warmly.', '*She smiles.*') == 'it changed the words: "warmly" was left out'
    assert check(EXAMPLE, EXAMPLE_STYLED.replace('{{char}}', 'Seraphina')) == \
        'it changed the placeholders ({{char}}, {{user}}...) or the <START> lines'
    # What models add around an answer sometimes: taken off
    assert _unwrapped('```\n*She smiles.*\n```', 'She smiles.') == '*She smiles.*'
    assert _unwrapped('Here is the reformatted text:\n*She smiles.*', 'She smiles.') == '*She smiles.*'
    assert _unwrapped('Note: *She smiles.*', 'Note: she smiles.') == 'Note: *She smiles.*'  # (its own words)


class Canned:
    """A connection answering what it's given."""

    def __init__(self, answer: str = '', error: str = '') -> None:
        self.answer, self.error, self.sent = answer, error, []
        self.params = type('P', (), {'stop': ['\nUser:']})()

    def model_copy(self, deep: bool = False):
        return self

    async def generate(self, messages, params=None):
        self.sent.append((messages, params))
        if self.error:
            raise ModelError(self.error)
        return Reply(text=self.answer)


async def test_a_text_reformatted():
    conn = Canned(STYLED)
    outcome = await reformat(PLAIN, 'first_messages', connection=conn, rules=DEFAULT_RULES)
    assert (outcome.status, outcome.text) == ('changed', STYLED)
    [(messages, params)] = conn.sent
    assert messages[0].content.startswith(INSTRUCTIONS.split('{rules}')[0])
    assert DEFAULT_RULES in messages[0].content and 'a character\'s first message' in messages[0].content
    assert messages[1].content == PLAIN and params.temperature == 0.2 and conn.params.stop == []

    assert (await reformat(STYLED, 'first_messages', connection=Canned(STYLED), rules='x')).status == 'same'
    refused = await reformat(PLAIN, 'first_messages', connection=Canned('*She leaves.*'), rules='x')
    assert refused.status == 'refused' and refused.note.startswith('it changed the words:')
    failed = await reformat(PLAIN, 'first_messages', connection=Canned(error='401: no key'), rules='x')
    assert (failed.status, failed.note) == ('failed', '401: no key')
    empty = await reformat(PLAIN, 'example_dialogues', connection=Canned('  '), rules='x')
    assert (empty.status, empty.note) == ('failed', 'the model gave an empty answer')


def test_which_connection(tmp_path):
    connections = connection_store(tmp_path)
    chat_conn, cheap = FakeConnection(name='Chat'), FakeConnection(name='Cheap')
    connections.save(chat_conn)
    connections.save(cheap)
    settings = Settings()
    assert format_connection(settings, None, connections) == \
        (None, 'No connection to reformat with: choose one in Settings › Message format, or a chat connection.')
    settings.chat.connection = ConnectionRef(id=chat_conn.id, name='Chat')
    assert format_connection(settings, None, connections)[0].name == 'Chat'
    sera = new_character('Seraphina')
    sera.connection = ConnectionRef(id=cheap.id, name='Cheap')
    assert format_connection(settings, sera, connections)[0].name == 'Cheap'  # (its chats')
    settings.format.connection = ConnectionRef(id='gone-1', name='Gone')
    assert format_connection(settings, sera, connections) == \
        (None, 'The reformat connection "Gone" is missing: choose another one in Settings › Message format.')


def test_format_in_a_prompt():
    character, persona = cast()
    preset = PromptPreset(name='P', instructions='Write like this:\n{{format}}')
    settings = Settings()
    prompt = build_prompt(chat('Hi.'), character=character, persona=persona, settings=settings, preset=preset)
    assert prompt.parts[0].content == f'Write like this:\n{DEFAULT_RULES}'
    settings.format.rules = '- narration in _underscores_'
    prompt = build_prompt(chat('Hi.'), character=character, persona=persona, settings=settings, preset=preset)
    assert prompt.parts[0].content == 'Write like this:\n- narration in _underscores_'


# --- on the character's page -----------------------------------------------------------------------------

@pytest.fixture
async def page(tmp_path, fake_model):
    connections = connection_store(tmp_path)
    fake = FakeConnection(name='Fake', model='fake/model')
    connections.save(fake)
    settings = SettingsStore(tmp_path)
    s = settings.load()
    s.chat.connection = ConnectionRef(id=fake.id, name='Fake')
    settings.save(s)
    store = CharacterStore(tmp_path)
    sera = new_character('Seraphina')
    sera.first_messages = [PLAIN, STYLED, 'She waits.']
    sera.example_dialogues = [EXAMPLE]
    store.save(sera)
    async with user_simulation(build_root(tmp_path)) as user:
        yield user, store, sera, settings


def the(user, marker):
    [element] = [e for e in user.client.layout.descendants() if marker in e._markers]
    return element


def found(user, marker) -> bool:
    return any(marker in e._markers for e in user.client.layout.descendants())


async def settled(user):
    for _ in range(60):
        await asyncio.sleep(0.05)
        if the(user, 'reformat-apply').enabled or not found(user, 'reformat-keep') and \
                not any(isinstance(e, ui.spinner) for e in the(user, 'reformat-dialog').descendants()):
            return


async def test_reformat_review_apply_undo(page, fake_model):
    user, store, sera, _ = page
    fake_model.reformatted = {PLAIN: STYLED, 'She waits.': '*She leaves.*'}
    await user.open(f'/characters/{sera.id}/edit')
    user.find(marker='reformat-first_messages').click()
    await user.should_see(marker='reformat-dialog')
    await settled(user)
    assert [m[-1].content for m in fake_model.reformat_calls] == [PLAIN, 'She waits.']  # (the styled one: not sent)
    with user.scope(marker='reformat-first_messages-1'):
        await user.should_see('Follows your message format already.')
    with user.scope(marker='reformat-first_messages-2'):
        await user.should_see('Not offered: it changed the words: "waits" became "leaves".')
    with user.scope(marker='reformat-first_messages-0'):
        await user.should_see('Before')
    assert the(user, 'reformat-apply').text == 'Apply 1'
    user.find(marker='reformat-apply').click()
    await user.should_see('1 text reformatted. Undo is in their section.')
    saved = store.load(sera.id)
    assert saved.first_messages == [STYLED, STYLED, 'She waits.']
    assert [(r.field, r.index, r.before) for r in saved.reformatted] == [('first_messages', 0, PLAIN)]
    await asyncio.sleep(0.05)  # (redrawn: in the background)
    [field] = [e for e in the(user, 'message-0').descendants() if isinstance(e, ui.textarea)]
    assert field.value == STYLED

    user.find(marker='reformat-undo-first_messages').click()
    await user.should_see('1 text put back as before.')
    saved = store.load(sera.id)
    assert saved.first_messages == [PLAIN, STYLED, 'She waits.'] and saved.reformatted == []
    await user.should_not_see(marker='reformat-undo-first_messages')


async def test_undo_leaves_what_was_changed_since(page, fake_model):
    user, store, sera, _ = page
    fake_model.reformatted = {PLAIN: STYLED}
    await user.open(f'/characters/{sera.id}/edit')
    user.find(marker='reformat-first_messages').click()
    await settled(user)
    user.find(marker='reformat-apply').click()
    await user.should_see(marker='reformat-undo-first_messages')
    sera = store.load(sera.id)
    sera.first_messages[0] = STYLED + ' *A pause.*'  # (edited since)
    store.save(sera)
    await user.open(f'/characters/{sera.id}/edit')
    user.find(marker='reformat-undo-first_messages').click()
    await user.should_see('Nothing to put back. 1 text changed since, left as it is.')
    assert store.load(sera.id).first_messages[0] == STYLED + ' *A pause.*'


async def test_unticked_not_applied_and_examples_apart(page, fake_model):
    user, store, sera, _ = page
    fake_model.reformatted = {EXAMPLE: EXAMPLE_STYLED}
    await user.open(f'/characters/{sera.id}/edit')
    user.find(marker='reformat-example_dialogues').click()
    await settled(user)
    assert [m[-1].content for m in fake_model.reformat_calls] == [EXAMPLE]
    assert 'an example dialogue' in fake_model.reformat_calls[0][0].content
    the(user, 'reformat-keep').set_value(False)
    assert the(user, 'reformat-apply').text == 'Apply' and not the(user, 'reformat-apply').enabled
    the(user, 'reformat-keep').set_value(True)
    user.find(marker='reformat-apply').click()
    await user.should_see('1 text reformatted.')
    assert store.load(sera.id).example_dialogues == [EXAMPLE_STYLED]


async def test_without_a_connection(page, fake_model):
    user, store, sera, settings = page
    s = settings.load()
    s.chat.connection = None
    settings.save(s)
    await user.open(f'/characters/{sera.id}/edit')
    user.find(marker='reformat-first_messages').click()
    await user.should_see('No connection to reformat with: choose one in Settings › Message format, or a chat '
                          'connection.')
    assert fake_model.reformat_calls == [] and not the(user, 'reformat-apply').enabled


def card(**fields) -> SmallFileUpload:
    data = {'name': 'Hale', 'description': '', 'personality': '', 'scenario': '', 'first_mes': PLAIN,
            'mes_example': f'<START>\n{EXAMPLE}', **fields}
    content = json.dumps({'spec': 'chara_card_v2', 'spec_version': '2.0', 'data': data}).encode('utf-8')
    return SmallFileUpload(name='hale.json', content_type='application/json', _data=content)


async def test_offered_after_an_import(page, fake_model):
    user, store, _, _ = page
    fake_model.reformatted = {PLAIN: STYLED, EXAMPLE: EXAMPLE_STYLED}
    await user.open('/characters')
    await user.find(ui.upload).elements.pop().handle_uploads([card()])
    await user.should_see('Imported "Hale"')
    await user.should_see(marker='reformat-offer')  # (its editor, opened)
    await user.should_see('Some of its first messages and example dialogues don\'t follow your message format')
    [hale] = [c for c in store.list() if c.title == 'Hale']
    assert hale.reformat_offer
    user.find(marker='reformat-offer-yes').click()
    await user.should_see(marker='reformat-dialog')
    await user.should_see('Reformat first messages and example dialogues')
    await settled(user)
    user.find(marker='reformat-apply').click()
    await user.should_see('2 texts reformatted.')
    hale = store.load(hale.id)
    assert (hale.first_messages, hale.example_dialogues, hale.reformat_offer) == ([STYLED], [EXAMPLE_STYLED], False)
    await user.open(f'/characters/{hale.id}/edit')
    await user.should_see(marker='part-summary')
    assert not found(user, 'reformat-offer')  # (once is enough)

    # Declined: not offered again. Already in your format: not offered at all.
    await user.open('/characters')
    await user.find(ui.upload).elements.pop().handle_uploads([card(name='Wren')])
    await user.should_see(marker='reformat-offer')
    user.find(marker='reformat-offer-no').click()
    [wren] = [c for c in store.list() if c.title == 'Wren']
    assert not store.load(wren.id).reformat_offer
    await user.open('/characters')
    await user.find(ui.upload).elements.pop().handle_uploads([card(name='Ivy', first_mes=STYLED,
                                                                   mes_example=f'<START>\n{EXAMPLE_STYLED}')])
    await user.should_see('Imported "Ivy"')
    await user.should_see(marker='part-summary')
    assert not found(user, 'reformat-offer')
    [ivy] = [c for c in store.list() if c.title == 'Ivy']
    assert not store.load(ivy.id).reformat_offer


async def test_message_format_settings(page):
    user, _, _, settings = page
    await user.open('/settings')
    rules = the(user, 'format-rules')
    assert rules.value == DEFAULT_RULES and found(user, 'format-default')
    rules.set_value('- narration in _underscores_')
    assert settings.load().format.rules == '- narration in _underscores_'
    await user.should_see(marker='format-reset')
    user.find(marker='format-reset').click()
    assert settings.load().format.rules == ''
    assert 'Change only the formatting marks' in the(user, 'format-instructions').text
    assert json.loads(settings.path.read_text(encoding='utf-8'))['format'] == {'connection': None, 'rules': ''}
