"""Vector search: memories and lore entries picked by meaning too (vectors.py, embeddings.py, lore.py).

The model is a stand-in here (TopicEmbedder): a text's vector says which of a few topics it's about, so
"a boat to cross the river" is close to "Brann the ferryman" without a word in common... nearly. The real
models are checked by hand (they're downloads); downloading and checking files is tested with a fake server.
"""
import asyncio
import hashlib
import re
import threading

import httpx
import numpy as np
import pytest
from nicegui import ui
from nicegui.testing import user_simulation

from rpapp import vectors as vectors_module
from rpapp.characters import CharacterStore
from rpapp.conversations import ChatMessage, ConversationStore
from rpapp.embeddings import INSTALLED, DownloadError, EmbeddingModel, ModelFile, download, is_installed, uninstall
from rpapp.lore import Closeness, activate
from rpapp.lorebooks import lore_book_store, new_lore_book
from rpapp.memory import Memories
from rpapp.profiles import LoreBookRef
from rpapp.settings import LoreSettings, SettingsStore
from rpapp.ui import app as app_module
from rpapp.vector_files import fingerprint, vector_file
from rpapp.vectors import Vectorizer, closeness, entry_text, lore_texts, memory_texts, topic_text
from test_chat import message_ids, save_cast, send, settled, the
from test_memory import ADD_BRANN, make_chat, proposal

TOPICS = [('river', 'ferry', 'boat', 'crossing', 'ferryman', 'oar'),
          ('fire', 'burned', 'warehouses', 'flames', 'smoke'),
          ('locket', 'silver', 'chapel', 'necklace')]

MODEL = EmbeddingModel(key='topics', name='Topics', languages='English only', multilingual=False, license='MIT',
                       repo='test/topics', revision='r1', pooling='mean', max_tokens=256, threshold=0.5, approx_mb=1,
                       files=[ModelFile(path='model.onnx', size=1, sha256='a'),
                              ModelFile(path='tokenizer.json', size=1, sha256='b')])


class TopicEmbedder:
    """Stands in for a model: which topics a text is about (and a little of "anything else")."""

    def __init__(self) -> None:
        self.model = MODEL
        self.made: list[str] = []                  # texts vectorized as entries, in order
        self.gate: threading.Event | None = None  # if set, vectorizing waits for it (to see "Vectorizing…")

    def embed_pieces(self, text: str, *, kind: str = 'passage') -> np.ndarray:
        if self.gate is not None:
            self.gate.wait(10)
        if kind == 'passage':
            self.made.append(text)
        words = re.findall(r'[a-z]+', text.lower())
        v = np.array([sum(w in topic for w in words) for topic in TOPICS] + [0.2], dtype=np.float32)
        return (v / np.linalg.norm(v))[None, :]


class TopicVectorizer(Vectorizer):
    """The app's vector search, with the stand-in model (always 'downloaded')."""
    instances: list['TopicVectorizer'] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fake = TopicEmbedder()
        self.problem_text = ''
        TopicVectorizer.instances.append(self)

    def problem(self, settings) -> str:
        return self.problem_text

    def model(self, settings) -> EmbeddingModel:
        return MODEL

    async def embedder(self, model) -> TopicEmbedder:
        self._embedder = self.fake
        return self.fake


def vectorizer(tmp_path) -> TopicVectorizer:
    conversations = ConversationStore(tmp_path)
    return TopicVectorizer(tmp_path, tmp_path / 'models', settings=SettingsStore(tmp_path),
                           lore_books=lore_book_store(tmp_path), memories=conversations.memories)


def turn_on(tmp_path, *, memories: bool = True, lore: bool = True) -> None:
    store = SettingsStore(tmp_path)
    s = store.load()
    s.vectors.memories, s.vectors.lore, s.vectors.model = memories, lore, MODEL.key
    store.save(s)


