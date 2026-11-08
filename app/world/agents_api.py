"""Real agent and world-state endpoints for the town view.

The game server had no source of real agent data, so game/world_client.py
silently fell back to mock_world.MockWorldClient and the operator spent a
whole run watching fabricated "mock-agent-011" rows standing in fabricated
places. This module is that missing source: every field below is read from
the live WorldState, LocationIndex, Ledger and MovementSystem, never
synthesised.

Deliberately read-only. Nothing here mutates the world; the President/God
console (world/admin_api.py) is the only write path.
"""

from __future__ import annotations

import time
import uuid

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from world.core.clock import timestamp_for
from world.life.models import Genesis
from world.tools.state import WorldState


@dataclass
class AgentsDeps:
    """Gathered by whichever host mounts this router (world/main.py and
    run_genesis.py's embedded app both do), so the routes never reach for
    a module-level singleton.
    """

    world: WorldState
    genesis: Genesis
    map_service: Any | None = None


class StepView(BaseModel):
    """One step of an authored workflow. `ref` is None on a `check` step."""

    order: int
    kind: str
    ref: str | None = None
    note: str = ""


class HeldSkillView(BaseModel):
    """One skill this agent holds, as the DOCUMENT it is.

    Part E.1. A proficiency bar says an agent is good at something; the step
    list says what they think the job IS - and two foresters at the same
    level with a different step 3 is the town's knowledge diverging, which is
    the experiment. So the steps travel with the row rather than sitting
    behind a second call.

    `completions` is `skill_reps` and `proficiency` is `skills`: for an
    authored workflow only an end-to-end walk earns a repetition, so the two
    are genuinely different numbers and both are reported.
    """

    name: str
    proficiency: int
    completions: int
    # True when this agent wrote it. A built-in trade (farming, forestry, ...)
    # has no definition at all, so `authored` is False and `steps` is empty -
    # the viewer shows a bar and no document, which is the truth.
    authored: bool = False
    author_agent_id: str | None = None
    author_name: str | None = None
    taught_by_agent_id: str | None = None
    taught_by_name: str | None = None
    steps: list[StepView] = Field(default_factory=list)
    # Index into `steps` of the step expected next, or None when this agent
    # is not currently partway through the workflow.
    next_step: int | None = None
    following: bool = False
    version: int = 1
    governmental: bool = False
    namespace: str = "public"


class FollowingView(BaseModel):
    """The workflow this agent is partway through, and how far in.

    Part E.2. `step` is 1-based for display ("step 4/7"); `next_step` on the
    held skill above stays a 0-based index into `steps` because that is what
    it indexes.
    """

    skill: str
    step: int
    total: int


class AgentRow(BaseModel):
    agent_id: str
    name: str
    age_years: float
    sex: str
    occupation: str
    household_id: str | None
    business_id: str | None
    location_id: str | None
    tile_x: int | None
    tile_y: int | None
    is_minor: bool
    alive: bool
    # Dicts, not scalars: game/world_client.py's _agent_summary_from_world
    # does `raw.get("dormancy") or {}` then `.get(...)` on the result, so a
    # bare bool/str here makes every row raise and get silently skipped -
    # which is exactly why the snapshot came back with 0 agents while
    # /agents itself returned 200 with all 250.
    dormancy: dict[str, Any]
    health: dict[str, Any]
    usd_wallet_cents: int
    sim_balance: int
    ablation_arm: str
    # Part E. Carried on the row rather than fetched separately so a
    # resident's money and the step they are on are true at the same moment -
    # two polls would disagree by a tick and nobody would know why.
    skills: list[HeldSkillView] = Field(default_factory=list)
    following: FollowingView | None = None


# Minted once, when this process imports the module - so it is constant for
# one run of the world and different for the next, which is exactly the
# property a per-run document needs.
RUN_ID = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"


