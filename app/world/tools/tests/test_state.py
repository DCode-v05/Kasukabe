from __future__ import annotations

import pytest

from world.tools.tests.conftest import spawn_agent


@pytest.mark.asyncio
async def test_snapshot_restore_reverts_owned_state(world) -> None:
    await spawn_agent(world, "a1", "park")
    world.inventory.credit("a1", "wheat", 10)
    world.skills["a1"] = {"farming": 50}

    snapshot = world.snapshot()
    world.inventory.credit("a1", "wheat", 90)
    world.skills["a1"]["farming"] = 100
    del world.agents["a1"]

    world.restore(snapshot)

    assert world.inventory.quantity("a1", "wheat") == 10
    assert world.skills["a1"]["farming"] == 50
    assert "a1" in world.agents


@pytest.mark.asyncio
async def test_snapshot_does_not_touch_ledger_or_charter(world) -> None:
    """Ledger/charter are deliberately excluded from snapshot/restore
    (state.py's module docstring) - a snapshot taken, then restored,
    must never revert a ledger balance a call in between also changed."""
    from world.ledger.models import Currency, TransactionReason

    await spawn_agent(world, "a1", "park")
    await spawn_agent(world, "a2", "cafe")
    snapshot = world.snapshot()

    await world.ledger.move(
        from_account_id="a1",
        to_account_id="a2",
        amount=100,
        currency=Currency.SIM,
        reason=TransactionReason.TRANSFER,
        tick=0,
    )
    balance_after_move = world.ledger.sim_balance("a2")

    world.restore(snapshot)

    assert world.ledger.sim_balance("a2") == balance_after_move


@pytest.mark.asyncio
async def test_game_clock_matches_tick_minutes(world) -> None:
    world.current_tick = 144
    assert world.game_clock().total_hours == 24.0