def river_book():
    book = new_lore_book('Velt')
    book.add_entry(comment='Brann the ferryman', key=['Brann'], content='A gruff ferryman on the river.')
    book.add_entry(comment='The harbor fire', key=['harbor'], content='The warehouses burned to the ground.')
    book.add_entry(comment='The kingdom', key=[], content='Velt is a river kingdom.', constant=True)
    return book


# --- what's vectorized --------------------------------------------------------------------------

def test_the_texts():
    book = river_book()
    ferryman, fire, kingdom = book.entries.values()
    assert entry_text(ferryman) == 'Brann the ferryman\nA gruff ferryman on the river.'
    ferryman.content = '{{char}} owes {{user}} a favour.'
    assert entry_text(ferryman) == 'Brann the ferryman\n owes  a favour.'  # (names say nothing of the topic)
    assert topic_text('*Seraphina smiles at Mira.* "Seraphina\'s boat."', ['Seraphina', 'Mira']) == \
        '* smiles at .* " boat."'
    fire.disable = True
    assert lore_texts(book) == {ferryman.uid: entry_text(ferryman)}  # not off, not always in anyway

    memories = Memories(id='c')
    memories.propose('m0', 'm1', proposal(memories, 'Brann'))
    now, ahead, kept = memory_texts(memories)
    assert now == {} and ahead == ['About Brann.'] and kept == []  # (its title is in its text: once is enough)
    memories.apply_pending()
    memories.propose('m2', 'm3', proposal(memories, 'Oona'))
    memories.apply_pending()
    now, ahead, kept = memory_texts(memories)
    assert sorted(now.values()) == ['About Brann.', 'About Oona.'] and ahead == []
    assert kept == ['About Brann.']  # (the memories before the last update: to undo it)


async def test_only_what_isnt_vectorized_yet_is(tmp_path):
    v, book = vectorizer(tmp_path), river_book()
    texts = lore_texts(book)
    made = await v.ensure('lorebooks', book.id, MODEL, texts.values())
    assert sorted(v.fake.made) == sorted(texts.values()) and set(made) == {fingerprint(t) for t in texts.values()}

    v.fake.made.clear()
    await v.ensure('lorebooks', book.id, MODEL, texts.values())
    assert v.fake.made == []  # nothing new
    entry = next(iter(book.entries.values()))
    entry.content = 'A gruff ferryman on the Velt, who never smiles.'
    await v.ensure('lorebooks', book.id, MODEL, lore_texts(book).values())
    assert v.fake.made == [entry_text(entry)]  # that entry alone
    assert v.missing('lorebooks', book.id, MODEL, lore_texts(book).values()) == 0
    # The file keeps the vectors of the texts as they are, not of the old one.
    assert len(v._file('lorebooks', book.id, MODEL).load()) == len(lore_texts(book))


async def test_a_lore_book_serves_every_chat(tmp_path, fake_model):
    store = lore_book_store(tmp_path)
    book = river_book()
    store.save(book)
    character, persona = save_cast(tmp_path)
    character.lorebooks = [LoreBookRef(id=book.id, name=book.name)]
    CharacterStore(tmp_path).save(character)
    conversations = ConversationStore(tmp_path)
    first, second = conversations.start(character, persona), conversations.start(character, persona)
    turn_on(tmp_path)
    v = vectorizer(tmp_path)
    settings = SettingsStore(tmp_path).load()
    for chat in (first, second):
        chat.messages.append(ChatMessage(role='user', content='We need a boat across the river.'))
        meaning = await v.prepare(chat, books=[book], memories=Memories(id=chat.id), settings=settings)
        assert meaning.lore.score(book.id, 0, 2) > 0.9  # the ferryman
    assert len(v.fake.made) == 2  # its two entries, once, for both chats


async def test_deleting_a_lore_book_or_a_chat_deletes_their_vectors(tmp_path, fake_model):
    v, books = vectorizer(tmp_path), lore_book_store(tmp_path)
    book = river_book()
    books.save(book)
    await v.ensure('lorebooks', book.id, MODEL, lore_texts(book).values())
    path = vector_file(tmp_path, MODEL.key, 'lorebooks', book.id)
    assert path.is_file()
    lore_book_store(tmp_path).delete(book.id)  # (any store of the folder)
    assert not path.exists()

    conversations, conversation = make_chat(tmp_path)
    await v.ensure('memories', conversation.id, MODEL, ['About Brann.'])
    path = vector_file(tmp_path, MODEL.key, 'memories', conversation.id)
    assert path.is_file()
    conversations.delete(conversation.id)
    assert not path.exists()


