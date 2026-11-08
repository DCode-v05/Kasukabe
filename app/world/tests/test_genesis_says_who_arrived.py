"""Somebody arriving is an event, including at genesis.

`CORE-21`. `seed_genesis` wrote BOTH LocationIndexes by hand -
`world_state.locations.place(...)` and `tick_engine.register_agent(...)` -
and published nothing. They are separate instances subscribed to the same
bus, and genesis was the one place in the town that moved a person without
saying so.

The consequences are all of the quiet kind: the trace held no record that
anybody ever arrived anywhere, `GenesisMetrics` could count births and not
arrivals, and any subscriber attached later - a viewer, a recorder, the
crime engine's witness model - would begin life not knowing where a single
resident stood.

`build_world_server` has emitted this since it was written
(`wiring.py:443`), with a comment explaining exactly why. This is the same
absorb-the-other-host's-wiring gap as CORE-02 and GROW-06.
"""
from __future__ import annotations

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


@pytest.mark.asyncio
async def test_every_genesis_resident_is_announced(tmp_path):
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    ))

    seen: list[dict] = []

    async def watch(event):
        if event.type == "agent.spawned":
            seen.append(dict(event.payload))

    await runner.boot()
    spawned = [
        e for e in runner.world_state.event_bus.recent()
        if e.type == "agent.spawned"
    ]
    assert spawned, (
        "CORE-21: not one agent.spawned was published at genesis, so nothing "
        "that attaches to the bus can learn where the town started"
    )
    announced = {e.payload.get("agent_id") for e in spawned}
    assert announced >= set(runner.world_state.agents), (
        f"CORE-21: {len(set(runner.world_state.agents) - announced)} residents "
        "were placed without an event"
    )


@pytest.mark.asyncio
async def test_the_event_carries_where(tmp_path):
    """An arrival with no place is not an arrival."""
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    ))
    await runner.boot()

    for event in runner.world_state.event_bus.recent():
        if event.type == "agent.spawned":
            assert event.payload.get("location_id"), event.payload
            assert (
                runner.world_state.location_of(event.payload["agent_id"])
                == event.payload["location_id"]
            )
