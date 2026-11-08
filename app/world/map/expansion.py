"""Land growth: DESIGN.md section 1's "government petitions the President
... on approval the app appends tiles to the Tiled JSON, regenerates
collision, and the town physically grows" and section 8's President
console action "land petitions (approve -> map grows)".

Two entry points:

- approve_land_petition: the general, ongoing-game primitive. Grows the
  map by `depth` rows/columns on one of the two safe edges (Edge, see
  tiled_json.py's module docstring for why only south/east are offered)
  and optionally carves exactly one new Sector into the freshly grown
  strip. This is what a future government/petitions module calls once a
  petition is approved - it has no idea what zone the government asked
  for beyond a name and how many rooms/arenas it should have.
- append_kasukabe_genesis_zones: the one-time, Kasukabe-specific genesis
  operation (DESIGN.md section 1's "plus map-edge resource zones"). Grows
  the map once and carves five zones side by side in the same strip
  (government complex, temple, farmland x10 crops, ore outcrop x5
  metals, forest) - genesis.py's build_kasukabe_genesis_map calls this
  directly rather than five separate petitions, since genesis land is
  presidentially pre-approved by construction (DESIGN.md doesn't route
  the town's own starting territory through its not-yet-elected
  government).

Both are built on the same sync primitives (tiled_json.append_rows/
append_columns/stamp_rect/regenerate_collision_layer + this module's
carve_sector) so there is exactly one implementation of "grow, stamp,
regenerate, rebuild" - genesis is not a special code path, just a
special caller.
"""

from __future__ import annotations

from collections.abc import Iterable

import copy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from world.map.blocks import SpecialBlockTables
from world.map.model import CROP_VARIETIES, ORE_METALS, WorldMap
from world.map.tiled_json import (
    COLLISION_LAYER,
    DEFAULT_GROUND_GID,
    SYNTHETIC_ID_FLOOR,
    ARENA_LAYER,
    SECTOR_LAYER,
    Edge,
    IdAllocator,
    append_columns,
    append_rows,
    build_world_map_from_tiled,
    clone_tiled_map,
    get_layer,
    regenerate_collision_layer,
    set_layer,
    stamp_rect,
)

Rect = tuple[int, int, int, int]

GOVERNMENT_SECTOR_NAME = "Kasukabe Government Complex"
TEMPLE_SECTOR_NAME = "Kasukabe Temple"
FARMLAND_SECTOR_NAME = "Kasukabe Farmland"
ORE_OUTCROP_SECTOR_NAME = "Kasukabe Ore Outcrop"
FOREST_SECTOR_NAME = "Kasukabe Forest"

DEFAULT_GENESIS_STRIP_DEPTH = 30

ARENA_ID_RANGE_OFFSET = 100_000


class ExpansionError(ValueError):
    pass


@dataclass
class GenesisZoneIds:
    """The five sector ids append_kasukabe_genesis_zones minted, so a
    caller (genesis.py) can immediately look them up in the rebuilt
    WorldMap without re-deriving them from sector names."""

    government_sector_id: int
    temple_sector_id: int
    farmland_sector_id: int
    ore_outcrop_sector_id: int
    forest_sector_id: int


class LandExpansion(BaseModel):
    """The result of one land-growth operation - shaped to become a
    `world.land.expanded` event payload (app/CONVENTIONS.md's event
    table names this event explicitly) once a caller wires this module
    to world/core's EventBus. A pydantic model (not a dataclass) because,
    unlike WorldMap itself, this is exactly the kind of point-in-time
    record that crosses a module boundary - see ledger/models.py's
    Transaction for the same reasoning applied to money movements.
    """

    edge: Edge
    depth: int
    previous_width: int
    previous_height: int
    new_width: int
    new_height: int
    new_sector_ids: list[int] = []
    new_sector_names: list[str] = []
    purpose: str | None = None
    corridors_opened: int = 0
    strip_x0: int = 0
    strip_y0: int = 0
    strip_x1: int = 0
    strip_y1: int = 0

    @property
    def strip_rect(self) -> Rect:
        """The tiles this expansion added, half-open, in the new map."""
        return (self.strip_x0, self.strip_y0, self.strip_x1, self.strip_y1)


