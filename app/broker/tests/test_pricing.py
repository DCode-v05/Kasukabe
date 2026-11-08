"""Exact-integer cost accounting - see pricing.py's module docstring for
why this has to be exact-integer and not float-rounded-per-call."""

from __future__ import annotations

import pytest

from broker.errors import ModelNotAllowedError
from broker.pricing import (
    NANO_PER_CENT,
    CostAccountant,
    estimate_cost_cents_ceiling,
    exact_cost_nano_dollars,
    price_for_model,
)


def test_kimi_price_table_matches_design_doc() -> None:
    price = price_for_model("moonshotai.kimi-k2.5")
    assert price.input_nano_per_token == 360
    assert price.output_nano_per_token == 1800


def test_deepseek_price_table_matches_design_doc() -> None:
    price = price_for_model("deepseek.v3.2")
    assert price.input_nano_per_token == 370
    assert price.output_nano_per_token == 1110


def test_price_for_unknown_model_raises() -> None:
    with pytest.raises(ModelNotAllowedError):
        price_for_model("not-a-real-model")


def test_exact_cost_is_precise_integer_arithmetic() -> None:
    cost = exact_cost_nano_dollars("moonshotai.kimi-k2.5", input_tokens=1_000_000, output_tokens=0)
    assert cost == 360_000_000


def test_exact_cost_combines_input_and_output() -> None:
    cost = exact_cost_nano_dollars("moonshotai.kimi-k2.5", input_tokens=1000, output_tokens=500)
    assert cost == 1000 * 360 + 500 * 1800


def test_estimate_ceiling_rounds_up_never_down() -> None:
    cents = estimate_cost_cents_ceiling("moonshotai.kimi-k2.5", input_tokens=1, output_tokens=0)
    assert cents == 1


def test_estimate_ceiling_of_zero_cost_is_zero() -> None:
    assert estimate_cost_cents_ceiling("moonshotai.kimi-k2.5", input_tokens=0, output_tokens=0) == 0


def test_estimate_ceiling_exact_multiple_of_a_cent_does_not_overshoot() -> None:
    tokens = 250_000
    cost_nano = tokens * 360
    assert cost_nano % NANO_PER_CENT == 0
    cents = estimate_cost_cents_ceiling("moonshotai.kimi-k2.5", input_tokens=tokens, output_tokens=0)
    assert cents == cost_nano // NANO_PER_CENT == 9


class TestCostAccountant:
    def test_single_debit_below_one_cent_carries_as_remainder(self) -> None:
        accountant = CostAccountant()
        cents = accountant.debit_cents("agent-1", 360)
        assert cents == 0
        assert accountant.remainders_nano["agent-1"] == 360

    def test_remainder_accumulates_across_calls_until_a_whole_cent(self) -> None:
        accountant = CostAccountant()
        total_debited = 0
        calls = (NANO_PER_CENT // 360) + 1
        for _ in range(calls):
            total_debited += accountant.debit_cents("agent-1", 360)
        assert total_debited >= 1

    def test_conservation_no_drift_over_many_small_calls(self) -> None:
        """The property that makes this design correct: summed over any
        number of calls, total cents debited * NANO_PER_CENT is within
        one cent's worth of nano-dollars of the true total cost - never
        more (that would invent money) and the gap is always less than
        one full cent (that would be silent, growing loss)."""
        accountant = CostAccountant()
        total_cost_nano = 0
        total_debited_cents = 0
        for input_tokens in range(1, 2000):
            cost = exact_cost_nano_dollars("moonshotai.kimi-k2.5", input_tokens, output_tokens=3)
            total_cost_nano += cost
            total_debited_cents += accountant.debit_cents("agent-1", cost)

        undebited_nano = total_cost_nano - total_debited_cents * NANO_PER_CENT
        assert 0 <= undebited_nano < NANO_PER_CENT
        assert accountant.remainders_nano["agent-1"] == undebited_nano

    def test_different_payers_have_independent_remainders(self) -> None:
        accountant = CostAccountant()
        accountant.debit_cents("agent-1", 360)
        accountant.debit_cents("agent-2", NANO_PER_CENT + 500)

        assert accountant.remainders_nano["agent-1"] == 360
        assert accountant.remainders_nano["agent-2"] == 500

    def test_credit_back_undoes_a_debit_that_was_never_collected(self) -> None:
        """The ledger-debit-failed rollback path (broker/service.py's
        settle()): a debit_cents result that never actually reached the
        ledger must be fully recoverable, byte-for-byte, by crediting it
        back - the next call for this payer should behave exactly as if
        the failed debit had never been computed at all."""
        accountant = CostAccountant()
        cents = accountant.debit_cents("agent-1", 3 * NANO_PER_CENT + 4_000_000)
        assert cents == 3
        remainder_before_credit_back = accountant.remainders_nano["agent-1"]

        accountant.credit_back_cents("agent-1", cents)

        assert accountant.remainders_nano["agent-1"] == remainder_before_credit_back + 3 * NANO_PER_CENT

    def test_credit_back_then_re_debit_bills_the_same_total_exactly_once(self) -> None:
        accountant = CostAccountant()
        cost_nano = 7 * NANO_PER_CENT + 250_000
        cents = accountant.debit_cents("agent-1", cost_nano)
        accountant.credit_back_cents("agent-1", cents)

        re_debited = accountant.debit_cents("agent-1", 0)
        assert re_debited == cents

    def test_credit_back_of_zero_cents_is_a_no_op(self) -> None:
        accountant = CostAccountant()
        accountant.credit_back_cents("agent-1", 0)
        assert accountant.remainders_nano.get("agent-1", 0) == 0

    def test_pending_remainder_cents_fraction_is_observability_only(self) -> None:
        accountant = CostAccountant()
        accountant.debit_cents("agent-1", NANO_PER_CENT // 2)
        assert accountant.pending_remainder_cents_fraction("agent-1") == pytest.approx(0.5)

    def test_unknown_payer_has_zero_remainder(self) -> None:
        accountant = CostAccountant()
        assert accountant.pending_remainder_cents_fraction("never-seen") == 0.0
