"""The publisher's key: one per installation, kept in a file of its own, readable by you only.

Its public half identifies you as a publisher, for good: every version of your cards is signed with it,
and only it can publish new ones, or withdraw them. Lost, it can't be recovered: a new key is a new
publisher. Hence its backup (Identity.nsec(): an "nsec1..." text to keep somewhere safe), and
import_identity() to bring it back, or onto another computer.

The app keeps it with the HTTPS certificate's key (key_path()): in the settings folder's "certificates"
folder, which is already kept out of git, of the Flatpak's build, and of the zips. The file holds the key
written as nsec, on one line.

Publishing needs it; discovering and downloading don't.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import nostr

KEY_FILE_NAME = 'nostr-key.txt'


class IdentityError(Exception):
    """The key can't be read, made or replaced: the message says why, in plain words."""


@dataclass(frozen=True, slots=True)
class Identity:
    """A publisher's key pair. Its secret half isn't shown in repr(), or anywhere it's printed."""
    secret: bytes = field(repr=False)
    public_key: str = ''   # 64 hex digits: what's in the events

    @classmethod
    def from_secret(cls, secret: bytes) -> Identity:
        if not nostr.is_valid_secret(secret):
            raise IdentityError('This isn\'t a valid key.')
        return cls(secret=secret, public_key=nostr.public_key(secret))

    @property
    def npub(self) -> str:
        """The public key as people share it: npub1..."""
        return nostr.to_npub(self.public_key)

    def nsec(self) -> str:
        """The secret key, for its backup: nsec1... (to show only when asked, and never to log)."""
        return nostr.to_nsec(self.secret)


def key_path(config_dir: str | Path) -> Path:
    """Where the app keeps it: <settings folder>/certificates/nostr-key.txt (see the module's notes)."""
    return Path(config_dir) / 'certificates' / KEY_FILE_NAME


def load_identity(path: str | Path) -> Identity | None:
    """The key in that file; None if there's none yet."""
    path = Path(path)
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    except OSError as e:
        raise IdentityError(f'Your publisher key can\'t be read ({path}: {e.strerror or e}).') from None
    try:
        return Identity.from_secret(parse_secret(text))
    except IdentityError:
        raise IdentityError(f'The file of your publisher key ({path}) doesn\'t hold a valid key: restore it from '
                            f'your backup.') from None


def create_identity(path: str | Path) -> Identity:
    """A new key, saved. Never over an existing one (IdentityError): replace_identity() does that."""
    path = Path(path)
    if path.exists():
        raise IdentityError('You already have a publisher key.')
    identity = Identity.from_secret(nostr.new_secret())
    _save(path, identity)
    return identity


def import_identity(path: str | Path, text: str, *, replace: bool = False) -> Identity:
    """A key from its backup (nsec1..., or its 64 hex digits), saved. If there's one already: an error, or
    with `replace`, the new one replaces it, the old one kept beside it (nostr-key.replaced-<date>.txt)."""
    path = Path(path)
    identity = Identity.from_secret(parse_secret(text))
    current = load_identity(path) if path.exists() else None
    if current is not None and current.public_key == identity.public_key:
        return current  # (the same key: nothing to do)
    if path.exists():
        if not replace:
            raise IdentityError('You already have a publisher key: replacing it changes who you are as a publisher.')
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        os.replace(path, path.with_name(f'{path.stem}.replaced-{stamp}{path.suffix}'))
    _save(path, identity)
    return identity


def parse_secret(text: str) -> bytes:
    """A secret key written as nsec1... or as 64 hex digits."""
    text = text.strip()
    try:
        if text.lower().startswith('nsec1'):
            secret = nostr.from_bech32(text, 'nsec')
        elif len(text) == 64:
            secret = bytes.fromhex(text)
        elif text.lower().startswith('npub1'):
            raise IdentityError('That\'s a public key (npub): the backup of your key starts with nsec1.')
        else:
            raise IdentityError('A key is written nsec1..., or as 64 hexadecimal digits.')
    except ValueError as e:
        raise IdentityError(f'This isn\'t a valid key ({e}).') from None
    if not nostr.is_valid_secret(secret):
        raise IdentityError('This isn\'t a valid key.')
    return secret


def _save(path: Path, identity: Identity) -> None:
    """Written atomically, readable by you only from the start (on Windows, the folder's own permissions:
    your account's, in your profile)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix='.tmp')
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(identity.nsec() + '\n')
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
