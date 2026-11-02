"""The fields shown when editing a connection, one editor per connection type.

Each editor is a function decorated with @editor_for(SomeConnectionClass).
A subclass without its own editor uses its parent's. Shared parts (the model
picker, generation settings) are plain functions the editors call.

Adding a field to a connection class? Add one form.xxx(...) line here.
"""
from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from ..connections import (
    Connection, ModelError, ModelInfo, OpenAICompatibleConnection, OpenAIStyleConnection, OpenRouterConnection,
)
from .form import Form
from .widgets import folding_panel, panel

Editor = Callable[[Connection, Form], None]
EDITORS: dict[type, Editor] = {}

KIND_ICONS = {'openrouter': 'hub', 'openai_compatible': 'dns'}


def editor_for(cls: type[Connection]) -> Callable[[Editor], Editor]:
    def decorator(fn: Editor) -> Editor:
        EDITORS[cls] = fn
        return fn
    return decorator


def build_editor(conn: Connection, form: Form) -> None:
    for cls in type(conn).__mro__:
        if cls in EDITORS:
            EDITORS[cls](conn, form)
            return
    ui.label(f'There is no editor for {type(conn).display_name} connections yet.').classes('muted')


def kind_icon(conn: Connection) -> str:
    return KIND_ICONS.get(getattr(conn, 'kind', ''), 'cable')


# -- editors ----------------------------------------------------------------------

LOCAL_PRESETS = {
    'LM Studio': 'http://localhost:1234/v1',
    'Ollama': 'http://localhost:11434/v1',
    'llama.cpp': 'http://localhost:8080/v1',
    'KoboldCpp': 'http://localhost:5001/v1',
}

REASONING_OPTIONS = {
    '': 'Model default', 'none': 'Off', 'minimal': 'Minimal', 'low': 'Low', 'medium': 'Medium',
    'high': 'High', 'xhigh': 'Very high', 'max': 'Maximum',
}

EXTEND_METHODS = {
    'instruction': 'With a hidden instruction (works with every model)',
    'prefill': 'By prefill: the model writes the rest of its own message',
}

SYSTEM_IN_CHAT = {
    'system': 'Send as system messages',
    'user': 'Send as user messages (for models that only read the first system message)',
}

QUANTIZATIONS = ['int4', 'int8', 'fp4', 'fp6', 'fp8', 'fp16', 'bf16', 'fp32', 'unknown']


@editor_for(OpenRouterConnection)
def openrouter_editor(conn: OpenRouterConnection, form: Form) -> None:
    with panel('Access'):
        form.text(conn, 'name', 'Name')
        form.text(conn, 'api_key', 'API key', password=True, hint='Create one at openrouter.ai/keys')

    def reasoning() -> None:
        form.select(conn, 'reasoning_effort', 'Reasoning effort', REASONING_OPTIONS,
                    hint='For thinking models. More effort is slower and costs more')

    model_panel(conn, form, extra=reasoning)
    generation_panel(conn, form)

    p = conn.provider
    with folding_panel('Provider routing', 'Which companies hosting the model OpenRouter may use'):
        form.select(p, 'data_collection', 'Data policy', {
            'allow': 'Allow all providers',
            'deny': 'Skip providers that may store or train on prompts',
        })
        form.select(p, 'sort', 'Prefer', {
            '': 'OpenRouter default', 'price': 'Lowest price', 'throughput': 'Fastest output',
            'latency': 'Quickest first word',
        })
        form.chips(p, 'order', 'Try these providers first',
                   hint='e.g. deepinfra, together. Press Enter after each')
        form.switch(p, 'allow_fallbacks', 'Use other providers when those are unavailable')
        form.chips(p, 'only', 'Use only these providers', hint='Empty means any provider')
        form.chips(p, 'ignore', 'Never use these providers')
        form.multi_select(p, 'quantizations', 'Allowed precisions', QUANTIZATIONS,
                          hint='Empty means any. int4 and fp4 can hurt quality')

    with folding_panel('Advanced'):
        form.text(conn, 'app_name', 'App name', hint='Shown in your OpenRouter activity page')
        form.text(conn, 'app_url', 'App URL', hint='Optional, sent along with the app name')
        form.text(conn, 'base_url', 'Base URL')
        form.number(conn, 'timeout', 'Timeout (seconds)', min=1, step=10, optional=False,
                    hint='How long to wait for the model before giving up')


@editor_for(OpenAICompatibleConnection)
def compatible_editor(conn: OpenAICompatibleConnection, form: Form) -> None:
    with panel('Server'):
        form.text(conn, 'name', 'Name')
        url = form.text(conn, 'base_url', 'Base URL', hint='Usually ends with /v1')
        with ui.row().classes('items-center gap-1 -mt-1'):
            ui.label('Fill in for').classes('text-sm muted mr-1')
            for label, value in LOCAL_PRESETS.items():
                ui.chip(label, on_click=lambda v=value: url.set_value(v)).props('outline dense clickable')
        form.text(conn, 'api_key', 'API key', password=True,
                  hint='Leave empty if the server doesn\'t need one')
    model_panel(conn, form)
    generation_panel(conn, form)
    with folding_panel('Advanced'):
        form.number(conn, 'timeout', 'Timeout (seconds)', min=1, step=10, optional=False,
                    hint='Local models can be slow on long chats')


