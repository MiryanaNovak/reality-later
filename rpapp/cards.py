"""Character cards, as shared by SillyTavern, Chub, RisuAI and others: reading V1, V2 and V3 cards.

The files:

    .png     the character's picture, with the card hidden in a text chunk as base64 JSON:
             "ccv3" for V3, else "chara" (V1 or V2). With both, ccv3 wins.
    .json    the card itself
    .charx   (V3) a zip: card.json, and files it refers to as embeded://<path>, like the picture

The cards:

    V1  flat: {"name", "description", "personality", "scenario", "first_mes", "mes_example"}
        (and the old Pygmalion spellings: char_name, char_persona, world_scenario, char_greeting,
        example_dialogue)
    V2  {"spec": "chara_card_v2", "data": {the V1 fields, creator_notes, alternate_greetings,
        character_book, ...}}
    V3  {"spec": "chara_card_v3", "data": {the V2 fields, nickname, creator_notes_multilingual,
        assets, ...}}

What goes where in this app:

    title               the card's name
    name                its nickname (V3: what {{char}} stands for), else its name
    description         creator_notes, the notes for people (V3: in your browser's language, when
                        creator_notes_multilingual has it), Markdown and HTML as they are, with their
                        pictures (data: URIs) stored as the character's (descriptions.py)
    summary             extensions.reality_later.summary, this app's own (see Exporting); else the first
                        sentence of the creator notes, as plain text
    tags                tags, normalized as shared cards' are (lowercase, 20 at most)
    scenario            description, personality and scenario, as SillyTavern puts them in a prompt:
                        the description, then "{{char}}'s personality: ...", then "Scenario: ..."
    first_messages      first_mes, then alternate_greetings
    example_dialogues   mes_example, split at each <START>
    picture             the PNG itself; in a CharX or a V3 JSON, the "main" icon
    lore books          character_book, converted to a lore book (see convert_book)

Everything else (system_prompt, post_history_instructions, creator, extensions, other
pictures, group greetings...) has no place in this app, and is ignored. So are pictures the
card's texts load from other websites (see without_outside_pictures), and anything that
unpacks to more than MAX_CARD_BYTES (IMPORT_MAX_BYTES for a card you import: Characters › Import).

A card imported (Characters › Import) may be as big as IMPORT_MAX_BYTES: cards from other apps can carry a
lot (pictures written into their notes, which this app keeps; or other assets, which it doesn't). Its
picture is stored without the card's data (the PNG's text chunks: Pillow refuses pictures with more than
64 MB of them), and reading it and storing its pictures (store_pictures) can run in a worker thread.

Exporting writes a V2 card in a PNG (card_png): V2 holds every field this app has, and more
apps read it than V3, whose additions (nickname, notes in several languages, more pictures,
group greetings) this app doesn't have. The other way round:

    name                the character's name (the title is only for you: it isn't exported)
    description         the scenario, all the AI is told about them
    creator_notes       the description, for people, its pictures written into it (data: URIs);
                        without one, the summary (other apps show creator notes)
    tags                the tags
    extensions.reality_later   {"summary": the summary, "card_uuid": its UUID as a shared card, if it has one}
    first_mes, alternate_greetings, mes_example (<START> before each dialogue)
    character_book      the character's lore books (several are merged into one: a card holds one)
    the picture         the character's picture as a PNG, or their initials when they have none

The V1 fields are also written at the top level, for apps that only read V1.
"""
from __future__ import annotations

import base64
import binascii
import html
import io
import json
import re
import struct
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .characters import Character, CharacterStore, new_character
from .descriptions import MAX_SUMMARY_BYTES, clip, first_sentence, inlined_pictures, with_stored_pictures
from .lorebooks import AT_DEPTH, LoreBook, LoreEntry, new_lore_book
from .publishing.announcements import normalize_tags
from .storage import JsonStore

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'

# The most a card may unpack to. Compressed data can be made to unpack to gigabytes (a "zip
# bomb"): it's read up to these sizes only, and refused beyond.
SHARED_EXTENSION = 'reality_later'  # data.extensions.reality_later: {"card_uuid": its UUID as a shared card,
                                    # "summary": its summary}
