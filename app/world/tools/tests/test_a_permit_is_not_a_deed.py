"""A government permit does not entitle you to somebody else's ground.

`GROW-09`. This codebase has four unconnected notions of land:

  - `tile_owners` - 1x1 `TileOwnership`, who holds each unit
  - `tools.models.Plot` - a development RIGHT, with `permitted`
  - `map/parcel.PlotRegistry` - footprint claims validated against the
    map's actual geometry
  - buildings and businesses keyed by opaque location strings

`build_structure` consulted exactly one of them: `Plot.permitted`. So a
resident holding a permit could raise a building on ground another
resident owns, and the record of who owns what would never hear about it.

`require_tile` materialises section 31.2's "unclaimed land is a
government asset" for ground nobody has traded, so this does not forbid
building on the commons. It forbids building in somebody's garden.

WHAT THIS DOES NOT DO. The review's full fix is to make
`tools.models.Plot` the single record, have its creation validate through
`PlotRegistry.validate_claim`, and "delete `MapService.plots` or make it
a view". That is an architectural change and it needs the `grant_plot` /
`claim_plot` tools the finding also names, which do not exist - plots are
seeded directly by tests and bootstrap today. Doing half of it here would
leave two half-unified registries, which is worse than one clearly
separated pair.

`validate_claim` in particular is still not consulted: it lives on
`PlotRegistry`, which hangs off `MapService`, and the handler is given
`(world, agent, params)` with no map service in reach. Threading one in
is part of the same unification and is deliberately left to it.
"""
from __future__ import annotations

import pytest

from world.tools.errors import ActorRequirementError
from world.tools.models import OwnerClass, Plot, TileOwnership
from world.tools.state import tile_key

from .test_construction import license_builder, spawn_agent  # noqa: F401


async def _permitted_builder(world, registry, agent_id="builder"):
    await spawn_agent(world, agent_id, "plot-1")
    await license_builder(world, agent_id)
    world.plots["plot-1"] = Plot(
        plot_id="plot-1", owner_agent_id=agent_id, location_id="plot-1",
        footprint=frozenset({(0, 0), (1, 0)}), permitted=True,
    )
    world.inventory.credit(agent_id, "timber", 100)
    world.inventory.credit(agent_id, "iron_ore", 100)


async def _build(registry, world, agent_id="builder", tiles=((0, 0),)):
    return await registry.execute(
        world, agent_id, "build_structure",
        {"plot_id": "plot-1", "footprint": [list(t) for t in tiles], "purpose": "shed"},
    )


@pytest.mark.asyncio
async def test_building_on_the_commons_is_still_allowed(world, registry) -> None:
    """The control, and it matters: unrecorded ground reads as
    government-held, so a check written carelessly would forbid every
    build in the town."""
    await _permitted_builder(world, registry)
    result = await _build(registry, world)
    assert result.result["building_id"]


@pytest.mark.asyncio
async def test_you_cannot_build_on_someone_elses_ground(world, registry) -> None:
    """The finding. A permit for the plot, and the tile belongs to
    somebody else."""
    await _permitted_builder(world, registry)
    world.tile_owners[tile_key(0, 0)] = TileOwnership(
        tile_x=0, tile_y=0, owner_id="a0099", owner_class=OwnerClass.PRIVATE
    )

    with pytest.raises(ActorRequirementError) as refused:
        await _build(registry, world)
    assert "held by someone else" in str(refused.value), (
        "GROW-09: build_structure checks only Plot.permitted, so a permit "
        "is treated as a deed to whatever ground the plot covers"
    )
    assert "a0099" in str(refused.value), (
        "the refusal does not name who holds it, so the builder cannot "
        "go and negotiate"
    )


@pytest.mark.asyncio
async def test_you_can_build_on_ground_you_hold_yourself(world, registry) -> None:
    """Owning it is the whole point of owning it."""
    await _permitted_builder(world, registry)
    world.tile_owners[tile_key(0, 0)] = TileOwnership(
        tile_x=0, tile_y=0, owner_id="builder", owner_class=OwnerClass.PRIVATE
    )
    result = await _build(registry, world)
    assert result.result["building_id"]


@pytest.mark.asyncio
async def test_one_bad_tile_stops_the_whole_footprint(world, registry) -> None:
    """A building half on your land and half on your neighbour's is not
    half-built - it is a trespass."""
    await _permitted_builder(world, registry)
    world.tile_owners[tile_key(1, 0)] = TileOwnership(
        tile_x=1, tile_y=0, owner_id="a0099", owner_class=OwnerClass.PRIVATE
    )
    with pytest.raises(ActorRequirementError):
        await _build(registry, world, tiles=((0, 0), (1, 0)))


@pytest.mark.asyncio
async def test_government_held_ground_is_not_a_trespass(world, registry) -> None:
    """Section 31.2 makes unclaimed land a government asset, and GROW-13
    now records expanded strips that way explicitly. If those counted as
    someone else's, nothing could ever be built on new land - which is
    the opposite of what expansion is for."""
    await _permitted_builder(world, registry)
    world.tile_owners[tile_key(0, 0)] = TileOwnership(
        tile_x=0, tile_y=0, owner_id="government", owner_class=OwnerClass.GOVERNMENT,
        from_expansion=True,
    )
    result = await _build(registry, world)
    assert result.result["building_id"]


def test_the_unification_is_recorded_as_still_open() -> None:
    """The finding asks for one land record and this delivers one CHECK.
    A reader who finds the check and assumes the unification happened
    would be wrong, so the module says so."""
    import inspect

    from world.tools.primitives import construction

    source = inspect.getsource(construction.build_structure_handler)
    assert "GROW-09" in source
    assert "PlotRegistry" in source, (
        "the comment must name what is still NOT consulted, or the next "
        "reader will think this closed the whole finding"
    )
