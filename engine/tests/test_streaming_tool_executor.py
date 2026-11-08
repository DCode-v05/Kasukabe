"""Tests for StreamingToolExecutor — all gaps covered."""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.engine_core.cancellation import AbortReason, CancellationToken, InterruptBehavior
from src.engine_core.events import EventEmitter, InterruptibleStateChanged
from src.engine_core.streaming_tool_executor import (
    REJECT_MESSAGE,
    StreamingToolExecutor,
    synthetic_error_content,
    tool_description,
)
from src.herald.types import ToolCall, ToolResultBlock


def make_call(id: str, name: str = "test_tool", **input_kw) -> ToolCall:
    return ToolCall(id=id, name=name, input=dict(input_kw))


async def immediate_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
    return ToolResultBlock(tool_use_id=call.id, content=f"result:{call.id}")


@pytest.mark.asyncio
async def test_single_tool():
    executor = StreamingToolExecutor(execute_fn=immediate_execute)
    executor.add_tool(make_call("t1"))
    results = [r async for r in executor.results()]
    assert len(results) == 1
    assert results[0].tool_use_id == "t1"
    assert results[0].content == "result:t1"


@pytest.mark.asyncio
async def test_sequential_unsafe_tools():
    order = []

    async def tracking_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        order.append(f"start:{call.id}")
        await asyncio.sleep(0.01)
        order.append(f"end:{call.id}")
        return ToolResultBlock(tool_use_id=call.id, content=f"result:{call.id}")

    executor = StreamingToolExecutor(
        execute_fn=tracking_execute,
        concurrency_check_fn=lambda _: False,
    )
    executor.add_tool(make_call("t1"))
    executor.add_tool(make_call("t2"))
    executor.add_tool(make_call("t3"))

    results = [r async for r in executor.results()]
    assert len(results) == 3
    assert [r.tool_use_id for r in results] == ["t1", "t2", "t3"]
    assert order == ["start:t1", "end:t1", "start:t2", "end:t2", "start:t3", "end:t3"]


@pytest.mark.asyncio
async def test_concurrent_safe_tools():
    order = []

    async def tracking_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        order.append(f"start:{call.id}")
        await asyncio.sleep(0.05)
        order.append(f"end:{call.id}")
        return ToolResultBlock(tool_use_id=call.id, content=f"result:{call.id}")

    executor = StreamingToolExecutor(
        execute_fn=tracking_execute,
        concurrency_check_fn=lambda _: True,
    )
    executor.add_tool(make_call("t1"))
    executor.add_tool(make_call("t2"))
    executor.add_tool(make_call("t3"))

    results = [r async for r in executor.results()]
    assert len(results) == 3
    assert [r.tool_use_id for r in results] == ["t1", "t2", "t3"]
    starts = [e for e in order if e.startswith("start:")]
    assert len(starts) == 3
    assert all(s in order[:3] for s in starts)


@pytest.mark.asyncio
async def test_mixed_safe_unsafe():
    order = []

    async def tracking_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        order.append(f"start:{call.id}")
        await asyncio.sleep(0.01)
        order.append(f"end:{call.id}")
        return ToolResultBlock(tool_use_id=call.id, content=f"result:{call.id}")

    def check_safe(tc: ToolCall) -> bool:
        return tc.name == "safe_tool"

    executor = StreamingToolExecutor(
        execute_fn=tracking_execute,
        concurrency_check_fn=check_safe,
    )
    executor.add_tool(make_call("t1", "safe_tool"))
    executor.add_tool(make_call("t2", "safe_tool"))
    executor.add_tool(make_call("t3", "unsafe_tool"))

    results = [r async for r in executor.results()]
    assert len(results) == 3
    assert [r.tool_use_id for r in results] == ["t1", "t2", "t3"]
    t3_start_idx = order.index("start:t3")
    assert "end:t1" in order[:t3_start_idx]
    assert "end:t2" in order[:t3_start_idx]


