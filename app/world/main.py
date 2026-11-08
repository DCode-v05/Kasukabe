"""World server HTTP/WebSocket entrypoint (DESIGN.md section 9, task
"wire-world"): the thin FastAPI shell around wiring.WorldServer.

app/CONVENTIONS.md, "How modules talk": the world server exposes two
surfaces from the same running process - "an internal async API ... for
anything running in-process" (wiring.WorldServer's own methods: its own
tool-registry execution, its own tick loop) and "an HTTP API ... for
everything out-of-process: agents' tool calls arrive here, game/'s
President/God console actions ... arrive here, research/'s read-only
queries arrive here." This module is exactly that second half - it holds
no simulation logic of its own, every route below is a thin translation
from an HTTP request to a WorldServer method call plus an HTTP-shaped
error on failure.

Makefile's `run-world` and docker-compose.yml's `world` service both
launch `world.main:app` (this module), not `world.app:app` (the
health-check-only scaffold that predates this file - kept around only for
its own standalone test, world/tests/test_world_app.py). Run directly
with `uvicorn world.main:app --port 8100` (same default dev port
app/CONVENTIONS.md documents for world/); the docker-compose route also
mounts ../smallville read-only, since WorldConfig()'s default
require_map=True needs the real Ville tileset to boot (see
build_world_server()'s own RuntimeError message in wiring.py for the
exact path it looks for).

President/God console routes (STATUS.md risk #2, "console-contract" task):
this module used to serve a hand-rolled, non-`/admin`-prefixed subset of
these (`/president/land-petitions`, `/god/prayers`, no spend/control/
alerts routes at all) that shared zero path overlap with
game/console/world_client.py's actual contract - the operator console
could not steer a live world server at all. world/admin_api.py is now the
one implementation of that full contract (see its own module docstring),
mounted below via build_admin_router(); this file only supplies the
closures admin_api.py needs (this process's WorldServer, its MapService,
where population dials actually live) and resolves the operator token
both this process's /admin/* routes and run_genesis.py's embedded world
app independently re-validate (game/console/README.md: "world
independently re-validates instead of trusting this proxy blindly").
"""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from world.admin_api import (
    AdminDeps,
    AdminExtras,
    build_admin_router,
    install_admin_exception_handlers,
    resolve_operator_token,
)
from world.agent_viewer_api import AgentViewerDeps, build_agent_viewer_router
from world.agents_api import AgentsDeps, build_agents_router
from world.governance_api import GovernanceDeps, build_governance_router
from world.family_api import FamilyDeps, build_family_router
from world.skills_api import SkillsDeps, build_skills_router
from world.workshop_api import WorkshopDeps, build_workshop_router
from world.ledger.errors import InsufficientFundsError, LedgerError
from world.location_api import LocationDeps, build_location_router
from world.social_api import SocialDeps, build_social_router
from world.tools.registry import tool_registry_rows
from world.wiring import WorldConfig, WorldServer, build_world_server

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    server = await build_world_server(WorldConfig())
    app.state.world_server = server
    token, was_generated = resolve_operator_token()
    app.state.admin_extras = AdminExtras(operator_token=token)
    if was_generated:
        logger.warning(
            "OPERATOR_TOKEN not set - generated an ephemeral token for this process. "
            "Set OPERATOR_TOKEN in the environment for a token that survives a restart. "
            "Ephemeral operator token: %s",
            token,
        )
    logger.info(
        "world server booted: %d agents, tick=%d",
        len(server.world.agents), server.world.current_tick,
    )
    try:
        yield
    finally:
        await server.close()


app = FastAPI(title="kasukabe-world", version="0.3.0", lifespan=lifespan)


def server_dep() -> WorldServer:
    """Every route below calls this instead of using FastAPI's Depends()
    machinery - `app` is this module's own global, so a plain lookup
    against app.state (set once in lifespan(), before uvicorn starts
    accepting requests) is exactly as safe and considerably shorter."""
    server = getattr(app.state, "world_server", None)
    if server is None:
        raise HTTPException(status_code=503, detail="world server not booted yet")
    return server


def admin_extras_dep() -> AdminExtras:
    extras = getattr(app.state, "admin_extras", None)
    if extras is None:
        raise HTTPException(status_code=503, detail="world server not booted yet")
    return extras