class WorldStateView(BaseModel):
    tick: int
    game_day: int
    game_hour: int
    game_minute: int
    population: int
    alive: int
    dormant: int
    dead: int
    households: int
    businesses: int
    buildings: int
    crime_cases: int
    # Which BOOT of the world this is.
    #
    # Nothing else here identifies a run: tick 0 is not it (a resumed run
    # starts at 1048) and wall-clock now is not it (it changes every poll).
    # world/run_report.py uses this to decide whether it is refreshing the
    # current run's document or opening a new one for a fresh start.
    run_id: str = ""


def personas_by_id(genesis: Genesis) -> dict[str, Any]:
    return {a.agent_id: a for a in genesis.agents}


def health_label(record: Any) -> str:
    health = getattr(record, "health", None)
    ailment = getattr(health, "ailment", None) if health is not None else None
    if ailment is None:
        return "well"
    return str(getattr(ailment, "kind", ailment))


def held_skills(deps: AgentsDeps, agent_id: str) -> tuple[list[HeldSkillView], FollowingView | None]:
    """Every skill this agent holds, plus the one it is partway through.

    Built here rather than in `skills_api` because these rows ride the SAME
    response as the agent's position and money (see AgentRow.skills), which
    is the whole point: one poll, one instant.

    A built-in trade has no `SkillDefinition` - it is a proficiency the world
    always knew - so it yields a row with a level and no document. An
    authored workflow yields the steps its author wrote, with the step the
    agent is expected to take next marked.
    """
    world = deps.world
    names = personas_by_id(deps.genesis)
    levels = world.skills.get(agent_id, {})
    reps = world.skill_reps.get(agent_id, {})
    sources = world.skill_sources.get(agent_id, {})

    def display(other_id: str | None) -> str | None:
        if not other_id:
            return None
        return getattr(names.get(other_id), "name", other_id)

    rows: list[HeldSkillView] = []
    following: FollowingView | None = None
    for name, level in sorted(levels.items(), key=lambda kv: (-kv[1], kv[0])):
        definition = world.skill_definitions.get(name)
        progress = world.skill_progress.get(f"{agent_id}::{name}")
        steps = (
            [
                StepView(order=st.order, kind=st.kind, ref=st.ref, note=st.note)
                for st in sorted(definition.steps, key=lambda st: st.order)
            ]
            if definition is not None
            else []
        )
        taught_by = sources.get(name)
        rows.append(
            HeldSkillView(
                name=name,
                proficiency=int(level),
                completions=int(reps.get(name, 0)),
                authored=definition is not None,
                author_agent_id=definition.author_agent_id if definition else None,
                author_name=display(definition.author_agent_id) if definition else None,
                taught_by_agent_id=taught_by,
                taught_by_name=display(taught_by),
                steps=steps,
                next_step=progress.next_step if progress else None,
                following=progress is not None,
                version=definition.version if definition else 1,
                governmental=bool(definition.governmental) if definition else False,
                namespace=definition.namespace if definition else "public",
            )
        )
        if progress is not None and steps and following is None:
            following = FollowingView(
                skill=name,
                step=min(progress.next_step + 1, len(steps)),
                total=len(steps),
            )
    return rows, following


def build_agent_row(agent_id: str, record: Any, persona: Any, deps: AgentsDeps) -> AgentRow:
    world = deps.world
    tile = world.agent_tiles.get(agent_id)
    dormancy = getattr(record, "dormancy", None)
    skills, following = held_skills(deps, agent_id)
    return AgentRow(
        agent_id=agent_id,
        name=getattr(persona, "name", agent_id),
        age_years=getattr(record, "age_years", getattr(persona, "age_years", 0.0)),
        sex=str(getattr(record, "sex", getattr(persona, "sex", ""))),
        occupation=getattr(persona, "occupation", ""),
        household_id=getattr(record, "household_id", None),
        business_id=getattr(persona, "business_id", None),
        location_id=world.locations.location_of(agent_id),
        tile_x=tile[0] if tile else None,
        tile_y=tile[1] if tile else None,
        is_minor=bool(getattr(record, "is_minor", False)),
        alive=bool(getattr(record, "alive", True)),
        dormancy={
            "is_dormant": bool(getattr(dormancy, "is_dormant", getattr(dormancy, "dormant", False)))
            if dormancy is not None
            else False
        },
        health={"state": health_label(record)},
        usd_wallet_cents=world.ledger.usd_balance(agent_id),
        sim_balance=world.ledger.sim_balance(agent_id),
        ablation_arm=str(getattr(persona, "ablation_arm", "")),
        skills=skills,
        following=following,
    )