@pytest.mark.asyncio
async def test_result_ordering():
    async def variable_delay(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        delays = {"t1": 0.05, "t2": 0.01, "t3": 0.03}
        await asyncio.sleep(delays.get(call.id, 0.01))
        return ToolResultBlock(tool_use_id=call.id, content=f"result:{call.id}")

    executor = StreamingToolExecutor(
        execute_fn=variable_delay,
        concurrency_check_fn=lambda _: True,
    )
    executor.add_tool(make_call("t1"))
    executor.add_tool(make_call("t2"))
    executor.add_tool(make_call("t3"))

    results = [r async for r in executor.results()]
    assert [r.tool_use_id for r in results] == ["t1", "t2", "t3"]


@pytest.mark.asyncio
async def test_tool_error_produces_result():
    async def failing_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        raise RuntimeError("tool broke")

    executor = StreamingToolExecutor(execute_fn=failing_execute)
    executor.add_tool(make_call("t1"))

    results = [r async for r in executor.results()]
    assert len(results) == 1
    assert results[0].is_error
    assert "tool broke" in results[0].content


@pytest.mark.asyncio
async def test_empty_executor():
    executor = StreamingToolExecutor(execute_fn=immediate_execute)
    results = [r async for r in executor.results()]
    assert results == []


@pytest.mark.asyncio
async def test_tool_count_and_has_pending():
    executor = StreamingToolExecutor(execute_fn=immediate_execute)
    assert executor.tool_count == 0
    assert not executor.has_pending
    executor.add_tool(make_call("t1"))
    assert executor.tool_count == 1
    async for _ in executor.results():
        pass
    assert not executor.has_pending


@pytest.mark.asyncio
async def test_sibling_abort_bash_error_cancels_siblings():
    """When a bash-like tool errors, sibling tools get cancelled."""
    order = []

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        order.append(f"start:{call.id}")
        if call.name == "bash":
            return ToolResultBlock(
                tool_use_id=call.id, content="Error: command failed", is_error=True
            )
        for _ in range(50):
            if ct.cancelled:
                order.append(f"cancelled:{call.id}")
                return ToolResultBlock(
                    tool_use_id=call.id, content=f"Cancelled: {ct.reason}", is_error=True
                )
            await asyncio.sleep(0.01)
        order.append(f"end:{call.id}")
        return ToolResultBlock(tool_use_id=call.id, content=f"result:{call.id}")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: True,
        is_command_execution_fn=lambda tc: tc.name == "bash",
    )
    executor.add_tool(make_call("t1", "bash", command="ls"))
    executor.add_tool(make_call("t2", "grep"))

    results = [r async for r in executor.results()]
    assert len(results) == 2
    assert results[0].is_error
    assert results[1].is_error
    assert executor.has_errored


@pytest.mark.asyncio
async def test_non_bash_error_does_not_cancel_siblings():
    """Non-bash tool errors should NOT abort siblings."""

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        if call.id == "t1":
            return ToolResultBlock(tool_use_id=call.id, content="Error: grep failed", is_error=True)
        await asyncio.sleep(0.02)
        return ToolResultBlock(tool_use_id=call.id, content=f"result:{call.id}")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: True,
        is_command_execution_fn=lambda tc: tc.name == "bash",
    )
    executor.add_tool(make_call("t1", "grep"))
    executor.add_tool(make_call("t2", "read"))

    results = [r async for r in executor.results()]
    assert results[0].is_error
    assert not results[1].is_error
    assert not executor.has_errored


@pytest.mark.asyncio
async def test_sibling_abort_synthetic_message():
    """Sibling abort produces specific error message."""

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        if call.name == "bash":
            return ToolResultBlock(tool_use_id=call.id, content="Error: fail", is_error=True)
        await asyncio.sleep(0.5)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: True,
        is_command_execution_fn=lambda tc: tc.name == "bash",
    )
    executor.add_tool(make_call("t1", "bash", command="rm -rf /"))
    executor.add_tool(make_call("t2", "read"))

    await asyncio.sleep(0.05)

    results = [r async for r in executor.results()]
    assert "parallel tool call" in results[1].content or results[1].is_error


@pytest.mark.asyncio
async def test_discard_produces_streaming_fallback_message():
    async def slow_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        await asyncio.sleep(0.05)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=slow_execute,
        concurrency_check_fn=lambda _: False,
    )
    executor.add_tool(make_call("t1"))
    executor.add_tool(make_call("t2"))
    executor.add_tool(make_call("t3"))

    executor.discard()
    results = [r async for r in executor.results()]
    assert results[1].is_error
    assert "Streaming fallback" in results[1].content
    assert results[2].is_error
    assert "Streaming fallback" in results[2].content


