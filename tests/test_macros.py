"""The pronoun macros (macros.py): {{sub}}, {{obj}}, {{poss}}, {{poss_p}}, {{ref}}, your persona's pronouns, filled
in wherever {{char}} and {{user}} are: the prompt, lore entries, first messages, memory calls."""
import pytest
from test_chat import mira, seraphina

from rpapp.conversations import ConversationStore
from rpapp.lore import activate
from rpapp.lorebooks import new_lore_book
from rpapp.macros import fill
from rpapp.memory import memory_prompt
from rpapp.personas import PRONOUNS
from rpapp.prompts import build_prompt
from rpapp.settings import LoreSettings, Settings
from rpapp.vectors import entry_text

SHE, HE, XE = (PRONOUNS[key][1] for key in ('she/her', 'he/him', 'xe/xem'))


@pytest.mark.parametrize('text, pronouns, filled', [
    ('{{char}} hands {{obj}} {{poss}} sword: it is {{poss_p}} now, and {{sub}} admires {{ref}}.', SHE,
     'Seraphina hands her her sword: it is hers now, and she admires herself.'),
    ('{{char}} hands {{obj}} {{poss}} sword: it is {{poss_p}} now, and {{sub}} admires {{ref}}.', HE,
     'Seraphina hands him his sword: it is his now, and he admires himself.'),
    ('{{sub}} and {{obj}}, {{poss}}, {{poss_p}}, {{ref}}', XE, 'Xe and xem, xyr, xyrs, xemself'),
    ('{{char}} waits for {{obj}}; {{poss_p}} is the choice.', None,  # (none chosen: they/them)
     'Seraphina waits for them; theirs is the choice.'),
])
def test_the_forms(text, pronouns, filled):
    assert fill(text, char='Seraphina', user='Mira', pronouns=pronouns) == filled


@pytest.mark.parametrize('text, filled', [
    ('{{sub}} smiles.', 'She smiles.'),                                  # (the text's start)
    ('The door opens. {{sub}} walks in!', 'The door opens. She walks in!'),
    ('Where? {{sub}} left… {{sub}} said so.', 'Where? She left… She said so.'),
    ('First line\n{{sub}} waits.', 'First line\nShe waits.'),           # (a line's start)
    ('*{{sub}} draws {{poss}} blade.* "{{sub}} is ready."', '*She draws her blade.* "She is ready."'),
    ('"Go." {{sub}} goes.', '"Go." She goes.'),                         # (after a closing quote)
    ('Ask {{obj}}, not {{ref}}.', 'Ask her, not herself.'),             # (inside a sentence: as it is)
    ('{{Sub}}, {{OBJ}}, {{Poss_P}}', 'She, Her, Hers'),                  # (written with a capital: a capital)
])
def test_capitals_at_the_start_of_a_sentence(text, filled):
    assert fill(text, char='Seraphina', user='Mira', pronouns=SHE) == filled


def test_names_and_other_braces_are_left_alone():
    assert fill('{{user}} and {{char}}: {{subject}}, {{object}}, {{outlet::x}}', char='S', user='M', pronouns=SHE) \
        == 'M and S: {{subject}}, {{object}}, {{outlet::x}}'
    assert fill('No macros at all.', char='S', user='M') == 'No macros at all.'


def test_wherever_names_are_filled_in(tmp_path):
    character, persona = seraphina('*{{char}} kneels beside {{user}} and checks {{poss}} wound.*'), mira()
    persona.pronouns = 'he/him'
    character.scenario = '{{user}} wakes up in the glade. {{sub}} remembers nothing.'
    persona.description = '{{user}} keeps {{poss}} herbs to {{ref}}.'
    conversations = ConversationStore(tmp_path)
    conversation = conversations.start(character, persona)
    assert conversation.messages[0].content == '*Seraphina kneels beside Mira and checks his wound.*'  # (first message)

    book = new_lore_book('Glade')
    book.add_entry(comment='Wound', key=['wound'], content='{{char}} healed {{obj}} once before.')
    prompt = build_prompt(conversation, character=character, persona=persona, settings=Settings(), books=[book])
    sent = '\n'.join(part.content for part in prompt.parts)
    assert 'Mira wakes up in the glade. He remembers nothing.' in sent       # (the scenario)
    assert 'Mira keeps his herbs to himself.' in sent                         # (the persona)
    assert 'Seraphina healed him once before.' in sent                        # (a lore entry)
    assert '{{' not in sent

    found = activate([book], chat=[('Mira', 'My wound.')], settings=LoreSettings(), budget=1000, char='Seraphina',
                     user='Mira', pronouns=persona.pronoun_forms)
    assert found.used[0].content == 'Seraphina healed him once before.'
    call = memory_prompt(instructions='Remember what {{sub}} does.', char='Seraphina', user='Mira',
                         character=character.scenario, persona=persona.description, book=new_lore_book('M'),
                         lines=[], pronouns=persona.pronoun_forms)
    assert call[0].content.startswith('Remember what he does.') and 'He remembers nothing.' in call[1].content


def test_vector_search_leaves_them_out():
    """Pronouns, like names, say nothing of what an entry is about (vectors.py)."""
    book = new_lore_book('Glade')
    entry = book.add_entry(comment='Wound', key=['wound'], content='{{char}} healed {{obj}} and {{poss}} horse.')
    assert entry_text(entry) == 'Wound\n healed  and  horse.'
