"""Spoken words in chat messages: what's between quotation marks, marked for the stylesheet.

mark_dialogue() takes the HTML of a message, already cleaned (see safe_html.py), and wraps
each stretch of dialogue in <span class="dialogue">, its quotation marks included. The
stylesheet gives it the accent's color (theme.py, --dialogue).

What counts:
- "Straight", “curly”, „German“ or „Polish”, «French» and 「Japanese」 『quotes』.
- A quote ends at its closing mark or, without one, at the end of its paragraph or line: a
  speech over several paragraphs only closes in the last one, and a mark the AI forgot
  shouldn't swap speech and narration on the lines after it.
- A straight " right after a letter or a digit doesn't open a quote: 5'10" is a height.
- Code is left alone, and its marks neither open nor close anything.
- Single quotes don't count: ’ is also an apostrophe (don’t), so 'British' dialogue isn't marked.

A quote can run across italics or bold ("I *really* mean it"): each piece of text in it gets
its own span, which keeps the HTML well nested.
"""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser

# opening mark -> the marks that close it
QUOTES = {'"': '"', '“': '”', '„': '“”', '«': '»', '「': '」', '『': '』'}
# Where a quote stops, closed or not: a new block, or a new line
BREAKS = {'p', 'br', 'hr', 'blockquote', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'h1', 'h2', 'h3', 'h4', 'h5',
          'h6', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'details', 'summary', 'div',
          'center', 'pre'}
CODE = {'code', 'pre', 'kbd', 'samp'}
SPAN = '<span class="dialogue">'


def mark_dialogue(html: str) -> str:
    """The HTML, with its dialogue in <span class="dialogue"> (see the module's notes)."""
    marker = _Marker()
    marker.feed(html)
    marker.close()
    return ''.join(marker.out)


class _Marker(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.closing = ''  # in a quote: the marks that end it
        self.last = ''  # the character before, on this line
        self.code = 0  # how deep in code

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in BREAKS:
            self._new_line()
        if tag in CODE:
            self.code += 1
        self.out.append(self.get_starttag_text() or '')

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        if tag in BREAKS:
            self._new_line()
        self.out.append(self.get_starttag_text() or '')

    def handle_endtag(self, tag: str) -> None:
        if tag in CODE:
            self.code = max(0, self.code - 1)
        if tag in BREAKS:
            self._new_line()
        self.out.append(f'</{tag}>')

    def handle_data(self, data: str) -> None:
        if self.code:
            self.out.append(escape(data, quote=False))
            self.last = data[-1:] or self.last
            return
        start = 0
        for i, char in enumerate(data):
            if self.closing:
                if char in self.closing:
                    self._add(data[start:i + 1], spoken=True)
                    start, self.closing = i + 1, ''
            elif char in QUOTES and not (char == '"' and self.last.isalnum()):
                self._add(data[start:i], spoken=False)
                start, self.closing = i, QUOTES[char]
            self.last = char
        self._add(data[start:], spoken=bool(self.closing))

    def _add(self, text: str, *, spoken: bool) -> None:
        if text:
            text = escape(text, quote=False)
            self.out.append(f'{SPAN}{text}</span>' if spoken else text)

    def _new_line(self) -> None:
        self.closing = self.last = ''
