"""Widgets that edit one attribute of a pydantic object each.

Every change is written straight into the object. Pydantic checks it, so a bad
value (a temperature of 5, say) shows an error under the field and isn't
applied. After each accepted change, `on_change` runs (the editor uses it to save).

    form = Form(on_change=save)
    form.text(conn, 'name', 'Name')
    form.number(conn.params, 'temperature', 'Temperature', min=0, max=2, step=0.05)

For values that aren't a plain attribute, use form.bind(element, get, set).

Lists of free-form values (form.chips) add a value on Enter, and also when the field is left with a
value typed in it (keep_typed): that one isn't silently lost.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from nicegui import ui
from nicegui.elements.mixins.validation_element import ValidationElement
from pydantic import BaseModel, ValidationError

from ..tokens import estimate

NOT_SET = ''  # the dropdown key that stands for None

TRISTATE = {NOT_SET: 'Use the global setting', True: 'Yes', False: 'No'}


class Form:
    def __init__(self, on_change: Callable[[], Any] | None = None) -> None:
        self.on_change = on_change

    # -- widgets -------------------------------------------------------------

    def text(self, obj: BaseModel, attr: str, label: str, *, hint: str = '',
             placeholder: str = '', password: bool = False) -> ui.input:
        element = ui.input(label, value=getattr(obj, attr), placeholder=placeholder or None,
                           password=password, password_toggle_button=password)
        if password:
            element.props('autocomplete=off')
        return self._wire_attr(element, obj, attr, hint)

    def textarea(self, obj: BaseModel, attr: str, label: str, *, hint: str = '',
                 placeholder: str = '') -> ui.textarea:
        element = ui.textarea(label, value=getattr(obj, attr), placeholder=placeholder or None)
        element.props('autogrow')
        return self._wire_attr(element, obj, attr, hint)

    def long_text(self, obj: BaseModel, attr: str, label: str, *, min_height: str = '10rem',
                  placeholder: str = '', tokens: bool = True) -> ui.textarea:
        """A text area that grows with its text, with its length shown underneath.

        `tokens`: with the estimated tokens too (leave them out for text the AI never gets).
        """
        element = self.textarea(obj, attr, label, placeholder=placeholder)
        element.props(f'input-style="min-height: {min_height}"')
        count = text_stats if tokens else lambda text: f'{len(text)} characters'
        stats = ui.label(count(getattr(obj, attr))).classes('muted text-sm -mt-2')
        element.on_value_change(lambda e: stats.set_text(count(e.value or '')))
        return element

    def number(self, obj: BaseModel, attr: str, label: str, *, hint: str = '', step: float = 1,
               min: float | None = None, max: float | None = None, integer: bool = False,
               placeholder: str = 'Default', optional: bool = True) -> ui.number:
        """With `optional`, the field can be emptied (None): for generation settings, "use the default"."""
        element = ui.number(label, value=getattr(obj, attr), step=step, min=min, max=max,
                            placeholder=placeholder, precision=0 if integer else None)
        if optional:
            element.props('clearable')
        to_model = (lambda v: None if v is None else int(v)) if integer else (lambda v: v)
        return self._wire_attr(element, obj, attr, hint, to_model)

    def select(self, obj: BaseModel, attr: str, label: str, options: dict[Any, str], *,
               hint: str = '') -> ui.select:
        """A dropdown. The key NOT_SET ('') stands for None."""
        current = getattr(obj, attr)
        value = NOT_SET if current is None else current
        element = ui.select(with_unknown(options, value), label=label, value=value)
        return self._wire_attr(element, obj, attr, hint, lambda v: None if v == NOT_SET else v)

    def choice(self, label: str, options: dict[Any, str], get: Callable[[], Any], set: Callable[[Any], None], *,
               hint: str = '') -> ui.select:
        """A dropdown for a value that isn't a plain attribute."""
        return self.bind(ui.select(with_unknown(options, get()), label=label), get, set, hint=hint)

    def tristate(self, obj: BaseModel, attr: str, label: str, *, hint: str = '') -> ui.select:
        """Yes / No / "use the global setting" (None)."""
        return self.select(obj, attr, label, TRISTATE, hint=hint)

    def multi_select(self, obj: BaseModel, attr: str, label: str, options: list[str] | dict[str, str], *,
                     hint: str = '') -> ui.select:
        values = list(getattr(obj, attr))
        if isinstance(options, list):
            options = {o: o for o in options}
        for v in values:
            options = with_unknown(options, v)
        element = ui.select(options, label=label, value=values, multiple=True)
        element.props('use-chips')
        return self._wire_attr(element, obj, attr, hint, lambda v: list(v or []))

    def chips(self, obj: BaseModel, attr: str, label: str, *, hint: str = '', split: str = '') -> ui.input_chips:
        """A list of free-form strings. Type a value and press Enter (or leave the field) to add it.
        `split`: a character separating several values typed at once (see keep_typed)."""
        element = keep_typed(ui.input_chips(label, value=list(getattr(obj, attr)), new_value_mode='add-unique'),
                             split=split)
        return self._wire_attr(element, obj, attr, hint, lambda v: list(v or []))

    def switch(self, obj: BaseModel, attr: str, label: str, *, invert: bool = False) -> ui.switch:
        """An on/off switch. `invert` shows True as off (for "disabled" flags shown as "enabled")."""
        element = ui.switch(label, value=bool(getattr(obj, attr)) != invert)
        return self._wire_attr(element, obj, attr, '', lambda v: bool(v) != invert)

    # -- plumbing ------------------------------------------------------------

    def bind(self, element: Any, get: Callable[[], Any], set: Callable[[Any], None], *, hint: str = '') -> Any:
        """Wire any value element to a getter and a setter. The setter may raise to reject a value."""
        element.value = get()
        if hint:
            element.props['hint'] = hint
        if not isinstance(element, (ui.switch, ui.checkbox)):
            element.classes('w-full')
        if isinstance(element, ValidationElement):
            element.without_auto_validation()  # the errors set below would otherwise be cleared

        def handle(e: Any) -> None:
            try:
                set(e.value)
            except (ValidationError, ValueError, TypeError) as err:
                _show_error(element, err)
                return
            _show_error(element, None)
            if self.on_change:
                self.on_change()

        element.on_value_change(handle)
        return element

    def _wire_attr(self, element: Any, obj: BaseModel, attr: str, hint: str,
                   to_model: Callable[[Any], Any] = lambda v: v) -> Any:
        from_value = element.value  # already in the widget's own form (e.g. '' for None)
        self.bind(element, lambda: from_value, lambda v: setattr(obj, attr, to_model(v)), hint=hint)
        return element


