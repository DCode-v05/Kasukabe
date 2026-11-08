"""MapService: the one stateful object a running world process holds for
everything this package owns - the live WorldMap, its backing Tiled JSON
and block-name tables (so further land growth can keep extending the same
map instead of starting over), and the plot registry.

This is world/map's equivalent of ledger/core.py's Ledger class: a plain
class (not a dataclass - construction needs real logic, the classmethod
constructors below), public mutating methods are async and funnel through
`emit()` to publish events, and the pure geometry/validation work stays in
the synchronous helpers this module delegates to (expansion.py,
parcel.py) rather than being inlined here. See ledger/core.py's module
docstring for the fuller version of this reasoning; it applies verbatim.

Also implements the `Checkpointable` structural protocol
(world/core/checkpoint.py: `snapshot() -> dict`, `restore(dict) -> None`)
without importing world.core - Checkpointable is a typing.Protocol, so
satisfying its shape is enough for CheckpointManager.register("map", ...)
to accept a MapService with no import-time coupling between the two
packages, mirroring how ledger/, charter/, and core/ all stay decoupled
from each other at import time per app/CONVENTIONS.md's module-boundary
spirit.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from world.map.blocks import SpecialBlockTables, load_special_blocks
from world.map.expansion import (
    ARENA_ID_RANGE_OFFSET,
    DEFAULT_GENESIS_STRIP_DEPTH,
    GenesisZoneIds,
    LandExpansion,
    append_kasukabe_genesis_zones,
    building_sector_name,
    footprint_rect,
    open_walkable_corridors,
    approve_land_petition,
    next_available_id,
)
from world.map.genesis import apply_genesis_config
from world.map.model import TileCoord, WorldMap
from world.map.parcel import Plot, PlotRegistry
from world.map.tiled_json import (
    ARENA_LAYER,
    SECTOR_LAYER,
    SYNTHETIC_ID_FLOOR,
    Edge,
    build_world_map_from_tiled,
    get_layer,
    load_tiled_map,
    set_layer,
)

# Matches world.core.events.EventBus.emit(event_type, tick, payload)'s
# exact signature (event_type: str, tick: int, payload: dict[str, Any])
# -> None, so `MapService(..., event_sink=event_bus.emit)` wires in with
# no adapter, the same seam ledger/core.py's narrower EventSink uses for
# its own LedgerEvent. Kept as a bare Callable here (not importing
# WorldEvent) so this package has no import-time dependency on
# world.core - see module docstring.
EventSink = Callable[[str, int, dict[str, Any]], Awaitable[None]]


class BuildingStampError(ValueError):
    """Aggregated failure stamping a completed building's footprint onto
    the live map (MapService.stamp_building) - matches parcel.py's
    PlotError and expansion.py's ExpansionError's own one-report-per-
    attempt convention: a builder whose footprint clashes with two
    different existing sectors sees both problems at once."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(f"cannot stamp building onto the map ({len(self.errors)} error(s)):\n  - {joined}")


def blocks_to_snapshot(blocks: SpecialBlockTables) -> dict[str, Any]:
    return {
        "world": list(blocks.world),
        "sectors": {str(k): list(v) for k, v in blocks.sectors.items()},
        "arenas": {str(k): list(v) for k, v in blocks.arenas.items()},
        "game_objects": {str(k): list(v) for k, v in blocks.game_objects.items()},
        "spawning_locations": {str(k): list(v) for k, v in blocks.spawning_locations.items()},
    }


def blocks_from_snapshot(data: dict[str, Any]) -> SpecialBlockTables:
    tables = SpecialBlockTables(world=tuple(data["world"]))
    for block_id, row in data["sectors"].items():
        tables.sectors[int(block_id)] = tuple(row)
    for block_id, row in data["arenas"].items():
        tables.arenas[int(block_id)] = tuple(row)
    for block_id, row in data["game_objects"].items():
        tables.game_objects[int(block_id)] = tuple(row)
    for block_id, row in data["spawning_locations"].items():
        tables.spawning_locations[int(block_id)] = tuple(row)
    return tables


def plot_to_snapshot(plot: Plot) -> dict[str, Any]:
    return {
        "id": plot.id,
        "footprint": [[tile.x, tile.y] for tile in plot.footprint],
        "purpose": plot.purpose,
        "owner": plot.owner,
        "permit_id": plot.permit_id,
        "registered_at_tick": plot.registered_at_tick,
    }


def plot_from_snapshot(data: dict[str, Any]) -> Plot:
    return Plot(
        id=data["id"],
        footprint=frozenset(TileCoord(x, y) for x, y in data["footprint"]),
        purpose=data["purpose"],
        owner=data["owner"],
        permit_id=data.get("permit_id"),
        registered_at_tick=data.get("registered_at_tick", 0),
    )


