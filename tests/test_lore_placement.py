"""Where lore entries and memories go in the prompt (prompts.py): in an order that doesn't change from one reply to
the next (those always included first), and, with Settings › Lore books › "Lore and memories that come and go" set
to "just before the last message", those that come and go leave the chat before them as it was (cached)."""
from nicegui import ui
from nicegui.testing import user_simulation
from test_lore import book
from test_prompts import cast, chat

from rpapp.lore import Closeness, Meaning
from rpapp.lorebooks import new_lore_book
from rpapp.prompts import build_prompt
from rpapp.settings import Settings
from rpapp.ui.prompt_view import show_prompt


def world():
    return book('World',
                Rule={'constant': True, 'content': 'Magic has a price.', 'order': 900},
                Forest={'key': ['forest'], 'content': 'The forest is old.'},
                River={'key': ['river'], 'content': 'The river runs north.'},
                Mood={'key': ['forest'], 'content': '[Dusk falls.]', 'position': 4, 'depth': 1})


def remembered():
    memories = new_lore_book('Memories')
    memories.add_entry(comment='The fox', key=['forest'], content='Mira followed a fox.')
    memories.add_entry(comment='The oath', constant=True, content='Mira swore to protect the glade.')
    return memories


def prompt(*texts, settings=None, books=None, meaning=None):
    character, persona = cast()
    return build_prompt(chat(*texts), character=character, persona=persona, settings=settings or Settings(),
                        books=books or [world()], memories=remembered(), meaning=meaning)


def kinds(p):
    return [(part.kind, part.source) for part in p.parts]


def late() -> Settings:
    s = Settings()
    s.lore.placement = 'end'
    return s


# --- the order ---------------------------------------------------------------------------------------

def test_always_included_first_then_a_fixed_order():
    p = prompt('Welcome.', 'By the forest and the river.')
    assert [src for kind, src in kinds(p) if kind == 'lore'] == [
        'Lore: World › Rule',    # (always included: first, whatever its Order)
        'Lore: World › Forest',  # (the same Order: by their place in the book)
        'Lore: World › River',
        'Lore: World › Mood']    # (at a depth: in the chat)
    assert [src for kind, src in kinds(p) if kind == 'memory'] == ['Memory: The oath', 'Memory: The fox']

    # However relevant each was this time, they're where they were.
    b = world()
    closer = Closeness({(b.id, 2): [0.9], (b.id, 1): [0.1]}, threshold=0.5, limit=3)  # River much closer
    again = prompt('Welcome.', 'By the forest and the river.', books=[b], meaning=Meaning(lore=closer))
    assert [u.entry for u in again.report.lore][:3] == ['Rule', 'River', 'Forest']  # (ranked: River first)
    assert [src for kind, src in kinds(again) if kind == 'lore'] == [src for kind, src in kinds(p) if kind == 'lore']


# --- just before the last message ---------------------------------------------------------------------

def test_what_comes_and_goes_just_before_the_last_message():
    p = prompt('Welcome.', 'Hello.', 'Hi.', 'By the forest.', settings=late())
    assert kinds(p)[1:] == [
        ('lore', 'Lore: World › Rule'),        # (always included: with the background)
        ('memory', 'Memory: The oath'),
        ('start', 'Placeholder: some models need the chat to start with you'),
        ('message', 'Seraphina'), ('message', 'Mira'), ('message', 'Seraphina'),
        ('lore', 'Lore: World › Mood'),        # (at a depth: where it was)
        ('lore', 'Lore: World › Forest'),      # (comes and goes: just before the last message)
        ('memory', 'Memory: The fox'),
        ('message', 'Mira')]
    uses = {u.entry: u.placement for u in p.report.lore}
    assert uses == {'Rule': 'with the background', 'Forest': 'just before the last message',
                    'Mood': 'in the chat, 1 message from the end, as system'}
    assert {u.entry: u.placement for u in p.report.memories}['The fox'] == 'just before the last message'
    assert p.report.lore_placement == 'end'


def test_the_chat_before_stays_the_same():
    """The next reply's prompt starts as this one did, up to where what came and went is: providers reuse it."""
    for settings, same in ((Settings(), False), (late(), True)):
        before = prompt('Welcome.', 'Hello.', 'Hi.', 'By the forest.', settings=settings)
        after = prompt('Welcome.', 'Hello.', 'Hi.', 'By the forest.', 'Indeed.', 'Now the river.', settings=settings)
        moved = next(i for i, part in enumerate(before.parts) if part.source == 'Lore: World › Mood')
        assert (before.parts[:moved] == after.parts[:moved]) is same  # (by default: the lore before the chat changed)
    assert after.parts[moved].content == 'By the forest.'  # (then the chat goes on: the cache stops there)


def test_the_setting():
    assert Settings().lore.placement == 'background'
    assert Settings.model_validate({'lore': {'placement': 'end'}}).lore.placement == 'end'


async def test_in_the_settings_page(tmp_path):
    from rpapp.settings import SettingsStore
    from rpapp.ui.app import build_root
    store = SettingsStore(tmp_path)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open('/settings')
        [placement] = [e for e in user.client.layout.descendants() if 'lore-placement' in e._markers]
        assert placement.options == {'background': 'With the background, before the chat',
                                     'end': 'Just before the last message'}
        placement.set_value('end')
        assert store.load().lore.placement == 'end'


# --- in the prompt dialog -----------------------------------------------------------------------------

async def test_the_dialog_splits_the_lore():
    p = prompt('Welcome.', 'Hello.', 'Hi.', 'By the forest.', settings=late())

    def root() -> None:
        ui.button('Show', on_click=lambda: show_prompt(p, None, time=str, copy_js=lambda _: '')).mark('show')

    def section(user, key):
        [found] = [e for e in user.client.layout.descendants() if f'section-{key}' in e._markers]
        return found

    async with user_simulation(root) as user:
        await user.open('/')
        user.find(marker='show').click()
        await user.should_see(marker='prompt-chart')
        legend = [m.removeprefix('legend-') for e in user.client.layout.descendants() for m in e._markers
                  if m.startswith('legend-')]
        assert legend == ['character', 'lore', 'memories', 'messages', 'lore-chat', 'memories-chat']  # (as sent)
        section(user, 'lore').set_value(True)
        with user.scope(marker='section-lore'):
            await user.should_see('Magic has a price.')
            await user.should_see('2 more entries went in among the messages (see Lore among the messages).')
        section(user, 'lore-chat').set_value(True)
        with user.scope(marker='section-lore-chat'):
            await user.should_see('Those that come and go are placed just before the last message')
            await user.should_see('Placed just before the last message, about')
            await user.should_see('[Dusk falls.]')
        section(user, 'memories').set_value(True)
        with user.scope(marker='section-memories'):
            await user.should_see('Mira swore to protect the glade.')  # (always included: before the chat)
            await user.should_see('1 more memory went in among the messages (see Memories among the messages).')
        section(user, 'memories-chat').set_value(True)
        with user.scope(marker='section-memories-chat'):
            await user.should_see('Placed just before the last message, about')
            await user.should_see('Mira followed a fox.')
        section(user, 'messages').set_value(True)
        with user.scope(marker='section-messages'):
            await user.should_see('Here: the lore entries "Mood" and "Forest", and the memory "The fox" (see Lore '
                                  'among the messages and Memories among the messages).')  # (in a row: one line)