def split_span(total: int, parts: int) -> list[tuple[int, int]]:
    """Divides `total` units into `parts` contiguous, non-empty integer
    spans as evenly as possible: the first (total % parts) spans get one
    extra unit. Used both to lay the genesis zones side by side across
    the strip's width and to lay each zone's arenas out along its depth
    (crop/metal bands)."""
    if parts <= 0:
        raise ValueError(f"parts must be positive, got {parts}")
    if total < parts:
        raise ValueError(f"cannot split {total} units into {parts} non-empty parts")
    base, remainder = divmod(total, parts)
    spans = []
    start = 0
    for i in range(parts):
        size = base + (1 if i < remainder else 0)
        spans.append((start, start + size))
        start += size
    return spans


def building_sector_name(purpose: str, owner: str) -> str:
    """The name a stamped building's sector takes.

    UI-33. Here rather than inline in `stamp_building` because
    `world.building.completed` has to report the same name for the same
    building, and a second copy of an f-string is a second copy that
    drifts the first time either is edited.
    """
    return f"{purpose} ({owner})"


def footprint_rect(footprint: "Iterable[TileCoord] | Iterable[tuple[int, int]]") -> dict[str, int]:
    """The bounding box of a set of tiles, as {x, y, w, h}.

    UI-33. A building's footprint is a set of tiles and the wire needs a
    rectangle - `world.building.mapped` carried `len(footprint)`, a count,
    which tells a renderer how big the building is and nothing about where
    it stands.

    A bounding box rather than the tile set: every building this project
    creates is a rectangle (`plots.py` allocates rectangular plots), the
    Phaser page draws building markers from a rect, and a 40-tile list on
    every event costs more than the one fact anybody reads off it. A
    non-rectangular footprint therefore reports the box that CONTAINS it,
    which is the honest degradation - too big, never wrong about position.
    """
    xs, ys = [], []
    for tile in footprint:
        x, y = (tile.x, tile.y) if hasattr(tile, "x") else (tile[0], tile[1])
        xs.append(x)
        ys.append(y)
    if not xs:
        raise ValueError("cannot take the rect of an empty footprint")
    return {
        "x": min(xs), "y": min(ys),
        "w": max(xs) - min(xs) + 1, "h": max(ys) - min(ys) + 1,
    }


def next_available_id(existing_ids: dict[int, Any], floor: int = SYNTHETIC_ID_FLOOR) -> int:
    """Where the next synthetic id should start, given ids already minted
    into `existing_ids` (a SpecialBlockTables.sectors/.arenas dict).
    Always at least `floor`, and past the highest id already used, so
    repeated expansions (genesis, then any number of later petitions)
    never collide with each other regardless of call order."""
    if not existing_ids:
        return floor
    return max(floor, max(existing_ids) + 1)


def carve_sector(
    tiled: dict[str, Any],
    blocks: SpecialBlockTables,
    sector_ids: IdAllocator,
    arena_ids: IdAllocator,
    *,
    world_name: str,
    sector_name: str,
    rect: Rect,
    arenas: list[tuple[str, Rect]],
) -> int:
    """Stamps one new sector (and its arenas) into already-grown land.

    Mutates `tiled` (paints SECTOR_LAYER/ARENA_LAYER) and `blocks`
    (registers the new ids' names) in place. Does not touch
    COLLISION_LAYER - a resource/government/temple zone is ordinary
    walkable ground by default; a caller that wants part of a carved
    zone blocked (e.g. a boulder in an ore outcrop) uses
    tiled_json.stamp_rect on COLLISION_LAYER separately after this call.
    Returns the minted sector id.
    """
    x0, y0, x1, y1 = rect
    sector_id = sector_ids.next()
    stamp_rect(tiled, SECTOR_LAYER, x0, y0, x1, y1, sector_id)
    blocks.add_sector(sector_id, world_name, sector_name)
    for arena_name, arena_rect in arenas:
        ax0, ay0, ax1, ay1 = arena_rect
        arena_id = arena_ids.next()
        stamp_rect(tiled, ARENA_LAYER, ax0, ay0, ax1, ay1, arena_id)
        blocks.add_arena(arena_id, world_name, sector_name, arena_name)
    return sector_id


