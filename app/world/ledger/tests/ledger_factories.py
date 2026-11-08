"""Shared test helpers for the ledger suite.

Not a test module itself (no test_ functions), so pytest collects
nothing from it directly; each test_ledger_*.py file imports what it
needs from here. Keeping this separate from conftest.py means a test
can opt into exactly the helpers it wants instead of relying on
fixture auto-injection for what is, really, plain setup code.
"""

from __future__ import annotations

from world.ledger.core import Ledger
from world.ledger.money import GENESIS_AGENT_USD_CENTS, TOTAL_USD_CENTS


async def build_genesis_ledger(
    agent_ids: list[str],
    *,
    agent_usd_cents: int = GENESIS_AGENT_USD_CENTS,
    sim_per_agent: int = 0,
    event_sink=None,
) -> Ledger:
    """A ledger past genesis, funded so the $1000 invariant holds.

    Treasury absorbs whatever the agent wallets did not take, so any
    agent count works, not just DESIGN.md's real 250, while the total
    always lands on TOTAL_USD_CENTS exactly.
    """
    ledger = Ledger(event_sink=event_sink)
    treasury_cents = TOTAL_USD_CENTS - len(agent_ids) * agent_usd_cents
    await ledger.genesis(
        agent_ids, agent_usd_cents=agent_usd_cents, treasury_usd_cents=treasury_cents
    )
    if sim_per_agent:
        for agent_id in agent_ids:
            ledger.accounts[agent_id].sim_units = sim_per_agent
    return ledger


class RecordingEventSink:
    """An event_sink that just remembers every LedgerEvent it saw, so a
    test can assert on what was emitted without a real Supabase/Realtime
    connection."""

    def __init__(self) -> None:
        self.events: list = []

    async def __call__(self, event) -> None:
        self.events.append(event)
