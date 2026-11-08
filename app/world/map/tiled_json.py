"""Reads, grows, and re-derives collision from the Ville's Tiled-style JSON.

DESIGN.md section 1: land expansion "appends tiles to the Tiled JSON and
regenerates collision." This module is the mechanics of both halves.

Why the Tiled JSON at all, when loader.py already reads the matrix/ CSVs?
Because the CSVs are themselves an export of specific named layers inside
visuals/the_ville_jan7.json - verified directly against the source data:
every non-zero cell of the Collisions, Sector Blocks, Object Interaction
Blocks, and Spawning Blocks layers matches its corresponding maze CSV
value exactly, cell for cell. (Arena Blocks and Collisions have a small
number - 40 and 61 out of 14000 - of stale mismatches between this
particular jan7.json snapshot and the shipped CSVs, most likely from a
later hand-edit of one without the other upstream; irrelevant to this
module, which always derives collision FROM whichever Tiled JSON it is
given, never from a CSV.) So "append tiles to the Tiled JSON" is not a
separate representation to keep in sync by hand - grow every layer's flat
`data` array together, then regenerate_collision_layer() re-extracts
exactly the same Collisions layer loader.py's collision_maze.csv would
have come from, and build_world_map_from_tiled folds every layer into a
fresh WorldMap the same way loader.py's CSV path does.

Growth is append-only at the south and east edges (see Edge below) -
appending north or west would shift every existing tile's (x, y), which
would silently invalidate every agent's remembered location, every
in-flight path, and every plot's footprint the moment land grows. That
failure mode is worse than the limitation of only two growable edges, so
this module does not offer north/west growth at all.
"""

from __future__ import annotations

import copy
import enum
import json
from pathlib import Path
from typing import Any

from world.map.blocks import SpecialBlockTables
from world.map.model import WorldMap, build_world_map

COLLISION_LAYER = "Collisions"
SECTOR_LAYER = "Sector Blocks"
ARENA_LAYER = "Arena Blocks"
GAME_OBJECT_LAYER = "Object Interaction Blocks"
SPAWNING_LAYER = "Spawning Blocks"

# Every visual/decoration layer present in the base Ville file. Not
# load-bearing for WorldMap construction (build_world_map only reads the
# five layers above), but append_land still has to grow these in lockstep
# so the Tiled JSON stays structurally valid (every layer's data array
# must equal width*height, or a Tiled-compatible renderer - game/,
# eventually - rejects the file).
VISUAL_LAYERS = (
    "Bottom Ground",
    "Exterior Ground",
    "Exterior Decoration L1",
    "Exterior Decoration L2",
    "Interior Ground",
    "Wall",
    "Interior Furniture L1",
    "Interior Furniture L2 ",  # trailing space is in the source file's own layer name
    "Foreground L1",
    "Foreground L2",
    "World Blocks",
    "Special Blocks Registry",
)

# gid 2 is the base grass tile: it covers 13979 of the 14000 cells in the
# base map's "Bottom Ground" layer (measured directly against
# the_ville_jan7.json), i.e. it is the default ground everywhere nothing
# more specific was hand-painted. Newly grown land gets this as its base
# rather than gid 0 (blank/no tile) so it renders as ordinary ground
# instead of a void, without this module needing to know anything about
# the tileset's actual art beyond this one already-dominant choice.
DEFAULT_GROUND_GID = 2

# Comfortably above every real gid in the base map (the highest tileset,
# blocks_3, starts at firstgid 32285 and its tile count keeps the whole
# file well under 40000) - synthetic ids minted for Kasukabe-only zones
# start here so they can never collide with a real Ville tile graphic.
SYNTHETIC_ID_FLOOR = 900_000


class Edge(str, enum.Enum):
    """The only two edges append_land will grow from - see module docstring."""

    SOUTH = "south"
    EAST = "east"


