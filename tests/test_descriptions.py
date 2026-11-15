"""A character's summary, description and tags: the model, cards, the pictures, what shows."""
import base64
import io
import json

import pytest
from PIL import Image

from rpapp.cards import SafeguardError, card_data, card_png, check_shared_card, read_card, save_card
from rpapp.characters import Character, CharacterStore, new_character
from rpapp.descriptions import (MAX_SUMMARY_BYTES, as_plain_text, clip, first_sentence, pictures_in,
                                with_inlined_pictures, with_picture_urls, with_stored_pictures)
from rpapp.lorebooks import lore_book_store
from rpapp.safe_html import description_html


def picture(color=(200, 30, 30), size=(40, 30), kind='PNG') -> bytes:
    out = io.BytesIO()
    Image.new('RGB', size, color).save(out, kind)
    return out.getvalue()


def data_uri(content: bytes, mime='image/png') -> str:
    return f'data:{mime};base64,{base64.b64encode(content).decode()}'


def v2(**fields) -> bytes:
    return json.dumps({'spec': 'chara_card_v2', 'spec_version': '2.0',
                       'data': {'name': 'Seraphina', 'description': 'A guardian.', 'first_mes': 'Hi.', **fields}}).encode()


# --- the model --------------------------------------------------------------------------------------------

def test_files_from_before_have_their_description_as_summary(tmp_path):
    store = CharacterStore(tmp_path)
    old = {'id': 'sera-1a2b3c', 'title': 'Seraphina', 'name': 'Seraphina', 'format_version': 1,
           'description': 'A guardian, for long chats.', 'first_messages': ['Hi.']}
    store.path_for(old['id']).write_text(json.dumps(old), encoding='utf-8')
    character = store.load(old['id'])
    assert (character.summary, character.description, character.format_version) == \
        ('A guardian, for long chats.', '', 2)
    store.save(character)
    again = store.load(old['id'])  # (saved as version 2: not moved again)
    assert (again.summary, again.description) == ('A guardian, for long chats.', '')
    assert Character(description='Long.').description == 'Long.'  # (made here: nothing to upgrade)


def test_tags_are_kept_as_shared_cards_have_them():
    character = new_character('Hale')
    character.tags = ['Dark Fantasy', '#Sailor', 'dark-fantasy', '', 'x' * 40, *[f't{i}' for i in range(30)]]
    assert character.tags[:2] == ['dark-fantasy', 'sailor'] and len(character.tags) == 20


# --- texts ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('notes, expected', [
    ('A gentle guardian. Best with long replies.', 'A gentle guardian.'),
    ('# Seraphina\n\nThe last *guardian* of the [Eldwood](https://x.y). She waits.', 'The last guardian of the Eldwood.'),
    ('<h1>Wren</h1><p>A thief with a heart of gold! Or so she says.</p>', 'A thief with a heart of gold!'),
    ('Made for the winter contest\n\nThe rest.', 'Made for the winter contest'),
    ('![banner](https://x/y.png)\n\n> "Who goes there?" asked the guard.', '"Who goes there?" asked the guard.'),
    ('城を守る。その後。', '城を守る。'),
    ('', ''),
    ('<p>One line\nand its next one, without an end', 'One line and its next one, without an end'),
])
def test_a_summary_from_creator_notes(notes, expected):
    assert first_sentence(notes) == expected


def test_plain_text_and_clipping():
    assert as_plain_text('**Bold** and __strong__, `code`, ~~old~~\n- one\n- two\n\n---\n&amp; done') == \
        'Bold and strong, code, old\none\ntwo\n\n& done'
    long = 'word ' * 5000
    clipped = clip(long)
    assert len(clipped.encode()) <= MAX_SUMMARY_BYTES and clipped.endswith('word…')
    assert len(first_sentence('é' * 20_000).encode()) <= MAX_SUMMARY_BYTES


