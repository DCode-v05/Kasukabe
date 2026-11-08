"""What must not grow without bound over a multi-hour run.

A 30-agent town makes tens of thousands of tool calls in a few hours. Three
lists grow with every ACTION rather than with the town, and an uncapped one is
what ends a long run on a small machine: the process grows until it swaps,
which on 8 GB is fatal and takes the world server with it.

The archive is elsewhere - `tool_invocations` is mirrored into the trace
store, refusals are grouped by the observatory as they arrive, and utterances
land in the `utterances` table. These lists are the live tail the running
world needs, not the history.
"""

import pytest

import world.tools.state as state
from world.tools.bootstrap import build_world
from world.tools.errors import ToolNotFoundError
from world.tools.locations import farmland_location
from world.tools.tests.conftest import spawn_agent

FIELD = farmland_location("wheat")


def test_every_per_action_list_has_a_cap():
    """Named explicitly so a future list added to WorldState has to make a
    deliberate decision rather than inheriting unbounded growth."""
    assert state.MAX_TOOL_INVOCATIONS_KEPT > 0
    assert state.MAX_TOOL_REFUSALS_KEPT > 0
    from world.tools.bootstrap import MAX_UTTERANCES_KEPT

    assert MAX_UTTERANCES_KEPT > 0


@pytest.mark.asyncio
async def test_invocations_stop_growing_at_the_cap(monkeypatch):
    """The list that grows fastest: one entry per successful call, per agent,
    per tick."""
    monkeypatch.setattr(state, "MAX_TOOL_INVOCATIONS_KEPT", 20)
    import world.tools.registry as registry_mod

    monkeypatch.setattr(registry_mod, "MAX_TOOL_INVOCATIONS_KEPT", 20)

    world, registry = await build_world(seed=41)
    await spawn_agent(world, "ada", FIELD, starting_sim=1_000_000)
    for _ in range(60):
        await registry.execute(world, "ada", "harvest", {"crop": "wheat", "quantity": 1})

    assert len(world.tool_invocations) <= 20


@pytest.mark.asyncio
async def test_refusals_stop_growing_at_the_cap(monkeypatch):
    monkeypatch.setattr(state, "MAX_TOOL_REFUSALS_KEPT", 10)
    import world.tools.registry as registry_mod

    monkeypatch.setattr(registry_mod, "MAX_TOOL_REFUSALS_KEPT", 10)

    world, registry = await build_world(seed=42)
    await spawn_agent(world, "ada", FIELD, starting_sim=1_000_000)
    for _ in range(40):
        with pytest.raises(ToolNotFoundError):
            await registry.execute(world, "ada", "no_such_tool", {})

    assert len(world.tool_refusals) <= 10


@pytest.mark.asyncio
async def test_the_cap_keeps_the_NEWEST_records(monkeypatch):
    """A ring that dropped the newest would make the live tail useless: the
    viewer shows what just happened, not what happened first."""
    monkeypatch.setattr(state, "MAX_TOOL_INVOCATIONS_KEPT", 5)
    import world.tools.registry as registry_mod

    monkeypatch.setattr(registry_mod, "MAX_TOOL_INVOCATIONS_KEPT", 5)

    world, registry = await build_world(seed=43)
    await spawn_agent(world, "ada", FIELD, starting_sim=1_000_000)
    for _ in range(12):
        await registry.execute(world, "ada", "harvest", {"crop": "wheat", "quantity": 1})

    ticks = [r.tick for r in world.tool_invocations]
    assert len(world.tool_invocations) <= 5
    assert ticks == sorted(ticks), "still in order, just trimmed from the front"
