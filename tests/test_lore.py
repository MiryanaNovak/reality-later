"""Lore entry activation: which entries of the books in play go into a prompt."""
import json
from pathlib import Path

from rpapp.lore import activate, key_matches
from rpapp.lorebooks import LoreBook, new_lore_book
from rpapp.settings import LoreSettings

ELDORIA = Path(__file__).parent / 'eldoria_sillytavern.json'


def book(name='World', **entries) -> LoreBook:
    """A book with entries given as title=dict(fields)."""
    b = new_lore_book(name)
    for title, fields in entries.items():
        b.add_entry(comment=title, **fields)
    return b


def run(books, *messages, settings=None, budget=10_000, texts=None):
    """The titles of the entries used, and the result. Messages alternate Mira / Seraphina, ending with Mira."""
    chat = [('Mira' if (len(messages) - 1 - i) % 2 == 0 else 'Seraphina', text) for i, text in enumerate(messages)]
    result = activate(books if isinstance(books, list) else [books], chat=chat, settings=settings or LoreSettings(),
                      budget=budget, char='Seraphina', user='Mira', texts=texts)
    return [a.entry.title for a in result.used], result


def test_key_matching():
    assert key_matches('Sword', 'a sword!')                          # case-insensitive by default
    assert not key_matches('Sword', 'a sword!', case_sensitive=True)
    assert key_matches('sword', 'my sword, then')
    assert not key_matches('sword', 'swordsman')                     # whole words by default
    assert key_matches('sword', 'swordsman', whole_words=False)      # anywhere, as SillyTavern does
    assert key_matches('forêt', 'la forêt.')                         # accents are part of words
    assert key_matches('/silver ?towers?/i', 'The SilverTower')     # a regular expression
    assert not key_matches('/silver ?towers?/', 'The SilverTower')  # without i, case counts
    assert not key_matches('/([/', 'anything ([')                   # an invalid pattern matches nothing


def test_whole_words_take_plurals_and_possessives():
    for text in ('Dragons!', "the dragon's egg", 'dragons’ eggs', 'two DRAGONS'):
        assert key_matches('dragon', text), text
    assert not key_matches('dragon', 'a dragonfly')
    assert key_matches('witch', 'the witches')
    assert key_matches('château', 'les châteaux') and key_matches('Académie', "l'Académie, les académies")
    assert not key_matches('Ma', 'Max')                              # (x only after a u: French plurals)
    assert key_matches('DRAGON', 'DRAGONS', case_sensitive=True)    # (the ending in either case)
    assert not key_matches('Brann', 'brann', case_sensitive=True)


def test_star_allows_any_ending():
    assert not key_matches('duel', 'they were dueling')
    assert key_matches('duel*', 'they were dueling') and key_matches('duel*', 'a duel')
    assert not key_matches('duel*', 'an aduelist')                    # still the start of a word
    assert key_matches('duel*', 'the duelist', whole_words=False)
    assert not key_matches('*', 'anything')


def test_short_keywords_are_whole_words():
    for whole in (True, False):
        assert not key_matches('K', 'I know you think so', whole_words=whole)
        assert not key_matches('K', 'Kate and the king', whole_words=whole)
        assert key_matches('K', "K's blade", whole_words=whole) and key_matches('K', 'Then K nods.', whole_words=whole)
        assert not key_matches('Eve', 'every evening, even so', whole_words=whole)
        assert key_matches('cat', 'two cats', whole_words=whole)
    assert key_matches('Mira', 'a miracle', whole_words=False)       # (4 letters: anywhere, with it off)
    assert key_matches('K*', 'Kate', whole_words=False)


def test_several_words():
    assert key_matches('main hall', 'in the Main Halls')
    assert key_matches('main hall', 'the main\n  hall')               # any spaces between the words
    assert not key_matches('main hall', 'the domain hallway')
    assert key_matches('main hall', 'the domain hallway', whole_words=False)


def test_keywords_with_punctuation_at_an_edge():
    assert key_matches('Dr.', 'Dr.Venn') and key_matches('#oath', 'the #oath') and key_matches('K-9', 'K-9s')
    assert not key_matches('Dr.', 'Adr.')


def test_languages_without_spaces():
    assert key_matches('東京', '東京に行く') and key_matches('剣', '剣を抜いた')  # (no word edges: anywhere)
    assert key_matches('서울', '서울에서 만나')


def test_keywords_scan_depth_and_names():
    b = book(Forest={'key': ['forest'], 'content': 'The forest is old.'},
             Glade={'key': ['glade'], 'content': 'The glade is safe.'},
             Self={'key': ['seraphina'], 'content': 'Seraphina is a guardian.'})
    used, result = run(b, 'We met in the glade.', 'Hello.', 'Is this forest dangerous?')
    assert used == ['Forest', 'Self']  # the glade is 3 messages back; names are scanned too ("Seraphina: ...")
    assert result.activations[0].reason == 'keyword "forest" in the chat'

    used, _ = run(b, 'We met in the glade.', 'Hello.', 'Is this forest dangerous?',
                  settings=LoreSettings(scan_depth=3))
    assert 'Glade' in used
    b.entry(1).scanDepth = 3  # the entry's own setting wins
    assert 'Glade' in run(b, 'We met in the glade.', 'Hello.', 'Is this forest dangerous?')[0]


def test_constant_disabled_and_triggers():
    b = book(Always={'constant': True, 'content': 'Magic is real.'},
             Off={'key': ['forest'], 'content': 'x', 'disable': True},
             Swipes={'key': ['forest'], 'content': 'y', 'triggers': ['swipe']},
             Normal={'key': ['forest'], 'content': 'z', 'triggers': ['normal', 'swipe']})
    used, result = run(b, 'The forest.')
    assert used == ['Always', 'Normal']
    assert result.activations[0].reason == 'always included'


