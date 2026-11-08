"""Core map data model: tiles, sectors, arenas, and the WorldMap they live in.

The Ville tilemap (and, by extension, Kasukabe's grown copy of it) is a
140x100 grid of 32px tiles carrying five independent layers of meaning per
tile: is it walkable, which sector is it part of (a named building or
public space), which arena within that sector (a room), which interactive
game object sits there, and which numbered spawn point. The upstream
Generative Agents project (smallville/) represents all five as parallel
CSV matrices keyed by a shared (x, y) grid plus small "special_blocks"
lookup tables that give each id a human name - see loader.py and blocks.py
for where that raw shape gets read. This module is what those loaders
build: one WorldMap object that answers "is (x, y) walkable", "what sector
is agent standing on", "where is Hobbs Cafe" without the caller ever
touching a CSV row again.

Everything here is a plain dataclass, not pydantic. app/CONVENTIONS.md
carves out "tile geometry" as pure computation, and a WorldMap is exactly
that: an in-memory geometry index rebuilt wholesale on load or on a land
expansion (rare, not a per-tick operation), never partially mutated field
by field the way a pydantic boundary model would be. Sector.active and
Sector.categories are the one thing that does get set after construction
(by genesis.apply_genesis_config), which is also why Sector is a mutable
dataclass rather than frozen - see that module for why the categories
can't be known until classification runs. Types that genuinely cross a
process boundary (an expansion result destined for a `world.land.expanded`
event, say) are pydantic models instead - see expansion.py.
"""

from __future__ import annotations

import enum
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TileCoord:
    """One grid cell. Hashable and frozen so it can key dicts/sets and sit
    directly in pathfinding's open/closed sets without a wrapper.

    Deliberately does not check against a map's width/height here (a
    TileCoord has no map to check against); only non-negativity is
    enforced, since a negative coordinate is never meaningful regardless
    of which map it is compared against. Full bounds checking against a
    specific WorldMap is WorldMap.in_bounds()'s job.
    """

    x: int
    y: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0:
            raise ValueError(f"tile coordinates must be non-negative, got ({self.x}, {self.y})")


class BuildingCategory(str, enum.Enum):
    """The building-type taxonomy Kasukabe classifies every Ville sector
    into (genesis.py decides which categories are active at genesis;
    this enum only names the possibilities). Values are lowercase to
    match app/CONVENTIONS.md's event-payload snake_case convention.

    STREET has no corresponding Sector: it describes a walkable tile that
    belongs to no sector at all (public ground between buildings), so it
    is tracked as a WorldMap.is_street() predicate, not a classified
    Sector. It stays in this enum anyway because genesis.py's active-set
    needs to say "streets are always open" using the same vocabulary as
    every other category.

    PUB exists on the base Ville map (The Rose and Crown) but is NOT in
    Kasukabe's genesis-active set (DESIGN.md section 1 lists the genesis
    buildings explicitly and a pub is not one of them) - it is present on
    the map, walkable, and fully modeled, just closed for business until
    someone re-registers it, exactly like DESIGN.md section 3's "all
    business types contestable" already allows for any building.

    HOSPITAL and POLICE are deliberately absent from this enum, not just
    from the active set: DESIGN.md section 1 is explicit that the town
    must build these itself, and neither exists anywhere on the base
    Ville map. When a licensed builder eventually puts one up, it is a
    freeform Plot (parcel.py) with purpose="hospital" or
    purpose="police station", a free-text field, not a pre-classified
    Sector - this module has no authority to pre-declare an institution
    the town charter hasn't created yet.
    """

    HOUSE = "house"
    CAFE = "cafe"
    GROCERY = "grocery"
    PHARMACY = "pharmacy"
    PARK = "park"
    COLLEGE = "college"
    OFFICE = "office"
    STREET = "street"
    GOVERNMENT = "government"
    TEMPLE = "temple"
    PUB = "pub"


class ResourceKind(str, enum.Enum):
    """Map-edge resource zones (DESIGN.md section 1: "not buildings").

    A resource-zone Sector carries exactly one of these on its
    resource_kind field instead of a BuildingCategory set - the two are
    mutually exclusive by construction (see build_world_map / genesis.py),
    matching DESIGN's own distinction between buildings and zones.
    """

    FARMLAND = "farmland"
    ORE_OUTCROP = "ore_outcrop"
    FOREST = "forest"


