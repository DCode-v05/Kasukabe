"""Through the REAL agent loop: quota exhaustion is ONE attempt, never retried.

This is the end-to-end regression for the whole quota-terminal contract. The
only substituted dependency is the LLM adapter (a FakeAdapter that raises
``CitadelQuotaExhausted`` from ``stream_with_tools``) — everything else is the
real ``ExecutionThread.agent_loop`` path: OverloadTracker construction, the
``except Exception`` recovery branch, ``record_error``, and
``get_persistent_delay``.

The bug this guards: ``CitadelQuotaExhausted`` carries no ``status_code``, so the
loop must NOT classify it as a transient 429 and must NOT persistent-retry it —
not even when ``settings.persistent_retry`` is True (the mode that otherwise
spins a 429 for up to 6h). The model is hit exactly ONCE, the typed exception
propagates out of the loop unchanged, and the product layer maps it to a single
``quota_exhausted`` structured error block (asserted via the real
``error_from_exception``, the same mapping server.py uses at the SSE boundary).
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest

from src.config import Settings
from src.engine_core.events import EventEmitter
from src.engine_core.schema import RequestOptions
from src.engine_core.thread import ExecutionThread
from src.herald.provider import CitadelQuotaExhausted
from src.herald.types import Message, StreamEvent, ToolDef
from src.memory.memory_factory import create_working_memory


class QuotaExhaustedAdapter:
    """Counts upstream attempts and raises CitadelQuotaExhausted on each one.

    A real quota rejection surfaces as this typed exception from the provider's
    streaming entry point (provider.py:642). Raising it inside the async
    generator reproduces exactly what the loop catches."""

    def __init__(
        self,
        *,
        used_tokens: int = 5000,
        max_tokens: int = 5000,
        window_seconds: int = 2592000,
    ):
        self.attempts = 0
        self.used_tokens = used_tokens
        self.max_tokens = max_tokens
        self.window_seconds = window_seconds

    async def stream_with_tools(
        self,
        messages: list[Message],
        system=None,
        tools: list[ToolDef] | None = None,
        super_thinking: bool = False,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        self.attempts += 1
        raise CitadelQuotaExhausted(
            "engine eng-x used 5000/5000 tokens in last 2592000s",
            used_tokens=self.used_tokens,
            max_tokens=self.max_tokens,
            window_seconds=self.window_seconds,
        )
        yield StreamEvent(type="text", text="")  # pragma: no cover

    async def generate(self, messages, system=""):
        return "summary"


def tools_with_noop():
    """One trivial tool so the loop takes the stream_with_tools path (tools>0),
    matching production where tools are always wired. Never invoked here."""
    from src.utility_directory.tool_runtime import (
        ToolDef as RuntimeToolDef,
        ToolRegistry,
    )

    reg = ToolRegistry()

    async def noop_handler(params):
        return "ok"

    reg.register(
        RuntimeToolDef(
            name="noop",
            description="A no-op tool (never called).",
            input_schema={"type": "object", "properties": {}},
            handler=noop_handler,
        )
    )
    return reg


async def make_thread(db, adapter, emitter, settings):
    memory = create_working_memory("sess-quota-loop", db=db)
    return ExecutionThread(
        adapter=adapter,
        emitter=emitter,
        memory=memory,
        tools=tools_with_noop(),
        settings=settings,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("persistent_retry", [False, True])
async def test_quota_exhausted_is_single_attempt_no_retry(db, persistent_retry):
    """One upstream attempt, no retry, the typed exception re-raised — under
    BOTH persistent_retry settings. The True case is the dangerous one: that is
    the mode the bug would turn into a 6h spin if status_code ever reappeared."""
    settings = Settings()
    settings.persistent_retry = persistent_retry
    adapter = QuotaExhaustedAdapter()
    emitter = EventEmitter()
    th = await make_thread(db, adapter, emitter, settings)

    options = RequestOptions(query_source="foreground")
    with pytest.raises(CitadelQuotaExhausted) as ei:
        await th.agent_loop("hi", options=options)

    assert adapter.attempts == 1
    assert ei.value.window_seconds == 2592000
    assert ei.value.max_tokens == 5000


@pytest.mark.asyncio
async def test_quota_exhausted_background_source_also_single_attempt(db):
    """A background query_source must not change the count either — a non-
    foreground terminal is still one shot, never retried."""
    settings = Settings()
    settings.persistent_retry = True
    adapter = QuotaExhaustedAdapter()
    emitter = EventEmitter()
    th = await make_thread(db, adapter, emitter, settings)

    options = RequestOptions(query_source="background")
    with pytest.raises(CitadelQuotaExhausted):
        await th.agent_loop("hi", options=options)

    assert adapter.attempts == 1


def test_quota_exhausted_maps_to_single_structured_error_block():
    """The product-layer half of the contract: the propagated exception becomes
    exactly one ``quota_exhausted`` error block (HTTP 429, non-retryable), with
    the window/used/max details — the same ``error_from_exception`` path
    server.py runs to synthesize the terminal SSE error."""
    from src.errors import ErrorCode, error_from_exception

    exc = CitadelQuotaExhausted(
        "engine eng-x used 5000/5000 tokens in last 2592000s",
        used_tokens=5000,
        max_tokens=5000,
        window_seconds=2592000,
    )
    err = error_from_exception(exc)
    assert err.code == ErrorCode.QUOTA_EXHAUSTED
    assert err.http_status == 429
    assert err.retryable is False
    block = err.to_dict()["error"]
    assert block["code"] == "quota_exhausted"
    assert block["details"]["window_seconds"] == 2592000
