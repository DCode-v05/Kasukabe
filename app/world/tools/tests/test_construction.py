from __future__ import annotations

from pathlib import Path

import pytest

from ville_assets import require_ville_assets

from world.charter.models import License
from world.map.model import TileCoord
from world.map.service import MapService
from world.tools.errors import ActorRequirementError
from world.tools.models import Plot
from world.tools.primitives.construction import (
    buildings_due_for_completion,
    compute_perimeter,
    complete_construction,
)
from world.tools.tests.conftest import spawn_agent

VILLE_DIR = (
    Path(__file__).resolve().parents[4]
    / "smallville"
    / "environment"
    / "frontend_server"
    / "static_dirs"
    / "assets"
    / "the_ville"
)


async def license_builder(world, agent_id: str) -> None:
    await world.charter.store.put_license(
        License(
            license_id=f"lic-{agent_id}",
            license_type="builder_license",
            holder_agent_id=agent_id,
            fee_paid_sim=500,
            issued_by_agent_id="genesis",
            issued_tick=0,
        )
    )


def test_compute_perimeter_of_a_2x2_square_is_8() -> None:
    tiles = frozenset({(0, 0), (1, 0), (0, 1), (1, 1)})
    assert compute_perimeter(tiles) == 8


def test_compute_perimeter_of_a_single_tile_is_4() -> None:
    assert compute_perimeter(frozenset({(5, 5)})) == 4


@pytest.mark.asyncio
async def test_build_structure_debits_bill_of_materials(world, registry) -> None:
    await spawn_agent(world, "builder", "plot-1")
    await license_builder(world, "builder")
    footprint = frozenset({(0, 0), (1, 0), (0, 1)})
    world.plots["plot-1"] = Plot(
        plot_id="plot-1", owner_agent_id="builder", location_id="plot-1", footprint=footprint, permitted=True
    )
    world.inventory.credit("builder", "timber", 1_000)
    world.inventory.credit("builder", "iron_ore", 1_000)

    result = await registry.execute(
        world,
        "builder",
        "build_structure",
        {"plot_id": "plot-1", "footprint": [[0, 0], [1, 0], [0, 1]], "purpose": "smithy", "metal_kind": "iron"},
    )

    building = world.buildings[result.result["building_id"]]
    assert building.timber_used == 3 * 5
    assert building.metal_used == compute_perimeter(footprint) * 2
    assert world.inventory.quantity("builder", "timber") == 1_000 - building.timber_used
    assert world.inventory.quantity("builder", "iron_ore") == 1_000 - building.metal_used
    assert building.completion_tick == world.tick() + 3 * 6


@pytest.mark.asyncio
async def test_build_structure_requires_builder_license(world, registry) -> None:
    await spawn_agent(world, "unlicensed", "plot-1")
    world.plots["plot-1"] = Plot(
        plot_id="plot-1",
        owner_agent_id="unlicensed",
        location_id="plot-1",
        footprint=frozenset({(0, 0)}),
        permitted=True,
    )
    world.inventory.credit("unlicensed", "timber", 100)
    world.inventory.credit("unlicensed", "iron_ore", 100)

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world,
            "unlicensed",
            "build_structure",
            {"plot_id": "plot-1", "footprint": [[0, 0]], "purpose": "shed"},
        )


@pytest.mark.asyncio
async def test_build_structure_requires_permit(world, registry) -> None:
    await spawn_agent(world, "builder", "plot-1")
    await license_builder(world, "builder")
    world.plots["plot-1"] = Plot(
        plot_id="plot-1",
        owner_agent_id="builder",
        location_id="plot-1",
        footprint=frozenset({(0, 0)}),
        permitted=False,
    )
    world.inventory.credit("builder", "timber", 100)
    world.inventory.credit("builder", "iron_ore", 100)

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world, "builder", "build_structure", {"plot_id": "plot-1", "footprint": [[0, 0]], "purpose": "shed"}
        )


@pytest.mark.asyncio
async def test_build_structure_rejects_footprint_outside_plot(world, registry) -> None:
    await spawn_agent(world, "builder", "plot-1")
    await license_builder(world, "builder")
    world.plots["plot-1"] = Plot(
        plot_id="plot-1",
        owner_agent_id="builder",
        location_id="plot-1",
        footprint=frozenset({(0, 0)}),
        permitted=True,
    )
    world.inventory.credit("builder", "timber", 100)
    world.inventory.credit("builder", "iron_ore", 100)

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world,
            "builder",
            "build_structure",
            {"plot_id": "plot-1", "footprint": [[9, 9]], "purpose": "shed"},
        )


