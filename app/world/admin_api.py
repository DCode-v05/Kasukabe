"""The President/God operator console's admin contract (DESIGN.md section
8, STATUS.md risk #2 "console-contract" task).

game/console/world_client.py calls ~25 routes under `{world_base_url}/admin`
(game/console/README.md's "Open integration point" section documents the
exact list). Before this module, neither world/main.py (which served
`/president/land-petitions`, `/god/prayers`, no `/admin` prefix, no spend/
control/alerts routes at all) nor world/run_genesis.py's embedded world
app (health + tool registry + ledger only) served ANY of it - zero path
overlap, so the operator console could not steer a live run.

This module is the ONE implementation of that contract, mounted by BOTH
hosts (world/main.py's standalone WorldServer-backed process and
run_genesis.py's embedded, in-process app - the one a live genesis run
actually boots) so there is exactly one place these routes' behavior
lives, not two copies that can drift. It is deliberately written against
the loosest interface that is still real: a bare `world.tools.state.
WorldState` (+ its own `event_bus`/`ledger`/`charter`/`prayer_requests`/
`rng`/`agents`/`locations`/`resource_zones`) plus a `TickEngine` (for
pacing/checkpoints) plus an optional `MapService`. GROW-06: it is now
present on BOTH hosts - `GenesisRunner.build_the_map()` constructs one at
boot exactly as `build_world_server` always did - and stays optional only
because `smallville/` is gitignored, so a checkout without the assets must
still boot a town that talks, trades and governs. See
approve_land_petition below for how that case is handled honestly rather
than faked. Both hosts already hold every one of those
pieces; wrapping WorldServer specifically would have made this module
unusable from run_genesis.py, which never constructs one.

Two design decisions worth calling out:

- Land petitions, amendments, prayers, and baby blessings reuse REAL,
  already-wired primitives one layer down (world.charter.land_petitions,
  world.charter.amendments, world.prayer_requests, world.life.birth) -
  the same primitives world/wiring.py's WorldServer wraps in its own
  president_*/god_* methods. This module cannot import WorldServer's
  methods directly (run_genesis.py's embedded app has no WorldServer
  instance, only the bare WorldState + TickEngine GenesisRunner already
  owns), so it is a second, equally-thin shell directly over the same
  real charter/life calls - not a second copy of business logic, a
  second caller of the same one.
- Broadcasts, honors, miracles, misfortunes, and disasters are, per
  game/console/README.md's own words, "new world state, no owning module
  yet." Rather than inventing a parallel mutable store for each (a second
  source of truth that can drift from the real causal record DESIGN.md
  section 2 promises), every one of these is a pure projection of the
  world event bus: POST emits one real WorldEvent carrying every field
  the console's view model needs, GET filters `event_bus.recent()` for
  that event type and reconstructs the view from the payload. This is
  the same pattern the alerts feed uses (game/console/README.md: "reads
  world.core.events.EventBus.recent() filtered to these categories") -
  one mechanism, not five bespoke ones. President/God actions on a named
  agent additionally set `target_agent_id` (world/core/perception.py's
  documented interrupt-policy key) so the affected agent's perception
  genuinely surfaces it; `broadcast_address` sets `involved_agent_ids` to
  every living agent for the same reason, town-wide.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from world.charter.errors import CharterError
from world.core.clock import timestamp_for
from world.core.engine import TickEngine
from world.core.pacing import PacingMode
from world.crime.errors import CrimeError
from world.ledger.errors import LedgerError
from world.ledger.models import AccountKind
from world.ledger.money import BURNED_ACCOUNT_ID, TOTAL_USD_CENTS, TREASURY_ACCOUNT_ID
from world.life import birth as life_birth
from world.life import health as life_health
from world.life.models import AilmentKind, BirthRequest, BirthRequestStatus, PopulationDials
from world.map.service import MapService
from world.map.tiled_json import Edge
from world.tools.economy_bridge import LedgerEconomyPort, life_event_to_world_event
from world.tools.errors import ToolError
from world.tools.models import ResourceZoneKind
from world.tools.state import WorldState

logger = logging.getLogger(__name__)

ADMIN_PREFIX = "/admin"


class DisasterType(str, Enum):
    CROP_BLIGHT = "crop_blight"
    STORM = "storm"
    QUAKE = "quake"
    PLAGUE = "plague"


class DisasterScopeType(str, Enum):
    TOWN = "town"
    DISTRICT = "district"
    ZONE = "zone"


class AlertCategory(str, Enum):
    CRIME_SPIKE = "crime_spike"
    CULT_CENTRALIZATION = "cult_centralization"
    AMENDMENT_TABLED = "amendment_tabled"
    REGIME_CHANGE = "regime_change"


LandEdge = Literal["south", "east"]


class LandPetitionView(BaseModel):
    petition_id: str
    filed_by_agent_id: str
    title: str
    description: str
    tile_count_requested: int
    filed_tick: int
    status: Literal["pending", "approved", "denied"]
    decided_tick: int | None = None
    decision_note: str | None = None


class ApprovePetitionRequest(BaseModel):
    decision_note: str = ""
    edge: LandEdge
    depth: int = Field(gt=0)
    new_sector_name: str | None = None
    arena_names: list[str] | None = None
    purpose: str | None = None


class DenyPetitionRequest(BaseModel):
    decision_note: str = ""


class BuildingSiteView(BaseModel):
    """One building, under way or finished.

    GROW-25. `world.buildings` was served nowhere: `agents_api.py:572`
    reports `len(world.buildings)` and that was the whole of it. The
    operator console could see land petitions and charter amendments and
    nothing else on the growth path - no tenders, no permits, no licences,
    no sites, no finished buildings. DESIGN section 1's "the town
    physically grows" was a thing only the map could show, and only by
    looking at it.
    """

    building_id: str
    purpose: str
    status: str
    builder_agent_id: str
    plot_id: str
    started_tick: int
    completion_tick: int
    labour_ticks_required: int
    labour_ticks_done: int
    labour_remaining: int
    rect: dict[str, int]


class PublicWorksView(BaseModel):
    """Everything the town is building, and everything it is allowed to.

    GROW-25's "Public works panel", assembled from what the world already
    knew and never showed together.
    """

    sites: list[BuildingSiteView]
    completed: list[BuildingSiteView]
    tenders: list[dict[str, Any]]
    permits_and_licenses: list[dict[str, Any]]
    expanded_strips: list[tuple[int, int, int, int]]


class LandExpansionSummary(BaseModel):
    edge: LandEdge
    depth: int
    previous_width: int
    previous_height: int
    new_width: int
    new_height: int
    new_sector_ids: list[int] = Field(default_factory=list)
    new_sector_names: list[str] = Field(default_factory=list)


class PetitionDecisionView(BaseModel):
    petition: LandPetitionView
    expansion: LandExpansionSummary | None = None


class AmendmentProposalView(BaseModel):
    proposal_id: str
    field_name: str
    new_value: object
    title: str
    rationale: str
    proposed_by_agent_id: str
    proposed_tick: int
    votes: dict[str, bool] = Field(default_factory=dict)
    status: Literal["proposed", "passed_awaiting_president", "enacted", "vetoed", "rejected"]
    resulting_charter_version: int | None = None
    enacted_tick: int | None = None
    vetoed_tick: int | None = None
    veto_note: str | None = None


class VetoAmendmentRequest(BaseModel):
    veto_note: str = ""


class BroadcastRequest(BaseModel):
    message: str = Field(min_length=1)


class BroadcastView(BaseModel):
    broadcast_id: str
    message: str
    issued_tick: int


class HonorRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    citation: str = ""


class HonorView(BaseModel):
    honor_id: str
    agent_id: str
    title: str
    citation: str
    granted_tick: int


class PrayerView(BaseModel):
    prayer_id: str
    agent_id: str
    content: str
    offered_tick: int


class GrantPrayerRequest(BaseModel):
    response_note: str = ""


class PrayerDecisionView(BaseModel):
    prayer_id: str
    decision: Literal["granted", "silently_denied"]
    visible_to_agent: bool
    decided_tick: int

    @model_validator(mode="after")
    def check_consistent(self) -> "PrayerDecisionView":
        expected_visible = self.decision == "granted"
        if self.visible_to_agent != expected_visible:
            raise ValueError(
                f"visible_to_agent={self.visible_to_agent} is inconsistent with "
                f"decision={self.decision!r}"
            )
        return self


class BirthRequestView(BaseModel):
    request_id: str
    household_id: str
    parent_ids: list[str]
    sponsor_parent_id: str
    requested_at_hours: float
    status: Literal["pending", "granted", "denied"]
    silent_deny: bool = False
    newborn_agent_id: str | None = None


class DenyBlessingRequest(BaseModel):
    silent: bool = False


class BirthDecisionView(BaseModel):
    request: BirthRequestView


class DisasterScope(BaseModel):
    scope_type: DisasterScopeType
    target: str | None = None

    @model_validator(mode="after")
    def check_target_required(self) -> "DisasterScope":
        if self.scope_type is not DisasterScopeType.TOWN and not self.target:
            raise ValueError(f"scope_type={self.scope_type.value!r} requires a target")
        return self


class DisasterRequest(BaseModel):
    disaster_type: DisasterType
    scope: DisasterScope
    magnitude: float = Field(default=1.0, gt=0.0)
    note: str = ""


class DisasterView(BaseModel):
    disaster_id: str
    disaster_type: DisasterType
    scope: DisasterScope
    magnitude: float
    note: str
    triggered_tick: int


class MiracleRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class MiracleView(BaseModel):
    miracle_id: str
    agent_id: str
    description: str
    cast_tick: int


class MisfortuneRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class MisfortuneView(BaseModel):
    misfortune_id: str
    agent_id: str
    description: str
    cast_tick: int


class PopulationDialsView(BaseModel):
    target_band_min: int
    target_band_max: int
    death_hazard_multiplier: float
    blessing_throttle: float


class PopulationDialsUpdate(BaseModel):
    target_band_min: int | None = None
    target_band_max: int | None = None
    death_hazard_multiplier: float | None = Field(default=None, ge=0.0)
    blessing_throttle: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def check_band_order(self) -> "PopulationDialsUpdate":
        if (
            self.target_band_min is not None
            and self.target_band_max is not None
            and self.target_band_min > self.target_band_max
        ):
            raise ValueError(
                f"target_band_min={self.target_band_min} must be <= "
                f"target_band_max={self.target_band_max}"
            )
        return self


class SpendStatusView(BaseModel):
    burned_usd_cents: int = Field(ge=0)
    treasury_usd_cents: int = Field(ge=0)
    wallets_usd_cents: int = Field(ge=0)
    budget_total_usd_cents: int = Field(gt=0)
    run_cap_usd_cents: int | None = None
    tick_index: int
    sampled_at: str

    @model_validator(mode="after")
    def check_ledger_invariant(self) -> "SpendStatusView":
        total = self.burned_usd_cents + self.treasury_usd_cents + self.wallets_usd_cents
        if total != self.budget_total_usd_cents:
            raise ValueError(
                f"spend pools sum to {total} cents, not "
                f"budget_total_usd_cents={self.budget_total_usd_cents}"
            )
        return self


class AlertView(BaseModel):
    alert_id: str
    category: AlertCategory
    summary: str
    raised_tick: int
    payload: dict[str, object] = Field(default_factory=dict)


class SimControlStatus(BaseModel):
    paused: bool
    pacing_mode: Literal["continuous", "burst"]
    continuous_interval_s: float
    tick_index: int
    game_day: int


class TickSpeedRequest(BaseModel):
    pacing_mode: Literal["continuous", "burst"]
    continuous_interval_s: float = Field(default=0.0, ge=0.0)


class CheckpointSummary(BaseModel):
    tick_index: int
    game_day: int
    written_at: str


EVENT_BROADCAST_ISSUED = "politics.broadcast.issued"
EVENT_HONOR_GRANTED = "politics.honor.granted"
EVENT_MIRACLE_CAST = "religion.miracle.cast"
EVENT_MISFORTUNE_CAST = "religion.misfortune.cast"
EVENT_DISASTER_RECORDED = "world.disaster.recorded"
EVENT_SIM_PAUSED = "system.sim.paused"
EVENT_SIM_RESUMED = "system.sim.resumed"

CRIME_SPIKE_THRESHOLD = 5

CRIME_SPIKE_WINDOW_TICKS = 200

MAX_RAISED_ALERTS = 500
CULT_MEMBER_FRACTION_THRESHOLD = 0.2
CULT_MEMBER_MIN_COUNT = 10


class SimControl:
    """Shared between this router and whichever tick loop the host actually
    runs. `wait_if_paused()` blocks until resumed - GenesisRunner.run_tick
    awaits it before doing anything else (world/run_genesis.py), so no new
    tick starts, no due agent is dispatched (hence no new broker call -
    "broker holds dispatch" falls out for free, nothing upstream of it
    changed), and the game clock (derived from world.current_tick, which
    only advances inside a tick) stays frozen exactly at the boundary
    pause was requested at. world/main.py's standalone server has no
    continuous loop of its own (ticks are single-stepped by an external
    driver via POST /admin/tick); there, `paused` instead gates that one
    route directly (see main.py) - same guarantee, different mechanism,
    because there is no wall-clock loop to block there.
    """

    def __init__(self) -> None:
        self.paused = False
        self._resume_event = asyncio.Event()
        self._resume_event.set()

    def pause(self) -> None:
        self.paused = True
        self._resume_event.clear()

    def resume(self) -> None:
        self.paused = False
        self._resume_event.set()

    async def wait_if_paused(self) -> None:
        await self._resume_event.wait()


def _record_the_new_ground(world, expansion) -> int:
    """Write the town's new tiles into `tile_owners`, and seed whatever
    the strip was grown FOR.

    GROW-13. `approve_land_petition` carved a sector and stopped. It never
    recorded who held the ground and never seeded a resource zone, so a
    strip petitioned as farmland grew nothing and a strip petitioned at
    all was indistinguishable from ground that had simply always been
    there.

    Ownership first. `require_tile` already reads an unrecorded tile as
    government-owned, so this changes no answer to "who owns this" - and
    that is exactly why it mattered: "nobody has said" and "the government
    holds it" were the same state. `from_expansion` is the field that
    separates them, section 31.2 scopes `auction_land` to "newly
    petitioned/expanded land", and until now nothing ever set it.

    Best effort by construction: a strip that is recorded but unzoned is a
    worse outcome than a raise here would be, because the map has already
    grown by the time this runs and there is no putting it back.
    """
    from world.tools.models import OwnerClass, TileOwnership
    from world.tools.primitives.property import GOVERNMENT_OWNER_ID
    from world.tools.state import tile_key

    x0, y0, x1, y1 = expansion.strip_rect
    tick = world.tick()
    recorded = 0
    for x in range(x0, x1):
        for y in range(y0, y1):
            key = tile_key(x, y)
            if key in world.tile_owners:
                continue
            world.tile_owners[key] = TileOwnership(
                tile_x=x, tile_y=y,
                owner_id=GOVERNMENT_OWNER_ID,
                owner_class=OwnerClass.GOVERNMENT,
                acquired_at_tick=tick,
                from_expansion=True,
            )
            recorded += 1

    if expansion.purpose:
        _seed_the_strips_purpose(world, expansion)
    return recorded


def _seed_the_strips_purpose(world, expansion) -> None:
    """GROW-13: a strip grown for farmland has farmland on it.

    `seed_resource_zones` runs once, at boot (`bootstrap.py`), so land the
    town grew afterwards had no zone and nothing could be harvested,
    mined or felled on it. A petition that says "we need farmland" and
    produces ground that grows nothing is the pipeline reporting success
    and delivering nothing.

    Only the three extractive purposes seed a zone; `residential`,
    `civic` and `industrial` are places to build, and building is
    `build_structure`'s job.
    """
    from world.tools.models import ResourceZone, ResourceZoneKind
    from world.tools.primitives.resources import (
        DEFAULT_ZONE_REGEN_PER_TICK,
        DEFAULT_ZONE_YIELD,
        FOREST_RESOURCE,
    )

    purpose = (expansion.purpose or "").strip().lower()
    recipes = {
        "farmland": (ResourceZoneKind.FARMLAND, "wheat", DEFAULT_ZONE_YIELD),
        "ore": (ResourceZoneKind.ORE_OUTCROP, "iron_ore", DEFAULT_ZONE_YIELD),
        "forest": (ResourceZoneKind.FOREST, FOREST_RESOURCE, None),
    }
    if purpose not in recipes:
        return
    kind, item, yield_ = recipes[purpose]
    for name in expansion.new_sector_names or []:
        if name in world.resource_zones:
            continue
        world.resource_zones[name] = ResourceZone(
            location_id=name,
            kind=kind,
            resource_item=item,
            remaining_yield=yield_,
            regen_per_tick=DEFAULT_ZONE_REGEN_PER_TICK if yield_ is not None else 0,
        )
        logger.info(
            "GROW-13: seeded a %s zone on the new %s strip at %s",
            kind.value, purpose, name,
        )


def resolve_operator_token() -> tuple[str, bool]:
    """Reads OPERATOR_TOKEN from the environment - the ONE token both
    world/main.py's standalone process and run_genesis.py's embedded
    world app resolve independently, matching game/console/config.py's
    own ConsoleSettings.operator_token reasoning: no hardcoded default (a
    well-known "safe" secret is not safe), but never refuse to boot for
    lack of one either - a fresh per-process token is generated and
    logged instead, so a first `make run-world` or a mock genesis run
    with no configuration still boots gated behind a real, unguessable
    secret rather than wide open. Returns (token, was_generated) so each
    caller can log a warning exactly when it matters, the same way
    game/console/app.py's create_app() does."""
    token = os.environ.get("OPERATOR_TOKEN")
    if token:
        return token, False
    return secrets.token_urlsafe(32), True


