"""The card announcements' format, schema 1: docs/card-announcements.md, which this follows step by step.

    build_publication()   a new publication (one version of a card), signed, from what the user wrote
    build_withdrawal()    a withdrawal of publications
    check_publication()   a publication received: what it says, or Rejected with the reason's code
    check_withdrawal()    the same, for a withdrawal
    read_publication()    what an event already checked says (the catalog's), without checking it again
    read_withdrawal()     the same, for a withdrawal

What's built is checked like what's received: a publication this app would refuse is never made (the
error says which rule it breaks, with the same codes). tests/card_announcements.json has the cases.
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata
import uuid
from collections.abc import Container, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from . import nostr
from .nostr import Event, Malformed
from .torrents import CardFile

KIND_CARD = 2003       # NIP-35: a torrent
KIND_WITHDRAWAL = 5    # NIP-09: a deletion request
MARKER = 'ai-roleplay-character-card'  # the t tag relays find them by
SCHEMA = '1'
LANGUAGE_LABEL = 'ISO-639-1'           # NIP-32's namespace for a two-letter language code

MAX_EVENT_BYTES = 48 * 1024
MAX_TAGS = 100
MAX_TITLE = 200                        # characters
MAX_PUBLISHER = 100
MAX_DESCRIPTION_BYTES = 16 * 1024
MAX_CARD_TAGS = 20
MAX_CARD_TAG = 32
MAX_TRACKERS = 5
MAX_TRACKER_BYTES = 256
MAX_REASON = 1000                      # characters of a withdrawal's reason kept
MAX_WITHDRAWN = MAX_TAGS - 2           # publications one withdrawal names (besides its k and t tags)
FUTURE = 15 * 60                       # seconds ahead of our clock still accepted
MAX_FILE_BYTES = 100 * 1024 * 1024    # a card file at most: the format's limit, publishing and downloading

EXTENSIONS = ('png', 'json', 'charx')
REQUIRED = ('title', 'uuid', 'publisher', 'spec', 'rating', 'sha256', 'x', 'file')
RATINGS = ('sfw', 'nsfw')

_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')  # (any version: this app makes v4)
_SPEC = re.compile(r'[a-z0-9_]{1,32}')
_HEX = re.compile(r'[0-9a-f]+')
_CONTROL = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f]')  # (tab, line feed and carriage return are fine)
_TRACKER = re.compile(r'(?:udp|http|https)://[^\s/?#]+[^\s]*')
_LANGUAGE = re.compile(r'[a-z]{2}')


class Rejected(ValueError):
    """Not a valid publication (or withdrawal). `code` is the format's: 'bad_uuid', 'missing_tag:title'..."""

    def __init__(self, code: str, detail: str = '') -> None:
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class PublicationDraft:
    """What the user writes to publish a card (the rest comes from the file and the key)."""
    publisher_name: str
    card_name: str
    card_description: str = ''
    card_tags: tuple[str, ...] = ()
    card_format: str = 'chara_card_v2'          # the card's `spec`: what this app exports
    content_rating: Literal['sfw', 'nsfw'] = 'sfw'
    language: str | None = None                 # a BCP-47 tag ('en', 'fr-CA'): its language is kept


@dataclass(frozen=True, slots=True)
class Publication:
    """One publication (a version of a card), checked: what its signed event says. Never changes: the
    current name of its publisher, whether it's the latest version... are the catalog's."""
    event_id: str
    publisher_id: str              # the event's pubkey
    created_at: int
    schema_version: int
    published_name: str
    card_uuid: str
    card_name: str
    card_description: str
    card_sha256: str
    torrent_infohash: str
    file_name: str
    card_size: int
    card_format: str               # the spec tag: 'chara_card_v2'
    content_rating: str            # 'sfw' or 'nsfw' (a content-warning makes it nsfw)
    language: str | None
    card_tags: tuple[str, ...]
    trackers: tuple[str, ...]
    event: Event


@dataclass(frozen=True, slots=True)
class Withdrawal:
    """A publisher withdrawing publications of theirs (NIP-09): no one can download them any more, but
    those who have them keep them."""
    event_id: str
    publisher_id: str
    created_at: int
    withdrawn: tuple[str, ...]     # event ids (only the publisher's own count: up to the catalog)
    reason: str
    event: Event


# --- building ---------------------------------------------------------------------------------------

def new_card_uuid() -> str:
    """A new card's UUID: its identity, with its publisher's key, for all its versions. Version 4 (random), as
    the format says publishers should make (any version is accepted)."""
    return str(uuid.uuid4())


