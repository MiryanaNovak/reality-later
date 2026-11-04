"""The chat: messages in the model, the prompt, the chat page and its replies."""
import asyncio

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

from rpapp.characters import CharacterStore, new_character
from rpapp.connections import Reply, Usage, connection_store
from rpapp.conversations import Conversation, ConversationStore, ProfileRef
from rpapp.lorebooks import lore_book_store, new_lore_book
from rpapp.profiles import LoreBookRef
from rpapp.macros import fill
from rpapp.personas import PersonaStore, new_persona
from rpapp.presets import new_preset, preset_store
from rpapp.prompts import build_prompt
from rpapp.replies import clean
from rpapp.settings import ConnectionRef, PresetRef, Settings, SettingsStore
from rpapp.ui.app import build_root


def seraphina(*greetings: str):
    character = new_character('Seraphina (forest guardian)')
    character.name = 'Seraphina'
    character.description = 'The guardian of a forest glade.'  # only for you: never sent
    character.scenario = 'Guardian of the glade. Protective of {{user}}.\n\n{{user}} wakes up wounded in the glade.'
    character.first_messages = list(greetings) or ['']
    return character


def mira():
    persona = new_persona('Mira, wandering healer')
    persona.name = 'Mira'
    persona.pronouns = 'she/her'
    persona.gender = 'female'
    persona.description = 'A healer who walks the old roads.'
    return persona


# --- the model -------------------------------------------------------------------

def test_macros():
    assert fill('{{char}} looks at {{User}}. <BOT> and <USER>.', char='S', user='M') == 'S looks at M. S and M.'


def test_greetings_are_filled_and_chosen(tmp_path):
    store = ConversationStore(tmp_path)
    c = store.start(seraphina('*{{char}} smiles at {{user}}.*', '', 'Awake, {{user}}?'), mira())
    [greeting] = c.messages
    assert greeting.greeting and greeting.role == 'assistant'
    assert greeting.content == '*Seraphina smiles at Mira.*'
    assert greeting.alternatives == ['*Seraphina smiles at Mira.*', 'Awake, Mira?']  # the empty one is skipped
    assert c.can_choose_greeting
    assert c.choose_greeting(1).content == 'Awake, Mira?'
    assert c.choose_greeting(1).content == '*Seraphina smiles at Mira.*'  # wraps around
    assert c.choose_greeting(-1).content == 'Awake, Mira?'

    c.edit(greeting.id, 'Awake at last, Mira?')
    c.choose_greeting(1)
    assert c.choose_greeting(1).content == 'Awake at last, Mira?'  # the edit is kept

    c.add('user', 'Where am I?')
    assert not c.can_choose_greeting
    with pytest.raises(ValueError):
        c.choose_greeting(1)


def test_single_or_no_greeting(tmp_path):
    store = ConversationStore(tmp_path)
    one = store.start(seraphina('Hello, {{user}}.'), mira())
    assert one.messages[0].content == 'Hello, Mira.' and one.messages[0].alternatives == []
    assert not one.can_choose_greeting
    assert store.start(seraphina(), mira()).messages == []


def test_delete_from(tmp_path):
    c = ConversationStore(tmp_path).start(seraphina('Hello.'), mira())
    first = c.add('user', 'one')
    c.add('assistant', 'two')
    c.add('user', 'three')
    with pytest.raises(ValueError):
        c.delete_from(c.messages[0].id)  # the greeting stays
    deleted = c.delete_from(first.id)
    assert [m.content for m in deleted] == ['one', 'two', 'three'] and len(c.messages) == 1



def test_reply_versions(tmp_path):
    c = ConversationStore(tmp_path).start(seraphina('Hello.'), mira())
    assert not c.can_change_reply  # the greeting has its own choice
    c.add('user', 'Hi.')
    reply = c.add('assistant', 'First.')
    assert c.can_change_reply and reply.versions == 1
    c.add_version(reply.id, 'Second.')
    assert (reply.alternatives, reply.alternative, reply.content, reply.versions) == (['First.', 'Second.'], 1, 'Second.', 2)
    assert c.choose_reply(0).content == 'First.'
    with pytest.raises(IndexError):
        c.choose_reply(2)
    c.edit(reply.id, 'First, edited.')
    c.choose_reply(1)
    assert c.choose_reply(0).content == 'First, edited.'  # the edit is kept
    c.add('user', 'Thanks.')
    assert not c.can_change_reply
    with pytest.raises(ValueError):
        c.choose_reply(1)


