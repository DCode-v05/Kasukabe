"""Aggressive stress tests for sibling abort + bubble-up + unknown tool.

Exercises edge cases not covered by unit tests:
- Race conditions between sibling abort and bubble-up
- Multiple bash errors in same batch
- Mixed unknown + known + erroring tools
- Discard during bubble-up
- Rapid add_tool calls
- cleanup() after various abort states
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.engine_core.cancellation import AbortReason, CancellationToken, InterruptBehavior
from src.engine_core.streaming_tool_executor import (
    StreamingToolExecutor,
)
from src.herald.types import ToolCall, ToolResultBlock


def make_call(id: str, name: str = "test_tool", **input_kw) -> ToolCall:
    return ToolCall(id=id, name=name, input=dict(input_kw))


@pytest.mark.asyncio
async def test_sibling_abort_and_permission_rejection_race():
    """Bash error fires sibling abort. Simultaneously, another tool gets
    permission-rejected. Only one should win. Parent must NOT be cancelled
    (sibling abort wins the race on the parent check)."""
    parent = CancellationToken()

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        if call.name == "bash":
            return ToolResultBlock(tool_use_id=call.id, content="Error: fail", is_error=True)
        for _ in range(100):
            if ct.cancelled:
                return ToolResultBlock(tool_use_id=call.id, content="cancelled", is_error=True)
            await asyncio.sleep(0.01)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: True,
        is_command_execution_fn=lambda tc: tc.name == "bash",
        parent_cancellation=parent,
    )
    executor.add_tool(make_call("t1", "bash", command="fail"))
    executor.add_tool(make_call("t2", "read_file"))
    executor.add_tool(make_call("t3", "grep"))

    results = [r async for r in executor.results()]
    assert len(results) == 3
    assert results[0].is_error
    assert results[1].is_error
    assert results[2].is_error
    assert not parent.cancelled
    assert executor.has_errored


@pytest.mark.asyncio
async def test_multiple_bash_errors_only_first_fires():
    """Two bash tools in same batch both error. Only first fires sibling abort."""
    parent = CancellationToken()

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        if call.name == "bash":
            if call.id == "t2":
                await asyncio.sleep(0.02)
            return ToolResultBlock(tool_use_id=call.id, content="Error: fail", is_error=True)
        for _ in range(100):
            if ct.cancelled:
                return ToolResultBlock(tool_use_id=call.id, content="cancelled", is_error=True)
            await asyncio.sleep(0.01)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: True,
        is_command_execution_fn=lambda tc: tc.name == "bash",
        parent_cancellation=parent,
    )
    executor.add_tool(make_call("t1", "bash", command="first"))
    executor.add_tool(make_call("t2", "bash", command="second"))
    executor.add_tool(make_call("t3", "read_file"))

    results = [r async for r in executor.results()]
    assert len(results) == 3
    assert results[0].is_error
    assert results[1].is_error
    assert results[2].is_error
    assert executor.has_errored
    assert not parent.cancelled


@pytest.mark.asyncio
async def test_unknown_tool_mixed_with_erroring_bash():
    """Unknown tool + bash error + normal tool — all three paths exercised."""
    parent = CancellationToken()

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        if call.name == "bash":
            return ToolResultBlock(tool_use_id=call.id, content="Error: fail", is_error=True)
        for _ in range(100):
            if ct.cancelled:
                return ToolResultBlock(tool_use_id=call.id, content="cancelled", is_error=True)
            await asyncio.sleep(0.01)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: True,
        is_command_execution_fn=lambda tc: tc.name == "bash",
        tool_exists_fn=lambda name: name != "ghost",
        parent_cancellation=parent,
    )
    executor.add_tool(make_call("t1", "ghost"))
    executor.add_tool(make_call("t2", "bash", command="fail"))
    executor.add_tool(make_call("t3", "read_file"))

    results = [r async for r in executor.results()]
    assert len(results) == 3
    assert results[0].is_error
    assert "No such tool available" in results[0].content
    assert results[1].is_error
    assert results[2].is_error
    assert not parent.cancelled


@pytest.mark.asyncio
async def test_all_unknown_tools():
    """All tools unknown — no execute_fn calls, all immediate errors."""
    call_count = 0

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        nonlocal call_count
        call_count += 1
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        tool_exists_fn=lambda _: False,
    )
    for i in range(5):
        executor.add_tool(make_call(f"t{i}", f"ghost_{i}"))

    results = [r async for r in executor.results()]
    assert len(results) == 5
    assert all(r.is_error for r in results)
    assert all("No such tool available" in r.content for r in results)
    assert call_count == 0


@pytest.mark.asyncio
async def test_discard_blocks_bubble_up():
    """discard() sets discarded=True which should prevent bubble-up."""
    parent = CancellationToken()

    async def slow_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        for _ in range(100):
            if ct.cancelled:
                return ToolResultBlock(tool_use_id=call.id, content="cancelled", is_error=True)
            await asyncio.sleep(0.01)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=slow_execute,
        concurrency_check_fn=lambda _: True,
        interrupt_behavior_fn=lambda _: InterruptBehavior.CANCEL,
        parent_cancellation=parent,
    )
    executor.add_tool(make_call("t1"))

    await asyncio.sleep(0.01)
    executor.discard()

    assert not parent.cancelled


@pytest.mark.asyncio
async def test_permission_rejection_cancels_all_siblings_via_parent():
    """Permission rejection on one tool bubbles to parent, which cascades to siblings."""
    parent = CancellationToken()

    async def slow_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        for _ in range(100):
            if ct.cancelled:
                return ToolResultBlock(tool_use_id=call.id, content="cancelled", is_error=True)
            await asyncio.sleep(0.01)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=slow_execute,
        concurrency_check_fn=lambda _: True,
        parent_cancellation=parent,
    )
    executor.add_tool(make_call("t1"))
    executor.add_tool(make_call("t2"))
    executor.add_tool(make_call("t3"))

    await asyncio.sleep(0.02)

    executor.tracked[1].cancellation_token.cancel("Permission denied")

    results = [r async for r in executor.results()]
    assert len(results) == 3
    assert parent.cancelled
    assert parent.reason == "Permission denied"
    assert all(r.is_error for r in results)


@pytest.mark.asyncio
async def test_cleanup_after_sibling_abort():
    """cleanup() after sibling abort is idempotent and doesn't raise."""
    parent = CancellationToken()

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        if call.name == "bash":
            return ToolResultBlock(tool_use_id=call.id, content="Error: fail", is_error=True)
        for _ in range(50):
            if ct.cancelled:
                return ToolResultBlock(tool_use_id=call.id, content="cancelled", is_error=True)
            await asyncio.sleep(0.01)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: True,
        is_command_execution_fn=lambda tc: tc.name == "bash",
        parent_cancellation=parent,
    )
    executor.add_tool(make_call("t1", "bash", command="fail"))
    executor.add_tool(make_call("t2", "read_file"))

    results = [r async for r in executor.results()]
    assert len(results) == 2

    executor.cleanup()
    executor.cleanup()


