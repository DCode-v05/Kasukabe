"""Genesis: the $1000 seed and the one-shot invariant check that follows it."""

from __future__ import annotations

import pytest

from world.ledger.core import Ledger
from world.ledger.errors import LedgerInvariantViolation
from world.ledger.money import (
    GENESIS_AGENT_COUNT,
    GENESIS_AGENT_USD_CENTS,
    GENESIS_TREASURY_USD_CENTS,
    TOTAL_USD_CENTS,
    TREASURY_ACCOUNT_ID,
)
from world.ledger.tests.ledger_factories import build_genesis_ledger


@pytest.mark.asyncio
async def test_real_design_genesis_sums_to_1000_dollars() -> None:
    agent_ids = [f"agent-{i}" for i in range(GENESIS_AGENT_COUNT)]
    ledger = Ledger()

    await ledger.genesis(
        agent_ids,
        agent_usd_cents=GENESIS_AGENT_USD_CENTS,
        treasury_usd_cents=GENESIS_TREASURY_USD_CENTS,
    )

    assert ledger.usd_total() == TOTAL_USD_CENTS
    assert ledger.halted is False


@pytest.mark.asyncio
async def test_genesis_gives_each_agent_the_configured_wallet() -> None:
    ledger = await build_genesis_ledger(["a1", "a2", "a3"], agent_usd_cents=200)

    assert ledger.usd_balance("a1") == 200
    assert ledger.usd_balance("a2") == 200
    assert ledger.usd_balance("a3") == 200


@pytest.mark.asyncio
async def test_genesis_records_one_transaction_per_funded_account() -> None:
    ledger = await build_genesis_ledger(["a1", "a2", "a3"], agent_usd_cents=200)

    # 3 agent wallets + 1 treasury funding = 4 GENESIS rows.
    genesis_rows = [t for t in ledger.transactions if t.reason.value == "genesis"]
    assert len(genesis_rows) == 4
    assert {t.to_account for t in genesis_rows} == {"a1", "a2", "a3", TREASURY_ACCOUNT_ID}


@pytest.mark.asyncio
async def test_genesis_sets_the_invariant_to_what_it_actually_seeded() -> None:
    """A cohort of any size is a valid closed economy.

    The invariant used to be the literal constant $1000, which meant a
    smaller experiment (the operator's 50-agents-at-$1 run) halted the
    ledger on its first check purely for having a different population.
    The economy is closed because nothing enters or leaves after genesis -
    not because it happens to total $1000.
    """
    ledger = Ledger()

    await ledger.genesis(["a1", "a2"], agent_usd_cents=100, treasury_usd_cents=5_000)

    assert ledger.halted is False
    assert ledger.expected_total_usd_cents == 5_200
    assert ledger.usd_total() == 5_200
    ledger.check_invariant()


@pytest.mark.asyncio
async def test_money_appearing_after_genesis_still_halts_the_ledger() -> None:
    """The tripwire that actually matters: drift AFTER genesis."""
    ledger = Ledger()
    await ledger.genesis(["a1", "a2"], agent_usd_cents=100, treasury_usd_cents=5_000)

    # Money materialising from nowhere - the failure the invariant exists
    # to catch, and the one no population size makes acceptable.
    ledger.accounts["a1"].usd_cents += 1

    with pytest.raises(LedgerInvariantViolation):
        ledger.check_invariant()
    assert ledger.halted is True
    assert ledger.halt_reason is not None