def single_arena_rect_bands(rect: Rect, names: tuple[str, ...]) -> list[tuple[str, Rect]]:
    """Splits `rect` into len(names) equal-depth horizontal bands (rows),
    one per name, in the order given. Used for farmland's 10 crop
    varieties and ore outcrop's 5 metals: each variety gets its own strip
    within the zone rather than sharing one arena, since DESIGN.md counts
    them as separate ("10 crop varieties", "5 metals")."""
    x0, y0, x1, y1 = rect
    depth = y1 - y0
    bands = split_span(depth, len(names))
    return [(name, (x0, y0 + by0, x1, y0 + by1)) for name, (by0, by1) in zip(names, bands)]


def append_kasukabe_genesis_zones(
    tiled: dict[str, Any],
    blocks: SpecialBlockTables,
    *,
    depth: int = DEFAULT_GENESIS_STRIP_DEPTH,
) -> GenesisZoneIds:
    """DESIGN.md section 1: genesis buildings include a government
    complex and a temple, neither of which exists on the base Ville map,
    plus map-edge resource zones (farmland, ore outcrop, forest). Grows
    the map once on the south edge and lays five zones side by side
    across the new strip's width, left to right: government, temple,
    farmland, ore outcrop, forest.

    Mutates `tiled` and `blocks` in place (same contract as carve_sector)
    and returns the five minted sector ids. Callers rebuild a WorldMap
    from the mutated `tiled`/`blocks` afterward (genesis.py does this) -
    this function does not construct a WorldMap itself, matching every
    other function in this module.
    """
    world_name = blocks.world[1]
    sector_ids = IdAllocator(next_available_id(blocks.sectors))
    arena_ids = IdAllocator(next_available_id(blocks.arenas, floor=SYNTHETIC_ID_FLOOR + ARENA_ID_RANGE_OFFSET))

    old_height = tiled["height"]
    width = tiled["width"]
    append_rows(tiled, depth, fill_by_layer={"Bottom Ground": DEFAULT_GROUND_GID})
    y0, y1 = old_height, old_height + depth

    zone_spans = split_span(width, 5)
    gov_span, temple_span, farm_span, ore_span, forest_span = zone_spans

    def zone_rect(span: tuple[int, int]) -> Rect:
        return (span[0], y0, span[1], y1)

    government_id = carve_sector(
        tiled,
        blocks,
        sector_ids,
        arena_ids,
        world_name=world_name,
        sector_name=GOVERNMENT_SECTOR_NAME,
        rect=zone_rect(gov_span),
        arenas=single_arena_rect_bands(zone_rect(gov_span), ("Town Hall", "Treasury Vault")),
    )
    temple_id = carve_sector(
        tiled,
        blocks,
        sector_ids,
        arena_ids,
        world_name=world_name,
        sector_name=TEMPLE_SECTOR_NAME,
        rect=zone_rect(temple_span),
        arenas=[("Sanctuary", zone_rect(temple_span))],
    )
    crop_names = tuple(f"{crop.capitalize()} Field" for crop in CROP_VARIETIES)
    farmland_id = carve_sector(
        tiled,
        blocks,
        sector_ids,
        arena_ids,
        world_name=world_name,
        sector_name=FARMLAND_SECTOR_NAME,
        rect=zone_rect(farm_span),
        arenas=single_arena_rect_bands(zone_rect(farm_span), crop_names),
    )
    metal_names = tuple(f"{metal.capitalize()} Outcrop" for metal in ORE_METALS)
    ore_id = carve_sector(
        tiled,
        blocks,
        sector_ids,
        arena_ids,
        world_name=world_name,
        sector_name=ORE_OUTCROP_SECTOR_NAME,
        rect=zone_rect(ore_span),
        arenas=single_arena_rect_bands(zone_rect(ore_span), metal_names),
    )
    forest_id = carve_sector(
        tiled,
        blocks,
        sector_ids,
        arena_ids,
        world_name=world_name,
        sector_name=FOREST_SECTOR_NAME,
        rect=zone_rect(forest_span),
        arenas=[("Timber Stand", zone_rect(forest_span))],
    )

    return GenesisZoneIds(
        government_sector_id=government_id,
        temple_sector_id=temple_id,
        farmland_sector_id=farmland_id,
        ore_outcrop_sector_id=ore_id,
        forest_sector_id=forest_id,
    )