async def test_undoing_memories_needs_no_vectorizing(tmp_path):
    v = vectorizer(tmp_path)
    memories = Memories(id='chat')
    memories.propose('m0', 'm1', proposal(memories, 'Brann'))
    memories.apply_pending()
    later = memories.editable.model_copy(deep=True)
    later.entries['0'].content = 'Brann moved to the coast.'
    memories.propose('m2', 'm3', later)
    memories.apply_pending()

    async def refresh():
        now, ahead, kept = memory_texts(memories)
        await v.ensure('memories', 'chat', MODEL, [*now.values(), *ahead], keep=kept)

    await refresh()
    assert sorted(v.fake.made) == ['Brann moved to the coast.']  # (its older text: not needed now)
    await v.ensure('memories', 'chat', MODEL, ['About Brann.'])  # (as when it was in use)
    await refresh()  # kept: it can come back
    v.fake.made.clear()
    assert memories.forget({'m3'})  # the second update is undone: Brann's first text is back
    await refresh()
    assert v.fake.made == []


# --- picking by meaning -------------------------------------------------------------------------

def test_keywords_first_then_the_closest_up_to_the_limit():
    book = new_lore_book('B')
    for title, key in (('Keyword', 'boat'), ('Close', 'x1'), ('Closer', 'x2'), ('Closest', 'x3'), ('Far', 'x4')):
        book.add_entry(comment=title, key=[key], content=f'{title} text.')
    scores = {(book.id, 1): [0.2, 0.61], (book.id, 2): [0.7, 0.1], (book.id, 3): [0.1, 0.9], (book.id, 4): [0.3, 0.2]}
    result = activate([book], chat=[('Mira', 'The boat.'), ('Sera', 'Yes.')], settings=LoreSettings(), budget=10_000,
                      char='Sera', user='Mira', closeness=Closeness(scores, threshold=0.6, limit=2))
    assert [(a.entry.comment, a.reason) for a in result.activations] == [
        ('Keyword', 'keyword "boat" in the chat'),
        ('Closest', 'close in meaning to the chat (0.90)'),
        ('Closer', 'close in meaning to the chat (0.70)')]  # 'Close' (0.61) is past the limit; 'Far' too far

    # The same budget: by meaning, after the keyword matches.
    small = activate([book], chat=[('Mira', 'The boat.')], settings=LoreSettings(), budget=6, char='Sera',
                     user='Mira', closeness=Closeness(scores, threshold=0.6, limit=2))
    assert [(a.entry.comment, a.status) for a in small.activations] == [
        ('Keyword', 'used'), ('Closest', 'over_budget'), ('Closer', 'over_budget')]


def test_each_entry_says_how_it_may_come_in():
    book = new_lore_book('B')
    both = book.add_entry(comment='Both', key=['boat'], content='Both text.')
    keywords = book.add_entry(comment='Keywords only', key=['boat'], content='Keywords text.')
    meaning = book.add_entry(comment='Meaning only', key=['boat'], content='Meaning text.')
    keywords.set_strategy('keywords')
    meaning.set_strategy('meaning')
    assert set(lore_texts(book)) == {both.uid, meaning.uid}  # (the keywords-only one needs no vector)
    chat = [('Mira', 'The boat.')]

    def picked(closeness):
        result = activate([book], chat=chat, settings=LoreSettings(), budget=10_000, char='S', user='M',
                          closeness=closeness)
        return [(a.entry.comment, a.reason.split(' (')[0]) for a in result.activations], result.notes

    # "boat" is in the chat: the meaning-only entry ignores it
    close = Closeness({(book.id, uid): [0.9] for uid in (both.uid, keywords.uid, meaning.uid)}, 0.5, 5)
    assert picked(close)[0] == [('Both', 'keyword "boat" in the chat'), ('Keywords only', 'keyword "boat" in the chat'),
                                ('Meaning only', 'close in meaning to the chat')]
    chat = [('Mira', 'The river.')]  # no keyword: by meaning, but not the keywords-only one
    assert picked(close)[0] == [('Both', 'close in meaning to the chat'), ('Meaning only', 'close in meaning to the chat')]
    activations, notes = picked(None)  # vector search not used: the meaning-only one can't come in, and it says so
    assert activations == [] and notes == ['1 entry is set to come in only by meaning, but vector search isn\'t used '
                                           'for lore books: it can\'t come in (Settings › Vector search).']


