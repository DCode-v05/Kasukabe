"""M6 — telemetry levels deliver the documented block sets, end to end.

`test_gate_filter.py` covers the `passes_gates` truth table in isolation. This
file validates the *pipeline*: real events fold to the right block type
(`BlockSerializer.fold`) and then survive the per-level gate — in particular the
hook events wired into FOLD at M6, which only surface at `telemetry="full"`.
"""

from src.engine_core.blocks import BlockSerializer, Gates, passes_gates
from src.engine_core.events import (
    TextDelta,
    ThreadLifecycle,
    ExecutionSummary,
    PreToolUseHookExecuted,
    PostToolUseHookExecuted,
    StopHookExecuted,
    MemoryWriteEvent,
    CompactionEvent,
)


def fold(event):
    return BlockSerializer("r1").fold(event)


def test_hook_events_fold_to_hook_blocks():
    """The three *HookExecuted events wired into FOLD at M6 become 'hook' blocks
    and preserve their owning thread_id."""
    pre = fold(
        PreToolUseHookExecuted(
            thread_id="t1",
            tool_name="bash",
            tool_use_id="tu1",
            denied=False,
            allowed=True,
            prevented=False,
            hook_outcomes=[],
        )
    )
    post = fold(
        PostToolUseHookExecuted(
            thread_id="t1",
            tool_name="bash",
            tool_use_id="tu1",
            prevented=False,
            context_count=0,
            hook_outcomes=[],
        )
    )
    stop = fold(
        StopHookExecuted(
            thread_id="t1",
            turn_number=1,
            trigger="after_end_turn",
            prevented=False,
            blocked=False,
            retry_active=False,
            hook_outcomes=[],
        )
    )
    for b in (pre, post, stop):
        assert b.type == "hook", f"expected hook, got {b.type}"
        assert b.thread_id == "t1"


def test_hook_blocks_only_at_full():
    """A hook block is full-telemetry only — suppressed at off and trace."""
    b = fold(
        StopHookExecuted(
            thread_id="t",
            turn_number=1,
            trigger="after_end_turn",
            prevented=False,
            blocked=False,
            retry_active=False,
            hook_outcomes=[],
        )
    )
    assert passes_gates(b, Gates(telemetry="off")) is False
    assert passes_gates(b, Gates(telemetry="trace")) is False
    assert passes_gates(b, Gates(telemetry="full")) is True


def test_documented_sets_per_level_end_to_end():
    """Drive a representative event from each gate bucket through fold + gate and
    assert the delivered set matches the documented off/trace/full contract."""
    always = fold(TextDelta(thread_id="t", text="hi"))
    trace = fold(ThreadLifecycle(thread_id="t", status="started"))
    trace2 = fold(ExecutionSummary(thread_id="t", status="completed"))
    full = fold(
        MemoryWriteEvent(
            thread_id="t", memory_type="fact", key="k", value_preview="v"
        )
    )
    full2 = fold(
        CompactionEvent(
            thread_id="t",
            messages_before=10,
            messages_after=5,
            tokens_before=100,
            tokens_after=50,
            method="normal",
        )
    )

    off, trc, fll = Gates(telemetry="off"), Gates(telemetry="trace"), Gates(telemetry="full")

    for g in (off, trc, fll):
        assert passes_gates(always, g) is True
    for b in (trace, trace2):
        assert passes_gates(b, off) is False
        assert passes_gates(b, trc) is True
        assert passes_gates(b, fll) is True
    for b in (full, full2):
        assert passes_gates(b, off) is False
        assert passes_gates(b, trc) is False
        assert passes_gates(b, fll) is True
