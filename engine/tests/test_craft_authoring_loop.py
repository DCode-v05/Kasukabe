"""M2 — end-to-end through the REAL agent loop (ExecutionThread.execute).

The only substituted dependency is the LLM adapter, replaced with a FakeAdapter
that yields scripted StreamEvents — the same pattern the rest of the suite uses
(test_max_tokens_escalation, etc.). Determinism is required (assert exact axes /
content / ids / awaiting) and the LLM gateway is unreachable in the test
container, so a real model call cannot serve here. Everything else — the loop,
the text branch, the craft parser, the post-loop flush, the craft_gate_var seam —
is the real code path.
"""

from typing import AsyncIterator

import pytest

from src.herald.types import Message, StreamEvent, StopReason, ToolCall, ToolDef, Usage
from src.engine_core.events import EventEmitter, CraftAuthored, TextDelta
from src.engine_core.coordinator import craft_gate_var, craft_wait_var
from src.engine_core.thread import ExecutionThread
from src.memory.memory_factory import create_working_memory
from src.utility_directory.tool_runtime import ToolDef as RuntimeToolDef, ToolRegistry


class FakeAdapter:
    """Yields one scripted text body then a clean stop — no network, no model."""

    def __init__(self, text: str):
        self.text = text

    async def stream_with_tools(
        self,
        messages: list[Message],
        system=None,
        tools: list[ToolDef] | None = None,
        super_thinking: bool = False,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        # Stream the body in two chunks so the cross-delta path is exercised.
        mid = len(self.text) // 2
        yield StreamEvent(type="text", text=self.text[:mid])
        yield StreamEvent(type="text", text=self.text[mid:])
        yield StreamEvent(type="stop", stop_reason=StopReason.END_TURN)
        yield StreamEvent(type="usage", usage=Usage(input_tokens=10, output_tokens=10))

    async def generate(self, messages, system=""):
        return "summary"


CRAFT_TEXT = (
    "Here is a confirm:"
    '<craft surface="inline" format="html" wait="true" key="c" title="C">'
    '<button craft-action="yes">Yes</button></craft>'
    " all set."
)


def tools_with_a_noop():
    """A registry with one trivial tool so the REAL agent_loop path runs
    (tool_count > 0 → stream_with_tools, where the craft parser is wired).
    Production always wires tools, so this mirrors the real path; the FakeAdapter
    never emits a tool_call, so the tool is never invoked."""
    reg = ToolRegistry()

    async def noop_handler(params):
        return "ok"

    reg.register(
        RuntimeToolDef(
            name="noop",
            description="A no-op tool (never called in this test).",
            input_schema={"type": "object", "properties": {}},
            handler=noop_handler,
        )
    )
    return reg


async def make_thread(db, adapter, emitter):
    memory = create_working_memory("sess-craft", db=db)
    return ExecutionThread(
        adapter=adapter,
        emitter=emitter,
        memory=memory,
        tools=tools_with_a_noop(),
    )


def collect(emitter):
    events: list = []

    async def sink(ev):
        events.append(ev)

    emitter.on(sink)
    return events


@pytest.mark.asyncio
async def test_loop_keeps_raw_text_when_gate_off(db):
    emitter = EventEmitter()
    events = collect(emitter)
    th = await make_thread(db, FakeAdapter(CRAFT_TEXT), emitter)

    # gate off (default) → parser never runs → the raw tag streams as plain text.
    await th.execute("hi", thread_id="t-off")

    assert [e for e in events if isinstance(e, CraftAuthored)] == []
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert "<craft" in text and "craft-action" in text


# --- wait-craft halts the turn -------------------------------------------------

CRAFT_ONLY = (
    '<craft surface="inline" format="html" wait="true" key="c" title="C">'
    '<button craft-action="yes">Yes</button></craft>'
)


class ScriptedAdapter:
    """Yields an explicit list of StreamEvents — text, tool_call, stop, usage."""

    def __init__(self, events: list[StreamEvent]):
        self.events = events

    async def stream_with_tools(
        self,
        messages,
        system=None,
        tools=None,
        super_thinking=False,
        max_output_tokens=None,
        skip_cache_write=False,
    ) -> AsyncIterator[StreamEvent]:
        for ev in self.events:
            yield ev

    async def generate(self, messages, system=""):
        return "summary"


def tools_with_flag(flag: dict):
    """A registry whose one tool flips ``flag['called']`` when actually invoked."""
    reg = ToolRegistry()

    async def noop_handler(params):
        flag["called"] = True
        return "ok"

    reg.register(
        RuntimeToolDef(
            name="noop",
            description="Flips a flag when run.",
            input_schema={"type": "object", "properties": {}},
            handler=noop_handler,
        )
    )
    return reg


@pytest.mark.asyncio
async def test_craft_wait_off_runs_post_craft_tool_call(db):
    """craft_wait OFF: no halt → the same post-craft tool call IS consumed and
    runs. Proves the gate is what suppresses it, not some other path."""
    emitter = EventEmitter()
    collect(emitter)
    flag = {"called": False}
    memory = create_working_memory("sess-craft-tool-off", db=db)
    th = ExecutionThread(
        adapter=ScriptedAdapter(
            [
                StreamEvent(type="text", text="Before." + CRAFT_ONLY),
                StreamEvent(type="tool_call", tool_call=ToolCall(id="t1", name="noop", input={})),
                StreamEvent(type="stop", stop_reason=StopReason.END_TURN),
                StreamEvent(type="usage", usage=Usage(input_tokens=10, output_tokens=10)),
            ]
        ),
        emitter=emitter,
        memory=memory,
        tools=tools_with_flag(flag),
    )

    token = craft_gate_var.set(True)
    token_wait = craft_wait_var.set(False)
    try:
        await th.execute("hi", thread_id="t-tool-off")
    finally:
        craft_gate_var.reset(token)
        craft_wait_var.reset(token_wait)

    assert flag["called"] is True  # no halt → the tool ran


# --- craft is persisted as a placeholder, not raw markup -----------------------

CRAFT_NOWAIT = (
    '<craft surface="inline" format="html" wait="false" key="c" title="C">'
    '<button craft-action="yes">Yes</button></craft>'
)


def assistant_text(messages) -> str:
    """All assistant-authored text across a message list (string or block content)."""
    out: list[str] = []
    for m in messages:
        if getattr(m, "role", "") != "assistant":
            continue
        content = getattr(m, "content", "")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            out.extend(getattr(b, "text", "") or "" for b in content)
    return " ".join(out)


