"""Relays and the system's proxy (rpapp/publishing/relay.py): a relay on this computer or its network is
reached directly, as browsers do, since a proxy usually can't reach it; others go through the proxy, if the
system has one, and an error says which."""
import os
import socket

import pytest
from fake_relay import FakeRelay

from rpapp.publishing.relay import Relay, RelayError, is_local, proxy_for


@pytest.fixture
def dead_proxy(monkeypatch):
    """A proxy in the environment that nobody answers at, and no address to bypass it for."""
    for name in list(os.environ):
        if name.lower().endswith('_proxy'):
            monkeypatch.delenv(name)
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    monkeypatch.setenv('HTTPS_PROXY', f'http://user:secret@127.0.0.1:{port}')
    return port


def test_local_relays():
    for url in ('ws://127.0.0.1:7777', 'ws://localhost:7777', 'ws://192.168.1.20:7777', 'ws://10.42.0.216:7777',
                'ws://[::1]:7777', 'ws://[fd00::1]:7777', 'ws://[fe80::1]:7777', 'ws://nas.local:7777',
                'ws://nas:7777'):
        assert is_local(url), url
    for url in ('wss://relay.damus.io', 'wss://8.8.8.8', 'wss://localhost.example.com', 'wss://[2606:4700:4700::1111]'):
        assert not is_local(url), url


def test_which_go_through_the_proxy(dead_proxy, monkeypatch):
    assert proxy_for('ws://10.42.0.216:7777') is None  # (the network's: directly)
    assert proxy_for('ws://127.0.0.1:7777') is None
    assert proxy_for('wss://relay.example.com') == f'http://user:secret@127.0.0.1:{dead_proxy}'
    monkeypatch.setenv('NO_PROXY', 'relay.example.com')  # (the system's own exceptions, kept)
    assert proxy_for('wss://relay.example.com') is None


async def test_a_local_relay_despite_the_proxy(dead_proxy):
    async with FakeRelay() as fake:
        async with Relay(fake.url, timeout=5) as relay:  # (through the dead proxy, it would fail)
            assert await relay.query([{'kinds': [2003], 'limit': 5}]) == []


async def test_the_proxy_named_when_it_fails(dead_proxy):
    with pytest.raises(RelayError) as failed:
        async with Relay('wss://relay.example.invalid', timeout=5):
            pass
    assert f'can\'t connect through the proxy http://127.0.0.1:{dead_proxy} (' in str(failed.value)
    assert 'secret' not in str(failed.value)  # (its password, never shown)