def build_publication(draft: PublicationDraft, file: CardFile, *, card_uuid: str, secret: bytes,
                      trackers: Iterable[str] = (), created_at: int | None = None,
                      size_limit: int = MAX_FILE_BYTES, aux: bytes | None = None) -> Publication:
    """A new publication of `file`, signed with `secret`. The text is tidied (Unicode NFC, \\n for line
    breaks, spaces at the ends removed), the tags normalized, the language reduced to its two letters.
    Rejected if what's left breaks a rule: a title too long, a file too big..."""
    created_at = int(time.time()) if created_at is None else created_at
    title = tidy(draft.card_name).strip()
    language = language_code(draft.language)
    tags: list[list[str]] = [
        ['t', MARKER], ['schema', SCHEMA], ['alt', f'Character card: {title}'], ['title', title],
        ['uuid', card_uuid], ['publisher', tidy(draft.publisher_name).strip()], ['spec', draft.card_format],
        ['rating', draft.content_rating],
    ]
    if draft.content_rating == 'nsfw':
        tags.append(['content-warning', 'nsfw'])
    tags += [['sha256', file.sha256], ['x', file.infohash], ['file', file.name, str(file.size)]]
    tags += [['t', t] for t in normalize_tags(draft.card_tags)]
    if language:
        tags += [['L', LANGUAGE_LABEL], ['l', language, LANGUAGE_LABEL]]
    tags += [['tracker', t] for t in _valid_trackers(trackers)]
    event = nostr.sign(secret, kind=KIND_CARD, tags=tags, content=tidy(draft.card_description).strip(),
                       created_at=created_at, aux=aux)
    return check_publication(event, now=created_at, size_limit=size_limit)


def build_withdrawal(event_ids: Iterable[str], *, secret: bytes, reason: str = '', created_at: int | None = None,
                     aux: bytes | None = None) -> Withdrawal:
    """A withdrawal of these publications (the publisher's own: others' would be ignored). MAX_WITHDRAWN at most
    (Rejected 'too_large' beyond): more take several."""
    created_at = int(time.time()) if created_at is None else created_at
    tags = [['e', i] for i in dict.fromkeys(event_ids)] + [['k', str(KIND_CARD)], ['t', MARKER]]
    event = nostr.sign(secret, kind=KIND_WITHDRAWAL, tags=tags, content=tidy(reason).strip()[:MAX_REASON],
                       created_at=created_at, aux=aux)
    return check_withdrawal(event, now=created_at)


# --- checking (docs/card-announcements.md, "Checking a publication") --------------------------------

def check_publication(data: Any, *, now: int | None = None, size_limit: int = MAX_FILE_BYTES,
                      blocked: Container[str] = ()) -> Publication:
    """A publication received (an Event, or the event's parsed JSON), checked in the format's order.
    Rejected with the first problem found."""
    event = _event(data)
    _common(event, KIND_CARD)
    if ('t', MARKER) not in (t[:2] for t in event.tags):
        raise Rejected('not_a_card')
    schema = event.tag_values('schema')
    if not schema:
        raise Rejected('missing_tag:schema')
    if len(schema) > 1:
        raise Rejected('duplicate_tag:schema')
    if schema[0][:1] != (SCHEMA,):
        raise Rejected('unsupported_schema', ' '.join(schema[0]))
    _signed(event, now=now, blocked=blocked)
    return _read(event, size_limit)


def read_publication(event: Event) -> Publication:
    """What a publication checked before says (the catalog's events): its signature isn't checked again,
    nor its date or the size limit (both may have changed since)."""
    return _read(event, sys.maxsize)


def _read(event: Event, size_limit: int) -> Publication:
    """Steps 11 and 12 of the checks, and what the publication says."""
    one: dict[str, tuple[str, ...]] = {}
    for name in REQUIRED:
        values = event.tag_values(name)
        if not values:
            raise Rejected(f'missing_tag:{name}')
        if len(values) > 1:
            raise Rejected(f'duplicate_tag:{name}')
        one[name] = values[0]

    def first(name: str) -> str:
        return one[name][0] if one[name] else ''

    title, publisher = first('title'), first('publisher')
    if not title.strip():
        raise Rejected('empty:title')
    if len(title) > MAX_TITLE:
        raise Rejected('too_long:title', f'{len(title)} characters')
    if not _UUID.fullmatch(first('uuid')):
        raise Rejected('bad_uuid', first('uuid'))
    if not publisher.strip():
        raise Rejected('empty:publisher')
    if len(publisher) > MAX_PUBLISHER:
        raise Rejected('too_long:publisher', f'{len(publisher)} characters')
    if not _SPEC.fullmatch(first('spec')):
        raise Rejected('bad_spec', first('spec'))
    if first('rating') not in RATINGS:
        raise Rejected('bad_rating', first('rating'))
    sha256 = first('sha256')
    if not (len(sha256) == 64 and _HEX.fullmatch(sha256)):
        raise Rejected('bad_sha256')
    if not (len(first('x')) == 40 and _HEX.fullmatch(first('x'))):
        raise Rejected('bad_infohash')
    file = one['file']
    if len(file) < 2 or not re.fullmatch(rf'{sha256}\.(?:png|json|charx)', file[0]) \
            or not re.fullmatch(r'[1-9][0-9]{0,15}', file[1]):
        raise Rejected('bad_file', ' '.join(file))
    if int(file[1]) > size_limit:
        raise Rejected('file_too_big', f'{int(file[1])} bytes')
    if len(event.content.encode('utf-8')) > MAX_DESCRIPTION_BYTES:
        raise Rejected('too_long:description')

    warned = bool(event.tag_values('content-warning'))
    return Publication(
        event_id=event.id, publisher_id=event.pubkey, created_at=event.created_at, schema_version=int(SCHEMA),
        published_name=publisher, card_uuid=first('uuid'), card_name=title, card_description=event.content,
        card_sha256=sha256, torrent_infohash=first('x'), file_name=file[0], card_size=int(file[1]),
        card_format=first('spec'), content_rating='nsfw' if warned else first('rating'),
        language=_language(event), card_tags=normalize_tags(v[0] for v in event.tag_values('t') if v),
        trackers=_valid_trackers(v[0] for v in event.tag_values('tracker') if v), event=event)


