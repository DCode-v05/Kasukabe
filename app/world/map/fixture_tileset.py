"""A small, generated Ville-shaped tileset, so the map chain can be tested.

`EVAL-18`. The whole geography chain - map loading, movement, land
expansion, construction, place resolution, and the console's "approve a
petition and the map actually grows" test - is only exercised when a
gitignored `smallville/` directory happens to be checked out beside the
repo. Without it, 71 of 110 failures and 46 of 68 skips on the review's
run were that one missing directory, and CI on `main` proved nothing at
all about DESIGN sections 1 and 2.

`GROW-27` made the absence loud - the tests skip rather than fail, and
the run says what went unchecked. That is honest, and it still leaves the
chain untested. This is the other half: a tileset small enough to build
in code, shaped exactly like the real one, so the chain runs everywhere.

WHY GENERATED RATHER THAN VENDORED. The upstream assets are Apache-2.0 and
could be committed, and 140x100 tiles of CSV is about a megabyte of data
whose contents nobody would ever read or review. A 24x18 town written by
the function below is a fixture a reader can hold in their head, and
every value in it is here rather than in a blob.

WHAT IT IS NOT. It is not the Ville and no test should assert Ville facts
against it - "Hobbs Cafe is reachable from the college" is a claim about
the real map. What this supports is the SHAPE: that a map loads, that
sectors and arenas resolve, that a walk finds a route, that expansion
adds land and construction stamps a building onto it. A test that needs
the real geography still needs the real assets, and `ville_assets.py`
still gates those.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

# Small enough to reason about, big enough to have an inside and an
# outside: two buildings, a road between them, and open ground.
WIDTH = 24
HEIGHT = 18
TILE_SIZE = 32
WORLD_NAME = "the Fixture"

# Block ids. The real data numbers these in the thousands with wide gaps;
# what matters to every reader of the maze CSVs is only that 0 means
# "nothing here" and that ids are distinct.
NOTHING = 0
WALL = 1
SECTOR_HOUSE = 100
SECTOR_SHOP = 101
ARENA_KITCHEN = 200
ARENA_COUNTER = 201
OBJECT_STOVE = 300
SPAWN_DOORSTEP = 400

# The two sectors, as rectangles (x0, y0, x1, y1), half-open on the far
# edge. Deliberately NOT touching: the corridor of open ground between
# them is what makes a pathfinding test meaningful.
HOUSE_RECT = (2, 2, 8, 7)
SHOP_RECT = (14, 9, 21, 15)


def _blank(fill: int = NOTHING) -> list[int]:
    return [fill] * (WIDTH * HEIGHT)


def _stamp(grid: list[int], rect: tuple[int, int, int, int], value: int) -> None:
    x0, y0, x1, y1 = rect
    for y in range(y0, y1):
        for x in range(x0, x1):
            grid[y * WIDTH + x] = value


def _outline(grid: list[int], rect: tuple[int, int, int, int], value: int) -> None:
    """Walls around a rectangle, with a gap in the bottom edge for a door.

    A building with no door is a building nothing can test: `find_path`
    would correctly report no route and every movement test would pass by
    proving the wrong thing.
    """
    x0, y0, x1, y1 = rect
    door = (x0 + x1) // 2
    for x in range(x0, x1):
        grid[y0 * WIDTH + x] = value
        if x != door:
            grid[(y1 - 1) * WIDTH + x] = value
    for y in range(y0, y1):
        grid[y * WIDTH + x0] = value
        grid[y * WIDTH + (x1 - 1)] = value


def build_fixture_tileset(root: Path) -> Path:
    """Write a complete Ville-shaped asset directory under `root`.

    Returns the directory to hand to `load_ville_map` or
    `build_kasukabe_genesis_map` - the same `ville_dir` those take, with
    the same `matrix/` and `visuals/` layout.
    """
    ville = root / "the_fixture"
    matrix = ville / "matrix"
    (matrix / "maze").mkdir(parents=True, exist_ok=True)
    (matrix / "special_blocks").mkdir(parents=True, exist_ok=True)
    (ville / "visuals").mkdir(parents=True, exist_ok=True)

    (matrix / "maze_meta_info.json").write_text(
        json.dumps({
            "world_name": WORLD_NAME,
            "maze_width": WIDTH,
            "maze_height": HEIGHT,
            "sq_tile_size": TILE_SIZE,
        }),
        encoding="utf-8",
    )

    collision = _blank()
    _outline(collision, HOUSE_RECT, WALL)
    _outline(collision, SHOP_RECT, WALL)

    sector = _blank()
    _stamp(sector, HOUSE_RECT, SECTOR_HOUSE)
    _stamp(sector, SHOP_RECT, SECTOR_SHOP)

    arena = _blank()
    _stamp(arena, (3, 3, 7, 6), ARENA_KITCHEN)
    _stamp(arena, (15, 10, 20, 14), ARENA_COUNTER)

    game_object = _blank()
    game_object[4 * WIDTH + 4] = OBJECT_STOVE

    spawning = _blank()
    spawning[(HOUSE_RECT[3] - 1) * WIDTH + (HOUSE_RECT[0] + HOUSE_RECT[2]) // 2] = SPAWN_DOORSTEP

    for filename, grid in (
        ("collision_maze.csv", collision),
        ("sector_maze.csv", sector),
        ("arena_maze.csv", arena),
        ("game_object_maze.csv", game_object),
        ("spawning_location_maze.csv", spawning),
    ):
        # One row of width*height comma-separated ints, which is what
        # `read_maze_csv` expects - the upstream format, not a tidier one.
        (matrix / "maze" / filename).write_text(",".join(str(v) for v in grid), encoding="utf-8")

    blocks = matrix / "special_blocks"
    _write_rows(blocks / "world_blocks.csv", [[1, WORLD_NAME]])
    _write_rows(blocks / "sector_blocks.csv", [
        [SECTOR_HOUSE, WORLD_NAME, "Fixture House"],
        [SECTOR_SHOP, WORLD_NAME, "Fixture Shop"],
    ])
    _write_rows(blocks / "arena_blocks.csv", [
        [ARENA_KITCHEN, WORLD_NAME, "Fixture House", "kitchen"],
        [ARENA_COUNTER, WORLD_NAME, "Fixture Shop", "counter"],
    ])
    _write_rows(blocks / "game_object_blocks.csv", [
        # Column 2 is "<all>" in the upstream data for every row; kept so
        # the fixture is read by the same code path, not a lenient one.
        [OBJECT_STOVE, WORLD_NAME, "<all>", "stove"],
    ])
    _write_rows(blocks / "spawning_location_blocks.csv", [
        [SPAWN_DOORSTEP, WORLD_NAME, "Fixture House", "kitchen", "doorstep"],
    ])

    (ville / "visuals" / "the_ville_jan7.json").write_text(
        json.dumps(_tiled(collision, sector, arena, game_object, spawning)),
        encoding="utf-8",
    )
    return ville


def _write_rows(path: Path, rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)


def _tiled(collision, sector, arena, game_object, spawning) -> dict:
    """The Tiled JSON view of the same data.

    `expansion.py` grows the map on the JSON side and the CSVs are the
    other view of it, so a fixture that had only one of them would test
    half the chain - which is the failure mode this whole module is
    about.
    """
    def layer(name: str, data: list[int]) -> dict:
        return {
            "name": name, "type": "tilelayer",
            "width": WIDTH, "height": HEIGHT, "data": list(data),
        }

    return {
        "width": WIDTH,
        "height": HEIGHT,
        "tilewidth": TILE_SIZE,
        "tileheight": TILE_SIZE,
        "layers": [
            layer("Bottom Ground", [2] * (WIDTH * HEIGHT)),
            layer("Collisions", collision),
            layer("Sector Blocks", sector),
            layer("Arena Blocks", arena),
            layer("Object Interaction Blocks", game_object),
            layer("Spawning Blocks", spawning),
        ],
    }
