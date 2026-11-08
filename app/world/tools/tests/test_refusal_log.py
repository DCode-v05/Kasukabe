"""Refused intentions are recorded, and survive the rollback that caused them.

The gaps view on :8500 is built entirely from these rows. If they were kept in
OWNED_STATE_FIELDS they would be erased by the very rollback that created
them - a log that is empty precisely when it has something to say - so this
file exists to pin that they are not.
"""

import pytest

from world.ledger.action_costs import ActionUnaffordableError
from world.tools.bootstrap import build_world
from world.tools.errors import LocationGateError, ToolNotFoundError, ToolValidationError
from world.tools.locations import farmland_location
from world.tools.tests.conftest import spawn_agent

FIELD = farmland_location("wheat")


async def _world():
    world, registry = await build_world(seed=71)
    await spawn_agent(world, "ada", FIELD, starting_sim=10_000)
    return world, registry


@pytest.mark.asyncio
async def test_a_refusal_survives_the_rollback_that_caused_it() -> None:
    world, registry = await _world()

    with pytest.raises(LocationGateError):
        await registry.execute(world, "ada", "harvest", {"crop": "rice", "quantity": 1})

    assert len(world.tool_refusals) == 1, "the record outlived world.restore()"
    refusal = world.tool_refusals[0]
    assert refusal.agent_id == "ada"
    assert refusal.tool_name == "harvest"
    assert refusal.unmet_requirement.startswith("location:")


@pytest.mark.asyncio
async def test_reaching_for_a_verb_the_town_does_not_have_is_the_headline_gap() -> None:
    """The row that becomes a prompt to invent something."""
    world, registry = await _world()

    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "ada", "repair_the_roof", {})

    refusal = world.tool_refusals[-1]
    assert refusal.unmet_requirement == "no-such-tool:repair_the_roof"


@pytest.mark.asyncio
async def test_not_affording_an_action_is_a_gap_too() -> None:
    world, registry = await build_world(seed=72)
    await spawn_agent(world, "pauper", FIELD, starting_sim=0)

    with pytest.raises(ActionUnaffordableError):
        await registry.execute(world, "pauper", "harvest", {"crop": "wheat", "quantity": 1})

    assert world.tool_refusals[-1].unmet_requirement == "funds"


@pytest.mark.asyncio
async def test_a_malformed_call_is_a_mistake_not_a_capability_gap() -> None:
    """Letting typos into the gaps view would bury the real gaps under them."""
    world, registry = await _world()

    with pytest.raises(ToolValidationError):
        await registry.execute(world, "ada", "harvest", {"crop": "sapphires", "quantity": 1})

    assert world.tool_refusals == []


@pytest.mark.asyncio
async def test_a_successful_call_records_nothing() -> None:
    world, registry = await _world()
    await registry.execute(world, "ada", "harvest", {"crop": "wheat", "quantity": 1})
    assert world.tool_refusals == []


@pytest.mark.asyncio
async def test_the_same_gap_groups_across_agents() -> None:
    """The key must be stable across agents and calls, or a town-wide gap
    fragments into one row per agent and stops looking like a gap at all."""
    world, registry = await build_world(seed=73)
    for who in ("ada", "bram", "cyn"):
        await spawn_agent(world, who, FIELD, starting_sim=10_000)
        with pytest.raises(ToolNotFoundError):
            await registry.execute(world, who, "mend_nets", {})

    keys = {r.unmet_requirement for r in world.tool_refusals}
    assert keys == {"no-such-tool:mend_nets"}

    from observatory.gaps import summarise

    gap = summarise([r.model_dump() for r in world.tool_refusals])[0]
    assert gap.agents == 3 and gap.attempts == 3