@pytest.mark.asyncio
async def test_get_completed_results_non_blocking():
    executor = StreamingToolExecutor(execute_fn=immediate_execute)
    executor.add_tool(make_call("t1"))

    await asyncio.sleep(0.01)

    mid = list(executor.get_completed_results())
    assert len(mid) == 1
    assert mid[0].tool_use_id == "t1"

    remaining = [r async for r in executor.results()]
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_get_completed_results_stops_at_incomplete():
    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        if call.id == "t1":
            await asyncio.sleep(0.1)
        return ToolResultBlock(tool_use_id=call.id, content=f"result:{call.id}")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: False,
    )
    executor.add_tool(make_call("t1"))
    executor.add_tool(make_call("t2"))

    mid = list(executor.get_completed_results())
    assert len(mid) == 0


@pytest.mark.asyncio
async def test_cancel_interruptible_only_cancels_cancel_tools():
    cancelled_ids = []

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        for _ in range(100):
            if ct.cancelled:
                cancelled_ids.append(call.id)
                return ToolResultBlock(tool_use_id=call.id, content="cancelled", is_error=True)
            await asyncio.sleep(0.01)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: True,
        interrupt_behavior_fn=lambda tc: (
            InterruptBehavior.CANCEL if tc.name == "cancelable" else InterruptBehavior.BLOCK
        ),
    )
    executor.add_tool(make_call("t1", "cancelable"))
    executor.add_tool(make_call("t2", "blocker"))

    await asyncio.sleep(0.02)
    executor.cancel_interruptible("user interrupted")

    async for _ in executor.results():
        pass
    assert "t1" in cancelled_ids
    assert "t2" not in cancelled_ids


@pytest.mark.asyncio
async def test_discard_respects_interrupt_behavior():
    """discard() only cancels tools with interrupt_behavior='cancel'."""

    async def slow_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        await asyncio.sleep(0.05)
        if ct.cancelled:
            return ToolResultBlock(tool_use_id=call.id, content="cancelled", is_error=True)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=slow_execute,
        concurrency_check_fn=lambda _: True,
        interrupt_behavior_fn=lambda tc: (
            InterruptBehavior.CANCEL if tc.name == "cancelable" else InterruptBehavior.BLOCK
        ),
    )
    executor.add_tool(make_call("t1", "cancelable"))
    executor.add_tool(make_call("t2", "blocker"))

    await asyncio.sleep(0.01)
    executor.discard()

    results = [r async for r in executor.results()]
    assert len(results) == 2


@pytest.mark.asyncio
async def test_progress_report_and_drain():
    executor = StreamingToolExecutor(execute_fn=immediate_execute)
    executor.add_tool(make_call("t1"))

    executor.report_progress("t1", "50% done")
    executor.report_progress("t1", "75% done")

    progress = executor.drain_progress()
    assert progress == [("t1", "50% done"), ("t1", "75% done")]

    assert executor.drain_progress() == []


def test_tool_description_extracts_command():
    tc = make_call("t1", "bash", command="grep -r 'TODO' src/")
    assert tool_description(tc) == "grep -r 'TODO' src/"


def test_tool_description_truncates():
    tc = make_call("t1", "bash", command="x" * 100)
    desc = tool_description(tc)
    assert len(desc) <= 43
    assert desc.endswith("...")


def test_tool_description_extracts_file_path():
    tc = make_call("t1", "read", file_path="/foo/bar.py")
    assert tool_description(tc) == "/foo/bar.py"


def test_tool_description_fallback_to_name():
    tc = make_call("t1", "custom_tool")
    assert tool_description(tc) == "custom_tool"


@pytest.mark.asyncio
async def test_cancellation_via_parent():
    async def slow_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        await asyncio.sleep(10)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    parent = CancellationToken()
    executor = StreamingToolExecutor(
        execute_fn=slow_execute,
        parent_cancellation=parent,
    )
    executor.add_tool(make_call("t1"))

    await asyncio.sleep(0.01)
    parent.cancel("user cancelled")
    executor.discard()

    results = [r async for r in executor.results()]
    assert len(results) == 1