class TiledJsonError(ValueError):
    """Raised for a Tiled JSON that is missing a layer, dimension, or
    field this module needs. Aggregates every problem found so a
    malformed file is diagnosed in one pass, matching engine/src/
    config.py's ConfigError convention."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(f"invalid Tiled map ({len(self.errors)} error(s)):\n  - {joined}")


REQUIRED_LAYERS = (
    COLLISION_LAYER,
    SECTOR_LAYER,
    ARENA_LAYER,
    GAME_OBJECT_LAYER,
    SPAWNING_LAYER,
)


def load_tiled_map(path: Path) -> dict[str, Any]:
    """Parses a Tiled JSON file. Synchronous local-file read, same
    reasoning as loader.py's module docstring."""
    return json.loads(path.read_text(encoding="utf-8"))


def save_tiled_map(tiled: dict[str, Any], path: Path) -> None:
    """Write a Tiled map to disk.

    GROW-22: NO PRODUCTION CALLER, deliberately, and kept.

    The grown map already survives a restart without this: GROW-07
    checkpoints `MapService.snapshot()`, which carries tiled + blocks +
    plots, so persistence is covered.

    What this is still for is GROW-14 - "the viewer never sees a grown map".
    `game/world_client.py` loads a static genesis JSON off disk and serves
    it as `/api/map`, so exporting the LIVE map to a file the viewer can
    read is one of the two shapes that fix could take (the other being
    `/map/tiled` proxied through, per UI-01). Whichever is chosen, this is
    the writer, so it is declared dead rather than removed and re-derived.

    Verified by AST on 2026-09-05: zero non-test call sites.
    """
    path.write_text(json.dumps(tiled, indent=1), encoding="utf-8")


def validate_tiled_map(tiled: dict[str, Any]) -> None:
    errors: list[str] = []
    for field_name in ("width", "height", "tilewidth", "layers"):
        if field_name not in tiled:
            errors.append(f"missing top-level field {field_name!r}")
    layer_names = {layer.get("name") for layer in tiled.get("layers", [])}
    for required in REQUIRED_LAYERS:
        if required not in layer_names:
            errors.append(f"missing required layer {required!r}")
    if errors:
        raise TiledJsonError(errors)


def get_layer(tiled: dict[str, Any], name: str) -> list[int]:
    for layer in tiled["layers"]:
        if layer.get("name") == name:
            return layer["data"]
    raise TiledJsonError([f"no layer named {name!r}"])


def set_layer(tiled: dict[str, Any], name: str, data: list[int]) -> None:
    for layer in tiled["layers"]:
        if layer.get("name") == name:
            layer["data"] = data
            layer["width"] = tiled["width"]
            layer["height"] = tiled["height"]
            return
    raise TiledJsonError([f"no layer named {name!r}"])


def layer_names(tiled: dict[str, Any]) -> list[str]:
    return [layer["name"] for layer in tiled["layers"] if layer.get("type") == "tilelayer"]


def append_rows(
    tiled: dict[str, Any],
    count: int,
    *,
    fill_by_layer: dict[str, int] | None = None,
    default_fill: int = 0,
) -> None:
    """Grows the map by `count` new rows on the SOUTH edge (appended
    after the last existing row) across every tile layer. Mutates
    `tiled` in place. width is unchanged; height grows by count.
    """
    if count <= 0:
        raise ValueError(f"count must be positive, got {count}")
    width = tiled["width"]
    fill_by_layer = fill_by_layer or {}
    for name in layer_names(tiled):
        fill = fill_by_layer.get(name, default_fill)
        data = get_layer(tiled, name)
        new_rows = [fill] * (width * count)
        set_layer(tiled, name, data + new_rows)
    tiled["height"] = tiled["height"] + count


def append_columns(
    tiled: dict[str, Any],
    count: int,
    *,
    fill_by_layer: dict[str, int] | None = None,
    default_fill: int = 0,
) -> None:
    """Grows the map by `count` new columns on the EAST edge (appended
    after the last existing column of every row) across every tile
    layer. Mutates `tiled` in place. height is unchanged; width grows by
    count.
    """
    if count <= 0:
        raise ValueError(f"count must be positive, got {count}")
    width, height = tiled["width"], tiled["height"]
    new_width = width + count
    fill_by_layer = fill_by_layer or {}
    for name in layer_names(tiled):
        fill = fill_by_layer.get(name, default_fill)
        data = get_layer(tiled, name)
        grown: list[int] = []
        for y in range(height):
            row = data[y * width : (y + 1) * width]
            grown.extend(row)
            grown.extend([fill] * count)
        set_layer(tiled, name, grown)
    tiled["width"] = new_width


