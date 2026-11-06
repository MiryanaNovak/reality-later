"""Run with:  pytest -q   (from the project folder; needs `pip install pytest pytest-asyncio`)"""
import json
import logging
import os
import shutil
import stat

import httpx
import pytest
from pydantic import ValidationError

from rpapp.connections import (
    GenerationParams, Message, ModelError, OpenAICompatibleConnection, OpenRouterConnection,
    connection_from_dict, connection_store,
)

ON_WINDOWS = os.name == 'nt'  # (file permissions below are Unix's: on Windows, a user's folder is theirs alone)

HELLO = [Message(role='user', content='Hello')]


def sse(*events: str) -> bytes:
    return ''.join(f'{e}\n\n' for e in events).encode()


def chunk(text: str) -> str:
    return 'data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'content': text}}]})


def fake_server(handler):
    """An httpx client whose requests go to `handler` instead of the network."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- saving and loading ------------------------------------------------------

def test_round_trip_keeps_class_and_values(tmp_path):
    store = connection_store(tmp_path)
    conn = OpenRouterConnection(name='My DeepSeek', api_key='sk-or-secret', model='deepseek/deepseek-chat',
                                params=GenerationParams(temperature=0.8, stop=['\nUser:']))
    conn.provider.data_collection = 'deny'
    path = store.save(conn)

    assert path == tmp_path / 'connections' / f'{conn.id}.json'
    assert conn.id.startswith('my-deepseek-')
    loaded = store.load(conn.id)
    assert type(loaded) is OpenRouterConnection
    assert loaded == conn

    data = json.loads(path.read_text(encoding='utf-8'))
    assert list(data)[:3] == ['kind', 'id', 'name']
    assert data['api_key'] == 'sk-or-secret'
    assert ON_WINDOWS or stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob('*.tmp'))


def test_store_lists_mixed_kinds_sorted_by_name(tmp_path):
    store = connection_store(tmp_path)
    store.save(OpenRouterConnection(name='b cloud'))
    store.save(OpenAICompatibleConnection(name='A local', base_url='http://localhost:11434/v1'))
    items = store.list()
    assert [type(i).__name__ for i in items] == ['OpenAICompatibleConnection', 'OpenRouterConnection']


def test_broken_and_unknown_files_are_skipped(tmp_path, caplog):
    store = connection_store(tmp_path)
    store.save(OpenRouterConnection(name='good'))
    (store.folder / 'broken.json').write_text('{not json', encoding='utf-8')
    (store.folder / 'future.json').write_text('{"kind": "warp_drive", "name": "x"}', encoding='utf-8')
    (store.folder / 'bad-value.json').write_text('{"kind": "openrouter", "params": {"temperature": 9}}', encoding='utf-8')
    with caplog.at_level(logging.WARNING):
        assert [c.name for c in store.list()] == ['good']
    assert caplog.text.count('Skipping') == 3


def test_old_or_hand_written_files_load(tmp_path):
    store = connection_store(tmp_path)
    # Missing fields get defaults, unknown fields are ignored.
    (store.folder / 'handmade.json').write_text('{"kind": "openrouter", "model": "x/y", "old_field": 1}', encoding='utf-8')
    conn = store.load('handmade')
    assert conn.id == 'handmade' and conn.base_url == 'https://openrouter.ai/api/v1' and conn.model == 'x/y'


def test_copying_a_file_duplicates_the_connection(tmp_path):
    store = connection_store(tmp_path)
    conn = OpenRouterConnection(name='orig')
    store.save(conn)
    shutil.copy(store.path_for(conn.id), store.folder / 'copy.json')
    assert sorted(c.id for c in store.list()) == sorted([conn.id, 'copy'])


def test_delete_and_get(tmp_path):
    store = connection_store(tmp_path)
    conn = OpenRouterConnection(name='x')
    store.save(conn)
    store.delete(conn.id)
    assert store.get(conn.id) is None


def test_path_traversal_ids_are_refused(tmp_path):
    with pytest.raises(ValueError):
        connection_store(tmp_path).path_for('../../etc/passwd')


def test_api_key_hidden_from_repr():
    conn = OpenRouterConnection(api_key='sk-or-secret')
    assert 'sk-or-secret' not in repr(conn) and 'sk-or-secret' not in str(conn)


def test_invalid_values_are_refused():
    with pytest.raises(ValidationError):
        GenerationParams(temperature=5)
    conn = OpenRouterConnection()
    with pytest.raises(ValidationError):
        conn.params.top_p = 0
    with pytest.raises(ValidationError):
        conn.reasoning_effort = 'extreme'


def test_unknown_kind():
    with pytest.raises(ValueError, match='warp_drive'):
        connection_from_dict({'kind': 'warp_drive'})


def test_params_merge():
    base = GenerationParams(temperature=1.0, max_tokens=400, stop=['A'])
    merged = base.merged(GenerationParams(temperature=0.5))
    assert merged.values_set() == {'temperature': 0.5, 'max_tokens': 400, 'stop': ['A']}
    assert base.temperature == 1.0


# --- calling the model --------------------------------------------------------

async def test_openrouter_request_and_streaming():
    seen = {}

    def handler(request: httpx.Request):
        seen['url'] = str(request.url)
        seen['headers'] = request.headers
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, content=sse(
            ': OPENROUTER PROCESSING',
            chunk('*wipes'), chunk(' a mug*'), chunk(''),
            'data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'total_tokens': 9}}),
            'data: [DONE]',
            chunk(' never sent'),
        ))

    conn = OpenRouterConnection(api_key='sk-or-k', model='m/x', app_name='RP app', app_url='http://localhost',
                                reasoning_effort='low', params=GenerationParams(temperature=0.9, max_tokens=200))
    conn.provider.order = ['deepinfra']
    conn.provider.data_collection = 'deny'

    async with fake_server(handler) as client:
        pieces = [p async for p in conn.stream(HELLO, GenerationParams(temperature=0.3), client=client)]

    assert pieces == ['*wipes', ' a mug*']
    assert seen['url'] == 'https://openrouter.ai/api/v1/chat/completions'
    assert seen['headers']['authorization'] == 'Bearer sk-or-k'
    assert seen['headers']['x-openrouter-title'] == 'RP app'
    assert seen['headers']['http-referer'] == 'http://localhost'
    assert seen['body'] == {
        'model': 'm/x', 'messages': [{'role': 'user', 'content': 'Hello'}], 'stream': True,
        'temperature': 0.3, 'max_tokens': 200,
        'provider': {'order': ['deepinfra'], 'data_collection': 'deny'},
        'reasoning': {'effort': 'low'},
    }


async def test_openrouter_names_the_app_by_default():
    from rpapp import APP_NAME
    assert OpenRouterConnection(api_key='k')._headers()['X-OpenRouter-Title'] == APP_NAME == 'Reality Later'
    assert OpenRouterConnection(api_key='k', app_name='  ')._headers()['X-OpenRouter-Title'] == APP_NAME
    assert OpenRouterConnection(api_key='k', app_name='Mine')._headers()['X-OpenRouter-Title'] == 'Mine'
    assert 'HTTP-Referer' not in OpenRouterConnection(api_key='k')._headers()  # (the URL: only if set)
    assert 'X-OpenRouter-Title' not in OpenAICompatibleConnection()._headers()  # (OpenRouter's alone)


async def test_local_server_sends_only_what_is_set():
    seen = {}

    def handler(request):
        seen['headers'], seen['body'], seen['url'] = request.headers, json.loads(request.content), str(request.url)
        return httpx.Response(200, content=sse(chunk('Hi'), 'data: [DONE]'))

    conn = OpenAICompatibleConnection(base_url='http://localhost:11434/v1/')
    async with fake_server(handler) as client:
        assert await conn.complete(HELLO, client=client) == 'Hi'
    assert seen['url'] == 'http://localhost:11434/v1/chat/completions'
    assert 'authorization' not in seen['headers']
    assert seen['body'] == {'messages': [{'role': 'user', 'content': 'Hello'}], 'stream': True,
                            'stream_options': {'include_usage': True}}


async def test_generate_collects_the_reply_and_its_details():
    def event(**fields):
        return 'data: ' + json.dumps({'id': 'gen-123', 'model': 'deepseek/deepseek-r1', 'provider': 'DeepInfra',
                                      **fields})

    def handler(request):
        return httpx.Response(200, content=sse(
            ': OPENROUTER PROCESSING',
            event(choices=[{'index': 0, 'delta': {'role': 'assistant', 'content': '', 'reasoning': 'Hmm, '}}]),
            event(choices=[{'index': 0, 'delta': {'reasoning': 'she is hurt.'}}]),
            event(choices=[{'index': 0, 'delta': {'content': '*Seraphina smiles.* '}}]),
            event(choices=[{'index': 0, 'delta': {'content': 'Rest now.'}, 'finish_reason': 'stop'}]),
            event(choices=[], usage={'prompt_tokens': 1480, 'completion_tokens': 312, 'total_tokens': 1792,
                                     'cost': 0.00042, 'prompt_tokens_details': {'cached_tokens': 1024},
                                     'completion_tokens_details': {'reasoning_tokens': 120}}),
            'data: [DONE]'))

    conn = OpenRouterConnection(api_key='k', model='deepseek/deepseek-r1')
    async with fake_server(handler) as client:
        reply = await conn.generate(HELLO, client=client)
    assert reply.text == '*Seraphina smiles.* Rest now.' and reply.reasoning == 'Hmm, she is hurt.'
    assert (reply.model, reply.provider, reply.request_id, reply.finish_reason) == \
        ('deepseek/deepseek-r1', 'DeepInfra', 'gen-123', 'stop')
    u = reply.usage
    assert (u.prompt_tokens, u.completion_tokens, u.cached_tokens, u.reasoning_tokens, u.cost) == \
        (1480, 312, 1024, 120, 0.00042)


async def test_generate_with_a_bare_server():
    def handler(request):
        return httpx.Response(200, content=sse(chunk('Hi'), chunk(' there'), 'data: [DONE]'))

    async with fake_server(handler) as client:
        reply = await OpenAICompatibleConnection().generate(HELLO, client=client)
    assert reply.text == 'Hi there' and reply.usage.prompt_tokens is None and reply.model == ''


async def test_error_before_reply():
    def handler(request):
        return httpx.Response(401, json={'error': {'code': 401, 'message': 'No auth credentials found'}})

    async with fake_server(handler) as client:
        with pytest.raises(ModelError) as e:
            await OpenRouterConnection(api_key='bad').complete(HELLO, client=client)
    assert e.value.status == 401
    assert str(e.value) == '401: No auth credentials found (check the API key)'


async def test_error_with_provider_details_and_non_json_body():
    def handler(request):
        if 'chat' in request.url.path:
            return httpx.Response(400, json={'error': {'message': 'Provider returned error',
                                                       'metadata': {'raw': 'context too long'}}})
        return httpx.Response(502, text='<html>Bad Gateway</html>')

    conn = OpenRouterConnection()
    async with fake_server(handler) as client:
        with pytest.raises(ModelError, match=r'Provider returned error \(context too long\)'):
            await conn.complete(HELLO, client=client)
        with pytest.raises(ModelError, match='502: <html>Bad Gateway</html>'):
            await conn.list_models(client=client)


async def test_error_in_the_middle_of_the_reply():
    def handler(request):
        return httpx.Response(200, content=sse(
            chunk('Once upon'),
            'data: ' + json.dumps({'error': {'code': 'server_error', 'message': 'Provider disconnected unexpectedly'},
                                   'choices': [{'delta': {'content': ''}, 'finish_reason': 'error'}]}),
        ))

    received = []
    async with fake_server(handler) as client:
        with pytest.raises(ModelError, match='Provider disconnected'):
            async for piece in OpenRouterConnection().stream(HELLO, client=client):
                received.append(piece)
    assert received == ['Once upon']


async def test_server_not_running():
    def handler(request):
        raise httpx.ConnectError('Connection refused', request=request)

    async with fake_server(handler) as client:
        with pytest.raises(ModelError, match="Can't reach http://localhost:1234/v1"):
            await OpenAICompatibleConnection().complete(HELLO, client=client)


async def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout('timed out', request=request)

    async with fake_server(handler) as client:
        with pytest.raises(ModelError, match='after 5 s'):
            await OpenAICompatibleConnection(timeout=5).complete(HELLO, client=client)


async def test_list_models_follows_pages():
    def handler(request):
        if request.url.params.get('offset') == '1':
            return httpx.Response(200, json={'data': [{'id': 'b/2', 'name': 'B Two',
                                                       'top_provider': {'context_length': 8192}}]})
        return httpx.Response(200, json={'data': [{'id': 'a/1', 'name': 'A One', 'context_length': 128000}],
                                         'links': {'next': '/api/v1/models?offset=1&limit=1'}})

    async with fake_server(handler) as client:
        models = await OpenRouterConnection().list_models(client=client)
    assert [(m.id, m.context_length) for m in models] == [('a/1', 128000), ('b/2', 8192)]


async def test_list_models_local_server_ids_only():
    def handler(request):
        assert str(request.url) == 'http://localhost:1234/v1/models'
        return httpx.Response(200, json={'object': 'list', 'data': [{'id': 'qwen3-8b', 'object': 'model'}]})

    async with fake_server(handler) as client:
        models = await OpenAICompatibleConnection().list_models(client=client)
    assert models[0].id == models[0].name == 'qwen3-8b'
