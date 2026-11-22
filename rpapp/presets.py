"""Prompts: the instructions sent with every reply, saved under a name, so you can keep several and switch.

    data/presets/<id>.json
    {
      "id": "slow-burn-romance-3f9a2c",
      "name": "Slow-burn romance",
      "description": "For long chats ...",              <- only for you: what it's for (shown in the list)
      "instructions": "You are {{char}} ...",            <- at the start of the prompt
      "final_instructions": "Write {{char}}'s next reply ...", <- at the very end, after the chat
      "extend_instruction": "",                        <- hidden, to extend the last reply ('' = the default)
      "continue_instruction": "",                      <- hidden, to continue the scene ('' = the default)
      "modules": [                                     <- instructions to add, that each chat turns on or off
        {"id": "a1b2c3d4", "name": "Combat", "on_by_default": true,
         "instructions": "...", "final_instructions": "...", "memory_instructions": "..."}
      ]
    }

Modules: a module that's on for a chat (Conversation.modules says, for those the chat turned on or off; the others
are as `on_by_default` says) adds its instructions after the prompt's instructions, its final instructions after
the final ones (prompts.py), and its memory instructions after the memory instructions, when memories are written
(memory.py). Those empty, or only spaces, are left out.

Settings › Chat says which one chats use (settings.chat.preset). In the code they're
"presets": "Prompt" is already the prompt as built for one reply (see prompts.py), from
the preset chosen, the character, your persona, lore, memories and the chat.

set_up_presets() runs at startup: the first time, it moves the instructions that used
to live in Settings into a prompt of their own, or makes a starter one.
"""
from __future__ import annotations

import logging
import secrets
from pathlib import Path

from pydantic import BaseModel, Field

from .settings import PresetRef, SettingsStore
from .storage import JsonStore, StoredModel

log = logging.getLogger(__name__)

FOLDER = 'presets'
STARTER_INSTRUCTIONS = (
    'You are {{char}} in a never-ending roleplay with {{user}}. Stay in character, write vivid, '
    'detailed replies, and never speak or act for {{user}}.'
)
STARTER_FINAL = (
    'Write only {{char}}\'s next reply, in 2 to 4 paragraphs. Never write {{user}}\'s words or actions.'
)
# Sent, hidden, after the reply to extend, by connections that extend replies with an instruction
EXTEND_INSTRUCTION = (
    '[Continue your last message from exactly where it stopped. Don\'t repeat any of it: write only what '
    'comes next.]'
)
# Sent, hidden, in your place, to have the AI write its next message without one from you
CONTINUE_INSTRUCTION = (
    '[{{user}} stays silent and lets things unfold. Continue the scene: write {{char}}\'s next message and move '
    'the story forward, without speaking or acting for {{user}}.]'
)


def new_module_id() -> str:
    return secrets.token_hex(4)


class PromptModule(BaseModel):
    """Instructions to add to a prompt's, under a name, that each chat turns on or off (see the notes at the top)."""
    id: str = Field(default_factory=new_module_id)  # stable, whatever its name becomes (chats choose by it)
    name: str = 'New module'
    on_by_default: bool = True    # in chats that haven't turned it on or off
    instructions: str = ''        # after the prompt's instructions
    final_instructions: str = ''  # after its final instructions
    memory_instructions: str = ''  # after the memory instructions, when memories are written

    def is_on(self, chosen: dict[str, bool]) -> bool:
        """Whether it's on in a chat (`chosen`: the modules it turned on or off, by id)."""
        return chosen.get(self.id, self.on_by_default)


class PromptPreset(StoredModel):
    name: str = 'New prompt'
    description: str = ''         # only for you: what it's for (shown in the list)
    instructions: str = ''        # at the start of the prompt: the tone and the rules of the chat
    final_instructions: str = ''  # at the very end, right after the chat: what to write now
    extend_instruction: str = ''  # hidden, to extend the last reply ('' = EXTEND_INSTRUCTION)
    continue_instruction: str = ''  # hidden, to continue the scene ('' = CONTINUE_INSTRUCTION)
    modules: list[PromptModule] = Field(default_factory=list)

    def add_module(self, name: str = 'New module') -> PromptModule:
        module = PromptModule(name=name)
        self.modules = [*self.modules, module]
        return module

    def remove_module(self, module_id: str) -> None:
        self.modules = [m for m in self.modules if m.id != module_id]

    def modules_on(self, chosen: dict[str, bool]) -> list[PromptModule]:
        """The modules on in a chat (`chosen`: those it turned on or off, by id; the others as by default)."""
        return [m for m in self.modules if m.is_on(chosen)]

    @property
    def extend_text(self) -> str:
        return self.extend_instruction if self.extend_instruction.strip() else EXTEND_INSTRUCTION

    @property
    def continue_text(self) -> str:
        return self.continue_instruction if self.continue_instruction.strip() else CONTINUE_INSTRUCTION


def new_preset(name: str) -> PromptPreset:
    return PromptPreset(name=name or 'New prompt')


def preset_store(data_dir: str | Path) -> JsonStore[PromptPreset]:
    return JsonStore(Path(data_dir) / FOLDER, PromptPreset.model_validate)


def set_up_presets(data_dir: str | Path, settings_store: SettingsStore) -> None:
    """The first time: the instructions from Settings become the prompt "My prompt", else a starter one is made.

    Either way it's the one chats use. Later starts change nothing (even with every prompt deleted).
    """
    first_time = not (Path(data_dir) / FOLDER).exists()
    store = preset_store(data_dir)
    try:
        settings = settings_store.load()
    except ValueError:  # a damaged settings file: leave it, the Settings page says so
        return
    chat = settings.chat
    if (chat.instructions.strip() or chat.final_instructions.strip()) and chat.preset is None:
        preset = new_preset('My prompt')
        preset.instructions, preset.final_instructions = chat.instructions, chat.final_instructions
    elif first_time and not store.list():
        preset = new_preset('Default')
        preset.instructions, preset.final_instructions = STARTER_INSTRUCTIONS, STARTER_FINAL
    else:
        return
    store.save(preset)
    if chat.preset is None:
        chat.preset = PresetRef(id=preset.id, name=preset.name)
    chat.instructions = chat.final_instructions = ''  # (no longer written to settings.json)
    settings_store.save(settings)
    log.info('Made the prompt "%s"', preset.name)


def chat_preset(ref: PresetRef | None, store: JsonStore[PromptPreset]) -> PromptPreset | None:
    """The prompt chats use, or None if none is chosen or it was deleted."""
    if ref is None:
        return None
    try:
        return store.get(ref.id)
    except ValueError:  # a damaged file: as if missing
        return None
