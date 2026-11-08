"""The $1000 ledger invariant, as a repo-wide law (app/CONVENTIONS.md).

This lives at the top level, not in world/ledger/tests/, for the same
reason the boto3 import boundary does (see test_module_boundaries.py's
docstring): app/CONVENTIONS.md states the sum(agent wallets) + treasury
+ burned == $1000.00 rule as binding on every module that touches
money, not just world/'s ledger implementation, so it gets a test no
single package owns. world/ledger/tests/ covers the ledger's own
internal behavior in much more depth; this file only re-affirms the law
itself still holds for the real DESIGN.md numbers.
"""

from __future__ import annotations

import pytest

from world.ledger.core import Ledger
from world.ledger.errors import LedgerInvariantViolation
from world.ledger.money import (
    GENESIS_AGENT_COUNT,
    GENESIS_AGENT_USD_CENTS,
    GENESIS_TREASURY_USD_CENTS,
    TOTAL_USD_CENTS,
)


@pytest.mark.asyncio
async def test_design_doc_genesis_numbers_sum_to_exactly_1000_dollars() -> None:
    """DESIGN.md section 3: "$500 genesis (250 x $2 wallets) + $500
    government treasury." 250 x 200 cents + 50_000 cents must equal
    100_000 cents, not approximately, exactly, per app/CONVENTIONS.md's
    "must equal 100_000 cents at all times"."""
    assert TOTAL_USD_CENTS == 100_000
    assert GENESIS_AGENT_COUNT * GENESIS_AGENT_USD_CENTS + GENESIS_TREASURY_USD_CENTS == 100_000

    ledger = Ledger()
    agent_ids = [f"agent-{i}" for i in range(GENESIS_AGENT_COUNT)]
    await ledger.genesis(agent_ids)

    assert ledger.usd_total() == TOTAL_USD_CENTS
    assert ledger.halted is False


@pytest.mark.asyncio
async def test_a_bad_config_halts_instead_of_serving_a_broken_ledger() -> None:
    """app/CONVENTIONS.md: "on violation, emit economy.ledger.violated
    and halt the simulation. A silently-wrong ledger is worse than a
    stopped one." """
    ledger = Ledger()

    await ledger.genesis(["only-one-agent"], treasury_usd_cents=GENESIS_TREASURY_USD_CENTS)
    assert ledger.halted is False

    ledger.accounts["only-one-agent"].usd_cents += 1
    with pytest.raises(LedgerInvariantViolation):
        ledger.check_invariant()

    assert ledger.halted is True
