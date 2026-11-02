"""Prompts: the instructions sent with every reply, saved under a name, so you can keep several and switch.

    data/presets/<id>.json
    {
      "id": "slow-burn-romance-3f9a2c",
      "name": "Slow-burn romance",
      "instructions": "You are {{char}} ...",            <- at the start of the prompt
      "final_instructions": "Write {{char}}'s next reply ...", <- at the very end, after the chat
      "extend_instruction": "",                        <- hidden, to extend the last reply ('' = the default)
      "continue_instruction": ""                       <- hidden, to continue the scene ('' = the default)
    }

Settings › Chat says which one chats use (settings.chat.preset). In the code they're
"presets": "Prompt" is already the prompt as built for one reply (see prompts.py), from
the preset chosen, the character, your persona, lore, memories and the chat.

set_up_presets() runs at startup: the first time, it moves the instructions that used
to live in Settings into a prompt of their own, or makes a starter one.
"""
from __future__ import annotations

import logging
from pathlib import Path

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


class PromptPreset(StoredModel):
    name: str = 'New prompt'
    instructions: str = ''        # at the start of the prompt: the tone and the rules of the chat
    final_instructions: str = ''  # at the very end, right after the chat: what to write now
    extend_instruction: str = ''  # hidden, to extend the last reply ('' = EXTEND_INSTRUCTION)
    continue_instruction: str = ''  # hidden, to continue the scene ('' = CONTINUE_INSTRUCTION)

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
