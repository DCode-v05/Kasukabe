"""M0 block serializer — fold, envelope, and per-type frame contract."""

import json

from src.engine_core.blocks import (
    Block,
    BlockSerializer,
    sse_frame,
    make_done_block,
    make_error_block,
    BLOCK_TYPES,
    ALWAYS,
    TELEMETRY_TRACE,
    TELEMETRY_FULL,
)
from src.engine_core.events import (
    TextDelta,
    ToolExecution,
    ToolUseSummary,
    CraftRequest,
    CraftAuthored,
    NodeProgress,
    CharterCreated,
    ThreadLifecycle,
    ExecutionSummary,
    RoutingDecision,
    ReplanRequested,
    AssetRequiredEvent,
    CompactionEvent,
    MicrocompactEvent,
    CompactionCircuitBreakerSkip,
    TokenBudgetProgress,
    ModelFallback,
    PersistentRetryWait,
    MemoryWriteEvent,
    FileRestorationEvent,
    FileRestorationFailedEvent,
    SubprocessSpawnedEvent,
    ReconnectSummaryEvent,
    InterruptibleStateChanged,
    StrugglingDetected,
    ActivityStateChanged,
    IncrementalFlush,
)


def test_exactly_27_types():
    assert len(BLOCK_TYPES) == 27
    assert len(set(BLOCK_TYPES)) == 27


def test_gate_buckets_partition_cleanly():
    assert ALWAYS.isdisjoint(TELEMETRY_TRACE)
    assert ALWAYS.isdisjoint(TELEMETRY_FULL)
    assert TELEMETRY_TRACE.isdisjoint(TELEMETRY_FULL)


def test_sse_frame_shape():
    block = Block(type="text", payload={"text": "hi"}, thread_id="t1", id="text:r1")
    frame = sse_frame(block, 7)
    assert frame.startswith("seq: 7\nevent: block\ndata: ")
    assert frame.endswith("\n\n")
    body = json.loads(frame.split("data: ", 1)[1].strip())
    assert body == {
        "id": "text:r1",
        "type": "text",
        "thread_id": "t1",
        "ts": block.ts,
        "payload": {"text": "hi"},
    }


def test_text_folds_to_text_block_with_thread_from_thread_id():
    s = BlockSerializer("r1")
    b = s.fold(TextDelta(thread_id="thread-9", text="hello"))
    assert b.type == "text"
    assert b.thread_id == "thread-9"
    assert b.payload["text"] == "hello"
    assert "thread_id" not in b.payload


def test_tool_phases_share_one_id():
    s = BlockSerializer("r1")
    call = s.fold(ToolExecution(thread_id="t", tool_name="bash", source="platform", phase="call"))
    result = s.fold(
        ToolExecution(thread_id="t", tool_name="bash", source="platform", phase="result")
    )
    assert call.type == result.type == "tool"
    assert call.id == result.id


def test_tool_use_summary_folds_to_tool_phase_summary():
    s = BlockSerializer("r1")
    b = s.fold(ToolUseSummary(thread_id="t", summary="ran tests", tool_use_ids=["tu_1", "tu_2"]))
    assert b.type == "tool"
    assert b.id == "tool:tu_1"


def test_craft_request_folds_to_craft():
    s = BlockSerializer("r1")
    b = s.fold(CraftRequest(thread_id="t", question="ok?", options=[{"label": "a", "value": "a"}]))
    assert b.type == "craft"


def test_craft_authored_folds_to_craft_with_minted_id_and_axes():
    s = BlockSerializer("r1")
    b = s.fold(
        CraftAuthored(
            thread_id="thread-9",
            block_id="craft:thread-9:1",
            key="dispatch",
            surface="panel",
            format="react",
            wait=False,
            title="Live Dispatch",
            content="<App/>",
            final=True,
        )
    )
    assert b.type == "craft"
    assert b.thread_id == "thread-9"
    assert b.id == "craft:thread-9:1"
    assert b.payload["surface"] == "panel" and b.payload["format"] == "react"
    assert b.payload["wait"] is False
    assert "thread_id" not in b.payload


def test_status_kind_discriminator():
    s = BlockSerializer("r1")
    a = s.fold(InterruptibleStateChanged(thread_id="t", interruptible=True, tool_count=1))
    b = s.fold(
        StrugglingDetected(
            thread_id="t",
            reason="x",
            action_taken="auto_plan",
            turn_count=1,
            consecutive_errors=1,
            turns_without_text=1,
            turns_since_checkpoint=1,
            repeated_pattern="",
        )
    )
    c = s.fold(ActivityStateChanged(old_state="active", new_state="idle", idle_seconds=1.0))
    assert a.type == b.type == c.type == "status"
    assert a.payload["kind"] == "interruptible"
    assert b.payload["kind"] == "struggling"
    assert c.payload["kind"] == "activity"


