"""The Settings page: one folding section per topic (Chat, Lore books, Memory, Vector search, Appearance,
Devices).

    /settings

To add a section: add a sub-object to Settings (settings.py) and a
`with folding_panel(...)` block below, editing it with the same Form.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nicegui import background_tasks, ui

from ..access import Access
from ..connections import Connection
from ..embeddings import SOURCES, DownloadError, EmbeddingModel, catalog, download, is_installed, runtime_problem, \
    uninstall
from ..memory import ANSWER_FORMAT, DEFAULT_INSTRUCTIONS, DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE, MATERIAL
from ..prompts import DEFAULT_CONTEXT
from ..presets import PromptPreset
from ..settings import ConnectionRef, MemorySettings, PresetRef, Settings, SettingsStore
from ..storage import JsonStore
from ..vector_files import delete_model_vectors
from . import appearance, theme
from .devices_page import DevicesSection
from .form import NOT_SET, Form, error_text, text_stats
from .widgets import confirm, folding_panel, page_bar

if TYPE_CHECKING:
    from ..vectors import Vectorizer

EXAMPLE_DIALOGUES = {
    'make_room': 'Send them while the whole chat fits',
    'always': 'Always send them',
}
RETURN_KEY = {
    'send': 'Sends the message',
    'new_line': 'Adds a new line',
}


@dataclass
class Download:
    """A model being downloaded (in the background: it goes on if the page is left)."""
    model: EmbeddingModel
    done: int = 0
    total: int = 0
    task: asyncio.Task | None = field(default=None, repr=False)


class SettingsSection:
    def __init__(self, store: SettingsStore, connections: JsonStore[Connection], access: Access | None = None,
                 presets: JsonStore[PromptPreset] | None = None, vectors: Vectorizer | None = None) -> None:
        self.store = store
        self.connections = connections
        self.presets = presets
        self.devices = DevicesSection(access)
        self.vectors = vectors       # vector search (None: no section, in some tests)
        self.download: Download | None = None

    def page(self) -> None:
        page_bar('Settings')
        try:
            settings = self.store.load()
        except ValueError as err:
            with ui.column().classes('panel gap-2').mark('settings-error'):
                ui.label('The settings file can\'t be read').classes('panel-title text-negative')
                ui.label(f'{self.store.path}: {error_text(err)}').classes('text-sm break-all')
                ui.label('Nothing is saved over it. Fix or delete the file, then reload this page.').classes('muted')
            return
        ui.label('Changes save automatically.').classes('muted')

        form = Form(on_change=lambda: self.store.save_soon(settings))
        connections = {c.id: c for c in self.connections.list()}
        self._refresh_names(settings, connections)

        with folding_panel('Chat', 'The model and prompt chats use, and the Return key', opened=True):
            self._chat_connection(settings, connections, form)
            self._chat_preset(settings, form)
            form.select(settings.chat, 'example_dialogues', 'Characters\' example dialogues', EXAMPLE_DIALOGUES,
                        hint='They show the character\'s voice, which matters most early on: once the chat is long, '
                             'it shows that voice by itself. By default, the examples then make room for it, the '
                             'last ones first.').mark('example-dialogues-setting')
            self._return_key(settings, form)

        lore = settings.lore
        with folding_panel('Lore books', 'How lore book entries are picked for a reply'):
            with ui.element('div').classes('field-grid'):
                form.number(lore, 'scan_depth', 'Messages to scan', integer=True, min=0, max=1000, optional=False,
                            hint='How many of the last messages are searched for keywords')
                form.number(lore, 'budget_percent', 'Lore budget (%)', integer=True, min=1, max=100, step=5,
                            optional=False, hint='The most of the prompt\'s space lore entries may take')
            form.switch(lore, 'recursive', 'Entries can trigger other entries')
            ui.label('The text of the entries that go in is searched for keywords too.') \
                .classes('text-sm muted -mt-3 ml-12')
            form.switch(lore, 'whole_words', 'Match whole words')
            ui.label('"sword" then doesn\'t match "swordsman".').classes('text-sm muted -mt-3 ml-12')
            form.switch(lore, 'case_sensitive', 'Case-sensitive')
            ui.label('Off: "Brann" also matches "brann" and "BRANN".').classes('text-sm muted -mt-3 ml-12')
            ui.label('Entries can override these three settings, and the number of messages to scan.') \
                .classes('text-sm muted')

        with folding_panel('Memory', 'What the AI remembers of old messages, in each chat'):
            self._memory(settings.memory, connections, form)

        if self.vectors is not None:
            with folding_panel('Vector search', 'Memories and lore entries picked by meaning too'):
                self._vector_search(settings, form)

        with folding_panel('Appearance', 'Light or dark, text size, accent color'):
            self._appearance(settings)

        self.devices.section()

    @staticmethod
    def _return_key(settings: Settings, form: Form) -> None:
        ui.label('The Return key, when you write a message').classes('step-title mt-2')
        with ui.element('div').classes('field-grid'):
            form.select(settings.chat, 'return_key_desktop', 'On computers', RETURN_KEY).mark('return-key-desktop')
            form.select(settings.chat, 'return_key_mobile', 'On phones and tablets', RETURN_KEY) \
                .mark('return-key-mobile')
        ui.label('Either way, Shift+Return adds a new line and Ctrl+Return sends. Phones and tablets are the '
                 'devices you use by touch; pages already open change when reloaded.').classes('text-sm muted')

    # -- connections -------------------------------------------------------------------

    def _refresh_names(self, settings: Settings, connections: dict[str, Connection]) -> None:
        """Keep the remembered names of the chosen connections up to date while they exist."""
        changed = False
        for section in (settings.chat, settings.memory):
            ref = section.connection
            if ref and ref.id in connections and connections[ref.id].name != ref.name:
                section.connection = ConnectionRef(id=ref.id, name=connections[ref.id].name)
                changed = True
        preset = settings.chat.preset
        found = self.presets.get(preset.id) if preset and self.presets else None
        if preset and found and found.name != preset.name:  # renamed on the Prompts page
            settings.chat.preset = PresetRef(id=preset.id, name=found.name)
            changed = True
        if changed:
            self.store.save_soon(settings)

    def _chat_connection(self, settings: Settings, connections: dict[str, Connection], form: Form) -> None:
        chat = settings.chat
        if not connections and not chat.connection:
            with ui.row().classes('items-center gap-1').mark('no-connections'):
                ui.label('You have no connections yet: chats need one to reach a model.').classes('muted')
                ui.link('Create one', '/connections')
            return

        def context_line(connection: Connection) -> None:
            context = connection.context_window
            ui.label(f'Context size: {context:,} tokens. Longer chats are trimmed to fit.' if context else
                     f'Context size unknown: set it in the connection, or chats are trimmed to {DEFAULT_CONTEXT:,} tokens.') \
                .classes('text-sm muted -mt-1').mark('context-size')

        self._connection_choice(chat, connections, form, label='Chat connection', none_label='None chosen',
                                hint='The connection used to write the character\'s replies', more=context_line)

    def _chat_preset(self, settings: Settings, form: Form) -> None:
        """Which prompt (presets.py) chats use."""
        chat = settings.chat
        presets = {p.id: p for p in sorted(self.presets.list(), key=lambda p: p.name.lower())} if self.presets else {}
        options: dict[str, str] = {NOT_SET: 'None: no instructions'} | {p.id: p.name for p in presets.values()}
        if chat.preset and chat.preset.id not in presets:
            options[chat.preset.id] = f'{chat.preset.name} (missing)'

        def choose(value: str) -> None:
            chat.preset = PresetRef(id=value, name=presets[value].name) if value in presets else None
            details.refresh()

        form.choice('Prompt', options, get=lambda: chat.preset.id if chat.preset else NOT_SET, set=choose,
                    hint='The instructions sent with every reply').mark('chat-preset')

        @ui.refreshable
        def details() -> None:
            ref = chat.preset
            with ui.row().classes('items-center gap-3 text-sm -mt-1'):
                if ref is not None and ref.id not in presets:
                    ui.label(f'Prompt "{ref.name}" is missing: it was deleted, or its file was renamed. Choose '
                             'another one.').classes('text-negative').mark('preset-missing')
                elif ref is not None:
                    ui.link('Edit this prompt', f'/prompts/{ref.id}')
                ui.link('All prompts' if presets else 'Make a prompt', '/prompts')

        details()

    def _connection_choice(self, section: Any, connections: dict[str, Connection], form: Form, *, label: str,
                           none_label: str, hint: str, more: Callable[[Connection], None] | None = None,
                           marker: str = 'connection-missing') -> None:
        """A dropdown for `section.connection` (a ConnectionRef or None), with the chosen one's model below."""
        options: dict[str, str] = {NOT_SET: none_label} | {c.id: c.name for c in connections.values()}
        if section.connection and section.connection.id not in connections:
            options[section.connection.id] = f'{section.connection.name} (missing)'

        def choose(value: str) -> None:
            section.connection = ConnectionRef(id=value, name=connections[value].name) \
                if value in connections else None
            details.refresh()

        form.choice(label, options, get=lambda: section.connection.id if section.connection else NOT_SET,
                    set=choose, hint=hint)

        @ui.refreshable
        def details() -> None:
            ref = section.connection
            if ref is None:
                return
            connection = connections.get(ref.id)
            if connection is None:
                ui.label(f'Connection "{ref.name}" is missing. It was deleted, or its file was renamed: '
                         'choose another one.').classes('text-sm text-negative').mark(marker)
                return
            with ui.row().classes('items-center gap-2 text-sm muted'):
                ui.label(f'{type(connection).display_name}, model {connection.model or "not chosen yet"}.')
                ui.link('Edit connection', f'/connections/{connection.id}')
            if more:
                more(connection)

        details()

    # -- memory ------------------------------------------------------------------------

    # -- vector search -----------------------------------------------------------------

    def _vector_search(self, settings: Settings, form: Form) -> None:
        vs = settings.vectors
        models = catalog()
        ui.label('An entry then also comes into the prompt when it\'s close in meaning to the last messages, even '
                 'without its keywords: a memory about Brann the ferryman comes back when the chat talks about "the '
                 'old man at the river crossing". Keywords work as before. It runs on this computer, with a model '
                 'you download once.').classes('text-sm muted -mt-2')
        form.switch(vs, 'memories', 'For memories').mark('vectors-memories')
        form.switch(vs, 'lore', 'For lore books').mark('vectors-lore')
        choice = form.select(vs, 'model', 'Model', {key: m.label for key, m in models.items()},
                             hint='English-only models don\'t understand chats in other languages. Changing the '
                                  'model vectorizes the memories and lore books again, before the next replies.') \
            .mark('vectors-model')

        @ui.refreshable
        def state() -> None:
            self._model_state(settings, form, models.get(vs.model), state.refresh)

        def chosen() -> None:
            if self.vectors is not None:
                self.vectors.unload()  # (the next reply loads the one chosen)
            state.refresh()

        choice.on_value_change(chosen)
        state()
        form.number(vs, 'max_entries', 'At most, by meaning alone', integer=True, min=1, max=20, optional=False,
                    hint='Entries it may bring in for a reply, closest first: for the memories, and for the lore '
                         'books. They count in the same budget as those found by keywords, after them.') \
            .mark('vectors-max')

    def _model_state(self, settings: Settings, form: Form, model: EmbeddingModel | None, refresh: Callable) -> None:
        """The chosen model: downloaded or not, and how close is close enough with it."""
        vs = settings.vectors
        if model is None:
            ui.label(f'The model "{vs.model}" isn\'t one this version of the app knows: choose another one.') \
                .classes('text-sm text-negative')
            return
        models_dir = self.vectors.models_dir
        ui.label(f'{model.languages}. {model.about} License: {model.license}.').classes('text-sm muted')
        problem = runtime_problem()
        downloading = self.download if self.download and self.download.model.key == model.key else None
        with ui.column().classes('w-full gap-1').mark('vectors-state'):
            if problem:
                ui.label(f'Vector search can\'t run on this computer: {problem}.').classes('text-sm text-negative')
            elif downloading:
                self._progress(downloading, refresh)
            elif is_installed(models_dir, model):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('check_circle', size='sm').classes('text-positive')
                    ui.label(f'Downloaded ({model.megabytes} MB)').classes('text-sm').mark('vectors-installed')

                    async def delete() -> None:
                        if not await confirm(f'Delete {model.name}?', 'Vector search then works again once '
                                             'it\'s downloaded again.', 'Delete'):
                            return
                        if self.vectors is not None:
                            self.vectors.unload()
                        uninstall(models_dir, model)
                        delete_model_vectors(self.vectors.data_dir, model.key)
                        refresh()

                    ui.button('Delete', icon='delete', on_click=delete).props('flat dense no-caps') \
                        .mark('vectors-delete')
            elif not model.pinned:
                ui.label('This model can\'t be downloaded yet: its files\' fingerprints aren\'t recorded in the app '
                         '(tools/pin_models.py records them).').classes('text-sm text-negative').mark('vectors-unpinned')
            else:
                with ui.row().classes('w-full items-end no-wrap gap-2'):
                    form.select(vs, 'source', 'Download from', {k: src.name for k, src in SOURCES.items()}) \
                        .classes('grow').mark('vectors-source')
                    ui.button(f'Download ({model.megabytes} MB)', icon='download',
                              on_click=lambda: self._start_download(model, vs.source, refresh)) \
                        .props('unelevated no-caps').mark('vectors-download')
                ui.label('Each file is checked before it\'s used: one that isn\'t exactly the expected one is '
                         'refused, wherever it came from.').classes('text-sm muted')
                if vs.memories or vs.lore:
                    ui.label('Until it\'s downloaded, memories and lore entries are picked by their keywords only.') \
                        .classes('text-sm text-warning').mark('vectors-not-downloaded')

        def set_threshold(value: float | None) -> None:
            thresholds = dict(vs.thresholds)
            if value is None or round(value, 2) == model.threshold:
                thresholds.pop(model.key, None)
            else:
                thresholds[model.key] = round(min(1.0, max(0.0, value)), 2)
            vs.thresholds = thresholds

        form.bind(ui.number('Closeness needed', min=0, max=1, step=0.01, format='%.2f',
                            placeholder=f'{model.threshold:.2f}'),
                  lambda: vs.threshold(model.key, model.threshold), set_threshold,
                  hint=f'From 0 (unrelated) to 1: higher brings in fewer entries. Each model has its own scale: '
                       f'{model.threshold:.2f} by default for this one. The prompt view shows how close each entry '
                       f'was.').mark('vectors-threshold')

    def _start_download(self, model: EmbeddingModel, source: str, refresh: Callable) -> None:
        if self.download is not None:
            ui.notify(f'{self.download.model.name} is being downloaded: wait for it, or stop it.')
            return
        state = self.download = Download(model)

        def progress(done: int, total: int) -> None:
            state.done, state.total = done, total

        async def run() -> None:
            try:
                await download(model, self.vectors.models_dir, source, on_progress=progress)
                if self.vectors is not None:
                    self.vectors.unload()
                _notify_all(f'{model.name} is downloaded: vector search can use it.', 'positive')
            except asyncio.CancelledError:
                pass
            except DownloadError as e:
                _notify_all(f'Not downloaded: {e}', 'negative')
            except Exception as e:  # (a disk full...)
                _notify_all(f'Not downloaded: {e}', 'negative')
            finally:
                self.download = None

        state.task = background_tasks.create(run(), name=f'download {model.key}')
        refresh()

    def _progress(self, state: Download, refresh: Callable) -> None:
        bar = ui.linear_progress(value=0, show_value=False).props('rounded size=8px').mark('vectors-progress')
        with ui.row().classes('w-full items-center justify-between'):
            text = ui.label('Downloading…').classes('text-sm muted')

            def stop() -> None:
                if state.task:
                    state.task.cancel()

            ui.button('Stop', icon='close', on_click=stop).props('flat dense no-caps').mark('vectors-stop')

        def update() -> None:
            if self.download is not state:  # done (or stopped): show what's next
                timer.cancel()
                refresh()
                return
            if state.total:
                bar.value = state.done / state.total
                text.text = f'Downloading… {state.done / 1e6:.0f} / {state.total / 1e6:.0f} MB'

        timer = ui.timer(0.3, update)

    def _appearance(self, settings: Settings) -> None:
        look = appearance.device_look()

        def changed() -> None:
            appearance.change_look(look)

        ui.label('On this device').classes('step-title')
        ui.label('Each device keeps its own: your phone can be dark and your computer light.') \
            .classes('text-sm muted -mt-2')
        with ui.column().classes('gap-1'):
            ui.label('Light or dark').classes('text-sm')
            ui.toggle(appearance.MODES, value=look.mode,
                      on_change=lambda e: (setattr(look, 'mode', e.value), changed())) \
                .props('no-caps unelevated toggle-color=primary').mark('look-mode')
        with ui.column().classes('gap-1'):
            ui.label('Text size in chats').classes('text-sm')
            ui.toggle({key: label for key, (label, _) in appearance.SIZES.items()}, value=look.size,
                      on_change=lambda e: (setattr(look, 'size', e.value), changed())) \
                .props('no-caps unelevated toggle-color=primary').mark('look-size')

        ui.label('On every device').classes('step-title mt-3')

        @ui.refreshable
        def swatches() -> None:
            current = settings.appearance.accent if settings.appearance.accent in theme.ACCENTS \
                else theme.DEFAULT_ACCENT
            with ui.row().classes('gap-3 items-start').mark('accents'):
                for name, (label, light, dark) in theme.ACCENTS.items():
                    with ui.column().classes('items-center gap-1'):
                        with ui.button(on_click=lambda n=name: choose(n)) \
                                .props(f'round unelevated aria-label="{label}" aria-pressed={str(name == current).lower()}') \
                                .classes('accent-swatch' + (' chosen' if name == current else '')) \
                                .style(f'--swatch-light: {light}; --swatch-dark: {dark}').mark(f'accent-{name}'):
                            if name == current:
                                ui.icon('check')
                        ui.label(label).classes('text-xs' + ('' if name == current else ' muted'))

        def choose(name: str) -> None:
            settings.appearance.accent = name
            self.store.save(settings)
            css = theme.accent_css(name)
            ui.run_javascript(f'document.getElementById("rp-accent").textContent = {json.dumps(css)}')
            swatches.refresh()

        ui.label('Accent color: buttons, links and highlights. Pages open elsewhere change when reloaded.') \
            .classes('text-sm muted -mt-2')
        swatches()

    def _memory(self, memory: MemorySettings, connections: dict[str, Connection], form: Form) -> None:
        form.switch(memory, 'enabled', 'Keep a memory of each chat')
        ui.label('When old messages leave the prompt, the AI writes down what must not be forgotten (people, '
                 'places, events, promises...) as memories with keywords. A memory comes back into the prompt '
                 'when its keywords appear. New memories are added when you post your next message: you can '
                 'review them before. You can also generate them when you want, from a chat\'s menu.') \
            .classes('text-sm muted -mt-2')
        self._connection_choice(memory, connections, form, label='Memory connection',
                                none_label='Same as the chat connection', marker='memory-connection-missing',
                                hint='A cheaper or faster model can do this background work, or a smarter one')
        with ui.element('div').classes('field-grid'):
            # stack-label: the label stays on top, so the default shows in the empty field
            form.number(memory, 'temperature', 'Temperature', min=0, max=2, step=0.05,
                        placeholder=f'{DEFAULT_TEMPERATURE} (default)',
                        hint='Instead of the connection\'s: low gives consistent notes').props('stack-label')
            form.number(memory, 'max_tokens', 'Max reply length (tokens)', integer=True, min=1, step=100,
                        placeholder=f'{DEFAULT_MAX_TOKENS} (default)',
                        hint='Instead of the connection\'s, which suits chat replies').props('stack-label')
            form.number(memory, 'budget_percent', 'Memory budget (%)', integer=True, min=1, max=100, step=5,
                        optional=False, hint='Share of a chat prompt for memory entries')

        self._memory_instructions(memory, form)

        ui.label('Added after your instructions. It can\'t be changed, so the app can always read the answer:') \
            .classes('text-sm muted mt-2')
        ui.label(ANSWER_FORMAT).classes('prompt-text').mark('memory-format')
        ui.label('Then, in a message of its own, what to work on (filled in for each chat):') \
            .classes('text-sm muted mt-2')
        ui.label(MATERIAL).classes('prompt-text').mark('memory-material')

    def _memory_instructions(self, memory: MemorySettings, form: Form) -> None:
        """The instructions, showing the default ones until you change them ('' is saved for the default)."""
        def set_value(value: str | None) -> None:
            value = value or ''
            memory.instructions = '' if value.strip() == DEFAULT_INSTRUCTIONS.strip() else value
            state.refresh()

        editor = ui.textarea('Memory instructions') \
            .props('autogrow stack-label input-style="min-height: 14rem"').mark('memory-instructions')
        form.bind(editor, lambda: memory.instructions or DEFAULT_INSTRUCTIONS, set_value)

        @ui.refreshable
        def state() -> None:
            with ui.row().classes('w-full items-center gap-2 -mt-2 text-sm muted'):
                ui.label(text_stats(editor.value or ''))
                ui.space()
                if not memory.instructions:
                    ui.label('Empty: the default instructions are used.' if not (editor.value or '').strip()
                             else 'The default instructions.').mark('memory-default')
                else:
                    ui.label('Your own instructions.')
                    ui.button('Reset to default', icon='restart_alt',
                              on_click=lambda: editor.set_value(DEFAULT_INSTRUCTIONS)) \
                        .props('flat dense no-caps').mark('memory-reset')

        state()
        editor.on_value_change(lambda: state.refresh())


def _notify_all(message: str, kind: str) -> None:
    """A notice in every tab open on the app (a download finishes in the background, maybe after the page was left)."""
    from nicegui import Client
    for client in list(Client.instances.values()):
        if client.has_socket_connection:
            with client:
                ui.notify(message, type=kind)
