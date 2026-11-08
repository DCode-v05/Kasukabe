"""Tests for LoopState and StrugglingState dataclasses."""

import pytest
from dataclasses import dataclass
from src.engine_core.loop_state import (
    LoopState,
    StrugglingState,
    STRUGGLE_CONSECUTIVE_ERRORS,
    STRUGGLE_TURNS_NO_CHECKPOINT,
    STRUGGLE_TURNS_NO_TEXT,
    STRUGGLE_REPEATED_CALLS,
)
from src.engine_core.schema import LoopExitReason
from src.engine_core.token_budget import BudgetTracker
from src.herald import StopReason


# --- StrugglingState.check() ---


class TestStrugglingCheck:
    def test_no_struggle_by_default(self):
        s = StrugglingState()
        assert s.check(has_charter=False) is None
        assert s.check(has_charter=True) is None

    def test_consecutive_errors(self):
        s = StrugglingState(consecutive_errors=STRUGGLE_CONSECUTIVE_ERRORS)
        reason = s.check(has_charter=False)
        assert reason is not None
        assert "consecutive tool errors" in reason

    def test_turns_without_checkpoint_needs_charter(self):
        s = StrugglingState(turns_since_checkpoint=STRUGGLE_TURNS_NO_CHECKPOINT)
        # Without charter, checkpoint struggle is not reported
        assert s.check(has_charter=False) is None
        # With charter, it is
        reason = s.check(has_charter=True)
        assert reason is not None
        assert "turns without checkpoint" in reason

    def test_turns_without_text(self):
        s = StrugglingState(turns_without_text=STRUGGLE_TURNS_NO_TEXT)
        reason = s.check(has_charter=False)
        assert reason is not None
        assert "turns without text output" in reason

    def test_repeated_tool_calls(self):
        sig = "some_tool:['arg1']"
        s = StrugglingState(recent_tool_sigs=[sig] * STRUGGLE_REPEATED_CALLS)
        reason = s.check(has_charter=False)
        assert reason is not None
        assert "repeated tool call" in reason

    def test_below_thresholds_no_struggle(self):
        s = StrugglingState(
            consecutive_errors=STRUGGLE_CONSECUTIVE_ERRORS - 1,
            turns_since_checkpoint=STRUGGLE_TURNS_NO_CHECKPOINT - 1,
            turns_without_text=STRUGGLE_TURNS_NO_TEXT - 1,
        )
        assert s.check(has_charter=True) is None


# --- StrugglingState.record_tool_results() ---


@dataclass
class FakeToolCall:
    name: str
    input: dict


@dataclass
class FakeToolResult:
    content: object = ""
    is_error: bool = False


class TestStrugglingRecordToolResults:
    def test_all_errors_increments(self):
        s = StrugglingState()
        calls = [FakeToolCall(name="bash", input={"cmd": "ls"})]
        results = [FakeToolResult(content="Error: not found", is_error=True)]
        s.record_tool_results(calls, results, had_text=False, had_checkpoint=False)
        assert s.consecutive_errors == 1
        assert s.turns_without_text == 1

    def test_success_resets_errors(self):
        s = StrugglingState(consecutive_errors=5)
        calls = [FakeToolCall(name="bash", input={"cmd": "ls"})]
        results = [FakeToolResult(content="file1.txt\nfile2.txt")]
        s.record_tool_results(calls, results, had_text=True, had_checkpoint=False)
        assert s.consecutive_errors == 0
        assert s.turns_without_text == 0

    def test_checkpoint_resets_counter(self):
        s = StrugglingState(turns_since_checkpoint=10)
        calls = [FakeToolCall(name="report_checkpoint", input={"node_id": "a"})]
        results = [FakeToolResult(content="ok")]
        s.record_tool_results(calls, results, had_text=True, had_checkpoint=True)
        assert s.turns_since_checkpoint == 0

    def test_no_checkpoint_increments(self):
        s = StrugglingState(turns_since_checkpoint=5)
        calls = [FakeToolCall(name="bash", input={"cmd": "ls"})]
        results = [FakeToolResult(content="ok")]
        s.record_tool_results(calls, results, had_text=True, had_checkpoint=False)
        assert s.turns_since_checkpoint == 6

    def test_tool_sigs_capped_at_12(self):
        s = StrugglingState()
        for i in range(15):
            calls = [FakeToolCall(name=f"tool_{i}", input={"x": "y"})]
            results = [FakeToolResult(content="ok")]
            s.record_tool_results(calls, results, had_text=True, had_checkpoint=False)
        assert len(s.recent_tool_sigs) == 12


# --- Real ToolResultBlock shape coverage (regression for list-content crash) ---