# -- shared parts -----------------------------------------------------------------

def model_panel(conn: OpenAIStyleConnection, form: Form, extra: Callable[[], None] | None = None) -> None:
    with panel('Model'):
        model_input = form.text(conn, 'model', 'Model id', hint='Type it, or pick one from the list')
        with model_input.add_slot('append'):
            ui.button(icon='list', on_click=lambda: browse()) \
                .props('flat round dense aria-label="Browse models"').tooltip('Browse models')
        context = form.number(conn, 'context_window', 'Context size (tokens)', integer=True, min=1, step=1024,
                              placeholder='Unknown',
                              hint='Filled in when you pick a model from the list. Long chats are trimmed to '
                                   'fit: lower it to spend less on them')
        if extra:
            extra()
        form.select(conn, 'system_in_chat', 'Instructions inside the chat', SYSTEM_IN_CHAT,
                    hint='Lore placed among the messages, and the final instructions. Choose "user" if the '
                         'model ignores them')
        form.select(conn, 'extend_method', 'Extending a reply', EXTEND_METHODS,
                    hint='How "Extend the last reply" asks the model to go on. Prefill is the most seamless, but not '
                         'every model or server allows it: llama.cpp and vLLM do, OpenAI\'s models and the latest '
                         'Claude ones don\'t. If extending fails, switch back.').mark('extend-method')

    models: list[ModelInfo] = []

    with ui.dialog() as dialog, ui.card().classes('dialog-card'):
        ui.label('Choose a model').classes('panel-title')
        search = ui.input(placeholder='Search').props('clearable debounce=200 autofocus').classes('w-full')
        status = ui.label().classes('text-sm muted')
        results = ui.list().classes('w-full browse-list')
        with ui.row().classes('w-full justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')

    def show_results() -> None:
        query = (search.value or '').lower().strip()
        hits = [m for m in models if query in m.id.lower() or query in m.name.lower()]
        status.text = (f'{len(hits)} of {len(models)} models' if query else f'{len(models)} models') + \
            (', showing the first 100. Type to narrow down.' if len(hits) > 100 else '')
        results.clear()
        with results:
            for m in hits[:100]:
                with ui.item(on_click=lambda m=m: pick(m)):
                    with ui.item_section():
                        ui.item_label(m.name)
                        if m.name != m.id:
                            ui.item_label(m.id).props('caption').classes('break-all')
                    if m.context_length:
                        with ui.item_section().props('side'):
                            ui.label(f'{m.context_length // 1000}k').classes('text-sm muted')

    search.on_value_change(show_results)

    async def browse() -> None:
        dialog.open()
        if models:
            show_results()
            return
        status.text = 'Loading the model list…'
        results.clear()
        try:
            models.extend(await conn.list_models())
        except ModelError as err:
            status.text = f'Couldn\'t load the list. {err}'
            return
        show_results()

    def pick(m: ModelInfo) -> None:
        model_input.value = m.id
        if m.context_length:
            context.value = m.context_length
        dialog.close()


def generation_panel(conn: Connection, form: Form) -> None:
    p = conn.params
    with folding_panel('Generation defaults',
                       'Used unless a character or chat sets its own. Empty fields use the model\'s default.'):
        with ui.element('div').classes('field-grid'):
            form.number(p, 'max_tokens', 'Max reply length (tokens)', integer=True, min=1, step=50)
            form.number(p, 'temperature', 'Temperature', min=0, max=2, step=0.05,
                        hint='0 to 2. Higher gives more varied replies')
            form.number(p, 'min_p', 'Min P', min=0, max=1, step=0.01, hint='0 to 1')
            form.number(p, 'top_p', 'Top P', min=0, max=1, step=0.01, hint='Above 0, up to 1')
            form.number(p, 'top_k', 'Top K', integer=True, min=0, step=1)
            form.number(p, 'top_a', 'Top A', min=0, max=1, step=0.01, hint='0 to 1')
            form.number(p, 'repetition_penalty', 'Repetition penalty', min=0, max=2, step=0.01,
                        hint='Above 0, up to 2. 1 means off')
            form.number(p, 'frequency_penalty', 'Frequency penalty', min=-2, max=2, step=0.05, hint='-2 to 2')
            form.number(p, 'presence_penalty', 'Presence penalty', min=-2, max=2, step=0.05, hint='-2 to 2')
            form.number(p, 'seed', 'Seed', integer=True,
                        hint='Repeatable replies, where supported')
        form.chips(p, 'stop', 'Stop sequences',
                   hint='The reply stops at any of these. Press Enter after each')
