"""Continuing the scene: the AI's next message without one from you, with a hidden instruction in your place."""
import asyncio

import pytest
from nicegui.testing import user_simulation

from rpapp.connections import Message, Reply
from rpapp.conversations import Conversation, ConversationStore, ProfileRef
from rpapp.macros import fill
from rpapp.presets import CONTINUE_INSTRUCTION, new_preset, preset_store
from rpapp.prompts import build_prompt
from rpapp.replies import continues_scene
from rpapp.settings import Settings
from rpapp.ui.app import build_root
from test_chat import mira, save_cast, seraphina, settled, the

FIRST = '*Seraphina smiles.* Rest now.'  # the fake model's reply
HIDDEN = fill(CONTINUE_INSTRUCTION, char='Seraphina', user='Mira')


# --- the prompt ---------------------------------------------------------------------------------

def a_chat() -> tuple[Conversation, dict]:
    character, persona = seraphina('Hello, Mira.'), mira()
    c = Conversation(title='t', character=ProfileRef.to(character), persona=ProfileRef.to(persona))
    c.add('assistant', 'Hello, Mira.').greeting = True
    c.add('user', 'Where am I?')
    c.add('assistant', 'In my glade.')
    preset = new_preset('Careful')
    preset.final_instructions = 'Write only what {{char}} says.'
    return c, dict(character=character, persona=persona, settings=Settings(), preset=preset)


def test_the_instruction_stands_in_for_you():
    c, kw = a_chat()
    prompt = build_prompt(c, **kw, continue_scene=True)
    assert [(p.kind, p.role) for p in prompt.parts][-3:] == [
        ('message', 'assistant'), ('final', 'system'), ('continue', 'user')]
    assert prompt.parts[-1].content == HIDDEN and prompt.report.continue_scene
    assert prompt.parts[-1].source == 'Hidden instruction: continue the scene (Careful)'
    assert prompt.messages()[-1] == Message(role='user', content=HIDDEN)
    assert 'Mira stays silent' in HIDDEN and "Seraphina's next message" in HIDDEN

    kw['preset'].continue_instruction = 'Go on, {{char}}.'  # the prompt's own
    assert build_prompt(c, **kw, continue_scene=True).parts[-1].content == 'Go on, Seraphina.'
    assert not build_prompt(c, **kw).report.continue_scene  # (a reply, as before)


def test_two_messages_of_the_ai_in_a_row_are_sent_as_one():
    c, kw = a_chat()
    c.add('assistant', 'She stokes the fire.')
    c.add('user', 'Thank you.')
    messages = build_prompt(c, **kw).messages()
    assert [m.role for m in messages][-4:] == ['user', 'assistant', 'user', 'system']  # (then the final instructions)
    assert messages[-3].content == 'In my glade.\n\nShe stokes the fire.'


def test_which_messages_continue_the_scene():
    c, _ = a_chat()
    c.add('assistant', 'She stokes the fire.')
    assert [continues_scene(c.messages, i) for i in range(4)] == [False, False, False, True]
    first = Conversation(title='t', character=c.character, persona=c.persona)
    first.add('assistant', 'The glade is quiet.')  # the first message, written by the AI
    assert continues_scene(first.messages, 0) and not continues_scene(first.messages, 5)


# --- in the chat --------------------------------------------------------------------------------

async def open_chat(tmp_path, *greetings):
    character, persona = save_cast(tmp_path, *greetings)
    store = ConversationStore(tmp_path)
    return store, store.start(character, persona)


async def menu(user, choice: str) -> None:
    user.find(marker='chat-input').type('')
    user.find(marker='send').click()
    user.find(marker=choice).click()
    await settled(user)


