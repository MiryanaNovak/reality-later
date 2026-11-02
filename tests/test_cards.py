"""Importing character cards: V1, V2 and V3, from PNG, JSON and CharX files."""
import base64
import io
import json
import zipfile

import pytest
from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import user_simulation
from PIL import Image, PngImagePlugin

from rpapp.cards import CardError, convert_book, plain_text, read_card, save_card, split_examples
from rpapp.characters import CharacterStore
from rpapp.lorebooks import lore_book_store, new_lore_book
from rpapp.ui.app import build_root

V1 = {'name': 'Seraphina', 'description': 'A forest guardian.', 'personality': 'kind, protective',
      'scenario': '{{user}} wakes up in the glade.', 'first_mes': '*She smiles.* "You\'re awake."',
      'mes_example': '<START>\n{{user}}: Who are you?\n{{char}}: The guardian.\n<START>\n{{char}}: Rest now.'}

BOOK = {'name': 'Eldoria', 'entries': [
    {'keys': ['eldoria', 'forest'], 'content': 'Eldoria is old.', 'enabled': True, 'insertion_order': 50,
     'position': 'before_char', 'comment': 'Eldoria', 'use_regex': True,
     'extensions': {'position': 0, 'depth': 4, 'probability': 100, 'match_whole_words': True}},
    {'keys': ['glade'], 'content': 'The glade is safe.', 'enabled': False, 'insertion_order': 100,
     'position': 'after_char', 'secondary_keys': ['night'], 'selective': True},
    {'keys': ['shadowfang'], 'content': '@@depth 2\n@@role assistant\nShadowfangs hunt at night.', 'enabled': True,
     'insertion_order': 100, 'extensions': {'position': 4, 'depth': 'deep', 'role': 1, 'sticky': 0}},
]}


def v2(**fields) -> dict:
    return {'spec': 'chara_card_v2', 'spec_version': '2.0', 'data': {**V1, **fields}}


def v3(**fields) -> dict:
    return {'spec': 'chara_card_v3', 'spec_version': '3.0',
            'data': {**V1, 'group_only_greetings': [], **fields}}


def png_card(*chunks: tuple[str, dict], color='green') -> bytes:
    """A picture with cards in text chunks, as SillyTavern writes them."""
    info = PngImagePlugin.PngInfo()
    for key, card in chunks:
        info.add_text(key, base64.b64encode(json.dumps(card).encode('utf-8')).decode('ascii'))
    out = io.BytesIO()
    Image.new('RGB', (40, 60), color).save(out, 'PNG', pnginfo=info)
    return out.getvalue()


def as_json(card: dict) -> bytes:
    return json.dumps(card).encode('utf-8')


# --- the cards ------------------------------------------------------------------------------------

def test_v1():
    card = read_card('seraphina.json', as_json(V1))
    c = card.character
    assert card.version == 'V1' and (c.title, c.name) == ('Seraphina', 'Seraphina')
    assert c.description == ''  # V1 has no notes for people
    assert c.scenario == ('A forest guardian.\n\n{{char}}\'s personality: kind, protective\n\n'
                          'Scenario: {{user}} wakes up in the glade.')
    assert c.first_messages == ['*She smiles.* "You\'re awake."']
    assert c.example_dialogues == ['{{user}}: Who are you?\n{{char}}: The guardian.', '{{char}}: Rest now.']
    assert card.picture is None and card.book is None


def test_old_pygmalion_spelling():
    old = {'char_name': 'Hale', 'char_persona': 'A sailor.', 'world_scenario': 'At sea.', 'char_greeting': 'Ahoy.',
           'example_dialogue': '<START>\nHale: Ahoy.'}
    c = read_card('hale.json', as_json(old)).character
    assert (c.title, c.scenario, c.first_messages, c.example_dialogues) == \
        ('Hale', 'A sailor.\n\nScenario: At sea.', ['Ahoy.'], ['Hale: Ahoy.'])