def build_admin_deps() -> AdminDeps:
    server = server_dep()
    extras = admin_extras_dep()
    return AdminDeps(
        world=server.world,
        tick_engine=server.tick_engine,
        map_service=server.map_service,
        extras=extras,
        get_population_dials=lambda: server.population_dials,
        set_population_dials=lambda updates: server.set_population_dials(**updates),
    )


install_admin_exception_handlers(app)
app.include_router(build_admin_router(build_admin_deps))


def build_agents_deps() -> AgentsDeps:
    server = server_dep()
    return AgentsDeps(world=server.world, genesis=server.genesis, map_service=server.map_service)


def build_location_deps() -> LocationDeps:
    server = server_dep()
    return LocationDeps(world=server.world, map_service=server.map_service, registry=server.registry)


def build_governance_deps() -> GovernanceDeps:
    server = server_dep()
    return GovernanceDeps(world=server.world, genesis=server.genesis)


def build_social_deps() -> SocialDeps:
    server = server_dep()
    return SocialDeps(world=server.world, genesis=server.genesis, trace=server.trace_store)


def build_family_deps() -> FamilyDeps:
    server = server_dep()
    return FamilyDeps(world=server.world, genesis=server.genesis)


def build_skills_deps() -> SkillsDeps:
    server = server_dep()
    return SkillsDeps(world=server.world, genesis=server.genesis)


def build_workshop_deps() -> WorkshopDeps:
    server = server_dep()
    return WorkshopDeps(world=server.world, genesis=server.genesis)


def build_agent_viewer_deps() -> AgentViewerDeps:
    server = server_dep()
    return AgentViewerDeps(
        world=server.world,
        genesis=server.genesis,
        tick_engine=server.tick_engine,
        trace=server.trace_store,
    )