SHARED_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')  # (any version)
# What a card file may unpack to: as much as a shared card's file may be (publishing/announcements.py's
# MAX_FILE_BYTES, 100 MiB), so that any card within the format is one this app reads.
MAX_CARD_BYTES = 100 * 1024 * 1024     # a card's JSON (the biggest real ones, with large lore books, are a few MB)
MAX_PICTURE_BYTES = 100 * 1024 * 1024  # a picture inside a CharX file
IMPORT_MAX_BYTES = 1024 ** 3           # a card file you import (Characters › Import), and what it may unpack to
TEXT_CHUNKS = (b'tEXt', b'zTXt', b'iTXt')  # a PNG's: where a card keeps its data
TOO_BIG = 'This card is too big to be a real one: it unpacks to more than {}.'
# Shared cards (publishing/): what a card file from someone else may be, for this computer to add it without
# harm, and so what one published from here must be, for everyone to add it (check_shared_card). Its file's
# size: the format's limit (publishing/announcements.py). Its picture's: a picture is decoded whole to be
# stored (images.py), and a small file can hold a huge one (a plain color compresses to almost nothing): 50
# megapixels take about 200 MB of memory once decoded. Real ones have far fewer: this app's own, 1 at most.
MAX_PICTURE_PIXELS = 50_000_000

# A lore entry's SillyTavern settings, as cards keep them in the entry's "extensions": (ours, theirs)
ENTRY_EXTENSIONS = (
    ('depth', 'depth'), ('probability', 'probability'), ('useProbability', 'useProbability'),
    ('selectiveLogic', 'selectiveLogic'), ('group', 'group'), ('groupOverride', 'group_override'),
    ('groupWeight', 'group_weight'), ('scanDepth', 'scan_depth'), ('matchWholeWords', 'match_whole_words'),
    ('useGroupScoring', 'use_group_scoring'), ('automationId', 'automation_id'),
    ('excludeRecursion', 'exclude_recursion'), ('preventRecursion', 'prevent_recursion'),
    ('delayUntilRecursion', 'delay_until_recursion'), ('vectorized', 'vectorized'),
    ('ignoreBudget', 'ignore_budget'), ('sticky', 'sticky'), ('cooldown', 'cooldown'), ('delay', 'delay'),
    ('triggers', 'triggers'), ('outletName', 'outlet_name'),
    ('matchPersonaDescription', 'match_persona_description'),
    ('matchCharacterDescription', 'match_character_description'),
    ('matchCharacterPersonality', 'match_character_personality'),
    ('matchCharacterDepthPrompt', 'match_character_depth_prompt'),
    ('matchScenario', 'match_scenario'), ('matchCreatorNotes', 'match_creator_notes'),
)
PYGMALION = {'char_name': 'name', 'char_persona': 'description', 'world_scenario': 'scenario',
             'char_greeting': 'first_mes', 'example_dialogue': 'mes_example'}


class CardError(ValueError):
    """The file can't be read as a character card: the message says why, in plain words."""


class SafeguardError(CardError):
    """A card beyond the safeguards for shared cards (check_shared_card). `what`: 'file' (its size) or
    'picture' (its pixels). Its message goes after "It can't be added: " or "It can't be published: "."""

    def __init__(self, message: str, *, what: str) -> None:
        super().__init__(message)
        self.what = what


@dataclass
class Card:
    """A card read from a file, as this app's objects (nothing is saved yet)."""
    character: Character
    version: str                       # 'V1', 'V2' or 'V3'
    picture: bytes | None = None       # image file contents
    book: LoreBook | None = None       # the card's own lore book, converted
    pictures_stored: bool = False      # its pictures already stored as files (store_pictures)