def test_v2():
    card = read_card('x.json', as_json(v2(
        creator_notes='<p>A gentle guardian.<br>Best with <b>long</b> replies.</p>![art](https://x/y.png)',
        alternate_greetings=['*She waves.*', '  '], character_book=BOOK, system_prompt='Ignored.',
        tags=['fantasy'], personality='')))
    c = card.character
    assert card.version == 'V2'
    assert c.description == 'A gentle guardian.\nBest with long replies.'  # plain text, for the list
    assert c.scenario == 'A forest guardian.\n\nScenario: {{user}} wakes up in the glade.'  # no empty personality
    assert c.first_messages == ['*She smiles.* "You\'re awake."', '*She waves.*']
    assert 'Ignored' not in json.dumps(c.model_dump(mode='json'))  # fields with no place here are left out
    assert card.book is not None and card.book.name == 'Eldoria' and len(card.book.entries) == 3


def test_the_lore_book_is_converted_as_sillytavern_does():
    book = convert_book(BOOK, 'Eldoria')
    first, second, third = book.sorted_entries()
    assert (first.key, first.comment, first.order, first.position, first.matchWholeWords) == \
        (['eldoria', 'forest'], 'Eldoria', 50, 0, True)  # plain words stay plain, "use_regex" or not
    assert (second.disable, second.position, second.keysecondary, second.selective) == (True, 1, ['night'], True)
    assert (third.position, third.role, third.depth) == (4, 1, 4)  # a depth that isn't a number: the default
    assert third.content == 'Shadowfangs hunt at night.'  # V3 decorators left out
    assert first.role is None  # a role only goes with "at a depth"


def test_v3_png_with_both_versions_inside():
    picture = png_card(('chara', v2(name='Old')), ('ccv3', v3(nickname='Sera', creator_notes='Hi.')))
    card = read_card('card.png', picture)
    assert card.version == 'V3' and card.character.title == 'Seraphina'  # ccv3 wins over chara
    assert card.character.name == 'Sera'  # V3: the nickname is what {{char}} stands for
    assert card.picture == picture  # the card's picture is the PNG itself
    assert read_card('old.png', png_card(('chara', v2()))).version == 'V2'


def test_v3_json_notes_in_your_language_and_its_picture():
    icon = base64.b64encode(png_card()).decode('ascii')
    card = v3(creator_notes='English notes.', creator_notes_multilingual={'fr': 'Notes en français.', 'ja': '日本語'},
              assets=[{'type': 'icon', 'name': 'main', 'uri': f'data:image/png;base64,{icon}', 'ext': 'png'},
                      {'type': 'background', 'name': 'bg', 'uri': 'https://example.com/bg.png', 'ext': 'png'}])
    assert read_card('x.json', as_json(card), languages=['fr', 'en']).character.description == 'Notes en français.'
    assert read_card('x.json', as_json(card), languages=['de']).character.description == 'English notes.'
    assert read_card('x.json', as_json(card)).picture == base64.b64decode(icon)


def test_charx():
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('card.json', json.dumps(v3(assets=[
            {'type': 'icon', 'name': 'main', 'uri': 'embeded://assets/icon/images/main.png', 'ext': 'png'}])))
        z.writestr('assets/icon/images/main.png', png_card(color='red'))
    card = read_card('seraphina.charx', out.getvalue())
    assert card.version == 'V3' and card.character.title == 'Seraphina'
    assert card.picture == png_card(color='red')


def test_a_large_card():
    big = v2(description='A very long description. ' * 80_000)  # about 2 MB, more than Pillow reads in one chunk
    assert len(read_card('big.png', png_card(('chara', big))).character.scenario) > 1_900_000