@pytest.mark.asyncio
async def test_cleanup_after_permission_rejection():
    """cleanup() after bubble-up is idempotent."""
    parent = CancellationToken()

    async def slow_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        for _ in range(50):
            if ct.cancelled:
                return ToolResultBlock(tool_use_id=call.id, content="cancelled", is_error=True)
            await asyncio.sleep(0.01)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=slow_execute,
        concurrency_check_fn=lambda _: True,
        parent_cancellation=parent,
    )
    executor.add_tool(make_call("t1"))

    await asyncio.sleep(0.01)
    executor.tracked[0].cancellation_token.cancel("Permission denied")

    async for _ in executor.results():
        pass
    assert parent.cancelled

    executor.cleanup()
    executor.cleanup()


@pytest.mark.asyncio
async def test_rapid_add_20_tools_with_first_bash_error():
    """Rapidly add 20 tools. First is bash error. Rest should all get cancelled."""
    parent = CancellationToken()

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        if call.name == "bash":
            return ToolResultBlock(tool_use_id=call.id, content="Error: fail", is_error=True)
        for _ in range(100):
            if ct.cancelled:
                return ToolResultBlock(tool_use_id=call.id, content="cancelled", is_error=True)
            await asyncio.sleep(0.01)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: True,
        is_command_execution_fn=lambda tc: tc.name == "bash",
        parent_cancellation=parent,
    )

    executor.add_tool(make_call("t0", "bash", command="fail"))
    for i in range(1, 20):
        executor.add_tool(make_call(f"t{i}", "read_file"))

    results = [r async for r in executor.results()]
    assert len(results) == 20
    assert results[0].is_error
    assert all(r.is_error for r in results[1:])
    assert not parent.cancelled


@pytest.mark.asyncio
async def test_unknown_tool_error_has_xml_tags():
    """Unknown tool error is wrapped in <tool_use_error> tags."""
    executor = StreamingToolExecutor(
        execute_fn=lambda c, ct: None,
        tool_exists_fn=lambda _: False,
    )
    executor.add_tool(make_call("t1", "magic_tool"))

    results = [r async for r in executor.results()]
    assert "<tool_use_error>" in results[0].content
    assert "</tool_use_error>" in results[0].content
    assert "magic_tool" in results[0].content


@pytest.mark.asyncio
async def test_bubble_up_preserves_abort_reason_none():
    """When token is cancelled without an AbortReason, bubble-up still works
    and parent gets None as abort_reason."""
    parent = CancellationToken()

    async def slow_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        for _ in range(50):
            if ct.cancelled:
                return ToolResultBlock(tool_use_id=call.id, content="cancelled", is_error=True)
            await asyncio.sleep(0.01)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=slow_execute,
        parent_cancellation=parent,
    )
    executor.add_tool(make_call("t1"))

    await asyncio.sleep(0.01)
    executor.tracked[0].cancellation_token.cancel("some reason")

    async for _ in executor.results():
        pass
    assert parent.cancelled
    assert parent.reason == "some reason"
    assert parent.abort_reason is None


@pytest.mark.asyncio
async def test_timeout_does_not_bubble():
    """Per-tool timeout via CombinedCancellationToken must NOT bubble."""
    parent = CancellationToken()

    async def slow_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        for _ in range(200):
            if ct.cancelled:
                return ToolResultBlock(tool_use_id=call.id, content="timed out", is_error=True)
            await asyncio.sleep(0.01)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=slow_execute,
        parent_cancellation=parent,
    )
    executor.add_tool(make_call("t1"))
    await asyncio.sleep(0.01)
    executor.tracked[0].cancellation_token.cancel("Timeout", AbortReason.TIMEOUT)

    results = [r async for r in executor.results()]
    assert results[0].is_error
    assert not parent.cancelled


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