class TestStrugglingRealToolResultBlock:
    @pytest.mark.parametrize(
        "make_content,is_error,expected_errors",
        [
            (lambda _: "ok", False, 0),
            (lambda tb: [tb(text="ok"), tb(text="more")], False, 0),
            (lambda tb: [tb(text="boom")], True, 1),
            (lambda _: [{"type": "text", "text": "x"}, {"type": "image"}], False, 0),
            (lambda _: [], False, 0),
            (lambda _: "", True, 1),
        ],
        ids=[
            "str-ok",
            "list-textblock-ok",
            "list-textblock-err",
            "list-dict-ok",
            "list-empty-ok",
            "str-empty-err",
        ],
    )
    def test_shapes(self, make_content, is_error, expected_errors):
        from src.herald.types import ToolResultBlock, TextBlock

        s = StrugglingState()
        calls = [FakeToolCall(name="bash", input={"cmd": "ls"})]
        results = [
            ToolResultBlock(tool_use_id="t1", content=make_content(TextBlock), is_error=is_error)
        ]
        s.record_tool_results(calls, results, had_text=not is_error, had_checkpoint=False)
        assert s.consecutive_errors == expected_errors

    def test_mixed_error_and_success_resets(self):
        s = StrugglingState(consecutive_errors=4)
        calls = [
            FakeToolCall(name="bash", input={"cmd": "ls"}),
            FakeToolCall(name="bash", input={"cmd": "cat x"}),
        ]
        results = [
            FakeToolResult(content="boom", is_error=True),
            FakeToolResult(content="ok", is_error=False),
        ]
        s.record_tool_results(calls, results, had_text=True, had_checkpoint=False)
        assert s.consecutive_errors == 0

    def test_all_errors_increments_by_count(self):
        s = StrugglingState()
        calls = [FakeToolCall(name="bash", input={"cmd": f"x{i}"}) for i in range(4)]
        results = [FakeToolResult(content="err", is_error=True) for _ in range(4)]
        s.record_tool_results(calls, results, had_text=False, had_checkpoint=False)
        assert s.consecutive_errors == 4

    def test_empty_results_noop(self):
        s = StrugglingState(consecutive_errors=2)
        s.record_tool_results([], [], had_text=False, had_checkpoint=False)
        assert s.consecutive_errors == 2
        assert s.turns_without_text == 1

    def test_missing_is_error_attr_treated_as_success(self):
        @dataclass
        class BareResult:
            content: object = "hi"

        s = StrugglingState()
        s.record_tool_results(
            [FakeToolCall(name="bash", input={"x": "y"})],
            [BareResult(content="hi")],
            had_text=True,
            had_checkpoint=False,
        )
        assert s.consecutive_errors == 0


# --- StrugglingState.reset() ---


class TestStrugglingReset:
    def test_reset_clears_all(self):
        s = StrugglingState(
            consecutive_errors=5,
            turns_since_checkpoint=10,
            turns_without_text=3,
            recent_tool_sigs=["a", "b", "c"],
        )
        s.reset()
        assert s.consecutive_errors == 0
        assert s.turns_since_checkpoint == 0
        assert s.turns_without_text == 0
        assert s.recent_tool_sigs == []

    def test_reset_does_not_clear_struggle_triggered(self):
        s = StrugglingState(struggle_triggered=True)
        s.reset()
        # struggle_triggered is intentionally NOT reset — it's a one-shot flag
        assert s.struggle_triggered is True


# --- StrugglingState serialization ---


class TestStrugglingStateSerialization:
    def test_round_trip(self):
        original = StrugglingState(
            consecutive_errors=3,
            turns_since_checkpoint=7,
            turns_without_text=2,
            recent_tool_sigs=["a:['x']", "b:['y']"],
            struggle_triggered=True,
        )
        restored = StrugglingState.from_dict(original.to_dict())
        assert restored.consecutive_errors == original.consecutive_errors
        assert restored.turns_since_checkpoint == original.turns_since_checkpoint
        assert restored.turns_without_text == original.turns_without_text
        assert restored.recent_tool_sigs == original.recent_tool_sigs
        assert restored.struggle_triggered == original.struggle_triggered

    def test_from_empty_dict(self):
        s = StrugglingState.from_dict({})
        assert s.consecutive_errors == 0
        assert s.recent_tool_sigs == []


# --- LoopState.record_turn_usage() ---


@dataclass
class FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class TestLoopStateRecordTurnUsage:
    def test_accumulates_tokens(self):
        state = LoopState()
        state.record_turn_usage(
            FakeUsage(
                input_tokens=100,
                output_tokens=50,
                cache_read_input_tokens=80,
                cache_creation_input_tokens=20,
            )
        )
        state.record_turn_usage(
            FakeUsage(
                input_tokens=200,
                output_tokens=100,
                cache_read_input_tokens=150,
                cache_creation_input_tokens=30,
            )
        )
        assert state.turns == 2
        assert state.input_tokens == 300
        assert state.output_tokens == 150
        assert state.cache_read == 230
        assert state.cache_create == 50


# --- LoopState serialization round-trip ---


