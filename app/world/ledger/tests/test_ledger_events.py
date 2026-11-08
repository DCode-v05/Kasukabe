"""economy.* events (app/CONVENTIONS.md, "Event naming scheme")."""

from __future__ import annotations

import pytest

from world.ledger.core import Ledger
from world.ledger.errors import LedgerInvariantViolation
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import TOTAL_USD_CENTS
from world.ledger.tests.ledger_factories import RecordingEventSink, build_genesis_ledger


@pytest.mark.asyncio
async def test_a_usd_move_emits_debited_then_credited() -> None:
    sink = RecordingEventSink()
    ledger = await build_genesis_ledger(["a1", "a2"], agent_usd_cents=200, event_sink=sink)
    sink.events.clear()  # drop genesis's own events, they're not this test's concern

    await ledger.move(
        from_account_id="a1",
        to_account_id="a2",
        amount=50,
        currency=Currency.USD,
        reason=TransactionReason.TRANSFER,
        tick=7,
    )

    types = [event.type for event in sink.events]
    assert types == ["economy.wallet.debited", "economy.wallet.credited"]
    assert sink.events[0].payload["account_id"] == "a1"
    assert sink.events[0].payload["amount"] == 50
    assert sink.events[1].payload["account_id"] == "a2"


@pytest.mark.asyncio
async def test_a_mint_move_only_emits_credited_no_phantom_debit() -> None:
    sink = RecordingEventSink()
    ledger = await build_genesis_ledger(["a1"], agent_usd_cents=200, event_sink=sink)
    sink.events.clear()

    await ledger.mint_sim("a1", 50_000, tick=1)

    types = [event.type for event in sink.events]
    assert types == ["economy.wallet.credited"]


@pytest.mark.asyncio
async def test_invariant_violation_emits_the_tripwire_event() -> None:
    sink = RecordingEventSink()
    ledger = Ledger(event_sink=sink)

    # Genesis now DEFINES the total (any cohort size is a valid closed
    # economy), so the violation has to be provoked the way a real one
    # happens: money appearing after the economy is sealed.
    await ledger.genesis(["a1"], agent_usd_cents=200, treasury_usd_cents=TOTAL_USD_CENTS)
    ledger.accounts["a1"].usd_cents += 200

    with pytest.raises(LedgerInvariantViolation):
        await ledger.enforce_invariant(tick=1)

    violated = [e for e in sink.events if e.type == "economy.ledger.violated"]
    assert len(violated) == 1
    assert violated[0].payload["expected_cents"] == TOTAL_USD_CENTS + 200
    assert violated[0].payload["actual_cents"] == TOTAL_USD_CENTS + 400
