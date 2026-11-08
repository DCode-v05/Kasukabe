"""Loads a WorldMap from the Ville matrix/ CSV export.

The task's data source of record: matrix/maze/*.csv (the five flat grids)
plus matrix/special_blocks/*.csv (the name lookups, blocks.py) plus
matrix/maze_meta_info.json (world_name, width, height, tile_size). This is
the exact directory layout under smallville/environment/frontend_server/
static_dirs/assets/the_ville/.

tiled_json.py loads the same information from the sibling
visuals/*.json Tiled file instead (verified byte-identical for the layers
that matter - see that module's docstring); both funnel into
model.build_world_map so there is exactly one place that turns raw grids
into a WorldMap.

Reads local files synchronously, matching engine/src/config.py's
Settings.load() precedent for "parse a static file at boot" - this is
tile geometry setup, not the live I/O (HTTP/Bedrock/Supabase) app/
CONVENTIONS.md requires async for, and it runs once per process, not
per-tick or per-request.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from world.map.blocks import SpecialBlockTables, load_special_blocks
from world.map.model import WorldMap, build_world_map

MAZE_FILES = {
    "collision": "collision_maze.csv",
    "sector": "sector_maze.csv",
    "arena": "arena_maze.csv",
    "game_object": "game_object_maze.csv",
    "spawning": "spawning_location_maze.csv",
}


def read_maze_csv(path: Path) -> list[int]:
    """A maze CSV is a single row of width*height comma-separated ints
    (see MAZE_FILES docstring in this module and this task's own
    investigation of the source data: no newlines, one row, 14000 cells
    for the 140x100 base Ville map). csv.reader handles the row whether
    or not a trailing newline is present."""
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if len(rows) != 1:
        raise ValueError(f"{path}: expected exactly one row of flattened cells, got {len(rows)}")
    return [int(cell.strip()) for cell in rows[0] if cell.strip() != ""]


def read_maze_meta(ville_dir: Path) -> tuple[str, int, int, int]:
    meta_path = ville_dir / "matrix" / "maze_meta_info.json"
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    return (
        data["world_name"],
        int(data["maze_width"]),
        int(data["maze_height"]),
        int(data["sq_tile_size"]),
    )


def sector_names_table(blocks: SpecialBlockTables) -> dict[int, tuple[str, str]]:
    return dict(blocks.sectors)


def arena_names_table(blocks: SpecialBlockTables) -> dict[int, tuple[str, str, str]]:
    return dict(blocks.arenas)


def game_object_names_table(blocks: SpecialBlockTables) -> dict[int, tuple[str, str]]:
    return dict(blocks.game_objects)


def spawning_names_table(blocks: SpecialBlockTables) -> dict[int, tuple[str, str, str, str]]:
    return dict(blocks.spawning_locations)


def load_ville_map(ville_dir: Path) -> WorldMap:
    """Builds a WorldMap from matrix/maze/*.csv + matrix/special_blocks/*.csv
    + matrix/maze_meta_info.json under ville_dir.

    This function is deliberately generic: it knows nothing about
    Kasukabe's genesis rules (which sectors are "active", the pub being
    closed, minted resource zones). It loads exactly what the base Ville
    map contains - genesis.build_kasukabe_genesis_map is the Kasukabe-
    specific entry point that calls this and then layers genesis config
    and land expansion on top.
    """
    world_name, width, height, tile_size = read_maze_meta(ville_dir)
    maze_dir = ville_dir / "matrix" / "maze"
    blocks = load_special_blocks(ville_dir)

    flats = {
        key: read_maze_csv(maze_dir / filename) for key, filename in MAZE_FILES.items()
    }

    return build_world_map(
        world_name=world_name,
        width=width,
        height=height,
        tile_size=tile_size,
        collision_flat=flats["collision"],
        sector_flat=flats["sector"],
        arena_flat=flats["arena"],
        game_object_flat=flats["game_object"],
        spawning_flat=flats["spawning"],
        sector_names=sector_names_table(blocks),
        arena_names=arena_names_table(blocks),
        game_object_names=game_object_names_table(blocks),
        spawning_names=spawning_names_table(blocks),
    )