class TestLoopStateSerialization:
    def test_round_trip(self):
        original = LoopState(
            turns=10,
            input_tokens=5000,
            output_tokens=2000,
            cache_read=4000,
            cache_create=1000,
            exit_reason=LoopExitReason.MAX_TURNS,
            last_stop=StopReason.MAX_TOKENS,
            max_turns=100,
            thread_id="test-task-123",
        )
        original.struggle.consecutive_errors = 2
        original.struggle.recent_tool_sigs = ["a:['x']"]

        restored = LoopState.deserialize(original.serialize())
        assert restored.turns == original.turns
        assert restored.input_tokens == original.input_tokens
        assert restored.output_tokens == original.output_tokens
        assert restored.cache_read == original.cache_read
        assert restored.cache_create == original.cache_create
        assert restored.exit_reason == original.exit_reason
        assert restored.last_stop == original.last_stop
        assert restored.max_turns == original.max_turns
        assert restored.thread_id == original.thread_id
        assert restored.struggle.consecutive_errors == 2
        assert restored.struggle.recent_tool_sigs == ["a:['x']"]

    def test_deserialize_unknown_stop_reason(self):
        data = {"last_stop": "unknown_reason"}
        state = LoopState.deserialize(data)
        assert state.last_stop == StopReason.END_TURN

    def test_deserialize_empty_dict(self):
        state = LoopState.deserialize({})
        assert state.turns == 0
        assert state.exit_reason == LoopExitReason.COMPLETED

    def test_deserialize_unknown_exit_reason(self):
        data = {"exit_reason": "unknown_reason"}
        state = LoopState.deserialize(data)
        assert state.exit_reason == LoopExitReason.COMPLETED


# --- LoopState token budget additions ---


class TestLoopStateTokenBudget:
    def test_default_budget_fields(self):
        state = LoopState()
        assert state.output_tokens_at_turn_start == 0
        assert state.current_turn_token_budget is None
        assert state.budget_tracker is None

    def test_snapshot_turn_budget_sets_start_and_budget(self):
        state = LoopState(output_tokens=750)
        state.snapshot_turn_budget(500_000)
        assert state.output_tokens_at_turn_start == 750
        assert state.current_turn_token_budget == 500_000

    def test_snapshot_turn_budget_accepts_none(self):
        state = LoopState(output_tokens=200)
        state.snapshot_turn_budget(None)
        assert state.output_tokens_at_turn_start == 200
        assert state.current_turn_token_budget is None

    def test_get_turn_output_tokens_computes_delta(self):
        state = LoopState(output_tokens=0)
        state.snapshot_turn_budget(500_000)  # snapshot at 0
        state.record_turn_usage(FakeUsage(output_tokens=300))
        assert state.get_turn_output_tokens() == 300
        state.record_turn_usage(FakeUsage(output_tokens=200))
        assert state.get_turn_output_tokens() == 500

    def test_get_turn_output_tokens_clamps_to_zero(self):
        """If output_tokens is somehow less than the snapshot (e.g. after
        compaction resets the snapshot to a fresh value), return 0 not negative.
        """
        state = LoopState(output_tokens=100)
        state.output_tokens_at_turn_start = 200
        assert state.get_turn_output_tokens() == 0

    def test_snapshot_resets_after_compaction_scenario(self):
        """Spec edge case: compaction boundary should not inflate the delta."""
        state = LoopState(output_tokens=0)
        state.snapshot_turn_budget(500_000)
        state.record_turn_usage(FakeUsage(output_tokens=50_000))
        assert state.get_turn_output_tokens() == 50_000
        # Compaction fires; the engine re-snapshots with the same budget
        state.snapshot_turn_budget(state.current_turn_token_budget)
        # Next turn's delta is measured from the compaction boundary, not 0
        state.record_turn_usage(FakeUsage(output_tokens=10_000))
        assert state.get_turn_output_tokens() == 10_000

    def test_serialize_round_trip_with_budget_tracker(self):
        original = LoopState(
            turns=5,
            output_tokens=20_000,
            output_tokens_at_turn_start=15_000,
            current_turn_token_budget=500_000,
            budget_tracker=BudgetTracker(
                continuation_count=2,
                last_delta_tokens=3_000,
                last_global_turn_tokens=5_000,
                started_at=1700000000.5,
            ),
        )
        restored = LoopState.deserialize(original.serialize())
        assert restored.output_tokens_at_turn_start == 15_000
        assert restored.current_turn_token_budget == 500_000
        assert restored.budget_tracker is not None
        assert restored.budget_tracker.continuation_count == 2
        assert restored.budget_tracker.last_delta_tokens == 3_000
        assert restored.budget_tracker.last_global_turn_tokens == 5_000
        assert restored.budget_tracker.started_at == 1700000000.5

    def test_serialize_round_trip_without_budget_tracker(self):
        original = LoopState(
            output_tokens_at_turn_start=100,
            current_turn_token_budget=None,
            budget_tracker=None,
        )
        restored = LoopState.deserialize(original.serialize())
        assert restored.output_tokens_at_turn_start == 100
        assert restored.current_turn_token_budget is None
        assert restored.budget_tracker is None

    def test_deserialize_missing_budget_keys_defaults(self):
        """Old serialized state (pre-feature) must deserialize cleanly."""
        state = LoopState.deserialize({"turns": 3, "output_tokens": 500})
        assert state.output_tokens_at_turn_start == 0
        assert state.current_turn_token_budget is None
        assert state.budget_tracker is None
