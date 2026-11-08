"""Tests for world.map.expansion: split_span, carve_sector, the genesis
zone carve (against the real Ville data - resource/government/temple
zones only exist once minted, there is no synthetic fixture worth
building for this), and approve_land_petition's transactional growth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from world.map.blocks import SpecialBlockTables, load_special_blocks
from world.map.expansion import (
    FARMLAND_SECTOR_NAME,
    FOREST_SECTOR_NAME,
    GOVERNMENT_SECTOR_NAME,
    ORE_OUTCROP_SECTOR_NAME,
    TEMPLE_SECTOR_NAME,
    GenesisZoneIds,
    approve_land_petition,
    append_kasukabe_genesis_zones,
    carve_sector,
    next_available_id,
    single_arena_rect_bands,
    split_span,
)
from world.map.model import ResourceKind, WorldMap
from world.map.tiled_json import (
    Edge,
    IdAllocator,
    build_world_map_from_tiled,
    get_layer,
    load_tiled_map,
)

GenesisZonesFixture = tuple[WorldMap, GenesisZoneIds, int, int]


# -- split_span -------------------------------------------------------------


def test_split_span_divides_evenly() -> None:
    assert split_span(30, 10) == [(i * 3, i * 3 + 3) for i in range(10)]


def test_split_span_distributes_remainder_to_the_first_parts() -> None:
    spans = split_span(10, 3)
    assert spans == [(0, 4), (4, 7), (7, 10)]
    assert sum(b - a for a, b in spans) == 10


def test_split_span_rejects_more_parts_than_units() -> None:
    with pytest.raises(ValueError):
        split_span(2, 3)


def test_split_span_rejects_non_positive_parts() -> None:
    with pytest.raises(ValueError):
        split_span(10, 0)


# -- single_arena_rect_bands / carve_sector on the tiny fixture -----------


def test_single_arena_rect_bands_splits_along_the_depth(tiny_blocks: SpecialBlockTables) -> None:
    bands = single_arena_rect_bands((0, 0, 5, 5), ("a", "b", "c", "d", "e"))
    assert [name for name, _ in bands] == ["a", "b", "c", "d", "e"]
    assert [rect for _, rect in bands] == [(0, y, 5, y + 1) for y in range(5)]


def test_carve_sector_stamps_layers_and_registers_names(tiny_tiled: dict[str, Any], tiny_blocks: SpecialBlockTables) -> None:
    sector_ids = IdAllocator(floor=500)
    arena_ids = IdAllocator(floor=600)
    sector_id = carve_sector(
        tiny_tiled,
        tiny_blocks,
        sector_ids,
        arena_ids,
        world_name="Test Town",
        sector_name="New Zone",
        rect=(0, 0, 2, 1),
        arenas=[("Room A", (0, 0, 1, 1)), ("Room B", (1, 0, 2, 1))],
    )
    assert sector_id == 500
    assert tiny_blocks.sectors[500] == ("Test Town", "New Zone")
    assert tiny_blocks.arenas[600] == ("Test Town", "New Zone", "Room A")
    assert tiny_blocks.arenas[601] == ("Test Town", "New Zone", "Room B")

    from world.map.tiled_json import SECTOR_LAYER, ARENA_LAYER

    sector_layer = get_layer(tiny_tiled, SECTOR_LAYER)
    arena_layer = get_layer(tiny_tiled, ARENA_LAYER)
    width = tiny_tiled["width"]
    assert sector_layer[0 * width + 0] == 500
    assert sector_layer[0 * width + 1] == 500
    assert arena_layer[0 * width + 0] == 600
    assert arena_layer[0 * width + 1] == 601


def test_next_available_id_starts_at_floor_when_empty() -> None:
    assert next_available_id({}, floor=900_000) == 900_000


def test_next_available_id_continues_past_the_highest_existing_id() -> None:
    assert next_available_id({900_005: ("a", "b")}, floor=900_000) == 900_006


# -- approve_land_petition, transactional growth on the tiny fixture -----


@pytest.mark.asyncio
async def test_approve_land_petition_grows_south_and_leaves_original_untouched(
    tiny_tiled: dict[str, Any], tiny_blocks: SpecialBlockTables
) -> None:
    original_height = tiny_tiled["height"]
    grown_tiled, grown_blocks, world, result = await approve_land_petition(
        tiny_tiled, tiny_blocks, edge=Edge.SOUTH, depth=3
    )
    assert tiny_tiled["height"] == original_height  # caller's dict is untouched
    assert grown_tiled["height"] == original_height + 3
    assert world.height == original_height + 3
    assert result.previous_height == original_height
    assert result.new_height == original_height + 3
    assert result.edge is Edge.SOUTH


@pytest.mark.asyncio
async def test_approve_land_petition_grows_east(tiny_tiled: dict[str, Any], tiny_blocks: SpecialBlockTables) -> None:
    original_width = tiny_tiled["width"]
    _, _, world, result = await approve_land_petition(
        tiny_tiled, tiny_blocks, edge=Edge.EAST, depth=4
    )
    assert world.width == original_width + 4
    assert world.height == tiny_tiled["height"]
    assert result.new_width == original_width + 4


@pytest.mark.asyncio
async def test_approve_land_petition_carves_a_named_sector_into_the_new_land(
    tiny_tiled: dict[str, Any], tiny_blocks: SpecialBlockTables
) -> None:
    _, _, world, result = await approve_land_petition(
        tiny_tiled,
        tiny_blocks,
        edge=Edge.SOUTH,
        depth=2,
        new_sector_name="New Docks",
        arena_names=["Pier A", "Pier B"],
    )
    sector = world.find_sector("New Docks")
    assert sector is not None
    assert sector.active is False  # newly carved sectors are not genesis-classified here
    assert len(sector.arena_ids) == 2
    assert result.new_sector_names == ["New Docks"]


@pytest.mark.asyncio
async def test_approve_land_petition_rejects_non_positive_depth(tiny_tiled: dict[str, Any], tiny_blocks: SpecialBlockTables) -> None:
    from world.map.expansion import ExpansionError

    with pytest.raises(ExpansionError):
        await approve_land_petition(tiny_tiled, tiny_blocks, edge=Edge.SOUTH, depth=0)


@pytest.mark.asyncio
async def test_two_successive_petitions_never_collide_ids(tiny_tiled: dict[str, Any], tiny_blocks: SpecialBlockTables) -> None:
    tiled_1, blocks_1, _, result_1 = await approve_land_petition(
        tiny_tiled, tiny_blocks, edge=Edge.SOUTH, depth=1, new_sector_name="Zone One"
    )
    _, _, world_2, result_2 = await approve_land_petition(
        tiled_1, blocks_1, edge=Edge.SOUTH, depth=1, new_sector_name="Zone Two"
    )
    assert result_1.new_sector_ids[0] != result_2.new_sector_ids[0]
    assert world_2.find_sector("Zone One") is not None
    assert world_2.find_sector("Zone Two") is not None


# -- append_kasukabe_genesis_zones, integration against the real Ville data --


@pytest.fixture(scope="session")
def genesis_zones(ville_dir: Path) -> GenesisZonesFixture:
    blocks = load_special_blocks(ville_dir)
    tiled = load_tiled_map(ville_dir / "visuals" / "the_ville_jan7.json")
    original_width, original_height = tiled["width"], tiled["height"]
    zone_ids = append_kasukabe_genesis_zones(tiled, blocks)
    world = build_world_map_from_tiled(tiled, blocks)
    return world, zone_ids, original_width, original_height


def test_genesis_zones_grow_the_map_south(genesis_zones: GenesisZonesFixture) -> None:
    world, _zone_ids, original_width, original_height = genesis_zones
    assert world.width == original_width
    assert world.height == original_height + 30


def test_genesis_zones_mint_five_new_sectors(genesis_zones: GenesisZonesFixture) -> None:
    world, zone_ids, *_ = genesis_zones
    for sector_id in (
        zone_ids.government_sector_id,
        zone_ids.temple_sector_id,
        zone_ids.farmland_sector_id,
        zone_ids.ore_outcrop_sector_id,
        zone_ids.forest_sector_id,
    ):
        assert sector_id in world.sectors


def test_government_complex_has_two_arenas(genesis_zones: GenesisZonesFixture) -> None:
    world, zone_ids, *_ = genesis_zones
    sector = world.sectors[zone_ids.government_sector_id]
    assert sector.name == GOVERNMENT_SECTOR_NAME
    assert len(sector.arena_ids) == 2
    arena_names = {world.arenas[aid].name for aid in sector.arena_ids}
    assert arena_names == {"Town Hall", "Treasury Vault"}


def test_temple_has_one_arena(genesis_zones: GenesisZonesFixture) -> None:
    world, zone_ids, *_ = genesis_zones
    sector = world.sectors[zone_ids.temple_sector_id]
    assert sector.name == TEMPLE_SECTOR_NAME
    assert len(sector.arena_ids) == 1


def test_farmland_has_ten_crop_arenas(genesis_zones: GenesisZonesFixture) -> None:
    world, zone_ids, *_ = genesis_zones
    sector = world.sectors[zone_ids.farmland_sector_id]
    assert sector.name == FARMLAND_SECTOR_NAME
    assert len(sector.arena_ids) == 10


def test_ore_outcrop_has_five_metal_arenas(genesis_zones: GenesisZonesFixture) -> None:
    world, zone_ids, *_ = genesis_zones
    sector = world.sectors[zone_ids.ore_outcrop_sector_id]
    assert sector.name == ORE_OUTCROP_SECTOR_NAME
    assert len(sector.arena_ids) == 5


def test_forest_has_one_arena(genesis_zones: GenesisZonesFixture) -> None:
    world, zone_ids, *_ = genesis_zones
    sector = world.sectors[zone_ids.forest_sector_id]
    assert sector.name == FOREST_SECTOR_NAME
    assert len(sector.arena_ids) == 1


def test_genesis_zone_land_is_walkable(genesis_zones: GenesisZonesFixture) -> None:
    world, zone_ids, *_ = genesis_zones
    sector = world.sectors[zone_ids.farmland_sector_id]
    assert all(world.is_walkable(tile) for tile in sector.tiles)


def test_genesis_zones_do_not_overlap(genesis_zones: GenesisZonesFixture) -> None:
    world, zone_ids, *_ = genesis_zones
    sectors = [
        world.sectors[sid]
        for sid in (
            zone_ids.government_sector_id,
            zone_ids.temple_sector_id,
            zone_ids.farmland_sector_id,
            zone_ids.ore_outcrop_sector_id,
            zone_ids.forest_sector_id,
        )
    ]
    seen: set = set()
    for sector in sectors:
        assert not (sector.tiles & seen), sector.name
        seen |= sector.tiles


def test_genesis_zones_are_classified_as_resource_or_building_after_apply_genesis_config(
    ville_dir: Path,
) -> None:
    from world.map.genesis import apply_genesis_config

    blocks = load_special_blocks(ville_dir)
    tiled = load_tiled_map(ville_dir / "visuals" / "the_ville_jan7.json")
    zone_ids = append_kasukabe_genesis_zones(tiled, blocks)
    world = build_world_map_from_tiled(tiled, blocks)
    apply_genesis_config(world)

    assert world.sectors[zone_ids.government_sector_id].active
    assert world.sectors[zone_ids.temple_sector_id].active
    assert world.sectors[zone_ids.farmland_sector_id].resource_kind is ResourceKind.FARMLAND
    assert world.sectors[zone_ids.ore_outcrop_sector_id].resource_kind is ResourceKind.ORE_OUTCROP
    assert world.sectors[zone_ids.forest_sector_id].resource_kind is ResourceKind.FOREST
    assert world.sectors[zone_ids.farmland_sector_id].active
    assert world.sectors[zone_ids.ore_outcrop_sector_id].active
    assert world.sectors[zone_ids.forest_sector_id].active
