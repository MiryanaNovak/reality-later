"""The server's configuration: config.toml, next to the program.

    [server]
    port = 8090
    address = "0.0.0.0"
    allowed_networks = ["private"]
    pairing = true
    https = true
    certificate = ""
    key = ""
    local_port = 8091        (by default, port + 1)
    open_browser = true
    tray = true

The file is looked for next to main.py when the app runs from its source folder, and in your user
folder when it's installed (see paths.py). Without the file, or for anything
it doesn't set, the defaults above apply. A mistake in it (a typo in a name, a
port out of range...) stops the app with a message saying what and where,
rather than starting it with settings you didn't mean.

See config.example.toml for what each setting means.

`allowed_networks` is enforced by NetworkFilter, which turns away connections
from other addresses before they reach the app (pages, files and the live
connection alike). This computer itself is always let in.

With `https = true`, the app is served over HTTPS: with the certificate and key
files given (relative to this file's folder), or else with a self-signed
certificate it makes on first start (see certificates.py). https_setup() works
out which.

This computer gets its own address on top: http://localhost:<local_port>, plain
HTTP, which only it can reach (see launcher.py). Browsers count localhost as
secure, so there's no certificate warning on the computer, and it needs no
pairing. `local_url` is how this computer opens the app.
"""
from __future__ import annotations

import ipaddress
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .certificates import Certificate, ensure_self_signed
from .paths import EXAMPLE_NAME, places  # noqa: F401  (EXAMPLE_NAME: kept importable from here)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

log = logging.getLogger(__name__)

FILE_NAME = 'config.toml'

# Listening on these, the app is also reachable from this computer as localhost.
COVERS_LOCALHOST = {'0.0.0.0', '::', '127.0.0.1', '::1', 'localhost'}

Network = ipaddress.IPv4Network | ipaddress.IPv6Network

# Shorthands usable in allowed_networks
LOCAL = ['127.0.0.0/8', '::1/128']
NAMED_NETWORKS: dict[str, list[str]] = {
    'local': LOCAL,
    # This computer, and home or office networks: private IPv4 ranges, IPv6 unique-local and link-local.
    'private': [*LOCAL, '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16',
                'fc00::/7', 'fe80::/10'],
    'any': ['0.0.0.0/0', '::/0'],
}


class ConfigError(Exception):
    """The config file can't be used: the message says why, and where."""


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)  # a misspelled setting is an error, not ignored

    port: int = Field(8090, ge=1, le=65535)
    address: str = '0.0.0.0'
    allowed_networks: list[str] = Field(default_factory=lambda: ['private'], min_length=1)
    pairing: bool = True   # devices other than this computer must be paired once (see access.py)
    https: bool = True
    certificate: str = ''  # your own certificate and key files (e.g. from mkcert); '' = make a self-signed one
    key: str = ''
    local_port: int | None = Field(None, ge=0, le=65535)  # this computer's plain-HTTP address; None: port + 1, 0: none
    open_browser: bool = True  # open the app's "This computer" page in the browser at startup (on a desktop)
    tray: bool = True  # an icon in the system tray, to open the app or quit it (on a desktop, see tray.py)

    @field_validator('address')
    @classmethod
    def _address(cls, value: str) -> str:
        value = value.strip()
        if value != 'localhost':
            try:
                ipaddress.ip_address(value)
            except ValueError:
                raise ValueError(f'{value!r} is not an IP address (like "0.0.0.0" or "192.168.1.20")') from None
        return value

    @field_validator('allowed_networks')
    @classmethod
    def _networks(cls, values: list[str]) -> list[str]:
        for value in values:
            parse_networks([value])  # raises ValueError with a readable message
        return values

    @model_validator(mode='after')
    def _ports(self) -> ServerConfig:
        if self.local_port and self.local_port == self.port:
            raise ValueError(f'local_port and port must differ (both are {self.port})')
        return self

    def networks(self) -> list[Network]:
        return parse_networks(self.allowed_networks)

    @property
    def covers_localhost(self) -> bool:
        """Whether this computer can reach the main address as localhost."""
        return self.address in COVERS_LOCALHOST

    @property
    def local_http_port(self) -> int | None:
        """The port of the extra plain-HTTP address only this computer reaches, when the app needs one.

        It does with HTTPS on (so that this computer's browser shows no warning), and when it listens on one
        network address only (so that this computer reaches it at all). Not with `local_port = 0`.
        """
        if self.local_port == 0 or (not self.https and self.covers_localhost):
            return None
        if self.local_port is None:
            return self.port + 1 if self.port < 65535 else self.port - 1
        return self.local_port

    @property
    def local_url(self) -> str | None:
        """How this computer opens the app (None: it can't, as with address "192.168.1.20" and local_port = 0)."""
        if (port := self.local_http_port) is not None:
            return f'http://localhost:{port}'
        if self.covers_localhost:
            return f'{"https" if self.https else "http"}://localhost:{self.port}'
        return None

    @property
    def allows_everyone(self) -> bool:
        return any(n.prefixlen == 0 for n in self.networks())