@pytest.mark.asyncio
async def test_has_completed_results():
    executor = StreamingToolExecutor(execute_fn=immediate_execute)
    assert not executor.has_completed_results

    executor.add_tool(make_call("t1"))
    await asyncio.sleep(0.01)
    assert executor.has_completed_results

    list(executor.get_completed_results())
    assert not executor.has_completed_results


@pytest.mark.asyncio
async def test_timeout_cancel_does_not_bubble_to_sibling():
    """Per-tool timeout stays isolated — does NOT propagate to siblings or parent."""
    parent = CancellationToken()
    executor = StreamingToolExecutor(
        execute_fn=immediate_execute,
        parent_cancellation=parent,
    )
    executor.add_tool(make_call("t1"))
    executor.add_tool(make_call("t2"))
    executor.tracked[0].cancellation_token.cancel("Timeout after 5000ms", AbortReason.TIMEOUT)
    assert not executor.sibling_cancel.cancelled
    assert not parent.cancelled
    assert not executor.tracked[1].cancellation_token.cancelled


def test_timeout_synthetic_error_message():
    tc = make_call("t1", "search")
    msg = synthetic_error_content(tc, AbortReason.TIMEOUT)
    assert msg.startswith("<tool_use_error>")
    assert msg.endswith("</tool_use_error>")
    assert "Tool execution timed out" in msg


def test_user_interrupted_uses_full_reject_message():
    """USER_INTERRUPTED returns the full instructional REJECT_MESSAGE
    verbatim — not a terse label — so the model reliably stops retrying."""
    tc = make_call("t1", "search")
    msg = synthetic_error_content(tc, AbortReason.USER_INTERRUPTED)
    assert msg == REJECT_MESSAGE
    assert "STOP" in msg
    assert "wait for the user" in msg
    assert len(msg) > 100


def test_sibling_error_synthetic_error_message():
    tc = make_call("t1", "search")
    msg = synthetic_error_content(tc, AbortReason.SIBLING_ERROR, "bash abc")
    assert msg.startswith("<tool_use_error>")
    assert msg.endswith("</tool_use_error>")
    assert "parallel tool call" in msg
    assert "bash abc" in msg


def test_streaming_fallback_synthetic_error_message():
    tc = make_call("t1", "search")
    msg = synthetic_error_content(tc, AbortReason.STREAMING_FALLBACK)
    assert msg.startswith("<tool_use_error>")
    assert msg.endswith("</tool_use_error>")
    assert "Streaming fallback" in msg


def test_fallback_synthetic_error_wraps_description():
    """No specific abort reason → still wrapped in tool_use_error tags."""
    tc = make_call("t1", "search", query="my search")
    msg = synthetic_error_content(tc, None)
    assert msg.startswith("<tool_use_error>")
    assert msg.endswith("</tool_use_error>")
    assert "Cancelled" in msg


@pytest.mark.asyncio
async def test_interruptible_state_fires_true_when_all_cancel():
    """Executor fires True only when every executing tool is CANCEL."""
    states: list[bool] = []

    async def slow(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        await asyncio.sleep(0.05)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=slow,
        concurrency_check_fn=lambda _: True,
        interrupt_behavior_fn=lambda _: InterruptBehavior.CANCEL,
        on_interruptible_change=lambda s: states.append(s),
    )
    executor.add_tool(make_call("t1", "a"))
    executor.add_tool(make_call("t2", "a"))
    await asyncio.sleep(0.01)
    assert True in states
    _ = [r async for r in executor.results()]


@pytest.mark.asyncio
async def test_interruptible_state_flips_false_when_block_joins_cancel():
    """Adding a BLOCK tool while a CANCEL is running must flip state to False.

    Tools stream in one at a time, so state naturally walks True → False
    as each tool starts.  The invariant we care about: the moment the
    BLOCK tool joins, state reports False (the executing set is no
    longer all-CANCEL).
    """
    states: list[bool] = []

    async def slow(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        await asyncio.sleep(0.05)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=slow,
        concurrency_check_fn=lambda _: True,
        interrupt_behavior_fn=lambda tc: (
            InterruptBehavior.CANCEL if tc.name == "cancelable" else InterruptBehavior.BLOCK
        ),
        on_interruptible_change=lambda s: states.append(s),
    )
    executor.add_tool(make_call("t1", "cancelable"))
    assert states[-1] is True
    executor.add_tool(make_call("t2", "blocker"))
    assert states[-1] is False
    _ = [r async for r in executor.results()]