def text_stats(text: str) -> str:
    """'914 characters, about 262 tokens' (the estimate prompts are fitted with, see tokens.py)."""
    return f'{len(text)} characters, about {estimate(text)} tokens'


def with_unknown(options: dict[Any, str], value: Any) -> dict[Any, str]:
    """Add `value` to the options if it's missing, so a file from a newer program still shows."""
    if value in options or value == NOT_SET:
        return options
    return {**options, value: f'{value} (unknown value)'}


def keep_typed(chips: ui.input_chips, *, split: str = '') -> ui.input_chips:
    """A chips field adds what's typed on Enter only; left with a value typed in it, the field drops it,
    and its value looks taken when it isn't. Here, it's added then too.

    It's done on the browser's `focusout`, which arrives before the click that took the focus away (on
    Publish, say), while Quasar's `blur` arrives after it. `split`: a character separating values typed
    at once ("fantasy, elf" → two tags, with ','), on Enter too. Values are added as typed, but blank ones."""
    typed = ['']

    def on_input(e: Any) -> None:
        typed[0] = e.args if isinstance(e.args, str) else ''

    def on_leave() -> None:
        text, typed[0] = typed[0], ''
        if text.strip():
            chips.value = merged_values(chips.value, [text], split)

    def on_change(e: Any) -> None:
        if any(split in v for v in e.value or ()):
            chips.value = merged_values([], e.value, split)

    chips.on('input-value', on_input)
    chips.on('focusout', on_leave, args=[])
    if split:
        chips.on_value_change(on_change)
    return chips


def merged_values(values: Iterable[str] | None, new: Iterable[str], split: str = '') -> list[str]:
    """`values`, then those of `new` not there yet (each cut at `split`, if given, and its parts stripped)."""
    out = list(values or ())
    for text in new:
        for part in (text.split(split) if split else [text]):
            part = part.strip() if split else part
            if part.strip() and part not in out:
                out.append(part)
    return out


def _show_error(element: Any, err: Exception | None) -> None:
    if not isinstance(element, ValidationElement):
        if err is not None:
            ui.notify(error_text(err, location=False), type='negative')
        return
    element.error = None if err is None else error_text(err, location=False)


def error_text(err: Exception, *, location: bool = True) -> str:
    """A one-line description of an error, e.g. 'params.temperature: Input should be less than or equal to 2'."""
    if isinstance(err, ValidationError) and err.errors():
        first = err.errors()[0]
        loc = '.'.join(str(part) for part in first['loc'])
        return f'{loc}: {first["msg"]}' if location and loc else first['msg']
    return str(err)
