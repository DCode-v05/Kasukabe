"""A PreToolUse hook's ``updated_input`` must reach the whole call chain.

``pre_tool_hooks.py`` documents that a non-``None`` ``updated_input``
"replaces ``tool_input`` for the remainder of the call chain (permission
check, executor, post-tool hooks)." The permission check honoured it, but
the executor ran on the original input and the post-tool hook context was
built from the original, so a hook that sanitised or rewrote tool input
changed only whether the call was *permitted*, never what actually *ran*.

These drive the REAL agent loop (``ExecutionThread.execute``) with a
scripted adapter that emits one tool call carrying a non-empty input. The
rewrite is therefore observed at the three places it must land: the tool
handler that executes, the post-tool hook context, and the recorded node
trace. Asserting on ``pre_agg`` instead would pass against the old broken
code where the rewrite died at the permission boundary, so every
assertion here reads the value that was actually executed and recorded.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest

from src.config import Settings
from src.engine_core.events import EventEmitter
from src.engine_core.post_tool_hooks import (
    CallablePostToolUseHook,
    PostToolUseHookResult,
)
from src.engine_core.pre_tool_hooks import (
    CallablePreToolUseHook,
    PreToolUseHookResult,
)
from src.engine_core.thread import ExecutionThread
from src.herald.types import Message, StopReason, StreamEvent, ToolCall, ToolDef, Usage
from src.memory.memory_factory import create_working_memory
from src.utility_directory.tool_runtime import ToolDef as RuntimeToolDef, ToolRegistry


class EchoToolThenStopAdapter:
    """Turn 1 emits one ``echo`` call with a rewritable arg; turn 2 stops."""

    def __init__(self) -> None:
        self.turn = 0

    async def stream_with_tools(
        self,
        messages: list[Message],
        system=None,
        tools: list[ToolDef] | None = None,
        super_thinking: bool = False,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        self.turn += 1
        if self.turn == 1:
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id="t1", name="echo", input={"value": "original"}),
            )
            yield StreamEvent(type="stop", stop_reason=StopReason.TOOL_USE)
        else:
            yield StreamEvent(type="text", text="done")
            yield StreamEvent(type="stop", stop_reason=StopReason.END_TURN)
        yield StreamEvent(type="usage", usage=Usage(input_tokens=5, output_tokens=5))

    async def generate(self, messages, system=""):
        return "summary"


def echo_registry(observed_by_tool: list[dict]) -> ToolRegistry:
    """A real tool whose handler records the exact input dict it executed on."""
    reg = ToolRegistry()

    async def echo_handler(params):
        observed_by_tool.append(dict(params))
        return f"echoed:{params.get('value', '')}"

    reg.register(
        RuntimeToolDef(
            name="echo",
            description="Echo the 'value' argument back.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
            handler=echo_handler,
        )
    )
    return reg


@pytest.mark.asyncio
async def test_updated_input_reaches_executor_posthook_and_trace(db):
    observed_by_tool: list[dict] = []
    post_seen: list[dict] = []

    th = ExecutionThread(
        adapter=EchoToolThenStopAdapter(),
        emitter=EventEmitter(),
        memory=create_working_memory("sess-updated-input", db=db),
        tools=echo_registry(observed_by_tool),
        settings=Settings(),
    )

    async def rewrite_hook(inp) -> PreToolUseHookResult:
        # Sanitise/rewrite the tool's argument before it runs.
        return PreToolUseHookResult(updated_input={"value": "rewritten"})

    async def record_post(ctx) -> PostToolUseHookResult:
        post_seen.append(dict(ctx.tool_input))
        return PostToolUseHookResult()

    th.pre_tool_hooks.register("rewrite", CallablePreToolUseHook(rewrite_hook))
    th.post_tool_hooks.register("record", CallablePostToolUseHook(record_post))

    await th.execute("use the tool", thread_id="t-updated-input")

    # (a) The tool that actually executed observed the rewritten argument.
    assert observed_by_tool == [{"value": "rewritten"}]

    # (b) The post-tool hook context carried the rewritten input, not the original.
    assert post_seen == [{"value": "rewritten"}]

    # (c) The recorded node trace input matches what executed.
    assert th.current_node_trace is not None
    echo_calls = [tc for tc in th.current_node_trace.tool_calls if tc.tool == "echo"]
    assert len(echo_calls) == 1
    assert echo_calls[0].input == {"value": "rewritten"}
    assert echo_calls[0].output == "echoed:rewritten"


@pytest.mark.asyncio
async def test_no_updated_input_leaves_original_intact(db):
    """Control: a hook that returns no ``updated_input`` must not alter the call."""
    observed_by_tool: list[dict] = []
    post_seen: list[dict] = []

    th = ExecutionThread(
        adapter=EchoToolThenStopAdapter(),
        emitter=EventEmitter(),
        memory=create_working_memory("sess-no-update", db=db),
        tools=echo_registry(observed_by_tool),
        settings=Settings(),
    )

    async def inert_hook(inp) -> PreToolUseHookResult:
        return PreToolUseHookResult()

    async def record_post(ctx) -> PostToolUseHookResult:
        post_seen.append(dict(ctx.tool_input))
        return PostToolUseHookResult()

    th.pre_tool_hooks.register("inert", CallablePreToolUseHook(inert_hook))
    th.post_tool_hooks.register("record", CallablePostToolUseHook(record_post))

    await th.execute("use the tool", thread_id="t-no-update")

    assert observed_by_tool == [{"value": "original"}]
    assert post_seen == [{"value": "original"}]
    assert th.current_node_trace is not None
    echo_calls = [tc for tc in th.current_node_trace.tool_calls if tc.tool == "echo"]
    assert len(echo_calls) == 1
    assert echo_calls[0].input == {"value": "original"}
