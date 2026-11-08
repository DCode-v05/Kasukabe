"""The $1000 tripwire: holds across normal operations, halts on corruption."""

from __future__ import annotations

import pytest

from world.ledger.errors import LedgerHaltedError, LedgerInvariantViolation
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import SIM_PER_USD_CENT, TOTAL_USD_CENTS
from world.ledger.tests.ledger_factories import build_genesis_ledger


@pytest.mark.asyncio
async def test_invariant_holds_after_a_usd_move() -> None:
    ledger = await build_genesis_ledger(["a1", "a2"], agent_usd_cents=200)

    await ledger.move(
        from_account_id="a1",
        to_account_id="a2",
        amount=50,
        currency=Currency.USD,
        reason=TransactionReason.TRANSFER,
        tick=1,
    )

    assert ledger.usd_balance("a1") == 150
    assert ledger.usd_balance("a2") == 250
    assert ledger.halted is False


@pytest.mark.asyncio
async def test_directly_corrupting_a_balance_trips_the_tripwire() -> None:
    ledger = await build_genesis_ledger(["a1", "a2"], agent_usd_cents=200)

    ledger.accounts["a1"].usd_cents += 1

    with pytest.raises(LedgerInvariantViolation) as excinfo:
        ledger.check_invariant()

    assert excinfo.value.actual_cents == excinfo.value.expected_cents + 1
    assert ledger.halted is True


@pytest.mark.asyncio
async def test_halted_ledger_rejects_every_further_operation() -> None:
    ledger = await build_genesis_ledger(["a1", "a2"], agent_usd_cents=200)
    ledger.accounts["a1"].usd_cents += 1
    with pytest.raises(LedgerInvariantViolation):
        ledger.check_invariant()

    with pytest.raises(LedgerHaltedError):
        await ledger.move(
            from_account_id="a2",
            to_account_id="a1",
            amount=10,
            currency=Currency.USD,
            reason=TransactionReason.TRANSFER,
            tick=2,
        )

    assert ledger.usd_balance("a2") == 200


@pytest.mark.asyncio
async def test_invariant_holds_across_an_exchange_at_the_new_rate() -> None:
    """DESIGN.md section 3 (re-rated 2026-09-04): 5,000 sim -> $1. An
    exchange moves USD from treasury to an agent wallet (and sim the
    other way), so it is exactly the kind of transaction the tripwire
    must survive: wallets + treasury + burned must still equal $1000 to
    the cent, both mid-run and after every leg completes.
    """
    ledger = await build_genesis_ledger(["a1"], agent_usd_cents=200, sim_per_agent=50_000)

    sim_leg, usd_leg = await ledger.exchange_sim_for_usd("a1", 30_000, tick=1)

    assert sim_leg.amount == 30_000
    assert usd_leg.amount == 30_000 // SIM_PER_USD_CENT
    assert ledger.usd_total() == TOTAL_USD_CENTS
    assert ledger.halted is False


@pytest.mark.asyncio
async def test_sim_only_moves_never_trip_the_usd_invariant() -> None:
    ledger = await build_genesis_ledger(["a1", "a2"], agent_usd_cents=200, sim_per_agent=1_000)

    await ledger.move(
        from_account_id="a1",
        to_account_id="a2",
        amount=500,
        currency=Currency.SIM,
        reason=TransactionReason.TRANSFER,
        tick=1,
    )

    assert ledger.halted is False
    assert ledger.sim_balance("a1") == 500
    assert ledger.sim_balance("a2") == 1_500
