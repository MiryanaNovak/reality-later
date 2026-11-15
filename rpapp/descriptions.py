"""A character's two texts for people (the AI never gets either):

    summary       a sentence or two, plain text: on its card in the list of characters, and in the announcement
                  when it's published (the format's description: MAX_SUMMARY_BYTES at most)
    description   as long as one likes: Markdown, the most basic HTML, links, and pictures of its own

The description's pictures are files of the characters' image folder, written into its text as

    ![The glade at dawn](picture:3f9a2c81d0b4e5a7.webp)        (or <img src="picture:3f9a2c81d0b4e5a7.webp">)

Shown, they're the app's own (/media/characters/...: with_picture_urls); exported, written into the card itself
(data: URIs: with_inlined_pictures); imported, the card's become files again (with_stored_pictures). Nothing
comes from anywhere else: pictures from websites are taken out on import (cards.without_outside_pictures) and
never shown (safe_html.description_html).
"""
from __future__ import annotations

import base64
import binascii
import html
import re
from collections.abc import Callable, Iterator

from .publishing.announcements import MAX_DESCRIPTION_BYTES

MAX_SUMMARY_BYTES = MAX_DESCRIPTION_BYTES  # (UTF-8) what a published card's announcement holds: 16 KB

PICTURE_REF = re.compile(r'picture:([0-9a-f]{16,64}\.(?:webp|png|jpe?g|gif))')
# A picture written into the text itself: only raster ones (an SVG can hold scripts and links)
DATA_PICTURE = re.compile(r'data:image/(png|jpeg|jpg|gif|webp);base64,([A-Za-z0-9+/]+={0,2})', re.IGNORECASE)
MIME = {'webp': 'image/webp', 'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'gif': 'image/gif'}


def pictures_in(text: str) -> set[str]:
    """The file names of the pictures a description shows."""
    return set(PICTURE_REF.findall(text or ''))


def with_picture_urls(text: str, url: Callable[[str], str | None]) -> str:
    """The description with its pictures' addresses (`url`: a file name's, None when it's gone, then left as it
    is: not shown)."""
    return PICTURE_REF.sub(lambda m: url(m.group(1)) or m.group(0), text or '')


def with_inlined_pictures(text: str, read: Callable[[str], bytes | None]) -> str:
    """The description with its pictures written into it (data: URIs), as cards carry them. `read`: a file's
    content, None when it's gone (then left as it is)."""
    def inline(m: re.Match) -> str:
        content = read(m.group(1))
        if content is None:
            return m.group(0)
        mime = MIME.get(m.group(1).rsplit('.', 1)[-1].lower(), 'image/webp')
        return f'data:{mime};base64,{base64.b64encode(content).decode("ascii")}'
    return PICTURE_REF.sub(inline, text or '')


def inlining_growth(text: str, size: Callable[[str], int | None]) -> int:
    """How many characters with_inlined_pictures() adds to the text, from the pictures' file sizes only (`size`:
    a file's, None when it's gone): what a card's size grows by, without reading them."""
    growth = 0
    for m in PICTURE_REF.finditer(text or ''):
        n = size(m.group(1))
        if n is not None:
            mime = MIME.get(m.group(1).rsplit('.', 1)[-1].lower(), 'image/webp')
            growth += len(f'data:{mime};base64,') + 4 * -(-n // 3) - len(m.group(0))
    return growth


def inlined_pictures(text: str) -> Iterator[bytes]:
    """The pictures written into a text (data: URIs), decoded (those that can't be are skipped)."""
    for m in DATA_PICTURE.finditer(text or ''):
        try:
            yield base64.b64decode(m.group(2) + '=' * (-len(m.group(2)) % 4))
        except (binascii.Error, ValueError):
            continue


def with_stored_pictures(text: str, store: Callable[[bytes], str]) -> str:
    """The description with the pictures written into it (data: URIs, as in a card) stored as files: `store`
    gives a picture's file name, or raises ValueError when it isn't one it can read (it goes, then)."""
    def stored(m: re.Match) -> str:
        try:
            content = base64.b64decode(m.group(2) + '=' * (-len(m.group(2)) % 4))
            return f'picture:{store(content)}'
        except (binascii.Error, ValueError):
            return ''
    return DATA_PICTURE.sub(stored, text or '')


def utf8_size(text: str) -> int:
    return len((text or '').encode('utf-8'))


def clip(text: str, limit: int = MAX_SUMMARY_BYTES) -> str:
    """The text cut to `limit` bytes (UTF-8), at a word if it can, with an ellipsis."""
    if utf8_size(text) <= limit:
        return text
    cut = text.encode('utf-8')[:limit - 3].decode('utf-8', errors='ignore')
    words = cut.rsplit(None, 1)[0] if ' ' in cut.strip() else cut
    return words.rstrip(' ,;:') + '…'


# -- a summary from creator notes ----------------------------------------------------------------------------

_MD_HEADING = re.compile(r'^[ \t]{0,3}#{1,6}(?:[ \t].*)?$', re.MULTILINE)
_HTML_HEADING = re.compile(r'<h[1-6]\b[^>]*>.*?</h[1-6]\s*>', re.IGNORECASE | re.DOTALL)
# A sentence's end: . ! ? … then a space, where what follows isn't lowercase ('"Who's there?" she asked.' goes
# on), or the end; 。！？ anywhere (no spaces between sentences there)
_SENTENCE = re.compile(r'(.+?(?:[.!?…]+["\'”’»)\]]*(?=\s+(?![a-z])|$)|[。！？]+["\'”’»)\]」』]*))', re.DOTALL)


def as_plain_text(text: str) -> str:
    """Creator notes (HTML or Markdown, pictures and all) as plain text: the words only."""
    text = re.sub(r'(?is)<(style|script|template)\b.*?</\1\s*>', '', text or '')
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)                      # Markdown pictures
    text = re.sub(r'(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|blockquote|center)\s*>|<hr\b[^>]*>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\[([^\]\n]*)\]\([^)\n]*\)', r'\1', text)               # [words](link) -> words
    text = re.sub(r'(?m)^[ \t]{0,3}(?:[-*_][ \t]*){3,}$', '', text)        # rules
    text = re.sub(r'(?m)^[ \t]*(?:>[ \t]?)+', '', text)                     # quotes
    text = re.sub(r'(?m)^[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+', '', text)     # list markers
    text = re.sub(r'(\*{1,3}|_{2,3}|~~|`+)(?=\S)(.+?)(?<=\S)\1', r'\2', text)  # emphasis, strikes, code
    text = html.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def first_sentence(notes: str, limit: int = MAX_SUMMARY_BYTES) -> str:
    """A card's summary when it has none of this app's: the first sentence of its creator notes, as plain text
    (headings, which aren't sentences, left out). A first paragraph without a sentence's end is taken whole."""
    plain = as_plain_text(_MD_HEADING.sub('', _HTML_HEADING.sub('\n\n', notes or '')))
    for paragraph in re.split(r'\n\s*\n', plain):
        paragraph = ' '.join(paragraph.split())
        if not paragraph:
            continue
        found = _SENTENCE.match(paragraph)
        return clip(found.group(1) if found else paragraph, limit)
    return ''
