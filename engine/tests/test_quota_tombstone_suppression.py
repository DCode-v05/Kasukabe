"""Terminal quota/budget/suspension leave NO tombstone in conversation history.

On a runtime error the agent loop writes an ``err_tombstone`` — a synthetic
``[Runtime error: <exc>]`` entry — into the message history before re-raising
(thread.py:4394-4398). That is correct for opaque mid-turn failures: the next
turn needs every orphaned tool_use closed, and the raw text is useful debugging
breadcrumb.

It is WRONG for the three terminal citadel conditions (quota / budget /
subscription suspended). server.py already emits a clean, structured SSE error
block for those (``quota_exhausted`` / ``budget_exhausted`` /
``subscription_suspended`` with display-ready details). Persisting the raw
internal exception string on top of that double-writes the failure and leaks an
internal message into the durable transcript. For these three, the structured
block must be the ONLY artifact — the loop must skip the tombstone.

These drive the REAL ``ExecutionThread.agent_loop`` with a FakeAdapter that
raises the relevant typed exception. ``generate_tombstone_results`` is wrapped
with a counting spy at its definition site (the exact symbol agent_loop imports
at call time) so we can assert it is NOT invoked on the terminal path — while a
generic RuntimeError control still trips it, proving the spy works and the
suppression is specific to the typed terminals.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest

import src.engine_core.context_engineering as ce
from src.config import Settings
from src.engine_core.events import EventEmitter
from src.engine_core.schema import RequestOptions
from src.engine_core.thread import ExecutionThread
from src.herald.provider import (
    CitadelBudgetExhausted,
    CitadelQuotaExhausted,
    CitadelSubscriptionSuspended,
)
from src.herald.types import Message, StreamEvent, ToolDef
from src.memory.memory_factory import create_working_memory


class RaisingAdapter:
    """Raises a caller-supplied exception from the streaming entry point."""

    def __init__(self, exc: Exception):
        self.exc = exc
        self.attempts = 0

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
        raise self.exc
        yield StreamEvent(type="text", text="")  # pragma: no cover

    async def generate(self, messages, system=""):
        return "summary"


def tools_with_noop():
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
    memory = create_working_memory("sess-tombstone", db=db)
    return ExecutionThread(
        adapter=adapter,
        emitter=emitter,
        memory=memory,
        tools=tools_with_noop(),
        settings=settings,
    )


@pytest.fixture
def tombstone_spy(monkeypatch):
    """Wrap generate_tombstone_results at its module so every call is counted,
    while preserving its real behavior (so non-terminal paths still work)."""
    calls: list[str] = []
    real = ce.generate_tombstone_results

    def spy(messages, reason):
        calls.append(reason)
        return real(messages, reason)

    monkeypatch.setattr(ce, "generate_tombstone_results", spy)
    return calls


TERMINAL_EXCEPTIONS = [
    CitadelQuotaExhausted(
        "engine used 5000/5000", used_tokens=5000, max_tokens=5000, window_seconds=2592000
    ),
    CitadelBudgetExhausted(
        "spent 12.50/10.00", spent_usd=12.5, budget_usd=10.0, window_seconds=2592000
    ),
    CitadelSubscriptionSuspended("suspended", subscription_id="sub_123", status="suspended"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", TERMINAL_EXCEPTIONS, ids=lambda e: type(e).__name__)
async def test_terminal_error_writes_no_tombstone(db, tombstone_spy, exc):
    """No ``[Runtime error: ...]`` tombstone for a terminal citadel error — the
    structured block stands alone. The spy records every tombstone reason; none
    of them may be the runtime-error tombstone for this terminal exception."""
    settings = Settings()
    adapter = RaisingAdapter(exc)
    th = await make_thread(db, adapter, EventEmitter(), settings)

    with pytest.raises(type(exc)):
        await th.agent_loop("hi", options=RequestOptions(query_source="foreground"))

    runtime_tombstones = [r for r in tombstone_spy if r.startswith("[Runtime error:")]
    assert runtime_tombstones == [], (
        f"terminal {type(exc).__name__} produced runtime tombstone(s): {runtime_tombstones}"
    )


@pytest.mark.asyncio
async def test_generic_runtime_error_still_writes_tombstone(db, tombstone_spy):
    """Control: a non-terminal RuntimeError MUST still hit the tombstone path —
    proving the suppression above is specific to the typed terminals and the spy
    is wired to the live call site (a false-green guard)."""
    settings = Settings()
    adapter = RaisingAdapter(RuntimeError("upstream exploded"))
    th = await make_thread(db, adapter, EventEmitter(), settings)

    with pytest.raises(RuntimeError):
        await th.agent_loop("hi", options=RequestOptions(query_source="foreground"))

    runtime_tombstones = [r for r in tombstone_spy if r.startswith("[Runtime error:")]
    assert runtime_tombstones, "generic RuntimeError should still reach the tombstone path"
    assert "upstream exploded" in runtime_tombstones[0]