async def test_continue_after_a_reply(tmp_path, fake_model):
    store, conversation = await open_chat(tmp_path, '*{{char}} kneels beside {{user}}.*')
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        user.find(marker='chat-input').type('Where am I?')
        user.find(marker='send').click()
        await settled(user)
        reply = store.load(conversation.id).messages[-1]

        fake_model.replies = [Reply(text='*She stokes the fire.*'), Reply(text='*She hums.*')]
        user.find(marker='send').click()
        await user.should_see('Seraphina goes on, without a message from you')
        user.find(marker='continue-scene').click()
        await settled(user)
        messages = store.load(conversation.id).messages
        assert [(m.role, m.content) for m in messages[2:]] == [('assistant', FIRST),
                                                               ('assistant', '*She stokes the fire.*')]
        assert fake_model.calls[-1][-1] == Message(role='user', content=HIDDEN)
        assert fake_model.calls[-1][-2] == Message(role='assistant', content=FIRST)
        await user.should_not_see('stays silent')  # hidden
        with user.scope(marker=f'message-{reply.id}'):
            await user.should_not_see(marker='reply-count')  # no longer the last reply
        new = messages[-1]
        with user.scope(marker=f'message-{new.id}'):
            await user.should_see('1 / 1')
            user.find(marker='reply-next').click()  # another version: asked for the same way
        await settled(user)
        assert fake_model.calls[-1][-1] == Message(role='user', content=HIDDEN)
        assert fake_model.calls[-1][-2] == Message(role='assistant', content=FIRST)
        new = store.load(conversation.id).messages[-1]
        assert new.alternatives == ['*She stokes the fire.*', '*She hums.*']

        # Its details, and its prompt (kept with them: it follows no message of yours)
        with user.scope(marker=f'message-{new.id}'):
            user.find(marker='menu-details').click()
        with user.scope(marker='reply-dialog'):
            await user.should_see(marker='reply-continues')
            user.find(marker='reply-prompt').click()
        with user.scope(marker='prompt-dialog'):
            await user.should_see(marker='prompt-continue')
            await user.should_see('Hidden instruction: continue the scene (Test) · user')
        assert store.prompts.get(conversation.id, reply.id) is None

        # Your next message: the two in a row go as one
        user.find(marker='chat-input').type('Thank you.')
        user.find(marker='send').click()
        await settled(user)
        assert fake_model.calls[-1][-2:] == [Message(role='assistant', content=f'{FIRST}\n\n*She hums.*'),
                                             Message(role='user', content='Thank you.')]


async def test_continue_when_your_message_is_last_is_a_reply(tmp_path, fake_model):
    store, conversation = await open_chat(tmp_path, '*{{char}} kneels beside {{user}}.*')
    mine = conversation.add('user', 'Hello?')
    store.save(conversation)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        user.find(marker='send').click()
        await user.should_see('Seraphina replies to your last message')
        user.find(marker='continue-scene').click()
        await settled(user)
        assert fake_model.calls[-1][-1] == Message(role='user', content='Hello?')  # nothing hidden
        assert store.load(conversation.id).messages[-1].content == FIRST
        assert store.prompts.get(conversation.id, mine.id) is not None  # saved as for any reply


async def test_continue_in_an_empty_chat_writes_the_first_message(tmp_path, fake_model):
    store, conversation = await open_chat(tmp_path)  # a character with no first message
    assert conversation.messages == []
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await user.should_see('Seraphina has no first message')
        user.find(marker='send').click()
        await user.should_see('Seraphina writes the first message')
        assert not the(user, 'extend-reply').enabled
        user.find(marker='continue-scene').click()
        await settled(user)
        await user.should_not_see('Seraphina has no first message')
        [first] = store.load(conversation.id).messages
        assert first.content == FIRST and not first.greeting
        assert [m.role for m in fake_model.calls[-1]] == ['system', 'user']  # the instruction alone after the rest

        fake_model.replies = [Reply(text='The glade is quiet.'), Reply(text='A bird sings.')]
        user.find(marker='reply-next').click()  # another version of it
        await settled(user)
        assert fake_model.calls[-1][-1] == Message(role='user', content=HIDDEN)
        await menu(user, 'extend-reply')  # and it can be extended
        [first] = store.load(conversation.id).messages
        assert first.alternatives == [FIRST, 'The glade is quiet.', 'The glade is quiet.\n\nA bird sings.']


async def test_a_failed_continuation_says_so(tmp_path, fake_model):
    store, conversation = await open_chat(tmp_path, '*{{char}} kneels beside {{user}}.*')
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        fake_model.error = 'The model is overloaded'
        await menu(user, 'continue-scene')
        await user.should_see('No new message: The model is overloaded')
        assert len(store.load(conversation.id).messages) == 1
        fake_model.error = ''
        user.find(marker='retry').click()
        await settled(user)
        assert [m.content for m in store.load(conversation.id).messages][1:] == [FIRST]
        await user.should_not_see(marker='reply-status')

        fake_model.gate = asyncio.Event()  # stopped while it's written
        user.find(marker='chat-input').type('')
        user.find(marker='send').click()
        user.find(marker='continue-scene').click()
        await user.should_see(marker='replying')
        user.find(marker='send').click()  # (now Stop)
        await settled(user)
        await user.should_see('You stopped the new message.')


async def test_the_instruction_is_editable(tmp_path):
    presets = preset_store(tmp_path)
    preset = new_preset('Mine')
    presets.save(preset)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/prompts/{preset.id}')
        field = the(user, 'continue-instruction')
        assert field.props['placeholder'] == CONTINUE_INSTRUCTION
        field.set_value('Go on.')
        presets.flush()
        assert presets.load(preset.id).continue_instruction == 'Go on.'


@pytest.mark.parametrize('greeting', [True, False])
def test_greetings_never_count_as_continued(greeting):
    c, _ = a_chat()
    c.messages[0].greeting = greeting
    assert continues_scene(c.messages, 0) is (not greeting)
