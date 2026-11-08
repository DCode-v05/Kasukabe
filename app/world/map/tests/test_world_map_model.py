"""Unit tests for world.map.model: TileCoord, build_world_map, and every
WorldMap lookup method, against the tiny synthetic fixture in conftest.py
(a 5x5 map: one house with a door, one exterior street tile row) so every
expected answer is knowable by inspection.
"""

from __future__ import annotations

import pytest

from world.map.model import BuildingCategory, NamedLocation, ResourceKind, TileCoord, WorldMap


def test_tile_coord_rejects_negative_x() -> None:
    with pytest.raises(ValueError):
        TileCoord(-1, 0)


def test_tile_coord_rejects_negative_y() -> None:
    with pytest.raises(ValueError):
        TileCoord(0, -1)


def test_tile_coord_is_hashable_and_usable_as_a_set_member() -> None:
    coords = {TileCoord(1, 2), TileCoord(1, 2), TileCoord(3, 4)}
    assert coords == {TileCoord(1, 2), TileCoord(3, 4)}


def test_build_world_map_sets_dimensions(tiny_world: WorldMap) -> None:
    assert tiny_world.world_name == "Test Town"
    assert tiny_world.width == 5
    assert tiny_world.height == 5
    assert tiny_world.tile_size == 32


def test_walls_are_not_walkable(tiny_world: WorldMap) -> None:
    assert not tiny_world.is_walkable(TileCoord(0, 0))


def test_house_interior_is_walkable(tiny_world: WorldMap) -> None:
    assert tiny_world.is_walkable(TileCoord(2, 2))


def test_door_is_walkable(tiny_world: WorldMap) -> None:
    assert tiny_world.is_walkable(TileCoord(2, 3))


def test_out_of_bounds_is_not_walkable(tiny_world: WorldMap) -> None:
    assert not tiny_world.is_walkable(TileCoord(99, 99))


def test_out_of_bounds_is_not_in_bounds(tiny_world: WorldMap) -> None:
    assert not tiny_world.in_bounds(TileCoord(5, 0))
    assert not tiny_world.in_bounds(TileCoord(0, 5))
    assert tiny_world.in_bounds(TileCoord(4, 4))


def test_street_tile_is_walkable_and_has_no_sector(tiny_world: WorldMap) -> None:
    street_tile = TileCoord(0, 4)
    assert tiny_world.is_street(street_tile)
    assert tiny_world.sector_at(street_tile) is None


def test_house_tile_is_not_a_street(tiny_world: WorldMap) -> None:
    assert not tiny_world.is_street(TileCoord(2, 2))


def test_sector_at_resolves_the_house(tiny_world: WorldMap) -> None:
    sector = tiny_world.sector_at(TileCoord(2, 2))
    assert sector is not None
    assert sector.name == "Test House"


def test_arena_at_resolves_the_room(tiny_world: WorldMap) -> None:
    arena = tiny_world.arena_at(TileCoord(2, 2))
    assert arena is not None
    assert arena.name == "Main Room"


def test_door_belongs_to_sector_but_no_arena(tiny_world: WorldMap) -> None:
    door = TileCoord(2, 3)
    assert tiny_world.sector_at(door) is not None
    assert tiny_world.arena_at(door) is None


def test_game_object_at_bed_tile(tiny_world: WorldMap) -> None:
    kind = tiny_world.game_object_at(TileCoord(1, 1))
    assert kind is not None
    assert kind.name == "bed"


def test_game_object_at_empty_tile_is_none(tiny_world: WorldMap) -> None:
    assert tiny_world.game_object_at(TileCoord(2, 2)) is None


def test_spawning_location_at_sp_a(tiny_world: WorldMap) -> None:
    spawn = tiny_world.spawning_location_at(TileCoord(2, 2))
    assert spawn is not None
    assert spawn.label == "sp-A"
    assert spawn.sector_name == "Test House"
    assert spawn.arena_name == "Main Room"


