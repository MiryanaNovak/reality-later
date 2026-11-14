"""The starter set: example prompts, personas, characters and lore books, for a new install.

    rpapp/starter/
        presets/<id>.json        the files exactly as in a data folder (see storage.py)
        personas/<id>.json
        personas/images/<picture>.webp    only the pictures the personas use
        characters/<id>.json
        characters/images/<picture>.webp
        lorebooks/<id>.json
        settings.json            only which prompt chats use: {"chat": {"preset": {"id": ..., "name": ...}}}

It goes into the data folder once, on the very first start (seed(), called by main.py): when the folder
has no settings file and nothing saved yet. Then the examples are ordinary data, to change or delete;
they never come back, since by then there's a settings file. The starter folder is only read, never
written: in a packaged app it may be read-only.

It's made from a data folder kept for the purpose (freeze(), or tools/freeze_starter.py):

    RPAPP_DATA=examples-data python main.py     write the examples with the app itself
    python tools/freeze_starter.py examples-data

Only those four kinds of files go in, and the pictures they use. Never the connections (they hold API
keys), the settings (only the prompt chosen), the conversations, memories or paired devices.
"""
from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .characters import Character, CharacterStore
from .lorebooks import LoreBook, lore_book_store
from .personas import Persona, PersonaStore
from .presets import PromptPreset, preset_store
from .settings import PresetRef, SettingsStore
from .storage import JsonStore

STARTER_DIR = Path(__file__).parent / 'starter'

# folder -> what's in it, and whether its items have pictures (in <folder>/images/)
KINDS: dict[str, tuple[type[BaseModel], bool]] = {
    'presets': (PromptPreset, False),
    'personas': (Persona, True),
    'characters': (Character, True),
    'lorebooks': (LoreBook, False),
}
STORES: dict[str, Callable[[Path], JsonStore]] = {
    'presets': preset_store, 'personas': PersonaStore, 'characters': CharacterStore, 'lorebooks': lore_book_store,
}
NOUNS = {'presets': 'prompt', 'personas': 'persona', 'characters': 'character', 'lorebooks': 'lore book'}
# What counts as "something saved already" in a data folder
DATA_FOLDERS = (*KINDS, 'connections', 'conversations')
SETTINGS = 'settings.json'


@dataclass
class Report:
    """What was copied: {folder: names}, the pictures, and anything worth a warning."""
    items: dict[str, list[str]] = field(default_factory=dict)
    pictures: int = 0
    warnings: list[str] = field(default_factory=list)
    preset: str = ''  # the name of the prompt chosen for chats

    def summary(self) -> str:
        """'2 prompts, 1 persona, 3 pictures'."""
        counts = [(len(names), NOUNS[folder]) for folder, names in self.items.items() if names]
        if self.pictures:
            counts.append((self.pictures, 'picture'))
        return ', '.join(f'{n} {noun}{"s" if n > 1 else ""}' for n, noun in counts) or 'nothing'


# --- the first start ---------------------------------------------------------------------------

def is_new(data_dir: str | Path) -> bool:
    """True for a data folder nothing was saved in yet (it may exist: the app's lock file is in it)."""
    data_dir = Path(data_dir)
    if (data_dir / SETTINGS).exists():
        return False
    return not any((data_dir / folder).is_dir() and any((data_dir / folder).glob('*.json'))
                   for folder in DATA_FOLDERS)


def seed(data_dir: str | Path, starter: str | Path = STARTER_DIR) -> Report | None:
    """On the first start, copy the starter set into the data folder. None if it isn't the first start,
    or there's nothing to copy (the first start then goes on as without a starter set). Their "added"
    dates become now, and the prompt the starter set chooses is chosen in Settings. The settings file is
    written either way, so it never happens again."""
    data_dir, starter = Path(data_dir), Path(starter)
    if not is_new(data_dir) or not starter.is_dir():
        return None
    found = {folder: _read(starter / folder, model) for folder, (model, _) in KINDS.items()}
    if not any(found.values()):  # an empty starter set: the first start goes on as without one
        return None
    report = Report()
    now = datetime.now(timezone.utc)
    for folder, (model, has_pictures) in KINDS.items():
        if not found[folder]:  # (no folder made for nothing: presets.set_up_presets checks the prompts' one)
            continue
        store = STORES[folder](data_dir)
        for item in found[folder]:
            if 'created_at' in type(item).model_fields:
                item.created_at = now
            store.save(item)
            report.items.setdefault(folder, []).append(item.label)
        if has_pictures and (pictures := starter / folder / 'images').is_dir():
            target = data_dir / folder / 'images'
            target.mkdir(parents=True, exist_ok=True)
            for picture in sorted(pictures.iterdir()):
                if picture.is_file() and not (target / picture.name).exists():
                    shutil.copyfile(picture, target / picture.name)
                    report.pictures += 1

    settings_store = SettingsStore(data_dir)
    settings = settings_store.load()
    chosen = _chosen_preset(starter)
    if chosen and chosen.id in {p.id for p in found['presets']}:
        settings.chat.preset = chosen
        report.preset = chosen.name
    settings_store.save(settings)  # (also what says the first start is over)
    return report


