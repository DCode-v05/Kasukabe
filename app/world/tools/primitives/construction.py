"""build_structure: DESIGN.md section 1's freeform building primitive.
"Licensed builders use a design tool - arbitrary footprint on the tile
grid, rooms, purpose. Bill of materials auto-computed from geometry
(timber/metal per tile). Requires plot rights + government permit ...
Construction occupies the crew for time proportional to size."

A `Plot` (models.py) is the government's grant of *where* an agent may
build and how large an area they may claim - this module never creates
one (that is a not-yet-built government-permitting tool, outside this
task's primitive list; tests and bootstrap seed plots directly). One
`build_structure` call claims any subset of a permitted plot's footprint,
computes area/perimeter from that exact tile set, and debits timber and
one chosen structural metal proportionally - "auto-computed from
geometry" taken literally: `compute_perimeter` walks the actual tile
adjacency, it is not a fixed multiplier on tile count.

`complete_construction` is a plain function, not a tool: DESIGN.md's
"construction occupies the crew for time proportional to size" is a
tick-driven state transition (nobody "calls" a building finished), so it
is the seam a future world tick loop drives, the same documented-but-
not-yet-wired pattern world.tools.primitives.resources.regenerate_zones
already uses for the same reason - `buildings_due_for_completion` is the
pure filter that seam needs (mirrors regenerate_zones' own signature:
plain function of `world`, no side effects).

`complete_construction` optionally takes a `map_service` (world.map.
service.MapService): when supplied, it calls MapService.stamp_building
so the finished building becomes a real Sector on the live map, not just
a status flip in WorldState.buildings - see that method's own docstring
for why parcel.py's PlotRegistry deliberately leaves this exact step to
whichever module owns construction (this one). map_service is optional
and defaults to None because a host may have no map at all when the
gitignored Ville assets are absent (GROW-06 wired the service onto both
hosts; the assets are the only remaining reason it can be missing) -
completion still succeeds and world.buildings still flips to
COMPLETE either way; only the map half is skipped when there is nothing
to stamp onto. The map-service call happens BEFORE the status flip
(mirrors registry.py's own "validate/call fallible externals before
mutating owned state" ordering discipline) so a footprint that clashes
with an existing sector leaves the building UNDER_CONSTRUCTION rather
than COMPLETE-but-unmapped.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator

from world.core.events import WorldEvent
from world.life.models import AgentLifeRecord
from world.map.expansion import building_sector_name, footprint_rect
from world.map.model import ORE_METALS, TileCoord
from world.map.service import MapService
from world.tools.errors import (
    ActorRequirementError,
    LocationGateError,
    NotFoundError,
)
from world.tools.models import Building, BuildingStatus, OwnerClass
from world.tools.place_aliases import satisfies_gate
from world.tools.primitives.property import require_tile
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState

# ASSUMPTION (DESIGN.md describes the mechanic, "auto-computed from
# geometry," but pins no rate): timber scales with footprint area (walls
# and floor), the structural metal scales with perimeter (the frame's
# outer edge), and build time scales with area, all in the units
# recipes.py's own timber/ore chains produce.
TIMBER_PER_TILE = 5
METAL_PER_TILE = 2
CONSTRUCTION_TICKS_PER_TILE = 6

BUILDER_LICENSE_TYPE = "builder_license"


def compute_perimeter(tiles: frozenset[tuple[int, int]]) -> int:
    """Count of tile edges with no same-footprint neighbor - the actual
    exposed boundary of a freeform polyomino, not tile_count * 4."""
    perimeter = 0
    for x, y in tiles:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (x + dx, y + dy) not in tiles:
                perimeter += 1
    return perimeter


class WorkSiteParams(BaseModel):
    building_id: str


class BuildStructureParams(BaseModel):
    plot_id: str
    footprint: list[tuple[int, int]] = Field(min_length=1)
    purpose: str
    metal_kind: str = "iron"

    @field_validator("metal_kind")
    @classmethod
    def check_metal_kind(cls, value: str) -> str:
        if value not in ORE_METALS:
            raise ValueError(f"{value!r} is not one of the 5 ore metals {ORE_METALS}")
        return value


async def build_structure_handler(
    world: WorldState, agent: AgentLifeRecord, params: BuildStructureParams
) -> ToolOutcome:
    plot = world.plots.get(params.plot_id)
    if plot is None:
        raise NotFoundError("plot", params.plot_id)
    if not plot.permitted:
        raise ActorRequirementError("build_structure", [f"plot {plot.plot_id} has no government permit"])

    # GROW-17. You build where you stand.
    #
    # This tool is registered with `location_gate=None`, so until this
    # check a licensed builder could raise a smithy on a plot across town
    # without leaving the tavern - and DESIGN.md section 2's "construction
    # occupies the crew for time proportional to size" means nothing if the
    # crew is never on site.
    #
    # HERE and not in the ToolSpec because `location_gate` is a constant
    # the registry checks before the handler runs, and the place a builder
    # must stand in is not a constant: it is wherever the plot it named
    # happens to be. The registry cannot know that without reading params.
    #
    # `satisfies_gate`, not `!=`: every other co-location check in the town
    # folds (TOOL-19 fixed `trade` and `steal` for exactly this), and a
    # strict compare would refuse a builder standing in the right field
    # under another spelling of its name.
    if plot.location_id:
        here = world.location_of(agent.agent_id)
        if not satisfies_gate(here, plot.location_id):
            raise LocationGateError("build_structure", plot.location_id, here)

    footprint = frozenset(params.footprint)
    # GROW-09. The permit is not the deed.
    #
    # There are four unconnected notions of land in this codebase:
    # `tile_owners` (1x1 `TileOwnership`), `tools.models.Plot` (a
    # development RIGHT), `map/parcel.PlotRegistry` (footprint claims
    # validated against map geometry), and buildings and businesses keyed
    # by opaque location strings. `build_structure` checked exactly one of
    # them - `Plot.permitted` - so a resident holding a permit could raise
    # a building on ground another resident owns, and the ledger that
    # records who owns what would never hear about it.
    #
    # `require_tile` materialises the government default for unrecorded
    # ground (section 31.2: "unclaimed land is a government asset"), so
    # this does not forbid building on the commons - it forbids building
    # on somebody's garden.
    trespassing = sorted(
        f"({x},{y}) is held by {require_tile(world, x, y).owner_id}"
        for x, y in footprint
        if require_tile(world, x, y).owner_class is OwnerClass.PRIVATE
        and require_tile(world, x, y).owner_id != agent.agent_id
    )
    if trespassing:
        raise ActorRequirementError(
            "build_structure",
            [
                "cannot build on ground held by someone else: "
                + ", ".join(trespassing[:5])
            ],
        )

    if not footprint.issubset(plot.footprint):
        raise ActorRequirementError(
            "build_structure",
            [f"requested footprint is not within plot {plot.plot_id}'s granted area"],
        )

    area = len(footprint)
    perimeter = compute_perimeter(footprint)
    timber_needed = area * TIMBER_PER_TILE
    metal_needed = perimeter * METAL_PER_TILE
    construction_ticks = area * CONSTRUCTION_TICKS_PER_TILE
    metal_item = f"{params.metal_kind}_ore"

    # Fallible tools-owned mutations (insufficient inventory) both happen
    # before the Building row is created, so a failed materials check
    # leaves no half-built record behind.
    world.inventory.debit(agent.agent_id, "timber", timber_needed)
    world.inventory.debit(agent.agent_id, metal_item, metal_needed)

    building = Building(
        building_id=f"building-{uuid.uuid4().hex[:12]}",
        plot_id=plot.plot_id,
        builder_agent_id=agent.agent_id,
        purpose=params.purpose,
        footprint=footprint,
        timber_used=timber_needed,
        metal_used=metal_needed,
        started_tick=world.tick(),
        completion_tick=world.tick() + construction_ticks,
        # GROW-18: the same arithmetic as the deadline, so a crew that
        # works every tick finishes exactly when the old countdown would
        # have. The difference is that somebody has to turn up.
        labour_ticks_required=construction_ticks,
    )
    world.buildings[building.building_id] = building

    event = WorldEvent(
        type="world.building.construction_started",
        tick=world.tick(),
        payload={
            "building_id": building.building_id,
            "plot_id": plot.plot_id,
            "builder_agent_id": agent.agent_id,
            "purpose": params.purpose,
            "area_tiles": area,
            # UI-33. Where it is going up, not just how big it is. A
            # viewer told a building exists and given only `area_tiles`
            # cannot put a hoarding on the map - and construction is the
            # one thing in this town that changes the map's shape.
            "rect": footprint_rect(footprint),
            "kind": params.purpose,
            "status": "under_construction",
            "timber_used": timber_needed,
            "metal_used": metal_needed,
            "completion_tick": building.completion_tick,
        },
    )
    return ToolOutcome(
        result={"building_id": building.building_id, "completion_tick": building.completion_tick},
        events=[event],
    )


def sites_worth_naming(world: WorldState) -> list[str]:
    """Which building ids to put in a `work_site` refusal.

    Only the sites that are actually open: a COMPLETE building is not an
    answer to "which site did you mean", and offering one would send the
    resident straight into the next refusal. Ordered so the list is stable
    to read across turns.
    """
    return sorted(
        building_id
        for building_id, building in world.buildings.items()
        if building.status is BuildingStatus.UNDER_CONSTRUCTION
        and building.labour_remaining > 0
    )


async def work_site_handler(
    world: WorldState, agent: AgentLifeRecord, params: WorkSiteParams
) -> ToolOutcome:
    """Put in a tick of work on a building under construction.

    GROW-18. DESIGN section 2: "construction occupies the crew for time
    proportional to size". Before this, `completion_tick` was
    `now + area * 6` and nothing else was ever required - no crew, no
    hands, and no building verb in `CONTINUABLE_TOOLS`. A licensed builder
    could break ground and walk away, and the town handed them a finished
    smithy on the deadline.

    Continuable, and that is the point: this is the verb `keep_doing`
    repeats, so a resident who starts building spends their afternoon
    building instead of paying for a fresh decision every tick. It is in
    `CONTINUABLE_TOOLS` for the same reason `harvest` and `craft` are.

    NOT licence-gated. The builder needs the licence to open a site
    (`build_structure` requires it); the hands carrying timber do not, and
    requiring it would mean a two-person company could never put two
    people on a job. That is GROW-10's yard, one layer along.
    """
    building = world.buildings.get(params.building_id)
    if building is None:
        # A site id is not something a resident can look up. election30's
        # seven `work_site` calls all failed here, on invented ids -
        # `b_construction`, `whitlock_repair` - and each was told only that
        # the id did not exist, so nobody worked a site all run and 52
        # successful fellings of timber went into nothing. Naming the open
        # sites is the same rule as `withdraw_item`'s storages and
        # `teach_skill`'s skills: a refusal that teaches, or noise.
        raise NotFoundError(
            "building", params.building_id, sites_worth_naming(world)
        )
    if building.status is not BuildingStatus.UNDER_CONSTRUCTION:
        raise ActorRequirementError(
            "work_site", [f"{params.building_id} is not under construction"]
        )
    if building.labour_remaining <= 0:
        raise ActorRequirementError(
            "work_site",
            [f"{params.building_id} is already built and waiting on its deadline"],
        )

    # You work where you stand - the same rule `build_structure` follows
    # (GROW-17), for the same reason: a crew that is not on site is not a
    # crew. `satisfies_gate`, not `!=`, because every co-location check in
    # this town folds place names.
    plot = world.plots.get(building.plot_id)
    if plot is not None and plot.location_id:
        here = world.location_of(agent.agent_id)
        if not satisfies_gate(here, plot.location_id):
            raise LocationGateError("work_site", plot.location_id, here)

    world.buildings[params.building_id] = building.model_copy(
        update={"labour_ticks_done": building.labour_ticks_done + 1}
    )
    done = world.buildings[params.building_id]
    event = WorldEvent(
        type="world.building.worked",
        tick=world.tick(),
        payload={
            "building_id": params.building_id,
            "worker_agent_id": agent.agent_id,
            "labour_ticks_done": done.labour_ticks_done,
            "labour_ticks_required": done.labour_ticks_required,
            "rect": footprint_rect(done.footprint),
            "kind": done.purpose,
            "status": "under_construction",
        },
    )
    return ToolOutcome(
        result={
            "building_id": params.building_id,
            "labour_ticks_done": done.labour_ticks_done,
            "labour_remaining": done.labour_remaining,
        },
        events=[event],
    )


async def complete_construction(
    world: WorldState, building_id: str, *, map_service: MapService | None = None
) -> WorldEvent:
    """Flip a building to COMPLETE once world.tick() has reached its
    completion_tick, and - when a MapService is wired in - stamp its
    footprint onto the live map as a real Sector. Not a tool - see module
    docstring for the map_service contract and ordering.
    """
    building = world.buildings.get(building_id)
    if building is None:
        raise NotFoundError("building", building_id)
    if world.tick() < building.completion_tick:
        raise ActorRequirementError(
            "complete_construction",
            [f"{building_id} is not due until tick {building.completion_tick}, now is {world.tick()}"],
        )
    # GROW-18: and the crew has to have built it.
    #
    # Both, not either. The deadline alone let a builder start a smithy and
    # walk away; the labour alone would let a crew of twelve raise a
    # cathedral in an afternoon. DESIGN section 2 asks for time
    # proportional to size AND for the crew to be occupied by it.
    if building.labour_remaining > 0:
        raise ActorRequirementError(
            "complete_construction",
            [
                f"{building_id} still needs {building.labour_remaining} "
                f"tick(s) of work on site ({building.labour_ticks_done} of "
                f"{building.labour_ticks_required} done)"
            ],
        )

    # UI-33: the same four keys the started event carries, so a viewer
    # that drew a hoarding can replace it with the finished building
    # without re-fetching anything.
    payload: dict[str, object] = {
        "building_id": building_id,
        "purpose": building.purpose,
        "rect": footprint_rect(building.footprint),
        "kind": building.purpose,
        "status": "complete",
    }
    if map_service is not None:
        tile_footprint = frozenset(TileCoord(x, y) for x, y in building.footprint)
        sector_id = await map_service.stamp_building(
            tile_footprint, purpose=building.purpose, owner=building.builder_agent_id, tick=world.tick()
        )
        payload["map_sector_id"] = sector_id
        # Built by the same function the stamp uses, not a second copy
        # of the f-string - see building_sector_name's docstring.
        payload["sector_name"] = building_sector_name(
            building.purpose, building.builder_agent_id
        )

    world.buildings[building_id] = building.model_copy(update={"status": BuildingStatus.COMPLETE})
    return WorldEvent(type="world.building.completed", tick=world.tick(), payload=payload)


def buildings_due_for_completion(world: WorldState) -> list[str]:
    """Building ids still UNDER_CONSTRUCTION whose completion_tick has
    arrived - the pure filter a real tick loop (WorldServer.advance_tick,
    world/wiring.py) runs every tick before calling complete_construction
    on each id, mirroring how that same method already drives
    resources.regenerate_zones. Read-only: never mutates anything, so it
    is safe to call every tick regardless of whether anything is due.
    """
    return [
        building.building_id
        for building in world.buildings.values()
        if building.status is BuildingStatus.UNDER_CONSTRUCTION
        and world.tick() >= building.completion_tick
        # GROW-18: and the work is done. A site nobody has worked is not
        # due; it is unstarted. Without this the tick loop would call
        # `complete_construction` on it every tick for the rest of the run
        # and eat the refusal each time.
        and building.labour_remaining == 0
    ]


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="build_structure",
            description="Raise a building on a permitted plot, freeform footprint.",
            params_model=BuildStructureParams,
            handler=build_structure_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(required_license_types=frozenset({BUILDER_LICENSE_TYPE})),
        ),
        ToolSpec(
            name="work_site",
            description="Put in a tick of work on a building under construction.",
            params_model=WorkSiteParams,
            handler=work_site_handler,
            namespace=ToolNamespace.PUBLIC,
            # GROW-18: the site is wherever the plot is, which is a
            # parameter and not a constant - so the check lives in the
            # handler, exactly as build_structure's does (GROW-17).
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
    ]