app.include_router(build_agents_router(build_agents_deps))
app.include_router(build_location_router(build_location_deps))
app.include_router(build_governance_router(build_governance_deps))
app.include_router(build_social_router(build_social_deps))
app.include_router(build_agent_viewer_router(build_agent_viewer_deps))
app.include_router(build_family_router(build_family_deps))
app.include_router(build_skills_router(build_skills_deps))
app.include_router(build_workshop_router(build_workshop_deps))


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Does not require the world to be booted (unlike
    every other route below) so an orchestrator can distinguish
    "process is up, still booting" from "process is dead"."""
    return {"status": "ok", "service": "world", "cognition": "none"}


@app.get("/state/summary")
async def state_summary() -> dict[str, Any]:
    return server_dep().world_summary()


@app.get("/agents")
async def list_agents() -> list[dict[str, Any]]:
    return server_dep().list_agents()


@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    summary = server_dep().agent_summary(agent_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"no agent {agent_id!r}")
    return summary


@app.get("/ledger/invariant")
async def ledger_invariant() -> dict[str, Any]:
    return server_dep().ledger_invariant_status()


@app.get("/ledger/balance/{account_id}")
async def ledger_balance(account_id: str) -> dict[str, Any]:
    server = server_dep()
    return {
        "account_id": account_id,
        "usd_cents": server.world.ledger.usd_balance(account_id),
        "sim_units": server.world.ledger.sim_balance(account_id),
    }


@app.get("/ledger/payer/{agent_id}")
async def ledger_payer(agent_id: str, purpose: str = "", amount_cents: int = 0) -> dict[str, Any]:
    """Who actually pays for agent_id's thinking, and what they have left.

    broker/ledger.py's HttpLedgerClient calls this before every dispatch;
    its docstring carried this shape as an ASSUMED CONTRACT because the
    route did not exist yet, so a real call died at UnknownAgentError
    before ever reaching Bedrock. Parent-debit (DESIGN.md section 4:
    minors' calls bill the parent) is resolved here rather than in the
    broker, so the broker never needs to know what a minor is.
    """
    server = server_dep()
    ledger = server.world.ledger
    try:
        account = ledger.get_account(agent_id)
        payer_id = ledger.payer_for(agent_id, amount_cents, purpose)
    except LedgerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "agent_id": agent_id,
        "payer_agent_id": payer_id,
        "balance_cents": ledger.usd_balance(payer_id),
        "is_minor": bool(getattr(account, "is_minor", False)),
    }


class LedgerDebitRequest(BaseModel):
    agent_id: str
    amount_cents: int = Field(gt=0)
    purpose: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


@app.post("/ledger/debit")
async def ledger_debit(payload: LedgerDebitRequest) -> dict[str, Any]:
    """Burn USD for one inference call, billed to the resolved payer.

    Same underlying path as /ledger/burn (burn_inference, which is also
    where cognitive-poverty dormancy is detected) - this route exists
    because it is the contract the broker was written against. 402 on an
    empty wallet is a real outcome, not a hypothetical: an agent that has
    spent its $2 genuinely cannot afford to think.
    """
    server = server_dep()
    ledger = server.world.ledger
    memo = payload.purpose or "inference"
    try:
        await server.burn_inference(payload.agent_id, payload.amount_cents, memo=memo)
        payer_id = ledger.resolve_usd_payer(payload.agent_id)
    except InsufficientFundsError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except LedgerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "payer_agent_id": payer_id,
        "balance_cents_after": ledger.usd_balance(payer_id),
    }


class BurnRequest(BaseModel):
    agent_id: str
    usd_cents: int = Field(gt=0)
    memo: str = ""


@app.post("/ledger/burn")
async def ledger_burn(payload: BurnRequest) -> dict[str, Any]:
    """The endpoint broker/ calls to burn USD for an inference call
    (ledger/core.py's burn_for_inference docstring: "reached through
    world's HTTP API since broker never imports world directly").
    """
    server = server_dep()
    transaction = await server.burn_inference(payload.agent_id, payload.usd_cents, memo=payload.memo)
    return transaction.model_dump(mode="json")


class ToolCallRequest(BaseModel):
    actor_id: str
    params: dict[str, Any] = Field(default_factory=dict)


def registry_etag(rows: list[dict[str, Any]]) -> tuple[bytes, str]:
    """The payload and its validator, from the payload itself.

    ENG-19 asked for an ETag "keyed on (version, location, seat)". A digest
    of the serialised rows is that key and strictly more of it: those three
    are the only inputs `tool_registry_rows` has, so a tuple key can only
    ever be a hand-maintained restatement of what the body already says -
    and the failure mode of a hand-maintained key is a resident holding a
    schema the town no longer serves, which is unfixable from the engine
    side. A digest cannot go stale by omission.

    `sort_keys` because two dicts that differ only in insertion order are
    the same registry, and a validator that changed when nothing did would
    quietly turn this back into an unconditional fetch.
    """
    body = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return body, '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


@app.get("/tools/registry")
async def tools_registry(request: Request) -> Response:
    """Every agent's first call. Without it an engine cannot boot.

    Declared BEFORE the POST route below for readability, though FastAPI
    matches on method as well as path so the two never collided - and that
    is exactly why this was missed: `POST /tools/{tool_name}` happily
    matched the *path* /tools/registry, so a GET returned "405 Method Not
    Allowed" rather than a 404. A 405 reads like "wrong verb, route
    exists", which is precisely wrong here - the route did not exist, and
    engine/src/sim/world_tools.py's fetch_tool_registry raised on it
    before any agent had built its tool list.

    Shares its body with the embedded app in world/run_genesis.py via
    tool_registry_rows, so the two hosts cannot drift apart again.

    ENG-19. Answers a conditional GET. Every engine refetches this whole
    payload once a TICK - the seam forces it because schemas are served by
    location and the registry's version counter cannot see a resident walk
    (engine/src/sim/pinned_boot.py) - so at 250 agents this route was
    shipping ~53 KB x 250 x every tick to tell them all that nothing had
    changed. With `If-None-Match` it ships a 304 and no body. The rows are
    still built to compute the validator; what is saved is the transfer and
    the 76 ToolDefs the engine would otherwise rebuild from it.

    A caller that sends no `If-None-Match` gets exactly what it always got,
    which is every existing caller: the observatory, `world/preflight.py`,
    the tests.
    """
    server = server_dep()
    body, etag = registry_etag(tool_registry_rows(server.registry))
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(content=body, media_type="application/json", headers={"ETag": etag})


@app.get("/world/vitals")
async def world_vitals() -> dict[str, Any]:
    world_state = server_dep().world
    """Health, buildings, food and treasury — from the world, not a model.

    These four panels were previously drawn by a deterministic client-side
    simulation: `healthSim` invented illnesses and deaths, `BUILD_PROJECTS`
    listed a plausible build order, `foodSim` grew and ate imaginary crops,
    and the treasury was projected from a mock clock. All four sat beside
    figures that were real, distinguished only by a small tag.

    Everything below is read off the running world. Where the world has no
    answer the field is absent rather than estimated - a missing number a
    reader can see is worth more than a plausible one they cannot check.
    """
    agents = world_state.agents

    by_state: dict[str, int] = {}
    dormant = dead = 0
    for agent in agents.values():
        alive = getattr(agent, "alive", True)
        if not alive:
            dead += 1
            continue
        health = getattr(agent, "health", None)
        label = getattr(getattr(health, "state", None), "value", None) or str(
            getattr(health, "state", "well")
        )
        by_state[label] = by_state.get(label, 0) + 1
        d = getattr(agent, "dormancy", None)
        if d is not None and getattr(d, "is_dormant", False):
            dormant += 1

    buildings = [
        {
            "building_id": getattr(b, "building_id", ""),
            "kind": getattr(b, "kind", "") or getattr(b, "building_type", ""),
            "owner_id": getattr(b, "owner_id", None),
        }
        for b in world_state.buildings.values()
    ]
    businesses = [
        {
            "business_id": b.business_id,
            "name": b.name,
            "business_type": b.business_type,
            "owner_agent_id": b.owner_agent_id,
            "location_id": b.location_id,
        }
        for b in world_state.businesses.values()
    ]

    edible = {"wheat", "rice", "corn", "potato", "tomato", "carrot",
              "cabbage", "soybean", "barley", "flour", "bread", "meal",
              "apple", "milk", "egg", "fish"}
    holdings: dict[str, int] = {}
    try:
        balances = world_state.inventory.balances
        for _owner, goods in balances.items():
            for item, qty in goods.items():
                if item in edible and qty:
                    holdings[item] = holdings.get(item, 0) + int(qty)
    except Exception:  # noqa: BLE001 - a shape change must not 500 the viewer
        holdings = {}

    zones = [
        {
            "zone_id": getattr(z, "zone_id", ""),
            "kind": getattr(getattr(z, "kind", None), "value", None) or str(getattr(z, "kind", "")),
            "remaining": getattr(z, "remaining", None),
        }
        for z in world_state.resource_zones.values()
    ]

    treasury_sim = None
    try:
        from world.ledger.money import TREASURY_ACCOUNT_ID

        treasury_sim = world_state.ledger.sim_balance(TREASURY_ACCOUNT_ID)
    except Exception:  # noqa: BLE001
        treasury_sim = None

    town_wealth = 0
    try:
        for agent_id in agents:
            town_wealth += world_state.ledger.sim_balance(agent_id)
    except Exception:  # noqa: BLE001
        town_wealth = None

    return {
        "tick": world_state.tick(),
        "town_wealth_sim": town_wealth,
        "health": {
            "by_state": by_state,
            "dormant": dormant,
            "dead": dead,
            "alive": len(agents) - dead,
        },
        "built": {
            "buildings": buildings,
            "businesses": businesses,
            "building_count": len(buildings),
            "business_count": len(businesses),
            "household_count": len(world_state.households),
        },
        "food": {"holdings": holdings, "zones": zones},
        "treasury": {"balance_sim": treasury_sim},
    }


@app.get("/world/utterances")
async def world_utterances(limit: int = 200) -> dict[str, Any]:
    """Mirrors run_genesis.py's route - see there. Both hosts serve it so the
    viewer's Talk feed works whichever world is running."""
    rows = server_dep().world.utterances[-limit:]
    return {"count": len(rows), "utterances": rows}


@app.get("/world/authored")
async def world_authored() -> dict[str, Any]:
    """Mirrors run_genesis.py's route - what the town has invented."""
    world = server_dep().world
    return {
        "skills": [
            {
                "name": d.name, "author_agent_id": d.author_agent_id,
                "steps": len(d.steps), "discounts_tools": d.discounts_tools,
                "governmental": d.governmental, "status": d.status,
                "version": d.version, "created_tick": d.created_tick,
                "holders": [a for a, h in world.skills.items() if d.name in h],
            }
            for d in world.skill_definitions.values()
        ],
        "tools": [
            {
                "tool_name": t.tool_name, "creator_agent_id": t.creator_agent_id,
                "steps": [s.tool_name for s in t.steps], "version": t.version,
                "patented": t.patent_application_id is not None,
            }
            for t in world.custom_tools.values()
        ],
        "skill_count": len(world.skill_definitions),
        "tool_count": len(world.custom_tools),
        "proficiency": world.skills,
        "repetitions": world.skill_reps,
    }


@app.get("/world/refusals")
async def world_refusals(limit: int = 5000) -> dict[str, Any]:
    """Every intention the world refused, for the observatory on :8500.

    Mirrors run_genesis.py's embedded route. Both hosts serve it for the same
    reason both serve /tools/registry: they drifted apart over that one once,
    and a host missing this one would show an empty gaps view rather than an
    error - a silent wrong answer, which is the worse failure.
    """
    rows = server_dep().world.tool_refusals[-limit:]
    return {"count": len(rows), "refusals": [r.model_dump() for r in rows]}


@app.get("/world/justice")
async def world_justice() -> dict[str, Any]:
    """GOV-10. Mirrors run_genesis.py's embedded route: the false-conviction
    rate, read from the world because no event carries the truth."""
    from world.tools.primitives.institutions import justice_reading

    return justice_reading(server_dep().world)


@app.get("/tools/registry/version")
async def tools_registry_version() -> dict[str, int]:
    """Cheap staleness check for engines - see ToolRegistry.version.

    A separate route rather than a `version` key on /tools/registry so the
    list shape stays what every existing caller already parses. It also polls
    better: 40 engines asking "has anything changed" once a tick should not
    each pull the whole vocabulary to find out that nothing has.
    """
    return {"version": server_dep().registry.version}


@app.post("/tools/{tool_name}")
async def call_tool(tool_name: str, payload: ToolCallRequest) -> dict[str, Any]:
    server = server_dep()
    result = await server.execute_tool(payload.actor_id, tool_name, payload.params)
    return {
        "tool_name": result.tool_name,
        "actor_id": result.actor_id,
        "result": result.result,
        "events": [event.model_dump(mode="json") for event in result.events],
    }


class MarkDecidedRequest(BaseModel):
    agent_id: str


@app.post("/admin/tick")
async def advance_tick() -> dict[str, Any]:
    server = server_dep()
    extras = admin_extras_dep()
    if extras.sim_control.paused:
        raise HTTPException(status_code=409, detail="sim is paused, resume via POST /admin/control/resume")
    result = await server.advance_tick()
    return {
        "tick": result.tick.model_dump(mode="json"),
        "due_agents": result.due_agents,
        "perceptions": {
            agent_id: perception.model_dump(mode="json") for agent_id, perception in result.perceptions.items()
        },
        "checkpoint_written": result.checkpoint is not None,
    }


@app.post("/admin/agents/{agent_id}/mark-decided")
async def mark_decided(agent_id: str) -> dict[str, str]:
    server_dep().mark_agent_decided(agent_id)
    return {"status": "ok", "agent_id": agent_id}


@app.get("/crime/cases")
async def crime_cases() -> list[dict[str, Any]]:
    return server_dep().crime_cases()


@app.get("/crime/metrics")
async def crime_metrics() -> dict[str, Any]:
    return await server_dep().crime_metrics()


@app.get("/research/control-businesses")
async def control_businesses() -> list[dict[str, Any]]:
    return list(server_dep().control_businesses.values())


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    """DESIGN.md section 9: "The game app is fed by the world server's own
    WebSocket relay (no external realtime)." On connect, replays every
    buffered WorldEvent (EventBus.replay - catches a freshly-opened
    dashboard up on recent history) and then forwards every new one live,
    matching app/CONVENTIONS.md's `kasukabe:<domain>` channel split at the
    payload level (each message carries its own `type`, whose prefix
    before the first dot is the domain, so a client can filter locally
    without this relay needing per-domain WebSocket routes).
    """
    await websocket.accept()
    server = server_dep()

    async def on_event(event: Any) -> None:
        await websocket.send_json(event.model_dump(mode="json"))

    await server.event_bus.replay(on_event)
    server.event_bus.subscribe("*", on_event)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        server.event_bus.unsubscribe("*", on_event)
