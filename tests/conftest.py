"""Shared test helpers: a fake model connection, so chats get replies without the network."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import ClassVar, Literal

import pytest

from rpapp.connections import Connection, Message, ModelError, ModelInfo, Reply, register


class FakeModel:
    """What the fake connection does; tests change it through the `fake_model` fixture."""

    def __init__(self) -> None:
        self.replies: list[Reply] = []        # given in order; then a default reply
        self.error = ''                       # if set, generate() fails with it
        self.gate: asyncio.Event | None = None  # if set, generate() waits for it (to test "Replying…")
        self.calls: list[list[Message]] = []  # the messages of each chat request
        self.continued: list[bool] = []       # for each: asked to continue the last message (prefill)
        self.memory_answers: list[str] = []   # memory calls answer these in order, then '[]'
        self.memory_calls: list[list[Message]] = []
        self.memory_gate: asyncio.Event | None = None

    async def generate(self, messages: Sequence[Message]) -> Reply:
        if 'Answer with a JSON array of changes' in messages[0].content:  # a memory call
            self.memory_calls.append(list(messages))
            if self.memory_gate is not None:
                await self.memory_gate.wait()
            return Reply(text=self.memory_answers.pop(0) if self.memory_answers else '[]')
        self.calls.append(list(messages))
        if self.gate is not None:
            await self.gate.wait()
        if self.error:
            raise ModelError(self.error)
        return self.replies.pop(0) if self.replies else Reply(text='*Seraphina smiles.* Rest now.')


FAKE = FakeModel()


@register
class FakeConnection(Connection):
    kind: Literal['fake'] = 'fake'
    display_name: ClassVar[str] = 'Fake model'

    async def stream(self, messages, params=None, *, client=None) -> AsyncIterator[str]:
        yield (await FAKE.generate(messages)).text

    async def list_models(self, *, client=None) -> list[ModelInfo]:
        return []

    async def generate(self, messages, params=None, *, client=None, continue_last=False) -> Reply:
        if 'Answer with a JSON array of changes' not in messages[0].content:
            FAKE.continued.append(continue_last)
        return await FAKE.generate(messages)


@pytest.fixture
def fake_model() -> FakeModel:
    FAKE.__init__()
    yield FAKE
    for gate in (FAKE.gate, FAKE.memory_gate):
        if gate is not None:
            gate.set()  # let a waiting reply finish


@pytest.fixture(autouse=True)
def never_public(monkeypatch):
    """No test ever publishes to a public relay: card sharing's PUBLIC_PUBLISHING (True in the releases) is off
    for them all, so that a public relay's deliveries are only "held". Those that try the public path give
    their Sharing its own (public_publishing=True), with relays on 127.0.0.1."""
    try:
        from rpapp.publishing import service
    except ImportError:  # (without card sharing's libraries: nothing publishes)
        return
    monkeypatch.setattr(service, 'PUBLIC_PUBLISHING', False)