def test_closeness_follows_the_scan_depth():
    c = Closeness({('b', 1): [0.9, 0.2, 0.3]}, threshold=0.5, limit=3)
    assert c.score('b', 1, 1) == pytest.approx(0.3) and c.score('b', 1, 3) == pytest.approx(0.9)
    assert c.score('b', 1, 0) is None and c.score('b', 2, 2) is None
    a, b = np.eye(3, dtype=np.float32)[[0]], np.eye(3, dtype=np.float32)[[0, 1]]
    assert closeness({('b', 1): a}, [b, np.zeros((0, 0), np.float32)]) == {('b', 1): [1.0, 0.0]}


async def test_without_its_model_keywords_only_and_says_why(tmp_path, fake_model):
    turn_on(tmp_path)
    conversations, conversation = make_chat(tmp_path)
    conversation.messages.append(ChatMessage(role='user', content='Hello.'))
    real = Vectorizer(tmp_path, tmp_path / 'models', settings=SettingsStore(tmp_path),
                      lore_books=lore_book_store(tmp_path), memories=conversations.memories)
    settings = SettingsStore(tmp_path).load()
    settings.vectors.model = 'all-minilm-l6-v2'
    meaning = await real.prepare(conversation, books=[river_book()], memories=Memories(id='x'), settings=settings)
    assert meaning.lore is None and meaning.memory is None
    assert meaning.note.startswith('Vector search is on, but its model (all-MiniLM-L6-v2) isn\'t downloaded')
    settings.vectors.lore = False
    assert await real.prepare(conversation, books=[river_book()], memories=Memories(id='x'), settings=settings) is None


# --- in the chat --------------------------------------------------------------------------------

@pytest.fixture
def topic_search(monkeypatch):
    TopicVectorizer.instances.clear()
    monkeypatch.setattr(app_module, 'Vectorizer', TopicVectorizer)
    return TopicVectorizer.instances


async def test_vectorizing_then_replying_and_the_prompt_says_why(tmp_path, fake_model, topic_search):
    books = lore_book_store(tmp_path)
    book = river_book()
    books.save(book)
    character, persona = save_cast(tmp_path, 'Welcome, {{user}}.')
    character.lorebooks = [LoreBookRef(id=book.id, name=book.name)]
    CharacterStore(tmp_path).save(character)
    conversations = ConversationStore(tmp_path)
    conversation = conversations.start(character, persona)
    turn_on(tmp_path)
    async with user_simulation(build_root(tmp_path)) as user:
        [search] = topic_search
        search.fake.gate = threading.Event()
        await user.open(f'/conversations/{conversation.id}')
        await send(user, 'We need a boat to cross the river.', wait=False)
        for _ in range(100):  # the reply waits for vectorizing: it says so
            await asyncio.sleep(0.03)
            label = next((e for e in the(user, 'replying').descendants() if isinstance(e, ui.html)), None)
            if label is not None and label.content.startswith('Vectorizing'):
                break
        else:
            raise AssertionError('no "Vectorizing…"')
        assert fake_model.calls == []
        search.fake.gate.set()
        await settled(user)
        assert len(fake_model.calls) == 1  # then the reply

        mine = conversations.load(conversation.id).messages[1]
        report = conversations.prompts.get(conversation.id, mine.id).report
        assert [(u.entry, u.reason) for u in report.lore] == [
            ('The kingdom', 'always included'), ('Brann the ferryman', 'close in meaning to the chat (1.00)')]
        assert 'A gruff ferryman on the river.' in fake_model.calls[0][0].content

        # Next time, nothing to vectorize but the new messages: no "Vectorizing…".
        search.fake.made.clear()
        await send(user, 'Is the fire still burning?')
        assert search.fake.made == []
        report = conversations.prompts.get(conversation.id, conversations.load(conversation.id).messages[3].id).report
        assert ('The harbor fire', 'close in meaning to the chat (0.99)') in [(u.entry, u.reason) for u in report.lore]


