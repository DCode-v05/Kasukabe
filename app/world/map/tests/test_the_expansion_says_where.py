"""A growth event has to say where the town grew.

`GROW-26`. `LandExpansion` is the payload of `world.land.expanded`, and it
carried edge, depth, and the old and new dimensions - a receipt that the
town got bigger, with no way to learn WHICH TILES are new. So every
subscriber that wanted the new land had to re-fetch the whole Tiled map:
`town.js` re-downloads it on every expansion event and, per UI-01, gets
the same bytes back.

`approve_land_petition` already computes the rect - it needs it to carve
the new sectors into - and threw it away. Carrying it out is free.

Uses this package's own `tiny_tiled` / `tiny_blocks` fixtures rather than
a hand-rolled map, so the assertion is against the real growth path.
"""
from __future__ import annotations

from typing import Any

import pytest

from world.map.expansion import Edge, approve_land_petition
from world.map.tiled_json import SpecialBlockTables


@pytest.mark.asyncio
async def test_a_south_expansion_names_the_rows_it_added(
    tiny_tiled: dict[str, Any], tiny_blocks: SpecialBlockTables
) -> None:
    original_height = tiny_tiled["height"]
    _, _, _, result = await approve_land_petition(
        tiny_tiled, tiny_blocks, edge=Edge.SOUTH, depth=3
    )

    x0, y0, x1, y1 = result.strip_rect
    assert (x0, y0) == (0, original_height), (
        "GROW-26: a south strip begins at the old height"
    )
    assert y1 == result.new_height
    assert x1 == result.new_width
    assert y1 - y0 == 3, "the strip is as deep as the petition asked for"


@pytest.mark.asyncio
async def test_an_east_expansion_names_the_columns_it_added(
    tiny_tiled: dict[str, Any], tiny_blocks: SpecialBlockTables
) -> None:
    original_width = tiny_tiled["width"]
    _, _, _, result = await approve_land_petition(
        tiny_tiled, tiny_blocks, edge=Edge.EAST, depth=4
    )

    x0, y0, x1, y1 = result.strip_rect
    assert (x0, y0) == (original_width, 0), (
        "GROW-26: an east strip begins at the old width"
    )
    assert x1 - x0 == 4
    assert y1 == result.new_height


@pytest.mark.asyncio
async def test_the_rect_is_inside_the_grown_map(
    tiny_tiled: dict[str, Any], tiny_blocks: SpecialBlockTables
) -> None:
    """A delta a subscriber cannot index into is no better than a receipt."""
    _, _, world, result = await approve_land_petition(
        tiny_tiled, tiny_blocks, edge=Edge.SOUTH, depth=2
    )
    x0, y0, x1, y1 = result.strip_rect
    assert 0 <= x0 < x1 <= world.width
    assert 0 <= y0 < y1 <= world.height