class Config(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    server: ServerConfig = Field(default_factory=ServerConfig)
    source: Path | None = Field(None, exclude=True)  # the file it was read from (None: defaults)
    folder: Path = Field(default_factory=Path.cwd, exclude=True)  # where it's looked for; relative paths start here

    @property
    def path(self) -> Path:
        """Where config.toml is, or would be."""
        return self.source or self.folder / FILE_NAME


def load_config(folder: str | Path | None = None) -> Config:
    """Read config.toml from `folder` (by default, where paths.places() says); defaults if there's no file.

    Raises ConfigError if the file exists but can't be used.
    """
    folder = Path(folder if folder is not None else places().config)
    path = folder / FILE_NAME
    if not path.is_file():
        return Config(folder=folder)
    try:
        with path.open('rb') as f:
            data: dict[str, Any] = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f'{path}: {e}') from None
    except OSError as e:
        raise ConfigError(f'{path} can\'t be read: {e}') from None
    try:
        return Config.model_validate({**data, 'source': path, 'folder': folder})
    except ValidationError as e:
        problems = []
        for error in e.errors():
            where = '.'.join(str(part) for part in error['loc'])
            message = error['msg'].removeprefix('Value error, ')
            if error['type'] == 'extra_forbidden':
                message = 'unknown setting (misspelled?)'
            problems.append(f'  {where}: {message}')
        raise ConfigError(f'{path} has {"a mistake" if len(problems) == 1 else "mistakes"}:\n' + '\n'.join(problems)) \
            from None


@dataclass
class HttpsSetup:
    cert: Path
    key: Path
    self_signed: Certificate | None = None  # set when the app's own certificate is used


def https_setup(config: Config) -> HttpsSetup | None:
    """The certificate and key to serve HTTPS with, or None for plain HTTP.

    Without files of your own, the self-signed certificate is made (or renewed) here.
    Raises ConfigError if the files given are missing, or the certificate can't be written.
    """
    server = config.server
    if not server.https:
        return None
    where = config.source or config.folder / FILE_NAME
    if server.certificate or server.key:
        if not (server.certificate and server.key):
            raise ConfigError(f'{where}: give both server.certificate and server.key, or neither '
                              '(to use a self-signed certificate).')
        cert, key = (config.folder / Path(value).expanduser() for value in (server.certificate, server.key))
        for setting, path in (('certificate', cert), ('key', key)):
            if not path.is_file():
                raise ConfigError(f'{where}: server.{setting} is "{path}", which doesn\'t exist.')
        return HttpsSetup(cert, key)
    try:
        made = ensure_self_signed(config.folder / 'certificates', addresses=[server.address])
    except OSError as e:
        raise ConfigError(f'The self-signed certificate can\'t be written in {config.folder / "certificates"}: {e}') \
            from None
    return HttpsSetup(made.cert, made.key, self_signed=made)


def parse_networks(values: list[str]) -> list[Network]:
    """'private', 'local', 'any', '192.168.1.0/24' or '192.168.1.42' (one address) -> networks."""
    networks: list[Network] = []
    for value in values:
        value = value.strip()
        if value.lower() in NAMED_NETWORKS:
            networks += [ipaddress.ip_network(n) for n in NAMED_NETWORKS[value.lower()]]
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            raise ValueError(f'{value!r} is not a network (like "192.168.1.0/24"), an address, '
                             f'or one of {", ".join(NAMED_NETWORKS)}') from None
    return networks


def is_allowed(host: str | None, networks: list[Network]) -> bool:
    """Whether a client address is in one of the networks (an unknown address isn't)."""
    try:
        ip = ipaddress.ip_address((host or '').split('%')[0])  # drop an IPv6 zone like %eth0
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:  # ::ffff:192.168.1.5 on dual-stack sockets
        ip = ip.ipv4_mapped
    return any(ip in n for n in networks if n.version == ip.version)


LOOPBACK = parse_networks(['local'])  # this computer itself


class NetworkFilter:
    """ASGI middleware turning away clients outside the allowed networks.

    Works for plain requests and websockets (NiceGUI's live connection), so nothing gets through.
    This computer itself (127.0.0.1, ::1) always gets in, whatever the list: it holds the app's files anyway.
    Behind a reverse proxy, the client is the proxy: allow its address.
    """

    def __init__(self, app: Any, networks: list[Network]) -> None:
        self.app = app
        self.networks = networks
        self._reported: set[str] = set()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope['type'] in ('http', 'websocket'):
            client = scope.get('client')
            host = client[0] if client else None
            if not is_allowed(host, self.networks) and not is_allowed(host, LOOPBACK):
                if host not in self._reported:  # once per address, not for every file it asks for
                    self._reported.add(host)
                    log.warning('Turned away %s: not in allowed_networks (%s)', host, FILE_NAME)
                if scope['type'] == 'http':
                    await send({'type': 'http.response.start', 'status': 403,
                                'headers': [(b'content-type', b'text/plain; charset=utf-8')]})
                    await send({'type': 'http.response.body',
                                'body': f'Forbidden: your address ({host}) isn\'t allowed. See allowed_networks '
                                        f'in {FILE_NAME}.\n'.encode()})
                else:
                    await send({'type': 'websocket.close', 'code': 1008})
                return
        await self.app(scope, receive, send)