async def test_new_memories_are_vectorized_before_you_post(tmp_path, fake_model, topic_search):
    conversations, conversation = make_chat(tmp_path, context=50_000)
    turn_on(tmp_path, lore=False)
    fake_model.memory_answers = [ADD_BRANN]
    async with user_simulation(build_root(tmp_path)) as user:
        [search] = topic_search
        await user.open(f'/conversations/{conversation.id}')
        await send(user, 'Brann rowed us across.')
        user.find(marker='menu-generate-memories-now').click()
        await settled(user)
        await user.should_see('Memories generated from 2 messages: 1 new.')
        pending = conversations.memories.get(conversation.id).pending.proposed
        [text] = lore_texts(pending).values()
        assert text in search.fake.made  # with the memories, before you post

        search.fake.made.clear()
        await send(user, 'Where does the ferry go?')  # applies them: already vectorized
        assert search.fake.made == []
        report = conversations.prompts.get(conversation.id, conversations.load(conversation.id).messages[3].id).report
        [use] = report.memories
        assert use.reason.startswith('close in meaning to the chat') or use.reason.startswith('keyword')


async def test_without_the_model_the_chat_says_so_once(tmp_path, fake_model, topic_search):
    character, persona = save_cast(tmp_path, 'Welcome.')
    book = river_book()
    lore_book_store(tmp_path).save(book)
    character.lorebooks = [LoreBookRef(id=book.id, name=book.name)]
    CharacterStore(tmp_path).save(character)
    conversations = ConversationStore(tmp_path)
    conversation = conversations.start(character, persona)
    turn_on(tmp_path)
    async with user_simulation(build_root(tmp_path)) as user:
        [search] = topic_search
        search.problem_text = 'its model (Topics) isn\'t downloaded: Settings › Vector search'
        await user.open(f'/conversations/{conversation.id}')
        await send(user, 'A boat?')
        await user.should_see('Vector search is on, but its model (Topics) isn\'t downloaded')
        assert len(fake_model.calls) == 1  # replied anyway, with keywords only
        report = conversations.prompts.get(conversation.id, conversations.load(conversation.id).messages[1].id).report
        assert report.meaning_note.startswith('Vector search is on, but')


# --- downloading --------------------------------------------------------------------------------

def served(files: dict[str, bytes]) -> httpx.AsyncClient:
    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path.split('/resolve/r1/', 1)[-1]
        return httpx.Response(200, content=files[path]) if path in files else httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