def read_card(file_name: str, content: bytes, *, languages: list[str] = (), limit: int = MAX_CARD_BYTES) -> Card:
    """Read a card from a .png, .json or .charx file. `languages`: the user's, best first ('fr', 'en'); `limit`:
    what its data and its picture may unpack to (CardError beyond)."""
    picture: bytes | None = None
    if content.startswith(PNG_SIGNATURE):
        data, picture = _from_png(content, limit), without_text(content)
    elif content.startswith(b'PK\x03\x04'):
        data, picture = _from_charx(content, limit)
    else:
        data = _json(content, 'This file isn\'t a character card: it\'s not a PNG picture, a JSON file or a '
                              'CharX file.')
    version, fields = _unwrap(data)
    if picture is None:
        picture = _embedded_picture(fields)
    return _to_app(fields, version, picture, Path(file_name).stem, languages)


def check_shared_card(file_name: str, content: bytes, *, size_limit: int) -> Card:
    """A shared card's file as it must be, to be added here (from someone else) or published (for everyone
    to add it): `size_limit` bytes at most, a card this app reads (CardError), and a picture of
    MAX_PICTURE_PIXELS at most, as its header says: nothing is decoded (SafeguardError). A picture this app
    can't read isn't one: the card comes without it, as with Import. The card, read."""
    if len(content) > size_limit:
        raise SafeguardError(f'its file is {megabytes(len(content))}, and a shared card\'s is '
                             f'{megabytes(size_limit)} at most', what='file')
    card = read_card(file_name, content)
    if card.picture:
        _check_picture(card.picture)
    for picture in inlined_pictures(card.character.description):  # (the description's, stored on adding it)
        _check_picture(picture, which='one of the pictures in its description',
                       theirs='a shared card\'s pictures are')
    return card


def _check_picture(picture: bytes, *, which: str = 'its picture', theirs: str = 'a shared card\'s is') -> None:
    limit = MAX_PICTURE_PIXELS // 1_000_000
    if picture.startswith(PNG_SIGNATURE) and picture[12:16] == b'IHDR':  # (a PNG says its size first)
        width, height = struct.unpack('>II', picture[16:24])
    else:
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(picture)) as image:  # (its header only)
                width, height = image.size
        except Image.DecompressionBombError:  # (beyond Pillow's own limit, which is higher)
            raise SafeguardError(f'{which} is more than {2 * Image.MAX_IMAGE_PIXELS // 1_000_000} '
                                 f'megapixels, and {theirs} {limit} at most', what='picture') from None
        except (UnidentifiedImageError, OSError, ValueError):
            return  # (not a picture this app reads: the card comes without one)
    if width * height > MAX_PICTURE_PIXELS:
        raise SafeguardError(f'{which} is {width:,} × {height:,} pixels ({width * height / 1e6:.0f} '
                             f'megapixels), and {theirs} {limit} megapixels at most', what='picture')


def megabytes(size: int) -> str:
    """'20 MB', '23.4 MB' (MiB, as the format's limits)."""
    return f'{size / 1024 / 1024:.1f}'.removesuffix('.0') + ' MB'


@dataclass
class Imported:
    character: Character
    book: LoreBook | None = None  # the lore book it's linked to
    book_reused: bool = False     # True: one of yours, with the same entries, rather than a new one


def save_card(card: Card, characters: CharacterStore, lore_books: JsonStore[LoreBook]) -> Imported:
    """Save what a card holds: its picture, its lore book, and the character, linked to it.

    A lore book with the same name and entries as one you have isn't added twice: the character
    is linked to yours. One with the same name but other entries is added under another name.
    """
    character = card.character
    store_pictures(card, characters)
    imported = Imported(character)
    if card.book is not None:
        book = card.book
        books = lore_books.list()
        same = next((b for b in books if b.name == book.name and same_book(b, book)), None)
        if same is not None:
            imported.book, imported.book_reused = same, True
        else:
            names = {b.name for b in books}
            if book.name in names:
                base = f'{book.name} ({character.title})'
                book.name = next(n for n in (base, *(f'{base} {i}' for i in range(2, 1000))) if n not in names)
            lore_books.save(book)
            imported.book = book
        character.add_lorebook(imported.book.id, imported.book.name)
    characters.save(character)
    return imported


