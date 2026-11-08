"""Sim minting: baby blessings (DESIGN.md section 3: "Baby blessing: 5,000
sim to god", re-rated 2026-08-01 from the original 50,000 sim figure)."""

from __future__ import annotations

import pytest

from world.ledger.errors import LedgerError
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import MINT_ACCOUNT_ID
from world.ledger.tests.ledger_factories import build_genesis_ledger


@pytest.mark.asyncio
async def test_mint_sim_credits_the_agent_from_nothing() -> None:
    ledger = await build_genesis_ledger(["baby"], agent_usd_cents=200)

    transaction = await ledger.mint_sim("baby", 5_000, tick=0)

    assert ledger.sim_balance("baby") == 5_000
    assert transaction.from_account == MINT_ACCOUNT_ID
    assert transaction.reason is TransactionReason.BLESSING
    assert ledger.halted is False  # sim is not part of the USD invariant


@pytest.mark.asyncio
async def test_minting_sim_never_moves_usd() -> None:
    ledger = await build_genesis_ledger(["baby"], agent_usd_cents=200)
    usd_before = ledger.usd_total()

    await ledger.mint_sim("baby", 5_000, tick=0)

    assert ledger.usd_total() == usd_before


@pytest.mark.asyncio
async def test_usd_cannot_be_minted_outside_genesis() -> None:
    ledger = await build_genesis_ledger(["a1"], agent_usd_cents=200)

    with pytest.raises(LedgerError):
        await ledger.move(
            from_account_id=MINT_ACCOUNT_ID,
            to_account_id="a1",
            amount=100,
            currency=Currency.USD,
            reason=TransactionReason.BLESSING,
            tick=0,
        )