CROP_VARIETIES: tuple[str, ...] = (
    "wheat",
    "rice",
    "corn",
    "potato",
    "tomato",
    "carrot",
    "cabbage",
    "soybean",
    "barley",
    "cotton",
)
ORE_METALS: tuple[str, ...] = ("iron", "copper", "tin", "silver", "gold")
FOREST_RESOURCE = "timber"

assert len(CROP_VARIETIES) == 10, "DESIGN.md section 1: farmland has exactly 10 crop varieties"
assert len(ORE_METALS) == 5, "DESIGN.md section 1: ore outcrops have exactly 5 metals"


@dataclass
class GameObjectKind:
    """One entry from game_object_blocks: a furniture/fixture type an
    agent can interact with (a bed, a cooking area, a library table).

    The upstream data models these as "<all>" - not tied to one specific
    arena, the same `id` value (e.g. "bed") can appear inside any number
    of different rooms across the map. That is why this is a flat
    id->kind catalog rather than something nested under Sector/Arena;
    WorldMap.game_object_at(coord) is how a caller finds which *instance*
    of a kind sits at a given tile.
    """

    id: int
    name: str


@dataclass
class SpawningLocation:
    """One named agent spawn point (e.g. "sp-A" inside a bedroom).

    tiles is usually a single-tile set in the source data, but kept as a
    set (not one TileCoord) since nothing in the format guarantees
    exactly one tile per id.
    """

    id: int
    world_name: str
    sector_name: str
    arena_name: str
    label: str
    tiles: frozenset[TileCoord] = field(default_factory=frozenset)


@dataclass
class Arena:
    """A room within a Sector (e.g. "cafe" inside "Hobbs Cafe", or one of
    the 10 crop-variety strips inside Kasukabe's farmland zone).

    Arena names repeat across different sectors ("bathroom", "main room"
    are common to many houses) - they are only unique within their own
    sector, never globally, which is why lookups always go through
    WorldMap.find_arena(sector_name, arena_name) rather than an
    arena-name index.
    """

    id: int
    sector_id: int
    name: str
    tiles: frozenset[TileCoord] = field(default_factory=frozenset)

    @property
    def area(self) -> int:
        return len(self.tiles)


@dataclass
class Sector:
    """A named building or zone (e.g. "Hobbs Cafe", or Kasukabe's minted
    "Kasukabe Government Complex"): the top level of the world's location
    hierarchy, one step below the world itself.

    categories and active start empty/False at construction (the CSV/JSON
    loaders that build a Sector know nothing about Kasukabe's genesis
    rules) and are filled in by genesis.apply_genesis_config once, right
    after loading - see that function's docstring for the classification
    rule. resource_kind is the mutually-exclusive alternative to
    categories for map-edge resource zones (ResourceKind's docstring);
    exactly one of "categories is non-empty" or "resource_kind is set"
    holds for any sector genesis actually classifies, though a brand new,
    not-yet-classified Sector legitimately has neither.
    """

    id: int
    world_name: str
    name: str
    arena_ids: tuple[int, ...] = ()
    tiles: frozenset[TileCoord] = field(default_factory=frozenset)
    categories: frozenset[BuildingCategory] = field(default_factory=frozenset)
    resource_kind: ResourceKind | None = None
    active: bool = False

    @property
    def area(self) -> int:
        return len(self.tiles)


@dataclass(frozen=True, slots=True)
class NamedLocation:
    """A resolved address in the world/sector/arena/spawn-label hierarchy -
    the same four-level naming scheme the Ville CSVs already use, surfaced
    as a first-class value instead of a formatted string. Any level past
    world_name may be unset: describing an exterior street tile yields a
    NamedLocation with only world_name set.
    """

    world_name: str
    sector_name: str | None = None
    arena_name: str | None = None
    label: str | None = None

    def address(self) -> str:
        """Colon-joined human-readable address, e.g.
        "the Ville:Hobbs Cafe:cafe". Matches the world:sector:arena
        ordering the source CSVs already imply."""
        parts = [self.world_name]
        for part in (self.sector_name, self.arena_name, self.label):
            if part:
                parts.append(part)
        return ":".join(parts)


