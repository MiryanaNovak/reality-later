# Character card announcements — format, schema 1

How a publication of a character card is written on Nostr: the one part of card sharing that other
people's computers read, and that can't be changed once it's out (a signed event stays as it is,
wherever it was copied). Everything else (the catalog, the API, the user interface) can change from one
version of the app to the next; this can only be extended (see [Changes](#changes)).

**Status: fixed** — schema 1, as of 8 November 2026. Nothing in it changes any more; it can only be
extended, as [Changes](#changes) says.

It builds on these Nostr specifications ("NIPs"):

- [NIP-01](https://github.com/nostr-protocol/nips/blob/master/01.md): events, their id and signature;
- [NIP-35](https://github.com/nostr-protocol/nips/blob/master/35.md): torrents (kind 2003), which this is;
- [NIP-09](https://github.com/nostr-protocol/nips/blob/master/09.md): deletion requests, to withdraw a publication;
- [NIP-32](https://github.com/nostr-protocol/nips/blob/master/32.md): labels, for the language;
- [NIP-36](https://github.com/nostr-protocol/nips/blob/master/36.md): content warnings, for NSFW cards;
- [NIP-31](https://github.com/nostr-protocol/nips/blob/master/31.md): `alt`, a summary for apps that don't know the kind.

The words MUST, SHOULD and MAY have their usual meaning (RFC 2119).


## In short

- A **publication** is one Nostr event of kind 2003 (a NIP-35 torrent), signed by its publisher. It
  describes one version of a card, and its file.
- A **card** is identified by its publisher's public key and a UUID. Another publication with the same
  key and UUID is a new **version** of it. The same UUID under another key is another card.
- The **file** (a PNG, JSON or CharX card) is identified by its SHA-256, and shared by BitTorrent: the
  event gives its infohash. Nothing of the file is on Nostr.
- A publisher **withdraws** a publication with a NIP-09 deletion request: nobody can download it any
  more, but those who have it keep it.
- Apps **find** the publications on relays by the tag `t` = `ai-roleplay-character-card`.


## An example

The first version of a card, as the app would publish it (from [the test vectors](#test-vectors):
its key is a public test key):

```json
{
  "id": "41168a4ca9e73ca12bd41553ce96a97c2afe1e8755fccea1bcc37ccb45c75cd5",
  "pubkey": "d26a1ef2eb3e3e72c32641593d2d0d335c008a09ddded309b481d1ff0a3f42f6",
  "created_at": 1767225600,
  "kind": 2003,
  "tags": [
    ["t", "ai-roleplay-character-card"],
    ["schema", "1"],
    ["alt", "Character card: Reika Shichijo"],
    ["title", "Reika Shichijo"],
    ["uuid", "550e8400-e29b-41d4-a716-446655440000"],
    ["publisher", "Marie"],
    ["spec", "chara_card_v2"],
    ["rating", "sfw"],
    ["sha256", "3efa2138076c56aafa32042fb5b36c789f956645e643783ebe66a5a3b0b2e0e6"],
    ["x", "ce8146057fbe15141ebc751c78d2e972568c6bac"],
    ["file", "3efa2138076c56aafa32042fb5b36c789f956645e643783ebe66a5a3b0b2e0e6.png", "1326"],
    ["t", "anime"],
    ["t", "university"],
    ["t", "roleplay"],
    ["L", "ISO-639-1"],
    ["l", "en", "ISO-639-1"],
    ["tracker", "udp://tracker.opentrackr.org:1337/announce"]
  ],
  "content": "A university student and martial artist.\n\nReika trains every morning before class. Calm, precise, and a little too competitive: « Encore une fois ? » 🥋",
  "sig": "68b1815819e88582bee9ff404f2c9cc250a400ecf841246de0c84b24c20fb0f327d8632ddaeb88a1938ccb229a4217a341698e6a3671a23d25cd521adcc75b95"
}
```

Its magnet link, for any BitTorrent program:
`magnet:?xt=urn:btih:ce8146057fbe15141ebc751c78d2e972568c6bac&dn=3efa2138076c56aafa32042fb5b36c789f956645e643783ebe66a5a3b0b2e0e6.png&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce`


## The event

`kind` is **2003**. `created_at` is when it was published (Unix seconds). `pubkey` is the
publisher: the only publisher identity there is (nothing in the tags repeats it).

`content` is the card's **description**, as NIP-35 has it: plain text, possibly empty, at most
16 KiB in UTF-8. Apps show it as text, never as HTML or Markdown that loads anything.

### Tags

Required, exactly once each (except the first, which may appear among the card's tags too):

| Tag | Value | Notes |
|---|---|---|
| `["t", "ai-roleplay-character-card"]` | | What makes it a card, for relays and apps (see [Finding them](#finding-them)). |
| `["schema", "1"]` | this format's version | Apps skip events of a schema they don't know. |
| `["title", <name>]` | the card's name, 1–200 characters, not [empty](#text) | NIP-35's title. |
| `["uuid", <uuid>]` | a UUID, lowercase, `8-4-4-4-12`: 32 hex digits in all | With `pubkey`, the card's identity. Any version; publishers SHOULD make version 4 ones (random). |
| `["publisher", <name>]` | the publisher's name, 1–100 characters, not empty | As chosen when publishing: the *published name*. |
| `["spec", <spec>]` | `chara_card_v1`, `chara_card_v2`, `chara_card_v3`, or a later one: `[a-z0-9_]{1,32}` | The card's format: the card specifications' own `spec` values (V1 cards have none: `chara_card_v1`). |
| `["rating", <rating>]` | `sfw` or `nsfw` | Stated by the publisher. |
| `["sha256", <hash>]` | 64 lowercase hex digits | The file's SHA-256: the file's identity. |
| `["x", <infohash>]` | 40 lowercase hex digits | The BitTorrent v1 infohash (NIP-35). |
| `["file", <name>, <size>]` | `<sha256>.<extension>`, and the size in bytes | NIP-35's file tag. The extension is `png`, `json` or `charx`. The size is a decimal number, without leading zeros, from 1 to 104,857,600 (100 MiB): see [The file](#the-file-and-its-torrent). |

Optional:

| Tag | Value | Notes |
|---|---|---|
| `["content-warning", <reason>]` | `nsfw`, or any reason, or empty | NIP-36. Publishers MUST add it to an `nsfw` card, for the other apps that show NIP-35 torrents to hide it. An event with it is treated as `nsfw`, whatever its `rating`. |
| `["t", <tag>]` | the card's tags, up to 20 | [Normalized](#card-tags): lowercase, `-` for spaces. |
| `["L", "ISO-639-1"]` and `["l", <code>, "ISO-639-1"]` | the card's language: a two-letter ISO 639-1 code (`en`, `fr`) | NIP-32. One language at most. |
| `["tracker", <url>]` | a `udp://`, `http://` or `https://` announce URL, up to 5 | NIP-35. Helps BitTorrent find the peers sooner than by DHT alone. |
| `["alt", <text>]` | `Character card: <title>` | NIP-31: what apps that don't know kind 2003 show. Publishers SHOULD add it. |

Any other tag is **ignored**: later versions of this format may add some (see [Changes](#changes)).
The order of the tags doesn't matter, but it is part of what's signed: publishers write them in the
order of the example, with `content-warning` right after `rating`.

What the app calls each of them (`Publication`, in rpapp/publishing/announcements.py):

| Metadata | In the event |
|---|---|
| `publisher_id` | `pubkey` (not repeated in a tag) |
| `publisher_name` (when publishing): `published_name` | `publisher` |
| `card_uuid` | `uuid` |
| `card_sha256` | `sha256`, and the name in `file` |
| `torrent_infohash` | `x` |
| `card_size` | the size in `file` |
| `card_name` | `title` |
| `card_description` | `content` |
| `card_tags` | `t` (besides `ai-roleplay-character-card`) |
| `card_format` | `spec` (`v2` → `chara_card_v2`) |
| `content_rating` | `rating`, and `content-warning` |
| `language` | `l` (a BCP-47 tag's language: `en-US` → `en`) |
| `schema_version` | `schema` |
| `thumbnail_url` | none in this version (see [Not in this version](#not-in-this-version)) |

### Text

- Every string (the content and each tag's name and values) is UTF-8, of valid Unicode: no lone
  surrogate, which JSON can write (`\ud800`). Publishers write names, titles, tags and descriptions in
  Unicode NFC, with `\n` for line breaks (not `\r\n`).
- No string may contain a control character (U+0000–U+001F) other than tab, line feed and carriage
  return. NIP-01 wants most of them written as they are when computing the id, but JSON libraries
  write them as `\u00XX`: the apps would disagree on the event's id (rust-nostr does, with
  [the test vector](#test-vectors) that has one). Backspace and form feed, which NIP-01 does escape,
  are refused too: no card needs them.
- Lengths are in characters (Unicode code points), except the description's and the trackers' (in
  bytes).
- *Spaces*, here, are Unicode's White_Space characters (the space, the no-break space, the ideographic
  space...). A name or a title of nothing but spaces is *empty*.

### Card tags

Publishers write the card's tags normalized; apps receiving them normalize them again, and drop the
ones that are then empty, too long or repeated, without rejecting the event:

1. Unicode NFC, then lowercase;
2. spaces at the ends removed, then a leading `#` and the spaces after it; each run of spaces inside
   replaced with one `-` (`Slice of Life` → `slice-of-life`, `#Anime` → `anime`, `# Anime` → `anime`);
3. kept if 1–32 characters long, not `ai-roleplay-character-card`, and not already there; at most 20.


## Cards, versions and names

- A card is identified by **(pubkey, uuid)**. A publication with the same UUID under another key is
  another card: never an update of the first, whatever its title or published name.
- Each publication is a **version**. Any change to what's signed (the file, the title, the
  description, the tags, the rating, the published name...) makes a new publication, with the same
  UUID. Publishing the same thing again uses the same event: it isn't signed anew.
- The **latest version** of a card is its publication with the highest `created_at`; for equal times,
  the one with the smallest `id` (as NIP-01 does for replaceable events). Withdrawn ones don't count.
  The older versions stay valid: a new version replaces nothing.
- The publisher's **current name** is the `publisher` of their latest publication, any card,
  chosen the same way. Apps show it, and the published name of each version where it differs.
- Names are not unique: apps show something of the key next to them (a short fingerprint).
- These are regular events (kind 2003, not a replaceable kind): relays keep every version.


## The file and its torrent

The file is the card as a file: a PNG (a picture with the card inside), a JSON card, or a CharX
archive. Its SHA-256 identifies it, whoever publishes it.

It is **100 MiB at most** (104,857,600 bytes): enough for a card with many pictures (a CharX card's
expressions, say), and a bound on what downloading a card can take of a computer's disk. Publishers
MUST NOT publish a bigger file, and apps reject an event announcing one (`file_too_big`). Apps SHOULD
read any card within that limit, and what it unpacks to up to 100 MiB too (a CharX's members, a PNG's
compressed text; beyond, it's a decompression bomb more than a card): this app does.

Its torrent is made **the same way every time**, so that the same file always has the same infohash:
whoever publishes it, and however often. Publishers of the same file then share one swarm. A torrent's
info dictionary is exactly, bencoded:

| Key | Value |
|---|---|
| `length` | the file's size |
| `name` | `<sha256>.<extension>`, as in the `file` tag |
| `piece length` | 65536 |
| `pieces` | the SHA-1 of each 64 KiB piece, end to end |

A version 1 torrent with one file, and nothing else in its info dictionary (no `private`, no BitTorrent
v2 fields). The infohash is the SHA-1 of that bencoded dictionary. libtorrent makes exactly this with
`create_torrent.v1_only` and 64 KiB pieces (checked: [Test vectors](#test-vectors)). Trackers, creation
date and comment are outside the info dictionary, so they don't change the infohash; the app gives the
trackers in the event, not in the torrent.

Downloading, the app gets the torrent's info dictionary from peers by its infohash (BEP 9), then,
before downloading the file itself, rejects it unless:

- it's one file (no `files` key), named exactly as the `file` tag says;
- its `length` is the `file` tag's size (so 100 MiB at most).

Once downloaded, the file's SHA-256 MUST be the `sha256` tag's, and its torrent, made as above, the `x`
tag's infohash; otherwise it's deleted. The file is then read like any card file the user would import,
with the same limits, and one more: its picture (a PNG card's own, or a JSON or CharX card's main one)
is 50 megapixels at most, as its header says (width × height), before anything is decoded. A small file
can hold a huge picture (a plain color compresses to almost nothing), and an app decodes it whole. A card
beyond these limits isn't added, and its file is deleted; publishing apps SHOULD refuse to publish one
(this app does, saying why). Apps that use a card's other pictures too (a CharX card's expressions, say)
hold each of them to the same 50 megapixels.

Those who downloaded a file may share it in turn (the app does, unless its user says not to), until it's
withdrawn: that's how cards stay available while their publisher is away. Everyone in a swarm sees the
others' internet addresses.


## Withdrawing a publication

A publisher withdraws publications with a NIP-09 deletion request: a kind 5 event, signed by the same
key, with one `e` tag per withdrawn publication.

```json
{
  "kind": 5,
  "pubkey": "d26a1ef2eb3e3e72c32641593d2d0d335c008a09ddded309b481d1ff0a3f42f6",
  "created_at": 1779278400,
  "tags": [
    ["e", "41168a4ca9e73ca12bd41553ce96a97c2afe1e8755fccea1bcc37ccb45c75cd5"],
    ["k", "2003"],
    ["t", "ai-roleplay-character-card"]
  ],
  "content": "Published by mistake.",
  "id": "bc4f6c91164acf39ce64ea2e028b21ca8bdaea2d971780d7ecbd0da3b42f1885",
  "sig": "6868debffadc46a5fb9716b7ea25bc25a82dfc6f4c4263328d2d3a874d8861c52c4cdf96026a6879ef0935318c15159f0c3f5c7e9c39c2f48e37df1566d14eae"
}
```

- It withdraws only publications with the **same pubkey**: an `e` tag naming someone else's is ignored.
- `k` (NIP-09) and `t` are what apps find it by. `content` is the reason, optional: plain text, of which
  apps keep the first 1,000 characters.
- One version at a time: to withdraw a whole card, one `e` tag per version (98 at most in one
  withdrawal, with its `k` and `t`, as an event has 100 tags at most: more take several). When the
  latest version is withdrawn, the one before is the latest again.
- A withdrawal is final: publishing the same card unchanged afterwards makes a new publication (a new
  `created_at`, so a new id), not the withdrawn one again.

What a withdrawal does:

- **No new downloads**: apps no longer offer a withdrawn publication to download, and stop seeding its
  file, unless it's also the file of a publication they still share (another version, or another
  publisher's card, with the same file).
- **Those who have it keep it**: a copy already downloaded stays, with the conversations made with it.
  The app tells its user that it was withdrawn, when, and why if the publisher said.
- Apps keep the withdrawal (date and reason included). Kept, it also stops a relay that still has the
  publication from bringing it back; and a withdrawal received before its publication applies when the
  publication comes.
- Most relays delete the withdrawn events; not all.


## Finding them

Relays only search the tags with one-letter names: apps find publications by
`t` = `ai-roleplay-character-card`, then check everything else themselves.

| To get | Filter |
|---|---|
| New publications | `{"kinds": [2003], "#t": ["ai-roleplay-character-card"], "since": <checkpoint − 24 h>, "limit": 500}` |
| Older ones | the same, with `"until": <the oldest created_at received>`, page after page, until a page brings nothing new |
| Withdrawals | `{"kinds": [5], "#k": ["2003"], "since": <checkpoint − 24 h>, "limit": 500}`, and older ones the same way |
| One publisher's | `{"kinds": [2003], "authors": [<pubkey>], "#t": ["ai-roleplay-character-card"]}` and `{"kinds": [5], "authors": [<pubkey>], "#k": ["2003"]}` (after unblocking them, or for their page) |
| One publication | `{"ids": [<id>]}` |

- The checkpoint is per relay, and per filter: when its last complete discovery started (the first
  discovery, and a full one, have none: everything). Going back 24 hours each time catches events that
  reached the relay late, or were dated by a slow clock; duplicates are recognized by their id.
- `until` includes its second (NIP-01): the page's oldest events come again, recognized by their id.
  Some relays leave it out (`created_at < until`): apps notice, as the next page lacks the events of
  that second it should have, and ask up to the second after instead.
- A second holding more events than a page can't be gone through (NIP-01 has no other way than dates):
  apps take a page of it, and go on from the second before.
- So much at once is refused: apps take so many pages per discovery (Reality Later: 20 per relay and
  filter), and leave the rest for the next ones.
- A filter's values are alternatives: `"#t": ["ai-roleplay-character-card", "fantasy"]` means either.
  Filtering on the card's tags or language is done by the app, in its catalog.
- Publishing, the app sends the event to each relay it writes to; a relay answers `OK` true when it
  has it. One that had it already says `duplicate:`, with true (NIP-01) or, for some, false: it has it
  either way.

What relays commonly allow (strfry's defaults): events up to 64 KiB, tag values up to 1 KiB, 500
events per answer; events dated more than 15 minutes ahead are refused, and so are events older than
3 years: a publication can't be sent to a relay added more than 3 years later.


## Checking a publication

An app receiving an event checks, in this order, and rejects it with the first problem found (the codes
are those of the test vectors):

1. `malformed`: it isn't an object with `id` (64 lowercase hex digits), `pubkey` (64), `created_at`
   (an integer, 0 or more), `kind` (an integer), `tags` (a list of non-empty lists of strings),
   `content` (a string) and `sig` (128); or a string isn't valid Unicode (see [Text](#text)).
2. `too_large`: more than 48 KiB (49,152 bytes) as compact JSON in UTF-8, or more than 100 tags.
3. `wrong_kind`: its kind isn't 2003.
4. `not_a_card`: no `["t", "ai-roleplay-character-card"]`.
5. `missing_tag:schema`, `duplicate_tag:schema`, `unsupported_schema`: its schema isn't `1`.
6. `control_character`: see [Text](#text).
7. `id_mismatch`: its id isn't the SHA-256 of its serialization (NIP-01).
8. `bad_signature`: its signature isn't a valid BIP-340 signature of its id by its pubkey.
9. `from_the_future`: dated more than 15 minutes after the app's clock.
10. `blocked`: its publisher is blocked.
11. `missing_tag:<name>`, `duplicate_tag:<name>`: one of `title`, `uuid`, `publisher`, `spec`,
    `rating`, `sha256`, `x` and `file` is missing, or there twice.
12. Their values, in that order: `empty:title`, `too_long:title`, `bad_uuid`, `empty:publisher`,
    `too_long:publisher`, `bad_spec`, `bad_rating`, `bad_sha256`, `bad_infohash`, `bad_file`,
    `file_too_big`, then `too_long:description`.

Then, without rejecting it: the card's tags are normalized; the trackers are taken without the spaces
at their ends, and those that aren't `udp://`, `http://` or `https://` followed by a host, without
spaces, of 256 bytes at most, are dropped, then the repeated ones and those past the fifth; a language
that isn't two lowercase letters with the `ISO-639-1` label is ignored (the first that is counts); and
a `content-warning` makes it `nsfw`.

A withdrawal is checked by steps 1 to 3 (its kind being 5) and 6 to 10, and needs at least one `e`
tag of 64 lowercase hex digits (`nothing_withdrawn`). Its other `e` tags are ignored.

Receiving the same event again (from another relay, or another time) changes nothing.


## Limits

| | |
|---|---|
| Event, as compact JSON | 48 KiB |
| Tags | 100 |
| Title | 1–200 characters |
| Published name | 1–100 characters |
| Description | 16 KiB (UTF-8) |
| Card tags | 20, of 1–32 characters |
| Trackers | 5, of 256 bytes at most |
| File | 1 byte to 100 MiB (104,857,600 bytes) |
| Each of its pictures | 50 megapixels (width × height) |
| Date | at most 15 minutes ahead of the app's clock |

All of them fit relays' common limits, with room to spare: the example is about 1 KiB.


## Changes

- Apps ignore the tags they don't know: a later version can add optional tags (a thumbnail, other
  places to get the file from) with schema 1 still, and older apps keep reading the events.
- `schema` changes only for a change that older apps would misread. They then skip those events;
  once updated, a full discovery brings them.
- Other apps that show NIP-35 torrents ([dtan.xyz](https://dtan.xyz), noStrudel) show these too:
  title, description, file, tags. Those that follow NIP-36 hide the ones with a `content-warning`.
- A card published by another program, in this format, is a publication like any other.


## Not in this version

- Thumbnails: a URL would make every app that shows it ask a server the publisher chose, telling it
  who's looking. If they come, they'll be referred to by hash, and loaded only when asked.
- Other places to get the file from than BitTorrent (Blossom servers, which also identify files by
  SHA-256, could be one).
- Comments (NIP-35's kind 2004).


## Test vectors

[`tests/card_announcements.json`](../tests/card_announcements.json) has:

- the example's card file (in base64), its SHA-256, its torrent's bencoded info dictionary and infohash;
- 8 valid publications, with what an app stores of each (`expected`), among them the example, a new
  version of it, the same UUID and file under another key, and the edge cases (a version 7 UUID,
  unknown tags, a content warning on an `sfw` card, tags to normalize, a date 15 minutes ahead);
- 2 withdrawals: the example's, and another publisher's naming it, which withdraws nothing; with what
  each names (`names`), and what it withdraws of the valid publications (`withdraws`);
- 33 invalid publications and 3 invalid withdrawals, each with one problem, and the code it's rejected
  with: every code above but `blocked`, which depends on the app's user;
- `now`, the time to check them against, and the file limit (`size_limit`).

Its two keys are **public test keys**: never publish with them. Its signatures were made with no
auxiliary randomness, so they can be made again identically; real signatures are random: apps check
them, and never compare them.

They were made with Python's `hashlib`, a bencoder written for them, and coincurve; and checked with
other implementations: libtorrent 2.1.1 makes the same info dictionary and infohash, and rust-nostr
(nostr-sdk 0.45.1) finds the same ids and accepts the signatures.
