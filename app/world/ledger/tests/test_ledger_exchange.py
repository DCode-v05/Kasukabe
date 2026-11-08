"""The government store: 10,000 sim -> $1 API credit (DESIGN.md section 3,
re-rated 2026-08-01 from the original 100,000:1 rate)."""

from __future__ import annotations

import pytest

from world.ledger.errors import InsufficientFundsError
from world.ledger.money import SIM_PER_USD, SIM_PER_USD_CENT, TREASURY_ACCOUNT_ID
from world.ledger.tests.ledger_factories import build_genesis_ledger


@pytest.mark.asyncio
async def test_exchange_converts_at_the_design_rate() -> None:
    ledger = await build_genesis_ledger(["a1"], agent_usd_cents=200, sim_per_agent=20_000)
    treasury_usd_before = ledger.usd_balance(TREASURY_ACCOUNT_ID)

    sim_leg, usd_leg = await ledger.exchange_sim_for_usd("a1", 10_000, tick=5)

    assert sim_leg.amount == 10_000
    assert usd_leg.amount == 10_000 // SIM_PER_USD_CENT  # one rate's worth of sim -> its cents
    assert ledger.sim_balance("a1") == 10_000
    credited = 10_000 // SIM_PER_USD_CENT
    assert ledger.usd_balance("a1") == 200 + credited  # genesis + exchanged
    assert ledger.usd_balance(TREASURY_ACCOUNT_ID) == treasury_usd_before - credited
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) == 10_000


@pytest.mark.asyncio
async def test_exchange_allows_sub_dollar_multiples_of_the_rate() -> None:
    # SIM_PER_USD_CENT is the finest exact granularity of the rate; see
    # money.py's sim_to_usd_cents docstring. Three cents' worth, derived,
    # so this survives a re-rating instead of pinning one rate's spelling.
    ledger = await build_genesis_ledger(["a1"], agent_usd_cents=200, sim_per_agent=5_000)
    three_cents = SIM_PER_USD_CENT * 3

    _, usd_leg = await ledger.exchange_sim_for_usd("a1", three_cents, tick=1)

    assert usd_leg.amount == 3
    assert ledger.sim_balance("a1") == 5_000 - three_cents


@pytest.mark.asyncio
async def test_exchange_rejects_amount_not_a_multiple_of_the_rate() -> None:
    ledger = await build_genesis_ledger(["a1"], agent_usd_cents=200, sim_per_agent=5_000)

    # Half a cent's worth: exact at no rate, so it must be refused rather
    # than silently floor-divided away (money.py's integer-only rule).
    with pytest.raises(ValueError):
        await ledger.exchange_sim_for_usd("a1", SIM_PER_USD_CENT + 1, tick=1)


@pytest.mark.asyncio
async def test_exchange_rejects_insufficient_sim() -> None:
    ledger = await build_genesis_ledger(["a1"], agent_usd_cents=200, sim_per_agent=50)

    with pytest.raises(InsufficientFundsError):
        await ledger.exchange_sim_for_usd("a1", 100, tick=1)

    # Failed exchange must not have moved partial funds.
    assert ledger.sim_balance("a1") == 50
    assert ledger.usd_balance("a1") == 200


@pytest.mark.asyncio
async def test_exchange_rejects_when_treasury_cannot_cover_the_payout() -> None:
    ledger = await build_genesis_ledger(["a1"], agent_usd_cents=200, sim_per_agent=10_000_000)
    # Drain the treasury directly to simulate a nearly-spent budget late
    # in the run (DESIGN.md: "~6 game-days on $500").
    ledger.accounts[TREASURY_ACCOUNT_ID].usd_cents = 0

    with pytest.raises(InsufficientFundsError):
        await ledger.exchange_sim_for_usd("a1", 10_000, tick=1)

    # The sim leg must not have been applied either: an exchange is
    # atomic, both legs happen or neither does.
    assert ledger.sim_balance("a1") == 10_000_000