def test_pictures_written_in_or_stored():
    text = 'Look: ![The glade](picture:0123456789abcdef.webp) and <img src="picture:fedcba9876543210.webp">'
    assert pictures_in(text) == {'0123456789abcdef.webp', 'fedcba9876543210.webp'}
    assert with_picture_urls(text, lambda n: f'/media/characters/{n}' if n.startswith('0') else None) == \
        'Look: ![The glade](/media/characters/0123456789abcdef.webp) and <img src="picture:fedcba9876543210.webp">'
    inlined = with_inlined_pictures(text, lambda n: b'abc' if n.startswith('0') else None)
    assert '](data:image/webp;base64,YWJj)' in inlined and 'picture:fedcba9876543210.webp' in inlined
    def store(content: bytes) -> str:
        if content != b'one':
            raise ValueError('not a picture')
        return '0123456789abcdef.webp'
    stored = with_stored_pictures(f'A ![x]({data_uri(b"one")}) B ![y]({data_uri(b"bad")})', store)
    assert stored == 'A ![x](picture:0123456789abcdef.webp) B ![y]()'


# --- what shows -------------------------------------------------------------------------------------------

def test_only_basic_html_and_its_own_pictures_show():
    own = '/media/characters/0123456789abcdef.webp'
    html = description_html(
        f'<p>Hi <b>there</b> <span style="color:red">red</span></p><div class="x">d</div>'
        f'<img src="https://evil.example/x.png"><img src="//evil.example/y.png"><img src="/media/characters/../z.webp">'
        f'<img src="data:image/svg+xml;base64,PHN2Zz4="><img src="{own}" alt="ok" width="200" onerror="alert(1)">'
        f'<img src="data:image/png;base64,iVBOR"><a href="javascript:alert(1)">j</a><a href="/settings">app</a>'
        f'<a href="https://example.com">site</a><script>alert(1)</script><style>p{{}}</style>'
        f'<iframe src="https://x"></iframe><video src="https://x"></video><table background="https://x"><tr>'
        f'<td style="x" colspan="2">t</td></tr></table><details><summary>s</summary>h</details>',
        pictures='/media/characters/')
    assert 'evil' not in html and '../z' not in html and 'svg' not in html and 'style' not in html
    assert 'script' not in html and 'iframe' not in html and 'video' not in html and 'background' not in html
    assert f'<img src="{own}" alt="ok" width="200">' in html and '<img src="data:image/png;base64,iVBOR">' in html
    assert html.count('<img') == 2  # (the others gone whole: no broken picture)
    assert '<a href="https://example.com" target="_blank" rel="noopener noreferrer nofollow">site</a>' in html
    assert 'javascript' not in html and 'href="/settings"' not in html
    assert '<b>there</b>' in html and '<span' not in html and '<div' not in html and '<details' not in html
    assert '<td colspan="2">t</td>' in html


# --- cards ------------------------------------------------------------------------------------------------

def test_a_card_from_elsewhere():
    notes = (f'# Seraphina\n\nThe guardian of the glade. <img src="https://x/y.png">\n\n'
             f'![art]({data_uri(picture())})')
    card = read_card('x.json', v2(creator_notes=notes, tags=['Fantasy', 'Forest Spirit', 3]))
    c = card.character
    assert c.summary == 'The guardian of the glade.'  # (no summary of ours: its first sentence)
    assert 'https://' not in c.description and c.description.startswith('# Seraphina')
    assert 'data:image/png;base64,' in c.description  # (stored when it's saved)
    assert c.tags == ['fantasy', 'forest-spirit']


def test_its_pictures_are_stored_when_saved(tmp_path):
    store, books = CharacterStore(tmp_path), lore_book_store(tmp_path)
    red, blue = picture(), picture((20, 20, 200))
    card = read_card('x.json', v2(creator_notes=f'![red]({data_uri(red)}) ![blue]({data_uri(blue)}) '
                                                f'![bad]({data_uri(b"not a picture")})'))
    character = save_card(card, store, books).character
    names = pictures_in(character.description)
    assert len(names) == 2 and all(store.images.path(n) for n in names)
    assert 'base64' not in store.path_for(character.id).read_text(encoding='utf-8')  # (files, not in the JSON)
    assert '![bad]()' in character.description
    assert character.pictures() == names  # (no picture of its own)


