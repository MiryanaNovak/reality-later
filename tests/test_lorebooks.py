"""The LoreBook model and SillyTavern compatibility. Run with: pytest -q"""
import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from rpapp.lorebooks import LoreBook, LoreEntry, lore_book_store, new_lore_book
from rpapp.ui.collection_page import _file_name

ELDORIA = json.loads((Path(__file__).parent / 'eldoria_sillytavern.json').read_text())


def test_sillytavern_file_round_trips_unchanged(tmp_path):
    store = lore_book_store(tmp_path)
    book = store.import_data(ELDORIA, file_name='Eldoria.json')
    assert book.name == 'Eldoria' and len(book.entries) == 4

    saved = json.loads(store.path_for(book.id).read_text())
    assert saved['entries'] == ELDORIA['entries']  # our own file is a valid SillyTavern file

    exported = store.load(book.id).to_sillytavern()
    assert exported == {'name': 'Eldoria', 'entries': ELDORIA['entries']}


def test_unknown_fields_are_kept(tmp_path):
    data = json.loads(json.dumps(ELDORIA))
    data['originalData'] = {'name': 'from a character card', 'entries': []}
    data['entries']['0']['someFutureField'] = {'x': 1}
    data['entries']['0']['characterFilter']['futureFlag'] = True
    store = lore_book_store(tmp_path)
    book = store.import_data(data, file_name='x.json')
    book.entry(0).content = 'changed'
    store.save(book)
    out = store.load(book.id).to_sillytavern()
    assert out['originalData'] == data['originalData']
    assert out['entries']['0']['someFutureField'] == {'x': 1}
    assert out['entries']['0']['characterFilter']['futureFlag'] is True
    assert out['entries']['0']['content'] == 'changed'


def test_minimal_and_old_files_load():
    book = LoreBook.model_validate({'entries': {'7': {'uid': 7, 'key': ['a'], 'content': 'A'}}})
    entry = book.entry(7)
    assert entry.position == 0 and entry.order == 100 and entry.probability == 100 and entry.role is None
    out = entry.model_dump(mode='json')
    assert 'characterFilter' not in out and 'displayIndex' not in out  # not added when absent


def test_value_types_are_preserved():
    entry = LoreEntry.model_validate({'uid': 0, 'delayUntilRecursion': False, 'order': 12.5, 'groupWeight': 100})
    assert entry.delayUntilRecursion is False and entry.order == 12.5
    entry.delayUntilRecursion = 3
    assert entry.model_dump()['delayUntilRecursion'] == 3


def test_not_a_lore_book_is_refused(tmp_path):
    with pytest.raises(ValidationError, match='entries'):
        lore_book_store(tmp_path).import_data({'kind': 'openrouter', 'name': 'a connection'})


def test_add_duplicate_remove_entries():
    book = new_lore_book('Test')
    a = book.add_entry(key=['a'])
    b = book.add_entry(key=['b'])
    assert (a.uid, b.uid) == (0, 1) and (a.displayIndex, b.displayIndex) == (0, 1)
    book.remove_entry(0)
    c = book.add_entry(comment='C')  # reuses the free uid, but goes to the end of the list
    assert c.uid == 0 and [e.title for e in book.sorted_entries()] == ['b', 'C']
    copy = book.duplicate_entry(c)
    assert copy.uid == 2 and copy.comment == 'C (copy)' and list(book.entries) == ['1', '0', '2']
    # new entries use SillyTavern's defaults
    assert c.selective is True and c.useProbability is True and c.depth == 4 and c.sticky is None


def test_position_and_role_follow_sillytavern():
    entry = LoreEntry(uid=0)
    entry.set_position(4)
    assert entry.role == 0
    entry.role = 2
    entry.set_position(4)
    assert entry.role == 2
    entry.set_position(1)
    assert entry.role is None


def test_strategy():
    entry = LoreEntry(uid=0)
    assert entry.strategy == 'both'  # by default: a keyword, or (with vector search) the meaning
    assert LoreEntry(uid=1, vectorized=True).strategy == 'both'  # (SillyTavern's flag doesn't decide)
    entry.set_strategy('constant')
    assert (entry.constant, entry.onlyBy, entry.strategy) == (True, '', 'constant')
    entry.set_strategy('meaning')
    assert (entry.constant, entry.onlyBy, entry.vectorized, entry.strategy) == (False, 'meaning', True, 'meaning')
    entry.set_strategy('keywords')
    assert (entry.onlyBy, entry.vectorized, entry.strategy) == ('keywords', False, 'keywords')
    entry.set_strategy('both')
    assert (entry.onlyBy, entry.vectorized, entry.strategy) == ('', True, 'both')


def test_export_file_names():
    assert _file_name('Eldoria') == 'Eldoria.json'
    assert _file_name('a/b: c?') == 'a b c.json'
    assert _file_name('...') == 'export.json'


async def test_delayed_saves(tmp_path):
    store = lore_book_store(tmp_path)
    book = new_lore_book('Typing test')
    store.save(book)
    for i in range(5):
        book.name = f'name {i}'
        store.save_soon(book, delay=0.2)
    assert json.loads(store.path_for(book.id).read_text())['name'] == 'Typing test'  # not written yet
    await asyncio.sleep(0.3)
    assert json.loads(store.path_for(book.id).read_text())['name'] == 'name 4'

    book.name = 'pending'
    store.save_soon(book, delay=10)
    assert store.load(book.id).name == 'pending'  # reads write pending saves first

    book.name = 'deleted while pending'
    store.save_soon(book, delay=0.1)
    store.delete(book.id)
    await asyncio.sleep(0.2)
    assert not store.path_for(book.id).exists()  # a pending save doesn't bring it back