@dataclass
class AdminExtras:
    """Per-process state this router owns outright: the operator token
    (see auth_dependency in build_admin_router below), the pause gate, and the baby
    blessing queue (world.life.birth's request/grant/deny lifecycle needs
    a real mutable `status` per request - unlike broadcasts/honors/
    miracles/misfortunes/disasters, this is genuinely not reconstructible
    from an event-log projection, see module docstring)."""

    operator_token: str
    birth_requests: dict[str, BirthRequest] = field(default_factory=dict)
    sim_control: SimControl = field(default_factory=SimControl)


@dataclass
class AdminDeps:
    """Everything one request handler below needs, gathered by whichever
    host (world/main.py or run_genesis.py's embedded app) constructs one
    of these per process and hands a zero-arg getter to build_admin_router.
    `map_service` is now wired on BOTH hosts (GROW-06 -
    `GenesisRunner.build_the_map`); it is None only when the gitignored
    Ville assets are absent from the machine - see approve_land_petition's
    handling of that case below.
    `get_population_dials`/`set_population_dials` are closures rather than
    a plain field so each host can point them at whatever object its OWN
    tick loop actually reads for aging/health math (world/main.py's
    WorldServer.population_dials, GenesisRunner.population_dials) - a
    dials store this router owned itself would silently stop affecting
    the real simulation, exactly the "wired to nothing" bug this task
    exists to close.
    """

    world: WorldState
    tick_engine: TickEngine
    map_service: MapService | None
    extras: AdminExtras
    get_population_dials: Callable[[], PopulationDials]
    set_population_dials: Callable[[dict[str, Any]], PopulationDials]
    get_run_cap_usd_cents: "Callable[[], int | None] | None" = None


