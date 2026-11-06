"""The starter set: frozen from a data folder of examples, copied into a new install on its first start."""
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from nicegui.testing import user_simulation
from PIL import Image

from rpapp.characters import CharacterStore, new_character
from rpapp.connections import OpenRouterConnection, connection_store
from rpapp.conversations import ConversationStore
from rpapp.lorebooks import lore_book_store, new_lore_book
from rpapp.personas import PersonaStore, new_persona
from rpapp.presets import new_preset, preset_store
from rpapp.profiles import LoreBookRef
from rpapp.settings import ConnectionRef, PresetRef, SettingsStore
from rpapp.starter import freeze, is_new, seed
from rpapp.ui.app import build_root

KEY = 'sk-or-v1-very-secret'


def png(color) -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (60, 90), color).save(out, 'PNG')
    return out.getvalue()


def files(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob('*')) if p.is_file()}


@pytest.fixture
def examples(tmp_path) -> Path:
    """A data folder of examples, as the app writes it, with what must stay out of the starter set."""
    data = tmp_path / 'examples-data'
    presets = preset_store(data)
    chosen, other = new_preset('Slow burn'), new_preset('Plain')
    chosen.instructions = 'You are {{char}}.'
    presets.save(chosen)
    presets.save(other)
    books = lore_book_store(data)
    book = new_lore_book('Harbour town')
    books.save(book)
    personas = PersonaStore(data)
    persona = new_persona('Ana, a sailor')
    personas.set_picture(persona, png('navy'))
    personas.save(persona)
    characters = CharacterStore(data)
    character = new_character('Captain Hale')
    character.lorebooks = [LoreBookRef(id=book.id, name=book.name)]
    characters.set_picture(character, png('teal'))
    characters.save(character)
    (data / 'characters' / 'images' / f'{"a" * 16}.webp').write_bytes(b'left over')  # used by nothing
    # What must stay out: a connection and its key, a chat, the settings beyond the prompt chosen
    conn = OpenRouterConnection(name='Mine', api_key=KEY)
    connection_store(data).save(conn)
    ConversationStore(data).start(character, persona)
    settings = SettingsStore(data)
    s = settings.load()
    s.chat.connection = ConnectionRef(id=conn.id, name=conn.name)
    s.chat.preset = PresetRef(id=chosen.id, name=chosen.name)
    s.appearance.accent = 'rose'
    settings.save(s)
    (data / 'devices.json').write_text('{"devices": []}', encoding='utf-8')
    return data


# --- freezing -----------------------------------------------------------------------------------

def test_freeze_takes_the_examples_and_nothing_else(examples, tmp_path):
    starter = tmp_path / 'starter'
    starter.mkdir()
    (starter / 'README.md').write_text('About the starter set.', encoding='utf-8')
    report = freeze(examples, starter)

    assert report.summary() == '2 prompts, 1 persona, 1 character, 1 lore book, 4 pictures'
    assert report.preset == 'Slow burn' and report.warnings == []
    names = set(files(starter))
    assert {n.split('/')[0] for n in names} == {'presets', 'personas', 'characters', 'lorebooks', 'settings.json',
                                                'README.md'}
    assert len([n for n in names if '/images/' in n]) == 4  # each picture, whole and framed
    assert f'characters/images/{"a" * 16}.webp' not in names  # (used by nothing)
    assert not any(n.startswith(('connections', 'conversations', 'devices')) for n in names)
    assert all(KEY.encode() not in content for content in files(starter).values())
    settings = json.loads((starter / 'settings.json').read_text(encoding='utf-8'))
    assert settings == {'chat': {'preset': {'id': report_preset_id(examples), 'name': 'Slow burn'}}}
    assert (starter / 'README.md').read_text(encoding='utf-8') == 'About the starter set.'  # untouched


def report_preset_id(data: Path) -> str:
    return SettingsStore(data).load().chat.preset.id


def test_freezing_again_replaces_it(examples, tmp_path):
    starter = tmp_path / 'starter'
    freeze(examples, starter)
    first = files(starter)
    freeze(examples, starter)
    assert files(starter) == first  # the same examples: the same bytes (nothing to commit)

    characters = CharacterStore(examples)
    [hale] = characters.list()
    characters.delete(hale.id)
    report = freeze(examples, starter)
    assert 'characters' not in report.items
    assert not (starter / 'characters').exists()  # its file and pictures are gone
    assert len(files(starter / 'personas')) == 3


