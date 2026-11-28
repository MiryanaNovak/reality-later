"""The "Prompt for this message" dialog (prompt_view.py): a pie of what the prompt is made of, its key in the order
the parts are sent, and a folding section for each kind of part, in the same order, drawn as it's opened."""
import re

from nicegui import ui
from nicegui.testing import user_simulation
from test_prompt_modules import noir
from test_prompts import cast, chat

from rpapp.lorebooks import new_lore_book
from rpapp.prompts import Prompt, PromptPart, build_prompt
from rpapp.settings import Settings
from rpapp.ui.prompt_view import SHARES, pie_svg, shares_of, show_prompt
from rpapp.ui.theme import SHARE_COLORS

ALL = ['instructions', 'modules', 'character', 'persona', 'examples', 'lore', 'memories', 'messages', 'lore-chat',
       'final', 'final-modules', 'hidden']


def everything() -> Prompt:
    """A prompt with a part of every kind: a prompt with modules, the character's scenario and examples, a
    persona, lore (with the background, and among the messages), a memory, and a hidden instruction."""
    character, persona = cast()
    character.example_dialogues = ['{{char}}: Rest now.']
    persona.description = 'A wandering healer.'
    book = new_lore_book('Eldoria')
    book.add_entry(comment='Forest', key=['forest'], content='The forest is old.')
    book.add_entry(comment='Mood', key=['forest'], content='[It is getting dark.]', position=4, depth=1)
    memories = new_lore_book('Memories')
    memories.add_entry(comment='The fox', key=['forest'], content='Mira followed a silver fox.')
    return build_prompt(chat('Welcome.', 'Is this forest old?'), character=character, persona=persona,
                        settings=Settings(), preset=noir(), books=[book], memories=memories, continue_scene=True)


def test_the_kinds_of_parts_in_the_order_sent():
    prompt = everything()
    shares = shares_of(prompt)
    assert [s.key for s in shares] == ALL
    assert [k for k in SHARES if k != 'memories-chat'] == ALL + ['other']  # (memories here: before the chat)
    assert set(SHARE_COLORS) == set(SHARES)  # (each has its colors)
    assert sum(s.tokens for s in shares) == prompt.report.tokens  # (every part counted, once)
    by_key = {s.key: [p.source for _, p in s.parts] for s in shares}
    assert by_key['modules'] == ['Instructions of the module "Combat"']  # (Romance: off by default)
    assert by_key['final-modules'] == ['Final instructions of the module "Combat"']
    assert by_key['lore'] == ['Lore: Eldoria › Forest']
    assert by_key['lore-chat'] == ['Lore: Eldoria › Mood']  # (at a depth: among the messages)
    assert by_key['messages'][0] == 'Placeholder: some models need the chat to start with you'

    # The first saved prompts didn't say what their parts were: the chat's are messages, the rest "other".
    old = Prompt(parts=[PromptPart(role='system', content='Be kind.'), PromptPart(role='user', content='Hi.')])
    assert [s.key for s in shares_of(old)] == ['other', 'messages']


def test_the_pie():
    shares = [s for s in shares_of(everything())]
    total = sum(s.tokens for s in shares)
    svg = pie_svg(shares, total)
    assert re.findall(r'data-share="([a-z-]+)"', svg) == ALL  # (clockwise from the top, in the order sent)
    assert 'style="fill: var(--share-lore)"' in svg
    lore = next(s for s in shares if s.key == 'lore')
    assert f'<title>Lore: {lore.tokens:,} tokens, ' in svg  # (said when pointed at)
    assert svg.startswith('<svg class="prompt-pie" viewBox="0 0 200 200" role="img" aria-label="What the prompt '
                          'is made of: Instructions ')
    x, y = re.findall(r'A98,98 0 [01] 1 ([\d.]+),([\d.]+) Z', svg)[-1]
    assert (round(float(x)), round(float(y))) == (100, 2)  # (the last one ends at the top, where the first began)

    [alone] = [s for s in shares if s.key == 'messages']
    assert '<circle class="slice" data-share="messages"' in pie_svg([alone], alone.tokens)  # (the whole of it)


def dialog(prompt: Prompt):
    def root() -> None:
        ui.button('Show', on_click=lambda: show_prompt(prompt, None, time=str, copy_js=lambda _: '')).mark('show')
    return root


def the(user, marker):
    [element] = [e for e in user.client.layout.descendants() if marker in e._markers]
    return element


def found(user, marker) -> bool:
    return any(marker in e._markers for e in user.client.layout.descendants())


async def test_the_chart_and_its_sections():
    prompt = everything()
    total = prompt.report.tokens
    async with user_simulation(dialog(prompt)) as user:
        await user.open('/')
        user.find(marker='show').click()
        await user.should_see(marker='prompt-chart')
        await user.should_see(f'About {total:,} tokens')
        legend = [m.removeprefix('legend-') for e in the(user, 'prompt-legend').descendants() for m in e._markers
                  if m.startswith('legend-')]
        assert legend == ALL  # (the key: in the order sent)
        lore = next(s for s in shares_of(prompt) if s.key == 'lore')
        with user.scope(marker='legend-lore'):
            await user.should_see(f'{lore.tokens:,}')
            await user.should_see(f'{round(lore.tokens * 100 / total)}%')

        dialog_card = the(user, 'prompt-dialog')
        sections = [m.removeprefix('section-') for e in dialog_card.descendants() for m in e._markers
                    if m.startswith('section-')]
        assert sections == ALL + ['sent']  # (the same order, then as sent)
        assert all(not the(user, f'section-{key}').value for key in sections)  # (all folded)
        assert not found(user, 'prompt-part-0') and not found(user, 'prompt-message-0')  # (drawn when opened)

        the(user, 'section-instructions').set_value(True)
        await user.should_see('Instructions (Noir) · system')
        assert found(user, 'prompt-part-0')
        user.find(marker='legend-character').click()  # (the key opens its section)
        assert the(user, 'section-character').value
        await user.should_see('Their description is only for you')
        the(user, 'section-memories').set_value(True)
        with user.scope(marker='section-memories'):
            await user.should_see('Mira followed a silver fox.')
        the(user, 'section-sent').set_value(True)
        await user.should_see(marker='prompt-message-0')

        user.find(marker='show').click()  # (another time: those left open still are)
        await user.should_see(marker='prompt-chart')
        opened = [e for e in user.client.layout.descendants() if 'section-memories' in e._markers]
        assert opened[-1].value and found(user, 'memory-use')


async def test_lore_and_memories_say_why_there_are_none():
    character, persona = cast()
    prompt = build_prompt(chat('Welcome.', 'Hello.'), character=character, persona=persona, settings=Settings())
    assert [s.key for s in shares_of(prompt)] == ['character', 'messages']
    async with user_simulation(dialog(prompt)) as user:
        await user.open('/')
        user.find(marker='show').click()
        await user.should_see(marker='prompt-chart')
        sections = [m.removeprefix('section-') for e in the(user, 'prompt-dialog').descendants()
                    for m in e._markers if m.startswith('section-')]
        assert sections == ['character', 'lore', 'memories', 'messages', 'sent']  # (where they'd be)
        assert not found(user, 'legend-lore')  # (nothing in the pie)
        with user.scope(marker='section-lore'):
            await user.should_see('No lore books in this chat')
        with user.scope(marker='section-memories'):
            await user.should_see('No memories yet')