def extract_operator_token(authorization: str | None, x_operator_token: str | None) -> str | None:
    if authorization:
        stripped = authorization.strip()
        token = stripped[7:].strip() if stripped.lower().startswith("bearer ") else stripped
        if token:
            return token
    if x_operator_token and x_operator_token.strip():
        return x_operator_token.strip()
    return None


class OperatorAuthError(Exception):
    """Missing or wrong operator token on an /admin/* route."""


def check_operator_token(provided: str | None, expected: str) -> str:
    """Timing-safe token check used by build_admin_router's auth_dependency
    below - the one place every /admin/* route (mounted identically by
    both world/main.py and run_genesis.py's embedded world app) funnels
    through, matching game/console/auth.py's tokens_match() sibling
    helper."""
    if provided is None or not hmac.compare_digest(provided, expected):
        raise OperatorAuthError(
            "no valid operator token - send 'Authorization: Bearer <token>' or "
            "'X-Operator-Token: <token>' matching this process's OPERATOR_TOKEN"
        )
    return provided


def install_admin_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(OperatorAuthError)
    async def handle_operator_auth_error(request: Any, exc: OperatorAuthError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc)})

    @app.exception_handler(ToolError)
    async def handle_tool_error(request: Any, exc: ToolError) -> JSONResponse:
        body: dict[str, Any] = {"detail": str(exc), "kind": type(exc).__name__}
        if getattr(exc, "context", None):
            body["context"] = exc.context
        return JSONResponse(status_code=400, content=body)

    @app.exception_handler(LedgerError)
    async def handle_ledger_error(request: Any, exc: LedgerError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc), "kind": type(exc).__name__})

    @app.exception_handler(CharterError)
    async def handle_charter_error(request: Any, exc: CharterError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc), "kind": type(exc).__name__})

    @app.exception_handler(CrimeError)
    async def handle_crime_error(request: Any, exc: CrimeError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc), "kind": type(exc).__name__})

    @app.exception_handler(ValueError)
    async def handle_value_error(request: Any, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc), "kind": "ValueError"})


