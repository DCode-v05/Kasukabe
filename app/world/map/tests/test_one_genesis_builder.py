"""One sequence for building the genesis map, not two.

`GROW-21`. `MapService.from_kasukabe_genesis` and
`genesis.build_kasukabe_genesis_map` were step-for-step copies: load
blocks, load the Tiled JSON, mint the genesis zones, open the walkable
corridors, build the WorldMap, apply the genesis config. The only real
difference was that the service KEEPS `tiled` and `blocks` afterwards,
because `expand_land` needs every id minted so far to keep growing the
same map.

Why two copies cost something rather than merely being untidy: the
corridor step. The Ville map and the genesis strip are each internally
walkable and were never joined - 17 separate regions, residents in one and
every workplace in another, and 107 `move` calls in a single run answering
"no walkable route". A fix applied to one copy and not the other would
have left one host walkable and the other not, with both looking correct
in isolation.

These tests assert the two entry points agree, which is the property that
was silently unenforced.
"""
from __future__ import annotations

import pytest

from world.map.genesis import build_kasukabe_genesis_map, build_kasukabe_genesis_parts
from world.map.model import TileCoord
from world.map.service import MapService
from world.tools.bootstrap import DEFAULT_VILLE_DIR

pytestmark = pytest.mark.skipif(
    not DEFAULT_VILLE_DIR.exists(),
    reason="Ville assets absent (GROW-27)",
)


def test_both_entry_points_produce_the_same_map() -> None:
    """Same dimensions, same sectors, same active set - or the two hosts
    are not standing on the same town."""
    direct, direct_ids = build_kasukabe_genesis_map(DEFAULT_VILLE_DIR)
    service, service_ids = MapService.from_kasukabe_genesis(DEFAULT_VILLE_DIR)

    assert (direct.width, direct.height) == (service.world.width, service.world.height)
    assert set(direct.sectors) == set(service.world.sectors)
    assert {s.name for s in direct.sectors.values()} == {
        s.name for s in service.world.sectors.values()
    }
    assert {s.name for s in direct.sectors.values() if s.active} == {
        s.name for s in service.world.sectors.values() if s.active
    }


def test_both_mint_the_same_genesis_zones() -> None:
    _, direct_ids = build_kasukabe_genesis_map(DEFAULT_VILLE_DIR)
    _, service_ids = MapService.from_kasukabe_genesis(DEFAULT_VILLE_DIR)
    assert direct_ids == service_ids


def test_the_shared_builder_returns_every_piece() -> None:
    """The reason the duplication existed: the service needs `tiled` and
    `blocks` to keep growing later, and the direct builder throws them
    away. One builder hands back all four so neither has to repeat it."""
    tiled, blocks, world, zone_ids = build_kasukabe_genesis_parts(DEFAULT_VILLE_DIR)
    assert tiled["width"] == world.width
    assert tiled["height"] == world.height
    assert blocks is not None
    assert zone_ids.temple_sector_id in world.sectors


def test_the_town_is_joined_to_itself() -> None:
    """The step that made the duplication dangerous.

    Both paths must open the walkable corridors, or a resident cannot walk
    from home to work - and a fix landing on only one copy would leave one
    host walkable and the other not, both looking right.
    """
    service, _ = MapService.from_kasukabe_genesis(DEFAULT_VILLE_DIR)
    direct, _ = build_kasukabe_genesis_map(DEFAULT_VILLE_DIR)

    def walkable_tiles(world_map) -> int:
        return sum(
            1
            for y in range(world_map.height)
            for x in range(world_map.width)
            if world_map.is_walkable(TileCoord(x, y))
        )

    assert walkable_tiles(service.world) == walkable_tiles(direct)
    assert walkable_tiles(direct) > 0, "neither builder produced a walkable town"