def test_export_then_import_keeps_the_summary_description_pictures_and_tags(tmp_path):
    store = CharacterStore(tmp_path)
    name = store.images.add(picture())
    character = new_character('Seraphina')
    character.summary = 'A guardian.'
    character.description = f'# Seraphina\n\n![The glade](picture:{name})\n\nShe waits.'
    character.tags = ['fantasy']
    data = card_data(character, pictures=lambda text: with_inlined_pictures(
        text, lambda n: store.images.path(n).read_bytes()))
    assert 'picture:' not in data['data']['creator_notes'] and 'data:image/webp;base64,' in data['data']['creator_notes']
    assert data['data']['extensions']['reality_later'] == {'summary': 'A guardian.'}

    other = CharacterStore(tmp_path / 'elsewhere')
    back = save_card(read_card('Seraphina.png', card_png(data, None)), other, lore_book_store(tmp_path / 'elsewhere'))
    c = back.character
    assert (c.summary, c.tags) == ('A guardian.', ['fantasy'])
    assert c.description == character.description  # (the same picture: the same file name, made from its content)
    assert other.images.path(name) is not None


def test_the_summary_of_ours_even_empty_and_without_a_description():
    character = new_character('Hale')
    character.summary = 'A sailor.'
    data = card_data(character)
    assert data['data']['creator_notes'] == 'A sailor.'  # (other apps show creator notes)
    back = read_card('x.json', json.dumps(data).encode()).character
    assert (back.summary, back.description) == ('A sailor.', '')  # (not twice)

    character.summary, character.description = '', 'A long one. With more.'
    back = read_card('x.json', json.dumps(card_data(character)).encode()).character
    assert (back.summary, back.description) == ('', 'A long one. With more.')  # (ours, empty: kept so)


def test_shared_cards_description_pictures_are_checked_too():
    huge = io.BytesIO()
    Image.new('1', (8000, 8000)).save(huge, 'PNG')  # 64 megapixels, a few kilobytes
    check_shared_card('card.png', card_png(card_data(new_character('Wren')), None), size_limit=10**8)  # (fine)
    character = new_character('Wren')
    character.description = f'![big]({data_uri(huge.getvalue())})'
    with pytest.raises(SafeguardError) as e:
        check_shared_card('card.png', card_png(card_data(character), None), size_limit=10**8)
    assert e.value.what == 'picture'
    assert str(e.value) == ('one of the pictures in its description is 8,000 × 8,000 pixels (64 megapixels), and a '
                            'shared card\'s pictures are 50 megapixels at most')


# --- the pictures' files ----------------------------------------------------------------------------------

def test_picture_files_go_when_nothing_uses_them(tmp_path):
    store = CharacterStore(tmp_path)
    kept, dropped, shared = (store.images.add(picture(c)) for c in ((1, 1, 1), (2, 2, 2), (3, 3, 3)))
    sera, hale = new_character('Seraphina'), new_character('Hale')
    sera.description = f'![a](picture:{kept}) ![b](picture:{shared})'
    hale.description = f'![b](picture:{shared})'
    store.save(sera)
    store.save(hale)
    assert store.sweep_pictures() == 1 and store.images.path(dropped) is None  # (in no description)
    assert store.images.path(kept) and store.images.path(shared)

    store.delete(sera.id)
    assert store.images.path(kept) is None and store.images.path(shared) is not None  # (Hale's still)


def test_no_sweep_while_a_file_cant_be_read(tmp_path):
    store = CharacterStore(tmp_path)
    name = store.images.add(picture())
    store.path_for('broken-1a2b3c').write_text('{not json', encoding='utf-8')
    assert store.sweep_pictures() == 0 and store.images.path(name) is not None  # (it may be the one using it)


def test_the_starter_set_has_the_descriptions_pictures(tmp_path):
    from rpapp.starter import freeze
    source = tmp_path / 'data'
    store = CharacterStore(source)
    name = store.images.add(picture())
    character = new_character('Seraphina')
    character.description = f'![a](picture:{name})'
    store.save(character)
    report = freeze(source, tmp_path / 'starter')
    assert (tmp_path / 'starter' / 'characters' / 'images' / name).is_file() and report.pictures == 1
