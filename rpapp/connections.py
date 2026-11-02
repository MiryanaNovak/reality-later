"""Connections to AI models.

A connection holds everything needed to reach one model (where, which key,
which model, default generation settings) plus the code that calls it.
Each provider is a subclass with its own `kind`, which is written into the
JSON file so the right class is rebuilt when the file is loaded:

    Connection                         the interface the rest of the app uses
    └── OpenAIStyleConnection          shared code for /chat/completions APIs
        ├── OpenAICompatibleConnection kind="openai_compatible"  (LM Studio, Ollama, llama.cpp...)
        └── OpenRouterConnection       kind="openrouter"

Adding a provider later:
  - if it speaks the OpenAI chat API, the existing OpenAICompatibleConnection
    probably works as is; otherwise subclass OpenAIStyleConnection and
    override _headers() / _body() for its extras,
  - for a different API (Anthropic or Gemini native, say), subclass Connection
    and implement stream() and list_models(),
  - decorate the class with @register.

Usage:

    store = connection_store('data')
    conn = OpenRouterConnection(name='DeepSeek', api_key='sk-or-...', model='deepseek/deepseek-chat')
    store.save(conn)

    conn = store.load(conn.id)
    reply = await conn.generate([Message(role='user', content='Hi!')])
    print(reply.text, reply.usage.cost)

    async for text in conn.stream([Message(role='user', content='Hi!')]):  # or piece by piece
        print(text, end='')
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .storage import JsonStore, StoredModel


# ---------------------------------------------------------------------------
# Small shared types
# ---------------------------------------------------------------------------

class Message(BaseModel):
    """One message of a conversation, in the provider-neutral form the app uses."""
    role: Literal['system', 'user', 'assistant']
    content: str


class ModelInfo(BaseModel):
    """One entry of a provider's model list (for a model picker in the UI)."""
    id: str
    name: str = ''
    context_length: int | None = None