def test_freeze_refuses_what_it_cant_take(examples, tmp_path):
    starter = tmp_path / 'starter'
    freeze(examples, starter)
    before = files(starter)
    (examples / 'personas' / 'broken.json').write_text('{"name": ', encoding='utf-8')
    with pytest.raises(ValueError, match='broken.json'):
        freeze(examples, starter)
    assert files(starter) == before  # nothing changed
    with pytest.raises(ValueError, match='isn\'t a data folder'):
        freeze(tmp_path / 'nothing-here', starter)
    with pytest.raises(ValueError, match='apart'):
        freeze(examples, examples / 'starter')


def test_freeze_warns_about_what_a_new_install_would_miss(examples, tmp_path):
    lore_book_store(examples).delete(lore_book_store(examples).list()[0].id)
    settings = SettingsStore(examples)
    s = settings.load()
    s.chat.preset = None
    settings.save(s)
    report = freeze(examples, tmp_path / 'starter')
    assert any('"Captain Hale" uses the lore book "Harbour town"' in w for w in report.warnings)
    assert any('No prompt is chosen' in w for w in report.warnings)
    assert not (tmp_path / 'starter' / 'settings.json').exists()


def test_the_command(examples, tmp_path, capsys):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
    from freeze_starter import main
    assert main([str(examples), '--to', str(tmp_path / 'starter')]) == 0
    out = capsys.readouterr().out
    assert 'Frozen into' in out and 'characters: Captain Hale' in out and 'prompt "Slow burn"' in out
    assert main([str(tmp_path / 'nothing-here'), '--to', str(tmp_path / 'starter')]) == 1


# --- the first start ----------------------------------------------------------------------------

@pytest.fixture
def starter(examples, tmp_path) -> Path:
    folder = tmp_path / 'starter'
    freeze(examples, folder)
    return folder


def test_a_new_install_starts_with_the_examples(starter, tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'app.lock').write_text('', encoding='utf-8')  # (the app's lock is there before anything else)
    assert is_new(data)
    before = files(starter)
    report = seed(data, starter)

    assert report.summary() == '2 prompts, 1 persona, 1 character, 1 lore book, 4 pictures'
    assert files(starter) == before  # only read
    [hale] = CharacterStore(data).list()
    assert (data / 'characters' / 'images' / hale.image).is_file()
    assert (data / 'characters' / 'images' / hale.image_full).is_file()
    assert datetime.now(timezone.utc) - hale.created_at < timedelta(minutes=1)  # added now, for "Recently added"
    assert [b.name for b in lore_book_store(data).list()] == ['Harbour town']
    settings = SettingsStore(data).load()
    assert settings.chat.preset.name == 'Slow burn' and settings.chat.connection is None
    assert settings.appearance.accent == 'jade'  # only the prompt chosen comes along
    assert not (data / 'connections').exists() or not any((data / 'connections').iterdir())

    # Never again: not even once every example is deleted
    assert not is_new(data) and seed(data, starter) is None
    for item in CharacterStore(data).list():
        CharacterStore(data).delete(item.id)
    assert seed(data, starter) is None and CharacterStore(data).list() == []


def test_an_existing_install_gets_nothing(starter, tmp_path):
    data = tmp_path / 'data'
    preset_store(data).save(new_preset('Mine'))  # something saved, even without a settings file
    assert seed(data, starter) is None
    assert [p.name for p in preset_store(data).list()] == ['Mine']
    assert seed(tmp_path / 'other', tmp_path / 'no-starter-set') is None  # (and without a starter set: nothing)


async def test_the_app_after_its_first_start(starter, tmp_path):
    data = tmp_path / 'data'
    seed(data, starter)
    async with user_simulation(build_root(data)) as user:
        await user.open('/prompts')
        await user.should_see('Slow burn')
        await user.should_not_see('Default')  # the app's own starter prompt isn't added on top
        await user.open('/characters')
        await user.should_see('Captain Hale')
        await user.open('/')
        await user.should_see(marker='tip-create-connection')  # a connection is still to be added


@pytest.mark.parametrize('kept', [[], ['characters']])
def test_without_example_prompts_a_new_install_still_gets_the_default_one(examples, tmp_path, kept):
    """An empty starter set changes nothing; one without prompts leaves the app to make its own."""
    from rpapp.presets import set_up_presets
    folder = tmp_path / 'starter'
    freeze(examples, folder)
    for path in folder.iterdir():
        if path.is_dir() and path.name not in kept:
            import shutil
            shutil.rmtree(path)
    (folder / 'settings.json').unlink()
    data = tmp_path / 'data'
    report = seed(data, folder)
    assert (report is None) == (not kept)
    set_up_presets(data, SettingsStore(data))
    assert [p.name for p in preset_store(data).list()] == ['Default']
    assert SettingsStore(data).load().chat.preset.name == 'Default'
