"""Framing pictures: the whole picture is kept, a square of it is shown everywhere."""
import asyncio
import io
from types import SimpleNamespace

import pytest
from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import user_simulation
from PIL import Image

from rpapp.cards import card_data, card_png, read_card, save_card
from rpapp.characters import CharacterStore, new_character
from rpapp.lorebooks import lore_book_store
from rpapp.personas import PersonaStore, new_persona
from rpapp.profiles import FRAMED_SIZE, Frame, default_frame
from rpapp.ui.app import build_root
from rpapp.ui.frame_editor import FrameEditor


def portrait(width=400, height=600) -> bytes:
    """A tall picture: blue at the top (where the face would be), red below."""
    image = Image.new('RGB', (width, height), 'red')
    image.paste(Image.new('RGB', (width, height // 6), 'blue'), (0, 0))
    out = io.BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()


def color_at(store, name, x=5, y=5):
    """The color at a point, rounded (WebP shifts colors slightly): (255, 1, 0) -> (255, 0, 0)."""
    pixel = Image.open(store.images.path(name)).convert('RGB').getpixel((x, y))
    return tuple(255 if v > 200 else 0 if v < 50 else v for v in pixel)


def test_frames():
    assert default_frame(400, 600) == Frame(x=0, y=0, size=1)          # tall: from the top
    assert default_frame(800, 400) == Frame(x=0.25, y=0, size=1)       # wide: centered
    assert Frame(x=0.25, y=0.5, size=0.5).box(400, 600) == (100, 300, 300, 500)
    assert Frame(x=0.9, y=0.9, size=0.5).box(400, 600) == (200, 400, 400, 600)  # kept inside the picture
    with pytest.raises(ValueError):
        Frame(size=0)


def test_pictures_are_kept_whole_and_shown_framed(tmp_path):
    store = CharacterStore(tmp_path)
    hale = new_character('Hale')
    store.set_picture(hale, portrait())
    store.save(hale)
    assert store.images.size(hale.image_full) == (400, 600)                 # the whole picture
    assert store.images.size(hale.image) == (400, 400)                      # the square shown
    assert color_at(store, hale.image) == (0, 0, 255)                       # from the top

    shown = hale.image
    store.frame_picture(hale, Frame(x=0, y=0.5, size=0.5))
    store.save(hale)
    assert store.images.size(hale.image) == (200, 200) and color_at(store, hale.image) == (255, 0, 0)
    assert store.images.path(shown) is None                                 # the old square is gone
    assert store.images.path(hale.image_full) is not None and hale.image_frame.y == 0.5

    copy = store.duplicate(hale)  # shares the files
    store.remove_picture(hale)
    store.save(hale)
    assert store.images.path(copy.image) is not None and store.images.path(copy.image_full) is not None
    store.delete(copy.id)
    assert list(store.images.folder.iterdir()) == []                        # nobody uses them any more


def test_big_pictures_are_shown_at_most_512_pixels(tmp_path):
    store = PersonaStore(tmp_path)
    mira = new_persona('Mira')
    store.set_picture(mira, portrait(2000, 3000))  # stored whole at 1024 at most
    assert store.images.size(mira.image_full) == (683, 1024)
    assert store.images.size(mira.image) == (FRAMED_SIZE, FRAMED_SIZE)


def test_pictures_from_before_framing(tmp_path):
    store = CharacterStore(tmp_path)
    old = new_character('Old')
    old.image = store.images.add(portrait())  # as saved before: the whole picture, no frame
    store.save(old)
    whole = old.image
    store.frame_picture(old, Frame(x=0, y=0, size=1))
    store.save(old)
    assert old.image_full == whole and old.image != whole and store.images.path(whole) is not None


def test_cards_keep_the_whole_picture(tmp_path):
    characters = CharacterStore(tmp_path)
    character = new_character('Seraphina')
    data = card_data(character)
    imported = save_card(read_card('s.png', card_png(data, portrait(), initials='SE')), characters,
                         lore_book_store(tmp_path)).character
    assert characters.images.size(imported.image_full) == (400, 600)
    assert color_at(characters, imported.image) == (0, 0, 255)  # framed from the top, where faces are
    # Exporting it again gives the whole picture back, not the square.
    from rpapp.ui.characters_page import CharactersSection
    section = CharactersSection.__new__(CharactersSection)
    section.store, section.lore_books = characters, lore_book_store(tmp_path)
    content, _, _ = section.export_card(imported)
    assert Image.open(io.BytesIO(content)).size == (400, 600)


def test_the_browser_numbers_are_kept_in_bounds():
    editor = SimpleNamespace(frame=Frame())
    FrameEditor._changed(editor, SimpleNamespace(args={'x': 2, 'y': -1, 'size': 0}))
    assert editor.frame == Frame(x=1, y=0, size=0.01)
    FrameEditor._changed(editor, SimpleNamespace(args={'x': 'nonsense'}))
    assert editor.frame == Frame(x=1, y=0, size=0.01)  # unchanged
    FrameEditor._changed(editor, SimpleNamespace(args=None))
    assert editor.frame == Frame(x=1, y=0, size=0.01)


async def wait_for(condition, tries=60):
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(0.05)
    assert condition()


async def test_framing_in_the_editor(tmp_path):
    store = CharacterStore(tmp_path)
    hale = new_character('Hale')
    store.save(hale)
    async with user_simulation(build_root(tmp_path)) as user:
        await user.open(f'/characters/{hale.id}')
        await user.should_see('Add picture')
        picker = user.find(ui.upload).elements.pop()
        await picker.handle_uploads([SmallFileUpload(name='hale.png', content_type='image/png', _data=portrait())])
        await user.should_see(marker='frame-dialog', retries=40)
        await user.should_see('Frame the new picture')
        saved = store.load(hale.id)
        assert color_at(store, saved.image) == (0, 0, 255)  # framed by default, already
        [editor] = user.find(marker='frame-editor').elements
        editor.frame = Frame(x=0, y=0.5, size=0.5)  # as the browser would say, after a drag and a zoom
        with user.scope(marker='frame-dialog'):
            user.find(marker='frame-save').click()
        await wait_for(lambda: store.load(hale.id).image_frame == Frame(x=0, y=0.5, size=0.5))
        assert color_at(store, store.load(hale.id).image) == (255, 0, 0)

        user.find(marker='reframe-picture').click()  # again, later
        await user.should_see('Reframe the picture')
        [editor] = user.find(marker='frame-editor').elements
        assert editor.frame == Frame(x=0, y=0.5, size=0.5)  # opens where it was
        editor.frame = Frame(x=0, y=0, size=1)
        with user.scope(marker='frame-dialog'):
            user.find(marker='frame-cancel').click()
        await user.should_not_see(marker='frame-dialog')
        assert store.load(hale.id).image_frame == Frame(x=0, y=0.5, size=0.5)  # Cancel changes nothing
