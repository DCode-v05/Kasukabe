"""Tests for world.map.service.MapService: genesis boot, event emission,
land expansion (plots survive it), plot claim/transfer/release, and the
Checkpointable snapshot/restore round trip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from world.map.expansion import GOVERNMENT_SECTOR_NAME, TEMPLE_SECTOR_NAME, GenesisZoneIds
from world.map.model import TileCoord
from world.map.parcel import Plot
from world.map.pathfinding import find_path
from world.map.service import BuildingStampError, MapService
from world.map.tiled_json import Edge

GenesisServiceState = tuple[MapService, GenesisZoneIds, "RecordingSink"]


class RecordingSink:
    """A minimal EventSink test double: records every (type, tick, payload)
    call instead of publishing anywhere, matching EventSink's
    Callable[[str, int, dict], Awaitable[None]] shape (service.py)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, int, dict[str, Any]]] = []

    async def __call__(self, event_type: str, tick: int, payload: dict[str, Any]) -> None:
        self.events.append((event_type, tick, payload))


@pytest.fixture(scope="session")
def genesis_service_state(ville_dir: Path) -> GenesisServiceState:
    """Session-scoped: booting from the real Ville assets parses ~14000
    cells across 17 layers, worth doing once and sharing read-only
    across tests that don't mutate the returned service."""
    sink = RecordingSink()
    service, zone_ids = MapService.from_kasukabe_genesis(ville_dir, event_sink=sink)
    return service, zone_ids, sink


def test_from_kasukabe_genesis_applies_config_and_mints_zones(
    genesis_service_state: GenesisServiceState,
) -> None:
    service, zone_ids, _sink = genesis_service_state
    assert service.world.find_sector("Hobbs Cafe").active
    assert not service.world.find_sector("The Rose and Crown Pub").active
    assert service.world.sectors[zone_ids.government_sector_id].name == GOVERNMENT_SECTOR_NAME
    assert service.world.sectors[zone_ids.temple_sector_id].name == TEMPLE_SECTOR_NAME


def test_from_kasukabe_genesis_does_not_emit_any_events(
    genesis_service_state: GenesisServiceState,
) -> None:
    _service, _zone_ids, sink = genesis_service_state
    assert sink.events == []


@pytest.mark.asyncio
async def test_claim_plot_registers_and_emits_an_event(ville_dir: Path) -> None:
    sink = RecordingSink()
    service, _zone_ids = MapService.from_kasukabe_genesis(ville_dir, event_sink=sink)

    tile = next(t for t in [TileCoord(x, y) for x in range(5) for y in range(5)] if service.world.is_street(t))
    plot = Plot(id="stall-1", footprint=frozenset({tile}), purpose="market stall", owner="agent-1")

    await service.claim_plot(plot, tick=10)

    assert service.plots.plot_at(tile) is plot
    assert sink.events == [
        ("world.plot.claimed", 10, {"plot_id": "stall-1", "owner": "agent-1", "purpose": "market stall", "tiles": 1})
    ]


@pytest.mark.asyncio
async def test_transfer_and_release_plot_emit_events(ville_dir: Path) -> None:
    sink = RecordingSink()
    service, _zone_ids = MapService.from_kasukabe_genesis(ville_dir, event_sink=sink)
    tile = next(t for t in [TileCoord(x, y) for x in range(5) for y in range(5)] if service.world.is_street(t))
    plot = Plot(id="stall-1", footprint=frozenset({tile}), purpose="market stall", owner="agent-1")
    await service.claim_plot(plot, tick=1)

    await service.transfer_plot("stall-1", "agent-2", tick=2)
    assert service.plots.owner_of(tile) == "agent-2"

    await service.release_plot("stall-1", tick=3)
    assert service.plots.plot_at(tile) is None

    event_types = [e[0] for e in sink.events]
    assert event_types == ["world.plot.claimed", "world.plot.transferred", "world.plot.released"]


