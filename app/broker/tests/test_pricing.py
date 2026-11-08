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
    # DESIGN.md section 11: Kimi K2.5 flex $0.36 in / $1.80 out per 1M.
    price = price_for_model("moonshotai.kimi-k2.5")
    assert price.input_nano_per_token == 360
    assert price.output_nano_per_token == 1800


def test_deepseek_price_table_matches_design_doc() -> None:
    # DESIGN.md section 11: DeepSeek V3.2 flex $0.37 in / $1.11 out per 1M.
    price = price_for_model("deepseek.v3.2")
    assert price.input_nano_per_token == 370
    assert price.output_nano_per_token == 1110


def test_price_for_unknown_model_raises() -> None:
    with pytest.raises(ModelNotAllowedError):
        price_for_model("not-a-real-model")


def test_exact_cost_is_precise_integer_arithmetic() -> None:
    # 1,000,000 input tokens at $0.36/1M = exactly 360,000,000 nano-dollars
    # (= exactly $0.36). No rounding anywhere in this computation.
    cost = exact_cost_nano_dollars("moonshotai.kimi-k2.5", input_tokens=1_000_000, output_tokens=0)
    assert cost == 360_000_000


def test_exact_cost_combines_input_and_output() -> None:
    cost = exact_cost_nano_dollars("moonshotai.kimi-k2.5", input_tokens=1000, output_tokens=500)
    assert cost == 1000 * 360 + 500 * 1800


def test_estimate_ceiling_rounds_up_never_down() -> None:
    # 1 input token at 360 nano-dollars is far less than one cent
    # (10,000,000 nano-dollars) - the ceiling must still be 1 cent, not 0,
    # because this number gates a wallet refusal and must never understate.
    cents = estimate_cost_cents_ceiling("moonshotai.kimi-k2.5", input_tokens=1, output_tokens=0)
    assert cents == 1


def test_estimate_ceiling_of_zero_cost_is_zero() -> None:
    assert estimate_cost_cents_ceiling("moonshotai.kimi-k2.5", input_tokens=0, output_tokens=0) == 0


def test_estimate_ceiling_exact_multiple_of_a_cent_does_not_overshoot() -> None:
    # 250,000 input tokens at 360 nano-$/token costs exactly
    # 90,000,000 nano-$ = exactly 9 whole cents (LCM(360, NANO_PER_CENT)
    # / 360) - ceiling division on an already-exact boundary must land
    # on 9, not 10.
    tokens = 250_000
    cost_nano = tokens * 360
    assert cost_nano % NANO_PER_CENT == 0
    cents = estimate_cost_cents_ceiling("moonshotai.kimi-k2.5", input_tokens=tokens, output_tokens=0)
    assert cents == cost_nano // NANO_PER_CENT == 9


class TestCostAccountant:
    def test_single_debit_below_one_cent_carries_as_remainder(self) -> None:
        accountant = CostAccountant()
        # 360 nano-dollars is far below NANO_PER_CENT (10,000,000) -
        # nothing should be debited yet.
        cents = accountant.debit_cents("agent-1", 360)
        assert cents == 0
        assert accountant.remainders_nano["agent-1"] == 360

    def test_remainder_accumulates_across_calls_until_a_whole_cent(self) -> None:
        accountant = CostAccountant()
        total_debited = 0
        # Each call costs 360 nano-$; after ceil(NANO_PER_CENT / 360)
        # calls the accumulated remainder must have crossed at least one
        # whole cent.
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
        # And the accountant's own bookkeeping agrees with that gap.
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
        accountant.credit_back_cents("agent-1", cents)  # simulate the failed ledger call

        # A later, unrelated debit for the same payer must still recover
        # the full original amount - nothing was lost, nothing doubled.
        re_debited = accountant.debit_cents("agent-1", 0)  # 0 new cost, just re-checks the remainder
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