# ---------------------------------------------------------------------------
# B4: business profit and loss over time (The 250 section 23)
# ---------------------------------------------------------------------------
# "Visible profit/loss over time, so a failing business is something the town
# (and the operator) can see coming, not just a business that silently
# vanishes - and, because businesses are often family-owned in practice, a
# failing business is very often a failing household too."
#
# This is a new query and a view model, not a new ledger. Every number below
# is derived from `world.ledger` Transaction rows that `buy`, `sell`,
# `hire_employee` and `register_business` already write today - nothing here
# records anything new, and nothing here mutates.

# Which side of the books a transaction reason lands on, from the business's
# point of view. A reason absent from both is counted as neither rather than
# guessed at: an unclassified row showing up as profit would be a lie, and
# showing up as a loss would be a different one.
BUSINESS_REVENUE_REASONS = frozenset({"exchange", "transfer"})
BUSINESS_COST_REASONS = frozenset(
    {"wage", "tax", "business_registration_fee", "exchange", "transfer"}
)


class BusinessPeriod(BaseModel):
    """One slice of a business's books."""

    day: int
    revenue_sim: int
    costs_sim: int
    profit_sim: int


class BusinessPnL(BaseModel):
    """A business's books, and whether they are getting better or worse.

    `trend` is the plain-language framing section 19.7 asks for rather than
    leaving an operator to eyeball a number: a business can be profitable
    this week and still be dying, and that is the thing worth surfacing.
    """

    business_id: str
    name: str
    business_type: str
    owner_agent_id: str
    owner_name: str
    balance_sim: int
    revenue_sim: int
    costs_sim: int
    profit_sim: int
    wages_paid_sim: int
    employees: int
    days_of_wages_covered: int
    trend: str
    by_day: list[BusinessPeriod]


def classify_leg(business_id: str, transaction: Any) -> tuple[int, int]:
    """(revenue, cost) this one transaction represents for this business.

    A Transaction is one two-party leg (ledger/models.py), so the business
    is on exactly one side of it and the direction alone decides which
    column it lands in - no reason-guessing needed for the sign.
    """
    if transaction.to_account == business_id:
        return transaction.amount, 0
    if transaction.from_account == business_id:
        return 0, transaction.amount
    return 0, 0