def test_sector_area_counts_every_sector_tile(tiny_world: WorldMap) -> None:
    sector = tiny_world.find_sector("Test House")
    assert sector is not None
    assert sector.area == 7


def test_arena_area_excludes_the_door(tiny_world: WorldMap) -> None:
    arena = tiny_world.find_arena("Test House", "Main Room")
    assert arena is not None
    assert arena.area == 6


def test_sector_arena_ids_link_back_to_the_arena(tiny_world: WorldMap) -> None:
    sector = tiny_world.find_sector("Test House")
    arena = tiny_world.find_arena("Test House", "Main Room")
    assert sector is not None and arena is not None
    assert arena.id in sector.arena_ids


def test_find_sector_unknown_name_is_none(tiny_world: WorldMap) -> None:
    assert tiny_world.find_sector("Nonexistent Place") is None


def test_find_arena_unknown_arena_name_is_none(tiny_world: WorldMap) -> None:
    assert tiny_world.find_arena("Test House", "Attic") is None


def test_find_arena_unknown_sector_name_is_none(tiny_world: WorldMap) -> None:
    assert tiny_world.find_arena("Nonexistent Place", "Main Room") is None


def test_describe_house_tile(tiny_world: WorldMap) -> None:
    location = tiny_world.describe(TileCoord(2, 2))
    assert location == NamedLocation(
        world_name="Test Town", sector_name="Test House", arena_name="Main Room", label="sp-A"
    )


def test_describe_street_tile_has_only_world_name(tiny_world: WorldMap) -> None:
    location = tiny_world.describe(TileCoord(0, 4))
    assert location.world_name == "Test Town"
    assert location.sector_name is None
    assert location.arena_name is None


def test_named_location_address_joins_present_levels() -> None:
    location = NamedLocation("the Ville", "Hobbs Cafe", "cafe")
    assert location.address() == "the Ville:Hobbs Cafe:cafe"


def test_named_location_address_world_only() -> None:
    assert NamedLocation("the Ville").address() == "the Ville"


def test_search_finds_sector_by_partial_name(tiny_world: WorldMap) -> None:
    hits = tiny_world.search("test hou")
    assert any(hit.sector_name == "Test House" for hit in hits)


def test_search_finds_arena_by_partial_name(tiny_world: WorldMap) -> None:
    hits = tiny_world.search("main")
    assert any(hit.arena_name == "Main Room" for hit in hits)


def test_search_empty_query_returns_nothing(tiny_world: WorldMap) -> None:
    assert tiny_world.search("   ") == []


def test_entry_point_prefers_the_spawning_location(tiny_world: WorldMap) -> None:
    assert tiny_world.entry_point("Test House", "Main Room") == TileCoord(2, 2)


def test_entry_point_for_unknown_sector_is_none(tiny_world: WorldMap) -> None:
    assert tiny_world.entry_point("Nonexistent Place") is None


def test_nearest_walkable_returns_self_when_already_walkable(tiny_world: WorldMap) -> None:
    assert tiny_world.nearest_walkable(TileCoord(2, 2)) == TileCoord(2, 2)


def test_nearest_walkable_steps_off_a_wall(tiny_world: WorldMap) -> None:
    result = tiny_world.nearest_walkable(TileCoord(0, 0), max_radius=4)
    assert result is not None
    assert tiny_world.is_walkable(result)


def test_nearest_walkable_gives_up_past_max_radius(tiny_world: WorldMap) -> None:
    assert tiny_world.nearest_walkable(TileCoord(0, 0), max_radius=0) is None


def test_resource_kind_and_building_category_are_distinct_string_enums() -> None:
    assert BuildingCategory.CAFE.value == "cafe"
    assert ResourceKind.FARMLAND.value == "farmland"
    assert BuildingCategory.CAFE != ResourceKind.FARMLAND
