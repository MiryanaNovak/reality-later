"""Paired devices: which browsers may use the app, besides the computer it runs on.

A device is paired in one of two ways, both needing the computer itself:

- a pairing link, usually shown as a QR code on the computer: it works once, for
  PAIRING_MINUTES, and signs in the browser that opens it;
- a request: an unpaired browser shows a short code (like 7KQ-2MX) and waits; you
  allow it on the computer (Settings › Devices, or `python main.py approve`).

A paired browser holds a long random token in a cookie. Only a hash of it is
kept, in data/devices.json, with the device's name, when it was paired and last
seen. Removing a device there signs it out.

Links and requests live in memory only: restarting the app cancels them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

PAIRING_MINUTES = 10
REQUEST_MINUTES = 10
MAX_PENDING = 20             # requests waiting at once, in all
MAX_PENDING_PER_ADDRESS = 3
SEEN_EVERY = timedelta(minutes=10)  # how often "last seen" is written to the file at most
CODE_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'  # no 0/O, 1/I: easy to read out and compare


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Device(BaseModel):
    id: str
    name: str
    token_hash: str
    paired_at: datetime = Field(default_factory=_now)
    last_seen: datetime = Field(default_factory=_now)
    address: str = ''        # the address it was last seen from
    user_agent: str = ''
    how: Literal['link', 'request'] = 'link'


@dataclass
class PairingLink:
    code: str
    expires: datetime
    used_by: str = ''        # the device's name, once used


@dataclass
class JoinRequest:
    id: str                  # secret, kept in the waiting browser's cookie
    code: str                # shown on both sides, to compare: '7KQ-2MX'
    address: str
    user_agent: str
    created: datetime = field(default_factory=_now)
    status: Literal['waiting', 'approved', 'denied', 'done'] = 'waiting'

    @property
    def name(self) -> str:
        return describe_agent(self.user_agent)

    def expired(self, now: datetime) -> bool:
        return self.status in ('waiting', 'approved') and now - self.created > timedelta(minutes=REQUEST_MINUTES)


class TooManyRequests(Exception):
    """Too many requests are waiting already (from this address, or in all)."""


class DeviceRegistry:
    def __init__(self, data_dir: str | Path) -> None:
        self.path = Path(data_dir) / 'devices.json'
        self.ever_paired = False  # a device was paired once (even if removed since): set by _load
        self.devices: list[Device] = self._load()
        self.links: dict[str, PairingLink] = {}
        self.requests: dict[str, JoinRequest] = {}
        self.version = 0  # goes up whenever something changes, for pages to refresh

    # -- the file ---------------------------------------------------------------------

    def _load(self) -> list[Device]:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            raise ValueError(f'{self.path} can\'t be read: {e}') from e
        devices = [Device.model_validate(d) for d in data.get('devices', [])]
        self.ever_paired = bool(data.get('ever_paired')) or bool(devices)
        return devices

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps({'devices': [d.model_dump(mode='json') for d in self.devices],
                           'ever_paired': self.ever_paired}, indent=2) + '\n'
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix='.tmp')
        try:
            if hasattr(os, 'fchmod'):
                os.fchmod(fd, 0o600)  # hashes only, but still nobody else's business
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _changed(self, *, save: bool = True) -> None:
        self.version += 1
        if save:
            self._save()

    # -- devices ----------------------------------------------------------------------

    def find(self, token: str | None) -> Device | None:
        if not token:
            return None
        wanted = hash_token(token)
        return next((d for d in self.devices if secrets.compare_digest(d.token_hash, wanted)), None)

    def seen(self, device: Device, address: str) -> None:
        now = _now()
        if now - device.last_seen > SEEN_EVERY or address != device.address:
            device.last_seen, device.address = now, address
            self._changed()

    def rename(self, device_id: str, name: str) -> None:
        device = self.get(device_id)
        if device and name.strip() and name.strip() != device.name:
            device.name = name.strip()[:80]
            self._changed()

    def remove(self, device_id: str) -> Device | None:
        device = self.get(device_id)
        if device:
            self.devices = [d for d in self.devices if d.id != device_id]
            self._changed()
        return device

    def get(self, device_id: str) -> Device | None:
        return next((d for d in self.devices if d.id == device_id), None)

    def _add(self, user_agent: str, address: str, how: str) -> tuple[Device, str]:
        token = secrets.token_urlsafe(32)
        device = Device(id=secrets.token_hex(4), name=describe_agent(user_agent), token_hash=hash_token(token),
                        address=address, user_agent=user_agent[:300], how=how)
        self.devices = [*self.devices, device]
        self.ever_paired = True
        self._changed()
        return device, token

    # -- pairing links ----------------------------------------------------------------

    def new_link(self) -> PairingLink:
        self._prune()
        link = PairingLink(code=secrets.token_urlsafe(16), expires=_now() + timedelta(minutes=PAIRING_MINUTES))
        self.links[link.code] = link
        return link

    def use_link(self, code: str, *, user_agent: str, address: str) -> tuple[Device, str] | None:
        """Pair the browser opening a link: (device, its token), or None if the link is unknown, used or expired."""
        self._prune()
        link = self.links.get(code or '')
        if link is None or link.used_by:
            return None
        device, token = self._add(user_agent, address, 'link')
        link.used_by = device.name
        return device, token

    def link(self, code: str) -> PairingLink | None:
        return self.links.get(code)

    # -- requests ---------------------------------------------------------------------

    def new_request(self, *, address: str, user_agent: str) -> JoinRequest:
        self._prune()
        waiting = [r for r in self.requests.values() if r.status == 'waiting']
        if len(waiting) >= MAX_PENDING or sum(r.address == address for r in waiting) >= MAX_PENDING_PER_ADDRESS:
            raise TooManyRequests()
        codes = {r.code for r in waiting}
        while (code := _code()) in codes:
            pass
        request = JoinRequest(id=secrets.token_urlsafe(24), code=code, address=address, user_agent=user_agent[:300])
        self.requests[request.id] = request
        self._changed(save=False)
        return request

    def request(self, request_id: str | None) -> JoinRequest | None:
        self._prune()
        return self.requests.get(request_id or '')

    def pending(self) -> list[JoinRequest]:
        self._prune()
        return sorted((r for r in self.requests.values() if r.status == 'waiting'), key=lambda r: r.created)

    def answer(self, code: str, *, allow: bool) -> JoinRequest | None:
        """Allow or deny the waiting request with this code (as typed: case and dash don't matter)."""
        wanted = normalize_code(code)
        request = next((r for r in self.pending() if normalize_code(r.code) == wanted), None)
        if request:
            request.status = 'approved' if allow else 'denied'
            self._changed(save=False)
        return request

    def claim(self, request_id: str) -> tuple[Device, str] | None:
        """For the waiting browser, once allowed: pair it (once) and return (device, token)."""
        request = self.request(request_id)
        if request is None or request.status != 'approved':
            return None
        request.status = 'done'
        return self._add(request.user_agent, request.address, 'request')

    def _prune(self) -> None:
        now = _now()
        self.links = {c: link for c, link in self.links.items() if link.expires > now}  # used ones stay, as used
        before = len(self.requests)
        self.requests = {i: r for i, r in self.requests.items()
                         if not r.expired(now) and now - r.created < timedelta(minutes=REQUEST_MINUTES * 2)}
        if len(self.requests) != before:
            self.version += 1


def _code() -> str:
    raw = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(6))
    return f'{raw[:3]}-{raw[3:]}'


def normalize_code(code: str) -> str:
    return re.sub(r'[^0-9A-Z]', '', code.upper())


def describe_agent(user_agent: str) -> str:
    """'Chrome on Android', 'Firefox on Linux', 'Safari on iPhone'... from a browser's User-Agent."""
    ua = user_agent or ''
    browser = next((name for pattern, name in (
        ('SamsungBrowser', 'Samsung Internet'), ('Edg', 'Edge'), ('OPR', 'Opera'), ('Firefox', 'Firefox'),
        ('FxiOS', 'Firefox'), ('CriOS', 'Chrome'), ('Chrome', 'Chrome'), ('Safari', 'Safari'),
    ) if pattern in ua), 'A browser')
    system = next((name for pattern, name in (
        ('Android', 'Android'), ('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Windows', 'Windows'),
        ('CrOS', 'ChromeOS'), ('Macintosh', 'Mac'), ('Linux', 'Linux'),
    ) if pattern in ua), '')
    return f'{browser} on {system}' if system else browser