def test_first_version_reply_details_still_load():
    from rpapp.replies import ReplyRecord
    old = ReplyRecord.model_validate({'id': 'abc', 'format_version': 1, 'connection': 'Fake (fake/model)',
                                      'usage': {'prompt_tokens': 12}, 'reply_to': 'xyz'})
    assert old.id == 'abc' and old.get(0).usage.prompt_tokens == 12 and old.get(1) is None

def test_prompt():
    character, persona = seraphina('Hello, Mira.'), mira()
    c = Conversation(title='t', character=ProfileRef.to(character), persona=ProfileRef.to(persona))
    c.add('assistant', 'Hello, Mira.')
    c.add('user', 'Where am I?')
    settings = Settings()
    preset = new_preset('Careful')
    preset.instructions = 'You are {{char}}. Never speak for {{user}}.'
    preset.final_instructions = 'Write only what {{char}} says and does.'
    prompt = build_prompt(c, character=character, persona=persona, settings=settings, preset=preset)
    assert [(p.kind, p.role) for p in prompt.parts] == [
        ('instructions', 'system'), ('scenario', 'system'), ('persona', 'system'),
        ('start', 'user'), ('message', 'assistant'), ('message', 'user'), ('final', 'system')]
    messages = prompt.messages()
    assert [m.role for m in messages] == ['system', 'user', 'assistant', 'user', 'system']
    assert messages[0].content == (
        'You are Seraphina. Never speak for Mira.\n\n'
        'About Seraphina and the story:\nGuardian of the glade. Protective of Mira.\n\n'
        'Mira wakes up wounded in the glade.\n\n'
        'About Mira, played by the user:\nGender: female\nPronouns: she/her\nA healer who walks the old roads.'
    )
    assert 'forest glade' not in str(prompt.messages())  # the description is only for you
    assert messages[1].content == '[Start of the chat]'
    assert messages[-1].content == 'Write only what Seraphina says and does.'
    # Deleted character and persona: their remembered titles stand in for the names.
    preset.instructions = '{{char}} and {{user}}'
    bare = build_prompt(c, character=None, persona=None, settings=settings, preset=preset)
    assert bare.parts[0].content == 'Seraphina (forest guardian) and Mira, wandering healer'


# --- the page --------------------------------------------------------------------

def save_cast(tmp_path, *greetings, connection: bool = True):
    """Seraphina, Mira and the settings saved; with `connection`, the fake model is the chat connection."""
    characters, personas = CharacterStore(tmp_path), PersonaStore(tmp_path)
    character, persona = seraphina(*greetings), mira()
    characters.save(character)
    personas.save(persona)
    settings = SettingsStore(tmp_path)
    s = settings.load()
    preset = new_preset('Test')
    preset.instructions = 'You are {{char}}.'
    preset_store(tmp_path).save(preset)
    s.chat.preset = PresetRef(id=preset.id, name=preset.name)
    if connection:
        from conftest import FakeConnection
        conn = FakeConnection(name='Fake', model='fake/model', context_window=8000)
        connection_store(tmp_path).save(conn)
        s.chat.connection = ConnectionRef(id=conn.id, name=conn.name)
    settings.save(s)
    return character, persona


@pytest.fixture
async def setup(tmp_path, fake_model):
    character, persona = save_cast(tmp_path, '*{{char}} kneels beside {{user}}.*',
                                   '*The door creaks.* "Awake, {{user}}?"')
    conversations = ConversationStore(tmp_path)
    conversation = conversations.start(character, persona)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        yield user, conversations, conversation