class MapService:
    """Holds one live WorldMap plus the Tiled JSON/block tables it was
    built from and the PlotRegistry claimed against it.

    tiled/blocks are kept alongside world (not discarded after the
    initial build) specifically so expand_land can keep growing the same
    map correctly across any number of later petitions - expansion.py's
    id allocators need to see every id minted so far, which lives in
    `blocks`, not in the derived WorldMap.
    """

    def __init__(
        self,
        world: WorldMap,
        tiled: dict[str, Any],
        blocks: SpecialBlockTables,
        *,
        event_sink: EventSink | None = None,
    ) -> None:
        self.world = world
        self.tiled = tiled
        self.blocks = blocks
        self.plots = PlotRegistry(world)
        self.event_sink = event_sink

    @classmethod
    def from_kasukabe_genesis(
        cls,
        ville_dir: Path,
        *,
        strip_depth: int = DEFAULT_GENESIS_STRIP_DEPTH,
        event_sink: EventSink | None = None,
    ) -> tuple["MapService", GenesisZoneIds]:
        """Boots a MapService straight from the Ville assets, with
        Kasukabe's genesis config applied and the government/temple/
        resource zones already minted - the one call a world process
        makes at startup to get a ready-to-use map service.
        """
        # GROW-21: the same builder `build_kasukabe_genesis_map` uses.
        #
        # These eleven lines used to be a step-for-step copy of it, so
        # "load, mint, join, build, classify" lived in two places and a fix
        # to one would silently leave the other behind. The corridor step is
        # the one that already mattered: without it the Ville map and the
        # genesis strip are 17 disconnected walkable regions, and 107 `move`
        # calls in one run answered "no walkable route".
        from world.map.genesis import build_kasukabe_genesis_parts

        tiled, blocks, world, zone_ids = build_kasukabe_genesis_parts(
            ville_dir, strip_depth=strip_depth
        )
        # `tiled` and `blocks` are kept, not discarded: `expand_land` needs
        # every id minted so far to keep growing the SAME map (see this
        # class's own docstring), which is the one thing this caller wants
        # that `build_kasukabe_genesis_map` throws away.
        return cls(world, tiled, blocks, event_sink=event_sink), zone_ids

    async def emit(self, event_type: str, tick: int, payload: dict[str, Any]) -> None:
        if self.event_sink is not None:
            await self.event_sink(event_type, tick, payload)

    # -- land growth --------------------------------------------------------

    async def expand_land(
        self,
        *,
        edge: Edge,
        depth: int,
        tick: int,
        new_sector_name: str | None = None,
        arena_names: list[str] | None = None,
        purpose: str | None = None,
    ) -> LandExpansion:
        """DESIGN.md section 8's President "land petitions (approve ->
        map grows)". Adopts the new tiled/blocks/world only after
        approve_land_petition returns successfully (see that function's
        docstring on transactional safety), then publishes
        `world.land.expanded`.

        Existing plot claims survive: PlotRegistry keeps its own
        plots/claimed_by_tile dicts, only its `world` reference is
        repointed at the freshly grown map so future claims validate
        against the new bounds.
        """
        tiled, blocks, world, expansion = await approve_land_petition(
            self.tiled,
            self.blocks,
            edge=edge,
            depth=depth,
            new_sector_name=new_sector_name,
            arena_names=arena_names,
        )
        # GROW-13: join the new strip to the town before adopting it.
        #
        # `approve_land_petition` carves a sector into the Tiled JSON and
        # stops. `open_walkable_corridors` had only ever run at genesis, so
        # every strip the town grew was an island - and that function was
        # written precisely because a map of disconnected regions is a town
        # whose residents cannot reach their own workplaces.
        #
        # Cut into the TILES before the map is rebuilt, because
        # `build_world_map_from_tiled` is what turns tiles into the graph
        # `move` paths over. Rebuilt only when something was actually cut.
        opened = open_walkable_corridors(tiled)
        if opened:
            world = build_world_map_from_tiled(tiled, blocks)

        self.tiled = tiled
        self.blocks = blocks
        self.world = world
        self.plots.world = world
        expansion = expansion.model_copy(
            update={"purpose": purpose, "corridors_opened": opened}
        )
        await self.emit("world.land.expanded", tick, expansion.model_dump(mode="json"))
        return expansion

    # -- buildings --------------------------------------------------------

    async def stamp_building(
        self,
        footprint: frozenset[TileCoord],
        *,
        purpose: str,
        owner: str,
        tick: int,
    ) -> int:
        """Turns a just-completed building's footprint into a real Sector
        on the live map. world/map/parcel.py's own module docstring names
        this exact seam by hand: PlotRegistry answers "is this claim
        valid", never "what a completed building's rooms/purpose mean at
        runtime (which) is the tool registry's job once construction
        finishes and the footprint becomes a real Sector" - this method is
        that job. world/tools/primitives/construction.py's
        complete_construction calls this the moment a Building flips to
        COMPLETE, when a MapService is wired in.

        Freeform (DESIGN.md section 1: "arbitrary footprint on the tile
        grid"), not necessarily rectangular, so this paints SECTOR_LAYER/
        ARENA_LAYER tile by tile via tiled_json.get_layer/set_layer rather
        than expansion.py's carve_sector, which only accepts an
        axis-aligned Rect - a licensed builder's arbitrary footprint is
        exactly the freeform shape carve_sector cannot express. Every
        footprint tile must already be in bounds, walkable, and belong to
        no existing sector - the same three rules
        PlotRegistry.validate_claim holds a plot claim to (see that
        method) - so a building can never overwrite land another sector or
        another building already occupies. Mints one sector and one
        same-shaped arena (matching every genesis zone's own sector+arena
        pairing, expansion.py's carve_sector) so the finished building is
        immediately enterable by name via WorldMap.find_arena, not merely
        visible on sector_grid.

        Rebuilds self.world afterward (build_world_map_from_tiled) and
        repoints self.plots.world at it - the same two steps expand_land
        takes after mutating self.tiled/self.blocks, see that method's
        docstring. Publishes `world.building.mapped` and returns the
        minted sector id.
        """
        if not footprint:
            raise ValueError("cannot stamp a building with an empty footprint")
        rect = footprint_rect(footprint)

        errors: list[str] = []
        for tile in footprint:
            if not self.world.in_bounds(tile):
                errors.append(f"tile {tile} is outside the map ({self.world.width}x{self.world.height})")
                continue
            if not self.world.is_walkable(tile):
                errors.append(f"tile {tile} is not walkable (occupied by existing terrain)")
            existing_sector = self.world.sector_at(tile)
            if existing_sector is not None:
                errors.append(f"tile {tile} already belongs to sector {existing_sector.name!r}")
        if errors:
            raise BuildingStampError(errors)

        world_name = self.blocks.world[1]
        sector_id = next_available_id(self.blocks.sectors)
        arena_id = next_available_id(self.blocks.arenas, floor=SYNTHETIC_ID_FLOOR + ARENA_ID_RANGE_OFFSET)
        sector_name = building_sector_name(purpose, owner)
        self.blocks.add_sector(sector_id, world_name, sector_name)
        self.blocks.add_arena(arena_id, world_name, sector_name, sector_name)

        width = self.tiled["width"]
        sector_data = get_layer(self.tiled, SECTOR_LAYER)
        arena_data = get_layer(self.tiled, ARENA_LAYER)
        for tile in footprint:
            index = tile.y * width + tile.x
            sector_data[index] = sector_id
            arena_data[index] = arena_id
        set_layer(self.tiled, SECTOR_LAYER, sector_data)
        set_layer(self.tiled, ARENA_LAYER, arena_data)

        self.world = build_world_map_from_tiled(self.tiled, self.blocks)
        self.plots.world = self.world

        await self.emit(
            "world.building.mapped",
            tick,
            {
                "sector_id": sector_id,
                "arena_id": arena_id,
                "sector_name": sector_name,
                "owner": owner,
                "purpose": purpose,
                "tiles": len(footprint),
                # UI-33. `tiles` is a COUNT, and a count cannot be drawn.
                # The review's conclusion - whichever of its three options
                # is taken - is that "`world.building.*` events must carry
                # {rect, sector_name, kind, status}", because a viewer that
                # learns a building exists and not WHERE has learned
                # nothing it can put on a map.
                "rect": rect,
                "kind": purpose,
                "status": "mapped",
            },
        )
        return sector_id

    # -- plots ----------------------------------------------------------------

    async def claim_plot(self, plot: Plot, tick: int) -> None:
        self.plots.register(plot)
        await self.emit(
            "world.plot.claimed",
            tick,
            {"plot_id": plot.id, "owner": plot.owner, "purpose": plot.purpose, "tiles": plot.area},
        )

    async def transfer_plot(self, plot_id: str, new_owner: str | None, tick: int) -> Plot:
        plot = self.plots.transfer(plot_id, new_owner)
        await self.emit("world.plot.transferred", tick, {"plot_id": plot_id, "new_owner": new_owner})
        return plot

    async def release_plot(self, plot_id: str, tick: int) -> Plot:
        plot = self.plots.release(plot_id)
        await self.emit("world.plot.released", tick, {"plot_id": plot_id})
        return plot

    # -- Checkpointable (world/core/checkpoint.py's structural protocol) ------

    def snapshot(self) -> dict[str, Any]:
        """Everything needed to reconstruct this service exactly:
        tiled (already JSON-safe) + blocks + the live plot claims.
        world itself is NOT snapshotted directly - it is fully
        determined by tiled+blocks (restore() rebuilds it the same way
        the constructor does), so keeping it out avoids ever restoring a
        world that has drifted from the tiled/blocks that supposedly
        produced it.
        """
        return {
            "tiled": self.tiled,
            "blocks": blocks_to_snapshot(self.blocks),
            "plots": [plot_to_snapshot(p) for p in self.plots.plots.values()],
        }

    def restore(self, data: dict[str, Any]) -> None:
        self.tiled = data["tiled"]
        self.blocks = blocks_from_snapshot(data["blocks"])
        self.world = build_world_map_from_tiled(self.tiled, self.blocks)
        apply_genesis_config(self.world)

        self.plots = PlotRegistry(self.world)
        for entry in data["plots"]:
            plot = plot_from_snapshot(entry)
            self.plots.plots[plot.id] = plot
            for tile in plot.footprint:
                self.plots.claimed_by_tile[tile] = plot.id
