"""Who may use the app: the computer it runs on, and devices paired with it (see devices.py).

    the computer itself   a connection from this computer (127.0.0.1 or ::1), addressed to a
                          local name (localhost, 127.0.0.1 or [::1]), not relayed by a proxy:
                          no pairing needed
    a paired device       a browser holding a valid device cookie
    anyone else           can only reach the pairing pages: /pair (the link in the QR code) and
                          /join (ask to be allowed, then wait). Pages send them to /join; everything
                          else (files, the live connection...) is refused.

Why "addressed to a local name" and "not relayed by a proxy": with Tailscale Serve or
any reverse proxy on this computer, visitors from elsewhere also connect from
127.0.0.1. Proxies add headers saying who the real visitor is, and the name in the
address bar isn't localhost then, so those visitors still need pairing. Checking
the name also stops web pages from tricking your browser into reaching the app
under another name (DNS rebinding).

On this computer, open the app as http://localhost:<local_port> (see config.py), not its
network address.

The /local/... endpoints are for `python main.py pair` and `python main.py approve`:
only from the computer itself, and never from a web page (requests carrying an
Origin header, as browsers send for other sites' scripts, are refused).

AccessGuard is the ASGI middleware applying all this; it's added in main.py after
the network filter (config.py), when `pairing = true` in config.toml.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .devices import PAIRING_MINUTES, REQUEST_MINUTES, Device, DeviceRegistry, TooManyRequests, hash_token
from .paths import command

log = logging.getLogger(__name__)

COOKIE = 'rp_device'
JOIN_COOKIE = 'rp_join'
COOKIE_DAYS = 400  # the most browsers keep a cookie
OPEN_PATHS = {'/pair', '/join', '/join/status', '/favicon.ico'}
OPEN_FOLDERS = ('/icons/',)  # the app's icons: so the pairing pages have theirs (they tell nothing)
LOCAL_NAMES = {'localhost', '127.0.0.1', '[::1]'}
PROXY_HEADERS = {'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'forwarded', 'x-real-ip',
                 'tailscale-user-login', 'tailscale-user-name', 'cf-connecting-ip', 'true-client-ip'}


@dataclass
class Who:
    local: bool                  # on the computer itself
    device: Device | None = None

    @property
    def allowed(self) -> bool:
        return self.local or self.device is not None


def is_local(client_host: str | None, headers: Mapping[str, str]) -> bool:
    """A connection from this computer, to a local name, not relayed by a proxy (see the module's notes)."""
    try:
        ip = ipaddress.ip_address((client_host or '').split('%')[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_loopback or any(name in PROXY_HEADERS for name in headers):
        return False
    host = headers.get('host', '').strip().lower()
    name = host[:host.index(']') + 1] if host.startswith('[') and ']' in host else host.rsplit(':', 1)[0]
    return name in LOCAL_NAMES


def cookies_of(headers: Mapping[str, str]) -> dict[str, str]:
    jar = SimpleCookie()
    try:
        jar.load(headers.get('cookie', ''))
    except Exception:  # a malformed cookie header: as if there were none
        return {}
    return {key: morsel.value for key, morsel in jar.items()}


class Access:
    def __init__(self, devices: DeviceRegistry, *, scheme: str = 'http', port: int = 8090, enabled: bool = True,
                 listen: str = '0.0.0.0') -> None:
        self.devices = devices
        self.scheme = scheme
        self.port = port
        self.enabled = enabled
        self.listen = listen  # the address the app listens on (config.toml's address)

    # -- who's there ------------------------------------------------------------------

    def who(self, client_host: str | None, headers: Mapping[str, str]) -> Who:
        if is_local(client_host, headers):
            return Who(local=True)
        return Who(local=False, device=self.devices.find(cookies_of(headers).get(COOKIE)))

    def who_request(self, request: Request | None) -> Who:
        if request is None:
            return Who(local=False)
        headers = {k.lower(): v for k, v in request.headers.items()}
        return self.who(request.client.host if request.client else None, headers)

    # -- pairing links ------------------------------------------------------------------

    def addresses(self) -> list[str]:
        """Addresses other devices can reach the app at, likeliest first (see network_addresses)."""
        return network_addresses(self.listen)

    def link_url(self, code: str, address: str) -> str:
        return f'{url_for(self.scheme, address, self.port)}/pair?code={code}'

    # -- signing a device out everywhere --------------------------------------------------

    def kick(self, device: Device) -> None:
        """Reload the open pages of a removed device: they land on /join."""
        from nicegui import Client
        for client in list(Client.instances.values()):
            request = getattr(client, 'request', None)
            if request is None or client.is_deleted:
                continue
            token = request.cookies.get(COOKIE)
            if token and hash_token(token) == device.token_hash:
                try:
                    client.run_javascript('location.reload()')
                except Exception:
                    pass

    # -- the routes --------------------------------------------------------------------

    def register_routes(self, app: Any) -> None:
        app.add_api_route('/pair', self.pair_page, methods=['GET'], include_in_schema=False)
        app.add_api_route('/join', self.join_page, methods=['GET'], include_in_schema=False)
        app.add_api_route('/join/status', self.join_status, methods=['GET'], include_in_schema=False)
        app.add_api_route('/devices/sign-out', self.sign_out, methods=['GET'], include_in_schema=False)
        app.add_api_route('/local/pair', self.local_pair, methods=['POST'], include_in_schema=False)
        app.add_api_route('/local/requests', self.local_requests, methods=['GET'], include_in_schema=False)
        app.add_api_route('/local/answer', self.local_answer, methods=['POST'], include_in_schema=False)

    def _paired(self, response: Response, token: str) -> Response:
        response.set_cookie(COOKIE, token, max_age=COOKIE_DAYS * 86400, httponly=True, samesite='lax',
                            secure=self.scheme == 'https', path='/')
        return response

    async def pair_page(self, request: Request) -> Response:
        if self.who_request(request).allowed:
            return RedirectResponse('/', status_code=303)  # nothing to do: the link stays unused
        result = self.devices.use_link(request.query_params.get('code', ''), user_agent=_agent(request),
                                       address=_address(request))
        if result is None:
            return _page('This pairing link doesn\'t work any more',
                         '<p>It was already used, or it expired: links work once, for '
                         f'{PAIRING_MINUTES} minutes.</p><p>Show a new one on the computer the app runs on, or '
                         '<a href="/join">ask to be allowed</a> from here.</p>', status=410)
        device, token = result
        log.warning('Paired %s (%s) with a pairing link', device.name, device.address)
        return self._paired(RedirectResponse('/', status_code=303), token)

    async def join_page(self, request: Request) -> Response:
        after = safe_next(request.query_params.get('next'))
        if self.who_request(request).allowed:
            return RedirectResponse(after, status_code=303)
        current = self.devices.request(request.cookies.get(JOIN_COOKIE))
        again = 'again' in request.query_params
        if current is not None and (current.status in ('waiting', 'approved') or current.status == 'denied'
                                    and not again):
            return _join_html(current.code, current.status, after)
        try:
            current = self.devices.new_request(address=_address(request), user_agent=_agent(request))
        except TooManyRequests:
            return _page('Too many devices are waiting', '<p>Several devices are already waiting to be allowed. '
                         'Try again in a few minutes.</p>', status=429)
        response = _join_html(current.code, current.status, after)
        response.set_cookie(JOIN_COOKIE, current.id, max_age=REQUEST_MINUTES * 2 * 60, httponly=True,
                            samesite='strict', secure=self.scheme == 'https', path='/')
        return response

    async def join_status(self, request: Request) -> Response:
        if self.who_request(request).device:
            return JSONResponse({'status': 'approved'})
        current = self.devices.request(request.cookies.get(JOIN_COOKIE))
        if current is None:
            return JSONResponse({'status': 'expired'})
        if current.status == 'approved':
            result = self.devices.claim(current.id)
            if result:
                device, token = result
                log.warning('Paired %s (%s): allowed on the computer', device.name, device.address)
                response = self._paired(JSONResponse({'status': 'approved'}), token)
                response.delete_cookie(JOIN_COOKIE, path='/')
                return response
        return JSONResponse({'status': 'expired' if current.status == 'done' else current.status})

    async def sign_out(self, request: Request) -> Response:
        who = self.who_request(request)
        if who.local:
            return RedirectResponse('/', status_code=303)
        if who.device:
            self.devices.remove(who.device.id)
        response = RedirectResponse('/join', status_code=303)
        response.delete_cookie(COOKIE, path='/')
        return response

    # -- for the terminal commands ----------------------------------------------------------

    def _local_only(self, request: Request) -> Response | None:
        if not self.who_request(request).local or 'origin' in request.headers \
                or request.headers.get('sec-fetch-site', 'none') not in ('none', 'same-origin'):
            return JSONResponse({'error': 'Only from this computer itself.'}, status_code=403)
        return None

    async def local_pair(self, request: Request) -> Response:
        if refused := self._local_only(request):
            return refused
        body = await _json(request)
        addresses = self.addresses()
        address = str(body.get('address') or (addresses[0] if addresses else 'localhost'))
        link = self.devices.new_link()
        return JSONResponse({'url': self.link_url(link.code, address), 'expires': link.expires.isoformat(),
                             'addresses': addresses})

    async def local_requests(self, request: Request) -> Response:
        if refused := self._local_only(request):
            return refused
        now = datetime.now(timezone.utc)
        return JSONResponse({'requests': [
            {'code': r.code, 'name': r.name, 'address': r.address, 'minutes': int((now - r.created).total_seconds() // 60)}
            for r in self.devices.pending()]})

    async def local_answer(self, request: Request) -> Response:
        if refused := self._local_only(request):
            return refused
        body = await _json(request)
        answered = self.devices.answer(str(body.get('code', '')), allow=bool(body.get('allow', True)))
        if answered is None:
            return JSONResponse({'error': 'No device is waiting with that code.'}, status_code=404)
        return JSONResponse({'name': answered.name, 'address': answered.address, 'status': answered.status})


class AccessGuard:
    """ASGI middleware: the computer itself and paired devices get in; others only reach the pairing pages."""

    def __init__(self, app: Any, access: Access) -> None:
        self.app = app
        self.access = access

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope['type'] not in ('http', 'websocket'):
            await self.app(scope, receive, send)
            return
        headers = {k.decode('latin-1').lower(): v.decode('latin-1') for k, v in scope.get('headers', [])}
        client = scope.get('client')
        host = client[0] if client else None
        who = self.access.who(host, headers)
        if who.allowed or is_open(scope.get('path', '')):
            if who.device:
                self.access.devices.seen(who.device, host or '')
            await self.app(scope, receive, send)
            return
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
        elif scope.get('method') == 'GET' and 'text/html' in headers.get('accept', ''):
            wanted = scope.get('path', '/') + (f'?{scope["query_string"].decode("latin-1")}'
                                               if scope.get('query_string') else '')
            target = '/join' if wanted == '/' else f'/join?next={quote(wanted, safe="")}'
            await RedirectResponse(target, status_code=303)(scope, receive, send)
        else:
            await Response('This device isn\'t paired with the app.\n', status_code=401,
                           media_type='text/plain')(scope, receive, send)


# -- headers for every page -----------------------------------------------------------------

SECURITY_HEADERS = [
    # No other site may show the app inside one of its pages (where it could trick you into clicking).
    (b'x-frame-options', b'DENY'),
    (b'content-security-policy', b"frame-ancestors 'none'"),
    # Pictures and links leading elsewhere don't tell the other site which page of the app they were on.
    (b'referrer-policy', b'same-origin'),
    # Files are only ever what they say they are (a picture can't be run as a script).
    (b'x-content-type-options', b'nosniff'),
]


class SecurityHeaders:
    """ASGI middleware adding SECURITY_HEADERS to every response (unless the response sets them itself)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message['type'] == 'http.response.start':
                headers = list(message.get('headers') or [])
                present = {name.lower() for name, _ in headers}
                headers += [(name, value) for name, value in SECURITY_HEADERS if name not in present]
                message = {**message, 'headers': headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


# -- this computer's addresses ----------------------------------------------------------------

# Network cards of virtual machines and containers (Docker, VirtualBox, WSL...): their addresses lead nowhere
# a phone can go, so they're listed last.
VIRTUAL_CARDS = ('docker', 'br-', 'veth', 'virbr', 'vmnet', 'vbox', 'virtualbox', 'vmware', 'lxcbr', 'lxdbr', 'cni',
                 'flannel', 'podman', 'vethernet', 'hyper-v')
_HOME = [ipaddress.ip_network(n) for n in ('192.168.0.0/16', '10.0.0.0/8', '172.16.0.0/12', '100.64.0.0/10')]


def network_addresses(listen: str = '0.0.0.0') -> list[str]:
    """This computer's addresses other devices may reach the app at, listening on `listen`; likeliest first.

    Home network addresses (192.168..., then 10..., 172.16...) come first, then VPN ones like Tailscale's
    (100.64...), then the rest; virtual machines' and containers' last; IPv6 after IPv4, and the computer's
    .local name (which not every device resolves) at the end. Listening on one address: just that one.
    Listening on this computer only (127.0.0.1): none.
    """
    try:
        listening = ipaddress.ip_address(listen)
    except ValueError:  # 'localhost'
        return []
    if listening.is_loopback:
        return []
    if not listening.is_unspecified:
        return [listen]
    ranked = []
    for card, value in _cards():
        ip = ipaddress.ip_address(value)
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.version > listening.version:
            continue  # "0.0.0.0" is IPv4 only; "::" both
        home = next((i for i, net in enumerate(_HOME) if ip.version == 4 and ip in net), len(_HOME))
        virtual = any(word in card.lower() for word in VIRTUAL_CARDS)
        ranked.append(((virtual, ip.version, home, not ip.is_private, str(ip)), str(ip)))
    found = list(dict.fromkeys(value for _, value in sorted(ranked)))
    host = socket.gethostname()
    return found + ([f'{host}.local'] if host and host != 'localhost' and not host.endswith('.local') else [])


def _cards() -> list[tuple[str, str]]:
    """(network card, IP address) pairs of this computer."""
    try:
        import ifaddr
        return [(adapter.nice_name or adapter.name, ip.ip if isinstance(ip.ip, str) else ip.ip[0])
                for adapter in ifaddr.get_adapters() for ip in adapter.ips]
    except Exception:  # listing the network cards is a nicety
        from .certificates import local_names
        return [('', value) for value in local_names()[1]]


def url_for(scheme: str, host: str, port: int) -> str:
    """'https://192.168.1.20:8090', '[fd00::5]' for IPv6, no port when it's the scheme's usual one."""
    host = host.strip()
    try:
        if isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address):
            host = f'[{host}]'
    except ValueError:
        pass
    default = 443 if scheme == 'https' else 80
    shown = '' if port == default or ':' in host.split(']')[-1] else f':{port}'
    return f'{scheme}://{host}{shown}'


# -- small helpers ----------------------------------------------------------------------------

def safe_next(value: str | None) -> str:
    """Where to go after joining: a path of this app only (never another site)."""
    if value and value.startswith('/') and not value.startswith('//') and '\\' not in value:
        return value
    return '/'


def _agent(request: Request) -> str:
    return request.headers.get('user-agent', '')


def _address(request: Request) -> str:
    return request.client.host if request.client else ''


async def _json(request: Request) -> dict:
    try:
        data = await request.json()
    except (ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


STYLE = '''
  :root { color-scheme: light dark; --ink: #1D2233; --muted: #5B6376; --page: #F3F5F7; --surface: #FFFFFF;
          --line: #DDE2E8; --accent: #1F7A70; }
  @media (prefers-color-scheme: dark) { :root { --ink: #E7E9EE; --muted: #A2A9B7; --page: #13161D;
          --surface: #1B1F28; --line: #2C313D; --accent: #4DB6A8; } }
  body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
         background: var(--page); color: var(--ink); font: 16px/1.5 system-ui, -apple-system, 'Segoe UI', sans-serif; }
  main { max-width: 26rem; margin: 24px; padding: 28px; background: var(--surface); border: 1px solid var(--line);
         border-radius: 16px; }
  h1 { font: 600 1.4rem/1.25 Georgia, serif; margin: 0 0 12px; }
  p { margin: 0 0 12px; color: var(--muted); }
  a, button { color: var(--accent); }
  .code { font: 700 2.4rem/1 ui-monospace, 'SF Mono', Menlo, monospace; letter-spacing: .08em; color: var(--ink);
          text-align: center; margin: 18px 0; }
  .wait { text-align: center; font-size: .9rem; }
  button { font: inherit; background: none; border: 1px solid var(--accent); border-radius: 8px; padding: 6px 14px;
           cursor: pointer; }
'''


def _js(value: str) -> str:
    """A JavaScript string literal that's safe inside a <script> element."""
    return json.dumps(value).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def is_open(path: str) -> bool:
    """Whether any device may ask for it, paired or not."""
    return path in OPEN_PATHS or (path.startswith(OPEN_FOLDERS) and '..' not in path)


def _page(title: str, body: str, *, status: int = 200) -> HTMLResponse:
    return HTMLResponse(f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
                        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
                        f'<title>{escape(title)}</title><link rel="icon" href="/icons/favicon.svg" type="image/svg+xml">'
                        f'<link rel="icon" href="/favicon.ico" sizes="any"><style>{STYLE}</style></head>'
                        f'<body><main><h1>{escape(title)}</h1>{body}</main></body></html>', status_code=status)


def _join_html(code: str, status: str, after: str = '/') -> HTMLResponse:
    return _page('Allow this device', f'''
<p>This device isn't paired with the app yet. On the computer the app runs on, open the app's
<b>This computer</b> page (starting the app there opens it) and allow the device showing this code:</p>
<div class="code" id="code">{escape(code)}</div>
<p class="wait" id="state">Waiting…</p>
<p>Or, on the computer, scan or open a pairing link from the same place, or run <b>{escape(command())} approve {escape(code)}</b>.</p>
<script>
  const state = document.getElementById('state');
  function show(status) {{
    if (status === 'approved') {{ state.textContent = 'Allowed! Opening the app…'; location.href = {_js(after)}; return true; }}
    if (status === 'denied' || status === 'expired') {{
      state.innerHTML = (status === 'denied' ? 'This request was denied.' : 'This request expired.')
        + ' <button onclick="location.href=\\'/join?again&next=' + encodeURIComponent({_js(after)}) + '\\'">Ask again</button>';
      return true;
    }}
    return false;
  }}
  if (!show({_js(status)})) {{
    const timer = setInterval(async () => {{
      try {{
        const answer = await (await fetch('/join/status', {{credentials: 'same-origin'}})).json();
        if (show(answer.status)) clearInterval(timer);
      }} catch (e) {{ /* the app may be restarting: try again */ }}
    }}, 2000);
  }}
</script>''')

