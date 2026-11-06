"""Prompts (presets): the instructions saved under a name, the one chats use, and the move from Settings."""
import json

from nicegui import ui
from nicegui.testing import user_simulation

from rpapp.characters import new_character
from rpapp.conversations import Conversation, ProfileRef
from rpapp.personas import new_persona
from rpapp.presets import STARTER_INSTRUCTIONS, new_preset, preset_store, set_up_presets
from rpapp.prompts import build_prompt
from rpapp.settings import PresetRef, Settings, SettingsStore
from rpapp.ui.app import build_root


def by_label(user, kind):
    return {e.props.get('label'): e for e in user.find(kind).elements}


def test_the_instructions_from_settings_become_a_prompt(tmp_path):
    (tmp_path / 'settings.json').write_text(json.dumps({  # as saved before prompts had their page
        'id': 'settings', 'chat': {'instructions': 'You are {{char}}.', 'final_instructions': 'Be brief.'}}), encoding='utf-8')
    store = SettingsStore(tmp_path)
    set_up_presets(tmp_path, store)
    [mine] = preset_store(tmp_path).list()
    assert (mine.name, mine.instructions, mine.final_instructions) == ('My prompt', 'You are {{char}}.', 'Be brief.')
    assert store.load().chat.preset == PresetRef(id=mine.id, name='My prompt')  # and chats use it
    saved = json.loads((tmp_path / 'settings.json').read_text(encoding='utf-8'))
    assert 'instructions' not in saved['chat'] and 'final_instructions' not in saved['chat']

    set_up_presets(tmp_path, store)  # the next start: nothing more
    assert len(preset_store(tmp_path).list()) == 1


def test_the_very_first_version_of_the_file(tmp_path):
    (tmp_path / 'settings.json').write_text(json.dumps({'id': 'settings', 'chat': {'prompt': 'Old name.'}}), encoding='utf-8')
    set_up_presets(tmp_path, SettingsStore(tmp_path))
    assert [p.instructions for p in preset_store(tmp_path).list()] == ['Old name.']


def test_a_starter_prompt_the_first_time_only(tmp_path):
    store = SettingsStore(tmp_path)
    set_up_presets(tmp_path, store)
    [starter] = preset_store(tmp_path).list()
    assert starter.name == 'Default' and starter.instructions == STARTER_INSTRUCTIONS
    assert store.load().chat.preset.id == starter.id

    preset_store(tmp_path).delete(starter.id)  # you deleted them all: they don't come back
    set_up_presets(tmp_path, store)
    assert preset_store(tmp_path).list() == []


def test_a_damaged_settings_file_is_left_alone(tmp_path):
    (tmp_path / 'settings.json').write_text('{ not json', encoding='utf-8')
    set_up_presets(tmp_path, SettingsStore(tmp_path))
    assert (tmp_path / 'settings.json').read_text(encoding='utf-8') == '{ not json'


def test_the_prompt_says_which_one_it_used():
    character, persona = new_character('Seraphina'), new_persona('Mira')
    c = Conversation(title='t', character=ProfileRef.to(character), persona=ProfileRef.to(persona))
    c.add('user', 'Hi.')
    preset = new_preset('Terse')
    preset.instructions, preset.final_instructions = 'You are {{char}}.', 'One line.'
    settings = Settings()
    settings.chat.preset = PresetRef(id=preset.id, name='Terse')
    prompt = build_prompt(c, character=character, persona=persona, settings=settings, preset=preset)
    sources = [p.source for p in prompt.parts if p.kind in ('instructions', 'final')]
    assert sources == ['Instructions (Terse)', 'Final instructions (Terse)'] and prompt.report.preset == 'Terse'
    assert prompt.parts[0].content == 'You are Seraphina.'

    gone = build_prompt(c, character=character, persona=persona, settings=settings, preset=None)  # deleted since
    assert gone.report.missing_preset == 'Terse' and not any(p.kind == 'instructions' for p in gone.parts)


async def test_the_prompts_page(tmp_path):
    settings = SettingsStore(tmp_path)
    async with user_simulation(build_root(tmp_path)) as user:  # (makes the starter prompt "Default")
        store = preset_store(tmp_path)
        [default] = store.list()
        await user.open('/')
        user.find(marker='nav-prompts').click()
        await user.should_see('Default')
        await user.should_see('Used for chats')  # the one chats use

        user.find('New').click()
        with user.scope(marker='new-dialog'):
            by_label(user, ui.input)['Name'].set_value('Slow burn')
            user.find(kind=ui.button, content='Create').click()
        await user.should_see('Prompt. Changes save automatically.')
        [mine] = [p for p in store.list() if p.name == 'Slow burn']
        by_label(user, ui.textarea)['Instructions'].set_value('Take it slow, {{char}}.')
        by_label(user, ui.textarea)['Final instructions'].set_value('One paragraph.')
        await user.should_see('Chats use another prompt.')
        user.find(marker='use-preset').click()
        await user.should_see(marker='preset-in-use')
        saved = store.load(mine.id)
        assert (saved.instructions, saved.final_instructions) == ('Take it slow, {{char}}.', 'One paragraph.')
        assert settings.load().chat.preset == PresetRef(id=mine.id, name='Slow burn')

        by_label(user, ui.input)['Name'].set_value('Slower burn')  # renamed: Settings follows
        await user.open('/settings')
        await user.should_see('Slower burn')
        assert settings.load().chat.preset.name == 'Slower burn'

        await user.open('/prompts')
        with user.scope(marker=f'row-{mine.id}'):
            user.find(marker='menu-delete').click()
        await user.should_see('Chats use this prompt: until you choose another')  # the warning
        with user.scope(marker='confirm-dialog'):
            user.find(kind=ui.button, content='Delete').click()
        await user.should_not_see(marker=f'row-{mine.id}')

        await user.open('/settings')
        await user.should_see(marker='preset-missing')  # chosen, but gone
        assert by_label(user, ui.select)['Prompt'].value == mine.id
