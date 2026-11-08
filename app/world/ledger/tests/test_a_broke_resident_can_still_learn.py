"""Fifty-eight calls failed because a resident could not pay to remember.

`teambox-50-100usd-2026-09-16` lists `ledger x 58` among its failures. Every
one is an `InsufficientFundsError` on a LEARNING call - the working-memory
flush and the consolidation batch that turn a morning into something the
resident knows afterwards. They were billed to the resident's own USD
wallet, and by then 37 of 50 wallets were empty.

Thinking and remembering are not the same purchase. A resident that has
spent its $2 genuinely cannot afford to think - that is cognitive poverty,
it is the point of the design, and it stays exactly as it was. But the
consolidation runs over a morning the town has ALREADY paid for; refusing it
does not save that money, it throws away what the money bought. And it
refuses it precisely for the residents whose day was most eventful, because
those are the ones who spent their wallets.

So housekeeping - the learning centre, the summariser, importance scoring,
dream probes - falls back to the treasury when the resident cannot pay. The
treasury is the town's own reserve, the $1,000 invariant is untouched
(treasury to burned is the same leg as wallet to burned), and
KASUKABE_HOUSEKEEPING_PAYER=resident restores the old behaviour for a run
that wants to measure it.
"""

from __future__ import annotations

import pytest

from world.ledger.core import Ledger
from world.ledger.errors import InsufficientFundsError
from world.ledger.money import (
    BURNED_ACCOUNT_ID,
    TOTAL_USD_CENTS,
    TREASURY_ACCOUNT_ID,
)
from world.ledger.tests.ledger_factories import build_genesis_ledger

LEARNING = "learning_centre:consolidate"
A_TURN = "auto:agent_turn"


async def _a_town_with_a_broke_resident() -> Ledger:
    ledger = await build_genesis_ledger(["n01", "n02"])
    ledger.accounts["n01"].usd_cents = 0
    ledger.accounts[TREASURY_ACCOUNT_ID].usd_cents += 200
    return ledger


def _usd_total(ledger: Ledger) -> int:
    return sum(a.usd_cents for a in ledger.accounts.values())


@pytest.mark.asyncio
async def test_the_treasury_pays_when_a_wallet_cannot() -> None:
    ledger = await _a_town_with_a_broke_resident()
    before = ledger.usd_balance(TREASURY_ACCOUNT_ID)

    transaction = await ledger.burn_for_inference("n01", 5, 100, memo=LEARNING)

    assert transaction.from_account == TREASURY_ACCOUNT_ID
    assert ledger.usd_balance(TREASURY_ACCOUNT_ID) == before - 5
    assert ledger.usd_balance(BURNED_ACCOUNT_ID) >= 5


@pytest.mark.asyncio
async def test_a_resident_who_can_pay_pays_for_its_own_learning() -> None:
    """The treasury is a fallback, not a subsidy. A resident with money in
    its pocket buys its own memory."""
    ledger = await build_genesis_ledger(["n01"])
    treasury_before = ledger.usd_balance(TREASURY_ACCOUNT_ID)
    wallet_before = ledger.usd_balance("n01")

    await ledger.burn_for_inference("n01", 5, 100, memo=LEARNING)

    assert ledger.usd_balance("n01") == wallet_before - 5
    assert ledger.usd_balance(TREASURY_ACCOUNT_ID) == treasury_before


@pytest.mark.asyncio
async def test_thinking_is_never_paid_for_by_the_treasury() -> None:
    """Cognitive poverty is the design. A resident at $0 stops taking
    turns, and no fallback may quietly undo that."""
    ledger = await _a_town_with_a_broke_resident()

    with pytest.raises(InsufficientFundsError):
        await ledger.burn_for_inference("n01", 5, 100, memo=A_TURN)


@pytest.mark.asyncio
async def test_an_untagged_call_is_thinking() -> None:
    """The broker's default purpose is "unspecified", and cognition is the
    common case. An unrecognised tag must not open the treasury."""
    ledger = await _a_town_with_a_broke_resident()

    with pytest.raises(InsufficientFundsError):
        await ledger.burn_for_inference("n01", 5, 100, memo="unspecified")


@pytest.mark.asyncio
async def test_every_housekeeping_purpose_is_covered() -> None:
    """The same four the broker routes to background lanes."""
    for purpose in ("learning_centre", "summarizer", "importance_scoring", "dream_probe"):
        ledger = await _a_town_with_a_broke_resident()
        transaction = await ledger.burn_for_inference("n01", 2, 100, memo=f"auto:{purpose}:x")
        assert transaction.from_account == TREASURY_ACCOUNT_ID, purpose


@pytest.mark.asyncio
async def test_the_thousand_dollar_invariant_still_holds() -> None:
    ledger = await _a_town_with_a_broke_resident()

    await ledger.burn_for_inference("n01", 7, 100, memo=LEARNING)

    assert _usd_total(ledger) == TOTAL_USD_CENTS


@pytest.mark.asyncio
async def test_a_treasury_that_is_also_empty_still_refuses() -> None:
    """The fallback is the town's own reserve, not a printing press."""
    ledger = await _a_town_with_a_broke_resident()
    ledger.accounts[BURNED_ACCOUNT_ID].usd_cents += ledger.accounts[TREASURY_ACCOUNT_ID].usd_cents
    ledger.accounts[TREASURY_ACCOUNT_ID].usd_cents = 0

    with pytest.raises(InsufficientFundsError):
        await ledger.burn_for_inference("n01", 5, 100, memo=LEARNING)


@pytest.mark.asyncio
async def test_it_can_be_switched_off(monkeypatch) -> None:
    """One knob, so a run can put the refusals back and measure what the
    town loses by them."""
    import world.ledger.core as core

    monkeypatch.setattr(core, "HOUSEKEEPING_PAYER", "resident")
    ledger = await _a_town_with_a_broke_resident()

    with pytest.raises(InsufficientFundsError):
        await ledger.burn_for_inference("n01", 5, 100, memo=LEARNING)


def test_which_payer_was_in_force_is_recorded() -> None:
    from world.run_manifest import RECORDED_ENV

    assert "KASUKABE_HOUSEKEEPING_PAYER" in RECORDED_ENV