@pytest.mark.asyncio
async def test_complete_construction_flips_status_once_due(world, registry) -> None:
    await spawn_agent(world, "builder", "plot-1")
    await license_builder(world, "builder")
    world.plots["plot-1"] = Plot(
        plot_id="plot-1",
        owner_agent_id="builder",
        location_id="plot-1",
        footprint=frozenset({(0, 0)}),
        permitted=True,
    )
    world.inventory.credit("builder", "timber", 100)
    world.inventory.credit("builder", "iron_ore", 100)
    result = await registry.execute(
        world, "builder", "build_structure", {"plot_id": "plot-1", "footprint": [[0, 0]], "purpose": "shed"}
    )
    building_id = result.result["building_id"]

    with pytest.raises(ActorRequirementError):
        await complete_construction(world, building_id)

    world.current_tick = result.result["completion_tick"]
    with pytest.raises(ActorRequirementError):
        await complete_construction(world, building_id)
    while world.buildings[building_id].labour_remaining:
        await registry.execute(
            world, "builder", "work_site", {"building_id": building_id}
        )

    event = await complete_construction(world, building_id)
    assert event.type == "world.building.completed"
    assert world.buildings[building_id].status.value == "complete"


def adjacent_street_tiles(world_map, count: int) -> list[TileCoord]:
    """Finds `count` horizontally-adjacent, real street tiles (walkable,
    no existing sector) on the given WorldMap - a genuine, currently-free
    footprint to build on, not hand-picked coordinates that happen to work
    by accident. Mirrors world/map/tests/test_world_map_service.py's own
    "search the real Ville data for a street tile" idiom, extended to a
    contiguous run."""
    for y in range(world_map.height):
        for x in range(world_map.width - count + 1):
            candidates = [TileCoord(x + i, y) for i in range(count)]
            if all(world_map.is_street(tile) for tile in candidates):
                return candidates
    raise AssertionError(f"no run of {count} adjacent street tiles found on this map")


@pytest.mark.asyncio
async def test_full_chain_unlicensed_refused_then_licensed_build_appears_in_world_state_and_on_the_map(
    world, registry
) -> None:
    """The whole DESIGN.md section 1 chain, end to end, against a REAL
    MapService built from the real Ville tileset (no mock/fake anywhere
    in this test): an unlicensed agent is refused; a licensed builder
    with a government-permitted plot claims a real footprint; materials
    are consumed exactly per that footprint's geometry; construction time
    is proportional to its size; and the finished building appears both
    in WorldState.buildings AND as a real Sector+Arena on MapService's
    live WorldMap - the exact seam parcel.py's own docstring names
    ("becomes a real Sector") and that this task exists to close.
    """
    require_ville_assets(
        "the full construction chain: petition -> plot -> build -> a real "
        "Sector on the live WorldMap"
    )
    map_service, _zone_ids = MapService.from_kasukabe_genesis(VILLE_DIR)

    tiles = adjacent_street_tiles(map_service.world, 3)
    footprint = frozenset((t.x, t.y) for t in tiles)

    await spawn_agent(world, "drifter", "plot-1")
    world.plots["plot-1"] = Plot(
        plot_id="plot-1", owner_agent_id="drifter", location_id="plot-1", footprint=footprint, permitted=True
    )
    world.inventory.credit("drifter", "timber", 1_000)
    world.inventory.credit("drifter", "iron_ore", 1_000)

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world,
            "drifter",
            "build_structure",
            {
                "plot_id": "plot-1",
                "footprint": [[t.x, t.y] for t in tiles],
                "purpose": "smithy",
                "metal_kind": "iron",
            },
        )
    assert world.buildings == {}

    await world.charter.store.put_license(
        License(
            license_id="lic-drifter",
            license_type="builder_license",
            holder_agent_id="drifter",
            fee_paid_sim=500,
            issued_by_agent_id="genesis",
            issued_tick=0,
        )
    )
    result = await registry.execute(
        world,
        "drifter",
        "build_structure",
        {
            "plot_id": "plot-1",
            "footprint": [[t.x, t.y] for t in tiles],
            "purpose": "smithy",
            "metal_kind": "iron",
        },
    )
    building_id = result.result["building_id"]
    building = world.buildings[building_id]

    area = len(footprint)
    perimeter = compute_perimeter(footprint)
    assert building.timber_used == area * 5
    assert building.metal_used == perimeter * 2
    assert world.inventory.quantity("drifter", "timber") == 1_000 - building.timber_used
    assert world.inventory.quantity("drifter", "iron_ore") == 1_000 - building.metal_used
    assert building.completion_tick == world.tick() + area * 6
    assert building.status.value == "under_construction"

    for tile in tiles:
        assert map_service.world.sector_at(tile) is None

    assert buildings_due_for_completion(world) == []

    world.current_tick = building.completion_tick
    assert buildings_due_for_completion(world) == []
    while world.buildings[building_id].labour_remaining:
        await registry.execute(
            world, "drifter", "work_site", {"building_id": building_id}
        )
    assert buildings_due_for_completion(world) == [building_id]
    event = await complete_construction(world, building_id, map_service=map_service)

    assert event.type == "world.building.completed"
    assert world.buildings[building_id].status.value == "complete"
    assert buildings_due_for_completion(world) == []

    sector_id = event.payload["map_sector_id"]
    assert sector_id is not None
    minted_sector_name = None
    for tile in tiles:
        sector = map_service.world.sector_at(tile)
        assert sector is not None
        assert sector.id == sector_id
        assert building.builder_agent_id in sector.name
        assert map_service.world.arena_at(tile) is not None
        minted_sector_name = sector.name
    assert map_service.world.find_sector(minted_sector_name) is not None
    assert map_service.world.find_arena(minted_sector_name, minted_sector_name) is not None
