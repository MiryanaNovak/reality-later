"""Which connection and prompt a chat uses (rpapp/chat_setup.py): its own, else its character's, else Settings',
each on its own; and the replies and memories that follow it (replies.py)."""
import asyncio

from conftest import FakeConnection

from rpapp.characters import CharacterStore, new_character
from rpapp.chat_setup import chat_setup, missing_connection
from rpapp.connections import connection_store
from rpapp.conversations import Conversation, ConversationStore, ProfileRef
from rpapp.lorebooks import lore_book_store
from rpapp.personas import PersonaStore, new_persona
from rpapp.presets import PromptPreset, preset_store
from rpapp.replies import Replies
from rpapp.settings import ConnectionRef, PresetRef, PromptChoice, Settings, SettingsStore

FAKE = ConnectionRef(id='fake-1', name='Fake')
OTHER = ConnectionRef(id='other-2', name='Other')
DEFAULT = PresetRef(id='default-3', name='Default')
NOIR = PresetRef(id='noir-4', name='Noir')


def settings_with(connection=FAKE, preset=DEFAULT) -> Settings:
    s = Settings()
    s.chat.connection, s.chat.preset = connection, preset
    return s


def chat(**own) -> Conversation:
    return Conversation(character=ProfileRef(id='seraphina', title='Seraphina'),
                        persona=ProfileRef(id='mira', title='Mira'), **own)


def test_where_each_comes_from():
    settings, character = settings_with(), new_character('Seraphina')
    s = chat_setup(settings, character, chat())
    assert (s.connection, s.connection_from, s.preset, s.preset_from) == (FAKE, 'settings', DEFAULT, 'settings')

    character.connection = OTHER
    character.prompt = PromptChoice(preset=None)  # (no instructions, for this character)
    s = chat_setup(settings, character, chat())
    assert (s.connection, s.connection_from, s.preset, s.preset_from) == (OTHER, 'character', None, 'character')

    s = chat_setup(settings, character, chat(connection=FAKE))  # (the chat's own connection, the character's prompt)
    assert (s.connection, s.connection_from, s.preset, s.preset_from) == (FAKE, 'chat', None, 'character')
    s = chat_setup(settings, character, chat(prompt=PromptChoice(preset=NOIR)))
    assert (s.connection_from, s.preset, s.preset_from) == ('character', NOIR, 'chat')

    assert chat_setup(settings, character) == chat_setup(settings, character, chat())  # (what its chats use)
    s = chat_setup(settings, None, chat(prompt=PromptChoice(preset=None)))  # (the character deleted)
    assert (s.connection_from, s.preset, s.preset_from) == ('settings', None, 'chat')
    assert chat_setup(settings_with(connection=None, preset=None)).connection is None


def test_where_to_choose_another():
    settings, character = settings_with(), new_character('Seraphina')
    assert missing_connection(chat_setup(settings), 'Seraphina') == \
        'The chat connection "Fake" is missing: choose another one in Settings.'
    assert missing_connection(chat_setup(settings_with(connection=None)), 'Seraphina') == \
        'No chat connection is chosen: choose one in Settings.'
    character.connection = OTHER
    assert missing_connection(chat_setup(settings, character), 'Seraphina') == \
        'The connection "Other" chosen for Seraphina is missing: choose another one on their page.'
    assert missing_connection(chat_setup(settings, character, chat(connection=FAKE)), 'Seraphina') == \
        'The connection "Fake" chosen for this chat is missing: choose another one in its settings (⋮ › Settings).'


# --- the replies --------------------------------------------------------------------------------