async def approve_land_petition(
    tiled: dict[str, Any],
    blocks: SpecialBlockTables,
    *,
    edge: Edge,
    depth: int,
    new_sector_name: str | None = None,
    arena_names: list[str] | None = None,
) -> tuple[dict[str, Any], SpecialBlockTables, WorldMap, LandExpansion]:
    """The President approves a land petition (DESIGN.md section 8):
    grows the map by `depth` tiles on `edge` and, if `new_sector_name` is
    given, carves the entire newly grown strip into one sector (split
    into len(arena_names) equal bands if arena_names is given, else one
    arena spanning the whole strip named after the sector).

    Transactional: works on a deep copy of `tiled` and `blocks`, never
    the originals (tiled_json.clone_tiled_map / copy.deepcopy), and
    returns the grown copies alongside the rebuilt WorldMap. A caller
    only adopts the new state (MapService.expand_land reassigns
    self.tiled/self.blocks/self.world to the return values) after this
    coroutine returns successfully - a petition that raises partway
    through (a bad rect, an id collision) leaves the caller's original
    tiled/blocks untouched instead of half-grown.

    Async: this is DESIGN.md section 2's "world state mutates ONLY
    through tool calls" boundary - a land petition is a government tool
    call whose effect (the town physically grows) is exactly the kind of
    world-state mutation app/CONVENTIONS.md requires async for, even
    though every individual step (append_rows, carve_sector,
    regenerate_collision_layer, rebuild) is itself synchronous, pure
    computation. Today the only I/O this performs is none - the caller
    owns loading/saving the Tiled JSON file (tiled_json.load_tiled_map /
    save_tiled_map) around this call; this function only mutates its own
    working copy of the in-memory dict it is handed. That split keeps
    this function testable without a filesystem and gives whoever wires
    in Supabase persistence (world's petitions table, app/CONVENTIONS.md)
    a single call site to add a write to, without changing this
    function's signature.
    """
    if depth <= 0:
        raise ExpansionError(f"depth must be positive, got {depth}")

    working_tiled = clone_tiled_map(tiled)
    working_blocks = copy.deepcopy(blocks)
    previous_width, previous_height = working_tiled["width"], working_tiled["height"]

    if edge is Edge.SOUTH:
        append_rows(working_tiled, depth, fill_by_layer={"Bottom Ground": DEFAULT_GROUND_GID})
        strip_rect: Rect = (0, previous_height, working_tiled["width"], working_tiled["height"])
    elif edge is Edge.EAST:
        append_columns(working_tiled, depth, fill_by_layer={"Bottom Ground": DEFAULT_GROUND_GID})
        strip_rect = (previous_width, 0, working_tiled["width"], working_tiled["height"])
    else:  # pragma: no cover - Edge is a closed str enum, unreachable via type checking
        raise ExpansionError(f"unsupported edge {edge!r}, expected 'south' or 'east'")

    new_sector_ids: list[int] = []
    new_sector_names: list[str] = []
    if new_sector_name:
        world_name = working_blocks.world[1]
        sector_ids = IdAllocator(next_available_id(working_blocks.sectors))
        arena_ids = IdAllocator(
            next_available_id(working_blocks.arenas, floor=SYNTHETIC_ID_FLOOR + ARENA_ID_RANGE_OFFSET)
        )
        arenas = (
            single_arena_rect_bands(strip_rect, tuple(arena_names))
            if arena_names
            else [(new_sector_name, strip_rect)]
        )
        sector_id = carve_sector(
            working_tiled,
            working_blocks,
            sector_ids,
            arena_ids,
            world_name=world_name,
            sector_name=new_sector_name,
            rect=strip_rect,
            arenas=arenas,
        )
        new_sector_ids.append(sector_id)
        new_sector_names.append(new_sector_name)

    regenerate_collision_layer(working_tiled)
    world = build_world_map_from_tiled(working_tiled, working_blocks)

    expansion = LandExpansion(
        edge=edge,
        depth=depth,
        previous_width=previous_width,
        previous_height=previous_height,
        new_width=working_tiled["width"],
        new_height=working_tiled["height"],
        new_sector_ids=new_sector_ids,
        new_sector_names=new_sector_names,
        strip_x0=strip_rect[0],
        strip_y0=strip_rect[1],
        strip_x1=strip_rect[2],
        strip_y1=strip_rect[3],
    )
    return working_tiled, working_blocks, world, expansion


