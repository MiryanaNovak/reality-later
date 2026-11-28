"""Choosing lore entries by relevance (lore.py): the most related to the chat go first, chaining stops after so many
levels, and with vector search, an entry brought in by another must be close in meaning to the chat too."""
from nicegui import ui
from nicegui.testing import user_simulation
from test_lore import book, run
from test_prompts import cast, chat

from rpapp.lore import Closeness, Meaning, activate
from rpapp.prompts import build_prompt
from rpapp.settings import LoreSettings, Settings
from rpapp.ui.prompt_view import show_prompt


def academy():
    """The Council names Lord Seravold, whose entry names his son Lucian, whose entry names his sword."""
    return book('Academy',
                Council={'key': ['council'], 'content': 'The High Council rules the academy. Lord Seravold leads it.'},
                Seravold={'key': ['seravold'], 'content': 'Lord Seravold, head of his house, father of Lucian.'},
                Lucian={'key': ['lucian'], 'content': 'Lucian, a second-year student. He carries Dawnbreaker.'},
                Sword={'key': ['dawnbreaker'], 'content': 'Dawnbreaker, a blade of light.'})


def scores(b, limit=3, **by_title) -> Closeness:
    """Closeness to the last message, per entry title (threshold 0.5; at most `limit` by meaning alone)."""
    return Closeness({(b.id, e.uid): [by_title[e.comment]] for e in b.entries.values() if e.comment in by_title},
                     threshold=0.5, limit=limit)


def picked(result):
    return [(a.entry.comment, a.level, a.status) for a in result.activations]


# --- chaining ------------------------------------------------------------------------------------

def test_one_level_by_default():
    used, result = run(academy(), 'The council meets tonight.')
    assert used == ['Council', 'Seravold']  # (Lucian: named in Seravold's, a level further)
    assert picked(result) == [('Council', 0, 'used'), ('Seravold', 1, 'used')]
    assert result.activations[1].reason == 'keyword "seravold" in entry "Council"'
    assert result.notes == ['1 more entry is named in entries brought in by others: chaining stops after 1 level '
                            '(Settings › Lore books).']

    used, result = run(academy(), 'The council meets tonight.', settings=LoreSettings(recursion_levels=3))
    assert picked(result) == [('Council', 0, 'used'), ('Seravold', 1, 'used'), ('Lucian', 2, 'used'),
                              ('Sword', 3, 'used')]
    assert result.notes == []
    assert run(academy(), 'The council meets tonight.', settings=LoreSettings(recursive=False))[1].notes == []


def test_with_vector_search_those_brought_in_must_be_close_to_the_chat():
    b = academy()
    close = scores(b, Council=0.3, Seravold=0.2, Lucian=0.1, Sword=0.1)
    result = activate([b], chat=[('Mira', 'The council meets tonight.')], settings=LoreSettings(recursion_levels=3),
                      budget=10_000, char='S', user='Mira', closeness=close)
    assert picked(result) == [('Council', 0, 'used'),  # (by keyword in the chat: in, however far in meaning)
                              ('Seravold', 1, 'unrelated')]  # (and Lucian, whom it names: not brought in)
    assert result.activations[1].note == 'it\'s not close enough in meaning to the chat (0.20, 0.50 needed)'
    assert [a.closeness for a in result.activations] == [0.3, 0.2]

    loose = activate([b], chat=[('Mira', 'The council meets tonight.')], budget=10_000, char='S', user='Mira',
                     settings=LoreSettings(recursion_levels=3, chained_close=False), closeness=close)
    assert [a.status for a in loose.activations] == ['used'] * 4

    # Close enough: in by meaning alone (so many at most), or brought in past that limit.
    close = scores(b, limit=1, Council=0.3, Seravold=0.72, Lucian=0.6, Sword=0.2)
    result = activate([b], chat=[('Mira', 'The council meets tonight.')], settings=LoreSettings(recursion_levels=3),
                      budget=10_000, char='S', user='Mira', closeness=close)
    assert picked(result) == [('Council', 0, 'used'), ('Seravold', 0, 'used'), ('Lucian', 1, 'used'),
                              ('Sword', 2, 'unrelated')]
    assert result.activations[1].reason == 'close in meaning to the chat (0.72)'


def test_the_budget_is_a_limit_not_a_target():
    b = academy()
    result = activate([b], chat=[('Mira', 'The council meets tonight.')], settings=LoreSettings(recursion_levels=3),
                      budget=10_000, char='S', user='Mira', closeness=scores(b, Council=0.3, Seravold=0.2))
    assert [a.entry.comment for a in result.used] == ['Council'] and result.tokens < 30  # (room left: unused)


def test_brought_in_only_if_what_brought_it_in_is_in():
    long = 'The High Council rules the academy. ' * 10 + 'Lord Seravold leads it.'
    b = book(Council={'key': ['council'], 'content': long},
             Seravold={'key': ['seravold'], 'content': 'Lord Seravold.'})
    used, result = run(b, 'The council meets tonight.', budget=50)
    assert used == [] and picked(result) == [('Council', 0, 'over_budget'), ('Seravold', 1, 'skipped')]
    assert result.activations[1].note == 'the entry that brought it in isn\'t in'


