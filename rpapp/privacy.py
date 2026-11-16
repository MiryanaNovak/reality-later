"""Where a browser connects from, for Settings › Privacy (the pictures blurred away from home):

    computer   this computer itself (access.is_local: from 127.0.0.1 or ::1, to a local name, not relayed)
    home       the local network: a private address (192.168..., 10..., 172.16-31..., fc00::/7 but Tailscale's),
               a link-local one (169.254..., fe80::), or an IPv6 address in one of this computer's own networks
               (home networks often give their devices public IPv6 addresses: those of the same network share
               their first half)
    away       anything else: the internet, and services like Tailscale, whose addresses (100.64.0.0/10,
               fd7a:115c:a1e0::/48) reach this computer from anywhere. So is a connection relayed by a proxy on
               this computer (Tailscale Serve or Funnel, a reverse proxy, a tunnel): it comes from 127.0.0.1, but
               from someone elsewhere

A VPN giving private addresses (10..., say) looks like the home network: the address can't tell them apart.
"""
from __future__ import annotations

import ipaddress
import time
from collections.abc import Iterable, Mapping
from typing import Literal

from .access import is_local

Place = Literal['computer', 'home', 'away']

# Reaching this computer from anywhere: Tailscale (and carrier-grade NAT, the same range: 100.64.0.0/10)
ANYWHERE = [ipaddress.ip_network(n) for n in ('100.64.0.0/10', 'fd7a:115c:a1e0::/48')]
HOME = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16',
                                           'fc00::/7', 'fe80::/10')]
_own: tuple[float, list[ipaddress.IPv6Network]] = (0.0, [])


def where(client_host: str | None, headers: Mapping[str, str], *,
          own_networks: Iterable[ipaddress.IPv6Network] | None = None) -> Place:
    """Where a connection comes from (see the module's notes). `headers`: its HTTP headers, lowercase names;
    `own_networks`: this computer's IPv6 networks (default: its network cards')."""
    if is_local(client_host, headers):
        return 'computer'
    try:
        ip = ipaddress.ip_address((client_host or '').split('%')[0])
    except ValueError:
        return 'away'
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:  # (relayed, or to another name: from elsewhere, through this computer)
        return 'away'
    if any(ip in n for n in ANYWHERE if n.version == ip.version):
        return 'away'
    if any(ip in n for n in HOME if n.version == ip.version):
        return 'home'
    if ip.version == 6 and any(ip in n for n in (own_networks if own_networks is not None else own_ipv6_networks())):
        return 'home'
    return 'away'


def own_ipv6_networks() -> list[ipaddress.IPv6Network]:
    """The /64 networks of this computer's public IPv6 addresses (its network cards', looked at once a minute)."""
    global _own
    at, networks = _own
    if time.monotonic() - at < 60:
        return networks
    from .access import _cards
    networks = []
    for _, value in _cards():
        try:
            ip = ipaddress.ip_address(value.split('%')[0])
        except ValueError:
            continue
        if ip.version == 6 and ip.is_global:
            networks.append(ipaddress.ip_network(f'{ip}/64', strict=False))
    _own = (time.monotonic(), list(dict.fromkeys(networks)))
    return _own[1]