@pytest.mark.parametrize('name, content, message', [
    ('notes.txt', b'Just some text.', 'it\'s not a PNG picture, a JSON file or a CharX file'),
    ('photo.png', png_card(), 'This picture has no character card in it.'),
    ('world.json', json.dumps({'entries': {'0': {'key': ['x']}}}).encode(), 'This is a lore book'),
    ('list.json', b'[1, 2]', 'it\'s not a PNG picture'),
    ('other.json', b'{"hello": "world"}', 'it has no character in it'),
    ('empty.charx', b'PK\x03\x04garbage', 'This CharX file is damaged'),
])
def test_files_that_arent_cards(name, content, message):
    with pytest.raises(CardError, match=message.replace('(', r'\(').replace(')', r'\)')):
        read_card(name, content)


def test_damaged_card_data():
    info = PngImagePlugin.PngInfo()
    info.add_text('chara', '!!! not base64 !!!')
    out = io.BytesIO()
    Image.new('RGB', (4, 4)).save(out, 'PNG', pnginfo=info)
    with pytest.raises(CardError, match='damaged|isn\'t valid JSON'):
        read_card('x.png', out.getvalue())


def test_helpers():
    assert split_examples('<start>\nA: hi\n\n<START>\n\n<START>B: yo') == ['A: hi', 'B: yo']
    assert plain_text('<div>One &amp; two</div><style>p{}</style>\n\n\n\nThree') == 'One & two\n\nThree'


# --- saving ---------------------------------------------------------------------------------------

def test_saving_a_card(tmp_path):
    characters, books = CharacterStore(tmp_path), lore_book_store(tmp_path)
    first = save_card(read_card('card.png', png_card(('chara', v2(character_book=BOOK)))), characters, books)
    saved = characters.load(first.character.id)
    assert saved.image and characters.images.path(saved.image).exists()
    assert first.book.name == 'Eldoria' and not first.book_reused
    assert [ref.name for ref in saved.lorebooks] == ['Eldoria'] and books.exists(first.book.id)

    again = save_card(read_card('card.png', png_card(('chara', v2(character_book=BOOK)))), characters, books)
    assert again.book_reused and again.book.id == first.book.id  # the same book: not added twice
    assert len(books.list()) == 1 and len(characters.list()) == 2

    other = {**BOOK, 'entries': BOOK['entries'][:1]}  # same name, other entries
    third = save_card(read_card('x.json', as_json(v2(name='Hale', character_book=other))), characters, books)
    assert third.book.name == 'Eldoria (Hale)' and len(books.list()) == 2


def test_a_picture_this_app_cant_read(tmp_path):
    card = read_card('x.json', as_json(v3(assets=[
        {'type': 'icon', 'name': 'main', 'uri': 'data:image/png;base64,bm90IGFuIGltYWdl', 'ext': 'png'}])))
    imported = save_card(card, CharacterStore(tmp_path), lore_book_store(tmp_path))
    assert imported.character.image == ''  # it comes without one


# --- the Import button ----------------------------------------------------------------------------

async def test_importing_from_the_characters_page(tmp_path):
    books = lore_book_store(tmp_path)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open('/characters')
        picker = user.find(ui.upload).elements.pop()
        await picker.handle_uploads([SmallFileUpload(name='seraphina.png', content_type='image/png',
                                                     _data=png_card(('ccv3', v3(character_book=BOOK))))])
        await user.should_see('Imported "Seraphina" (V3 card), with its lore book "Eldoria".')
        await user.should_not_see('was not changed')
        await user.should_see('No conversations yet')  # its card in the grid
        [character] = CharacterStore(tmp_path).list()
        assert character.example_dialogues and [b.name for b in books.list()] == ['Eldoria']

        await picker.handle_uploads([SmallFileUpload(name='notes.txt', content_type='text/plain', _data=b'Hello')])
        await user.should_see('Could not import notes.txt: This file isn\'t a character card')


# --- exporting ------------------------------------------------------------------------------------