# --- who goes first ----------------------------------------------------------------------------

def test_the_most_related_first():
    long = 'word ' * 70  # 100 tokens each
    b = book(Older={'key': ['glade'], 'content': long, 'order': 900},       # one message back, high Order
             Scenario={'key': ['guardian'], 'content': long, 'order': 800, 'matchScenario': True},
             Last={'key': ['forest'], 'content': long, 'order': 1},         # in the last message, low Order
             Chained={'key': ['ember'], 'content': long, 'order': 1000},
             Named={'key': ['moss'], 'content': long + ' ember', 'order': 2})
    texts = {'scenario': 'Seraphina, guardian of the glade.'}
    _, result = run(b, 'We rested in the glade.', 'Is this forest full of moss?', texts=texts, budget=10_000)
    assert [(a.entry.comment, a.reason) for a in result.activations] == [
        ('Named', 'keyword "moss" in the last message'),  # (both in the last message: the higher Order first)
        ('Last', 'keyword "forest" in the last message'),
        ('Older', 'keyword "glade" in the chat, 1 message before the last'),
        ('Scenario', 'keyword "guardian" in the scenario'),
        ('Chained', 'keyword "ember" in entry "Named"')]
    used, _ = run(b, 'We rested in the glade.', 'Is this forest full of moss?', texts=texts, budget=250)
    assert used == ['Named', 'Last']  # (what's more related wins the room, whatever its Order)

    # As SillyTavern: the higher Order first (level by level)
    used, result = run(b, 'We rested in the glade.', 'Is this forest full of moss?', texts=texts, budget=250,
                       settings=LoreSettings(priority='order'))
    assert used == ['Older', 'Scenario']
    assert [a.entry.comment for a in result.activations] == ['Older', 'Scenario', 'Named', 'Last', 'Chained']


def test_among_equals_the_closest_then_the_higher_order():
    b = book(A={'key': ['forest'], 'content': 'A.', 'order': 500},
             B={'key': ['forest'], 'content': 'B.', 'order': 1},
             C={'key': ['forest'], 'content': 'C.', 'order': 100})
    _, result = run(b, 'The forest.')
    assert [a.entry.comment for a in result.activations] == ['A', 'C', 'B']  # (without vectors: by Order)
    result = activate([b], chat=[('Mira', 'The forest.')], settings=LoreSettings(), budget=10_000, char='S',
                      user='Mira', closeness=scores(b, A=0.2, B=0.8, C=0.4))
    assert [a.entry.comment for a in result.activations] == ['B', 'C', 'A']


def test_keep_room_for_more_once_one_doesnt_fit():
    b = book(Big={'key': ['forest'], 'content': 'word ' * 70, 'order': 200},
             Small={'key': ['forest'], 'content': 'Short.', 'order': 100})
    used, result = run(b, 'The forest.', budget=50)
    assert used == ['Small'] and result.activations[0].status == 'over_budget'


# --- in the prompt dialog --------------------------------------------------------------------------

async def test_the_dialog_says_level_and_closeness():
    character, persona = cast()
    b = academy()
    settings = Settings()
    settings.lore.recursion_levels = 3
    close = scores(b, limit=1, Council=0.31, Seravold=0.72, Lucian=0.6, Sword=0.2)
    prompt = build_prompt(chat('Welcome.', 'The council meets tonight.'), character=character, persona=persona,
                          settings=settings, books=[b], meaning=Meaning(lore=close))
    uses = {u.entry: u for u in prompt.report.lore}
    assert (uses['Lucian'].level, uses['Lucian'].closeness, uses['Sword'].status) == (1, 0.6, 'unrelated')

    def root() -> None:
        ui.button('Show', on_click=lambda: show_prompt(prompt, None, time=str, copy_js=lambda _: '')).mark('show')

    async with user_simulation(root) as user:
        await user.open('/')
        user.find(marker='show').click()
        await user.should_see(marker='prompt-chart')
        [section] = [e for e in user.client.layout.descendants() if 'section-lore' in e._markers]
        section.set_value(True)
        with user.scope(marker='section-lore'):
            await user.should_see('In the order they went in for the budget: the most related to the chat first')
            await user.should_see('Keyword "council" in the last message, close in meaning: 0.31. Placed with the '
                                  'background')
            await user.should_see('Close in meaning to the chat (0.72). Placed with the background')
            await user.should_see('Keyword "lucian" in entry "Seravold" (level 1), close in meaning: 0.60. Placed')
            await user.should_see('Keyword "dawnbreaker" in entry "Lucian" (level 2), close in meaning: 0.20. Left '
                                  'out: it\'s not close enough in meaning to the chat (0.20, 0.50 needed).')
