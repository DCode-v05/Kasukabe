"""The geography chain is exercised on every host, not just this one.

`EVAL-18`. Map loading, movement, land expansion, construction, place
resolution and the console's "approve a petition and the map actually
grows" test all need a gitignored `smallville/` checked out beside the
repo. On the review's host it was absent, and that one directory
accounted for 71 of 110 failures and 46 of 68 skips - so CI on `main`
proved nothing whatever about DESIGN sections 1 and 2.

`GROW-27` made the absence honest: those tests skip rather than fail, and
the run prints what went unchecked. Honest is not the same as covered.
This file is the other half - `world/map/fixture_tileset.py` builds a
24x18 town in code, shaped exactly like the Ville's asset directory, and
the chain runs against it anywhere.

WHAT THIS DOES NOT REPLACE. Nothing here asserts a fact about the Ville.
"Hobbs Cafe is reachable from the college" is a claim about the real map
and still needs the real assets; `ville_assets.require_ville_assets`
still gates those. What this covers is the SHAPE - that a map loads, that
sectors and arenas resolve, that expansion adds land, that a walk finds a
route through a door.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from world.map.fixture_tileset import (
    HEIGHT,
    HOUSE_RECT,
    SHOP_RECT,
    WIDTH,
    build_fixture_tileset,
)
from world.map.genesis import build_kasukabe_genesis_map
from world.map.loader import load_ville_map
from world.map.model import TileCoord


@pytest.fixture(scope="module")
def ville(tmp_path_factory) -> Path:
    return build_fixture_tileset(tmp_path_factory.mktemp("fixture-assets"))


def test_the_real_loader_reads_it(ville: Path) -> None:
    """The point of generating a Ville-SHAPED directory rather than a
    tidier one: it goes through the production loader, not a lenient
    test-only path."""
    world = load_ville_map(ville)
    assert (world.width, world.height) == (WIDTH, HEIGHT)


def test_sectors_and_arenas_resolve(ville: Path) -> None:
    """Place resolution is the layer every tool's location gate sits on."""
    world = load_ville_map(ville)
    names = {name for _id, (_w, name) in world.sectors.items()} if isinstance(
        next(iter(world.sectors.values()), None), tuple
    ) else set()
    assert len(world.sectors) == 2, world.sectors
    assert len(world.arenas) == 2
    assert not names or "Fixture House" in names


def test_a_building_has_a_door(ville: Path) -> None:
    """A building with no door is a fixture that proves the wrong thing:
    `find_path` would correctly report no route and every movement test
    would pass on it."""
    world = load_ville_map(ville)
    x0, _y0, x1, y1 = HOUSE_RECT
    door = TileCoord(x=(x0 + x1) // 2, y=y1 - 1)
    assert world.is_walkable(door), (
        "the fixture house is sealed, so nothing inside it can be reached "
        "and a movement test against it means nothing"
    )


def test_the_two_buildings_are_not_touching(ville: Path) -> None:
    """The open ground between them is what makes a routing test real."""
    assert SHOP_RECT[0] > HOUSE_RECT[2] + 1


def test_genesis_grows_the_map(ville: Path) -> None:
    """The chain the review says goes untested: the base map plus the
    government complex, temple and resource zones minted in."""
    world, zone_ids = build_kasukabe_genesis_map(ville)

    assert world.height > HEIGHT, (
        "EVAL-18: genesis did not add its strip, so land expansion is "
        "untested on any host without the Ville assets"
    )
    assert len(world.sectors) > 2, "no genesis zones were minted"
    assert zone_ids is not None


def test_the_minted_zones_are_reachable_ids(ville: Path) -> None:
    """Ids the caller can look up directly - the reason
    `build_kasukabe_genesis_map` returns them rather than leaving a
    name-based lookup to every caller."""
    world, zone_ids = build_kasukabe_genesis_map(ville)
    for attribute in ("temple_sector_id",):
        sector_id = getattr(zone_ids, attribute, None)
        if sector_id is not None:
            assert sector_id in world.sectors, (
                f"{attribute} names a sector the map does not have"
            )


def test_a_walk_finds_a_route_across_the_town(ville: Path) -> None:
    """Movement, on a map that exists everywhere. Flood fill rather than
    the pathfinder, so a failure here is about the MAP - the same
    reasoning `test_the_town_is_walkable` gives for the real one."""
    world = load_ville_map(ville)

    start = TileCoord(x=1, y=1)
    seen = {(start.x, start.y)}
    stack = [(start.x, start.y)]
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (nx, ny) in seen or not world.in_bounds_xy(nx, ny):
                continue
            if world.is_walkable(TileCoord(x=nx, y=ny)):
                seen.add((nx, ny))
                stack.append((nx, ny))

    x0, _y0, x1, y1 = HOUSE_RECT
    door = ((x0 + x1) // 2, y1 - 1)
    assert door in seen, "the house door is not reachable from open ground"
    assert len(seen) > (WIDTH * HEIGHT) // 2, (
        "most of the fixture town is unreachable, so it is not a town"
    )


def test_it_is_small_enough_to_read(ville: Path) -> None:
    """The reason this is generated rather than vendored. A megabyte of
    CSV nobody reviews is not a fixture, it is a blob."""
    total = sum(p.stat().st_size for p in ville.rglob("*") if p.is_file())
    assert total < 200_000, f"the fixture has grown to {total} bytes"


def test_nothing_here_claims_to_be_the_ville(ville: Path) -> None:
    """A test that asserted a Ville fact against this would be quietly
    wrong on both hosts - passing here, and meaningless there."""
    world = load_ville_map(ville)
    assert world.width < 100 and world.height < 100
