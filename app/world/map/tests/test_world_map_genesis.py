"""Tests for world.map.genesis: classify_sector's rules and
apply_genesis_config/build_kasukabe_base_map against the real Ville data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.map.genesis import (
    GENESIS_ACTIVE_CATEGORIES,
    build_kasukabe_base_map,
    classify_sector,
)
from world.map.model import BuildingCategory, WorldMap


def test_hobbs_cafe_classifies_as_cafe() -> None:
    assert classify_sector("Hobbs Cafe") == frozenset({BuildingCategory.CAFE})


def test_pub_classifies_as_pub_not_genesis_active() -> None:
    categories = classify_sector("The Rose and Crown Pub")
    assert categories == frozenset({BuildingCategory.PUB})
    assert not (categories & GENESIS_ACTIVE_CATEGORIES)


def test_market_and_pharmacy_gets_both_categories() -> None:
    categories = classify_sector("The Willows Market and Pharmacy")
    assert categories == frozenset({BuildingCategory.GROCERY, BuildingCategory.PHARMACY})


def test_dorm_classifies_as_college() -> None:
    assert classify_sector("Dorm for Oak Hill College") == frozenset({BuildingCategory.COLLEGE})


def test_apartment_classifies_as_house() -> None:
    assert classify_sector("Ryan Park's apartment") == frozenset({BuildingCategory.HOUSE})


def test_unknown_name_with_house_keyword_falls_back_to_house() -> None:
    assert classify_sector("Some New Family's House") == frozenset({BuildingCategory.HOUSE})


def test_unknown_name_with_no_matching_keyword_is_unclassified() -> None:
    assert classify_sector("Xyzzy Nonsense Place") == frozenset()


def test_fallback_is_case_insensitive() -> None:
    assert classify_sector("THE TOWN CAFE") == frozenset({BuildingCategory.CAFE})


def test_leading_and_trailing_whitespace_is_stripped() -> None:
    assert classify_sector("  Hobbs Cafe  ") == frozenset({BuildingCategory.CAFE})


@pytest.fixture(scope="session")
def base_map(ville_dir: Path) -> WorldMap:
    return build_kasukabe_base_map(ville_dir)


def test_hobbs_cafe_is_active(base_map: WorldMap) -> None:
    sector = base_map.find_sector("Hobbs Cafe")
    assert sector is not None
    assert sector.active
    assert BuildingCategory.CAFE in sector.categories


def test_pub_is_present_but_inactive(base_map: WorldMap) -> None:
    sector = base_map.find_sector("The Rose and Crown Pub")
    assert sector is not None
    assert not sector.active
    assert BuildingCategory.PUB in sector.categories


def test_pub_tiles_are_still_walkable(base_map: WorldMap) -> None:
    sector = base_map.find_sector("The Rose and Crown Pub")
    assert sector is not None
    walkable_tiles = [tile for tile in sector.tiles if base_map.is_walkable(tile)]
    assert walkable_tiles


def test_all_houses_are_active(base_map: WorldMap) -> None:
    house_sector_names = [
        "Adam Smith's house",
        "Yuriko Yamamoto's house",
        "Moore family's house",
        "Moreno family's house",
        "Lin family's house",
    ]
    for name in house_sector_names:
        sector = base_map.find_sector(name)
        assert sector is not None, name
        assert sector.active, name
        assert BuildingCategory.HOUSE in sector.categories, name


def test_college_and_dorm_are_active(base_map: WorldMap) -> None:
    for name in ("Oak Hill College", "Dorm for Oak Hill College"):
        sector = base_map.find_sector(name)
        assert sector is not None
        assert sector.active
        assert BuildingCategory.COLLEGE in sector.categories


def test_market_and_pharmacy_active_with_both_categories(base_map: WorldMap) -> None:
    sector = base_map.find_sector("The Willows Market and Pharmacy")
    assert sector is not None
    assert sector.active
    assert sector.categories == frozenset({BuildingCategory.GROCERY, BuildingCategory.PHARMACY})


def test_supply_store_classified_as_office_and_active(base_map: WorldMap) -> None:
    sector = base_map.find_sector("Harvey Oak Supply Store")
    assert sector is not None
    assert sector.active
    assert BuildingCategory.OFFICE in sector.categories


def test_park_is_active(base_map: WorldMap) -> None:
    sector = base_map.find_sector("Johnson Park")
    assert sector is not None
    assert sector.active
    assert BuildingCategory.PARK in sector.categories


def test_exactly_one_sector_is_inactive_on_the_base_map(base_map: WorldMap) -> None:
    inactive = [s.name for s in base_map.sectors.values() if not s.active]
    assert inactive == ["The Rose and Crown Pub"]


def test_known_exterior_tile_is_a_street(base_map: WorldMap) -> None:
    from world.map.model import TileCoord

    for x, y in ((5, 5), (0, 0)):
        tile = TileCoord(x, y)
        assert base_map.is_walkable(tile)
        assert base_map.sector_at(tile) is None
        assert base_map.is_street(tile)


def test_known_building_tile_is_not_a_street(base_map: WorldMap) -> None:
    sector = base_map.find_sector("Harvey Oak Supply Store")
    assert sector is not None
    tile = next(t for t in sector.tiles if base_map.is_walkable(t))
    assert not base_map.is_street(tile)