def stamp_rect(
    tiled: dict[str, Any],
    layer_name: str,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    value: int,
) -> None:
    """Sets every cell in the half-open rectangle [x0, x1) x [y0, y1) of
    one layer to `value`. Used to paint a newly grown strip of land with
    a sector/arena id (SECTOR_LAYER/ARENA_LAYER) or to mark specific
    cells blocked (COLLISION_LAYER, e.g. an ore outcrop's rock cluster).
    """
    width, height = tiled["width"], tiled["height"]
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError(
            f"rect ({x0},{y0})-({x1},{y1}) is out of bounds for a {width}x{height} map"
        )
    data = get_layer(tiled, layer_name)
    for y in range(y0, y1):
        row_start = y * width
        for x in range(x0, x1):
            data[row_start + x] = value
    set_layer(tiled, layer_name, data)


def regenerate_collision_layer(tiled: dict[str, Any]) -> list[list[bool]]:
    """Re-derives the [y][x] walkability grid from the Tiled JSON's
    current COLLISION_LAYER. This is literally what "regenerate collision"
    means in this module: after append_rows/append_columns/stamp_rect
    mutate the JSON, this reads the truth back out of it rather than
    maintaining a second, hand-tracked copy of what is blocked.
    """
    width, height = tiled["width"], tiled["height"]
    data = get_layer(tiled, COLLISION_LAYER)
    if len(data) != width * height:
        raise TiledJsonError(
            [f"{COLLISION_LAYER} has {len(data)} cells, expected {width * height}"]
        )
    return [[v != 0 for v in data[y * width : (y + 1) * width]] for y in range(height)]


def build_world_map_from_tiled(tiled: dict[str, Any], blocks: SpecialBlockTables) -> WorldMap:
    """model.build_world_map, sourced from a Tiled JSON's layers instead
    of loader.py's CSV files. Requires the same SpecialBlockTables shape
    either way (id -> name is never encoded in the Tiled layers
    themselves, only the bare id) - see blocks.py.
    """
    validate_tiled_map(tiled)
    width, height = tiled["width"], tiled["height"]
    tile_size = tiled.get("tilewidth", 32)

    return build_world_map(
        world_name=blocks.world[1],
        width=width,
        height=height,
        tile_size=tile_size,
        collision_flat=get_layer(tiled, COLLISION_LAYER),
        sector_flat=get_layer(tiled, SECTOR_LAYER),
        arena_flat=get_layer(tiled, ARENA_LAYER),
        game_object_flat=get_layer(tiled, GAME_OBJECT_LAYER),
        spawning_flat=get_layer(tiled, SPAWNING_LAYER),
        sector_names=dict(blocks.sectors),
        arena_names=dict(blocks.arenas),
        game_object_names=dict(blocks.game_objects),
        spawning_names=dict(blocks.spawning_locations),
    )


def clone_tiled_map(tiled: dict[str, Any]) -> dict[str, Any]:
    """Deep copy, so a caller can mutate the result of append_rows/
    append_columns/stamp_rect without corrupting a caller-held reference
    to the pre-expansion map (expansion.py always works on a clone, never
    the original, so a rejected/failed expansion leaves nothing changed).
    """
    return copy.deepcopy(tiled)


class IdAllocator:
    """Mints sequential synthetic ids starting at `floor`, for new
    sectors/arenas/game objects that expansion.py creates rather than
    reads from the source data. One allocator per id namespace (sectors,
    arenas, ...) - expansion.py keeps them separate so a minted sector id
    and a minted arena id are never accidentally equal, even though
    nothing here actually requires that (they index different dicts).
    """

    def __init__(self, floor: int = SYNTHETIC_ID_FLOOR) -> None:
        self.next_value = floor

    def next(self) -> int:
        value = self.next_value
        self.next_value += 1
        return value