def message_ids(user) -> list[str]:
    """Message markers, in page order."""
    return [m for e in user.client.layout.descendants() for m in e._markers if m.startswith('message-')]


def the(user, marker: str):
    return user.find(marker=marker).elements.pop()


def choose(user, item: str) -> None:
    """In a message's scope: open its ⋯ menu (made as it opens), and click that item."""
    user.find(marker='actions').click()
    user.find(marker=item).click()


async def send(user, text: str, *, wait: bool = True) -> None:
    """Type and send; with `wait`, until the reply (or the reason there's none) is in."""
    user.find(marker='chat-input').type(text)
    user.find(marker='send').click()
    if wait:
        await settled(user)


async def settled(user) -> None:
    for _ in range(100):
        await asyncio.sleep(0.03)
        if the(user, 'chat-input').enabled:
            return
    raise AssertionError('still waiting for the reply')


async def test_greeting_choice_until_the_first_reply(setup):
    user, store, conversation = setup
    greeting = conversation.messages[0].id
    await user.should_see('*Seraphina kneels beside Mira.*')
    await user.should_see('1 / 2')
    user.find(marker='greeting-next').click()
    await user.should_see('2 / 2')
    assert store.load(conversation.id).messages[0].content == '*The door creaks.* "Awake, Mira?"'

    await send(user, 'Where am I?')
    await user.should_see('Where am I?')
    await user.should_not_see(marker='greeting-next')  # fixed now
    assert message_ids(user)[0] == f'message-{greeting}'
    with user.scope(marker=f'message-{greeting}'):
        user.find(marker='actions').click()
        await user.should_see(marker='menu-edit')
        await user.should_not_see(marker='menu-delete')  # the first message can't be deleted


async def test_send_gets_a_reply(setup, fake_model):
    user, store, conversation = setup
    await send(user, 'Where am I?\nAnd who are you?')
    await user.should_see('*Seraphina smiles.* Rest now.')
    saved = store.load(conversation.id)
    assert [(m.role, m.content) for m in saved.messages[1:]] == [
        ('user', 'Where am I?\nAnd who are you?'), ('assistant', '*Seraphina smiles.* Rest now.')]
    assert the(user, 'chat-input').value == '' and the(user, 'chat-input').enabled
    await user.should_not_see(marker='replying')
    await user.should_not_see(marker='reply-status')

    mine, reply = saved.messages[1:]
    prompt = store.prompts.get(conversation.id, mine.id)
    assert [m.role for m in prompt.messages()] == ['system', 'user', 'assistant', 'user']
    assert fake_model.calls == [prompt.messages()]  # what was sent is what was saved
    assert prompt.connection == 'Fake (fake/model)'
    [details] = store.replies.get(conversation.id, reply.id).versions
    assert details.reply_to == mine.id and details.prompt_estimate == prompt.tokens

    with user.scope(marker=f'message-{mine.id}'):
        choose(user, 'menu-prompt')
    await user.should_see(marker='prompt-dialog')
    with user.scope(marker='prompt-dialog'):
        await user.should_see('Instructions (Test) · system')
        await user.should_see('for Fake (fake/model)')

    await send(user, '   ', wait=False)  # blank: nothing is sent
    assert len(store.load(conversation.id).messages) == 3


async def test_locked_while_replying_and_stop(setup, fake_model):
    user, store, conversation = setup
    fake_model.gate = asyncio.Event()  # the model takes its time
    await send(user, 'Where am I?', wait=False)
    await user.should_see(marker='replying')
    await user.should_see('Replying')
    assert not the(user, 'chat-input').enabled
    assert the(user, 'send').props['icon'] == 'stop'
    mine = store.load(conversation.id).messages[1]
    with user.scope(marker=f'message-{mine.id}'):
        choose(user, 'menu-edit')
    await user.should_see('Wait for the reply, or stop it first.')

    user.find(marker='send').click()  # now Stop
    await user.should_see('You stopped the reply.')
    assert the(user, 'chat-input').enabled and the(user, 'send').props['icon'] == 'send'
    assert len(store.load(conversation.id).messages) == 2  # no reply

    fake_model.gate = None
    user.find(marker='retry').click()
    await user.should_see('*Seraphina smiles.* Rest now.')
    await user.should_not_see(marker='reply-status')