def seraphina_here():
    from rpapp.characters import new_character
    c = new_character('Seraphina (forest guardian)')
    c.name = 'Seraphina'
    c.description = 'A gentle guardian, for long chats.'
    c.scenario = '{{char}} guards the glade.\n\n{{user}} wakes up there.'
    c.first_messages = ['*She smiles.*', '  ', '*She waves.*']
    c.example_dialogues = ['{{user}}: Hi.\n{{char}}: Hello.', '', '{{char}}: Rest now.']
    return c


def test_what_a_card_holds():
    from rpapp.cards import card_data
    book = convert_book(BOOK, 'Eldoria')
    shared = new_lore_book('Shared')
    shared.add_entry(key=['moon'], content='Two moons.')
    card = card_data(seraphina_here(), [book, shared, new_lore_book('Empty')])
    assert (card['spec'], card['spec_version']) == ('chara_card_v2', '2.0')
    data = card['data']
    assert data['name'] == 'Seraphina'  # the title is only for you
    assert data['description'] == '{{char}} guards the glade.\n\n{{user}} wakes up there.'  # the scenario
    assert (data['personality'], data['scenario']) == ('', '')
    assert data['creator_notes'] == 'A gentle guardian, for long chats.'
    assert (data['first_mes'], data['alternate_greetings']) == ('*She smiles.*', ['*She waves.*'])
    assert data['mes_example'] == '<START>\n{{user}}: Hi.\n{{char}}: Hello.\n<START>\n{{char}}: Rest now.'
    assert {k: card[k] for k in ('name', 'description', 'first_mes')} == \
        {k: data[k] for k in ('name', 'description', 'first_mes')}  # V1 fields at the top too
    merged = data['character_book']
    assert merged['name'] == 'Eldoria + Shared' and len(merged['entries']) == 4  # one book per card
    assert [e['id'] for e in merged['entries']] == [0, 1, 2, 3]
    assert 'character_book' not in card_data(seraphina_here(), [new_lore_book('Empty')])['data']


def test_export_then_import_gives_the_same_character():
    from rpapp.cards import card_data, card_png
    original, book = seraphina_here(), convert_book(BOOK, 'Eldoria')
    png = card_png(card_data(original, [book]), png_card(color='blue'), initials='SE')
    back = read_card('Seraphina.png', png)
    c = back.character
    assert back.version == 'V2' and c.title == c.name == 'Seraphina'
    assert (c.description, c.scenario) == (original.description, original.scenario)
    assert c.first_messages == ['*She smiles.*', '*She waves.*']
    assert c.example_dialogues == ['{{user}}: Hi.\n{{char}}: Hello.', '{{char}}: Rest now.']

    def entries(b):
        return [e.model_dump(exclude={'uid', 'displayIndex'}) for e in b.sorted_entries()]
    assert back.book.name == 'Eldoria' and entries(back.book) == entries(book)
    assert Image.open(io.BytesIO(back.picture)).getpixel((0, 0)) == (0, 0, 255)


def test_a_character_without_a_picture_gets_their_initials():
    from rpapp.cards import card_data, card_png
    png = card_png(card_data(seraphina_here()), None, initials='SE')
    image = Image.open(io.BytesIO(png))
    assert image.size == (400, 600) and image.getpixel((5, 5)) == (230, 240, 238)  # the tint
    assert any(image.getpixel((x, 300)) != (230, 240, 238) for x in range(100, 300))  # the letters
    assert read_card('x.png', png).character.name == 'Seraphina'


async def test_exporting_from_the_characters_page(tmp_path):
    characters, books = CharacterStore(tmp_path), lore_book_store(tmp_path)
    book = convert_book(BOOK, 'Eldoria')
    books.save(book)
    character = seraphina_here()
    character.image = characters.images.add(png_card(color='red'))
    character.add_lorebook(book.id, book.name)
    characters.save(character)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open('/characters')
        user.find(marker='menu-export').click()
        exported = read_card('x.png', (await user.download.next()).content)
        assert exported.character.scenario == character.scenario and len(exported.book.entries) == 3

        await user.open(f'/characters/{character.id}')  # and from the character's own ⋮ menu
        user.find(marker='menu-export').click()
        assert read_card('x.png', (await user.download.next()).content).version == 'V2'