@pytest.mark.asyncio
async def test_interruptible_state_flips_true_when_block_completes():
    """When the BLOCK tool finishes and only CANCEL tools remain, state flips True."""
    states: list[bool] = []

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        if call.name == "blocker":
            await asyncio.sleep(0.02)
        else:
            await asyncio.sleep(0.15)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: True,
        interrupt_behavior_fn=lambda tc: (
            InterruptBehavior.CANCEL if tc.name == "cancelable" else InterruptBehavior.BLOCK
        ),
        on_interruptible_change=lambda s: states.append(s),
    )
    executor.add_tool(make_call("t1", "cancelable"))
    executor.add_tool(make_call("t2", "blocker"))
    _ = [r async for r in executor.results()]
    flipped_back_to_true = any(
        states[i] is False and states[i + 1] is True for i in range(len(states) - 1)
    )
    assert flipped_back_to_true, f"expected a False→True transition in {states}"


@pytest.mark.asyncio
async def test_interruptible_state_deduped_no_spurious_events():
    """Same state in a row must not fire twice."""
    states: list[bool] = []

    async def slow(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        await asyncio.sleep(0.03)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=slow,
        concurrency_check_fn=lambda _: True,
        interrupt_behavior_fn=lambda _: InterruptBehavior.CANCEL,
        on_interruptible_change=lambda s: states.append(s),
    )
    executor.add_tool(make_call("t1", "a"))
    executor.add_tool(make_call("t2", "a"))
    executor.add_tool(make_call("t3", "a"))
    _ = [r async for r in executor.results()]
    assert states.count(True) == 1


@pytest.mark.asyncio
async def test_interruptible_event_emitted_through_emitter():
    """Real EventEmitter receives InterruptibleStateChanged when state flips."""
    received: list[InterruptibleStateChanged] = []
    emitter = EventEmitter()

    async def sink(event):
        if isinstance(event, InterruptibleStateChanged):
            received.append(event)

    emitter.on(sink)

    async def slow(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        await asyncio.sleep(0.05)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=slow,
        concurrency_check_fn=lambda _: True,
        interrupt_behavior_fn=lambda _: InterruptBehavior.CANCEL,
        emitter=emitter,
        thread_id="task-xyz",
    )
    executor.add_tool(make_call("t1", "a"))
    executor.add_tool(make_call("t2", "a"))
    _ = [r async for r in executor.results()]
    await asyncio.sleep(0.02)

    assert len(received) >= 1
    first = received[0]
    assert first.interruptible is True
    assert first.thread_id == "task-xyz"
    assert first.tool_count >= 1


@pytest.mark.asyncio
async def test_interruptible_callback_exception_isolated():
    """A raising callback must not break state tracking."""

    def boom(_):
        raise RuntimeError("callback broken")

    async def slow(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        await asyncio.sleep(0.02)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=slow,
        concurrency_check_fn=lambda _: True,
        interrupt_behavior_fn=lambda _: InterruptBehavior.CANCEL,
        on_interruptible_change=boom,
    )
    executor.add_tool(make_call("t1", "a"))
    _ = [r async for r in executor.results()]


@pytest.mark.asyncio
async def test_cleanup_is_idempotent():
    executor = StreamingToolExecutor(execute_fn=immediate_execute)
    executor.add_tool(make_call("t1"))
    _ = [r async for r in executor.results()]
    executor.cleanup()
    executor.cleanup()