async def test_error_then_retry(setup, fake_model):
    user, store, conversation = setup
    fake_model.error = '402: Insufficient credits (the account is out of credits)'
    await send(user, 'Where am I?')
    await user.should_see('No reply: 402: Insufficient credits (the account is out of credits)')
    fake_model.error = ''
    user.find(marker='retry').click()
    await user.should_see('Rest now.')
    assert len(fake_model.calls) == 2 and len(store.load(conversation.id).messages) == 3


async def test_reply_details(setup, fake_model):
    user, store, conversation = setup
    fake_model.replies = [Reply(
        text='Seraphina: *She smiles.* Rest.', reasoning='She is hurt; be gentle.', model='deepseek/deepseek-r1',
        provider='DeepInfra', finish_reason='length', request_id='gen-123',
        usage=Usage(prompt_tokens=120, completion_tokens=300, cached_tokens=64, reasoning_tokens=100, cost=0.00042))]
    await send(user, 'Where am I?')
    await user.should_see('The reply was cut off at the max reply length')
    reply = store.load(conversation.id).messages[-1]
    assert reply.content == '*She smiles.* Rest.'  # without the "Seraphina:" the model added

    with user.scope(marker=f'message-{reply.id}'):
        user.find(marker='actions').click()
        await user.should_not_see(marker='menu-prompt')
        user.find(marker='menu-details').click()
    await user.should_see(marker='reply-dialog')
    with user.scope(marker='reply-dialog'):
        await user.should_see('Written by deepseek/deepseek-r1, run by DeepInfra, through the connection '
                              'Fake (fake/model)')
        await user.should_see(marker='reply-cut-off')
        await user.should_see('120 tokens (estimated')
        await user.should_see(', 64 of them from the cache')
        await user.should_see('300 tokens, 100 of them thinking')
        await user.should_see('$0.00042')
        await user.should_see('She is hurt; be gentle.')
        await user.should_see('Request id: gen-123')
        user.find(marker='reply-prompt').click()
    await user.should_see(marker='prompt-dialog')


async def test_no_connection_chosen(tmp_path, fake_model):
    character, persona = save_cast(tmp_path, 'Hello.', connection=False)
    conversations = ConversationStore(tmp_path)
    conversation = conversations.start(character, persona)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await send(user, 'Hi.')
        await user.should_see('No reply: No chat connection is chosen: choose one in Settings.')
        with user.scope(marker='reply-status'):
            await user.should_see(kind=ui.link)
        mine = conversations.load(conversation.id).messages[1]
        assert conversations.prompts.get(conversation.id, mine.id).connection == ''  # built, not sent
        assert fake_model.calls == []


async def test_reply_arrives_after_leaving_the_page(setup, fake_model):
    user, store, conversation = setup
    fake_model.gate = asyncio.Event()
    await send(user, 'Where am I?', wait=False)
    await user.should_see(marker='replying')
    await user.open('/characters')
    await user.open(f'/conversations/{conversation.id}')
    await user.should_see(marker='replying')  # still on its way
    fake_model.gate.set()
    await user.should_see('Rest now.')
    await user.open('/characters')
    await user.open(f'/conversations/{conversation.id}')
    await user.should_see('Rest now.')
    assert len(store.load(conversation.id).messages) == 3


async def test_edit_in_place(setup):
    user, store, conversation = setup
    await send(user, 'Where am I?')
    mine = store.load(conversation.id).messages[1]
    with user.scope(marker=f'message-{mine.id}'):
        choose(user, 'menu-edit')
    await user.should_see(marker='edit-input')
    with user.scope(marker=f'message-{mine.id}'):
        user.find(marker='edit-input').clear().type('Where *exactly* am I?')
        user.find(marker='edit-save').click()
    await user.should_not_see(marker='edit-input')
    saved = store.load(conversation.id).messages[1]
    assert saved.content == 'Where *exactly* am I?' and saved.edited_at is not None
    with user.scope(marker=f'message-{mine.id}'):
        await user.should_see(', edited')

    # The prompt dialog says it predates the edit.
    with user.scope(marker=f'message-{mine.id}'):
        choose(user, 'menu-prompt')
    await user.should_see('this is the prompt as it was before')


