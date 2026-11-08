"""Tests for BudgetTracker and check_token_budget decision function."""

import time

from src.engine_core.token_budget import (
    BudgetTracker,
    ContinueDecision,
    StopDecision,
    check_token_budget,
    create_budget_tracker,
    pct_of_budget,
    COMPLETION_THRESHOLD,
    DIMINISHING_THRESHOLD,
)


class TestCreateBudgetTracker:
    def test_zero_init(self):
        t = create_budget_tracker()
        assert t.continuation_count == 0
        assert t.last_delta_tokens == 0
        assert t.last_global_turn_tokens == 0

    def test_started_at_is_recent(self):
        before = time.time()
        t = create_budget_tracker()
        after = time.time()
        assert before <= t.started_at <= after


class TestShortCircuit:
    def test_sub_agent_returns_stop_with_null_event(self):
        t = create_budget_tracker()
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=True,
            budget=500_000,
            global_turn_tokens=100_000,
        )
        assert isinstance(decision, StopDecision)
        assert decision.completion_event is None
        assert t.continuation_count == 0

    def test_null_budget_returns_stop_with_null_event(self):
        t = create_budget_tracker()
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=None,
            global_turn_tokens=100_000,
        )
        assert isinstance(decision, StopDecision)
        assert decision.completion_event is None
        assert t.continuation_count == 0

    def test_zero_budget_returns_stop_with_null_event(self):
        t = create_budget_tracker()
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=0,
            global_turn_tokens=100_000,
        )
        assert isinstance(decision, StopDecision)
        assert decision.completion_event is None

    def test_negative_budget_returns_stop_with_null_event(self):
        t = create_budget_tracker()
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=-100,
            global_turn_tokens=100_000,
        )
        assert isinstance(decision, StopDecision)
        assert decision.completion_event is None


class TestContinue:
    def test_under_90pct_returns_continue(self):
        t = create_budget_tracker()
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=100_000,
        )
        assert isinstance(decision, ContinueDecision)
        assert decision.pct == 20
        assert decision.turn_tokens == 100_000
        assert decision.budget == 500_000
        assert decision.continuation_count == 1
        assert "20%" in decision.nudge_message
        assert "100,000" in decision.nudge_message
        assert "500,000" in decision.nudge_message

    def test_continue_mutates_tracker(self):
        t = create_budget_tracker()
        check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=100_000,
        )
        assert t.continuation_count == 1
        assert t.last_delta_tokens == 100_000
        assert t.last_global_turn_tokens == 100_000

    def test_second_continue_computes_delta(self):
        t = create_budget_tracker()
        check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=100_000,
        )
        check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=180_000,
        )
        assert t.continuation_count == 2
        assert t.last_delta_tokens == 80_000
        assert t.last_global_turn_tokens == 180_000

    def test_continue_at_0pct(self):
        t = create_budget_tracker()
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=0,
        )
        assert isinstance(decision, ContinueDecision)
        assert decision.pct == 0


class TestCompletionThreshold:
    def test_exactly_90pct_stops(self):
        t = create_budget_tracker()
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=450_000,
        )
        assert isinstance(decision, StopDecision)
        assert decision.completion_event is None

    def test_above_90pct_stops(self):
        t = create_budget_tracker()
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=460_000,
        )
        assert isinstance(decision, StopDecision)
        assert decision.completion_event is None

    def test_above_90pct_after_continuations_emits_event(self):
        t = BudgetTracker(
            continuation_count=2,
            last_delta_tokens=50_000,
            last_global_turn_tokens=400_000,
            started_at=time.time() - 5,
        )
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=460_000,
        )
        assert isinstance(decision, StopDecision)
        assert decision.completion_event is not None
        assert decision.completion_event.diminishing_returns is False
        assert decision.completion_event.pct == 92
        assert decision.completion_event.turn_tokens == 460_000
        assert decision.completion_event.budget == 500_000
        assert decision.completion_event.continuation_count == 2
        assert decision.completion_event.duration_ms >= 4_000


class TestDiminishingReturns:
    def test_needs_three_plus_continuations(self):
        """With continuation_count=2, even two low deltas do not fire diminishing."""
        t = BudgetTracker(
            continuation_count=2,
            last_delta_tokens=200,
            last_global_turn_tokens=10_000,
            started_at=time.time(),
        )
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=10_200,
        )
        assert isinstance(decision, ContinueDecision)

    def test_needs_two_consecutive_lows(self):
        """With continuation_count=3 but last_delta=800 (not low), current low
        alone should not fire — needs two consecutive lows."""
        t = BudgetTracker(
            continuation_count=3,
            last_delta_tokens=800,
            last_global_turn_tokens=10_000,
            started_at=time.time(),
        )
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=10_200,
        )
        assert isinstance(decision, ContinueDecision)
        assert t.last_delta_tokens == 200

    def test_fires_when_all_three_conditions_met(self):
        t = BudgetTracker(
            continuation_count=3,
            last_delta_tokens=200,
            last_global_turn_tokens=10_000,
            started_at=time.time() - 10,
        )
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=10_150,
        )
        assert isinstance(decision, StopDecision)
        assert decision.completion_event is not None
        assert decision.completion_event.diminishing_returns is True
        assert decision.completion_event.continuation_count == 3
        assert decision.completion_event.duration_ms >= 9_000

    def test_scenario_from_spec_edge_cases(self):
        """Spec edge case: continuation #4 delta=200, last_delta=800 → does NOT fire.
        Continuation #5 delta=150, last_delta=200 → fires."""
        t = BudgetTracker(
            continuation_count=4,
            last_delta_tokens=800,
            last_global_turn_tokens=50_000,
            started_at=time.time(),
        )
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=50_200,
        )
        assert isinstance(decision, ContinueDecision)
        assert t.last_delta_tokens == 200
        assert t.last_global_turn_tokens == 50_200
        decision2 = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=50_350,
        )
        assert isinstance(decision2, StopDecision)
        assert decision2.completion_event is not None
        assert decision2.completion_event.diminishing_returns is True