def build_control_status(world: WorldState, tick_engine: TickEngine, sim_control: SimControl) -> SimControlStatus:
    ts = timestamp_for(world.current_tick)
    return SimControlStatus(
        paused=sim_control.paused,
        pacing_mode=tick_engine.pacing.config.mode.value,
        continuous_interval_s=tick_engine.pacing.config.continuous_interval_s,
        tick_index=world.current_tick,
        game_day=ts.game_day,
    )


def build_admin_router(get_deps: Callable[[], AdminDeps]) -> APIRouter:
    """Constructs the full /admin/* router (game/console/README.md's exact
    route list) against `get_deps` - a zero-arg callable each host
    supplies, returning that process's AdminDeps. Mounted by both
    world/main.py and run_genesis.py's build_embedded_world_app.
    """

    router = APIRouter(prefix=ADMIN_PREFIX)

    def deps_dependency() -> AdminDeps:
        return get_deps()

    def auth_dependency(
        deps: AdminDeps = Depends(deps_dependency),
        authorization: str | None = Header(default=None),
        x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
    ) -> str:
        """The actual FastAPI-managed dependency every route below depends
        on. Unlike an earlier version of this function, `authorization`/
        `x_operator_token` are declared directly on this function's own
        signature (not on a nested closure built by a separate factory and
        then called by hand) - FastAPI only resolves `Header(...)` defaults
        into real header values for a callable it invokes itself via
        `Depends()`; calling a Header-decorated function directly, with no
        arguments, leaves those parameters bound to the unresolved
        `Header(...)` sentinel objects instead of strings, which is exactly
        the shape of bug this module's own integration test
        (tests/test_console_world_integration.py) caught for real against a
        live server."""
        token = extract_operator_token(authorization, x_operator_token)
        return check_operator_token(token, deps.extras.operator_token)


    @router.get("/president/public-works", response_model=PublicWorksView)
    async def public_works(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> PublicWorksView:
        """GROW-25: the growth path, in one place.

        The console could approve a land petition and then had no way to
        see what the town did with the ground. Tenders and licences were
        already served read-only at `/governance/active-policies` and the
        Town tab never rendered them; buildings were served nowhere at
        all.
        """
        from world.map.expansion import footprint_rect
        from world.tools.models import Building, BuildingStatus

        sites: list[BuildingSiteView] = []
        completed: list[BuildingSiteView] = []
        for building in deps.world.buildings.values():
            if not isinstance(building, Building):
                continue
            row = BuildingSiteView(
                building_id=building.building_id,
                purpose=building.purpose,
                status=building.status.value,
                builder_agent_id=building.builder_agent_id,
                plot_id=building.plot_id,
                started_tick=building.started_tick,
                completion_tick=building.completion_tick,
                labour_ticks_required=building.labour_ticks_required,
                labour_ticks_done=building.labour_ticks_done,
                labour_remaining=building.labour_remaining,
                rect=footprint_rect(building.footprint),
            )
            if building.status is BuildingStatus.COMPLETE:
                completed.append(row)
            else:
                sites.append(row)

        store = deps.world.charter.store
        tenders = [t.model_dump(mode="json") for t in await store.list_tenders()]
        licenses = [
            lic.model_dump(mode="json") for lic in await store.list_licenses()
        ]
        return PublicWorksView(
            sites=sites,
            completed=completed,
            tenders=tenders,
            permits_and_licenses=licenses,
            expanded_strips=[tuple(r) for r in deps.world.expanded_rects],
        )

    @router.get("/president/petitions", response_model=list[LandPetitionView])
    async def list_petitions(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> list[LandPetitionView]:
        petitions = await deps.world.charter.land_petitions.list_pending()
        return [LandPetitionView.model_validate(p.model_dump(mode="json")) for p in petitions]

    @router.post("/president/petitions/{petition_id}/approve", response_model=PetitionDecisionView)
    async def approve_petition(
        petition_id: str,
        req: ApprovePetitionRequest,
        deps: AdminDeps = Depends(deps_dependency),
        token: str = Depends(auth_dependency),
    ) -> PetitionDecisionView:
        petition = await deps.world.charter.land_petitions.approve_petition(
            petition_id, req.decision_note, deps.world.tick()
        )
        expansion_view: LandExpansionSummary | None = None
        if deps.map_service is not None:
            expansion = await deps.map_service.expand_land(
                edge=Edge(req.edge),
                depth=req.depth,
                tick=deps.world.tick(),
                new_sector_name=req.new_sector_name,
                arena_names=req.arena_names,
                purpose=req.purpose,
            )
            deps.world.expanded_rects.append(tuple(expansion.strip_rect))
            _record_the_new_ground(deps.world, expansion)
            expansion_view = LandExpansionSummary.model_validate(expansion.model_dump(mode="json"))
        else:
            logger.warning(
                "land petition %s approved with no MapService wired - decision recorded, "
                "no physical map growth applied (no Ville assets on this host)",
                petition_id,
            )
        return PetitionDecisionView(
            petition=LandPetitionView.model_validate(petition.model_dump(mode="json")),
            expansion=expansion_view,
        )

    @router.post("/president/petitions/{petition_id}/deny", response_model=LandPetitionView)
    async def deny_petition(
        petition_id: str,
        req: DenyPetitionRequest,
        deps: AdminDeps = Depends(deps_dependency),
        token: str = Depends(auth_dependency),
    ) -> LandPetitionView:
        petition = await deps.world.charter.land_petitions.deny_petition(
            petition_id, req.decision_note, deps.world.tick()
        )
        return LandPetitionView.model_validate(petition.model_dump(mode="json"))


    @router.get("/president/amendments", response_model=list[AmendmentProposalView])
    async def list_amendments(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> list[AmendmentProposalView]:
        return [
            AmendmentProposalView.model_validate(p.model_dump(mode="json"))
            for p in deps.world.charter.amendments.list_pending()
        ]

    @router.post("/president/amendments/{proposal_id}/sign", response_model=AmendmentProposalView)
    async def sign_amendment(
        proposal_id: str, deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> AmendmentProposalView:
        proposal = await deps.world.charter.amendments.sign_amendment(proposal_id, deps.world.tick())
        return AmendmentProposalView.model_validate(proposal.model_dump(mode="json"))

    @router.post("/president/amendments/{proposal_id}/veto", response_model=AmendmentProposalView)
    async def veto_amendment(
        proposal_id: str,
        req: VetoAmendmentRequest,
        deps: AdminDeps = Depends(deps_dependency),
        token: str = Depends(auth_dependency),
    ) -> AmendmentProposalView:
        proposal = await deps.world.charter.amendments.veto_amendment(
            proposal_id, req.veto_note, deps.world.tick()
        )
        return AmendmentProposalView.model_validate(proposal.model_dump(mode="json"))


    @router.post("/president/broadcast", response_model=BroadcastView)
    async def broadcast_address(
        req: BroadcastRequest, deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> BroadcastView:
        broadcast_id = f"broadcast-{uuid.uuid4().hex[:12]}"
        tick = deps.world.tick()
        living_ids = sorted(a.agent_id for a in deps.world.agents.values() if a.alive)
        await deps.world.event_bus.emit(
            EVENT_BROADCAST_ISSUED,
            tick=tick,
            payload={
                "broadcast_id": broadcast_id,
                "message": req.message,
                "issued_tick": tick,
                "involved_agent_ids": living_ids,
            },
        )
        return BroadcastView(broadcast_id=broadcast_id, message=req.message, issued_tick=tick)

    @router.get("/president/broadcasts", response_model=list[BroadcastView])
    async def list_broadcasts(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> list[BroadcastView]:
        events = [e for e in deps.world.event_bus.recent() if e.type == EVENT_BROADCAST_ISSUED]
        return [
            BroadcastView(
                broadcast_id=e.payload["broadcast_id"],
                message=e.payload["message"],
                issued_tick=e.payload["issued_tick"],
            )
            for e in events
        ]


    @router.post("/president/honors", response_model=HonorView)
    async def grant_honor(
        req: HonorRequest, deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> HonorView:
        if req.agent_id not in deps.world.agents:
            raise ValueError(f"no agent {req.agent_id!r}")
        honor_id = f"honor-{uuid.uuid4().hex[:12]}"
        tick = deps.world.tick()
        await deps.world.event_bus.emit(
            EVENT_HONOR_GRANTED,
            tick=tick,
            payload={
                "honor_id": honor_id,
                "agent_id": req.agent_id,
                "title": req.title,
                "citation": req.citation,
                "granted_tick": tick,
                "target_agent_id": req.agent_id,
            },
        )
        return HonorView(honor_id=honor_id, agent_id=req.agent_id, title=req.title, citation=req.citation, granted_tick=tick)

    @router.get("/president/honors", response_model=list[HonorView])
    async def list_honors(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> list[HonorView]:
        events = [e for e in deps.world.event_bus.recent() if e.type == EVENT_HONOR_GRANTED]
        return [
            HonorView(
                honor_id=e.payload["honor_id"],
                agent_id=e.payload["agent_id"],
                title=e.payload["title"],
                citation=e.payload["citation"],
                granted_tick=e.payload["granted_tick"],
            )
            for e in events
        ]


    @router.get("/god/prayers", response_model=list[PrayerView])
    async def list_prayers(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> list[PrayerView]:
        return [
            PrayerView(prayer_id=p.request_id, agent_id=p.agent_id, content=p.message, offered_tick=p.tick)
            for p in deps.world.prayer_requests
        ]

    @router.post("/god/prayers/{prayer_id}/grant", response_model=PrayerDecisionView)
    async def grant_prayer(
        prayer_id: str,
        req: GrantPrayerRequest,
        deps: AdminDeps = Depends(deps_dependency),
        token: str = Depends(auth_dependency),
    ) -> PrayerDecisionView:
        prayer = next((p for p in deps.world.prayer_requests if p.request_id == prayer_id), None)
        if prayer is None:
            raise ValueError(f"no prayer request {prayer_id!r}")
        tick = deps.world.tick()
        await deps.world.event_bus.emit(
            "religion.prayer.granted",
            tick=tick,
            payload={
                "request_id": prayer_id,
                "target_agent_id": prayer.agent_id,
                "response_note": req.response_note,
            },
        )
        return PrayerDecisionView(prayer_id=prayer_id, decision="granted", visible_to_agent=True, decided_tick=tick)

    @router.post("/god/prayers/{prayer_id}/silent-deny", response_model=PrayerDecisionView)
    async def silent_deny_prayer(
        prayer_id: str, deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> PrayerDecisionView:
        prayer = next((p for p in deps.world.prayer_requests if p.request_id == prayer_id), None)
        if prayer is None:
            raise ValueError(f"no prayer request {prayer_id!r}")
        tick = deps.world.tick()
        await deps.world.event_bus.emit("religion.prayer.denied", tick=tick, payload={"request_id": prayer_id})
        return PrayerDecisionView(
            prayer_id=prayer_id, decision="silently_denied", visible_to_agent=False, decided_tick=tick
        )


    @router.get("/god/blessings", response_model=list[BirthRequestView])
    async def list_blessings(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> list[BirthRequestView]:
        return [
            BirthRequestView.model_validate(r.model_dump(mode="json"))
            for r in deps.extras.birth_requests.values()
            if r.status is BirthRequestStatus.PENDING
        ]

    @router.post("/god/blessings/{request_id}/grant", response_model=BirthDecisionView)
    async def grant_blessing(
        request_id: str, deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> BirthDecisionView:
        request = deps.extras.birth_requests.get(request_id)
        if request is None:
            raise ValueError(f"no birth request {request_id!r}")
        household = deps.world.households.get(request.household_id)
        if household is None:
            raise ValueError(
                f"birth request {request_id!r} names unknown household {request.household_id!r}"
            )
        parents = [deps.world.require_agent(pid) for pid in request.parent_ids]
        economy = LedgerEconomyPort(deps.world.ledger, deps.world.tick, deps.world)
        newborn_id = f"a{uuid.uuid4().hex[:8]}"
        outcome = await life_birth.grant_birth_request(
            request,
            newborn_id,
            household,
            parents,
            sum(1 for a in deps.world.agents.values() if a.alive),
            deps.get_population_dials(),
            economy,
            deps.world.rng,
            deps.world.game_clock(),
        )
        deps.extras.birth_requests[request_id] = outcome.request
        deps.world.households[household.household_id] = outcome.household
        for parent in outcome.parents:
            deps.world.agents[parent.agent_id] = parent
        deps.world.agents[newborn_id] = outcome.newborn
        deps.world.ledger.open_agent(newborn_id, starting_usd_cents=0, starting_sim=0)
        deps.tick_engine.register_agent(newborn_id, f"home:{household.household_id}")
        for life_event in outcome.events:
            await deps.world.event_bus.publish(life_event_to_world_event(life_event, deps.world.tick()))
        return BirthDecisionView(request=BirthRequestView.model_validate(outcome.request.model_dump(mode="json")))

    @router.post("/god/blessings/{request_id}/deny", response_model=BirthDecisionView)
    async def deny_blessing(
        request_id: str,
        req: DenyBlessingRequest,
        deps: AdminDeps = Depends(deps_dependency),
        token: str = Depends(auth_dependency),
    ) -> BirthDecisionView:
        request = deps.extras.birth_requests.get(request_id)
        if request is None:
            raise ValueError(f"no birth request {request_id!r}")
        updated, events = life_birth.deny_birth_request(request, deps.world.game_clock(), silent=req.silent)
        deps.extras.birth_requests[request_id] = updated
        for life_event in events:
            await deps.world.event_bus.publish(life_event_to_world_event(life_event, deps.world.tick()))
        return BirthDecisionView(request=BirthRequestView.model_validate(updated.model_dump(mode="json")))


    @router.post("/god/disasters", response_model=DisasterView)
    async def trigger_disaster(
        req: DisasterRequest, deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> DisasterView:
        disaster_id = f"disaster-{uuid.uuid4().hex[:12]}"
        tick = deps.world.tick()
        fraction = min(max(req.magnitude, 0.0), 1.0)

        if req.disaster_type is DisasterType.PLAGUE:
            living = [a for a in deps.world.agents.values() if a.alive and a.health.current_ailment is None]
            if req.scope.scope_type is DisasterScopeType.DISTRICT and req.scope.target:
                target = req.scope.target
                living = [
                    a
                    for a in living
                    if (loc := deps.world.locations.location_of(a.agent_id)) is not None
                    and (loc == target or loc.startswith(f"{target}:"))
                ]
            target_count = round(len(living) * fraction)
            chosen = deps.world.rng.sample(living, k=min(target_count, len(living)))
            day = timestamp_for(tick).game_day
            for agent in chosen:
                ailment = life_health.onset_ailment(deps.world.rng, AilmentKind.MEDIUM, day)
                deps.world.agents[agent.agent_id] = agent.model_copy(
                    update={"health": agent.health.model_copy(update={"current_ailment": ailment})}
                )
            await deps.world.event_bus.emit(
                "health.disaster.plague",
                tick=tick,
                payload={"affected_agent_ids": sorted(a.agent_id for a in chosen)},
            )
        elif req.disaster_type is DisasterType.CROP_BLIGHT:
            zones = deps.world.resource_zones
            if req.scope.scope_type is DisasterScopeType.ZONE and req.scope.target:
                zones = {k: v for k, v in zones.items() if k == req.scope.target}
            affected: list[str] = []
            for location_id, zone in zones.items():
                if zone.kind is ResourceZoneKind.FARMLAND and zone.remaining_yield is not None:
                    zone.remaining_yield = round(zone.remaining_yield * (1.0 - fraction))
                    affected.append(location_id)
            await deps.world.event_bus.emit(
                "world.disaster.crop_blight",
                tick=tick,
                payload={"affected_zones": sorted(affected), "yield_reduction_fraction": fraction},
            )
        elif req.disaster_type is DisasterType.STORM:
            await deps.world.event_bus.emit(
                "world.disaster.storm", tick=tick, payload={"magnitude": req.magnitude, "note": req.note}
            )
        elif req.disaster_type is DisasterType.QUAKE:
            await deps.world.event_bus.emit(
                "world.disaster.quake", tick=tick, payload={"magnitude": req.magnitude, "note": req.note}
            )

        await deps.world.event_bus.emit(
            EVENT_DISASTER_RECORDED,
            tick=tick,
            payload={
                "disaster_id": disaster_id,
                "disaster_type": req.disaster_type.value,
                "scope_type": req.scope.scope_type.value,
                "scope_target": req.scope.target,
                "magnitude": req.magnitude,
                "note": req.note,
                "triggered_tick": tick,
            },
        )
        return DisasterView(
            disaster_id=disaster_id,
            disaster_type=req.disaster_type,
            scope=req.scope,
            magnitude=req.magnitude,
            note=req.note,
            triggered_tick=tick,
        )

    @router.get("/god/disasters", response_model=list[DisasterView])
    async def list_disasters(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> list[DisasterView]:
        events = [e for e in deps.world.event_bus.recent() if e.type == EVENT_DISASTER_RECORDED]
        result: list[DisasterView] = []
        for e in events:
            p = e.payload
            result.append(
                DisasterView(
                    disaster_id=p["disaster_id"],
                    disaster_type=DisasterType(p["disaster_type"]),
                    scope=DisasterScope(scope_type=DisasterScopeType(p["scope_type"]), target=p.get("scope_target")),
                    magnitude=p["magnitude"],
                    note=p["note"],
                    triggered_tick=p["triggered_tick"],
                )
            )
        return result


    @router.post("/god/miracles", response_model=MiracleView)
    async def cast_miracle(
        req: MiracleRequest, deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> MiracleView:
        if req.agent_id not in deps.world.agents:
            raise ValueError(f"no agent {req.agent_id!r}")
        miracle_id = f"miracle-{uuid.uuid4().hex[:12]}"
        tick = deps.world.tick()
        await deps.world.event_bus.emit(
            EVENT_MIRACLE_CAST,
            tick=tick,
            payload={
                "miracle_id": miracle_id,
                "agent_id": req.agent_id,
                "description": req.description,
                "cast_tick": tick,
                "target_agent_id": req.agent_id,
            },
        )
        return MiracleView(miracle_id=miracle_id, agent_id=req.agent_id, description=req.description, cast_tick=tick)

    @router.get("/god/miracles", response_model=list[MiracleView])
    async def list_miracles(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> list[MiracleView]:
        events = [e for e in deps.world.event_bus.recent() if e.type == EVENT_MIRACLE_CAST]
        return [
            MiracleView(
                miracle_id=e.payload["miracle_id"],
                agent_id=e.payload["agent_id"],
                description=e.payload["description"],
                cast_tick=e.payload["cast_tick"],
            )
            for e in events
        ]

    @router.post("/god/misfortunes", response_model=MisfortuneView)
    async def cast_misfortune(
        req: MisfortuneRequest, deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> MisfortuneView:
        if req.agent_id not in deps.world.agents:
            raise ValueError(f"no agent {req.agent_id!r}")
        misfortune_id = f"misfortune-{uuid.uuid4().hex[:12]}"
        tick = deps.world.tick()
        await deps.world.event_bus.emit(
            EVENT_MISFORTUNE_CAST,
            tick=tick,
            payload={
                "misfortune_id": misfortune_id,
                "agent_id": req.agent_id,
                "description": req.description,
                "cast_tick": tick,
                "target_agent_id": req.agent_id,
            },
        )
        return MisfortuneView(
            misfortune_id=misfortune_id, agent_id=req.agent_id, description=req.description, cast_tick=tick
        )

    @router.get("/god/misfortunes", response_model=list[MisfortuneView])
    async def list_misfortunes(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> list[MisfortuneView]:
        events = [e for e in deps.world.event_bus.recent() if e.type == EVENT_MISFORTUNE_CAST]
        return [
            MisfortuneView(
                misfortune_id=e.payload["misfortune_id"],
                agent_id=e.payload["agent_id"],
                description=e.payload["description"],
                cast_tick=e.payload["cast_tick"],
            )
            for e in events
        ]


    @router.get("/god/population-dials", response_model=PopulationDialsView)
    async def get_population_dials_route(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> PopulationDialsView:
        return PopulationDialsView.model_validate(deps.get_population_dials().model_dump(mode="json"))

    @router.put("/god/population-dials", response_model=PopulationDialsView)
    async def update_population_dials_route(
        req: PopulationDialsUpdate,
        deps: AdminDeps = Depends(deps_dependency),
        token: str = Depends(auth_dependency),
    ) -> PopulationDialsView:
        updates = {k: v for k, v in req.model_dump().items() if v is not None}
        dials = deps.set_population_dials(updates)
        return PopulationDialsView.model_validate(dials.model_dump(mode="json"))


    @router.get("/spend/status", response_model=SpendStatusView)
    async def spend_status(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> SpendStatusView:
        ledger = deps.world.ledger
        wallets = sum(a.usd_cents for a in ledger.accounts.values() if a.kind is AccountKind.AGENT)
        treasury = ledger.usd_balance(TREASURY_ACCOUNT_ID)
        burned = ledger.usd_balance(BURNED_ACCOUNT_ID)
        return SpendStatusView(
            burned_usd_cents=burned,
            treasury_usd_cents=treasury,
            wallets_usd_cents=wallets,
            budget_total_usd_cents=TOTAL_USD_CENTS,
            run_cap_usd_cents=(
                deps.get_run_cap_usd_cents() if deps.get_run_cap_usd_cents else None
            ),
            tick_index=deps.world.current_tick,
            sampled_at=datetime.now(timezone.utc).isoformat(),
        )


    @router.get("/alerts", response_model=list[AlertView])
    async def list_alerts(
        limit: int = 50, deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> list[AlertView]:
        events = deps.world.event_bus.recent()
        alerts: list[AlertView] = []

        for e in events:
            if e.type == "politics.regime.changed":
                alerts.append(
                    AlertView(
                        alert_id=f"alert-{e.event_id}",
                        category=AlertCategory.REGIME_CHANGE,
                        summary="regime changed",
                        raised_tick=e.tick,
                        payload=e.payload,
                    )
                )
            elif e.type == "politics.charter.amendment_proposed":
                alerts.append(
                    AlertView(
                        alert_id=f"alert-{e.event_id}",
                        category=AlertCategory.AMENDMENT_TABLED,
                        summary=f"amendment tabled: {e.payload.get('title', '')}",
                        raised_tick=e.tick,
                        payload=e.payload,
                    )
                )

        now = deps.world.current_tick
        crime_events = [
            e
            for e in events
            if e.type == "crime.case.opened" and e.tick > now - CRIME_SPIKE_WINDOW_TICKS
        ]
        if len(crime_events) >= CRIME_SPIKE_THRESHOLD:
            last = crime_events[-1]
            alerts.append(
                AlertView(
                    alert_id=f"alert-crime-spike-{last.event_id}",
                    category=AlertCategory.CRIME_SPIKE,
                    summary=(
                        f"{len(crime_events)} crime cases opened in the last "
                        f"{CRIME_SPIKE_WINDOW_TICKS} ticks"
                    ),
                    raised_tick=last.tick,
                    payload={
                        "case_count": len(crime_events),
                        "window_ticks": CRIME_SPIKE_WINDOW_TICKS,
                    },
                )
            )

        membership: dict[str, set[str]] = {}
        for e in events:
            if e.type == "politics.organization.member_added":
                membership.setdefault(e.payload["org_id"], set()).add(e.payload["agent_id"])
            elif e.type == "politics.organization.member_removed":
                membership.get(e.payload.get("org_id", ""), set()).discard(e.payload.get("agent_id"))
        population = max(1, sum(1 for a in deps.world.agents.values() if a.alive))
        for org_id, members in membership.items():
            threshold = max(CULT_MEMBER_MIN_COUNT, round(population * CULT_MEMBER_FRACTION_THRESHOLD))
            if len(members) >= threshold:
                alerts.append(
                    AlertView(
                        alert_id=f"alert-cult-{org_id}-{deps.world.current_tick}",
                        category=AlertCategory.CULT_CENTRALIZATION,
                        summary=(
                            f"organization {org_id} has {len(members)} members "
                            f"({len(members) / population:.0%} of the living population)"
                        ),
                        raised_tick=deps.world.current_tick,
                        payload={"org_id": org_id, "member_count": len(members)},
                    )
                )

        known = {a["alert_id"]: a for a in deps.world.raised_alerts}
        for alert in alerts:
            known.setdefault(alert.alert_id, alert.model_dump(mode="json"))
        history = sorted(known.values(), key=lambda a: a["raised_tick"], reverse=True)
        deps.world.raised_alerts = history[:MAX_RAISED_ALERTS]
        return [AlertView.model_validate(a) for a in deps.world.raised_alerts[:limit]]


    @router.get("/control/status", response_model=SimControlStatus)
    async def control_status(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> SimControlStatus:
        return build_control_status(deps.world, deps.tick_engine, deps.extras.sim_control)

    @router.post("/control/pause", response_model=SimControlStatus)
    async def pause_sim(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> SimControlStatus:
        deps.extras.sim_control.pause()
        await deps.world.event_bus.emit(EVENT_SIM_PAUSED, tick=deps.world.tick(), payload={})
        return build_control_status(deps.world, deps.tick_engine, deps.extras.sim_control)

    @router.post("/control/resume", response_model=SimControlStatus)
    async def resume_sim(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> SimControlStatus:
        deps.extras.sim_control.resume()
        await deps.world.event_bus.emit(EVENT_SIM_RESUMED, tick=deps.world.tick(), payload={})
        return build_control_status(deps.world, deps.tick_engine, deps.extras.sim_control)

    @router.put("/control/tick-speed", response_model=SimControlStatus)
    async def set_tick_speed(
        req: TickSpeedRequest, deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> SimControlStatus:
        deps.tick_engine.pacing.config.mode = PacingMode(req.pacing_mode)
        deps.tick_engine.pacing.config.continuous_interval_s = req.continuous_interval_s
        return build_control_status(deps.world, deps.tick_engine, deps.extras.sim_control)

    @router.post("/control/checkpoint-now", response_model=CheckpointSummary)
    async def checkpoint_now(
        deps: AdminDeps = Depends(deps_dependency), token: str = Depends(auth_dependency)
    ) -> CheckpointSummary:
        game_day = timestamp_for(deps.world.current_tick).game_day
        checkpoint = await deps.tick_engine.checkpoints.write(deps.world.current_tick, game_day)
        return CheckpointSummary(
            tick_index=checkpoint.tick_index, game_day=checkpoint.game_day, written_at=checkpoint.written_at
        )

    return router