class ModelError(Exception):
    """Calling the model failed. The message is meant to be shown to the user."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Usage(BaseModel):
    """Token counts and cost of one request, as the server reports them (None: not reported)."""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None   # the reply, thinking included
    cached_tokens: int | None = None       # prompt tokens read from the provider's cache (cheaper)
    reasoning_tokens: int | None = None    # of the completion tokens, those spent thinking
    cost: float | None = None              # OpenRouter: what was charged, in credits (US dollars)

    @classmethod
    def from_openai(cls, usage: dict) -> Usage:
        return cls(
            prompt_tokens=usage.get('prompt_tokens'),
            completion_tokens=usage.get('completion_tokens'),
            cached_tokens=(usage.get('prompt_tokens_details') or {}).get('cached_tokens'),
            reasoning_tokens=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens'),
            cost=usage.get('cost'),
        )


class Reply(BaseModel):
    """A whole reply, with what the server said about it."""
    text: str = ''
    reasoning: str = ''       # the model's thinking, for models that show it
    model: str = ''           # the model that actually answered (OpenRouter can route to another one)
    provider: str = ''        # OpenRouter: the company that ran it
    finish_reason: str = ''   # 'stop': finished; 'length': cut off at the max reply length; ...
    request_id: str = ''      # the server's id for the request (OpenRouter: see it in your activity)
    usage: Usage = Field(default_factory=Usage)


class GenerationParams(BaseModel):
    """Generation settings.

    None (or an empty list) means "don't send it, use the server's default".
    Only what you set is sent, so servers that don't know a parameter aren't
    bothered by it.
    """
    model_config = ConfigDict(extra='ignore', validate_assignment=True)

    max_tokens: int | None = Field(None, ge=1)
    temperature: float | None = Field(None, ge=0, le=2)
    top_p: float | None = Field(None, gt=0, le=1)
    top_k: int | None = Field(None, ge=0)
    min_p: float | None = Field(None, ge=0, le=1)
    top_a: float | None = Field(None, ge=0, le=1)
    frequency_penalty: float | None = Field(None, ge=-2, le=2)
    presence_penalty: float | None = Field(None, ge=-2, le=2)
    repetition_penalty: float | None = Field(None, gt=0, le=2)
    seed: int | None = None
    stop: list[str] = Field(default_factory=list)

    def values_set(self) -> dict[str, Any]:
        """Only the settings that have a value, ready to put in a request."""
        return {k: v for k, v in self.model_dump().items() if v is not None and v != []}

    def merged(self, overrides: GenerationParams | None) -> GenerationParams:
        """A copy where every value set in `overrides` wins.

        Lets a character or a chat tweak, say, the temperature while keeping
        the connection's other defaults.
        """
        if overrides is None:
            return self
        return self.model_copy(update=overrides.values_set(), deep=True)


# ---------------------------------------------------------------------------
# Base class and registry
# ---------------------------------------------------------------------------

class Connection(StoredModel, ABC):
    """What the rest of the app relies on, whatever the provider."""

    display_name: ClassVar[str] = 'Connection'   # shown in an "add connection" menu

    model: str = ''                   # model id, spelled the way the provider spells it
    context_window: int | None = None # in tokens; long chats are trimmed to fit. None = unknown
    # How instructions placed inside the chat (lore at a depth, final instructions) are sent.
    # Some models ignore system messages after the first one, or move them to the top: 'user' helps there.
    system_in_chat: Literal['system', 'user'] = 'system'
    # How "Extend the last reply" asks the model to go on: with a hidden instruction after the reply
    # (works with every model), or by prefill: the reply is sent as the start of the model's own message,
    # and it writes the rest (seamless, but not every model or server allows it).
    extend_method: Literal['instruction', 'prefill'] = 'instruction'
    params: GenerationParams = Field(default_factory=GenerationParams)  # this connection's defaults
    timeout: float = 300              # seconds to wait for data; local models can be slow on long prompts

    @abstractmethod
    def stream(self, messages: Sequence[Message], params: GenerationParams | None = None, *,
               client: httpx.AsyncClient | None = None) -> AsyncIterator[str]:
        """Yield the reply piece by piece as it's generated.

        `params` overrides this connection's defaults for this call only.
        Raises ModelError on failure. Stopping the iteration (or cancelling the
        task) closes the connection, which stops generation where the provider supports it.
        """

    @abstractmethod
    async def list_models(self, *, client: httpx.AsyncClient | None = None) -> list[ModelInfo]:
        """The models this connection can use. Also a handy "test connection" call."""

    async def complete(self, messages: Sequence[Message], params: GenerationParams | None = None, *,
                       client: httpx.AsyncClient | None = None) -> str:
        """The whole reply at once, text only (for summaries and other background jobs)."""
        return ''.join([piece async for piece in self.stream(messages, params, client=client)])

    async def generate(self, messages: Sequence[Message], params: GenerationParams | None = None, *,
                       client: httpx.AsyncClient | None = None, continue_last: bool = False) -> Reply:
        """The whole reply with its details (model, tokens, cost...) where the provider gives them.

        Streamed underneath, so cancelling the task closes the connection, which stops the
        generation (and its cost) at providers that support it. Subclasses can do better than this.

        `continue_last`: the last message is the model's own, unfinished: it writes the rest of it
        (prefill). The reply is only what it adds.
        """
        return Reply(text=await self.complete(messages, params, client=client))


CONNECTION_TYPES: dict[str, type[Connection]] = {}


def register(cls: type[Connection]) -> type[Connection]:
    """Class decorator: makes a connection class loadable from its `kind`."""
    kind = cls.model_fields['kind'].default
    if kind in CONNECTION_TYPES:
        raise ValueError(f'Connection kind {kind!r} is already registered')
    CONNECTION_TYPES[kind] = cls
    return cls


def connection_from_dict(data: dict) -> Connection:
    """Rebuild a connection of the right class from its saved dict."""
    kind = data.get('kind')
    cls = CONNECTION_TYPES.get(kind)  # type: ignore[arg-type]
    if cls is None:
        raise ValueError(f'Unknown connection kind {kind!r} (known: {", ".join(CONNECTION_TYPES)})')
    return cls.model_validate(data)


def connection_store(data_dir: str | Path) -> JsonStore[Connection]:
    """The store for <data_dir>/connections/."""
    return JsonStore(Path(data_dir) / 'connections', connection_from_dict)


# ---------------------------------------------------------------------------
# OpenAI-style chat API (shared by OpenRouter and most local servers)
# ---------------------------------------------------------------------------

_STATUS_HINTS = {
    401: 'check the API key',
    402: 'the account is out of credits',
    403: 'access denied',
    404: 'check the base URL and the model name',
    408: 'the request timed out',
    429: 'rate limited, wait a bit and retry',
    502: 'the model provider is down or returned an invalid response',
    503: 'no provider is available for this model right now',
}


class OpenAIStyleConnection(Connection):
    """Shared code for servers that speak the OpenAI /chat/completions API.

    Not registered itself: use one of the subclasses below.
    """

    base_url: str = 'http://localhost:1234/v1'
    api_key: str = Field('', repr=False)  # hidden from print() and logs, still saved to the file

    # -- hooks subclasses can extend ---------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers

    def _body(self, messages: Sequence[Message], params: GenerationParams | None, *,
              continue_last: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {
            'messages': [m.model_dump() for m in messages],
            'stream': True,
            **self.params.merged(params).values_set(),
        }
        if self.model:  # some local servers ignore it; OpenRouter falls back to your account default
            body['model'] = self.model
        return body

    # -- the interface -----------------------------------------------------

    async def stream(self, messages: Sequence[Message], params: GenerationParams | None = None, *,
                     client: httpx.AsyncClient | None = None) -> AsyncIterator[str]:
        async for chunk in self._chunks(messages, params, client):
            for choice in chunk.get('choices') or []:
                text = (choice.get('delta') or {}).get('content')
                if text:
                    yield text

    async def generate(self, messages: Sequence[Message], params: GenerationParams | None = None, *,
                       client: httpx.AsyncClient | None = None, continue_last: bool = False) -> Reply:
        reply = Reply()
        text: list[str] = []
        thinking: list[str] = []
        async for chunk in self._chunks(messages, params, client, continue_last=continue_last):
            reply.request_id = reply.request_id or str(chunk.get('id') or '')
            reply.model = str(chunk.get('model') or reply.model)
            reply.provider = str(chunk.get('provider') or reply.provider)
            for choice in chunk.get('choices') or []:
                delta = choice.get('delta') or {}
                if delta.get('content'):
                    text.append(delta['content'])
                # OpenRouter says "reasoning"; DeepSeek's own API and llama.cpp say "reasoning_content"
                if piece := delta.get('reasoning') or delta.get('reasoning_content'):
                    thinking.append(piece)
                if choice.get('finish_reason'):
                    reply.finish_reason = str(choice['finish_reason'])
            if isinstance(chunk.get('usage'), dict):  # usually in the last chunk
                reply.usage = Usage.from_openai(chunk['usage'])
        reply.text, reply.reasoning = ''.join(text), ''.join(thinking)
        return reply

    async def list_models(self, *, client: httpx.AsyncClient | None = None) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        path: str | None = '/models'
        for _ in range(50):  # follow pagination links, with a safety limit
            if path is None:
                break
            async with self._request('GET', path, client) as response:
                await response.aread()
                try:
                    page = response.json()
                except ValueError as e:
                    raise ModelError(f'{response.url} did not return a model list') from e
            for m in page.get('data') or []:
                context = m.get('context_length') or (m.get('top_provider') or {}).get('context_length')
                models.append(ModelInfo(id=m['id'], name=m.get('name') or m['id'], context_length=context))
            next_link = (page.get('links') or {}).get('next')
            path = str(response.url.join(next_link)) if next_link else None
        return sorted(models, key=lambda m: m.name.lower())

    # -- plumbing ----------------------------------------------------------

    async def _chunks(self, messages: Sequence[Message], params: GenerationParams | None,
                      client: httpx.AsyncClient | None, *, continue_last: bool = False) -> AsyncIterator[dict]:
        """The decoded events of a streamed /chat/completions reply."""
        async with self._request('POST', '/chat/completions', client,
                                 json=self._body(messages, params, continue_last=continue_last)) as response:
            async for line in response.aiter_lines():
                chunk = _parse_sse_line(line)
                if chunk is None:
                    continue
                if chunk == 'DONE':
                    return
                if 'error' in chunk:  # error after the reply had started (HTTP status is still 200)
                    raise ModelError(_error_message(chunk['error']))
                yield chunk

    @asynccontextmanager
    async def _request(self, method: str, path: str, client: httpx.AsyncClient | None,
                       **kwargs: Any) -> AsyncIterator[httpx.Response]:
        """Open a streaming request, turning every failure into a readable ModelError.

        `path` is relative to base_url ('/models'), or a full URL.
        """
        if path.startswith(('http://', 'https://')):
            url = httpx.URL(path)
        else:
            url = httpx.URL(self.base_url.rstrip('/') + '/' + path.lstrip('/'))
        timeout = httpx.Timeout(self.timeout, connect=15)
        async with _http_client(client) as http:
            try:
                async with http.stream(method, url, headers=self._headers(), timeout=timeout,
                                       **kwargs) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        raise _error_from_response(response)
                    yield response
            except httpx.ConnectError as e:
                raise ModelError(f"Can't reach {self.base_url}. Is the server running and the URL right? ({e})") from e
            except httpx.TimeoutException as e:
                raise ModelError(f'No answer from {self.base_url} after {self.timeout:g} s') from e
            except httpx.HTTPError as e:
                raise ModelError(f'Connection problem with {self.base_url}: {e}') from e


@register
class OpenAICompatibleConnection(OpenAIStyleConnection):
    """Any server with an OpenAI-style chat API: LM Studio, Ollama, llama.cpp server,
    KoboldCpp, vLLM, text-generation-webui, or another cloud provider.

    Usual local base URLs: LM Studio http://localhost:1234/v1,
    Ollama http://localhost:11434/v1, llama.cpp http://localhost:8080/v1,
    KoboldCpp http://localhost:5001/v1.
    """
    kind: Literal['openai_compatible'] = 'openai_compatible'
    display_name: ClassVar[str] = 'OpenAI-compatible server'

    def _body(self, messages: Sequence[Message], params: GenerationParams | None, *,
              continue_last: bool = False) -> dict[str, Any]:
        # Ask for token counts at the end of the reply (OpenRouter always sends them).
        body = {**super()._body(messages, params), 'stream_options': {'include_usage': True}}
        if continue_last:  # vLLM's flags, also understood by llama.cpp: go on with the last message
            body.update(continue_final_message=True, add_generation_prompt=False)
        return body


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------

class ProviderPreferences(BaseModel):
    """Which of the companies hosting a model OpenRouter may route to.

    Only values you change from the defaults are sent.
    See https://openrouter.ai/docs/features/provider-routing
    """
    model_config = ConfigDict(extra='ignore', validate_assignment=True)

    order: list[str] = Field(default_factory=list)          # provider slugs to try first, in order
    allow_fallbacks: bool = True                            # may use others when those are unavailable
    only: list[str] = Field(default_factory=list)           # use only these providers
    ignore: list[str] = Field(default_factory=list)         # never use these
    quantizations: list[str] = Field(default_factory=list)  # e.g. ["fp8", "bf16"] to avoid heavily quantized hosts
    data_collection: Literal['allow', 'deny'] = 'allow'     # "deny" skips providers that may store your prompts
    sort: Literal['price', 'throughput', 'latency'] | None = None

    def values_set(self) -> dict[str, Any]:
        defaults = type(self)().model_dump()
        return {k: v for k, v in self.model_dump().items() if v != defaults[k]}


@register
class OpenRouterConnection(OpenAIStyleConnection):
    kind: Literal['openrouter'] = 'openrouter'
    display_name: ClassVar[str] = 'OpenRouter'

    base_url: str = 'https://openrouter.ai/api/v1'
    provider: ProviderPreferences = Field(default_factory=ProviderPreferences)
    # For models that think before answering. None = the model's default.
    # The thinking text itself isn't passed on by stream(), only the answer.
    reasoning_effort: Literal['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] | None = None
    # Optional: how your app appears in your OpenRouter activity and its public rankings.
    app_name: str = ''
    app_url: str = ''

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if self.app_name:
            headers['X-OpenRouter-Title'] = self.app_name
        if self.app_url:
            headers['HTTP-Referer'] = self.app_url
        return headers

    def _body(self, messages: Sequence[Message], params: GenerationParams | None, *,
              continue_last: bool = False) -> dict[str, Any]:
        # (continue_last: OpenRouter needs nothing more than the model's message last, see its "assistant prefill")
        body = super()._body(messages, params)
        if prefs := self.provider.values_set():
            body['provider'] = prefs
        if self.reasoning_effort:
            body['reasoning'] = {'effort': self.reasoning_effort}
        return body


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _http_client(client: httpx.AsyncClient | None) -> AsyncIterator[httpx.AsyncClient]:
    """Use the caller's client (connection reuse, tests) or a short-lived one."""
    if client is not None:
        yield client
    else:
        async with httpx.AsyncClient() as new_client:
            yield new_client


def _parse_sse_line(line: str) -> dict | Literal['DONE'] | None:
    """Decode one line of a Server-Sent Events stream. None means "nothing to do"."""
    if not line.startswith('data:'):  # blank lines, ": keep-alive comments", "event:" lines
        return None
    payload = line[5:].strip()
    if payload == '[DONE]':
        return 'DONE'
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _error_message(error: Any) -> str:
    if not isinstance(error, dict):
        return str(error)
    message = str(error.get('message') or 'Unknown error')
    raw = (error.get('metadata') or {}).get('raw')  # OpenRouter: the provider's own message
    if raw and str(raw) not in message:
        message += f' ({str(raw)[:300]})'
    return message


def _error_from_response(response: httpx.Response) -> ModelError:
    try:
        body = response.json()
        error = body.get('error', body) if isinstance(body, dict) else body
        message = _error_message(error)
    except ValueError:
        message = response.text.strip()[:300] or response.reason_phrase
    hint = _STATUS_HINTS.get(response.status_code)
    text = f'{response.status_code}: {message}' + (f' ({hint})' if hint else '')
    return ModelError(text, status=response.status_code)
