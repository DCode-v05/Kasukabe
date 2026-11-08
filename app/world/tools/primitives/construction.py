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

    if plot.location_id:
        here = world.location_of(agent.agent_id)
        if not satisfies_gate(here, plot.location_id):
            raise LocationGateError("build_structure", plot.location_id, here)

    footprint = frozenset(params.footprint)
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
    if building.labour_remaining > 0:
        raise ActorRequirementError(
            "complete_construction",
            [
                f"{building_id} still needs {building.labour_remaining} "
                f"tick(s) of work on site ({building.labour_ticks_done} of "
                f"{building.labour_ticks_required} done)"
            ],
        )

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
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
    ]
