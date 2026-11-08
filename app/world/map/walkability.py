"""Pure grid-neighbor math, shared by pathfinding.py and WorldMap.nearest_walkable.

Kept free of any WorldMap/TileCoord dependency on purpose: these are raw
(x, y) int-tuple functions, the smallest possible unit of "tile geometry"
app/CONVENTIONS.md's pure-computation carve-out describes, so they are
trivial to unit test and reuse anywhere a bare grid (no Sector/Arena
metadata attached) is all that is available.
"""

from __future__ import annotations

Point = tuple[int, int]

FOUR_DIRECTIONS: tuple[Point, ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIAGONAL_DIRECTIONS: tuple[Point, ...] = ((1, 1), (1, -1), (-1, 1), (-1, -1))


def neighbors4(x: int, y: int) -> tuple[Point, ...]:
    return tuple((x + dx, y + dy) for dx, dy in FOUR_DIRECTIONS)


def neighbors8(x: int, y: int) -> tuple[Point, ...]:
    return neighbors4(x, y) + tuple((x + dx, y + dy) for dx, dy in DIAGONAL_DIRECTIONS)


def in_bounds_xy(width: int, height: int, x: int, y: int) -> bool:
    return 0 <= x < width and 0 <= y < height