async def test_delete_removes_the_message_and_those_after(setup):
    user, store, conversation = setup
    for text in ('one', 'two', 'three'):
        await send(user, text)
    one, r1, two, r2, three, r3 = store.load(conversation.id).messages[1:]
    with user.scope(marker=f'message-{two.id}'):
        choose(user, 'menu-delete')
    await user.should_see('This message and the 3 messages after it will be deleted.')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Delete').click()
    await user.should_not_see(marker=f'message-{r3.id}')
    await user.should_not_see(marker=f'message-{two.id}')
    assert [m.id for m in store.load(conversation.id).messages[1:]] == [one.id, r1.id]
    assert store.prompts.get(conversation.id, two.id) is None and store.prompts.get(conversation.id, one.id)
    assert store.replies.get(conversation.id, r2.id) is None and store.replies.get(conversation.id, r1.id)

    # Delete the last reply: your message waits for one again.
    with user.scope(marker=f'message-{r1.id}'):
        choose(user, 'menu-delete')
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        await user.should_see('This message will be deleted.')
        user.find(kind=ui.button, content='Delete').click()
    await user.should_see("Seraphina hasn't replied to your last message.")
    user.find(marker='retry').click()
    await user.should_see(marker='replying')
    await settled(user)
    assert len(store.load(conversation.id).messages) == 3

    # Back to the greeting alone: its choice is available again.
    with user.scope(marker=f'message-{one.id}'):
        choose(user, 'menu-delete')
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Delete').click()
    await user.should_see(marker='greeting-next')


async def test_copy(setup):
    user, _, conversation = setup
    with user.scope(marker=f'message-{conversation.messages[0].id}'):
        choose(user, 'menu-copy')
    await user.should_see('Copied')


async def test_character_without_first_message(tmp_path, fake_model):
    character, persona = save_cast(tmp_path)
    conversations = ConversationStore(tmp_path)
    conversation = conversations.start(character, persona)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await user.should_see('Seraphina has no first message. Write the first one below.')
        await send(user, 'Hello?')
        await user.should_not_see('has no first message')
        assert [m.content for m in conversations.load(conversation.id).messages] == ['Hello?', '*Seraphina smiles.* Rest now.']
        assert [m.role for m in fake_model.calls[0]] == ['system', 'user']  # no placeholder needed


async def test_prompt_view_shows_lore_and_what_is_sent(tmp_path, fake_model):
    books = lore_book_store(tmp_path)
    eldoria = new_lore_book('Eldoria')
    eldoria.add_entry(comment='The forest', key=['forest'], content='Shadowfangs roam the old forest.')
    eldoria.add_entry(comment='Shadowfangs', key=['shadowfang'], content='Beasts of the dark.', position=4, depth=0)
    books.save(eldoria)
    character, persona = save_cast(tmp_path, 'Welcome, {{user}}.')
    character.lorebooks = [LoreBookRef(id=eldoria.id, name='Eldoria'), LoreBookRef(id='gone-000000', name='Old')]
    CharacterStore(tmp_path).save(character)
    conversations = ConversationStore(tmp_path)
    conversation = conversations.start(character, persona)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await send(user, 'Is this forest dangerous?')
        mine = conversations.load(conversation.id).messages[1]
        with user.scope(marker=f'message-{mine.id}'):
            choose(user, 'menu-prompt')
        await user.should_see(marker='prompt-dialog')
        with user.scope(marker='prompt-lore'):
            await user.should_see('Eldoria › The forest')
            await user.should_see('Keyword "forest" in the chat. Placed with the background')
            await user.should_see('Keyword "shadowfang" in entry "The forest". Placed in the chat, after the last '
                                  'message, as system')
        await user.should_see('Lore book "Old" is missing')
        await user.should_see('Lore: Eldoria › The forest · system')
        await user.should_not_see(marker='prompt-message-0')

        user.find(marker='prompt-view').elements.pop().set_value('sent')
        await user.should_see(marker='prompt-message-0')
        await user.should_not_see(marker='prompt-part-0')
        prompt = conversations.prompts.get(conversation.id, mine.id)
        assert [m.role for m in prompt.messages()] == ['system', 'user', 'assistant', 'user', 'system']
        assert prompt.messages()[-1].content == 'Beasts of the dark.'


