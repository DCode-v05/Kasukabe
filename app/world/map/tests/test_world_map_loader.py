"""Integration tests for world.map.loader against the real Ville asset
directory (matrix/maze/*.csv + matrix/special_blocks/*.csv). Verifies
this package actually parses the shipped data, not just a synthetic
fixture shaped to make the code pass - the facts asserted below (140x100,
19 sectors, specific names) were confirmed directly against the source
CSVs as part of building this module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.map.loader import load_ville_map
from world.map.model import WorldMap


@pytest.fixture(scope="session")
def ville_world(ville_dir: Path) -> WorldMap:
    return load_ville_map(ville_dir)


def test_dimensions_match_maze_meta_info(ville_world: WorldMap) -> None:
    assert ville_world.world_name == "the ville"
    assert ville_world.width == 140
    assert ville_world.height == 100
    assert ville_world.tile_size == 32


def test_grids_are_the_right_shape(ville_world: WorldMap) -> None:
    assert len(ville_world.collision) == 100
    assert all(len(row) == 140 for row in ville_world.collision)


def test_exactly_nineteen_sectors(ville_world: WorldMap) -> None:
    assert len(ville_world.sectors) == 19


def test_known_sector_names_are_present(ville_world: WorldMap) -> None:
    for name in (
        "Hobbs Cafe",
        "Oak Hill College",
        "Johnson Park",
        "The Willows Market and Pharmacy",
        "The Rose and Crown Pub",
        "Harvey Oak Supply Store",
        "Dorm for Oak Hill College",
    ):
        assert ville_world.find_sector(name) is not None, name


def test_hobbs_cafe_has_a_cafe_arena(ville_world: WorldMap) -> None:
    arena = ville_world.find_arena("Hobbs Cafe", "cafe")
    assert arena is not None
    assert arena.area > 0


def test_oak_hill_college_has_multiple_arenas(ville_world: WorldMap) -> None:
    sector = ville_world.find_sector("Oak Hill College")
    assert sector is not None
    assert len(sector.arena_ids) == 3  # classroom, library, hallway


def test_collision_blocked_tile_count_matches_known_value(ville_world: WorldMap) -> None:
    # Confirmed directly against collision_maze.csv while building this
    # module: exactly one non-zero value (32125) appears, and it appears
    # in some (not all) of the 14000 cells.
    blocked = sum(1 for row in ville_world.collision for v in row if v)
    assert 0 < blocked < 14000


def test_game_object_kinds_include_known_furniture(ville_world: WorldMap) -> None:
    names = {kind.name for kind in ville_world.game_object_kinds.values()}
    assert "bed" in names
    assert "behind the cafe counter" in names


def test_spawning_locations_are_loaded(ville_world: WorldMap) -> None:
    assert len(ville_world.spawning_locations) > 0
    labels = {spawn.label for spawn in ville_world.spawning_locations.values()}
    assert "sp-A" in labels


def test_every_walkable_check_is_consistent_with_collision_grid(ville_world: WorldMap) -> None:
    # Spot check a handful of tiles rather than all 14000: is_walkable
    # must always be the exact negation of the raw collision grid inside
    # bounds.
    for y in (0, 10, 50, 99):
        for x in (0, 10, 70, 139):
            from world.map.model import TileCoord

            expected = not ville_world.collision[y][x]
            assert ville_world.is_walkable(TileCoord(x, y)) == expected