class TestNoOpWhenNeverContinued:
    def test_budget_too_small_for_single_turn(self):
        """Spec edge case: budget=100 but first turn blows past it.
        No prior continuations, not diminishing → stop with null event."""
        t = create_budget_tracker()
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=100,
            global_turn_tokens=5_000,
        )
        assert isinstance(decision, StopDecision)
        assert decision.completion_event is None
        assert t.continuation_count == 0


class TestSerialization:
    def test_to_dict_roundtrip(self):
        t = BudgetTracker(
            continuation_count=3,
            last_delta_tokens=1234,
            last_global_turn_tokens=50_000,
            started_at=1700000000.5,
        )
        d = t.to_dict()
        assert d == {
            "continuation_count": 3,
            "last_delta_tokens": 1234,
            "last_global_turn_tokens": 50_000,
            "started_at": 1700000000.5,
        }
        t2 = BudgetTracker.from_dict(d)
        assert t2.continuation_count == 3
        assert t2.last_delta_tokens == 1234
        assert t2.last_global_turn_tokens == 50_000
        assert t2.started_at == 1700000000.5

    def test_from_dict_with_missing_keys_uses_defaults(self):
        t = BudgetTracker.from_dict({})
        assert t.continuation_count == 0
        assert t.last_delta_tokens == 0
        assert t.last_global_turn_tokens == 0
        assert t.started_at == 0.0


class TestConstants:
    def test_completion_threshold(self):
        assert COMPLETION_THRESHOLD == 0.9

    def test_diminishing_threshold(self):
        assert DIMINISHING_THRESHOLD == 500


class TestPctOfBudget:
    def test_rounds_half_up(self):
        assert pct_of_budget(125, 1000) == 12

    def test_zero_used(self):
        assert pct_of_budget(0, 500_000) == 0

    def test_full(self):
        assert pct_of_budget(500_000, 500_000) == 100

    def test_over_full(self):
        assert pct_of_budget(600_000, 500_000) == 120


class TestResetDeltaOrigin:
    """The compaction-reset fix: clear last_global_turn_tokens so the next
    delta computation starts from 0, not from the stale pre-compaction value.
    """

    def test_resets_only_last_global_turn_tokens(self):
        t = BudgetTracker(
            continuation_count=4,
            last_delta_tokens=3_000,
            last_global_turn_tokens=50_000,
            started_at=1700000000.0,
        )
        t.reset_delta_origin()
        assert t.last_global_turn_tokens == 0
        assert t.continuation_count == 4
        assert t.last_delta_tokens == 3_000
        assert t.started_at == 1700000000.0

    def test_next_check_computes_fresh_delta(self):
        """Pre-compaction: tracker ran to continuation 4 with delta history.
        Post-compaction: reset_delta_origin; next check should compute delta
        from 0 (not from the stale 50_000), avoiding a spurious negative.
        """
        t = BudgetTracker(
            continuation_count=4,
            last_delta_tokens=2_000,
            last_global_turn_tokens=50_000,
            started_at=time.time(),
        )
        t.reset_delta_origin()

        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=10_000,
        )
        assert isinstance(decision, ContinueDecision)
        assert t.last_delta_tokens == 10_000

    def test_without_reset_would_fire_false_diminishing(self):
        """Regression witness: without reset_delta_origin, the pre-compaction
        last_global_turn_tokens causes a negative delta that (combined with
        an already-low last_delta_tokens) falsely fires diminishing.
        """
        t = BudgetTracker(
            continuation_count=4,
            last_delta_tokens=200,
            last_global_turn_tokens=50_000,
            started_at=time.time(),
        )
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=10_000,
        )
        assert isinstance(decision, StopDecision)
        assert decision.completion_event is not None
        assert decision.completion_event.diminishing_returns is True

    def test_with_reset_same_scenario_continues(self):
        """Same scenario as above, but WITH the fix applied."""
        t = BudgetTracker(
            continuation_count=4,
            last_delta_tokens=200,
            last_global_turn_tokens=50_000,
            started_at=time.time(),
        )
        t.reset_delta_origin()
        decision = check_token_budget(
            tracker=t,
            is_sub_agent=False,
            budget=500_000,
            global_turn_tokens=10_000,
        )
        assert isinstance(decision, ContinueDecision)