def test_clean():
    assert clean('  Seraphina: *nods*  ', 'Seraphina') == '*nods*'
    assert clean('seraphina:hi', 'Seraphina') == 'hi'
    assert clean('Seraphinas are rare.', 'Seraphina') == 'Seraphinas are rare.'



async def test_other_versions_of_the_last_reply(setup, fake_model):
    user, store, conversation = setup
    fake_model.replies = [Reply(text='First reply.'), Reply(text='Second reply.'), Reply(text='Third reply.')]
    await send(user, 'Where am I?')
    reply = store.load(conversation.id).messages[-1]
    with user.scope(marker=f'message-{reply.id}'):
        await user.should_see('First reply.')
        await user.should_see('1 / 1')
        assert not the(user, 'reply-previous').enabled
        user.find(marker='reply-next').click()  # asks for another one
    await settled(user)
    with user.scope(marker=f'message-{reply.id}'):
        await user.should_see('Second reply.')
        await user.should_see('2 / 2')
        user.find(marker='reply-previous').click()
        await user.should_see('First reply.')
        await user.should_see('1 / 2')
        user.find(marker='reply-next').click()  # back to the second: nothing asked
        await user.should_see('2 / 2')
    assert len(fake_model.calls) == 2
    # The new version was written from the same prompt, without the reply it replaces.
    assert fake_model.calls[1] == fake_model.calls[0]

    with user.scope(marker=f'message-{reply.id}'):
        user.find(marker='reply-next').click()
    await settled(user)
    with user.scope(marker=f'message-{reply.id}'):
        await user.should_see('Third reply.')
        await user.should_see('3 / 3')
    saved = store.load(conversation.id).messages[-1]
    assert saved.alternatives == ['First reply.', 'Second reply.', 'Third reply.'] and saved.alternative == 2
    assert len(store.replies.get(conversation.id, reply.id).versions) == 3

    with user.scope(marker=f'message-{reply.id}'):
        choose(user, 'menu-details')
    await user.should_see('Version 3 of 3')

    # Once you answer, the reply is fixed.
    await send(user, 'Thanks.')
    with user.scope(marker=f'message-{reply.id}'):
        await user.should_not_see(marker='reply-count')


async def test_stopping_another_version(setup, fake_model):
    user, store, conversation = setup
    await send(user, 'Where am I?')
    reply = store.load(conversation.id).messages[-1]
    fake_model.gate = asyncio.Event()
    with user.scope(marker=f'message-{reply.id}'):
        user.find(marker='reply-next').click()
        await user.should_see('Replying')  # in the reply itself
        await user.should_not_see('Rest now.')
    await user.should_not_see(marker='replying')
    assert the(user, 'send').props['icon'] == 'stop'
    user.find(marker='send').click()
    await user.should_see('You stopped the new reply.')
    with user.scope(marker=f'message-{reply.id}'):
        await user.should_see('Rest now.')  # the version shown before is back
        await user.should_see('1 / 1')
    fake_model.gate = None
    fake_model.replies = [Reply(text='Another one.')]
    user.find(marker='retry').click()
    await settled(user)
    with user.scope(marker=f'message-{reply.id}'):
        await user.should_see('Another one.')
        await user.should_see('2 / 2')
    await user.should_not_see(marker='reply-status')