@dataclass
class WorldMap:
    """The whole loaded map: geometry (grids) plus the named-location
    index (sectors/arenas/game objects/spawn points) built on top of it.

    Grids are stored [y][x] (row-major, matching how the source CSVs are
    laid out and how the reshape in build_world_map reads them) so
    `collision[y][x]` and `image_row[y]` both read the natural way; every
    public method here takes a TileCoord and does the y/x indexing
    internally so no caller outside this module needs to remember the
    axis order.

    Mutable by design (matches ledger/models.py's Account: "internal,
    mutable ledger state, a plain dataclass" - a WorldMap is world/map's
    equivalent single piece of live in-memory state), but the only code
    expected to mutate one after construction is
    genesis.apply_genesis_config, which fills in Sector.active/categories
    right after loading. Land growth (expansion.py) does NOT mutate a
    WorldMap's grids in place - the grids are plain lists sized at
    construction and an expansion changes those sizes, so expansion
    always rebuilds a brand new WorldMap from the grown Tiled JSON
    (tiled_json.build_world_map_from_tiled) and hands the caller that new
    object. service.MapService is what holds the single mutable
    `self.world` reference across an expansion, reassigning it (and
    keeping its PlotRegistry pointed at the new object) rather than this
    class trying to grow itself.
    """

    world_name: str
    width: int
    height: int
    tile_size: int
    collision: list[list[bool]]
    sector_grid: list[list[int]]
    arena_grid: list[list[int]]
    game_object_grid: list[list[int]]
    spawning_grid: list[list[int]]
    sectors: dict[int, Sector] = field(default_factory=dict)
    arenas: dict[int, Arena] = field(default_factory=dict)
    game_object_kinds: dict[int, GameObjectKind] = field(default_factory=dict)
    spawning_locations: dict[int, SpawningLocation] = field(default_factory=dict)
    sector_id_by_name: dict[str, int] = field(default_factory=dict)


    def in_bounds_xy(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def in_bounds(self, coord: TileCoord) -> bool:
        return self.in_bounds_xy(coord.x, coord.y)

    def is_walkable(self, coord: TileCoord) -> bool:
        """False for out-of-bounds tiles too (not an error) - pathfinding
        and every other caller can probe a coordinate without a separate
        bounds check first."""
        if not self.in_bounds(coord):
            return False
        return not self.collision[coord.y][coord.x]

    def is_street(self, coord: TileCoord) -> bool:
        """A walkable tile that belongs to no sector: public ground
        between buildings. Always genesis-active (BuildingCategory.STREET
        docstring) - there is no Sector object to carry that flag on."""
        return self.is_walkable(coord) and self.sector_grid[coord.y][coord.x] == 0


    def sector_at(self, coord: TileCoord) -> Sector | None:
        if not self.in_bounds(coord):
            return None
        sector_id = self.sector_grid[coord.y][coord.x]
        return self.sectors.get(sector_id) if sector_id else None

    def arena_at(self, coord: TileCoord) -> Arena | None:
        if not self.in_bounds(coord):
            return None
        arena_id = self.arena_grid[coord.y][coord.x]
        return self.arenas.get(arena_id) if arena_id else None

    def game_object_at(self, coord: TileCoord) -> GameObjectKind | None:
        if not self.in_bounds(coord):
            return None
        object_id = self.game_object_grid[coord.y][coord.x]
        return self.game_object_kinds.get(object_id) if object_id else None

    def spawning_location_at(self, coord: TileCoord) -> SpawningLocation | None:
        if not self.in_bounds(coord):
            return None
        spawn_id = self.spawning_grid[coord.y][coord.x]
        return self.spawning_locations.get(spawn_id) if spawn_id else None

    def describe(self, coord: TileCoord) -> NamedLocation:
        """Reverse lookup: tile -> the fullest address it resolves to."""
        sector = self.sector_at(coord)
        arena = self.arena_at(coord)
        spawn = self.spawning_location_at(coord)
        return NamedLocation(
            world_name=self.world_name,
            sector_name=sector.name if sector else None,
            arena_name=arena.name if arena else None,
            label=spawn.label if spawn else None,
        )


    def find_sector(self, name: str) -> Sector | None:
        sector_id = self.sector_id_by_name.get(name.strip())
        return self.sectors.get(sector_id) if sector_id is not None else None

    def find_arena(self, sector_name: str, arena_name: str) -> Arena | None:
        sector = self.find_sector(sector_name)
        if sector is None:
            return None
        arena_name = arena_name.strip()
        for arena_id in sector.arena_ids:
            arena = self.arenas.get(arena_id)
            if arena is not None and arena.name == arena_name:
                return arena
        return None

    def search(self, query: str) -> list[NamedLocation]:
        """Case-insensitive substring search over sector and arena names.
        Not indexed (there are only ever a few hundred sectors/arenas even
        after several land expansions) - a linear scan is simpler and fast
        enough for an operator console or an agent's occasional "where is
        the ___" tool call."""
        needle = query.strip().lower()
        if not needle:
            return []
        hits: list[NamedLocation] = []
        for sector in self.sectors.values():
            if needle in sector.name.lower():
                hits.append(NamedLocation(self.world_name, sector.name))
        for arena in self.arenas.values():
            if needle in arena.name.lower():
                sector = self.sectors.get(arena.sector_id)
                hits.append(
                    NamedLocation(self.world_name, sector.name if sector else None, arena.name)
                )
        return hits


    def entry_point(self, sector_name: str, arena_name: str | None = None) -> TileCoord | None:
        """A walkable tile to path toward for "go to <sector>[, <arena>]".

        Sector/Arena footprints include wall and furniture tiles (they are
        the room's full extent, not just its floor), so a caller cannot
        assume every tile in .tiles is walkable. This prefers a registered
        SpawningLocation inside the target (the source data's own
        "where an agent should appear here" hint) and falls back to any
        walkable tile in the footprint.
        """
        if arena_name is not None:
            arena = self.find_arena(sector_name, arena_name)
            if arena is None:
                return None
            footprint = arena.tiles
        else:
            sector = self.find_sector(sector_name)
            if sector is None:
                return None
            footprint = sector.tiles

        for spawn in self.spawning_locations.values():
            if spawn.sector_name != sector_name:
                continue
            if arena_name is not None and spawn.arena_name != arena_name:
                continue
            for tile in spawn.tiles:
                return tile

        for tile in footprint:
            if self.is_walkable(tile):
                return tile
        return None

    def nearest_walkable(self, coord: TileCoord, max_radius: int = 10) -> TileCoord | None:
        """BFS ring search outward from coord for the closest walkable
        tile, capped at max_radius steps. Used when a caller has a
        conceptual target (a game object, a footprint corner) that may
        itself be a wall/furniture tile and needs an adjacent standable
        tile instead."""
        if self.is_walkable(coord):
            return coord
        seen = {coord}
        queue: deque[tuple[TileCoord, int]] = deque([(coord, 0)])
        while queue:
            current, dist = queue.popleft()
            if dist >= max_radius:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = current.x + dx, current.y + dy
                if nx < 0 or ny < 0 or not self.in_bounds_xy(nx, ny):
                    continue
                neighbor = TileCoord(nx, ny)
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                if self.is_walkable(neighbor):
                    return neighbor
                queue.append((neighbor, dist + 1))
        return None


def reshape(flat: list[int], width: int, height: int) -> list[list[int]]:
    """Row-major flat list -> [y][x] grid. Every maze CSV and every Tiled
    layer's `data` array is exactly this shape (width*height ints, row 0
    first) - see loader.py and tiled_json.py, the two producers of the
    flat lists this function reshapes."""
    if len(flat) != width * height:
        raise ValueError(
            f"expected {width * height} cells ({width}x{height}), got {len(flat)}"
        )
    return [flat[y * width : (y + 1) * width] for y in range(height)]


def build_world_map(
    *,
    world_name: str,
    width: int,
    height: int,
    tile_size: int,
    collision_flat: list[int],
    sector_flat: list[int],
    arena_flat: list[int],
    game_object_flat: list[int],
    spawning_flat: list[int],
    sector_names: dict[int, tuple[str, str]],
    arena_names: dict[int, tuple[str, str, str]],
    game_object_names: dict[int, tuple[str, str]],
    spawning_names: dict[int, tuple[str, str, str, str]],
) -> WorldMap:
    """The one place a WorldMap gets constructed from raw layer data.

    Both loader.py (CSV matrices) and tiled_json.py (a Tiled-style JSON's
    layers) reduce their respective source format down to these five flat
    int arrays plus four id->name lookup tables, then call this shared
    builder - so there is exactly one implementation of "scan the grids
    and build Sector/Arena/GameObjectKind/SpawningLocation objects",
    regardless of which file format produced the numbers. See blocks.py
    for the shape of the four name-lookup dicts (SpecialBlockTables.as_*
    methods build them).

    sector_names/arena_names/game_object_names/spawning_names come from
    the "special_blocks" side of the source data (an id doesn't carry its
    own name; a separate table maps id -> name) - see blocks.py.
    """
    collision_grid = [[v != 0 for v in row] for row in reshape(collision_flat, width, height)]
    sector_grid = reshape(sector_flat, width, height)
    arena_grid = reshape(arena_flat, width, height)
    game_object_grid = reshape(game_object_flat, width, height)
    spawning_grid = reshape(spawning_flat, width, height)

    sector_tiles: dict[int, set[TileCoord]] = {}
    arena_tiles: dict[int, set[TileCoord]] = {}
    game_object_seen: set[int] = set()
    spawning_tiles: dict[int, set[TileCoord]] = {}

    for y in range(height):
        for x in range(width):
            sid = sector_grid[y][x]
            if sid:
                sector_tiles.setdefault(sid, set()).add(TileCoord(x, y))
            aid = arena_grid[y][x]
            if aid:
                arena_tiles.setdefault(aid, set()).add(TileCoord(x, y))
            gid = game_object_grid[y][x]
            if gid:
                game_object_seen.add(gid)
            spid = spawning_grid[y][x]
            if spid:
                spawning_tiles.setdefault(spid, set()).add(TileCoord(x, y))

    sector_id_by_name: dict[str, int] = {}
    sectors: dict[int, Sector] = {}
    for sid, (wname, sname) in sector_names.items():
        sectors[sid] = Sector(
            id=sid,
            world_name=wname,
            name=sname,
            tiles=frozenset(sector_tiles.get(sid, ())),
        )
        sector_id_by_name[sname] = sid

    arenas: dict[int, Arena] = {}
    sector_arena_ids: dict[int, list[int]] = {}
    for aid, (_wname, sname, aname) in arena_names.items():
        sid = sector_id_by_name.get(sname)
        if sid is None:
            sid = -1
        arenas[aid] = Arena(id=aid, sector_id=sid, name=aname, tiles=frozenset(arena_tiles.get(aid, ())))
        if sid != -1:
            sector_arena_ids.setdefault(sid, []).append(aid)

    for sid, arena_ids in sector_arena_ids.items():
        sectors[sid].arena_ids = tuple(arena_ids)

    game_object_kinds = {
        gid: GameObjectKind(id=gid, name=name)
        for gid, (_scope, name) in game_object_names.items()
        if gid in game_object_seen
    }

    spawning_locations = {
        spid: SpawningLocation(
            id=spid,
            world_name=wname,
            sector_name=sname,
            arena_name=aname,
            label=label,
            tiles=frozenset(spawning_tiles.get(spid, ())),
        )
        for spid, (wname, sname, aname, label) in spawning_names.items()
    }

    return WorldMap(
        world_name=world_name,
        width=width,
        height=height,
        tile_size=tile_size,
        collision=collision_grid,
        sector_grid=sector_grid,
        arena_grid=arena_grid,
        game_object_grid=game_object_grid,
        spawning_grid=spawning_grid,
        sectors=sectors,
        arenas=arenas,
        game_object_kinds=game_object_kinds,
        spawning_locations=spawning_locations,
        sector_id_by_name=sector_id_by_name,
    )
