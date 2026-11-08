"""harvest, mine, fell_timber: the three primitives that turn a map-edge
resource zone (DESIGN.md section 1: "farmland (10 crop varieties), ore
outcrops (5 metals), forest (timber, common)") into an item in the
actor's inventory. All three are the same shape - stand at the zone,
take some yield, credit inventory - so `harvest_from_zone` below is the
one mutation primitive; the three public handlers just resolve which
zone their own params name and validate the resource kind matches.

Farmland and ore outcrops carry a finite `remaining_yield` that this
module depletes and can later `regenerate_zones` (a plain function, not
a tool - see its docstring) refills over time; forest is DESIGN.md's one
explicitly "common" zone, modeled as unlimited (`remaining_yield=None`)
so `fell_timber` never depletes it. Every cap and regen rate here is an
ASSUMPTION (DESIGN.md does not pin one) - see `seed_resource_zones`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from world.core.events import WorldEvent
from world.life.models import AgentLifeRecord
from world.map.model import CROP_VARIETIES, FOREST_RESOURCE, ORE_METALS
from world.tools.errors import NotFoundError
from world.tools.locations import LOCATION_FOREST, farmland_location, ore_outcrop_location
from world.tools.models import ResourceZone, ResourceZoneKind
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState

DEFAULT_ZONE_YIELD = 10_000
DEFAULT_ZONE_REGEN_PER_TICK = 5


def seed_resource_zones(world: WorldState) -> None:
    """Populate world.resource_zones with all 10 farmland strips, all 5
    ore outcrops, and the one forest zone. Not a tool - genesis/bootstrap
    wiring, called once when a WorldState is constructed (bootstrap.py)
    or directly by tests that need zones seeded without a full bootstrap.
    """
    for crop in CROP_VARIETIES:
        location_id = farmland_location(crop)
        world.resource_zones[location_id] = ResourceZone(
            location_id=location_id,
            kind=ResourceZoneKind.FARMLAND,
            resource_item=crop,
            remaining_yield=DEFAULT_ZONE_YIELD,
            regen_per_tick=DEFAULT_ZONE_REGEN_PER_TICK,
        )
    for metal in ORE_METALS:
        location_id = ore_outcrop_location(metal)
        world.resource_zones[location_id] = ResourceZone(
            location_id=location_id,
            kind=ResourceZoneKind.ORE_OUTCROP,
            resource_item=f"{metal}_ore",
            remaining_yield=DEFAULT_ZONE_YIELD,
            regen_per_tick=DEFAULT_ZONE_REGEN_PER_TICK,
        )
    world.resource_zones[LOCATION_FOREST] = ResourceZone(
        location_id=LOCATION_FOREST,
        kind=ResourceZoneKind.FOREST,
        resource_item=FOREST_RESOURCE,
        remaining_yield=None,
        regen_per_tick=0,
    )


def regenerate_zones(world: WorldState) -> None:
    """Refill every finite-yield zone by its regen_per_tick, capped at
    DEFAULT_ZONE_YIELD. Not a tool - a plain function meant to be called
    once per tick by world's (not yet built) tick loop, the same
    "not-yet-wired, documented seam" pattern life/tick.py's
    run_hourly_life_tick already uses for its own caller.
    """
    for zone in world.resource_zones.values():
        if zone.remaining_yield is None or zone.regen_per_tick <= 0:
            continue
        zone.remaining_yield = min(DEFAULT_ZONE_YIELD, zone.remaining_yield + zone.regen_per_tick)


def harvest_from_zone(
    world: WorldState, agent: AgentLifeRecord, location_id: str, expected_kind: ResourceZoneKind, quantity: int
) -> ToolOutcome:
    zone = world.resource_zones.get(location_id)
    if zone is None or zone.kind is not expected_kind:
        raise NotFoundError("resource_zone", location_id)
    if zone.remaining_yield is not None:
        if zone.remaining_yield < quantity:
            raise NotFoundError("resource_zone_yield", location_id)
        zone.remaining_yield -= quantity

    world.inventory.credit(agent.agent_id, zone.resource_item, quantity)
    event = WorldEvent(
        type="world.resource.harvested",
        tick=world.tick(),
        payload={
            "agent_id": agent.agent_id,
            "location_id": location_id,
            "item": zone.resource_item,
            "quantity": quantity,
            "target_agent_id": agent.agent_id,
        },
    )
    return ToolOutcome(result={"item": zone.resource_item, "quantity": quantity}, events=[event])


class HarvestParams(BaseModel):
    crop: str
    quantity: int = Field(default=1, gt=0)


async def harvest_handler(world: WorldState, agent: AgentLifeRecord, params: HarvestParams) -> ToolOutcome:
    if params.crop not in CROP_VARIETIES:
        raise NotFoundError("crop_variety", params.crop)
    return harvest_from_zone(
        world, agent, farmland_location(params.crop), ResourceZoneKind.FARMLAND, params.quantity
    )


class MineParams(BaseModel):
    metal: str
    quantity: int = Field(default=1, gt=0)


async def mine_handler(world: WorldState, agent: AgentLifeRecord, params: MineParams) -> ToolOutcome:
    if params.metal not in ORE_METALS:
        raise NotFoundError("ore_metal", params.metal)
    return harvest_from_zone(
        world, agent, ore_outcrop_location(params.metal), ResourceZoneKind.ORE_OUTCROP, params.quantity
    )


class FellTimberParams(BaseModel):
    quantity: int = Field(default=1, gt=0)


async def fell_timber_handler(
    world: WorldState, agent: AgentLifeRecord, params: FellTimberParams
) -> ToolOutcome:
    return harvest_from_zone(world, agent, LOCATION_FOREST, ResourceZoneKind.FOREST, params.quantity)


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="harvest",
            description="Harvest a crop from the farmland strip growing it.",
            params_model=HarvestParams,
            handler=harvest_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=lambda world, params: farmland_location(params.crop),
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="mine",
            description="Mine ore from the outcrop producing it.",
            params_model=MineParams,
            handler=mine_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=lambda world, params: ore_outcrop_location(params.metal),
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="fell_timber",
            description="Fell timber in the forest.",
            params_model=FellTimberParams,
            handler=fell_timber_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=LOCATION_FOREST,
            actor_requirements=ActorRequirements(),
        ),
    ]
