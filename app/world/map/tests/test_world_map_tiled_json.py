"""Tests for world.map.tiled_json: layer get/set, append_rows/append_columns
growth, stamp_rect, regenerate_collision_layer, IdAllocator, and cloning -
against the tiny synthetic fixture (fast, exact) plus one integration
check that the real Ville visuals JSON parses and round-trips through
build_world_map_from_tiled correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.map.blocks import load_special_blocks
from world.map.loader import load_ville_map
from world.map.tiled_json import (
    COLLISION_LAYER,
    IdAllocator,
    TiledJsonError,
    append_columns,
    append_rows,
    build_world_map_from_tiled,
    clone_tiled_map,
    get_layer,
    load_tiled_map,
    regenerate_collision_layer,
    set_layer,
    stamp_rect,
    validate_tiled_map,
)


def test_get_layer_returns_the_named_layers_data(tiny_tiled: dict) -> None:
    data = get_layer(tiny_tiled, COLLISION_LAYER)
    assert len(data) == 25
    assert data[0] == 1


def test_get_layer_missing_name_raises(tiny_tiled: dict) -> None:
    with pytest.raises(TiledJsonError):
        get_layer(tiny_tiled, "Does Not Exist")


def test_set_layer_replaces_data_and_updates_declared_dimensions(tiny_tiled: dict) -> None:
    new_data = [0] * 25
    set_layer(tiny_tiled, COLLISION_LAYER, new_data)
    for layer in tiny_tiled["layers"]:
        if layer["name"] == COLLISION_LAYER:
            assert layer["data"] == new_data
            assert layer["width"] == tiny_tiled["width"]
            assert layer["height"] == tiny_tiled["height"]


def test_validate_tiled_map_accepts_the_fixture(tiny_tiled: dict) -> None:
    validate_tiled_map(tiny_tiled)


def test_validate_tiled_map_reports_every_missing_layer() -> None:
    broken = {"width": 5, "height": 5, "tilewidth": 32, "layers": []}
    with pytest.raises(TiledJsonError) as exc_info:
        validate_tiled_map(broken)
    assert len(exc_info.value.errors) == 5


def test_append_rows_grows_height_not_width(tiny_tiled: dict) -> None:
    append_rows(tiny_tiled, 3)
    assert tiny_tiled["width"] == 5
    assert tiny_tiled["height"] == 8


def test_append_rows_preserves_existing_cells_and_fills_new_ones(tiny_tiled: dict) -> None:
    original_collision = list(get_layer(tiny_tiled, COLLISION_LAYER))
    append_rows(tiny_tiled, 2, fill_by_layer={COLLISION_LAYER: 9})
    grown = get_layer(tiny_tiled, COLLISION_LAYER)
    assert grown[: len(original_collision)] == original_collision
    assert grown[len(original_collision):] == [9] * (5 * 2)


def test_append_rows_default_fill_is_zero(tiny_tiled: dict) -> None:
    append_rows(tiny_tiled, 1)
    grown = get_layer(tiny_tiled, "Bottom Ground")
    assert grown[-5:] == [0] * 5


def test_append_rows_rejects_non_positive_count(tiny_tiled: dict) -> None:
    with pytest.raises(ValueError):
        append_rows(tiny_tiled, 0)


def test_append_columns_grows_width_not_height(tiny_tiled: dict) -> None:
    append_columns(tiny_tiled, 3)
    assert tiny_tiled["width"] == 8
    assert tiny_tiled["height"] == 5


def test_append_columns_inserts_at_the_end_of_every_row(tiny_tiled: dict) -> None:
    append_columns(tiny_tiled, 2, fill_by_layer={COLLISION_LAYER: 7})
    grown = get_layer(tiny_tiled, COLLISION_LAYER)
    new_width = 7

    row0 = grown[0 * new_width : 1 * new_width]
    assert row0 == [1, 1, 1, 1, 1, 7, 7]

    row1 = grown[1 * new_width : 2 * new_width]
    assert row1 == [1, 0, 0, 0, 1, 7, 7]


def test_stamp_rect_sets_exactly_the_requested_region(tiny_tiled: dict) -> None:
    stamp_rect(tiny_tiled, COLLISION_LAYER, 1, 1, 3, 2, 42)
    grid = get_layer(tiny_tiled, COLLISION_LAYER)
    width = tiny_tiled["width"]
    assert grid[1 * width + 1] == 42
    assert grid[1 * width + 2] == 42
    assert grid[1 * width + 3] != 42
    assert grid[2 * width + 1] != 42


def test_stamp_rect_out_of_bounds_raises(tiny_tiled: dict) -> None:
    with pytest.raises(ValueError):
        stamp_rect(tiny_tiled, COLLISION_LAYER, 0, 0, 999, 1, 1)


def test_regenerate_collision_layer_matches_the_fixture(tiny_tiled: dict) -> None:
    grid = regenerate_collision_layer(tiny_tiled)
    assert grid[0][0] is True
    assert grid[1][1] is False
    assert grid[3][2] is False
    assert grid[4][0] is False


def test_regenerate_collision_layer_reflects_a_stamp(tiny_tiled: dict) -> None:
    stamp_rect(tiny_tiled, COLLISION_LAYER, 4, 4, 5, 5, 1)
    grid = regenerate_collision_layer(tiny_tiled)
    assert grid[4][4] is True


def test_clone_tiled_map_is_a_deep_independent_copy(tiny_tiled: dict) -> None:
    clone = clone_tiled_map(tiny_tiled)
    append_rows(clone, 1)
    assert tiny_tiled["height"] == 5
    assert clone["height"] == 6


def test_id_allocator_yields_sequential_ids_from_the_floor() -> None:
    allocator = IdAllocator(floor=1000)
    assert allocator.next() == 1000
    assert allocator.next() == 1001
    assert allocator.next() == 1002


def test_id_allocator_default_floor_is_well_above_any_real_ville_gid() -> None:
    from world.map.tiled_json import SYNTHETIC_ID_FLOOR

    assert SYNTHETIC_ID_FLOOR > 40_000


def test_real_tiled_json_loads_and_validates(ville_dir: Path) -> None:
    tiled = load_tiled_map(ville_dir / "visuals" / "the_ville_jan7.json")
    validate_tiled_map(tiled)
    assert tiled["width"] == 140
    assert tiled["height"] == 100


def test_real_tiled_json_builds_the_same_sector_count_as_the_csv_loader(ville_dir: Path) -> None:
    tiled = load_tiled_map(ville_dir / "visuals" / "the_ville_jan7.json")
    blocks = load_special_blocks(ville_dir)
    from_json = build_world_map_from_tiled(tiled, blocks)
    from_csv = load_ville_map(ville_dir)
    assert len(from_json.sectors) == len(from_csv.sectors) == 19