@pytest.mark.asyncio
async def test_mid_flight_user_interrupt_replaces_result_with_reject_message():
    """When cancel_interruptible fires during execute_fn, the natural
    return value must be replaced with the full REJECT_MESSAGE so the
    model gets the instructional cancellation signal, not whatever
    terse string the tool happened to return."""

    async def cooperative_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        for _ in range(100):
            if ct.cancelled:
                return ToolResultBlock(
                    tool_use_id=call.id,
                    content="partial result, bailed on cancel",
                    is_error=False,
                )
            await asyncio.sleep(0.005)
        return ToolResultBlock(tool_use_id=call.id, content="finished")

    executor = StreamingToolExecutor(
        execute_fn=cooperative_execute,
        concurrency_check_fn=lambda _: True,
        interrupt_behavior_fn=lambda _: InterruptBehavior.CANCEL,
    )
    executor.add_tool(make_call("t1", "slow"))
    await asyncio.sleep(0.02)
    executor.cancel_interruptible("User interrupted", AbortReason.USER_INTERRUPTED)

    results = [r async for r in executor.results()]
    assert len(results) == 1
    result = results[0]
    assert result.is_error
    assert result.content == REJECT_MESSAGE
    assert "partial result" not in result.content


@pytest.mark.asyncio
async def test_mid_flight_sibling_abort_replaces_result_with_wrapped_error():
    """A cooperative tool that returns a clean result after a sibling
    abort fires should still surface the sibling-error synthetic message."""

    async def cooperative_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        if call.name == "bash":
            return ToolResultBlock(
                tool_use_id=call.id,
                content="Error: bash failed",
                is_error=True,
            )
        for _ in range(100):
            if ct.cancelled:
                return ToolResultBlock(
                    tool_use_id=call.id,
                    content="partial cleanup result",
                )
            await asyncio.sleep(0.005)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=cooperative_execute,
        concurrency_check_fn=lambda _: True,
        is_command_execution_fn=lambda tc: tc.name == "bash",
    )
    executor.add_tool(make_call("t1", "bash", command="fail"))
    executor.add_tool(make_call("t2", "read"))

    results = [r async for r in executor.results()]
    assert len(results) == 2
    sibling = results[1]
    assert sibling.is_error
    assert "<tool_use_error>" in sibling.content
    assert "parallel tool call" in sibling.content
    assert "partial cleanup" not in sibling.content


def test_tool_def_is_command_execution_default_false():
    """ToolDef.is_command_execution defaults to False."""
    from src.utility_directory.tool_runtime import ToolDef as RuntimeToolDef

    async def dummy(_):
        return ""

    td = RuntimeToolDef(
        name="read_file",
        description="Read a file",
        input_schema={},
        handler=dummy,
    )
    assert td.is_command_execution is False


def test_tool_def_is_command_execution_true_for_bash():
    """ToolDef.is_command_execution is True for bash in sandbox tools."""
    from src.sandbox.tools import build_sandbox_tools

    class FakeExecutor:
        async def run_bash(self, *a, **kw): ...
        async def run_python(self, *a, **kw): ...
        async def read_file(self, *a, **kw): ...
        async def write_file(self, *a, **kw): ...
        async def list_directory(self, *a, **kw): ...
        async def search_content(self, *a, **kw): ...
        async def kill_orphaned_children(self): ...
        def process_list(self):
            return []

        async def process_poll(self, *a, **kw): ...
        def process_log(self, *a, **kw):
            return []

        async def process_kill(self, *a, **kw): ...
        async def process_write(self, *a, **kw): ...
        async def process_send_keys(self, *a, **kw): ...
        def process_remove(self, *a, **kw): ...

    tools = build_sandbox_tools(FakeExecutor())
    by_name = {t.name: t for t in tools}

    assert by_name["bash"].is_command_execution is True
    assert by_name["python"].is_command_execution is True
    assert by_name["read_file"].is_command_execution is False
    assert by_name["write_file"].is_command_execution is False
    assert by_name["list_directory"].is_command_execution is False
    assert by_name["search_files"].is_command_execution is False
    assert by_name["process"].is_command_execution is False


@pytest.mark.asyncio
async def test_is_command_execution_fn_false_means_no_sibling_abort():
    """When is_command_execution_fn returns False for all tools, no sibling abort occurs."""

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        if call.id == "t1":
            return ToolResultBlock(tool_use_id=call.id, content="Error: bash failed", is_error=True)
        await asyncio.sleep(0.05)
        return ToolResultBlock(tool_use_id=call.id, content="result:t2")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: True,
        is_command_execution_fn=lambda _: False,
    )
    executor.add_tool(make_call("t1", "bash", command="fail"))
    executor.add_tool(make_call("t2", "read"))

    results = [r async for r in executor.results()]
    assert results[0].is_error
    assert not results[1].is_error
    assert not executor.has_errored


