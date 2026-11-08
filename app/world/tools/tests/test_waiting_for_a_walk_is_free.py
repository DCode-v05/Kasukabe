"""Waiting for a walk to finish costs nothing, and says why it changes nothing.

Run 28's complete trace analysis: **226 of 329 `rest` calls (69%) were made
mid-walk.** Time passes only between turns, so a rest inside a turn returns
exactly what the last one did - in 25 of the 27 turns with five or more
rests, every rest was identical - and each one still cost a model call and
1 sim. The turns that ran to the 12-call limit were 45% of all turn spend,
and `rest` was their commonest act. Bram Holloway, in one turn:

    "Still walking. 4 ticks to go. Need to rest until I arrive, then work. ...
    Something's wrong with my count."

He was told "You paused. You are still walking to Kasukabe Ore Outcrop" -
true, and silent on the one fact he lacked: the walk moves only when the
turn ends.

Two changes, and one thing deliberately unchanged:

  * a rest while walking is free, the way a walk of zero tiles already is
    (`repeated_calls.costs_nothing_because_it_does_nothing`);
  * its note states the physics - time does not pass inside a turn;
  * a rest under a standing commitment (`keep_doing`) is still charged. That
    is work that earns proficiency, and TRADE-06's first attempt broke it by
    folding every rest.
"""

from __future__ import annotations

import pytest

from world.tools.state import ContinuingAction
from world.tools.tests.conftest import action_cost, spawn_agent

FIELD = "Kasukabe Farmland"


async def _walking(world, agent_id: str) -> None:
    await spawn_agent(world, agent_id, FIELD)
    if world.movement is None:
        pytest.skip("no map on this host - movement is None (GROW-27)")
    from world.core.movement import Journey
    from world.map.model import TileCoord

    world.movement.journeys[agent_id] = Journey(
        agent_id=agent_id,
        path=[TileCoord(x=1, y=1), TileCoord(x=2, y=1), TileCoord(x=3, y=1)],
        destination_location_id="Kasukabe Forest",
        ticks_remaining=4,
    )


@pytest.mark.asyncio
async def test_a_rest_while_walking_costs_nothing(world, registry):
    await _walking(world, "n01")
    before = world.ledger.sim_balance("n01")

    outcome = await registry.execute(world, "n01", "rest", {})

    assert outcome.result["still_walking"] is True
    assert world.ledger.sim_balance("n01") == before, "a rest that changes nothing was charged"


@pytest.mark.asyncio
async def test_the_rest_says_time_does_not_pass_inside_a_turn(world, registry):
    await _walking(world, "n02")

    outcome = await registry.execute(world, "n02", "rest", {})

    note = outcome.result["note"]
    assert "Time does not pass inside a turn" in note, note
    assert "Kasukabe Forest" in note


@pytest.mark.asyncio
async def test_a_rest_standing_still_is_still_charged(world, registry):
    await spawn_agent(world, "n03", FIELD)
    before = world.ledger.sim_balance("n03")

    await registry.execute(world, "n03", "rest", {})

    assert world.ledger.sim_balance("n03") == before - action_cost("rest")


@pytest.mark.asyncio
async def test_a_rest_under_a_standing_commitment_is_still_work(world, registry):
    """`keep_doing` repeats `rest` as work; it earns proficiency and is paid for."""
    await _walking(world, "n04")
    world.continuing["n04"] = ContinuingAction(
        tool="rest", params={}, until={"units": 5}, started_tick=world.tick(),
        location_id=FIELD, owner="n04",
    )
    before = world.ledger.sim_balance("n04")

    await registry.execute(world, "n04", "rest", {})

    assert world.ledger.sim_balance("n04") == before - action_cost("rest")
