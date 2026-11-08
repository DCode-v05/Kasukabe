"""Tests for max_budget_usd enforcement logic.

Tests the budget calculation and decision logic extracted from agent_loop().
Since agent_loop requires a full ExecutionThread, these tests verify the
components: estimate_cost, RequestOptions, LoopExitReason, and the
budget-check decision flow.
"""

import pytest

from src.engine_core.schema import RequestOptions, LoopExitReason
from src.herald.catalog import estimate_cost


# estimate_cost resolves through the citadel-synced catalog (no static
# seed); the shared conftest fixture stands in for a completed sync.
@pytest.fixture(autouse=True)
def catalog(synced_catalog):
    return synced_catalog


class TestRequestOptionsBudget:
    def test_default_none(self):
        opts = RequestOptions()
        assert opts.max_budget_usd is None

    def test_accepts_float(self):
        opts = RequestOptions(max_budget_usd=10.0)
        assert opts.max_budget_usd == 10.0

    def test_coexists_with_other_fields(self):
        opts = RequestOptions(max_turns=50, max_budget_usd=5.0, super_thinking=True)
        assert opts.max_turns == 50
        assert opts.max_budget_usd == 5.0
        assert opts.super_thinking is True


class TestBudgetExceededEnum:
    def test_value(self):
        assert LoopExitReason.BUDGET_EXCEEDED.value == "budget_exceeded"

    def test_serialization_round_trip(self):
        serialized = LoopExitReason.BUDGET_EXCEEDED.value
        restored = LoopExitReason(serialized)
        assert restored == LoopExitReason.BUDGET_EXCEEDED


class TestBudgetDecisionLogic:
    """Tests the budget check decision flow as implemented in agent_loop.

    Two outcomes only: exceeded (hard stop) or ok (continue).
    Unknown model cost → skip enforcement entirely.
    """

    def check_budget(self, running_cost, max_budget):
        """Simulate the budget decision logic from agent_loop."""
        if running_cost is None:
            return "skip"
        if running_cost >= max_budget:
            return "exceeded"
        return "ok"

    def test_exceeded_at_exact_budget(self):
        cost = estimate_cost("claude-sonnet-4-6", 100000, 50000)
        assert cost is not None
        assert self.check_budget(cost, cost) == "exceeded"

    def test_exceeded_over_budget(self):
        cost = estimate_cost("claude-sonnet-4-6", 100000, 50000)
        assert cost is not None
        assert self.check_budget(cost, cost * 0.5) == "exceeded"

    def test_ok_below_budget(self):
        cost = estimate_cost("claude-sonnet-4-6", 100000, 50000)
        assert cost is not None
        assert self.check_budget(cost, cost * 10) == "ok"

    def test_ok_at_99_percent(self):
        cost = estimate_cost("claude-sonnet-4-6", 100000, 50000)
        assert cost is not None
        budget = cost * 1.01  # cost is ~99% of budget — still ok, not exceeded
        assert self.check_budget(cost, budget) == "ok"

    def test_skip_when_cost_none(self):
        cost = estimate_cost("unknown-model-xyz", 100000, 50000)
        assert cost is None
        assert self.check_budget(cost, 5.0) == "skip"

    def test_zero_budget_skipped(self):
        opts = RequestOptions(max_budget_usd=0.0)
        assert not (opts.max_budget_usd is not None and opts.max_budget_usd > 0)

    def test_negative_budget_skipped(self):
        opts = RequestOptions(max_budget_usd=-1.0)
        assert not (opts.max_budget_usd is not None and opts.max_budget_usd > 0)

    def test_none_budget_skipped(self):
        opts = RequestOptions()
        assert not (opts.max_budget_usd is not None and opts.max_budget_usd > 0)


class TestBudgetVisibility:
    """Budget info is injected every turn so the model can self-regulate."""

    def test_visibility_format(self):
        from src.herald.catalog import format_usd

        cost = 1.2345
        max_budget = 5.00
        remaining = max_budget - cost
        msg = (
            f"<system-reminder>Budget: {format_usd(cost)} used "
            f"of {format_usd(max_budget)} ({format_usd(remaining)} remaining)"
            f"</system-reminder>"
        )
        assert "$1.23 used" in msg
        assert "$5.00" in msg
        assert "$3.77 remaining" in msg

    def test_visibility_skipped_on_turn_zero(self):
        # Budget injection only happens when state.turns > 0
        # (no cost data on the first turn)
        turns = 0
        max_budget = 5.0
        should_inject = max_budget > 0 and turns > 0
        assert not should_inject

    def test_visibility_active_after_first_turn(self):
        turns = 1
        max_budget = 5.0
        should_inject = max_budget > 0 and turns > 0
        assert should_inject


class TestCostEstimationForBudget:
    """Verify estimate_cost works correctly for budget enforcement scenarios."""

    def test_cache_tokens_reduce_cost(self):
        full_cost = estimate_cost("claude-sonnet-4-6", 100000, 50000)
        cached_cost = estimate_cost(
            "claude-sonnet-4-6",
            50000,
            50000,
            cache_read_tokens=50000,
        )
        assert full_cost is not None and cached_cost is not None
        assert cached_cost < full_cost

    def test_all_synced_models_have_cost(self, monkeypatch):
        # The catalog is citadel-synced (no static seed): install a canned
        # overlay the way a sync would and assert every priced row estimates.
        from src.herald import catalog_sync
        from src.herald.catalog import ModelCost, ModelDef

        canned = {
            mid: ModelDef(
                id=mid,
                provider="anthropic",
                name=mid,
                context_window=200_000,
                max_output_tokens=8192,
                supports_tools=True,
                supports_vision=False,
                cost=ModelCost(input_per_1m=price, output_per_1m=price * 5),
                tier="standard",
            )
            for mid, price in [("model-a", 3.0), ("model-b", 0.25), ("model-c", 5.0)]
        }
        monkeypatch.setattr(catalog_sync, "runtime_overlay", canned)
        for model_id in canned:
            cost = estimate_cost(model_id, 1000, 500)
            assert cost is not None, f"Model {model_id} returned None cost"
            assert cost > 0, f"Model {model_id} returned zero cost"
        # Unknown id (or pre-sync empty catalog): estimate is None, never 0.
        assert estimate_cost("model-never-synced", 1000, 500) is None