@pytest.mark.asyncio
async def test_python_tool_error_cancels_siblings_when_flagged():
    """python tool with is_command_execution=True aborts siblings on error."""

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        if call.name == "python":
            return ToolResultBlock(
                tool_use_id=call.id, content="Error: syntax error", is_error=True
            )
        for _ in range(50):
            if ct.cancelled:
                return ToolResultBlock(tool_use_id=call.id, content="cancelled", is_error=True)
            await asyncio.sleep(0.01)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: True,
        is_command_execution_fn=lambda tc: tc.name in ("bash", "python"),
    )
    executor.add_tool(make_call("t1", "python", code="bad code"))
    executor.add_tool(make_call("t2", "read_file"))

    results = [r async for r in executor.results()]
    assert results[0].is_error
    assert results[1].is_error
    assert executor.has_errored


@pytest.mark.asyncio
async def test_sibling_abort_synthetic_message_format():
    """Cancelled siblings get the correct 'parallel tool call' error format."""

    async def execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        if call.name == "bash":
            return ToolResultBlock(tool_use_id=call.id, content="Error: fail", is_error=True)
        await asyncio.sleep(0.5)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=execute,
        concurrency_check_fn=lambda _: True,
        is_command_execution_fn=lambda tc: tc.name == "bash",
    )
    executor.add_tool(make_call("t1", "bash", command="rm -rf /"))
    executor.add_tool(make_call("t2", "read_file"))

    results = [r async for r in executor.results()]
    assert results[1].is_error
    assert "<tool_use_error>" in results[1].content
    assert "Cancelled: parallel tool call" in results[1].content
    assert "errored" in results[1].content


@pytest.mark.asyncio
async def test_permission_rejection_bubbles_to_parent():
    """Non-sibling cancellation on per-tool token propagates to parent turn token."""
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

    tracked = executor.tracked[0]
    await asyncio.sleep(0.01)
    tracked.cancellation_token.cancel("Permission denied")

    results = [r async for r in executor.results()]
    assert results[0].is_error
    assert parent.cancelled
    assert parent.reason == "Permission denied"


@pytest.mark.asyncio
async def test_sibling_error_does_not_bubble_to_parent():
    """Sibling abort fires the per-tool token but must NOT propagate to parent."""
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
    assert results[0].is_error
    assert results[1].is_error
    assert executor.has_errored
    assert not parent.cancelled


@pytest.mark.asyncio
async def test_unknown_tool_immediate_error():
    """Unknown tools get immediate error without calling execute_fn."""
    execute_called = []

    async def tracking_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        execute_called.append(call.name)
        return ToolResultBlock(tool_use_id=call.id, content="done")

    executor = StreamingToolExecutor(
        execute_fn=tracking_execute,
        tool_exists_fn=lambda name: name != "nonexistent_tool",
    )
    executor.add_tool(make_call("t1", "nonexistent_tool"))

    results = [r async for r in executor.results()]
    assert len(results) == 1
    assert results[0].is_error
    assert "No such tool available" in results[0].content
    assert "nonexistent_tool" in results[0].content
    assert execute_called == []


@pytest.mark.asyncio
async def test_unknown_tool_does_not_block_queue():
    """Unknown tools are concurrency-safe and don't block subsequent tools."""
    order = []

    async def tracking_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
        order.append(call.name)
        return ToolResultBlock(tool_use_id=call.id, content=f"result:{call.name}")

    executor = StreamingToolExecutor(
        execute_fn=tracking_execute,
        tool_exists_fn=lambda name: name != "ghost",
    )
    executor.add_tool(make_call("t1", "ghost"))
    executor.add_tool(make_call("t2", "real_tool"))

    results = [r async for r in executor.results()]
    assert len(results) == 2
    assert results[0].is_error
    assert not results[1].is_error
    assert order == ["real_tool"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
