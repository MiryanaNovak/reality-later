"""Lists of free-form values (ui/form.py: Form.chips, keep_typed): what's typed and left in the field, without
Enter, is added rather than lost. The browser's events are simulated: 'inputValue' (Quasar's input-value, as
NiceGUI names it) with what's typed, 'focusout' as the field is left."""
from nicegui import ui
from nicegui.testing import User, user_simulation
from pydantic import BaseModel

from rpapp.ui.form import Form, keep_typed, merged_values


class Thing(BaseModel):
    keys: list[str] = []


def type_and_leave(user: User, marker: str, text: str) -> None:
    field = user.find(marker=marker)
    field.trigger('inputValue', text)
    field.trigger('focusout')


async def test_typed_then_left():
    thing, saved = Thing(keys=['dragon']), []

    def page():
        Form(on_change=lambda: saved.append(list(thing.keys))).chips(thing, 'keys', 'Keywords').mark('keys')

    async with user_simulation(page) as user:
        await user.open('/')
        await typed_then_left(user, thing, saved)


async def typed_then_left(user: User, thing: Thing, saved: list) -> None:
    type_and_leave(user, 'keys', 'Main Hall')
    assert thing.keys == ['dragon', 'Main Hall'] and saved[-1] == ['dragon', 'Main Hall']
    type_and_leave(user, 'keys', 'dragon')  # (there already: not twice)
    type_and_leave(user, 'keys', '   ')     # (blank: nothing)
    assert thing.keys == ['dragon', 'Main Hall']

    field = user.find(marker='keys')
    field.trigger('inputValue', 'wyvern')
    field.trigger('inputValue', '')  # (Enter added it, or it was erased: nothing left typed)
    field.trigger('focusout')
    assert thing.keys == ['dragon', 'Main Hall']


async def test_tags_typed_at_once():
    def page():
        keep_typed(ui.input_chips('Tags', value=['fantasy'], new_value_mode='add-unique'), split=',').mark('tags')

    async with user_simulation(page) as user:
        await user.open('/')
        chips = user.find(marker='tags').elements.pop()
        type_and_leave(user, 'tags', 'female, elf,')
        assert chips.value == ['fantasy', 'female', 'elf']
        chips.set_value(['fantasy', 'female', 'elf', 'cozy, slow burn'])  # (typed with commas, then Enter)
        assert chips.value == ['fantasy', 'female', 'elf', 'cozy', 'slow burn']


def test_merged_values():
    assert merged_values(['a'], [' b ', 'a', '']) == ['a', ' b ']  # (as typed: a stop sequence may need its spaces)
    assert merged_values(None, ['x, y ,x', ' , '], ',') == ['x', 'y']
