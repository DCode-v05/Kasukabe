"""ECON-18: `move` is charged for the distance actually walked.

`registry.py` filled every other `ActionActor` field and left
`distance_tiles` at 0. `extract_quantity` returns exactly that field for
"move", so the `per_unit_cost=2` half of the rule multiplied by nothing
and every move in Kasukabe collapsed to the 1-sim `min_cost`: a step
across the room and a walk from Johnson Park to Oak Hill College cost the
same, and living near your work bought you nothing.

Driven through `ToolRegistry.execute` - the seam a live resident uses -
over the real genesis map, because the defect was never in the pricing
rule (which was correct all along) but in the caller that never told it
how far. A test calling `cost_for` with a distance in hand would have
passed against the broken code, which is this project's most-repeated
way of shipping a fix that changes nothing.
"""

from __future__ import annotations

import pytest

from world.tools.tests.conftest import spawn_agent
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map

FAR_A = "Johnson Park"
FAR_B = "Oak Hill College"


async def _walk_cost(world, registry, agent_id: str, destination: str) -> int:
    """What one `move` actually took out of a resident's account."""
    before = world.ledger.get_account(agent_id).sim_units
    await registry.execute(world, agent_id, "move", {"destination_location_id": destination})
    return before - world.ledger.get_account(agent_id).sim_units


@pytest.mark.asyncio
async def test_a_cross_town_walk_costs_more_than_the_floor(world, registry) -> None:
    """The whole finding in one line: it used to cost exactly 1."""
    await spawn_agent(world, "walker", FAR_A)
    world.ledger.get_account("walker").sim_units = 10_000

    charged = await _walk_cost(world, registry, "walker", FAR_B)

    assert charged > 1, (
        f"ECON-18: a walk across the whole town cost {charged} sim - the "
        "1-sim min_cost floor, which is what a distance of 0 prices at"
    )


@pytest.mark.asyncio
async def test_distance_is_what_makes_one_walk_dearer_than_another(world, registry) -> None:
    """Not just "more than the floor" - MORE FOR FURTHER.

    A fix that fed a constant into `distance_tiles` would pass the test
    above and still price every journey in town identically.
    """
    await spawn_agent(world, "near", FAR_B)
    await spawn_agent(world, "far", FAR_A)
    for who in ("near", "far"):
        world.ledger.get_account(who).sim_units = 10_000

    near_cost = await _walk_cost(world, registry, "near", FAR_B)
    far_cost = await _walk_cost(world, registry, "far", FAR_B)

    assert far_cost > near_cost, (
        f"ECON-18: crossing the town cost {far_cost} and standing still "
        f"cost {near_cost} - distance is not reaching the price"
    )


@pytest.mark.asyncio
async def test_asking_again_is_not_billed_as_a_second_journey(world, registry) -> None:
    """Re-issuing `move` at the destination you are already walking to is
    an `already_walking_there` no-op. Charging the whole route again would
    make "am I nearly there?" cost more than the walk, and the run-15
    residents who called `move` five times in one turn would have paid
    five times for one journey.
    """
    await spawn_agent(world, "asker", FAR_A)
    world.ledger.get_account("asker").sim_units = 10_000

    first = await _walk_cost(world, registry, "asker", FAR_B)
    world.movement.advance(world.tick() + 1)
    again = await _walk_cost(world, registry, "asker", FAR_B)

    assert again < first, (
        f"ECON-18: re-asking cost {again} against the original {first} - "
        "the remaining leg is not being priced, the whole route is"
    )