async def test_download_checks_every_file(tmp_path):
    files = {'model.onnx': b'the model', 'tokenizer.json': b'{"the": "tokenizer"}'}
    model = MODEL.model_copy(update={'files': [ModelFile(path=p, size=len(b), sha256=hashlib.sha256(b).hexdigest())
                                               for p, b in files.items()]})
    seen = []
    await download(model, tmp_path, client=served(files), on_progress=lambda done, total: seen.append((done, total)))
    assert is_installed(tmp_path, model) and seen[-1] == (len(b'the model') + len(b'{"the": "tokenizer"}'),) * 2
    assert (tmp_path / model.key / 'model.onnx').read_bytes() == b'the model'

    uninstall(tmp_path, model)
    with pytest.raises(DownloadError, match='tokenizer.json isn\'t the expected file'):
        await download(model, tmp_path, client=served({**files, 'tokenizer.json': b'{"something": "else"}'}))
    assert not is_installed(tmp_path, model) and list(tmp_path.iterdir()) == []  # nothing half-installed

    with pytest.raises(DownloadError, match='answered 404'):
        await download(model, tmp_path, client=served({'model.onnx': b'the model'}))
    with pytest.raises(DownloadError, match='fingerprints aren\'t recorded'):
        await download(model.model_copy(update={'revision': ''}), tmp_path, client=served(files))

    # A newer version recorded in the app: what's installed isn't it any more.
    await download(model, tmp_path, client=served(files))
    newer = model.model_copy(update={'files': [model.files[0].model_copy(update={'sha256': 'f' * 64}), model.files[1]]})
    assert is_installed(tmp_path, model) and not is_installed(tmp_path, newer)
    assert (tmp_path / model.key / INSTALLED).is_file()


def test_the_list_of_models():
    from rpapp.embeddings import catalog
    models = catalog()
    assert set(models) == {'all-minilm-l6-v2', 'bge-small-en-v1.5', 'multilingual-e5-small'}
    assert [m.multilingual for m in models.values()] == [False, False, True]
    assert models['all-minilm-l6-v2'].label.startswith('all-MiniLM-L6-v2 · English only · ')
    assert all(m.license in ('Apache-2.0', 'MIT') for m in models.values())


async def test_settings_section(tmp_path, topic_search, monkeypatch):
    from rpapp.embeddings import catalog
    from rpapp.ui import settings_page
    monkeypatch.setattr(settings_page, 'catalog', lambda: {**catalog(), MODEL.key: MODEL})
    turn_on(tmp_path)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open('/settings')
        await user.should_see('Vector search')
        for marker in ('vectors-memories', 'vectors-lore', 'vectors-model', 'vectors-threshold', 'vectors-max'):
            await user.should_see(marker=marker)
        await user.should_see(marker='vectors-download')  # not downloaded (but its files are recorded)
        await user.should_see(marker='vectors-not-downloaded')  # and it's on: keywords only until then
        the(user, 'vectors-max').set_value(5)
        the(user, 'vectors-threshold').set_value(0.6)
        await asyncio.sleep(0.8)
        s = SettingsStore(tmp_path).load()
        assert s.vectors.max_entries == 5 and s.vectors.thresholds == {MODEL.key: 0.6}
        the(user, 'vectors-threshold').set_value(0.5)  # (its default: nothing kept)
        await asyncio.sleep(0.8)
        assert SettingsStore(tmp_path).load().vectors.thresholds == {}


async def test_a_model_not_pinned_yet_cant_be_downloaded(tmp_path):
    store = SettingsStore(tmp_path)
    s = store.load()
    s.vectors.lore = True
    store.save(s)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open('/settings')
        await user.should_see(marker='vectors-model')
        from rpapp.embeddings import catalog
        if catalog()['all-minilm-l6-v2'].pinned:
            await user.should_see(marker='vectors-download')
            await user.should_see(marker='vectors-not-downloaded')
        else:
            await user.should_see(marker='vectors-unpinned')


def test_every_model_is_pinned():
    """The app ships its models' exact versions and fingerprints (tools/pin_models.py records them): without
    them, a model can't be downloaded. An unpinned catalog once slipped in, replacing the pinned one."""
    import re

    from rpapp.embeddings import catalog
    for model in catalog().values():
        assert model.pinned and re.fullmatch(r'[0-9a-f]{40}', model.revision), \
            f'{model.key} isn\'t pinned: run tools/pin_models.py, and commit rpapp/embedding_models.json'
        for f in model.files:
            assert f.size > 0 and re.fullmatch(r'[0-9a-f]{64}', f.sha256), f'{model.key}: {f.path}'


def test_build_root_uses_this_module():
    assert vectors_module.Vectorizer is Vectorizer


from rpapp.ui.app import build_root  # noqa: E402  (after the monkeypatch target is defined)
