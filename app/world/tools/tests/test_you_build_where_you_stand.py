"""A builder has to be on the site.

`GROW-17`: `build_structure` is registered with `location_gate=None`, so a
licensed builder could raise a building on a plot on the far side of town
without leaving the tavern. DESIGN.md section 2 requires every tool to be
location-gated, and construction is the one verb where that is not a
formality - "construction occupies the crew for time proportional to size"
means nothing if the crew is never there.

Why the gate is in the handler and not in the ToolSpec: `location_gate` is
a CONSTANT, checked by the registry before the handler runs, and the place
a builder must stand in is not a constant - it is wherever the plot it
named happens to be. The registry cannot know that without reading the
params, so the check belongs where the plot is resolved.

Folded, not compared. `place_aliases.satisfies_gate` is what every other
co-location check in the town uses - TOOL-19 fixed `trade` and `steal` for
exactly this - and a raw `!=` here would refuse a builder standing in the
right field under a different spelling of its name.
"""
from __future__ import annotations

import pytest

from world.tools.errors import LocationGateError
from world.tools.models import Plot
from world.tools.tests.test_construction import license_builder, spawn_agent


async def _ready(world, standing_at: str) -> None:
    await spawn_agent(world, "builder", standing_at)
    await license_builder(world, "builder")
    world.plots["plot-1"] = Plot(
        plot_id="plot-1",
        owner_agent_id="builder",
        location_id="plot-1",
        footprint=frozenset({(0, 0), (1, 0), (0, 1)}),
        permitted=True,
    )
    world.inventory.credit("builder", "timber", 1_000)
    world.inventory.credit("builder", "iron_ore", 1_000)


@pytest.mark.asyncio
async def test_building_from_across_town_is_refused(world, registry) -> None:
    await _ready(world, "Kasukabe Town Square")

    with pytest.raises(LocationGateError):
        await registry.execute(
            world,
            "builder",
            "build_structure",
            {"plot_id": "plot-1", "footprint": [[0, 0], [1, 0], [0, 1]],
             "purpose": "smithy", "metal_kind": "iron"},
        )


@pytest.mark.asyncio
async def test_building_on_the_site_is_allowed(world, registry) -> None:
    """The other half. A gate that refuses everybody is not a gate, it is
    an outage."""
    await _ready(world, "plot-1")

    result = await registry.execute(
        world,
        "builder",
        "build_structure",
        {"plot_id": "plot-1", "footprint": [[0, 0], [1, 0], [0, 1]],
         "purpose": "smithy", "metal_kind": "iron"},
    )
    assert result.result["building_id"] in world.buildings
