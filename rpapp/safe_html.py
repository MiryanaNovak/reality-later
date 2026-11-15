"""Cleaning the HTML of chat messages before it reaches the browser.

Messages come from places you don't control: the AI's replies, and characters imported
from cards found online. Their Markdown is turned into HTML (raw HTML in it passes
through), and chat_html() keeps only what formats text: paragraphs, emphasis, lists,
quotes, code, tables, headings, links and pictures, plus <details> for spoilers.

Everything else goes: scripts and event handlers, of course, but also what could change
the page itself. With a <style> block, or a "style" or "class" attribute (the page's
own classes can pin an element over everything), a message could restyle the app or
draw a fake "enter your API key" box over it; with a <form>, send what you type
elsewhere. Links don't tell the other site where they came from (rel="noreferrer").

It's the first of two layers: the browser also runs NiceGUI's DOMPurify on the result.

A character's description (descriptions.py) is cleaned harder, by description_html(): only the most basic
HTML (no <div>, <span>, <details>...), links to websites only (opened in a new tab), and pictures only its
own: the app's files of them, or ones written into the text (data: URIs). Nothing it shows is loaded from
anywhere else.
"""
from __future__ import annotations

import re

import nh3

TAGS = {
    'p', 'br', 'hr', 'em', 'strong', 'b', 'i', 'u', 's', 'del', 'ins', 'mark', 'small', 'sub', 'sup', 'q',
    'abbr', 'code', 'pre', 'kbd', 'blockquote', 'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
    'a', 'img', 'details', 'summary', 'div', 'span', 'center',
}
ATTRIBUTES = {
    'a': {'href', 'title'},
    'img': {'src', 'alt', 'title', 'width', 'height'},
    'abbr': {'title'},
    'ol': {'start'},
    'td': {'colspan', 'rowspan'},
    'th': {'colspan', 'rowspan'},
    'details': {'open'},
}
# Tags removed with everything inside them (other unknown tags go, their text stays)
DROPPED_WITH_CONTENT = {'script', 'style', 'template', 'noscript', 'iframe', 'object', 'embed', 'textarea',
                        'select', 'title', 'svg', 'math'}


def chat_html(html: str) -> str:
    """The HTML of a message, with only what formats text (see the module's notes)."""
    return nh3.clean(
        html,
        tags=TAGS,
        attributes=ATTRIBUTES,
        clean_content_tags=DROPPED_WITH_CONTENT,
        url_schemes={'http', 'https', 'mailto', 'data'},
        attribute_filter=_url_filter,
        link_rel='noopener noreferrer nofollow',
        strip_comments=True,
    )


def _url_filter(tag: str, attribute: str, value: str) -> str | None:
    """data: URLs only for pictures in the message itself, and only raster ones."""
    if attribute in ('href', 'src') and value.strip().lower().startswith('data:'):
        allowed = tag == 'img' and value.strip().lower().startswith(('data:image/png', 'data:image/jpeg',
                                                                      'data:image/gif', 'data:image/webp'))
        return value if allowed else None
    return value


# -- a character's description ----------------------------------------------------------------------------

DESCRIPTION_TAGS = {
    'p', 'br', 'hr', 'em', 'strong', 'b', 'i', 'u', 's', 'del', 'ins', 'mark', 'small', 'sub', 'sup',
    'code', 'pre', 'blockquote', 'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'table', 'thead', 'tbody', 'tr', 'th', 'td', 'a', 'img',
}
DESCRIPTION_ATTRIBUTES = {
    'a': {'href', 'title'},
    'img': {'src', 'alt', 'title', 'width', 'height'},
    'ol': {'start'},
    'td': {'colspan', 'rowspan'},
    'th': {'colspan', 'rowspan'},
}
_PICTURE_FILE = re.compile(r'[0-9a-f]{16,64}\.(?:webp|png|jpe?g|gif)')
_RASTER = ('data:image/png;', 'data:image/jpeg;', 'data:image/jpg;', 'data:image/gif;', 'data:image/webp;')
_WITHOUT_SOURCE = re.compile(r'<img\b(?![^>]*\ssrc=")[^>]*>')


def description_html(html: str, *, pictures: str) -> str:
    """The HTML of a description, with only the most basic of formatting (see the module's notes). `pictures`:
    where the character's own are ('/media/characters/'): a picture from anywhere else goes."""
    def keep(tag: str, attribute: str, value: str) -> str | None:
        v = value.strip()
        if tag == 'img' and attribute == 'src':
            own = v.startswith(pictures) and _PICTURE_FILE.fullmatch(v[len(pictures):])
            return v if own or v.lower().startswith(_RASTER) else None
        if tag == 'a' and attribute == 'href':
            return v if v.lower().startswith(('https://', 'http://', 'mailto:')) else None
        if attribute in ('width', 'height', 'start', 'colspan', 'rowspan'):
            return v if re.fullmatch(r'\d{1,4}', v) else None
        return value

    cleaned = nh3.clean(
        html,
        tags=DESCRIPTION_TAGS,
        attributes=DESCRIPTION_ATTRIBUTES,
        clean_content_tags=DROPPED_WITH_CONTENT,
        url_schemes={'http', 'https', 'mailto', 'data'},
        attribute_filter=keep,
        link_rel='noopener noreferrer nofollow',
        set_tag_attribute_values={'a': {'target': '_blank'}},
        strip_comments=True,
    )
    return _WITHOUT_SOURCE.sub('', cleaned)  # (a picture from elsewhere: no broken picture in its place)
