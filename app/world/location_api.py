"""Real place/location API (task "world-agent-api"): GET /places,
GET /places/{location_id}, GET /world/state, wired against the REAL
WorldState the running world server holds (world.locations,
world.businesses, world.resource_zones, world.storages, world.charter,
world.ledger, world.event_bus) plus the REAL Ville map (world.map.
MapService / WorldMap / Sector / Arena), never a mock or a hand-rolled
stand-in.

THE CENTRAL FINDING THIS MODULE IS BUILT AROUND (read this before trusting
any "who is here" answer): the game's real Ville map and the tool
registry's location_id namespace are two DISCONNECTED spaces under
current wiring, exactly the "two modules built against an assumed
contract" failure shape this whole project is on guard against.

- world.map.MapService loads real Sectors with real, human-readable
  names ("Hobbs Cafe", "Kasukabe Government Complex", "Adam Smith's
  house") and real tile geometry (map/genesis.py, map/expansion.py).
- Every location_id an agent, a business, or a government tool actually
  uses is an unrelated opaque slug: charter/models.py's LOCATION_TOWN_HALL
  = "town_hall", tools/locations.py's LOCATION_TEMPLE = "temple",
  wiring.py's home_location() = "home:<household_id>", and every genesis
  business's location_id is its bare business_type ("cafe", "pharmacy").
  world.core.perception.LocationIndex tracks agents purely by these
  opaque strings; it has never heard of a real Sector name.
- Nothing in the repo bridges the two. tools/locations.py's own docstring
  calls this "future integration work for whoever wires world.map's
  WorldMap into this package" - as of this file, still nobody.

Concretely: every one of the 250 genesis agents starts at location_id
"home:<household_id>" (wiring.py's apply_genesis_population), which
matches no real Sector, no real Arena, no real tile. Only a handful of
canonical slugs (temple/government_complex/town_hall/forest/the 10
farmland crops/the 5 ore metals, and the grocery/cafe/pharmacy business
types by category) have a real, code-derivable counterpart on the map,
via the STATIC_SECTOR_ALIASES / STATIC_ARENA_ALIASES / farmland/ore
arena-name reconstruction / BUSINESS_TYPE_CATEGORY_HINT tables below -
every one of those tables is built directly from string constants that
already exist elsewhere in the repo (map/expansion.py's *_SECTOR_NAME
constants, charter/models.py's LOCATION_* constants, tools/locations.py's
farmland_location/ore_outcrop_location), never invented here.
resolve_location_on_map() is the one place that reconciliation happens;
everything else in this module calls it rather than re-deriving it.

Because of this, GET /places necessarily returns two different kinds of
row and is honest about which is which via `resolution_method`:
- rows seeded from a real, active map Sector (every genesis building:
  houses, cafe, grocery/pharmacy, park, college, office, government
  complex, temple, pub) - these show real tile geometry, but will show
  ZERO occupants unless some agent's location_id happens to alias onto
  them (see above - true today only for temple/government_complex/
  town_hall/forest/farmland/ore/grocery/cafe/pharmacy).
- rows seeded from a "logical" location_id actually referenced by
  world.locations / world.businesses / world.resource_zones (home:*,
  business-type slugs with no category hint, and anything else agents
  are actually standing in) - these show real, live occupants and real
  business/storage state, but `map_sector` is null unless resolved via
  the alias tables above.

Every occupant count, business, storage, resource-zone reading, and
event history entry in this module reads world.locations / world.
businesses / world.storages / world.resource_zones / world.event_bus
directly, live, off the same running WorldState the tool registry
mutates - nothing here is cached, snapshotted, or mocked.

Event history (`recent_events` on GET /places/{id}) is bounded by
world.core.events.EventBus's own 2000-entry ring buffer (events.py:
`deque(maxlen=buffer_size)`), not a full historical log - a place with a
long, quiet history will show fewer events than actually happened there.
The trace store (supabase/'s SQLite) is the durable, complete `events`
table; this module has no read path into it (world/'s HTTP layer has
never exposed one) and does not pretend to.

Namespace ACL (DESIGN.md section 2: "Non-members cannot see a
confidential tool exists"): `usable_tools` below lists PUBLIC-namespace
tools only. This is an unauthenticated read route with no operator token
(unlike world/admin_api.py's /admin/* routes) - GOVERNMENT/BUSINESS/
ORGANIZATION tools stay unlisted here on purpose, matching the design's
confidentiality rule, even though every invocation is still fully logged
elsewhere for research (world.tool_invocations, DESIGN.md section 2).

Mount (main.py is a sibling file this task does not edit - see main.py's
own docstring for why): add one import and one line inside main.py's
lifespan()/module body, after `server = await build_world_server(...)`:

    from world.location_api import LocationDeps, build_location_router
    app.include_router(build_location_router(lambda: LocationDeps(
        world=server_dep().world, map_service=server_dep().map_service,
        registry=server_dep().registry,
    )))

No route-path collision with main.py's existing routes (it has no
/places or /world/state today).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from world.charter.models import (
    LOCATION_GOVERNMENT_COMPLEX,
    LOCATION_PATENT_OFFICE,
    LOCATION_TOWN_HALL,
)
from world.core.clock import timestamp_for
from world.ledger.money import TOTAL_USD_CENTS
from world.map.expansion import (
    FARMLAND_SECTOR_NAME,
    FOREST_SECTOR_NAME,
    GOVERNMENT_SECTOR_NAME,
    ORE_OUTCROP_SECTOR_NAME,
    TEMPLE_SECTOR_NAME,
)
from world.map.model import CROP_VARIETIES, ORE_METALS, BuildingCategory, Sector, TileCoord
from world.map.service import MapService
from world.tools.locations import LOCATION_FOREST, LOCATION_TEMPLE
from world.tools.registry import ToolNamespace, ToolRegistry
from world.tools.state import WorldState

# ── the alias tables the module docstring describes: built entirely from
# string constants that already exist elsewhere in the repo ────────────

STATIC_SECTOR_ALIASES: dict[str, str] = {
    LOCATION_TEMPLE: TEMPLE_SECTOR_NAME,
    LOCATION_GOVERNMENT_COMPLEX: GOVERNMENT_SECTOR_NAME,
    LOCATION_FOREST: FOREST_SECTOR_NAME,
}

# town_hall is not its own sector - genesis carves it as an ARENA named
# literally "Town Hall" inside the government complex sector
# (map/expansion.py's append_kasukabe_genesis_zones:
# `arenas=single_arena_rect_bands(..., ("Town Hall", "Treasury Vault"))`).
STATIC_ARENA_ALIASES: dict[str, tuple[str, str]] = {
    LOCATION_TOWN_HALL: (GOVERNMENT_SECTOR_NAME, "Town Hall"),
}

# patent_office (charter/models.py's LOCATION_PATENT_OFFICE) has no real
# map counterpart at all: genesis only carves "Town Hall" and "Treasury
# Vault" inside the government sector, no third arena for a patent
# office. Listed here, unresolved, deliberately (not silently dropped)
# so GET /places/patent_office still returns a real row (occupants,
# usable tools) with map_sector explicitly null rather than a 404 that
# would read as "this place doesn't exist."
KNOWN_UNRESOLVED_LOCATION_IDS: frozenset[str] = frozenset({LOCATION_PATENT_OFFICE})

# Genesis's own arena-naming convention for the 10 farmland crop strips
# and 5 ore outcrop metal strips (map/expansion.py: `crop_names = tuple(
# f"{crop.capitalize()} Field" for crop in CROP_VARIETIES)`, same pattern
# for metals) - reconstructed here, not re-derived from a shared
# constant, because expansion.py computes it inline rather than exporting
# it; kept as its own named function so the one formula lives in one place.


def farmland_arena_name(crop: str) -> str:
    return f"{crop.capitalize()} Field"


def ore_outcrop_arena_name(metal: str) -> str:
    return f"{metal.capitalize()} Outcrop"


# Best-effort ONLY (see module docstring): a genesis business's
# location_id is its bare business_type (wiring.py's business_location()),
# which names no sector directly. Where DESIGN.md's own genesis building
# list ties a business type to exactly one real building category
# (cafe -> Hobbs Cafe, grocery/pharmacy -> The Willows Market and
# Pharmacy), this resolves it; a business_type with no entry here (e.g.
# "construction", the 10 control businesses' bakery/general_store/...)
# gets no map_sector, honestly, rather than a guess.
BUSINESS_TYPE_CATEGORY_HINT: dict[str, BuildingCategory] = {
    "cafe": BuildingCategory.CAFE,
    "grocery_store": BuildingCategory.GROCERY,
    "pharmacy": BuildingCategory.PHARMACY,
}


@dataclass(frozen=True)
class MapResolution:
    """What resolve_location_on_map() found for one location_id string.
    `method` is always one of the literal strings below so a caller (this
    module's own place-builder, or an external consumer of PlaceView's
    `resolution_method`) can branch or display it without guessing."""

    sector: Sector | None
    arena_name: str | None
    tile: TileCoord | None
    method: str  # "exact_sector" | "alias_sector" | "alias_arena" | "business_category_heuristic" | "no_map_service" | "unresolved"


def resolve_location_on_map(map_service: MapService | None, location_id: str) -> MapResolution:
    """The one reconciliation point the module docstring promises: turns
    an opaque tool-namespace location_id into real map geometry when a
    real correspondence exists, and says plainly when it does not (method
    == "unresolved") rather than inventing one. Every other function in
    this module that needs a tile/sector for a location_id calls this
    instead of re-deriving any part of it.
    """
    if map_service is None:
        return MapResolution(None, None, None, "no_map_service")
    world_map = map_service.world

    sector = world_map.find_sector(location_id)
    if sector is not None:
        return MapResolution(sector, None, world_map.entry_point(location_id), "exact_sector")

    if location_id in STATIC_SECTOR_ALIASES:
        sector_name = STATIC_SECTOR_ALIASES[location_id]
        sector = world_map.find_sector(sector_name)
        tile = world_map.entry_point(sector_name) if sector is not None else None
        return MapResolution(sector, None, tile, "alias_sector")

    if location_id in STATIC_ARENA_ALIASES:
        sector_name, arena_name = STATIC_ARENA_ALIASES[location_id]
        sector = world_map.find_sector(sector_name)
        tile = world_map.entry_point(sector_name, arena_name) if sector is not None else None
        return MapResolution(sector, arena_name, tile, "alias_arena")

    if location_id.startswith("farmland:"):
        crop = location_id.split(":", 1)[1]
        if crop in CROP_VARIETIES:
            arena_name = farmland_arena_name(crop)
            sector = world_map.find_sector(FARMLAND_SECTOR_NAME)
            tile = world_map.entry_point(FARMLAND_SECTOR_NAME, arena_name) if sector is not None else None
            return MapResolution(sector, arena_name, tile, "alias_arena")

    if location_id.startswith("ore_outcrop:"):
        metal = location_id.split(":", 1)[1]
        if metal in ORE_METALS:
            arena_name = ore_outcrop_arena_name(metal)
            sector = world_map.find_sector(ORE_OUTCROP_SECTOR_NAME)
            tile = world_map.entry_point(ORE_OUTCROP_SECTOR_NAME, arena_name) if sector is not None else None
            return MapResolution(sector, arena_name, tile, "alias_arena")

    category = BUSINESS_TYPE_CATEGORY_HINT.get(location_id)
    if category is not None:
        for candidate in world_map.sectors.values():
            if candidate.active and category in candidate.categories:
                return MapResolution(
                    candidate, None, world_map.entry_point(candidate.name), "business_category_heuristic"
                )

    return MapResolution(None, None, None, "unresolved")


def canonical_key_for(sector: Sector, arena_name: str | None) -> str:
    """One real map location's display key: the bare sector name for a
    whole-sector place, "<sector> :: <arena>" for an arena-specific one
    (the farmland/ore/town-hall cases) - `" :: "` never appears in a real
    Ville sector or arena name, so this round-trips unambiguously."""
    return sector.name if arena_name is None else f"{sector.name} :: {arena_name}"


# ── response models ──────────────────────────────────────────────────


class TileView(BaseModel):
    x: int
    y: int


class MapSectorView(BaseModel):
    sector_id: int
    sector_name: str
    arena_name: str | None
    categories: list[str]
    active: bool
    resource_kind: str | None
    tile_count: int
    entry_tile: TileView | None
    resolution_method: str


class BusinessAtPlaceView(BaseModel):
    business_id: str
    name: str
    business_type: str
    owner_agent_id: str
    employee_agent_ids: list[str]
    active: bool
    sim_balance: int
    inventory: dict[str, int]


class StorageAtPlaceView(BaseModel):
    storage_id: str
    owner_id: str
    holdings: dict[str, int]


class ResourceZoneAtPlaceView(BaseModel):
    kind: str
    resource_item: str
    remaining_yield: int | None
    regen_per_tick: int


class UsableToolView(BaseModel):
    name: str
    description: str
    namespace: str
    availability_note: str


class PlaceEventView(BaseModel):
    event_id: str
    type: str
    tick: int
    payload: dict[str, Any]


class PlaceSummaryView(BaseModel):
    location_id: str
    aliases: list[str]
    map_sector: MapSectorView | None
    occupant_agent_ids: list[str]
    occupant_count: int
    business_count: int
    has_resource_zone: bool


class PlaceDetailView(PlaceSummaryView):
    businesses: list[BusinessAtPlaceView]
    storages: list[StorageAtPlaceView]
    resource_zone: ResourceZoneAtPlaceView | None
    usable_tools: list[UsableToolView]
    recent_events: list[PlaceEventView]
    event_history_note: str


class ClockView(BaseModel):
    tick: int
    game_day: int
    day_tick: int
    hour: int
    minute: int
    is_daytime: bool


class LedgerStatusView(BaseModel):
    halted: bool
    halt_reason: str | None
    usd_total_cents: int
    expected_cents: int


class GovernmentView(BaseModel):
    pm_agent_id: str | None
    minister_agent_ids: list[str]
    elections_held: int
    latest_election_phase: str | None


class WorldStateView(BaseModel):
    clock: ClockView
    population_alive: int
    population_dormant: int
    population_ever: int
    births: int
    deaths: int
    businesses_active: int
    control_businesses: int
    government: GovernmentView
    ledger: LedgerStatusView


# ── place assembly ───────────────────────────────────────────────────


@dataclass
class PlaceRecord:
    location_id: str
    aliases: set[str] = field(default_factory=set)
    sector: Sector | None = None
    arena_name: str | None = None
    tile: TileCoord | None = None
    resolution_method: str = "unresolved"


def gather_logical_location_ids(world: WorldState) -> set[str]:
    """Every location_id currently real in the tool/ledger namespace: live
    occupancy (world.locations, exhaustive - see module docstring),
    every business's registered location, every resource zone, plus the
    handful of canonical government/temple slugs charter/tools/ already
    name so they show up even with nobody standing in them right now."""
    ids: set[str] = set(world.locations.location_agents.keys())
    ids.update(business.location_id for business in world.businesses.values())
    ids.update(world.resource_zones.keys())
    ids.update(
        {
            LOCATION_TEMPLE,
            LOCATION_GOVERNMENT_COMPLEX,
            LOCATION_TOWN_HALL,
            LOCATION_PATENT_OFFICE,
            LOCATION_FOREST,
        }
    )
    return ids


def build_place_records(world: WorldState, map_service: MapService | None) -> dict[str, PlaceRecord]:
    """Every place this API can return, keyed by its canonical
    location_id (see module docstring for the seeding + merge rule)."""
    records: dict[str, PlaceRecord] = {}

    if map_service is not None:
        for sector in map_service.world.sectors.values():
            if not sector.active:
                continue
            key = canonical_key_for(sector, None)
            records[key] = PlaceRecord(
                location_id=key,
                aliases={key},
                sector=sector,
                arena_name=None,
                tile=map_service.world.entry_point(sector.name),
                resolution_method="exact_sector",
            )

    for logical_id in sorted(gather_logical_location_ids(world)):
        resolution = resolve_location_on_map(map_service, logical_id)
        if resolution.sector is not None:
            key = canonical_key_for(resolution.sector, resolution.arena_name)
            record = records.get(key)
            if record is None:
                record = PlaceRecord(
                    location_id=key,
                    sector=resolution.sector,
                    arena_name=resolution.arena_name,
                    tile=resolution.tile,
                    resolution_method=resolution.method,
                )
                records[key] = record
            record.aliases.add(logical_id)
        else:
            record = records.get(logical_id)
            if record is None:
                record = PlaceRecord(location_id=logical_id, resolution_method=resolution.method)
                records[logical_id] = record
            record.aliases.add(logical_id)

    return records


def build_alias_index(records: dict[str, PlaceRecord]) -> dict[str, str]:
    """logical/display id -> canonical key, so GET /places/{id} accepts
    either the real sector name or any tool-namespace slug that aliases
    onto it."""
    index: dict[str, str] = {}
    for key, record in records.items():
        index[key] = key
        for alias in record.aliases:
            index[alias] = key
    return index


def occupants_of(world: WorldState, record: PlaceRecord) -> list[str]:
    occupants: set[str] = set()
    for alias in record.aliases:
        occupants.update(world.locations.agents_at(alias))
    return sorted(occupants)


def businesses_of(world: WorldState, record: PlaceRecord) -> list[Any]:
    return [b for b in world.businesses.values() if b.location_id in record.aliases]


def storages_of(world: WorldState, record: PlaceRecord) -> list[Any]:
    return [s for s in world.storages.values() if s.location_id in record.aliases]


def resource_zone_of(world: WorldState, record: PlaceRecord) -> Any | None:
    for alias in record.aliases:
        zone = world.resource_zones.get(alias)
        if zone is not None:
            return zone
    return None


def map_sector_view(record: PlaceRecord) -> MapSectorView | None:
    if record.sector is None:
        return None
    return MapSectorView(
        sector_id=record.sector.id,
        sector_name=record.sector.name,
        arena_name=record.arena_name,
        categories=sorted(c.value for c in record.sector.categories),
        active=record.sector.active,
        resource_kind=record.sector.resource_kind.value if record.sector.resource_kind else None,
        tile_count=record.sector.area,
        entry_tile=TileView(x=record.tile.x, y=record.tile.y) if record.tile is not None else None,
        resolution_method=record.resolution_method,
    )


# Tool names whose location_gate is a resolver function (world.tools.
# primitives.commerce), not a fixed string - ToolRegistry.resolve_
# location_gate can only be evaluated against real call params, which
# this read-only view never has. Resolved by direct domain knowledge of
# what each one actually requires instead of executing the resolver:
# buy/sell require a business AT this place (business_location_gate);
# store_item/withdraw_item require a Storage already registered at this
# place (storage_location_gate_required) - store_item alone is also
# callable at ANY location to create a brand-new storage
# (storage_location_gate_optional returns None for an unseen storage_id),
# so it is not listed as place-specific unless a Storage already exists
# here. See world/tools/primitives/commerce.py for the real gates this
# mirrors.
BUSINESS_GATED_TOOL_NAMES: frozenset[str] = frozenset({"buy", "sell"})
STORAGE_GATED_TOOL_NAMES: frozenset[str] = frozenset({"withdraw_item"})
STORAGE_OPTIONAL_TOOL_NAMES: frozenset[str] = frozenset({"store_item"})


def usable_tools_at(
    registry: ToolRegistry, record: PlaceRecord, has_business: bool, has_storage: bool
) -> list[UsableToolView]:
    tools: list[UsableToolView] = []
    for spec in registry.tools.values():
        if spec.namespace is not ToolNamespace.PUBLIC:
            continue  # namespace ACL - see module docstring
        if spec.location_gate is None:
            continue  # not location-specific, not useful information here
        if spec.name in BUSINESS_GATED_TOOL_NAMES:
            if has_business:
                tools.append(
                    UsableToolView(
                        name=spec.name,
                        description=spec.description,
                        namespace=spec.namespace.value,
                        availability_note="a business registered at this place makes this callable here",
                    )
                )
            continue
        if spec.name in STORAGE_GATED_TOOL_NAMES:
            if has_storage:
                tools.append(
                    UsableToolView(
                        name=spec.name,
                        description=spec.description,
                        namespace=spec.namespace.value,
                        availability_note="a storage container already exists at this place",
                    )
                )
            continue
        if spec.name in STORAGE_OPTIONAL_TOOL_NAMES:
            note = (
                "a storage container already exists at this place"
                if has_storage
                else "callable anywhere to create a brand-new storage here"
            )
            tools.append(
                UsableToolView(
                    name=spec.name, description=spec.description, namespace=spec.namespace.value, availability_note=note
                )
            )
            continue
        if isinstance(spec.location_gate, str) and spec.location_gate in record.aliases:
            tools.append(
                UsableToolView(
                    name=spec.name,
                    description=spec.description,
                    namespace=spec.namespace.value,
                    availability_note=f"location_gate == {spec.location_gate!r}",
                )
            )
    tools.sort(key=lambda t: t.name)
    return tools


def recent_events_at(world: WorldState, record: PlaceRecord, limit: int) -> list[PlaceEventView]:
    matches = [e for e in world.event_bus.recent() if e.payload.get("location_id") in record.aliases]
    matches.sort(key=lambda e: e.tick, reverse=True)
    return [
        PlaceEventView(event_id=e.event_id, type=e.type, tick=e.tick, payload=e.payload) for e in matches[:limit]
    ]


def build_summary_view(world: WorldState, record: PlaceRecord) -> PlaceSummaryView:
    occupants = occupants_of(world, record)
    return PlaceSummaryView(
        location_id=record.location_id,
        aliases=sorted(record.aliases),
        map_sector=map_sector_view(record),
        occupant_agent_ids=occupants,
        occupant_count=len(occupants),
        business_count=len(businesses_of(world, record)),
        has_resource_zone=resource_zone_of(world, record) is not None,
    )


def build_detail_view(world: WorldState, registry: ToolRegistry, record: PlaceRecord) -> PlaceDetailView:
    summary = build_summary_view(world, record)
    businesses = businesses_of(world, record)
    storages = storages_of(world, record)
    zone = resource_zone_of(world, record)

    business_views = [
        BusinessAtPlaceView(
            business_id=b.business_id,
            name=b.name,
            business_type=b.business_type,
            owner_agent_id=b.owner_agent_id,
            employee_agent_ids=sorted(b.employee_agent_ids),
            active=b.active,
            sim_balance=world.ledger.accounts[b.business_id].sim_units
            if b.business_id in world.ledger.accounts
            else 0,
            inventory=world.inventory.holdings(b.business_id),
        )
        for b in businesses
    ]
    storage_views = [
        StorageAtPlaceView(storage_id=s.storage_id, owner_id=s.owner_id, holdings=world.inventory.holdings(s.storage_id))
        for s in storages
    ]
    zone_view = (
        ResourceZoneAtPlaceView(
            kind=zone.kind.value,
            resource_item=zone.resource_item,
            remaining_yield=zone.remaining_yield,
            regen_per_tick=zone.regen_per_tick,
        )
        if zone is not None
        else None
    )

    return PlaceDetailView(
        **summary.model_dump(),
        businesses=business_views,
        storages=storage_views,
        resource_zone=zone_view,
        usable_tools=usable_tools_at(registry, record, has_business=bool(businesses), has_storage=bool(storages)),
        recent_events=recent_events_at(world, record, limit=50),
        event_history_note=(
            "bounded by the in-process EventBus's 2000-event ring buffer (world/core/events.py), "
            "not a full historical log - see this module's docstring"
        ),
    )


# ── /world/state ─────────────────────────────────────────────────────


def build_ledger_status_view(world: WorldState) -> LedgerStatusView:
    return LedgerStatusView(
        halted=world.ledger.halted,
        halt_reason=world.ledger.halt_reason,
        usd_total_cents=world.ledger.usd_total(),
        expected_cents=TOTAL_USD_CENTS,
    )


async def build_world_state_view(world: WorldState, genesis_population: int, control_business_count: int) -> WorldStateView:
    ts = timestamp_for(world.current_tick)
    government_state = await world.charter.store.get_government_state()
    elections = await world.charter.store.list_elections()
    latest_election = max(elections, key=lambda e: e.scheduled_tick, default=None)

    alive = sum(1 for a in world.agents.values() if a.alive)
    dormant = sum(1 for a in world.agents.values() if a.dormancy.is_dormant)
    deaths = sum(1 for a in world.agents.values() if not a.alive)
    # Every newborn agent (world.god_grant_birth_request in wiring.py)
    # gets a fresh agent_id inserted into world.agents beyond the genesis
    # roster - an exact live count, not an event-log tally bounded by the
    # ring buffer (see this module's docstring on that bound).
    births = max(0, len(world.agents) - genesis_population)

    return WorldStateView(
        clock=ClockView(
            tick=ts.tick_index, game_day=ts.game_day, day_tick=ts.day_tick, hour=ts.hour, minute=ts.minute,
            is_daytime=ts.is_daytime,
        ),
        population_alive=alive,
        population_dormant=dormant,
        population_ever=len(world.agents),
        births=births,
        deaths=deaths,
        businesses_active=sum(1 for b in world.businesses.values() if b.active),
        control_businesses=control_business_count,
        government=GovernmentView(
            pm_agent_id=government_state.pm_agent_id,
            minister_agent_ids=list(government_state.minister_agent_ids),
            elections_held=len(elections),
            latest_election_phase=latest_election.phase.value if latest_election is not None else None,
        ),
        ledger=build_ledger_status_view(world),
    )


# ── deps + router ────────────────────────────────────────────────────


@dataclass
class LocationDeps:
    """Everything the routes below need, gathered by whichever host
    constructs a FastAPI app and hands a zero-arg getter to
    build_location_router - see this module's docstring for the exact
    one-line mount main.py's owner needs. `genesis_population` and
    `control_business_count` back /world/state's births/businesses
    counters (deps.genesis.population, len(deps.control_businesses) on
    world/wiring.py's WorldServer - passed as plain ints here so this
    module has no import-time dependency on wiring.WorldServer, matching
    admin_api.py's own "loosest interface that is still real" discipline).
    """

    world: WorldState
    map_service: MapService | None
    registry: ToolRegistry
    genesis_population: int = 250
    control_business_count: int = 10



async def add_cors_headers(response: Response) -> None:
    """UI-22. `views.html` and `agents.html` are served from the game
    server on :8400 and read the world on :8100, so every call they make
    is cross-origin.

    Six of world's routers already carry this dependency - governance,
    social, agent-viewer, family, skills, workshop - and these two did
    not, for no reason anyone recorded. Both are read-only GET APIs with
    no credentials, which is the whole CORS story here: the same one
    `governance_api.add_cors_headers` documents.

    The cost of the gap is not obvious from a page that mostly works.
    Whichever call happens to be first fails, the page reports "could not
    reach world API", and the reader concludes the world is down.
    """
    response.headers["Access-Control-Allow-Origin"] = "*"


def build_location_router(get_deps: Callable[[], LocationDeps]) -> APIRouter:
    router = APIRouter(dependencies=[Depends(add_cors_headers)])

    def deps_dependency() -> LocationDeps:
        return get_deps()

    # CORE-19. These two used to be `/places` and `/world/state`, the same
    # paths `agents_api` declares. Both routers are mounted on the embedded
    # host, FastAPI serves whichever was included first - agents_api - and
    # nothing anywhere said so: this FastAPI keeps an included router as one
    # opaque entry in `app.routes`, so even a route dump showed no clash.
    #
    # The richer views here (map geometry, clock, government, ledger) were
    # therefore dead on the only host any run uses, and `run_report.py`
    # depends on `run_id` coming from the agents version - so a reordered
    # include would have moved the report's run identity without anything
    # failing. Renamed rather than merged: every existing caller keeps the
    # router that already answers it, and these become reachable at last.
    @router.get("/world/places", response_model=list[PlaceSummaryView])
    async def list_places(deps: LocationDeps = Depends(deps_dependency)) -> list[PlaceSummaryView]:
        records = build_place_records(deps.world, deps.map_service)
        views = [build_summary_view(deps.world, r) for r in records.values()]
        views.sort(key=lambda v: v.location_id)
        return views

    @router.get("/places/{location_id:path}", response_model=PlaceDetailView)
    async def get_place(location_id: str, deps: LocationDeps = Depends(deps_dependency)) -> PlaceDetailView:
        records = build_place_records(deps.world, deps.map_service)
        alias_index = build_alias_index(records)
        canonical = alias_index.get(location_id)
        if canonical is None:
            raise HTTPException(status_code=404, detail=f"no place {location_id!r}")
        return build_detail_view(deps.world, deps.registry, records[canonical])

    @router.get("/world/summary", response_model=WorldStateView)
    async def world_state(deps: LocationDeps = Depends(deps_dependency)) -> WorldStateView:
        return await build_world_state_view(deps.world, deps.genesis_population, deps.control_business_count)

    return router
