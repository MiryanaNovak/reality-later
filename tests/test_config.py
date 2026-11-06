"""config.toml: reading it, its defaults and mistakes, and the network filter."""
import shutil
import sys
from pathlib import Path

import pytest

from rpapp.config import ConfigError, NetworkFilter, is_allowed, load_config, parse_networks

EXAMPLE = Path(__file__).parent.parent / 'config.example.toml'


def write(folder: Path, text: str) -> Path:
    (folder / 'config.toml').write_text(text, encoding='utf-8')
    return folder


def test_no_file_means_defaults(tmp_path):
    config = load_config(tmp_path)
    assert config.source is None and config.path == tmp_path / 'config.toml'
    server = config.server
    assert (server.port, server.address, server.allowed_networks) == (8090, '0.0.0.0', ['private'])
    assert server.https and server.pairing and server.open_browser
    # This computer's own plain-HTTP address, so that its browser shows no certificate warning.
    assert (server.local_http_port, server.local_url) == (8091, 'http://localhost:8091')


@pytest.mark.parametrize('text, extra_port, url', [
    ('port = 9000', 9001, 'http://localhost:9001'),                     # the port + 1
    ('local_port = 7000', 7000, 'http://localhost:7000'),
    ('local_port = 0', None, 'https://localhost:8090'),                 # none: the main address, warning and all
    ('https = false', None, 'http://localhost:8090'),                   # the main address is plain HTTP already
    ('https = false\naddress = "192.168.1.20"', 8091, 'http://localhost:8091'),  # else unreachable from here
    ('address = "192.168.1.20"\nlocal_port = 0', None, None),           # this computer can't open it at all
    ('address = "127.0.0.1"\nhttps = false', None, 'http://localhost:8090'),
])
def test_this_computers_address(tmp_path, text, extra_port, url):
    server = load_config(write(tmp_path, f'[server]\n{text}\n')).server
    assert (server.local_http_port, server.local_url) == (extra_port, url)


def test_the_example_file_holds_the_defaults(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / 'config.toml')
    config = load_config(tmp_path)
    assert config.source == tmp_path / 'config.toml'
    assert config.server == load_config(tmp_path / 'nowhere').server


def test_what_is_missing_uses_the_defaults(tmp_path):
    server = load_config(write(tmp_path, '[server]\nport = 9000\n')).server
    assert (server.port, server.address, server.allowed_networks) == (9000, '0.0.0.0', ['private'])
    assert load_config(write(tmp_path, '# nothing set\n')).server.port == 8090


def test_everything_set(tmp_path):
    server = load_config(write(tmp_path, '''
[server]
port = 8443
address = "192.168.1.20"
allowed_networks = ["local", "192.168.1.0/24", "100.64.0.0/10"]
''')).server
    assert (server.port, server.address) == (8443, '192.168.1.20')
    assert is_allowed('100.101.1.2', server.networks()) and not is_allowed('10.0.0.2', server.networks())
    assert not server.allows_everyone


@pytest.mark.parametrize('text, message', [
    ('[server]\nport = 70000\n', 'server.port: Input should be less than or equal to 65535'),
    ('[server]\nprot = 9000\n', 'server.prot: unknown setting (misspelled?)'),
    ('[server]\naddress = "my-computer"\n', 'server.address: \'my-computer\' is not an IP address'),
    ('[server]\nallowed_networks = ["home"]\n', 'server.allowed_networks: \'home\' is not a network'),
    ('[server]\nallowed_networks = []\n', 'server.allowed_networks: List should have at least 1 item'),
    ('[server]\nport = \n', 'Invalid value'),
    ('[server]\nport = 9000\nlocal_port = 9000\n', 'server: local_port and port must differ (both are 9000)'),
    ('[server]\nopen_browser = "sometimes"\n', 'server.open_browser: Input should be a valid boolean'),
])
def test_mistakes_stop_with_a_clear_message(tmp_path, text, message):
    with pytest.raises(ConfigError) as error:
        load_config(write(tmp_path, text))
    assert str(tmp_path / 'config.toml') in str(error.value) and message in str(error.value)


def test_network_matching():
    private = parse_networks(['private'])
    for host in ('127.0.0.1', '192.168.1.42', '10.1.2.3', '172.20.0.5', '::1', 'fd12::5', 'fe80::1%eth0',
                 '::ffff:192.168.1.5'):
        assert is_allowed(host, private), host
    for host in ('8.8.8.8', '172.32.0.1', '2a01:cb00::1', '::ffff:8.8.8.8', 'testclient', None):
        assert not is_allowed(host, private), host
    local = parse_networks(['local'])
    assert is_allowed('127.0.0.1', local) and is_allowed('::1', local) and not is_allowed('192.168.1.2', local)
    one = parse_networks(['192.168.1.42'])
    assert is_allowed('192.168.1.42', one) and not is_allowed('192.168.1.43', one)
    assert is_allowed('8.8.8.8', parse_networks(['any'])) and is_allowed('2a01::1', parse_networks(['ANY']))


async def test_filter_turns_away_other_networks():
    middleware = NetworkFilter(None, parse_networks(['private']))
    reached = []

    async def app(scope, receive, send):
        reached.append((scope.get('client') or ('(no client)',))[0])

    middleware.app = app
    sent = []

    async def send(message):
        sent.append(message)

    await middleware({'type': 'http', 'client': ('192.168.1.42', 5000)}, None, send)
    assert reached == ['192.168.1.42'] and sent == []

    await middleware({'type': 'http', 'client': ('8.8.8.8', 5000)}, None, send)
    assert reached == ['192.168.1.42']
    assert sent[0]['status'] == 403 and b'8.8.8.8' in sent[1]['body']

    sent.clear()
    await middleware({'type': 'websocket', 'client': ('8.8.8.8', 5000)}, None, send)
    assert sent == [{'type': 'websocket.close', 'code': 1008}] and reached == ['192.168.1.42']

    await middleware({'type': 'lifespan'}, None, send)  # app startup and shutdown go through
    assert reached == ['192.168.1.42', '(no client)']


async def test_this_computer_always_gets_in():
    reached = []

    async def app(scope, receive, send):
        reached.append(scope['client'][0])

    async def send(message):
        raise AssertionError('turned away')

    middleware = NetworkFilter(app, parse_networks(['192.168.1.0/24']))  # "local" isn't in the list
    for host in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
        await middleware({'type': 'http', 'client': (host, 5000)}, None, send)
    assert reached == ['127.0.0.1', '::1', '::ffff:127.0.0.1']
