"""Shared fixtures for world/map's test suite.

Two map fixtures at two different scales:

- `tiny_world` / `tiny_blocks` / `tiny_tiled`: a hand-built 5x5 map (one
  small house with a door, an exterior street) used for fast, exact,
  isolated unit tests of walkability/pathfinding/parcel logic where the
  expected answer needs to be knowable by inspection.
- `ville_dir`: the real Ville asset directory this task points at
  (smallville/environment/frontend_server/static_dirs/assets/the_ville),
  used by loader/genesis/expansion/service's integration tests to prove
  this package actually parses the real data DESIGN.md section 1
  describes, not just a synthetic fixture shaped to make the code pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.map.blocks import SpecialBlockTables
from world.map.model import WorldMap, build_world_map

# app/world/map/tests/conftest.py -> parents[4] is the kasukabe/ repo root.
VILLE_DIR = (
    Path(__file__).resolve().parents[4]
    / "smallville"
    / "environment"
    / "frontend_server"
    / "static_dirs"
    / "assets"
    / "the_ville"
)

TINY_WIDTH = 5
TINY_HEIGHT = 5

# Row-major, y0 (north) first. # = wall/collision, . = open ground.
#   y0: # # # # #
#   y1: # . . . #   ("Test House" interior, bed at (1,1))
#   y2: # . . . #   (spawn point at (2,2))
#   y3: # # . # #   (door at (2,3))
#   y4: . . . . .   (exterior street)
TINY_COLLISION = [
    1, 1, 1, 1, 1,
    1, 0, 0, 0, 1,
    1, 0, 0, 0, 1,
    1, 1, 0, 1, 1,
    0, 0, 0, 0, 0,
]
TINY_SECTOR = [
    0, 0, 0, 0, 0,
    0, 1, 1, 1, 0,
    0, 1, 1, 1, 0,
    0, 0, 1, 0, 0,
    0, 0, 0, 0, 0,
]
TINY_ARENA = [
    0, 0, 0, 0, 0,
    0, 101, 101, 101, 0,
    0, 101, 101, 101, 0,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0,
]
TINY_GAME_OBJECT = [
    0, 0, 0, 0, 0,
    0, 201, 0, 0, 0,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0,
]
TINY_SPAWNING = [
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0,
    0, 0, 301, 0, 0,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0,
]


@pytest.fixture
def tiny_blocks() -> SpecialBlockTables:
    blocks = SpecialBlockTables(world=(1, "Test Town"))
    blocks.add_sector(1, "Test Town", "Test House")
    blocks.add_arena(101, "Test Town", "Test House", "Main Room")
    blocks.add_game_object(201, "Test Town", "bed")
    blocks.add_spawning_location(301, "Test Town", "Test House", "Main Room", "sp-A")
    return blocks


@pytest.fixture
def tiny_world(tiny_blocks: SpecialBlockTables) -> WorldMap:
    return build_world_map(
        world_name="Test Town",
        width=TINY_WIDTH,
        height=TINY_HEIGHT,
        tile_size=32,
        collision_flat=TINY_COLLISION,
        sector_flat=TINY_SECTOR,
        arena_flat=TINY_ARENA,
        game_object_flat=TINY_GAME_OBJECT,
        spawning_flat=TINY_SPAWNING,
        sector_names=dict(tiny_blocks.sectors),
        arena_names=dict(tiny_blocks.arenas),
        game_object_names=dict(tiny_blocks.game_objects),
        spawning_names=dict(tiny_blocks.spawning_locations),
    )


@pytest.fixture
def tiny_tiled() -> dict:
    """A minimal Tiled-style JSON covering just the layers this package's
    tiled_json/expansion code actually reads or grows: the five required
    logical layers plus one visual layer (Bottom Ground) to exercise
    per-layer fill overrides during growth."""
    return {
        "width": TINY_WIDTH,
        "height": TINY_HEIGHT,
        "tilewidth": 32,
        "tileheight": 32,
        "layers": [
            {"name": "Bottom Ground", "type": "tilelayer", "width": TINY_WIDTH, "height": TINY_HEIGHT, "data": [2] * (TINY_WIDTH * TINY_HEIGHT)},
            {"name": "Collisions", "type": "tilelayer", "width": TINY_WIDTH, "height": TINY_HEIGHT, "data": list(TINY_COLLISION)},
            {"name": "Sector Blocks", "type": "tilelayer", "width": TINY_WIDTH, "height": TINY_HEIGHT, "data": list(TINY_SECTOR)},
            {"name": "Arena Blocks", "type": "tilelayer", "width": TINY_WIDTH, "height": TINY_HEIGHT, "data": list(TINY_ARENA)},
            {"name": "Object Interaction Blocks", "type": "tilelayer", "width": TINY_WIDTH, "height": TINY_HEIGHT, "data": list(TINY_GAME_OBJECT)},
            {"name": "Spawning Blocks", "type": "tilelayer", "width": TINY_WIDTH, "height": TINY_HEIGHT, "data": list(TINY_SPAWNING)},
        ],
    }


@pytest.fixture(scope="session")
def ville_dir() -> Path:
    if not VILLE_DIR.exists():
        pytest.skip(f"Ville asset directory not found at {VILLE_DIR}")
    return VILLE_DIR