# --- making the starter set --------------------------------------------------------------------

def freeze(source: str | Path, starter: str | Path = STARTER_DIR) -> Report:
    """Make the starter set from the data folder `source`: its prompts, personas, characters and lore
    books, the pictures they use, and which prompt chats use. What the starter folder had is replaced.
    Raises ValueError if `source` isn't a data folder, or one of its files can't be read."""
    source, starter = Path(source).resolve(), Path(starter).resolve()
    if not source.is_dir() or not any((source / folder).is_dir() for folder in KINDS):
        raise ValueError(f'{source} isn\'t a data folder: it has none of {", ".join(KINDS)}.')
    if source == starter or starter in source.parents or source in starter.parents:
        raise ValueError('The data folder and the starter folder must be apart.')

    found = {folder: _read(source / folder, model) for folder, (model, _) in KINDS.items()}  # all, before any change
    report = Report()
    presets = {preset.id for preset in found['presets']}
    for character in found['characters']:  # (their own connection never goes in: a new install has none)
        if character.connection is not None:
            report.warnings.append(f'"{character.label}" has a connection of its own: left out (a new install has '
                                   'no connections), its chats use Settings\'.')
            character.connection = None
        chosen = character.prompt.preset if character.prompt is not None else None
        if chosen is not None and chosen.id not in presets:
            report.warnings.append(f'"{character.label}" uses the prompt "{chosen.name}", which isn\'t there: left '
                                   'out, its chats use Settings\'.')
            character.prompt = None
    for folder, (model, has_pictures) in KINDS.items():
        target = starter / folder
        _empty(target)
        items = found[folder]
        if not items:
            continue
        target.mkdir(parents=True, exist_ok=True)
        for item in sorted(items, key=lambda i: i.id):
            _write(target / f'{item.id}.json', item)
            report.items.setdefault(folder, []).append(item.label)
        if has_pictures:
            for name in sorted({name for item in items for name in (item.image, item.image_full) if name}):
                picture = source / folder / 'images' / name
                if picture.is_file():
                    (target / 'images').mkdir(exist_ok=True)
                    shutil.copyfile(picture, target / 'images' / name)
                    report.pictures += 1
                else:
                    report.warnings.append(f'The picture {name} is missing from {source / folder / "images"}.')

    books = {book.id for book in found['lorebooks']}
    for folder in ('personas', 'characters'):
        for item in found[folder]:
            for ref in item.lorebooks:
                if ref.id not in books:
                    report.warnings.append(f'"{item.label}" uses the lore book "{ref.name}", which isn\'t there.')

    (starter / SETTINGS).unlink(missing_ok=True)
    settings_file = source / SETTINGS
    preset = None
    if settings_file.exists():
        try:
            preset = SettingsStore(source).load().chat.preset
        except ValueError as e:
            report.warnings.append(f'{settings_file} can\'t be read ({e}): no prompt is chosen.')
    if preset and preset.id in {p.id for p in found['presets']}:
        starter.mkdir(parents=True, exist_ok=True)
        (starter / SETTINGS).write_text(json.dumps({'chat': {'preset': preset.model_dump()}}, indent=2) + '\n',
                                          encoding='utf-8')
        report.preset = preset.name
    elif found['presets']:
        report.warnings.append('No prompt is chosen in its Settings › Chat: a new install starts with none chosen.')
    return report


# --- helpers ------------------------------------------------------------------------------------

def _read(folder: Path, model: type[BaseModel]) -> list[Any]:
    """Every item in the folder, or ValueError naming the file that's wrong (nothing is skipped quietly)."""
    if not folder.is_dir():
        return []
    items = []
    for path in sorted(folder.glob('*.json')):
        try:
            item = model.model_validate(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError, ValidationError) as e:
            raise ValueError(f'{path} can\'t be read: {e}') from e
        if item.id != path.stem:
            raise ValueError(f'{path}: its id is "{item.id}", not its file name')
        items.append(item)
    return items


def _write(path: Path, item: BaseModel) -> None:
    """As the data folder has it (see JsonStore.save), so the files look the same in both."""
    data = item.model_dump(mode='json')
    data = {k: data.pop(k) for k in ('kind', 'id', 'title', 'name') if k in data} | data
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def _empty(folder: Path) -> None:
    """Remove what an earlier freeze left: the JSON files and pictures (nothing else is touched)."""
    for path in [*folder.glob('*.json'), *folder.glob('images/*')]:
        if path.is_file():
            path.unlink()
    for path in (folder / 'images', folder):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def _chosen_preset(starter: Path) -> PresetRef | None:
    try:
        data = json.loads((starter / SETTINGS).read_text(encoding='utf-8'))
        return PresetRef.model_validate(data['chat']['preset'])
    except (OSError, ValueError, KeyError, TypeError, ValidationError):
        return None