def walkable_components(collision: list[list[bool]]) -> list[list[tuple[int, int]]]:
    """Every group of walkable tiles you can walk between, largest first.

    Plain flood fill over the collision grid, four-connected, matching
    what MovementSystem's pathfinder can actually traverse.
    """
    height = len(collision)
    width = len(collision[0]) if height else 0
    seen = [[False] * width for _ in range(height)]
    components: list[list[tuple[int, int]]] = []
    for y0 in range(height):
        for x0 in range(width):
            if collision[y0][x0] or seen[y0][x0]:
                continue
            stack = [(x0, y0)]
            seen[y0][x0] = True
            cells: list[tuple[int, int]] = []
            while stack:
                x, y = stack.pop()
                cells.append((x, y))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        if not collision[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            stack.append((nx, ny))
            components.append(cells)
    components.sort(key=len, reverse=True)
    return components


def open_walkable_corridors(tiled: dict[str, Any], *, min_region: int = 24) -> int:
    """Carve roads until the town is one place you can walk across.

    THE FAULT THIS REPAIRS. The Ville map and Kasukabe's own genesis strip
    are each internally walkable and were never joined to one another.
    Measured on the shipped map: 89% of tiles walkable, 17 separate
    regions, and only 26% of the walkable ground reachable on foot from
    Hobbs Cafe. The town's residents live in one region (4,158 tiles) and
    every workplace the economy depends on - farmland, forest, ore
    outcrop, temple, government complex - sits in another (11,719 tiles).
    The Willows Market sits alone in a third, 70 tiles wide.

    What that produced, in one run: 107 `move` calls refused with "no
    walkable route", to the busiest places in the town - Kasukabe Farmland
    55, Willows Market 51, Hobbs Cafe 28. The residents diagnosed it
    themselves before any instrument did. Corwin Thorne, in dialogue:
    "kept ending up back at Hobbs, or wandering street tiles that don't
    lead anywhere."

    The repair is deliberately the smallest thing that works: for each
    stranded region, clear the collision cells along one straight line to
    the nearest tile of the main region. It carves roads; it never
    demolishes a sector, moves a building or edits any layer but
    collision. `min_region` skips slivers - a two-tile gap inside a wall
    is not somewhere a resident needs to reach, and joining every one of
    them would tunnel through buildings for no gain.

    Returns the number of regions joined, so a caller can log it and a
    test can assert the repair actually did something.
    """
    width, height = tiled["width"], tiled["height"]
    flat = get_layer(tiled, COLLISION_LAYER)
    collision = [
        [flat[y * width + x] != 0 for x in range(width)] for y in range(height)
    ]

    joined = 0
    while joined <= 64:
        components = walkable_components(collision)
        if len(components) < 2:
            break
        stranded = [c for c in components[1:] if len(c) >= min_region]
        if not stranded:
            break

        target = set(stranded[0])
        frontier = list(components[0])
        parent: dict[tuple[int, int], tuple[int, int] | None] = {
            cell: None for cell in frontier
        }
        reached = None
        while frontier and reached is None:
            nxt = []
            for x, y in frontier:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    step = (x + dx, y + dy)
                    if step in parent:
                        continue
                    if not (0 <= step[0] < width and 0 <= step[1] < height):
                        continue
                    parent[step] = (x, y)
                    if step in target:
                        reached = step
                        break
                    nxt.append(step)
                if reached is not None:
                    break
            frontier = nxt

        if reached is None:
            break

        cell: tuple[int, int] | None = reached
        while cell is not None:
            x, y = cell
            collision[y][x] = False
            flat[y * width + x] = 0
            cell = parent[cell]

        joined += 1

    set_layer(tiled, COLLISION_LAYER, flat)
    return joined