def store_pictures(card: Card, characters: CharacterStore) -> None:
    """Store the card's pictures as files: its own (the character's picture, kept whole and framed from the top:
    see profiles.py) and its description's. Only files: it can run in a worker thread, before save_card (which
    does it, when it wasn't done). A picture this app can't read goes: the character comes without it."""
    if card.pictures_stored:
        return
    character = card.character
    if card.picture:
        try:
            characters.set_picture(character, card.picture)
        except ValueError:  # not a picture this app can read
            pass
    character.description = with_stored_pictures(character.description, characters.images.add)
    card.pictures_stored = True


# -- the files ---------------------------------------------------------------------------------

def _from_png(content: bytes, limit: int = MAX_CARD_BYTES) -> dict:
    chunks = png_text_chunks(content, limit=limit)
    for key in ('ccv3', 'chara'):
        if key in chunks:
            try:
                text = base64.b64decode(chunks[key] + '=' * (-len(chunks[key]) % 4)).decode('utf-8')
            except (binascii.Error, UnicodeDecodeError):
                raise CardError(f'The card in this picture can\'t be read (its "{key}" data is damaged).') from None
            return _json(text.encode('utf-8'), 'The card in this picture can\'t be read (it isn\'t valid JSON).')
    raise CardError('This picture has no character card in it.')


def png_text_chunks(content: bytes, *, limit: int = MAX_CARD_BYTES) -> dict[str, str]:
    """The text chunks of a PNG file (tEXt, zTXt, iTXt), by keyword. Read by hand: cards can be large (and only
    the text chunks are copied: not the picture's data). `limit`: what a compressed one may unpack to."""
    chunks: dict[str, str] = {}
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(content):
        length, kind = struct.unpack('>I4s', content[pos:pos + 8])
        start, pos = pos + 8, pos + 12 + length  # length, type, data, CRC
        if kind == b'IEND':
            break
        if kind not in TEXT_CHUNKS:
            continue
        data = content[start:start + length]
        try:
            if kind == b'tEXt':
                key, _, value = data.partition(b'\0')
                chunks.setdefault(key.decode('latin-1'), value.decode('latin-1'))
            elif kind == b'zTXt':
                key, _, rest = data.partition(b'\0')
                chunks.setdefault(key.decode('latin-1'), _inflate(rest[1:], limit).decode('latin-1'))
            else:  # iTXt
                key, _, rest = data.partition(b'\0')
                compressed, rest = rest[0], rest[2:]
                _language, _, rest = rest.partition(b'\0')
                _translated, _, value = rest.partition(b'\0')
                chunks.setdefault(key.decode('latin-1'),
                                  (_inflate(value, limit) if compressed else value).decode('utf-8'))
        except (zlib.error, UnicodeDecodeError, IndexError):
            continue  # a damaged chunk: skip it
    return chunks


def without_text(content: bytes) -> bytes:
    """A PNG without its text chunks: a card's picture, without the card's data (Pillow refuses a picture with more
    than 64 MB of text chunks, and they're of no use once the card is read)."""
    view, parts, pos = memoryview(content), [content[:len(PNG_SIGNATURE)]], len(PNG_SIGNATURE)
    while pos + 8 <= len(content):
        length, kind = struct.unpack('>I4s', content[pos:pos + 8])
        end = pos + 12 + length
        if kind not in TEXT_CHUNKS:
            parts.append(view[pos:end])
        pos = end
        if kind == b'IEND':
            break
    return b''.join(parts)


def _inflate(data: bytes, limit: int = MAX_CARD_BYTES) -> bytes:
    """zlib data, unpacked up to `limit` bytes (CardError beyond)."""
    inflater = zlib.decompressobj()
    out = inflater.decompress(data, limit)
    if inflater.unconsumed_tail:
        raise CardError(TOO_BIG.format(megabytes(limit)))
    return out