class Setup:
    def __init__(self, tmp_path) -> None:
        self.characters, self.conversations = CharacterStore(tmp_path), ConversationStore(tmp_path)
        self.character, persona = new_character('Seraphina'), new_persona('Mira')
        self.character.first_messages = ['Hello, {{user}}.']
        self.characters.save(self.character)
        PersonaStore(tmp_path).save(persona)
        self.connections = connection_store(tmp_path)
        self.fake = FakeConnection(name='Fake', model='fake/model')
        self.other = FakeConnection(name='Other', model='other/model')
        for c in (self.fake, self.other):
            self.connections.save(c)
        self.presets = preset_store(tmp_path)
        self.default = PromptPreset(name='Default', instructions='Default instructions.')
        self.noir = PromptPreset(name='Noir', instructions='Noir instructions.')
        for p in (self.default, self.noir):
            self.presets.save(p)
        self.settings = SettingsStore(tmp_path)
        s = self.settings.load()
        s.chat.connection = self.ref(self.fake)
        s.chat.preset = PresetRef(id=self.default.id, name='Default')
        self.settings.save(s)
        self.replies = Replies(self.conversations, characters=self.characters, personas=PersonaStore(tmp_path),
                               lore_books=lore_book_store(tmp_path), settings=self.settings,
                               connections=self.connections, presets=self.presets)
        self.persona = persona

    @staticmethod
    def ref(connection) -> ConnectionRef:
        return ConnectionRef(id=connection.id, name=connection.name)

    def new_chat(self) -> Conversation:
        conversation = self.conversations.start(self.character, self.persona)
        return conversation

    async def reply(self, conversation: Conversation) -> tuple[str, str, str]:
        """A reply to a new message: the connection it came from, and the prompt's name and instructions."""
        c = self.conversations.load(conversation.id)
        message = c.add('user', 'Where am I?')
        self.conversations.save(c)
        job = self.replies.start(c.id)
        done = asyncio.Event()
        job.on_done(lambda _: done.set())
        await asyncio.wait_for(done.wait(), 2)
        assert not job.error, job.error
        prompt = self.conversations.prompts.get(c.id, message.id)
        instructions = next((p.content for p in prompt.parts if p.kind == 'instructions'), '')
        return self.replies.details(c.id, job.message).connection, prompt.report.preset, instructions


async def test_replies_follow_the_chat_then_the_character_then_settings(tmp_path, fake_model):
    s = Setup(tmp_path)
    first = s.new_chat()
    assert await s.reply(first) == ('Fake (fake/model)', 'Default', 'Default instructions.')

    s.character.connection = s.ref(s.other)
    s.character.prompt = PromptChoice(preset=None)  # (no instructions with this character)
    s.characters.save(s.character)
    assert await s.reply(first) == ('Other (other/model)', '', '')

    s.conversations.choose(first.id, connection=s.ref(s.fake), prompt=PromptChoice(preset=PresetRef(
        id=s.noir.id, name='Noir')))
    assert await s.reply(first) == ('Fake (fake/model)', 'Noir', 'Noir instructions.')
    second = s.new_chat()  # (another chat with the same character: the character's)
    assert await s.reply(second) == ('Other (other/model)', '', '')

    before = s.conversations.load(first.id).updated_at
    s.conversations.choose(first.id, connection=None, prompt=None)  # (back to the default: the character's)
    assert s.conversations.load(first.id).updated_at == before  # (not an update: it keeps its place)
    assert await s.reply(first) == ('Other (other/model)', '', '')


async def test_a_missing_connection_says_where(tmp_path, fake_model):
    s = Setup(tmp_path)
    conversation = s.new_chat()
    s.character.connection = ConnectionRef(id='gone-111111', name='Old Claude')
    s.characters.save(s.character)
    c = s.conversations.load(conversation.id)
    c.add('user', 'Hello?')
    s.conversations.save(c)
    job = s.replies.start(c.id)
    done = asyncio.Event()
    job.on_done(lambda _: done.set())
    await asyncio.wait_for(done.wait(), 2)
    assert job.error == 'The connection "Old Claude" chosen for Seraphina is missing: choose another one on their page.'


async def test_a_missing_prompt_is_reported(tmp_path, fake_model):
    s = Setup(tmp_path)
    conversation = s.new_chat()
    s.conversations.choose(conversation.id, connection=None,
                           prompt=PromptChoice(preset=PresetRef(id='gone-222222', name='Old noir')))
    await s.reply(conversation)
    c = s.conversations.load(conversation.id)
    prompt = s.conversations.prompts.get(c.id, c.messages[-2].id)
    assert prompt.report.missing_preset == 'Old noir' and prompt.report.preset == ''


async def test_memories_use_the_chats_connection(tmp_path, fake_model):
    """Settings › Memory's "Same as the chat's connection": this chat's (here, its character's: Settings has
    none, which would stop them)."""
    s = Setup(tmp_path)
    settings = s.settings.load()
    settings.chat.connection = None
    s.settings.save(settings)
    s.character.connection = s.ref(s.other)
    s.characters.save(s.character)
    conversation = s.new_chat()
    await s.reply(conversation)
    job = s.replies.start_memory(conversation.id, manual=True)
    done = asyncio.Event()
    job.on_done(lambda _: done.set())
    await asyncio.wait_for(done.wait(), 2)
    assert not job.error and fake_model.memory_calls