def test_the_file_name(tmp_path):
    from rpapp.ui.app import build_root as _  # noqa: F401  (the UI modules load)
    from rpapp.ui.characters_page import CharactersSection
    section = CharactersSection.__new__(CharactersSection)
    section.store, section.lore_books = CharacterStore(tmp_path), lore_book_store(tmp_path)
    character = seraphina_here()
    character.title = 'Seraphina: forest/guardian?'
    content, name, media_type = section.export_card(character)
    assert (name, media_type) == ('Seraphina forest guardian.png', 'image/png')
    assert content.startswith(b'\x89PNG')


# --- hardening ------------------------------------------------------------------------------------

@pytest.mark.parametrize('text, expected', [
    ('Hi ![banner](https://i.imgur.com/x.png "T")\n\n*She smiles.*', 'Hi\n\n*She smiles.*'),
    ('[![click](https://cdn.x/a.gif)](https://chub.ai/x) Text', 'Text'),
    ('<img src="https://x/a.png" width=300> <img src=//x.com/b.png>Keep <img src="data:image/png;base64,AA">',
     'Keep <img src="data:image/png;base64,AA">'),
    ('<video controls><source src="https://x/v.mp4"></video>After', 'After'),
    ('![pic][1] and a [link][1]\n\n[1]: https://x/y.png', 'and a [link][1]\n\n[1]: https://x/y.png'),
    ('![a][p]\n\n[p]: https://x/y.png\nEnd', 'End'),
    ('A [link](https://example.com) stays, and so does ![a local one](data:image/png;base64,AA).',
     'A [link](https://example.com) stays, and so does ![a local one](data:image/png;base64,AA).'),
])
def test_pictures_from_other_websites_go(text, expected):
    from rpapp.cards import without_outside_pictures
    assert without_outside_pictures(text) == expected


def test_imported_cards_lose_their_outside_pictures():
    picture = '![banner](https://files.catbox.moe/x.png)'
    book = {'entries': [{'keys': ['x'], 'content': f'{picture}\nThe forest.', 'enabled': True}]}
    card = read_card('x.json', as_json(v2(
        description=f'A guardian. {picture}', first_mes=f'{picture}\n*She smiles.*',
        alternate_greetings=[f'<img src="https://x/y.gif">*She waves.*'], mes_example=f'<START>\n{picture}Hi.',
        creator_notes=f'Notes {picture}', character_book=book)))
    c = card.character
    assert 'https://' not in json.dumps(c.model_dump(mode='json')) and 'https://' not in json.dumps(card.book.model_dump(mode='json'))
    assert c.first_messages == ['*She smiles.*', '*She waves.*'] and c.example_dialogues == ['Hi.']
    assert c.scenario.startswith('A guardian.') and c.description == 'Notes'


def test_cards_that_unpack_to_too_much_are_refused():
    from rpapp.cards import MAX_CARD_BYTES
    bomb = b'{"name": "x", "description": "' + b' ' * (MAX_CARD_BYTES + 10) + b'"}'  # compresses to almost nothing
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('card.json', bomb)
    assert len(out.getvalue()) < 200_000
    with pytest.raises(CardError, match='too big'):
        read_card('bomb.charx', out.getvalue())

    info = PngImagePlugin.PngInfo()
    info.add_text('chara', 'A' * (MAX_CARD_BYTES + 10), zip=True)  # a compressed text chunk
    picture = io.BytesIO()
    Image.new('RGB', (4, 4)).save(picture, 'PNG', pnginfo=info)
    assert len(picture.getvalue()) < 200_000
    with pytest.raises(CardError, match='too big'):
        read_card('bomb.png', picture.getvalue())