async def test_deleting_brings_the_choice_back(setup, fake_model):
    user, store, conversation = setup
    await send(user, 'one')
    await send(user, 'two')
    one, r1, two, r2 = store.load(conversation.id).messages[1:]
    with user.scope(marker=f'message-{r1.id}'):
        await user.should_not_see(marker='reply-count')
    with user.scope(marker=f'message-{two.id}'):
        choose(user, 'menu-delete')
    await user.should_see(marker='confirm-dialog')
    with user.scope(marker='confirm-dialog'):
        user.find(kind=ui.button, content='Delete').click()
    await user.should_not_see(marker=f'message-{two.id}')
    with user.scope(marker=f'message-{r1.id}'):
        await user.should_see('1 / 1')


async def test_return_key_sends_on_computers_by_default(setup):
    user, store, conversation = setup
    field = the(user, 'chat-input')
    [keydown] = [listener for listener in field._event_listeners.values() if listener.type == 'keydown']
    assert '(touch ? false : true)' in keydown.js_handler  # phones: a new line; computers: send
    assert field.props['enterkeyhint'] == 'enter'
    assert the(user, 'send').default_slot.children[0].text == 'Send (Return)'

    user.find(marker='chat-input').type('Hello?')
    user.find(marker='chat-input').trigger('keydown')
    await settled(user)
    # the text the browser sends with the key wins over what the server has (it may lag behind)
    user.find(marker='chat-input').type('Hello')
    user.find(marker='chat-input').trigger('keydown', 'Hello again')
    await settled(user)
    assert [m.content for m in store.load(conversation.id).messages if m.role == 'user'] == ['Hello?', 'Hello again']
    assert field.value == ''


async def test_return_key_settings_reach_the_chat(setup, tmp_path):
    user, store, conversation = setup
    settings_store = SettingsStore(tmp_path)
    settings = settings_store.load()
    settings.chat.return_key_desktop, settings.chat.return_key_mobile = 'new_line', 'send'
    settings_store.save(settings)
    await user.open(f'/conversations/{conversation.id}')
    field = the(user, 'chat-input')
    [keydown] = [listener for listener in field._event_listeners.values() if listener.type == 'keydown']
    assert '(touch ? true : false)' in keydown.js_handler
    assert 'e.shiftKey) return' in keydown.js_handler and 'e.ctrlKey || e.metaKey' in keydown.js_handler
    assert field.props['enterkeyhint'] == 'send'  # phones' keyboards show "Send"
    assert the(user, 'send').default_slot.children[0].text == 'Send (Ctrl+Return)'


async def test_send_with_nothing_written_asks_what_to_do(setup, fake_model):
    user, store, conversation = setup
    await user.should_see('*Seraphina kneels beside Mira.*')
    for text in ('', '   \n  '):
        user.find(marker='chat-input').type(text)
        user.find(marker='send').click()
        assert the(user, 'send-menu').value  # the menu is open
        await user.should_see('Extend the last reply')
        await user.should_see('Continue the scene')
        assert len(store.load(conversation.id).messages) == 1  # nothing sent, no reply asked for
        assert the(user, 'chat-input').enabled
        the(user, 'send-menu').close()
    # only the greeting so far: nothing to extend
    assert not the(user, 'extend-reply').enabled and the(user, 'continue-scene').enabled
    await user.should_see("There's no reply from the AI yet")

    await send(user, 'Where am I?')
    user.find(marker='chat-input').type('')
    user.find(marker='send').click()
    assert the(user, 'extend-reply').enabled  # the AI's reply is the last message now
    the(user, 'send-menu').close()
    assert len(store.load(conversation.id).messages) == 3  # the greeting, the question, the reply


async def test_nothing_to_extend_after_your_own_message(setup, fake_model):
    user, store, conversation = setup
    conversation = store.load(conversation.id)
    conversation.add('user', 'Hello?')
    store.save(conversation)
    await user.open(f'/conversations/{conversation.id}')
    await user.should_see('Hello?')
    user.find(marker='send').click()
    assert not the(user, 'extend-reply').enabled
    await user.should_see('The last message is yours')