def test_compaction_kind_discriminator_keeps_distinct_ids():
    s = BlockSerializer("r1")
    a = s.fold(
        CompactionEvent(
            thread_id="t",
            messages_before=1,
            messages_after=1,
            tokens_before=1,
            tokens_after=1,
            method="normal",
        )
    )
    b = s.fold(
        MicrocompactEvent(
            thread_id="t",
            mode="time_based",
            gap_minutes=1,
            gap_threshold_minutes=1.0,
            tools_cleared=1,
            tools_kept=1,
            tokens_saved=1,
            keep_recent=1,
        )
    )
    c = s.fold(CompactionCircuitBreakerSkip(thread_id="t", message_count=1, token_count=1))
    assert a.type == b.type == c.type == "compaction"
    assert {a.payload["kind"], b.payload["kind"], c.payload["kind"]} == {
        "compaction",
        "microcompact",
        "cb_skip",
    }
    assert len({a.id, b.id, c.id}) == 3


def test_model_kind_discriminator():
    s = BlockSerializer("r1")
    a = s.fold(
        ModelFallback(thread_id="t", original_model="o", fallback_model="f", consecutive_529s=3)
    )
    b = s.fold(PersistentRetryWait(thread_id="t", status_code=529, remaining_s=1.0, attempt=1))
    assert a.type == b.type == "model"
    assert a.payload["kind"] == "fallback"
    assert b.payload["kind"] == "retry_wait"


def test_file_phase_discriminator():
    s = BlockSerializer("r1")
    ok = s.fold(FileRestorationEvent(path="/a", tokens=1, truncated=False, thread_id="t"))
    bad = s.fold(FileRestorationFailedEvent(path="/a", reason="file_not_found", thread_id="t"))
    assert ok.type == bad.type == "file"
    assert ok.payload["phase"] == "restored"
    assert bad.payload["phase"] == "failed"
    assert ok.id == bad.id == "file:/a"


def test_trace_audit_folds():
    s = BlockSerializer("r1")
    assert s.fold(ThreadLifecycle(thread_id="t", status="started")).type == "thread"
    assert s.fold(ExecutionSummary(thread_id="t", status="completed")).type == "summary"
    assert (
        s.fold(
            RoutingDecision(
                thread_id="t",
                reasoning="",
                active_thread_count=0,
                segments=[{"extract": "m", "verdict": "open", "target": None}],
            )
        ).type
        == "routing"
    )
    assert s.fold(ReplanRequested(thread_id="t")).type == "replan"
    assert s.fold(AssetRequiredEvent(thread_id="t", server_name="asana")).type == "asset"
    assert (
        s.fold(MemoryWriteEvent(thread_id="t", memory_type="k", key="x", value_preview="v")).type
        == "memory"
    )
    assert (
        s.fold(
            SubprocessSpawnedEvent(
                spawn_site="sandbox_exec", mode="default", scrubbed_count=0, has_proxy_env=False
            )
        ).type
        == "subprocess"
    )
    assert (
        s.fold(
            TokenBudgetProgress(
                thread_id="t", turn_tokens=1, budget=10, pct=10, continuation_count=0
            )
        ).type
        == "budget"
    )
    assert (
        s.fold(
            ReconnectSummaryEvent(
                previous_state="away",
                idle_seconds=1.0,
                tool_calls=0,
                errors=0,
                user_active_seconds=0.0,
                engine_active_seconds=0.0,
                natural_language="back",
                source="deterministic",
            )
        ).type
        == "session"
    )
    assert s.fold(NodeProgress(thread_id="t", charter_id="c", node_id="n")).type == "progress"
    assert s.fold(CharterCreated(thread_id="t", charter_id="c")).type == "progress"


def test_dropped_events_do_not_cross_the_wire():
    s = BlockSerializer("r1")
    assert s.fold(IncrementalFlush(thread_id="t", slice_number=1, entries_flushed=1)) is None


def test_done_and_error_synthesis():
    done = make_done_block("r1", {"status": "completed", "num_turns": 2}, "thread-1")
    assert done.type == "done"
    assert done.thread_id == "thread-1"
    assert done.payload["status"] == "completed"
    err = make_error_block("r1", {"code": "boom"}, "thread-1")
    assert err.type == "error"
    assert err.payload["code"] == "boom"