@pytest.mark.asyncio
async def test_expand_land_grows_the_map_and_emits_world_land_expanded(ville_dir: Path) -> None:
    sink = RecordingSink()
    service, _zone_ids = MapService.from_kasukabe_genesis(ville_dir, event_sink=sink)
    previous_height = service.world.height

    result = await service.expand_land(edge=Edge.SOUTH, depth=5, tick=100, new_sector_name="New Docks")

    assert service.world.height == previous_height + 5
    assert service.world.find_sector("New Docks") is not None
    assert sink.events[-1][0] == "world.land.expanded"
    assert sink.events[-1][1] == 100
    assert result.new_height == previous_height + 5


@pytest.mark.asyncio
async def test_expand_land_regenerates_collision_and_an_agent_can_path_into_the_new_land(
    ville_dir: Path,
) -> None:
    """The exact proof this task's map-growth mechanic demands: approving
    a petition (expand_land) must genuinely append tiles, regenerate the
    collision matrix, and leave pathfinding valid on the grown map - not
    just bump width/height counters. admin_api.approve_petition calling
    this method with no MapService wired is the failure mode this test
    guards against; this test drives the method a real MapService actually
    has, with real collision/pathfinding checks on the result.
    """
    service, _zone_ids = MapService.from_kasukabe_genesis(ville_dir)
    previous_height = service.world.height
    previous_width = service.world.width
    previous_collision = service.world.collision
    assert len(previous_collision) == previous_height
    assert all(len(row) == previous_width for row in previous_collision)

    start = next(
        TileCoord(x, previous_height - 1)
        for x in range(previous_width)
        if service.world.is_walkable(TileCoord(x, previous_height - 1))
    )

    result = await service.expand_land(edge=Edge.SOUTH, depth=6, tick=1)

    assert service.world.height == previous_height + 6
    assert service.world.width == previous_width
    assert result.new_height == previous_height + 6

    new_collision = service.world.collision
    assert new_collision is not previous_collision
    assert len(new_collision) == previous_height + 6
    assert all(len(row) == previous_width for row in new_collision)
    assert new_collision[: len(previous_collision)] == previous_collision

    goal = TileCoord(start.x, service.world.height - 1)
    assert service.world.is_walkable(goal)
    path = find_path(service.world, start, goal)
    assert path is not None
    assert path[0] == start
    assert path[-1] == goal
    assert all(service.world.is_walkable(tile) for tile in path)


@pytest.mark.asyncio
async def test_expand_land_preserves_existing_plot_claims(ville_dir: Path) -> None:
    sink = RecordingSink()
    service, _zone_ids = MapService.from_kasukabe_genesis(ville_dir, event_sink=sink)
    tile = next(t for t in [TileCoord(x, y) for x in range(5) for y in range(5)] if service.world.is_street(t))
    plot = Plot(id="stall-1", footprint=frozenset({tile}), purpose="market stall", owner="agent-1")
    await service.claim_plot(plot, tick=1)

    await service.expand_land(edge=Edge.SOUTH, depth=3, tick=2)

    assert service.plots.plot_at(tile) is plot
    assert service.plots.world is service.world


@pytest.mark.asyncio
async def test_second_expansion_after_genesis_does_not_collide_with_genesis_zone_ids(
    ville_dir: Path,
) -> None:
    service, zone_ids = MapService.from_kasukabe_genesis(ville_dir)
    result = await service.expand_land(
        edge=Edge.SOUTH, depth=2, tick=1, new_sector_name="Second Petition Zone"
    )
    assert result.new_sector_ids[0] != zone_ids.government_sector_id
    assert result.new_sector_ids[0] != zone_ids.temple_sector_id
    assert result.new_sector_ids[0] != zone_ids.farmland_sector_id
    assert result.new_sector_ids[0] != zone_ids.ore_outcrop_sector_id
    assert result.new_sector_ids[0] != zone_ids.forest_sector_id


def find_adjacent_street_tiles(world_map, count: int) -> list[TileCoord]:
    for y in range(world_map.height):
        for x in range(world_map.width - count + 1):
            candidates = [TileCoord(x + i, y) for i in range(count)]
            if all(world_map.is_street(tile) for tile in candidates):
                return candidates
    raise AssertionError(f"no run of {count} adjacent street tiles found")


