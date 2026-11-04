"""A long chat opens with its last messages; the earlier ones are drawn as you scroll up (in the browser,
"Earlier messages" is clicked by itself as it comes near: here, the test clicks it)."""
from nicegui import ui
from nicegui.testing import user_simulation

from rpapp.conversations import ChatMessage, ConversationStore
from rpapp.ui.app import build_root
from rpapp.ui.chat_page import FIRST_MESSAGES, MORE_MESSAGES
from test_chat import choose, message_ids, save_cast, send
from test_memory import make_chat, marker_state, proposal


def long_chat(conversations, conversation, count: int) -> list[str]:
    """`count` messages after the greeting (yours first, the AI's last if even); returns all their ids."""
    for i in range(count):
        conversation.messages.append(ChatMessage(role='assistant' if i % 2 else 'user', content=f'Message {i + 1}.'))
    conversations.save(conversation)
    return [m.id for m in conversation.messages]


def drawn(ids: list[str]) -> list[str]:
    return [f'message-{i}' for i in ids]


def started(tmp_path, count: int):
    character, persona = save_cast(tmp_path, '*She waves.*')
    conversations = ConversationStore(tmp_path)
    conversation = conversations.start(character, persona)
    return conversations, conversation, long_chat(conversations, conversation, count)


async def test_it_opens_with_the_last_messages_then_the_earlier_ones(tmp_path, fake_model):
    _, conversation, ids = started(tmp_path, 74)  # 75 messages, with the greeting
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await user.should_see('Message 74.')
        assert message_ids(user) == drawn(ids[-FIRST_MESSAGES:])
        await user.should_see(marker='load-older')
        await user.should_not_see('The start of the chat')
        await user.should_not_see(marker='menu-copy')  # the ⋯ menus are made as they're opened

        user.find(marker='load-older').click()
        assert message_ids(user) == drawn(ids[-FIRST_MESSAGES - MORE_MESSAGES:])  # above, in order
        user.find(marker='load-older').click()
        assert message_ids(user) == drawn(ids)  # down to the greeting
        await user.should_not_see(marker='load-older')
        await user.should_see('The start of the chat')

        # A message drawn later has its menu like any other.
        with user.scope(marker=f'message-{ids[1]}'):
            user.find(marker='actions').click()
            await user.should_see(marker='menu-copy')
            await user.should_see(marker='menu-prompt')


async def test_a_short_chat_is_drawn_whole(tmp_path, fake_model):
    _, conversation, ids = started(tmp_path, 3)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await user.should_see('Message 3.')
        assert message_ids(user) == drawn(ids)
        await user.should_not_see(marker='chat-older')  # no "Earlier messages", nor "The start of the chat"


async def test_new_messages_go_at_the_end(tmp_path, fake_model):
    conversations, conversation, ids = started(tmp_path, 74)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await send(user, 'And now?')
        saved = conversations.load(conversation.id).messages
        assert len(saved) == len(ids) + 2
        assert message_ids(user) == drawn([m.id for m in saved[-FIRST_MESSAGES - 2:]])  # not the earlier ones


async def test_deleting_from_the_first_message_drawn(tmp_path, fake_model):
    conversations, conversation, ids = started(tmp_path, 74)
    first = ids[-FIRST_MESSAGES]
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        with user.scope(marker=f'message-{first}'):
            choose(user, 'menu-delete')
        await user.should_see('This message and the 29 messages after it will be deleted.')
        with user.scope(marker='confirm-dialog'):
            user.find(kind=ui.button, content='Delete').click()
        await user.should_not_see(marker=f'message-{first}')
        assert message_ids(user) == []  # what's left is before what was drawn
        await user.should_see(marker='load-older')  # (in view now: the browser clicks it)
        user.find(marker='load-older').click()
        assert message_ids(user) == drawn(ids[-FIRST_MESSAGES - MORE_MESSAGES:-FIRST_MESSAGES])
        assert [m.id for m in conversations.load(conversation.id).messages] == ids[:-FIRST_MESSAGES]


async def test_show_draws_the_messages_down_to_the_memory_line(tmp_path, fake_model):
    conversations, conversation = make_chat(tmp_path, context=50_000)
    ids = long_chat(conversations, conversation, 74)
    memories = conversations.memories.get(conversation.id)
    memories.propose(ids[0], ids[10], proposal(memories, 'Brann'))  # pending, up to an early message
    conversations.memories.save(memories)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/conversations/{conversation.id}')
        await user.should_see('Memories generated from 11 messages: 1 new.')
        assert marker_state(user) == ('', '', False, False)  # its message isn't drawn: no line yet
        user.find(marker='memory-show').click()
        assert message_ids(user)[0] == f'message-{ids[9]}'  # down to it, and the one before
        assert marker_state(user) == (
            ids[10], 'Memories generated up to here: added with your next message', True, False)
