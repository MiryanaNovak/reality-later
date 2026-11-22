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


def the(user, marker):
    """The element with this marker, shown or not."""
    [element] = [e for e in user.client.layout.descendants() if marker in e._markers]
    return element


def test_older_files_and_copies(tmp_path):
    store = preset_store(tmp_path)
    (tmp_path / 'presets').mkdir(exist_ok=True)
    (tmp_path / 'presets' / 'old-1a2b3c.json').write_text(json.dumps(  # (saved before descriptions and modules)
        {'id': 'old-1a2b3c', 'name': 'Old', 'instructions': 'Be kind.'}), encoding='utf-8')
    old = store.load('old-1a2b3c')
    assert (old.description, old.modules) == ('', [])

    old.description = 'For gentle stories.'
    combat = old.add_module('Combat')
    combat.instructions = 'Fights are quick.'
    store.save(old)
    copy = store.duplicate(store.load(old.id))
    assert copy.description == 'For gentle stories.' and [(m.id, m.name, m.instructions) for m in copy.modules] == \
        [(combat.id, 'Combat', 'Fights are quick.')]
    copy.modules[0].name = 'Duels'
    assert store.load(old.id).modules[0].name == 'Combat'  # (the copy's are its own)
    old.remove_module(combat.id)
    assert old.modules == []


async def test_description_sections_and_modules(tmp_path):
    store = preset_store(tmp_path)
    preset = new_preset('Slow burn')
    preset.instructions = 'Take it slow, {{char}}.\nVery slow.'
    store.save(preset)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open('/prompts')
        with user.scope(marker=f'row-{preset.id}'):
            await user.should_see('No description yet')
            await user.should_not_see('Take it slow')  # (no longer the start of its instructions)

        await user.open(f'/prompts/{preset.id}')
        await user.should_see('Identity')
        assert not any('part-identity' in e._markers for e in user.client.layout.descendants())  # (it doesn't fold)
        assert the(user, 'part-instructions').value is True  # (open, until folded on this device)
        assert the(user, 'part-hidden').value is False and the(user, 'part-modules').value is False
        the(user, 'preset-description').set_value('For long, patient chats.')
        store.flush()
        assert store.load(preset.id).description == 'For long, patient chats.'

        the(user, 'part-modules').set_value(True)
        await user.should_see(marker='no-modules')
        user.find(marker='module-add').click()
        await user.should_see(marker='module-name')
        [module] = store.load(preset.id).modules
        assert module.name == 'New module'
        the(user, 'module-name').set_value('Combat')
        the(user, 'module-instructions').set_value('Keep fights short.')
        the(user, 'module-final-instructions').set_value('End on a blow.')
        user.find(marker='module-add').click()
        await user.should_see(marker=f'module-{store.load(preset.id).modules[1].id}')
        store.flush()
        first, second = store.load(preset.id).modules
        assert (first.name, first.instructions, first.final_instructions) == \
            ('Combat', 'Keep fights short.', 'End on a blow.')
        prompt = build_prompt(Conversation(title='t', character=ProfileRef.to(new_character('S')),
                                           persona=ProfileRef.to(new_persona('M'))),
                              character=new_character('S'), persona=new_persona('M'), settings=Settings(),
                              preset=store.load(preset.id))
        assert 'Keep fights short.' in ' '.join(p.content for p in prompt.parts)  # (on by default: in the prompt)

        with user.scope(marker=f'module-{second.id}'):  # (empty: deleted at once)
            user.find(marker='module-delete').click()
        await user.should_not_see(marker=f'module-{second.id}')
        with user.scope(marker=f'module-{first.id}'):  # (with instructions: asked first)
            user.find(marker='module-delete').click()
        await user.should_see('Delete the module "Combat"?')
        with user.scope(marker='confirm-dialog'):
            user.find(kind=ui.button, content='Delete').click()
        await user.should_see(marker='no-modules')
        store.flush()
        assert store.load(preset.id).modules == []

        await user.open('/prompts')
        with user.scope(marker=f'row-{preset.id}'):
            await user.should_see('For long, patient chats.')