@pytest.mark.asyncio
async def test_stamp_building_turns_a_freeform_footprint_into_a_real_sector(ville_dir: Path) -> None:
    sink = RecordingSink()
    service, _zone_ids = MapService.from_kasukabe_genesis(ville_dir, event_sink=sink)
    tiles = find_adjacent_street_tiles(service.world, 3)
    footprint = frozenset(tiles)
    for tile in footprint:
        assert service.world.sector_at(tile) is None

    sector_id = await service.stamp_building(footprint, purpose="smithy", owner="agent-builder", tick=5)

    for tile in footprint:
        sector = service.world.sector_at(tile)
        assert sector is not None
        assert sector.id == sector_id
        assert "smithy" in sector.name and "agent-builder" in sector.name
        arena = service.world.arena_at(tile)
        assert arena is not None
        assert arena.sector_id == sector_id
    assert sink.events[-1][0] == "world.building.mapped"
    assert sink.events[-1][1] == 5
    assert sink.events[-1][2]["sector_id"] == sector_id
    assert sink.events[-1][2]["tiles"] == 3


@pytest.mark.asyncio
async def test_stamp_building_rejects_a_footprint_that_overlaps_an_existing_sector(ville_dir: Path) -> None:
    service, zone_ids = MapService.from_kasukabe_genesis(ville_dir)
    government_sector = service.world.sectors[zone_ids.government_sector_id]
    occupied_tile = next(iter(government_sector.tiles))

    with pytest.raises(BuildingStampError):
        await service.stamp_building(
            frozenset({occupied_tile}), purpose="shed", owner="agent-1", tick=1
        )


@pytest.mark.asyncio
async def test_stamp_building_rejects_out_of_bounds_tiles(ville_dir: Path) -> None:
    service, _zone_ids = MapService.from_kasukabe_genesis(ville_dir)
    out_of_bounds = TileCoord(service.world.width + 10, service.world.height + 10)

    with pytest.raises(BuildingStampError):
        await service.stamp_building(frozenset({out_of_bounds}), purpose="shed", owner="agent-1", tick=1)


@pytest.mark.asyncio
async def test_stamp_building_after_land_expansion_builds_on_the_freshly_grown_strip(
    ville_dir: Path,
) -> None:
    """Ties both mechanics together: land grows via expand_land, then a
    building is stamped onto tiles that only exist because of that growth
    - exactly the sequence a real petition-then-construction chain
    produces in the live sim."""
    service, _zone_ids = MapService.from_kasukabe_genesis(ville_dir)
    previous_height = service.world.height

    await service.expand_land(edge=Edge.SOUTH, depth=4, tick=1)
    assert service.world.height == previous_height + 4

    new_land_tiles = frozenset({TileCoord(0, previous_height), TileCoord(1, previous_height)})
    for tile in new_land_tiles:
        assert service.world.is_walkable(tile)
        assert service.world.sector_at(tile) is None

    sector_id = await service.stamp_building(new_land_tiles, purpose="outpost", owner="agent-2", tick=2)

    for tile in new_land_tiles:
        sector = service.world.sector_at(tile)
        assert sector is not None
        assert sector.id == sector_id


def test_snapshot_and_restore_round_trip(ville_dir: Path) -> None:
    service, _zone_ids = MapService.from_kasukabe_genesis(ville_dir)
    tile = next(t for t in [TileCoord(x, y) for x in range(5) for y in range(5)] if service.world.is_street(t))
    plot = Plot(id="stall-1", footprint=frozenset({tile}), purpose="market stall", owner="agent-1")
    service.plots.register(plot)

    data = service.snapshot()

    restored = MapService.__new__(MapService)
    restored.event_sink = None
    restored.restore(data)

    assert restored.world.width == service.world.width
    assert restored.world.height == service.world.height
    assert restored.world.find_sector("Hobbs Cafe").active
    assert restored.plots.plot_at(tile) is not None
    assert restored.plots.plot_at(tile).owner == "agent-1"


def test_snapshot_is_json_round_trippable(ville_dir: Path) -> None:
    import json

    service, _zone_ids = MapService.from_kasukabe_genesis(ville_dir)
    data = service.snapshot()
    reloaded = json.loads(json.dumps(data))

    restored = MapService.__new__(MapService)
    restored.event_sink = None
    restored.restore(reloaded)
    assert restored.world.width == service.world.width