def check_withdrawal(data: Any, *, now: int | None = None, blocked: Container[str] = ()) -> Withdrawal:
    """A withdrawal received: steps 1, 2 and 6 to 10 of the checks, and at least one publication named."""
    event = _event(data)
    _common(event, KIND_WITHDRAWAL)
    _signed(event, now=now, blocked=blocked)
    return read_withdrawal(event)


def read_withdrawal(event: Event) -> Withdrawal:
    """What a withdrawal says (for one checked before: the catalog's)."""
    withdrawn = tuple(dict.fromkeys(v[0] for v in event.tag_values('e')
                                    if v and len(v[0]) == 64 and _HEX.fullmatch(v[0])))
    if not withdrawn:
        raise Rejected('nothing_withdrawn')
    return Withdrawal(event_id=event.id, publisher_id=event.pubkey, created_at=event.created_at,
                      withdrawn=withdrawn, reason=event.content[:MAX_REASON], event=event)


def _event(data: Any) -> Event:
    if isinstance(data, Event):
        return data
    try:
        return Event.from_dict(data)
    except Malformed as e:
        raise Rejected('malformed', str(e)) from None


def _common(event: Event, kind: int) -> None:
    """Steps 2 and 3: its size, and its kind."""
    if event.size() > MAX_EVENT_BYTES or len(event.tags) > MAX_TAGS:
        raise Rejected('too_large', f'{event.size()} bytes, {len(event.tags)} tags')
    if event.kind != kind:
        raise Rejected('wrong_kind', str(event.kind))


def _signed(event: Event, *, now: int | None, blocked: Container[str]) -> None:
    """Steps 6 to 10: its text, its id, its signature, its date, its publisher."""
    if any(_CONTROL.search(s) for s in (event.content, *(s for t in event.tags for s in t))):
        raise Rejected('control_character')
    if not event.has_valid_id():
        raise Rejected('id_mismatch')
    if not event.has_valid_signature():
        raise Rejected('bad_signature')
    now = int(time.time()) if now is None else now
    if event.created_at > now + FUTURE:
        raise Rejected('from_the_future', f'{event.created_at - now} seconds ahead')
    if event.pubkey in blocked:
        raise Rejected('blocked')


# --- tidying ----------------------------------------------------------------------------------------

def tidy(text: str) -> str:
    """Unicode NFC, \\n for line breaks."""
    return unicodedata.normalize('NFC', text).replace('\r\n', '\n').replace('\r', '\n')


def normalize_tags(tags: Iterable[str]) -> tuple[str, ...]:
    """The card's tags, normalized ("Card tags"): lowercase, `-` for spaces, without a leading #; the ones
    left empty, too long, repeated or past the twentieth, and the marker, dropped."""
    out: list[str] = []
    for tag in tags:
        tag = unicodedata.normalize('NFC', tag).lower().strip().removeprefix('#')
        tag = re.sub(r'\s+', '-', tag.strip())
        if 1 <= len(tag) <= MAX_CARD_TAG and tag != MARKER and tag not in out:
            out.append(tag)
            if len(out) == MAX_CARD_TAGS:
                break
    return tuple(out)


def language_code(tag: str | None) -> str | None:
    """A BCP-47 language tag's language, if it's a two-letter one: 'fr-CA' → 'fr'. None otherwise."""
    code = (tag or '').strip().lower().replace('_', '-').split('-')[0]
    return code if _LANGUAGE.fullmatch(code) else None


def _language(event: Event) -> str | None:
    if (LANGUAGE_LABEL,) not in event.tag_values('L'):
        return None
    return next((v[0] for v in event.tag_values('l')
                 if len(v) >= 2 and v[1] == LANGUAGE_LABEL and _LANGUAGE.fullmatch(v[0])), None)


def _valid_trackers(trackers: Iterable[str]) -> tuple[str, ...]:
    """The trackers that are announce URLs (udp, http, https) of 256 bytes at most: the first 5."""
    out: list[str] = []
    for tracker in trackers:
        tracker = tracker.strip()
        if _TRACKER.fullmatch(tracker) and len(tracker.encode('utf-8')) <= MAX_TRACKER_BYTES and tracker not in out:
            out.append(tracker)
            if len(out) == MAX_TRACKERS:
                break
    return tuple(out)