def test_secondary_keywords():
    def used(logic, text):
        b = book(E={'key': ['tower'], 'keysecondary': ['silver', 'night'], 'selectiveLogic': logic,
                    'content': 'The tower.'})
        return run(b, text)[0] == ['E']
    AND_ANY, NOT_ALL, NOT_ANY, AND_ALL = 0, 1, 2, 3
    assert used(AND_ANY, 'the silver tower') and not used(AND_ANY, 'the tower')
    assert used(AND_ALL, 'the silver tower at night') and not used(AND_ALL, 'the silver tower')
    assert used(NOT_ANY, 'the tower') and not used(NOT_ANY, 'the tower at night')
    assert used(NOT_ALL, 'the silver tower') and not used(NOT_ALL, 'the silver tower at night')

    b = book(E={'key': ['tower'], 'keysecondary': ['silver'], 'selective': False, 'content': 'The tower.'})
    assert run(b, 'the tower')[0] == ['E']  # not selective: the secondary keywords don't count


def test_macros_in_keywords_and_content():
    b = book(E={'key': ['{{user}}\'s staff'], 'content': '{{user}} carries it for {{char}}.'})
    used, result = run(b, "Mira's staff glows.")
    assert used == ['E'] and result.used[0].content == 'Mira carries it for Seraphina.'


def test_recursion():
    b = book(Forest={'key': ['forest'], 'content': 'Shadowfangs roam the forest.'},
             Shadowfang={'key': ['shadowfang'], 'content': 'Beasts of the dark.'},
             Hidden={'key': ['shadowfang'], 'content': 'Only from the chat.', 'excludeRecursion': True})
    used, result = run(b, 'Is this forest dangerous?')
    assert used == ['Forest', 'Shadowfang']
    assert result.activations[1].reason == 'keyword "shadowfang" in entry "Forest"'
    assert run(b, 'Is this forest dangerous?', settings=LoreSettings(recursive=False))[0] == ['Forest']

    b.entry(0).preventRecursion = True
    assert run(b, 'Is this forest dangerous?')[0] == ['Forest']

    delayed = book(Forest={'key': ['forest'], 'content': 'Shadowfangs roam the forest.'},
                   Late={'key': ['forest'], 'content': 'Only on recursion.', 'delayUntilRecursion': True})
    used, result = run(delayed, 'The forest.')
    assert used == ['Forest', 'Late']  # it waits for the second pass, then the chat counts too
    assert run(delayed, 'The forest.', settings=LoreSettings(recursive=False))[0] == ['Forest']


def test_other_texts_can_be_scanned():
    b = book(E={'key': ['wounded'], 'content': 'Healing takes time.', 'matchScenario': True},
             F={'key': ['wounded'], 'content': 'Not this one.'})
    used, result = run(b, 'Hello.', texts={'scenario': 'Mira wakes up wounded.'})
    assert used == ['E'] and result.used[0].reason == 'keyword "wounded" in the scenario'


def test_the_characters_description_and_personality_are_its_scenario():
    """SillyTavern books may look in the character's description or personality: here, both are in the scenario."""
    b = book(D={'key': ['guardian'], 'content': 'Guardians are sworn.', 'matchCharacterDescription': True},
             P={'key': ['kind'], 'content': 'Kindness is rare.', 'matchCharacterPersonality': True},
             All={'key': ['glade'], 'content': 'The glade.', 'matchCharacterDescription': True,
                  'matchScenario': True})
    used, result = run(b, 'Hello.', texts={'scenario': 'Seraphina, a kind guardian of the glade.'})
    assert sorted(used) == ['All', 'D', 'P']
    assert {a.reason for a in result.used} == {f'keyword "{k}" in the scenario' for k in ('guardian', 'kind', 'glade')}


def test_budget_and_order():
    long = 'word ' * 70  # 350 characters: 100 tokens
    b = book(Low={'key': ['forest'], 'content': long, 'order': 10},
             High={'key': ['forest'], 'content': long, 'order': 200},
             Free={'key': ['forest'], 'content': long, 'order': 1, 'ignoreBudget': True})
    used, result = run(b, 'The forest.', budget=150)
    assert used == ['High', 'Free']  # the higher order wins the room left
    assert [(a.entry.title, a.status) for a in result.activations if a.status != 'used'] == [('Low', 'over_budget')]
    assert result.tokens == 200


def test_outlet_entries_are_skipped_and_unsupported_features_noted():
    b = book(Outlet={'key': ['forest'], 'content': 'x', 'position': 7},
             Chance={'key': ['forest'], 'content': 'y', 'probability': 30},
             Group={'key': ['forest'], 'content': 'z', 'group': 'weather'})
    used, result = run(b, 'The forest.')
    assert used == ['Chance', 'Group']
    assert result.activations[0].status == 'skipped' and 'macro' in result.activations[0].note
    assert result.notes == [
        "1 entry has a trigger chance, which isn't applied yet: it triggers as if it weren't set.",
        "1 entry has an inclusion group, which isn't applied yet: it triggers as if it weren't set.",
    ]


def test_eldoria():
    eldoria = LoreBook.model_validate({**json.loads(ELDORIA.read_text(encoding='utf-8')), 'name': 'Eldoria'})
    used, result = run(eldoria, 'Is this forest dangerous?', settings=LoreSettings(recursive=False))
    assert used == ['eldoria']  # "forest" is one of its keywords
    assert result.notes == []