def _read_member(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    """A file from a zip, read up to `limit` bytes (CardError beyond, whatever the zip claims its size is)."""
    with archive.open(name) as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise CardError(TOO_BIG.format(megabytes(limit)))
    return data


def _from_charx(content: bytes, limit: int = MAX_CARD_BYTES) -> tuple[dict, bytes | None]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        names = set(archive.namelist())
        if 'card.json' not in names:
            raise CardError('This zip file has no card.json: it isn\'t a CharX character card.')
        data = _json(_read_member(archive, 'card.json', limit),
                     'The card.json in this CharX file isn\'t valid JSON.')
        picture = None
        fields = data.get('data') if isinstance(data.get('data'), dict) else {}
        uri = _main_icon(fields)
        if uri and uri.startswith('embeded://') and uri[len('embeded://'):] in names:
            picture = _read_member(archive, uri[len('embeded://'):], max(limit, MAX_PICTURE_BYTES))
        if picture is None:  # no assets list: the first picture in the icon folder, if any
            icons = sorted(n for n in names if n.startswith('assets/icon/') and not n.endswith('/'))
            picture = _read_member(archive, icons[0], max(limit, MAX_PICTURE_BYTES)) if icons else None
        return data, picture
    except (zipfile.BadZipFile, KeyError, RuntimeError, OSError):
        raise CardError('This CharX file is damaged, or protected by a password.') from None


def _json(content: bytes, problem: str) -> dict:
    try:
        data = json.loads(content.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CardError(problem) from None
    if not isinstance(data, dict):
        raise CardError(problem)
    return data


def _main_icon(fields: dict) -> str:
    assets = fields.get('assets')
    if not isinstance(assets, list):
        return ''
    icons = [a for a in assets if isinstance(a, dict) and a.get('type') == 'icon']
    main = next((a for a in icons if a.get('name') == 'main'), icons[0] if icons else None)
    return str(main.get('uri') or '') if main else ''


def _embedded_picture(fields: dict) -> bytes | None:
    """A V3 JSON card's picture, when it's in the card itself (a data: URI). Links aren't followed."""
    uri = _main_icon(fields)
    if uri.startswith('data:') and ';base64,' in uri:
        try:
            return base64.b64decode(uri.split(';base64,', 1)[1])
        except binascii.Error:
            return None
    return None


# -- the card ----------------------------------------------------------------------------------

def _unwrap(data: dict) -> tuple[str, dict]:
    """('V1' | 'V2' | 'V3', the card's fields)."""
    spec = str(data.get('spec') or '')
    inner = data.get('data')
    if isinstance(inner, dict) and (spec.startswith('chara_card_v') or 'name' in inner):
        return ('V3' if spec == 'chara_card_v3' else 'V2'), inner
    if 'entries' in data and 'name' not in data:
        raise CardError('This is a lore book, not a character card: import it in Lore books.')
    if any(key in data for key in ('name', *PYGMALION)):
        fields = dict(data)
        for old, new in PYGMALION.items():
            if old in fields and not fields.get(new):
                fields[new] = fields[old]
        return 'V1', fields
    raise CardError('This file isn\'t a character card: it has no character in it.')


def _to_app(fields: dict, version: str, picture: bytes | None, fallback_name: str,
            languages: list[str]) -> Card:
    def text(key: str) -> str:
        value = fields.get(key)
        return value.strip() if isinstance(value, str) else ''

    def card_text(key: str) -> str:  # text that ends up in the chat or the prompt
        return without_outside_pictures(text(key))

    name = text('name') or fallback_name or 'Imported character'
    character = new_character(name)
    character.name = text('nickname') or name
    notes = _creator_notes(fields, languages).strip()
    character.description = without_outside_pictures(notes)
    character.tags = list(normalize_tags(t for t in fields.get('tags') or [] if isinstance(t, str))) \
        if isinstance(fields.get('tags'), list) else []
    parts = [card_text('description'),
             f'{{{{char}}}}\'s personality: {card_text("personality")}' if card_text('personality') else '',
             f'Scenario: {card_text("scenario")}' if card_text('scenario') else '']
    character.scenario = '\n\n'.join(part for part in parts if part)
    greetings = [card_text('first_mes'), *(without_outside_pictures(g.strip())
                                           for g in fields.get('alternate_greetings') or [] if isinstance(g, str))]
    character.first_messages = [g for g in greetings if g] or ['']
    character.example_dialogues = split_examples(card_text('mes_example'))

    extensions = fields.get('extensions')
    shared = extensions.get(SHARED_EXTENSION) if isinstance(extensions, dict) else None
    uuid_ = shared.get('card_uuid') if isinstance(shared, dict) else None
    if isinstance(uuid_, str) and SHARED_UUID.fullmatch(uuid_):
        character.shared_uuid = uuid_
    summary = shared.get('summary') if isinstance(shared, dict) else None
    if isinstance(summary, str):  # (this app's own: as it is, even empty)
        character.summary = clip(summary.strip(), MAX_SUMMARY_BYTES)
        if character.summary and character.description == character.summary:
            character.description = ''  # (exported without a description: its summary stood for it)
    else:
        character.summary = first_sentence(notes)

    book = None
    raw_book = fields.get('character_book')
    if isinstance(raw_book, dict) and isinstance(raw_book.get('entries'), list) and raw_book['entries']:
        book_name = str(raw_book.get('name') or '').strip() or f'{name}\'s lore'
        book = convert_book(raw_book, book_name)
    return Card(character=character, version=version, picture=picture, book=book)


def _creator_notes(fields: dict, languages: list[str]) -> str:
    """V3: the notes in the first of `languages` the card has, else English, else creator_notes."""
    notes = fields.get('creator_notes') if isinstance(fields.get('creator_notes'), str) else ''
    multilingual = fields.get('creator_notes_multilingual')
    if isinstance(multilingual, dict):
        texts = {str(k).lower(): v for k, v in multilingual.items() if isinstance(v, str) and v.strip()}
        for language in [*languages, 'en']:
            if language in texts:
                return texts[language]
        if not notes and texts:
            return next(iter(texts.values()))
    return notes


_OUTSIDE = r'(?:https?:)?//'  # an address on another website (http://, https:// or //)
_MD_PICTURE = re.compile(r'!\[[^\]\n]*\]\(\s*<?' + _OUTSIDE + r'[^)\n]*\)')   # ![alt](https://...)
_MD_REFERENCE = re.compile(r'^[ \t]*\[([^\]\n]+)\]:[ \t]*<?' + _OUTSIDE + r'\S*.*$', re.MULTILINE)
_HTML_PICTURE = re.compile(r'<(?:img|image|source)\b[^>]*>', re.IGNORECASE)
_HTML_MEDIA = re.compile(r'<(video|audio|picture)\b[^>]*>.*?</\1\s*>', re.IGNORECASE | re.DOTALL)
_OUTSIDE_ATTRIBUTE = re.compile(r'\b(?:src|srcset|href|poster)\s*=\s*["\']?\s*' + _OUTSIDE, re.IGNORECASE)
_EMPTY_LINK = re.compile(r'\[\s*\]\([^)\n]*\)')


def without_outside_pictures(text: str) -> str:
    """The text without pictures (and videos, sounds) loaded from other websites.

    Showing one tells that website your address and when you opened the chat. Markdown
    pictures ![...](https://...), reference ones ![...][name], and HTML <img>, <picture>,
    <video>... go; a link left empty by a picture inside it goes too. Pictures written into
    the text itself (data:...) stay: they don't reach anywhere.
    """
    if not text:
        return text
    references = {m.group(1).strip().lower() for m in _MD_REFERENCE.finditer(text)}
    result = _MD_PICTURE.sub('', text)
    if references:
        result = re.sub(r'!\[([^\]\n]*)\](?:\[([^\]\n]*)\])?',
                        lambda m: '' if (m.group(2) or m.group(1)).strip().lower() in references else m.group(0),
                        result)
    result = _HTML_MEDIA.sub(lambda m: '' if _OUTSIDE_ATTRIBUTE.search(m.group(0)) else m.group(0), result)
    result = _HTML_PICTURE.sub(lambda m: '' if _OUTSIDE_ATTRIBUTE.search(m.group(0)) else m.group(0), result)
    result = _EMPTY_LINK.sub('', result)
    for name in references:  # definitions only those pictures used
        body = _MD_REFERENCE.sub('', result)
        if not re.search(r'\[' + re.escape(name) + r'\]', body, re.IGNORECASE):
            result = re.sub(r'^[ \t]*\[' + re.escape(name) + r'\]:.*$\n?', '', result,
                            flags=re.MULTILINE | re.IGNORECASE)
    if result == text:
        return text
    result = re.sub(r'[ \t]+\n', '\n', result)
    return re.sub(r'\n{3,}', '\n\n', result).strip()


def split_examples(text: str) -> list[str]:
    """SillyTavern's example messages: dialogues separated by <START> lines."""
    return [part.strip() for part in re.split(r'<START>', text, flags=re.IGNORECASE) if part.strip()]


def plain_text(text: str) -> str:
    """Creator notes are often HTML or Markdown with pictures: keep the words, as plain text."""
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)  # Markdown pictures
    text = re.sub(r'(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>', '\n', text)
    text = re.sub(r'(?s)<(style|script)\b.*?</\1>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


# -- the card's lore book ----------------------------------------------------------------------------

def convert_book(book: dict, name: str) -> LoreBook:
    """A card's character_book as a lore book, the way SillyTavern converts it.

    Cards keep SillyTavern's own entry settings in each entry's "extensions"; the spec's fields
    give the rest. Like SillyTavern, it ignores V3's "use_regex" (SillyTavern writes it on every
    entry, plain words included): keys are regular expressions when written /like this/. V3
    decorators (lines starting with @@ at the top of an entry) are left out: this app doesn't use them.
    """
    result = new_lore_book(name)
    for raw in book.get('entries') or []:
        if not isinstance(raw, dict):
            continue
        ext = raw.get('extensions') if isinstance(raw.get('extensions'), dict) else {}
        position = ext.get('position')
        if not isinstance(position, int):
            position = 0 if raw.get('position') == 'before_char' else 1
        keys = [str(k) for k in raw.get('keys') or [] if str(k).strip()]
        comment = str(raw.get('comment') or raw.get('name') or '')
        fields: dict[str, Any] = {
            'key': keys,
            'keysecondary': [str(k) for k in raw.get('secondary_keys') or [] if str(k).strip()],
            'comment': comment,
            'addMemo': bool(comment),
            'content': without_outside_pictures(_without_decorators(str(raw.get('content') or ''))),
            'constant': bool(raw.get('constant', False)),
            'selective': bool(raw.get('selective', False)),
            'order': raw.get('insertion_order', 100),
            'position': position,
            'disable': not raw.get('enabled', True),
            'caseSensitive': ext.get('case_sensitive', raw.get('case_sensitive')),
            'role': ext.get('role', 0) if position == AT_DEPTH else None,
        }
        for ours, theirs in ENTRY_EXTENSIONS:
            if theirs in ext:
                fields[ours] = ext[theirs]
        result.add_entry(**_valid(fields))
    return result


def _valid(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop the settings a card got wrong (a depth that isn't a number...), keeping the rest of the entry."""
    while True:
        try:
            LoreEntry(uid=0, **fields)
            return fields
        except ValidationError as e:
            bad = {str(error['loc'][0]) for error in e.errors() if error['loc']}
            if not bad & fields.keys():
                return {k: v for k, v in fields.items() if k in ('key', 'content', 'comment')}
            fields = {k: v for k, v in fields.items() if k not in bad}


def _without_decorators(content: str) -> str:
    lines = content.split('\n')
    while lines and lines[0].lstrip().startswith('@@'):
        lines.pop(0)
    return '\n'.join(lines).strip()


def same_book(a: LoreBook, b: LoreBook) -> bool:
    """Whether two lore books hold the same entries (keys and text), whatever their settings and ids."""
    def entries(book: LoreBook) -> list[tuple[tuple[str, ...], str]]:
        return sorted((tuple(e.key), e.content.strip()) for e in book.entries.values())
    return entries(a) == entries(b)


# -- writing a card ----------------------------------------------------------------------------------

def card_data(character: Character, books: list[LoreBook] = (), *,
              pictures: Callable[[str], str] | None = None) -> dict:
    """The character as a V2 card (see the module's notes), with its lore books merged into one. `pictures`:
    writes its description's pictures into it (descriptions.with_inlined_pictures); without, they're left as
    they are, by their file's name (made from its content: enough to tell whether a card changed)."""
    greetings = [g.strip() for g in character.first_messages if g.strip()]
    description = character.description.strip()
    if description and pictures is not None:
        description = pictures(description)
    notes = description or character.summary.strip()  # (the summary as it is: plain text)
    shared = {'summary': character.summary.strip()}
    if character.shared_uuid:
        shared['card_uuid'] = character.shared_uuid
    data = {
        'name': character.name.strip() or character.title,
        'description': character.scenario.strip(),
        'personality': '',
        'scenario': '',
        'first_mes': greetings[0] if greetings else '',
        'mes_example': '\n'.join(f'<START>\n{d.strip()}' for d in character.example_dialogues if d.strip()),
        'creator_notes': notes,
        'system_prompt': '',
        'post_history_instructions': '',
        'alternate_greetings': greetings[1:],
        'tags': list(character.tags),
        'creator': '',
        'character_version': '',
        'extensions': {SHARED_EXTENSION: shared},
    }
    if any(book.entries for book in books):
        data['character_book'] = book_to_card(books, data['name'])
    v1 = {key: data[key] for key in ('name', 'description', 'personality', 'scenario', 'first_mes', 'mes_example')}
    return {**v1, 'spec': 'chara_card_v2', 'spec_version': '2.0', 'data': data}


def book_to_card(books: list[LoreBook], character_name: str) -> dict:
    """Lore books as one character_book, the way SillyTavern writes it (the reverse of convert_book)."""
    entries = []
    for book in books:
        for entry in book.sorted_entries():
            ext: dict[str, Any] = {'position': entry.position, 'display_index': len(entries),
                                   'case_sensitive': entry.caseSensitive,
                                   'role': entry.role if entry.role is not None else 0}
            for ours, theirs in ENTRY_EXTENSIONS:
                ext[theirs] = getattr(entry, ours)
            entries.append({
                'id': len(entries),
                'keys': list(entry.key),
                'secondary_keys': list(entry.keysecondary),
                'comment': entry.comment,
                'content': entry.content,
                'constant': entry.constant,
                'selective': entry.selective,
                'insertion_order': entry.order,
                'enabled': not entry.disable,
                'position': 'before_char' if entry.position == 0 else 'after_char',
                'case_sensitive': entry.caseSensitive,
                'extensions': ext,
            })
    names = [book.name for book in books if book.entries]
    return {'name': ' + '.join(names) if names else f'{character_name}\'s lore', 'extensions': {},
            'entries': entries}


def card_png(card: dict, picture: bytes | None, *, initials: str = '?') -> bytes:
    """A PNG character card: the picture (or the initials, when there's none), with the card inside."""
    return _card_png(json.dumps(card, ensure_ascii=False).encode('utf-8'), picture, initials)


def card_png_size(card: dict, picture: bytes | None, *, initials: str = '?', growth: int = 0) -> int:
    """The size card_png() would make, without making it: the picture's part is drawn once (for the last few
    pictures), and the card's is its JSON in base64. `growth`: bytes its JSON will have more (its description's
    pictures, written in: descriptions.inlining_growth)."""
    text = len(json.dumps(card, ensure_ascii=False).encode('utf-8')) + growth
    return _picture_part(picture, initials) + 4 * -(-text // 3)


@lru_cache(maxsize=4)
def _picture_part(picture: bytes | None, initials: str) -> int:
    return len(_card_png(b'{}', picture, initials)) - len(base64.b64encode(b'{}'))


def _card_png(text: bytes, picture: bytes | None, initials: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

    image = None
    if picture:
        try:
            image = Image.open(io.BytesIO(picture))
            image.load()
        except (OSError, Image.DecompressionBombError):
            image = None
    if image is None:  # like the app's own tiles: initials on the jade tint
        image = Image.new('RGB', (400, 600), '#E6F0EE')
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=150)
        draw.text((200, 300), initials, fill='#1F7A70', font=font, anchor='mm')
    if image.mode not in ('RGB', 'RGBA'):
        image = image.convert('RGBA')
    info = PngImagePlugin.PngInfo()
    info.add_text('chara', base64.b64encode(text).decode('ascii'))
    out = io.BytesIO()
    image.save(out, 'PNG', pnginfo=info, optimize=False)
    return out.getvalue()
