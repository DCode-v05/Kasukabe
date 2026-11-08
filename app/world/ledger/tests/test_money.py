"""Pure unit tests for money.py: the exchange rate itself, isolated from
the Ledger (see test_ledger_exchange.py for the ledger-integrated
version). DESIGN.md section 3 (re-rated 2026-08-01): 10,000 sim-currency
-> $1 API credit, the single rate SIM_PER_USD/SIM_PER_USD_CENT derive
from and everything downstream (constants.py's REVIVAL_USD_CREDIT_CENTS
included) imports rather than re-declares.
"""

from __future__ import annotations

import pytest

from world.ledger.money import (
    CENTS_PER_USD,
    SIM_PER_USD,
    SIM_PER_USD_CENT,
    sim_to_usd_cents,
)


def test_the_rate_is_the_operator_pinned_5_000_to_1() -> None:
    """Re-rated 2026-09-04 from 10,000:1 on the operator's decision.

    ECON-05 measured the old rate: at ~$2 of thinking per agent-day a
    resident had to earn 20,000 sim a day to fund its own cognition,
    against day wages an order of magnitude below that. Energy was not
    expensive, it was unbuyable from honest work.

    This test pins the DECISION. The test below it pins the structural
    relationship, which must survive any future re-rating.
    """
    assert SIM_PER_USD == 5_000
    assert SIM_PER_USD_CENT == 50


def test_the_rate_is_internally_consistent_at_any_value() -> None:
    """Survives the next re-rating. The one above will not, and should not."""
    assert SIM_PER_USD == SIM_PER_USD_CENT * CENTS_PER_USD
    assert SIM_PER_USD % CENTS_PER_USD == 0, "the rate would round cents away"


def test_exchanging_one_dollars_worth_of_sim_yields_exactly_100_cents() -> None:
    assert sim_to_usd_cents(SIM_PER_USD) == CENTS_PER_USD


@pytest.mark.parametrize("cents", [1, 2, 3, 7, 99, 100, 101, 12_345])
def test_exchange_conserves_value_exactly_for_every_whole_cent_amount(cents: int) -> None:
    """For any whole number of cents, minting that many sim units at the
    rate and immediately exchanging them back must reproduce the exact
    same cent amount, with zero drift, for every value tried, not just a
    convenient round number. This is the exact-integer-conservation
    guarantee app/CONVENTIONS.md requires of all currency arithmetic.
    """
    sim_amount = cents * SIM_PER_USD_CENT

    recovered_cents = sim_to_usd_cents(sim_amount)

    assert recovered_cents == cents
    assert recovered_cents * SIM_PER_USD_CENT == sim_amount


def test_exchange_rejects_a_partial_cent_instead_of_rounding() -> None:
    """A sim amount that is not an exact multiple of SIM_PER_USD_CENT
    must never be silently rounded away; it is rejected outright."""
    partial = SIM_PER_USD_CENT // 2 or 1
    assert SIM_PER_USD_CENT % 2 == 0
    with pytest.raises(ValueError):
        sim_to_usd_cents(SIM_PER_USD_CENT + partial)


def test_exchange_rejects_non_positive_amounts() -> None:
    with pytest.raises(ValueError):
        sim_to_usd_cents(0)
    with pytest.raises(ValueError):
        sim_to_usd_cents(-SIM_PER_USD_CENT)