def business_pnl(
    world: WorldState,
    business_id: str,
    names: dict[str, str],
    *,
    ticks_per_day: int = 144,
    since_tick: int = 0,
) -> BusinessPnL:
    """One business's books, day by day, from the ledger's own rows."""
    business = world.businesses[business_id]
    account = world.ledger.accounts.get(business_id)
    revenue = costs = wages = 0
    per_day: dict[int, list[int]] = {}
    for t in world.ledger.transactions:
        if t.tick < since_tick:
            continue
        got, paid = classify_leg(business_id, t)
        if not got and not paid:
            continue
        revenue += got
        costs += paid
        if paid and getattr(t.reason, "value", str(t.reason)) == "wage":
            wages += paid
        bucket = per_day.setdefault(t.tick // max(1, ticks_per_day), [0, 0])
        bucket[0] += got
        bucket[1] += paid

    by_day = [
        BusinessPeriod(day=d, revenue_sim=v[0], costs_sim=v[1], profit_sim=v[0] - v[1])
        for d, v in sorted(per_day.items())
    ]
    balance = account.sim_units if account is not None else 0
    employees = len(getattr(business, "employee_agent_ids", ()) or ())
    # What the books actually have to answer: can this business make payroll
    # again? A daily wage bill of zero means there is no payroll to miss.
    daily_wages = wages // max(1, len(by_day)) if by_day else 0
    covered = balance // daily_wages if daily_wages > 0 else 999

    if len(by_day) >= 2:
        recent = by_day[-1].profit_sim
        earlier = by_day[-2].profit_sim
        trend = "improving" if recent > earlier else "declining" if recent < earlier else "flat"
    else:
        trend = "too early to say"
    if balance <= 0:
        trend = "insolvent"
    elif daily_wages > 0 and covered <= 1:
        trend = "cannot make payroll"

    owner_id = business.owner_agent_id
    return BusinessPnL(
        business_id=business_id,
        name=getattr(business, "name", business_id),
        business_type=getattr(business, "business_type", "unknown"),
        owner_agent_id=owner_id,
        owner_name=names.get(owner_id, owner_id),
        balance_sim=balance,
        revenue_sim=revenue,
        costs_sim=costs,
        profit_sim=revenue - costs,
        wages_paid_sim=wages,
        employees=employees,
        days_of_wages_covered=min(999, covered),
        trend=trend,
        by_day=by_day,
    )



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


def build_agents_router(get_deps: Callable[[], AgentsDeps]) -> APIRouter:
    router = APIRouter(tags=["agents"], dependencies=[Depends(add_cors_headers)])

    def deps_dependency() -> AgentsDeps:
        return get_deps()

    @router.get("/agents", response_model=list[AgentRow])
    async def list_agents(deps: AgentsDeps = Depends(deps_dependency)) -> list[AgentRow]:
        personas = personas_by_id(deps.genesis)
        rows = []
        for agent_id, record in deps.world.agents.items():
            persona = personas.get(agent_id)
            if persona is None:
                continue
            rows.append(build_agent_row(agent_id, record, persona, deps))
        rows.sort(key=lambda r: r.agent_id)
        return rows

    @router.get("/agents/{agent_id}")
    async def get_agent(agent_id: str, deps: AgentsDeps = Depends(deps_dependency)) -> dict[str, Any]:
        record = deps.world.agents.get(agent_id)
        persona = personas_by_id(deps.genesis).get(agent_id)
        if record is None or persona is None:
            raise HTTPException(status_code=404, detail=f"no agent {agent_id!r}")
        row = build_agent_row(agent_id, record, persona, deps)
        location_id = row.location_id
        return {
            **row.model_dump(),
            "bio": getattr(persona, "bio", ""),
            "personality_traits": list(getattr(persona, "personality_traits", [])),
            "habits": list(getattr(persona, "habits", [])),
            "spouse_id": getattr(record, "spouse_id", None),
            "parent_ids": list(getattr(record, "parent_ids", [])),
            "children_ids": list(getattr(record, "children_ids", [])),
            "marital_status": str(getattr(record, "marital_status", "")),
            "skills": deps.world.skills.get(agent_id, {}),
            "co_located": sorted(
                a for a in deps.world.locations.agents_at(location_id) if a != agent_id
            )
            if location_id
            else [],
        }

    # -- B4: business books (section 23) ---------------------------------

    @router.get("/businesses/pnl", response_model=list[BusinessPnL])
    async def all_business_pnl(since_tick: int = 0) -> list[BusinessPnL]:
        """Every business's books, worst trend first - a failing business
        should be visible before it vanishes."""
        deps = get_deps()
        names = {a.agent_id: a.name for a in deps.genesis.agents}
        rows = [
            business_pnl(deps.world, bid, names, since_tick=since_tick)
            for bid in deps.world.businesses
        ]
        rows.sort(key=lambda r: (r.profit_sim, r.balance_sim))
        return rows

    @router.get("/businesses/{business_id}/pnl", response_model=BusinessPnL)
    async def one_business_pnl(business_id: str, since_tick: int = 0) -> BusinessPnL:
        deps = get_deps()
        if business_id not in deps.world.businesses:
            raise HTTPException(status_code=404, detail=f"no business {business_id!r}")
        names = {a.agent_id: a.name for a in deps.genesis.agents}
        return business_pnl(deps.world, business_id, names, since_tick=since_tick)

    @router.get("/places")
    async def list_places(deps: AgentsDeps = Depends(deps_dependency)) -> list[dict[str, Any]]:
        world = deps.world
        seen: dict[str, list[str]] = {}
        for agent_id in world.agents:
            location_id = world.locations.location_of(agent_id)
            if location_id:
                seen.setdefault(location_id, []).append(agent_id)
        # Every known place, not only occupied ones: an empty temple is
        # still a place the operator can look at.
        known = set(seen)
        for building in world.buildings.values():
            location_id = getattr(building, "location_id", None) or getattr(building, "plot_id", None)
            if location_id:
                known.add(str(location_id))
        for zone_id in world.resource_zones:
            known.add(str(zone_id))
        return [
            {
                "place_id": place_id,
                "occupants": sorted(seen.get(place_id, [])),
                "occupancy": len(seen.get(place_id, [])),
            }
            for place_id in sorted(known)
        ]

    @router.get("/map/tiled")
    async def map_tiled(deps: AgentsDeps = Depends(deps_dependency)) -> dict[str, Any]:
        """The full Tiled JSON the browser's Phaser scene consumes.

        Without this the game server could never build a Snapshot: its
        contract requires map_tiled_json, nothing served it, and the map
        stayed black no matter what else was fixed. MapService already
        holds the parsed map (it loads the_ville_jan7.json at boot and
        appends the Kasukabe genesis zones), so this just exposes it.
        """
        if deps.map_service is None or getattr(deps.map_service, "tiled", None) is None:
            raise HTTPException(status_code=503, detail="map service not wired")
        return deps.map_service.tiled

    @router.get("/world/state", response_model=WorldStateView)
    async def world_state(deps: AgentsDeps = Depends(deps_dependency)) -> WorldStateView:
        world = deps.world
        # core/'s own pure tick -> timestamp function, not world.game_clock().
        #
        # game_clock() returns a life.clock.GameClock, which carries
        # total_hours and a `day` property and NOTHING named `hour` or
        # `minute` - so `getattr(clock, "hour", 0)` and its `minute` twin
        # silently took their defaults on every poll, and the town clock
        # read 00:00 for the whole of every run while the day counter
        # beside it advanced. timestamp_for names hour and minute outright,
        # so a future rename breaks this line instead of freezing it.
        clock = timestamp_for(world.tick())
        records = list(world.agents.values())
        # `is_dormant`, which is what DormancyState actually calls it.
        #
        # This read `getattr(dormancy, "dormant", False)` - a field that does
        # not exist - so the default fired for every resident on every poll
        # and this route reported `dormant: 0` no matter how many residents
        # were dormant. Exactly the failure the clock above had, in the same
        # function, and it survived that fix.
        #
        # It matters more than the clock did: dormancy is the last step of
        # the starvation escalation (hunger -> illness -> dormancy -> death),
        # so the one number that would show a household starving was pinned
        # at zero. No fallback chain here - a rename should break this line
        # loudly rather than quietly resume reporting nobody.
        dormant = sum(1 for r in records if r.dormancy.is_dormant)
        # Read directly, like the two beside it. A default of True here
        # would report a town of corpses as fully alive the moment the
        # field were renamed - the same shape as the clock and the dormant
        # count, and the third instance of it found in this one function.
        alive = sum(1 for r in records if r.alive)
        return WorldStateView(
            tick=world.tick(),
            game_day=clock.game_day,
            game_hour=clock.hour,
            game_minute=clock.minute,
            population=len(records),
            alive=alive,
            dormant=dormant,
            dead=len(records) - alive,
            households=len(world.households),
            businesses=len(world.businesses),
            buildings=len(world.buildings),
            crime_cases=len(world.crime_cases),
            run_id=RUN_ID,
        )

    return router
