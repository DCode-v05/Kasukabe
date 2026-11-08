"""The genesis runner: DESIGN.md section 9's live sim, driven end to end.

Task (track: genesis-runner, app/CONVENTIONS.md's module boundaries apply):
boot the world from genesis.json, launch 250 engine processes pointed at
the broker, run continuously, checkpoint every game-day to SQLite + S3,
resume cleanly from the newest checkpoint after a crash, stop cleanly when
the spend governor trips, and expose live metrics. This module is that
runner. It owns nothing else - see app/world/retrieval_depth.py for the
memory-retrieval depth knob this file wires into the tick loop.

Architecture decisions this file makes, and why, since "boot the world" and
"launch 250 engine processes" both cross several tracks' half-finished
seams as of this writing:

1. Composition root. world/tools/bootstrap.py's `build_world()` is the
   shared, canonical way to assemble a running world (ledger + charter +
   tool registry + event bus + locations, DESIGN.md section 12's "world+
   tools" build step). world/app.py is a second, separate entrypoint into
   that same composition (the interactive dev/HTTP-only server, `make
   run-world`) - its own module docstring says it is still a health-check
   scaffold as of this writing, and infra/env/world.env.example
   independently confirms the same thing ("world/app.py is still a
   /health-only scaffold as of this writing"). A live genesis run cannot
   wait on that: GenesisRunner calls build_world() itself and becomes a
   second real caller of the same composition root, the same way
   Makefile's run-world/run-broker/run-game/run-console are already four
   separate processes sharing one dependency set. It does not touch
   world/app.py.

2. Embedded world HTTP surface. The 250 engine processes need somewhere
   real to send tool calls (engine/src/sim/world_tools.py's WorldClient,
   whose own module docstring writes out the exact ASSUMED CONTRACT:
   `GET {base_url}/tools/registry` and
   `POST {base_url}/agents/{agent_id}/tool_calls`) and broker needs
   somewhere real to debit wallets (broker/ledger.py's HttpLedgerClient,
   same pattern: `GET {world_base_url}/ledger/payer/{agent_id}` and
   `POST {world_base_url}/ledger/debit`, both documented there as "the
   FastAPI route wrapping world/ledger/core.py's Ledger does not exist
   yet... flagged as an open question"). Two independent tracks have
   already written down the exact HTTP contract they need from world/;
   this file implements exactly those contracts (nothing more) so a
   genesis run is self-sufficient today, per CONVENTIONS.md's own
   instruction: "code against the interface... leave a TODO rather than
   editing their files." `--world-mode=external` turns this off and
   points at an already-running world/app.py instead, once that lands -
   a config flag, not a rewrite.

3. Process supervision, not a Firecracker import.
   infra/firecracker/supervisor.py defines a `Supervisor` protocol whose
   own docstring says this track (app/world/run_genesis.py) is meant to
   program against it. It cannot be imported: CONVENTIONS.md's module
   boundaries are explicit that "infra/ ... drives them from the outside
   ... the other packages never import from it," and infra/README.md
   repeats the same rule by name ("Never imported by world/, broker/,
   game/, or research/"). So this file defines its own small,
   structurally-identical Supervisor-shaped protocol and a plain-process
   implementation (ProcessSupervisor) - exactly the "brings the sim up
   first on plain supervised OS processes" behavior that infra module's
   own docstring describes as this track's job. Swapping in the real
   Firecracker VMManager later is an infra/ script wiring a
   VMManager-backed adapter into GenesisRunner's constructor (infra/ is
   allowed to import world/, the reverse is what is forbidden) - a
   substitution at that call site, not a rewrite of this file.

4. Checkpointing. TickEngine already writes a checkpoint at every
   game-day boundary (world/core/checkpoint.py, world/core/clock.py's
   is_day_boundary) and hands it to whatever CheckpointSink this runner
   supplies. That sink here does two things: (a) an authoritative local
   gzip JSON file this runner reads back on resume - supabase/client.py's
   SupabaseClient interface has upload_archive() but no matching
   download, so a local copy is what resume actually restores from, and
   (b) a best-effort mirror through supabase.factory.get_client() +
   supabase.archiver.PayloadArchiver into the shared world_snapshots
   table, which is exactly "SQLite locally, S3 once the trace-store
   track's Storage backend is S3-backed" (that track's own tracker lists
   "Build boto3-free S3 client for archive uploads" - this runner does
   not reimplement that, it calls the same client interface every other
   writer in this repo uses). (a) never swallows a failure (a broken
   local checkpoint is worse than a crashed process, matching this
   repo's own "hard-fail boot" convention); (b) always does, since it is
   a redundant secondary copy and a transient Supabase outage must never
   take down a live genesis run.

5. Two axes of "how is one agent's turn executed", one flag. `mock_mode`
   selects MockSupervisor+MockAgentGateway (in-process, zero network, zero
   spend, deterministic - CONVENTIONS.md: "Stub or mock inference") or
   ProcessSupervisor+HttpAgentGateway (real `python -m src.sim.sim_main`
   subprocesses per DESIGN.md section 4, real POSTs to their /tick per
   engine/src/sim/agent_adapter.py's TickRequest/TickResponse contract).
   Both paths drive the exact same GenesisRunner tick loop, checkpointing,
   metrics, and spend-governor code - the "full mock-inference dry-run
   mode" the task asks for is not a separate code path, it is this file's
   normal code path with the bottom two layers swapped out, the same
   pattern infra/firecracker/supervisor.py's own process/firecracker split
   uses.

Known, deliberate gaps (out of this track's scope, not silently glossed
over):
  - No real map/tile placement. world/map/ (another track) owns real
    tile-based positions; agents here are placed at a household-derived
    location id (f"home:{household_id}") as a working placeholder so
    location-gated tools and perception have *something* real to key off
    of. Agents can `move` themselves once they discover real location ids
    through play.
  - Charter/governance state (elections, government seats, amendments,
    organizations) is bootstrapped once (the genesis charter, DESIGN.md
    section 7, plus one scheduled first election per charter/engine.py's
    own invitation to the "world/ tick-loop" caller). It IS checkpointed
    and restored exactly by this file (CharterCheckpointAdapter, STATUS.md
    risk #4 fix) - a resume restores the real charter history, election
    state (whatever phase it was in, tallied or not), sitting government,
    amendments, organizations, and patent-office queue instead of
    re-seeding a fresh genesis charter. Full election phase-machine
    auto-advancement (DESIGN.md section 7's "every 3 game-days") still
    belongs to a future charter-tick module; this runner only bootstraps
    the first election on a fresh (non-resumed) boot and reports
    politics.* event counts as a live metric.
  - Births are operator-gated by design (DESIGN.md section 8's
    grant/silent-deny god console); this runner never auto-grants a birth
    request, it only counts agent.spawned birth events as they occur.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import gzip
import hashlib
import hmac
import json
import logging
import os
import random
import signal
import subprocess
import time
import typing
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol, runtime_checkable

import httpx
import tarfile
import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel

from world.charter.engine import CharterEngine
from world.core.checkpoint import CheckpointError, WorldCheckpoint
from world.charter.models import AmendmentStatus, Charter, ElectionPhase, GovernmentState
from world.core.clock import TICKS_PER_DAY, TICK_MINUTES, GameTimestamp, game_time_label, timestamp_for
from world.core.engine import TickEngine, TickResult
from world.core.events import WorldEvent
from world.core.pacing import PacingConfig, PacingMode
from world.core.scheduler import SchedulerConfig
from world.ledger.core import Ledger
from world.ledger.errors import (
    AccountNotFoundError,
    InsufficientFundsError as LedgerInsufficientFundsError,
    LedgerHaltedError,
    ParentNotConfiguredError,
)
from world.ledger.models import Account, AccountKind, Transaction
from world.ledger.money import (
    BURNED_ACCOUNT_ID,
    SIM_PER_USD,
    TOTAL_USD_CENTS,
    TREASURY_ACCOUNT_ID,
)
from world.life import aging, death, health as life_health
from world.life.clock import GameClock as LifeGameClock
from world.life.constants import DEFAULT_TARGET_BAND, DORMANCY_DEATH_HOURS
from world.life.models import (
    AgentLifeRecord,
    DormancyState,
    HealthState,
    Household as LifeHousehold,
    LifeEvent,
    PopulationDials,
)
from world.admin_api import (
    AdminDeps,
    AdminExtras,
    SimControl,
    build_admin_router,
    install_admin_exception_handlers,
    resolve_operator_token,
)
from world.scenario import Scenario
from world.retrieval_depth import RetrievalDepthConfig, render_perception_text
from world.tools.goal_lifecycle import expire_goals
from world.tools.bootstrap import build_world
from world.tools.economy_bridge import LedgerEconomyPort
from world.tools.economy_bridge import life_event_to_world_event
from world.resource_log import ResourceSampler
from world.tools.errors import ToolError
from world import memory_budget
from world.tools import names
from world.tools.inventory import MAX_HOLDINGS_SHOWN, Inventory, holdings_line
from world.tools.primitives.movement import canonical_destination
from world.tools.primitives.resources import regenerate_zones
from world.tools.locations import workshops_for_business_type
from world.tools.models import (
    Business,
    PricePost,
    ToolRefusalRecord,
    WagePromise,
)
from world.call_budget import local_tool_schemas
from world.tools.registry import ToolRegistry, tool_registry_rows
from world.tools.state import OWNED_STATE_FIELDS, WorldState, conversation_outcome


WORKPLACE_BY_OCCUPATION: dict[str, str] = {
    "farmer": "Kasukabe Farmland",
    "miner": "Kasukabe Ore Outcrop",
    "forester": "Kasukabe Forest",
    "day_laborer": "Kasukabe Farmland",
    "construction_worker": "carpentry",
    "construction_company_owner": "carpentry",
    "grocery_store_owner": "Harvey Oak Supply Store",
    "grocery_clerk": "Harvey Oak Supply Store",
    "cafe_owner": "Hobbs Cafe",
    "barista": "Hobbs Cafe",
    "trader": "Harvey Oak Supply Store",
    "pharmacist": "The Willows Market and Pharmacy",
    "college_instructor": "Oak Hill College",
    "student": "Oak Hill College",
    "government_clerk": "Kasukabe Government Complex",
    "office_administrator": "Kasukabe Government Complex",
    "miller": "mill",
    "sawyer": "sawmill",
    "baker": "kitchen",
    "artisan": "carpentry",
    "pharmacist_assistant": "The Willows Market and Pharmacy",
}


logger = logging.getLogger("kasukabe.genesis_runner")

OPENING_WAGE_SIM = 60

DEFAULT_WORLD_HOST = "127.0.0.1"
DEFAULT_WORLD_PORT = 8100
def sign_broker_token(agent_id: str, fallback: str) -> str:
    """Build the credential the broker expects from an engine.

    broker/auth.py rejects an unsigned token whenever AUTH_SHARED_SECRET is
    configured, demanding '<agent_id>.<hex-hmac>'. The engine forwards
    BROKER_API_KEY verbatim as its bearer credential, so passing the raw
    shared secret made every awake tick fail with 500 (CitadelAPIError:
    "credential is unsigned but AUTH_SHARED_SECRET is configured") while
    sleeping ticks returned 200 - which is why the runner looked healthy
    and spend stayed at exactly $0.

    With no shared secret configured the broker accepts any credential -
    but it still derives WHO IS CALLING from that credential
    (broker/auth.py: `agent_id = token.split(".", 1)[0] if "." in token
    else token`). Returning the raw shared key unsigned therefore made
    every engine in the town identify as the literal string
    "kasukabe-sim-broker".

    Under MOCK_MODE that is invisible: the in-memory ledger simply
    provisions one wallet under that name and all 250 agents share it.
    Against a real world ledger it is fatal - every single call comes back
    404 "agent 'kasukabe-sim-broker' is not known to world's ledger", the
    runner reports 500s from every /tick, and spend sits at exactly $0.
    Measured, not theorised: 1242 consecutive 404s.

    So the unsigned form carries the agent id too. It is unsigned, which is
    all the broker asks for when no secret is set, but it is still the
    RIGHT identity - and identity is what the ledger bills.
    """
    secret = os.environ.get("AUTH_SHARED_SECRET", "")
    if not secret:
        return agent_id
    signature = hmac.new(secret.encode(), agent_id.encode(), hashlib.sha256).hexdigest()
    return f"{agent_id}.{signature}"


DEFAULT_BROKER_BASE_URL = "http://localhost:8200"
DEFAULT_BROKER_API_KEY = "kasukabe-sim-broker"
DEFAULT_HOME_ROOT = "/tmp/kasukabe-agents"
DEFAULT_SIM_AGENT_PORT_BASE = 9100

DAWN_TICK = 36
NIGHT_ONSET_TICK = 132
ENGINE_OUTPUT_TAIL_LINES = 50

ENGINE_ERROR_DETAIL_CHARS = 600

ENGINE_TAIL_ON_FAILURE = 12


def prune_ties_to_people_not_in_the_cast(genesis: Any) -> dict[str, int]:
    """Drop what a persona says about people this world does not contain.

    A small cast (`genesis_6.json`, `genesis_20.json`) is cut from the full
    town and kept every persona's ties. Run 26: Lior Jarrah spent the whole
    run searching for Saoirse Nakamura, because the persona prompt said
    "People in Kasukabe you already know, before today: - Saoirse Nakamura
    (agent id ...)". Farid Ravensworth sent a question about debts to Gideon
    Novak. Neither is in a six-person town, so the search had no possible
    ending and every answer was "haven't seen her". All six residents of
    genesis_6 carried such ties - sixteen opinions, every one about somebody
    absent - and a `debt` opinion also seeds a real loan, so a resident
    could owe sim to nobody.

    Removed at load, before anything reads the cast: the persona prompt, the
    TOOL-18 loan seeder and the secret target all see only people who exist.
    The secret's own sentence is kept - "would forgive Kai Zeleny almost
    anything" is a private feeling, not a claim that she lives here - but its
    target id is cleared. A full cast is untouched, because nobody in it is
    absent. Never raises; returns the counts so boot can say what it removed.
    """
    present = {a.agent_id for a in genesis.agents}
    counts = {"opinions": 0, "debts": 0, "secret_targets": 0}
    for agent in genesis.agents:
        persona = agent.persona
        if persona is None:
            continue
        absent = [o for o in persona.opinions if o.target_agent_id not in present]
        if absent:
            counts["opinions"] += len(absent)
            counts["debts"] += sum(1 for o in absent if o.kind == "debt")
            persona.opinions = [o for o in persona.opinions if o.target_agent_id in present]
        if persona.secret_target_agent_id and persona.secret_target_agent_id not in present:
            persona.secret_target_agent_id = None
            counts["secret_targets"] += 1
    if any(counts.values()):
        logger.info(
            "subset cast: removed %d opinion(s) about people not in this world "
            "(%d of them debts) and %d secret target(s)",
            counts["opinions"], counts["debts"], counts["secret_targets"],
        )
    return counts


def engine_failure_detail(exc: Exception) -> str:
    """Everything a failed /tick told us, in one line.

    WHAT WAS BEING THROWN AWAY. A 500 from an engine arrives with the
    reason in its RESPONSE BODY - FastAPI puts it there - and `str(exc)`
    for an httpx.HTTPStatusError is a sentence about the status code and
    the URL. So the runner logged

        tick dispatch failed for n08: HTTPStatusError: Server error
        '500 Internal Server Error' for url 'http://127.0.0.1:9107/tick'

    once per failing tick, and the one fact worth having crossed the wire,
    arrived, and was dropped one line short of the log. It is why an error
    dashboard can show three hundred rows with no cause attached to any of
    them.

    The CLASS is kept for the other half of the same problem, which this
    handler already learned once: httpx timeout errors carry no message,
    so the line read "tick dispatch failed for n07: " with a blank where
    the reason should be, and which of ReadTimeout, ConnectTimeout or
    RemoteProtocolError it was is the whole diagnosis.

    Never raises. It runs inside the except block for a tick that has
    already gone wrong, and a failure to READ a failure would replace a
    diagnosis with a second, less interesting one.
    """
    parts = [type(exc).__name__]
    message = str(exc).strip()
    if message:
        parts.append(message)

    response = getattr(exc, "response", None)
    body = ""
    if response is not None:
        try:
            body = (response.text or "").strip()
        except Exception:  # noqa: BLE001 - see the docstring: never raise here
            body = "<response body could not be read>"
    if body:
        if len(body) > ENGINE_ERROR_DETAIL_CHARS:
            body = f"{body[:ENGINE_ERROR_DETAIL_CHARS]} ... (truncated)"
        parts.append(f"engine said: {body}")
    return ": ".join(parts)

def household_members(world: Any, agent_id: str) -> "tuple[str, list[Any]] | None":
    """(household id, alive members) - the billing rule, in one place.

    ECON-31 made a household-less resident a household of one for billing.
    A function of the world rather than a runner method, so every caller of
    `todays_obligations` - including a runner built from only the two
    attributes that method reads - gets the same answer. None for a
    resident who is gone, or a world that is not there.
    """
    if world is None:
        return None
    agent = world.agents.get(agent_id)
    if agent is None or not agent.alive:
        return None
    if agent.household_id:
        return agent.household_id, [
            other
            for other in world.agents.values()
            if other.alive and other.household_id == agent.household_id
        ]
    return f"solo:{agent_id}", [agent]


DEFAULT_CHECKPOINT_DIR = "data/kasukabe-checkpoints"
DEFAULT_BUDGET_CAP_CENTS = 40_000

BARREN_TICKS_BEFORE_STOP = int(os.environ.get("KASUKABE_BARREN_TICKS", "20"))

DEFAULT_CALLS_PER_TURN = 5.0

BARREN_SECONDS_BEFORE_STOP = float(
    os.environ.get("KASUKABE_BARREN_SECONDS", "1200")
)
DEFAULT_GENESIS_PATH = Path(__file__).resolve().parent / "genesis.json"
DEFAULT_ENGINE_REPO = Path(__file__).resolve().parents[2] / "engine"

BROKER_TERMINAL_STATES = frozenset({"halted"})


def terminal_reason_from_spend(payload: Any) -> "str | None":
    """Why this run cannot continue, read from the broker's GET /admin/spend.

    The barren-tick guard already stops a run where nothing is thinking, and
    it works - but it counts TICKS, and one tick is every agent. Measured on
    the verification run: twenty barren ticks came to sixty-nine refused
    calls, against Build Spec v2's threshold 9.7 of twenty. Round 1 was
    worse by two orders of magnitude, refusing 5,155 calls against a cap it
    had already reached.

    The difference this function draws is between a failure that waiting
    fixes and one it does not. A throttle, a dropped connection, an engine
    coming back up: tick again, and the barren guard is the right net. A
    reached spend cap: no amount of ticking makes money reappear, and every
    further dispatch is a guaranteed refusal that still costs a round trip
    and a trace row.

    Returns None for everything except an explicit halt, and that
    asymmetry is deliberate. A missing broker, a malformed body, a state
    string this runner has never seen: all mean "I could not tell", and
    stopping a healthy run on a failed probe is a worse defect than the one
    being fixed here.
    """
    if not isinstance(payload, dict):
        return None
    state = payload.get("state")
    if not isinstance(state, str) or state.lower() not in BROKER_TERMINAL_STATES:
        return None
    spent = payload.get("spent_usd")
    cap = payload.get("cap_usd")
    numbers = ""
    if isinstance(spent, (int, float)) and isinstance(cap, (int, float)):
        numbers = f" (${spent:.2f} spent against a ${cap:.2f} cap)"
    return (
        f"the broker has halted dispatch{numbers}. Every further call would be "
        "refused, so the run is stopping rather than paying for refusals. "
        "Raise SPEND_CAP_USD and restart, or investigate if this is early."
    )


class ConfigError(ValueError):
    """Aggregated configuration failure, matching engine/src/config.py's
    ConfigError and every other config module in this repo (broker/config.py,
    supabase/config.py) - every problem reported at once, per
    app/CONVENTIONS.md."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(f"invalid genesis-runner configuration ({len(self.errors)} error(s)):\n  - {joined}")


def await_nothing(supervisor: Any) -> list[str]:
    """Unit ids the supervisor knows, without awaiting.

    `list()` is async on the Supervisor protocol; this reads the ids the
    process backend already holds so the output accessor can stay sync and
    be called from a route without another await.
    """
    return sorted(getattr(supervisor, "statuses", {}) or {})


def to_jsonable(value: Any) -> Any:
    """Recursively converts pydantic models, dataclasses, and enums into
    plain JSON-safe data (str/int/float/bool/None/dict/list)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: to_jsonable(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in value]
    return value


def _run_id() -> str:
    """The id this process already minted, so the manifest and the trace
    agree. agents_api mints it at import; reading it here rather than
    minting a second one is the whole point."""
    try:
        from world.agents_api import RUN_ID

        return RUN_ID
    except Exception:  # noqa: BLE001 - a manifest must not stop a boot
        return f"{int(time.time())}"


def _git_sha() -> str | None:
    """The commit this run's code came from, or None if it cannot be read."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _sha256_of(path: "Path | str | None") -> str | None:
    """The genesis file's digest - what cast this run actually used."""
    if not path:
        return None
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


RECORDED_EVENT_DOMAINS = frozenset(
    {"economy", "politics", "business", "health", "crime", "broker"}
)

RECORDED_EVENT_TYPES = frozenset(
    {
        "agent.goal.set",
        "agent.goal.progressed",
        "agent.goal.retired",
        "agent.goal.superseded",
        "agent.belief.updated",
        "agent.intention.set",
        "agent.intention.dropped",
        "system.scenario.set",
    }
)

NOISY_EVENT_TYPES = frozenset(
    {
        "agent.moved",
        "agent.perceived",
    }
)


DEBT_OWER_ROLES = frozenset({"owing", "ower", "borrower", "debtor"})
DEBT_LENDER_ROLES = frozenset({"owed", "lender", "creditor"})


WIRE_LOSS_ALARM = 0.01


PUBLISHED_BY_TYPE: dict[str, int] = {}
RECORDED_BY_TYPE: dict[str, int] = {}


def is_worth_recording(event_type: str) -> bool:
    """True when this event type earns a row in `events` (EVAL-02)."""
    if event_type in NOISY_EVENT_TYPES:
        return False
    if event_type in RECORDED_EVENT_TYPES:
        return True
    domain = event_type.split(".", 1)[0]
    return domain in RECORDED_EVENT_DOMAINS


@dataclass
class GenesisRunnerConfig:
    """Every knob this runner needs, env-driven with aggregated validation
    (matching broker/config.py's BrokerSettings and supabase/config.py's
    SupabaseConfig, both cited by app/CONVENTIONS.md as the pattern)."""

    mock_mode: bool = False
    population_seed: int = 2026
    genesis_path: Path = DEFAULT_GENESIS_PATH
    checkpoint_dir: Path = Path(DEFAULT_CHECKPOINT_DIR)
    budget_cap_cents: int = DEFAULT_BUDGET_CAP_CENTS
    retrieval_depth_fraction: float = 0.0

    world_mode: str = "embedded"
    world_host: str = DEFAULT_WORLD_HOST
    world_port: int = DEFAULT_WORLD_PORT
    world_base_url_override: str = ""

    broker_base_url: str = DEFAULT_BROKER_BASE_URL
    broker_api_key: str = DEFAULT_BROKER_API_KEY

    engine_repo: Path = DEFAULT_ENGINE_REPO
    home_root: str = DEFAULT_HOME_ROOT
    sim_agent_port_base: int = DEFAULT_SIM_AGENT_PORT_BASE

    supervisor_backend: str = "process"
    opening: str = "homes"
    engine_ready_timeout_s: float = 120.0
    watchdog_poll_interval_s: float = 10.0
    watchdog_silence_timeout_s: float = 180.0
    checkpoint_every_day: bool = True
    max_game_days: int | None = None
    max_ticks: int | None = None
    start_at_dawn: bool = False
    scenario_path: str | None = None
    start_tick: int = 0
    resume_at_tick: int | None = None
    resource_log_path: Path | None = None
    tick_timeout_s: float = 120.0
    veto_window_ticks: int = 0
    scheduler_target_fraction: float = 0.3
    tick_interval_s: float = 0.0

    supabase_bucket: str = "world-snapshots"

    def world_base_url(self) -> str:
        if self.world_base_url_override:
            return self.world_base_url_override
        return f"http://{self.world_host}:{self.world_port}"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.world_mode not in ("embedded", "external"):
            errors.append(f"world_mode must be 'embedded' or 'external', got {self.world_mode!r}")
        elif self.world_mode == "external":
            errors.append(
                "world_mode='external' points the engines at world/main.py, "
                "which does not serve the engine contract (/tools/registry, "
                "/ledger/payer, /ledger/debit). Use 'embedded' (CORE-27)."
            )
        if self.supervisor_backend not in ("namespace", "process", "firecracker"):
            errors.append(
                f"supervisor_backend must be 'namespace', 'process' or "
                f"'firecracker', got "
                f"{self.supervisor_backend!r}"
            )
        if self.supervisor_backend == "namespace" and not self.mock_mode:
            errors.append(
                "supervisor_backend='namespace' cannot host an engine: its "
                "gateway does not implement the tick contract the runner "
                "calls, and the backend is documented as unable to run one "
                "(CORE-20). Use 'process'."
            )
        if self.resume_at_tick is not None and self.resume_at_tick < 0:
            errors.append(f"resume_at_tick must be >= 0, got {self.resume_at_tick}")
        if self.budget_cap_cents <= 0:
            errors.append(f"budget_cap_cents must be > 0, got {self.budget_cap_cents}")
        if self.budget_cap_cents > TOTAL_USD_CENTS:
            errors.append(
                f"budget_cap_cents={self.budget_cap_cents} exceeds the system's entire "
                f"${TOTAL_USD_CENTS / 100:.2f} ledger (DESIGN.md section 3)"
            )
        if not (0.0 <= self.retrieval_depth_fraction <= 1.0):
            errors.append(
                f"retrieval_depth_fraction must be in [0, 1], got {self.retrieval_depth_fraction}"
            )
        if self.tick_timeout_s <= 0:
            errors.append(f"tick_timeout_s must be > 0, got {self.tick_timeout_s}")
        if self.tick_interval_s < 0:
            errors.append(f"tick_interval_s must be >= 0, got {self.tick_interval_s}")
        if not (0 < self.scheduler_target_fraction <= 1):
            errors.append(
                f"scheduler_target_fraction must be in (0, 1], got {self.scheduler_target_fraction}"
            )
        if self.max_game_days is not None and self.max_game_days <= 0:
            errors.append(f"max_game_days must be > 0 if set, got {self.max_game_days}")
        if self.start_tick < 0:
            errors.append(f"start_tick must be >= 0, got {self.start_tick}")
        if self.start_tick and self.max_ticks is not None and self.max_ticks <= self.start_tick:
            errors.append(
                f"max_ticks={self.max_ticks} is not after start_tick={self.start_tick}: "
                "the run would stop before it began"
            )
        return errors

    def effective_start_tick(self) -> int:
        """The tick a FRESH town's clock starts on.

        `start_tick` wins when a scenario set one; otherwise the old two
        choices, midnight or dawn. A resumed run ignores both and takes the
        checkpoint's tick (or `resume_at_tick`).
        """
        if self.start_tick:
            return self.start_tick
        return DAWN_TICK if self.start_at_dawn else 0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GenesisRunnerConfig":
        source = env if env is not None else os.environ
        kwargs: dict[str, Any] = {}

        def get(name: str) -> str | None:
            return source.get(name)

        if get("KASUKABE_MOCK_MODE") is not None:
            raise ValueError(
                "KASUKABE_MOCK_MODE is no longer supported - the runner only "
                "spawns real engines. Remove it from the environment."
            )
        if (raw := get("KASUKABE_GENESIS_SEED")) is not None:
            kwargs["population_seed"] = int(raw)
        if (raw := get("KASUKABE_GENESIS_PATH")) is not None:
            kwargs["genesis_path"] = Path(raw)
        if (raw := get("KASUKABE_CHECKPOINT_DIR")) is not None:
            kwargs["checkpoint_dir"] = Path(raw)
        if (raw := get("KASUKABE_BUDGET_CAP_CENTS")) is not None:
            kwargs["budget_cap_cents"] = int(raw)
        elif (raw := get("SPEND_CAP_USD")) is not None:
            try:
                broker_cap = int(round(float(raw) * 100))
                kwargs["budget_cap_cents"] = max(1, broker_cap - halt_reserve_cents(broker_cap))
            except ValueError:
                pass
        if (raw := get("KASUKABE_RETRIEVAL_DEPTH")) is not None:
            try:
                kwargs["retrieval_depth_fraction"] = float(raw)
            except ValueError:
                kwargs["retrieval_depth_fraction"] = 0.0
        if (raw := get("KASUKABE_WORLD_MODE")) is not None:
            kwargs["world_mode"] = raw.strip()
        if (raw := get("KASUKABE_WORLD_HOST")) is not None:
            kwargs["world_host"] = raw.strip()
        if (raw := get("KASUKABE_WORLD_PORT")) is not None:
            kwargs["world_port"] = int(raw)
        if (raw := get("KASUKABE_WORLD_BASE_URL")) is not None:
            kwargs["world_base_url_override"] = raw.strip()
        if (raw := get("KASUKABE_BROKER_BASE_URL")) is not None:
            kwargs["broker_base_url"] = raw.strip()
        if (raw := get("KASUKABE_BROKER_API_KEY")) is not None:
            kwargs["broker_api_key"] = raw
        if (raw := get("KASUKABE_ENGINE_REPO")) is not None:
            kwargs["engine_repo"] = Path(raw)
        if (raw := get("KASUKABE_HOME_ROOT")) is not None:
            kwargs["home_root"] = raw.strip()
        if (raw := get("KASUKABE_SIM_AGENT_PORT_BASE")) is not None:
            kwargs["sim_agent_port_base"] = int(raw)
        if (raw := get("KASUKABE_TICK_INTERVAL_S")) is not None:
            kwargs["tick_interval_s"] = float(raw)
        if (raw := get("KASUKABE_ENGINE_READY_TIMEOUT_S")) is not None:
            kwargs["engine_ready_timeout_s"] = float(raw)
        if (raw := get("KASUKABE_SUPERVISOR_BACKEND")) is not None:
            kwargs["supervisor_backend"] = raw.strip()
        if (raw := get("KASUKABE_CHECKPOINT_EVERY_DAY")) is not None:
            kwargs["checkpoint_every_day"] = raw.strip().lower() == "true"
        if (raw := get("KASUKABE_MAX_GAME_DAYS")) is not None:
            kwargs["max_game_days"] = int(raw)
        if (raw := get("KASUKABE_SCENARIO")) is not None:
            kwargs["scenario_path"] = raw.strip()

        config = cls(**kwargs)
        errors = config.validate()
        if errors:
            raise ConfigError(errors)
        if config.scenario_path:
            config = apply_scenario(config, config.scenario_path)
        return config


class UnitState(str, Enum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    UNHEALTHY = "unhealthy"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class UnitSpec:
    unit_id: str
    engine_id: str
    cdt_url: str
    cdt_key: str
    extra_env: dict[str, str] = field(default_factory=dict)
    cpu: float = 0.25
    memory_mb: int = 256


@dataclass(frozen=True)
class UnitStatus:
    unit_id: str
    state: UnitState
    backend_handle: str
    started_at: float | None
    last_health_check_at: float | None
    detail: str = ""

    @property
    def is_alive(self) -> bool:
        return self.state in (UnitState.STARTING, UnitState.RUNNING, UnitState.UNHEALTHY)


class NamespaceAgentGateway:
    """Delivers a tick to an in-process engine.

    The counterpart to HttpAgentGateway. That one POSTs to a port per agent;
    with the namespace backend the engine object is already in hand, so a tick
    is a direct await - no socket, no serialisation, and no port range to
    collide with anything else on the machine.
    """

    def __init__(self, supervisor: Any) -> None:
        self.supervisor = supervisor

    async def send_tick(
        self,
        agent_id: str,
        *,
        tick: int,
        is_sleeping: bool,
        perception: str,
        call_budget: int,
        game_day: int | None = None,
    ) -> Any:
        """The gateway contract, so a future reviver gets a sentence.

        CORE-20. This class defined `tick(agent_id, perception_text)` while
        the runner calls `send_tick(agent_id, tick=, is_sleeping=,
        perception=, call_budget=)`, so the namespace backend raised
        AttributeError on its first due agent - after genesis, after the
        engines were spawned, after the money for all of that was spent.
        `GenesisRunnerConfig.validate` now refuses the backend outright,
        which is the real fix.

        The signature is corrected anyway, and raises rather than
        forwarding: whoever revives this backend should be told what is
        missing, not handed an AttributeError three layers down. Forwarding
        to `supervisor.send_tick(unit, perception)` would ALSO be wrong -
        it drops `is_sleeping` and `call_budget`, so the engine would think
        through the night and ignore its per-tick call budget.
        """
        raise SupervisorError(
            "the namespace backend cannot host an engine (CORE-20). Its "
            "supervisor takes only a perception string, so `is_sleeping` "
            "and `call_budget` have nowhere to go - a tick delivered this "
            "way would think through the night and ignore its call budget. "
            "Use --supervisor-backend process."
        )


class SupervisorError(Exception):
    """Base class for every error a Supervisor-shaped backend can raise."""


class UnknownUnitError(SupervisorError):
    pass


class UnitAlreadyExistsError(SupervisorError):
    pass


@runtime_checkable
class Supervisor(Protocol):
    """Structurally identical to infra/firecracker/supervisor.py's own
    Supervisor protocol, deliberately not imported from it (module
    docstring point 3). Any object satisfying this shape - this file's own
    ProcessSupervisor/MockSupervisor, or an infra/ script's
    VMManager-backed adapter - can be handed to GenesisRunner."""

    async def start(self, spec: UnitSpec) -> UnitStatus: ...

    async def stop(self, unit_id: str, timeout_s: float = 10.0) -> UnitStatus: ...

    async def restart(self, unit_id: str) -> UnitStatus: ...

    async def status(self, unit_id: str) -> UnitStatus: ...

    async def list(self) -> list[UnitStatus]: ...

    async def health(self, unit_id: str) -> bool: ...

    async def shutdown_all(self, timeout_s: float = 30.0) -> None: ...


@dataclass
class RestartPolicy:
    """How hard, and how often, the watchdog is willing to try.

    A unit that fails on startup fails again the moment it comes back, so
    an unthrottled loop turns one bad config into a restart storm. Every
    restart is spaced by exponential backoff, and a unit that burns through
    `max_restarts` is given up on and reported rather than retried forever.
    """

    max_restarts: int = 5
    initial_backoff_s: float = 2.0
    max_backoff_s: float = 60.0
    backoff_factor: float = 2.0

    def backoff_for(self, attempt: int) -> float:
        delay = self.initial_backoff_s * (self.backoff_factor ** max(0, attempt - 1))
        return min(delay, self.max_backoff_s)


@dataclass
class UnitWatchRecord:
    unit_id: str
    restarts: int = 0
    last_restart_at: float | None = None
    given_up: bool = False
    last_error: str = ""


class SupervisorWatchdog:
    """Restarts engines that have died, without waiting for a person.

    Two conditions count, because section 33 names both: a unit that
    reported FAILED, and a unit that still claims RUNNING but whose
    `last_health_check_at` has gone stale. The second is the one that
    matters - a wedged engine looks perfectly healthy to anything that only
    reads `state`. UNHEALTHY is deliberately not restarted on sight: that is
    what a unit looks like mid-boot or during a slow broker call.
    """

    def __init__(
        self,
        supervisor: Supervisor,
        *,
        poll_interval_s: float = 10.0,
        silence_timeout_s: float = 180.0,
        policy: RestartPolicy | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.supervisor = supervisor
        self.poll_interval_s = poll_interval_s
        self.silence_timeout_s = silence_timeout_s
        self.policy = policy or RestartPolicy()
        self.clock = clock
        self.records: dict[str, UnitWatchRecord] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    def needs_restart(self, status: UnitStatus) -> str | None:
        """States are compared by VALUE, never identity - see B1's copy for
        the full reasoning; two structurally-identical str-Enums are never
        `is`-equal, and that difference silently disables a watchdog."""
        if status.state == UnitState.FAILED:
            return "reported FAILED"
        if status.state in (UnitState.STOPPED, UnitState.STOPPING, UnitState.PENDING):
            return None
        if status.last_health_check_at is None:
            return None
        silent_for = self.clock() - status.last_health_check_at
        if silent_for > self.silence_timeout_s:
            return f"silent for {silent_for:.0f}s (>{self.silence_timeout_s:.0f}s)"
        return None

    async def check_once(self) -> list[str]:
        """Never raises: a watchdog that dies on a backend hiccup is worse
        than none, because the system then looks supervised and is not."""
        try:
            statuses = await self.supervisor.list()
        except Exception as exc:                       # noqa: BLE001
            logger.warning("watchdog could not list units: %s", exc)
            return []

        for status in statuses:
            if status.state is not UnitState.RUNNING:
                continue
            try:
                await self.supervisor.health(status.unit_id)
            except Exception as exc:  # noqa: BLE001 - see this method's own
                logger.debug("health probe failed for %s: %s", status.unit_id, exc)

        try:
            statuses = await self.supervisor.list()
        except Exception as exc:                       # noqa: BLE001
            logger.warning("watchdog could not re-list units: %s", exc)
            return []

        restarted: list[str] = []
        now = self.clock()
        for status in statuses:
            reason = self.needs_restart(status)
            record = self.records.setdefault(status.unit_id, UnitWatchRecord(status.unit_id))
            if reason is None:
                if record.restarts and status.state == UnitState.RUNNING:
                    record.restarts = 0
                    record.last_error = ""
                continue
            if record.given_up:
                continue
            if record.restarts >= self.policy.max_restarts:
                record.given_up = True
                record.last_error = record.last_error or reason
                logger.error(
                    "watchdog giving up on %s after %d restarts: %s",
                    status.unit_id, record.restarts, reason,
                )
                continue
            if record.last_restart_at is not None:
                if now - record.last_restart_at < self.policy.backoff_for(record.restarts):
                    continue
            logger.warning("watchdog restarting %s: %s", status.unit_id, reason)
            try:
                await self.supervisor.restart(status.unit_id)
            except Exception as exc:                   # noqa: BLE001
                record.last_error = f"restart failed: {exc}"
                logger.error("watchdog restart of %s failed: %s", status.unit_id, exc)
                record.restarts += 1
                record.last_restart_at = now
                continue
            record.restarts += 1
            record.last_restart_at = now
            record.last_error = reason
            restarted.append(status.unit_id)
        return restarted

    async def run_forever(self) -> None:
        self._stopping.clear()
        while not self._stopping.is_set():
            await self.check_once()
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_interval_s)
            except asyncio.TimeoutError:
                continue

    def start(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run_forever(), name="supervisor-watchdog")
        return self._task

    async def stop(self) -> None:
        self._stopping.set()
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=self.poll_interval_s + 5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()


CommandBuilder = Callable[[UnitSpec], list[str]]


def default_engine_command_builder(engine_repo: Path) -> CommandBuilder:
    """Builds `python -m src.sim.sim_main` against the engine repo's own
    venv when one exists (.venv/bin/python on POSIX, .venv/Scripts/
    python.exe on Windows), falling back to a interpreter that is certain
    to exist so this never raises before a unit is actually started -
    only ProcessSupervisor.start() needs this to resolve, and only in
    real (non-mock) mode."""
    if os.name == "nt":
        venv_python = engine_repo / ".venv" / "Scripts" / "python.exe"
        fallback = sys.executable
    else:
        venv_python = engine_repo / ".venv" / "bin" / "python"
        fallback = "python3"
    interpreter = str(venv_python) if venv_python.exists() else fallback

    def build(spec: UnitSpec) -> list[str]:
        return [interpreter, "-m", "src.sim.sim_main"]

    return build


class ProcessSupervisor:
    """Supervisor backed by plain `asyncio.create_subprocess_exec` OS
    processes - the "brings the sim up first on plain supervised OS
    processes" backend infra/firecracker/supervisor.py's own docstring
    describes as this track's responsibility.

    `command_builder`/`cwd`/`extra_process_env` are injected rather than
    hardcoded to `python -m src.sim.sim_main` so this class is fully unit-
    testable with a harmless trivial command (see world/tests/test_run_genesis.py)
    without needing a real engine/ checkout or its dependencies present.
    """

    def __init__(
        self,
        command_builder: CommandBuilder,
        cwd: Path,
        extra_process_env: Mapping[str, str] | None = None,
        startup_grace_s: float = 0.05,
        health_timeout_s: float = 2.0,
    ) -> None:
        self.command_builder = command_builder
        self.cwd = cwd
        self.extra_process_env = dict(extra_process_env or {})
        self.startup_grace_s = startup_grace_s
        self.health_timeout_s = health_timeout_s
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.statuses: dict[str, UnitStatus] = {}
        self.output_tails: dict[str, deque[str]] = {}
        self.drain_tasks: dict[str, asyncio.Task[None]] = {}
        self.specs: dict[str, UnitSpec] = {}

    async def drain_output(self, unit_id: str, process: asyncio.subprocess.Process) -> None:
        """Continuously read one unit's merged stdout/stderr.

        This exists for CORRECTNESS, not for logging. start() creates every
        unit with stdout=PIPE and stderr=STDOUT, and a pipe nobody reads
        fills at the OS buffer limit (~64KB on Linux) and then blocks the
        writer forever. A September engine logs well past that while
        booting, so without this drain the engines wedge mid-startup, never
        bind their /tick port, and the only thing the runner can report is
        "tick dispatch failed" for every agent - with the actual reason
        sitting unread in the pipe. That is precisely the symptom this path
        had.

        The tail is bounded. 250 engines logging freely for hours would
        otherwise be a memory leak the length of the run, and a post-mortem
        only ever wants the last few lines anyway.
        """
        if process.stdout is None:  # pragma: no cover - start() always pipes
            return
        tail = self.output_tails.setdefault(unit_id, deque(maxlen=ENGINE_OUTPUT_TAIL_LINES))
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                tail.append(line.decode("utf-8", errors="replace").rstrip())
            await process.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a broken pipe must not kill the runner
            tail.append(f"<output drain stopped: {type(exc).__name__}: {exc}>")

    def recent_output(self, unit_id: str, limit: int = ENGINE_OUTPUT_TAIL_LINES) -> list[str]:
        """The last few lines this unit printed. Empty if it printed nothing."""
        return list(self.output_tails.get(unit_id, ()))[-limit:]

    async def start(self, spec: UnitSpec) -> UnitStatus:
        if spec.unit_id in self.processes and self.statuses[spec.unit_id].is_alive:
            raise UnitAlreadyExistsError(f"unit {spec.unit_id!r} is already running")

        env = {**os.environ, **self.extra_process_env, **spec.extra_env}
        env["CDT_URL"] = spec.cdt_url
        env["CDT_KEY"] = spec.cdt_key
        command = self.command_builder(spec)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (OSError, FileNotFoundError) as exc:
            status = UnitStatus(
                unit_id=spec.unit_id,
                state=UnitState.FAILED,
                backend_handle="",
                started_at=time.time(),
                last_health_check_at=None,
                detail=f"failed to launch: {exc}",
            )
            self.statuses[spec.unit_id] = status
            return status

        self.output_tails[spec.unit_id] = deque(maxlen=ENGINE_OUTPUT_TAIL_LINES)
        self.drain_tasks[spec.unit_id] = asyncio.create_task(
            self.drain_output(spec.unit_id, process)
        )

        await asyncio.sleep(self.startup_grace_s)
        self.processes[spec.unit_id] = process
        self.specs[spec.unit_id] = spec
        alive = process.returncode is None
        status = UnitStatus(
            unit_id=spec.unit_id,
            state=UnitState.RUNNING if alive else UnitState.FAILED,
            backend_handle=str(process.pid),
            started_at=time.time(),
            last_health_check_at=None,
            detail=(
                ""
                if alive
                else f"exited immediately with code {process.returncode}"
                + (
                    f" - last output: {' | '.join(self.recent_output(spec.unit_id, 5))}"
                    if self.recent_output(spec.unit_id, 5)
                    else " - no output captured"
                )
            ),
        )
        self.statuses[spec.unit_id] = status
        return status

    async def stop(self, unit_id: str, timeout_s: float = 10.0) -> UnitStatus:
        process = self.processes.get(unit_id)
        if process is None:
            raise UnknownUnitError(f"no unit {unit_id!r} has ever been started")
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        status = UnitStatus(
            unit_id=unit_id,
            state=UnitState.STOPPED,
            backend_handle=str(process.pid),
            started_at=self.statuses[unit_id].started_at,
            last_health_check_at=self.statuses[unit_id].last_health_check_at,
        )
        self.statuses[unit_id] = status
        return status

    async def restart(self, unit_id: str) -> UnitStatus:
        """Stop (if still up) and start again from the same spec.

        Deliberately not `stop()` then `start()` by the caller: start()
        raises UnitAlreadyExistsError on a live unit, so a caller doing it
        by hand has to race the process's own exit to get the ordering
        right. Doing it here makes unit_id a reliable idempotency key, which
        is what the watchdog depends on.
        """
        spec = self.specs.get(unit_id)
        if spec is None:
            raise UnknownUnitError(f"no unit {unit_id!r} has ever been started")
        process = self.processes.get(unit_id)
        if process is not None and process.returncode is None:
            await self.stop(unit_id)
        self.processes.pop(unit_id, None)
        return await self.start(spec)

    async def status(self, unit_id: str) -> UnitStatus:
        if unit_id not in self.statuses:
            raise UnknownUnitError(f"no unit {unit_id!r} has ever been started")
        process = self.processes.get(unit_id)
        current = self.statuses[unit_id]
        if process is not None and current.state is UnitState.RUNNING and process.returncode is not None:
            current = dataclasses.replace(
                current, state=UnitState.FAILED, detail=f"exited with code {process.returncode}"
            )
            self.statuses[unit_id] = current
        return current

    async def list(self) -> list[UnitStatus]:
        return [await self.status(unit_id) for unit_id in self.statuses]

    async def health(self, unit_id: str) -> bool:
        """Ask the engine, and record that it answered.

        CORE-18. This used to return `state is RUNNING`, which is what the
        caller already had and says nothing about whether the process is
        still doing anything. A wedged engine - alive, holding its
        `_turn_lock`, answering nothing - is RUNNING by that definition
        forever, and it is the exact failure the watchdog was written for.

        Stamping `last_health_check_at` is the load-bearing half: it is the
        only writer of the field `SupervisorWatchdog.needs_restart` reads,
        so without this the silence arm is unreachable code.
        """
        status = await self.status(unit_id)
        if status.state is not UnitState.RUNNING:
            return False

        spec = self.specs.get(unit_id)
        port = (spec.extra_env.get("SIM_AGENT_PORT") if spec else None)
        if not port:
            return True

        try:
            async with httpx.AsyncClient(timeout=self.health_timeout_s) as client:
                response = await client.get(f"http://127.0.0.1:{port}/health")
            answered = response.status_code == 200
        except Exception:  # noqa: BLE001 - an unreachable engine IS the signal
            answered = False

        if answered:
            self.statuses[unit_id] = dataclasses.replace(
                status, last_health_check_at=time.time()
            )
        return answered

    async def shutdown_all(self, timeout_s: float = 30.0) -> None:
        await asyncio.gather(
            *(self.stop(unit_id, timeout_s=timeout_s) for unit_id in list(self.processes)),
            return_exceptions=True,
        )


class MockSupervisor:
    """Dry-run Supervisor: no OS process, no network - a unit is "running"
    the instant start() returns. Pairs with MockAgentGateway so the full
    genesis-runner loop (scheduling, checkpointing, metrics, spend
    governor) runs deterministically with zero spend and zero external
    dependencies, per CONVENTIONS.md's "Stub or mock inference."""

    def __init__(self) -> None:
        self.statuses: dict[str, UnitStatus] = {}
        self.specs: dict[str, UnitSpec] = {}
        self.restarts: list[str] = []

    async def start(self, spec: UnitSpec) -> UnitStatus:
        if spec.unit_id in self.statuses and self.statuses[spec.unit_id].is_alive:
            raise UnitAlreadyExistsError(f"unit {spec.unit_id!r} is already running")
        status = UnitStatus(
            unit_id=spec.unit_id,
            state=UnitState.RUNNING,
            backend_handle=f"mock:{spec.unit_id}",
            started_at=time.time(),
            last_health_check_at=time.time(),
        )
        self.statuses[spec.unit_id] = status
        self.specs[spec.unit_id] = spec
        return status

    async def restart(self, unit_id: str) -> UnitStatus:
        spec = self.specs.get(unit_id)
        if spec is None:
            raise UnknownUnitError(f"no unit {unit_id!r} has ever been started")
        self.restarts.append(unit_id)
        self.statuses.pop(unit_id, None)
        return await self.start(spec)

    async def stop(self, unit_id: str, timeout_s: float = 10.0) -> UnitStatus:
        if unit_id not in self.statuses:
            raise UnknownUnitError(f"no unit {unit_id!r} has ever been started")
        status = dataclasses.replace(self.statuses[unit_id], state=UnitState.STOPPED)
        self.statuses[unit_id] = status
        return status

    async def status(self, unit_id: str) -> UnitStatus:
        if unit_id not in self.statuses:
            raise UnknownUnitError(f"no unit {unit_id!r} has ever been started")
        return self.statuses[unit_id]

    async def list(self) -> list[UnitStatus]:
        return list(self.statuses.values())

    async def health(self, unit_id: str) -> bool:
        return (await self.status(unit_id)).state is UnitState.RUNNING

    async def shutdown_all(self, timeout_s: float = 30.0) -> None:
        for unit_id in list(self.statuses):
            await self.stop(unit_id, timeout_s=timeout_s)


@dataclass(frozen=True)
class TickOutcome:
    agent_id: str
    reply: str
    learning_ran: bool
    is_sleeping: bool
    input_tokens: int
    output_tokens: int
    cost_usd_cents: int
    succeeded: bool
    error: str = ""
    steps_used: int = 0
    budget_hit: bool = False
    reflected: bool = False
    goal_revisions: int = 0
    turn_in_flight: bool = False


class AgentGateway(Protocol):
    async def send_tick(
        self,
        agent_id: str,
        tick: int,
        is_sleeping: bool,
        perception: str,
        call_budget: int | None = None,
        game_day: int | None = None,
    ) -> TickOutcome: ...

    async def drain_learning(self) -> dict[str, bool]: ...

    async def aclose(self) -> None: ...


class HttpAgentGateway:
    """Real gateway: POSTs to each agent's own /tick receiver
    (engine/src/sim/agent_adapter.py's TickRequest/TickResponse contract),
    one httpx.AsyncClient per agent since each is its own process on its
    own port (sim_agent_port_base + index, see GenesisRunner.boot()).

    Token/cost accounting is NOT read off the HTTP response - TickResponse
    carries no cost fields (agent_adapter.py's shape has none) because the
    real cost lands through broker's own ledger debit
    (broker/ledger.py -> this runner's /ledger/debit route, see
    build_embedded_world_app()) independently of this call returning.
    HttpAgentGateway therefore reports 0 for input/output tokens and cost
    here; GenesisMetrics' real spend figure comes from the ledger's burned
    total, not from this return value - see GenesisMetrics.
    """

    def __init__(
        self,
        ports_by_agent: Mapping[str, int],
        timeout_s: float = 30.0,
        output_tail: "Callable[[str], list[str]] | None" = None,
    ) -> None:
        self.ports_by_agent = dict(ports_by_agent)
        self.timeout_s = timeout_s
        self.clients: dict[str, httpx.AsyncClient] = {}
        self.output_tail = output_tail

    def engine_output_on_failure(self, agent_id: str, exc: Exception) -> str:
        """The engine's own last lines, for a 5xx. Empty otherwise. Never raises."""
        if self.output_tail is None or not isinstance(exc, httpx.HTTPStatusError):
            return ""
        if exc.response.status_code < 500:
            return ""
        try:
            lines = list(self.output_tail(agent_id) or [])
        except Exception:  # noqa: BLE001 - reading a failure must not fail
            return ""
        if not lines:
            return " | the engine printed nothing before failing"
        return " | engine's last output:\n    " + "\n    ".join(lines[-ENGINE_TAIL_ON_FAILURE:])

    def client_for(self, agent_id: str) -> httpx.AsyncClient:
        client = self.clients.get(agent_id)
        if client is None:
            port = self.ports_by_agent[agent_id]
            client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=self.timeout_s)
            self.clients[agent_id] = client
        return client

    async def send_tick(
        self,
        agent_id: str,
        tick: int,
        is_sleeping: bool,
        perception: str,
        call_budget: int | None = None,
        game_day: int | None = None,
    ) -> TickOutcome:
        client = self.client_for(agent_id)
        try:
            resp = await client.post(
                "/tick",
                json={
                    "tick": tick,
                    "is_sleeping": is_sleeping,
                    "perception": perception,
                    "call_budget": call_budget,
                    "game_day": game_day,
                },
            )
            if resp.status_code == 409:
                return TickOutcome(
                    agent_id=agent_id,
                    reply="",
                    learning_ran=False,
                    is_sleeping=is_sleeping,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd_cents=0,
                    succeeded=False,
                    turn_in_flight=True,
                    error="engine is still running the previous turn",
                )
            resp.raise_for_status()
            body = resp.json()
            return TickOutcome(
                agent_id=agent_id,
                reply=body.get("reply", ""),
                learning_ran=bool(body.get("learning_ran", False)),
                is_sleeping=is_sleeping,
                input_tokens=0,
                output_tokens=0,
                cost_usd_cents=0,
                succeeded=True,
                steps_used=int(body.get("steps_used", 0) or 0),
                budget_hit=bool(body.get("budget_hit", False)),
                reflected=bool(body.get("reflected", False)),
                goal_revisions=int(body.get("goal_revisions", 0) or 0),
            )
        except httpx.HTTPError as exc:
            detail = engine_failure_detail(exc)
            logger.warning(
                "tick dispatch failed for %s: %s%s",
                agent_id, detail, self.engine_output_on_failure(agent_id, exc),
            )
            return TickOutcome(
                agent_id=agent_id,
                reply="",
                learning_ran=False,
                is_sleeping=is_sleeping,
                input_tokens=0,
                output_tokens=0,
                cost_usd_cents=0,
                succeeded=False,
                error=detail,
            )

    async def drain_learning(self) -> dict[str, bool]:
        """POST /learn to every engine, once, before they are torn down.

        A run that stops at noon leaves the whole morning's signals sitting
        in each engine's `learning_signals` table with `processed = 0`, and
        the process is killed seconds later - so the last stretch of every
        run this project has made was thrown away unconsolidated. The
        engine has had `trigger_extra()` since the learning gate was
        written and nothing outside a test ever called it.

        Best-effort by construction. An engine that has already died, or
        whose pipeline fails, must not stop the other forty-nine from
        draining or keep the runner from shutting down.
        """
        async def one(agent_id: str) -> tuple[str, bool]:
            try:
                resp = await self.client_for(agent_id).post("/learn", timeout=60.0)
                resp.raise_for_status()
                return agent_id, bool(resp.json().get("learning_ran", False))
            except Exception as exc:  # noqa: BLE001 - see the docstring
                logger.warning(
                    "end-of-run learning drain failed for %s: %s",
                    agent_id, engine_failure_detail(exc),
                )
                return agent_id, False

        pairs = await asyncio.gather(*(one(a) for a in self.ports_by_agent))
        return dict(pairs)

    async def aclose(self) -> None:
        await asyncio.gather(*(c.aclose() for c in self.clients.values()), return_exceptions=True)


class MockAgentGateway:
    """Dry-run gateway: synthesizes a turn's outcome in-process using the
    retrieval-depth cost model (world/retrieval_depth.py), no network, no
    real engine. A sleeping agent costs nothing (matching
    agent_adapter.py's real behavior: is_sleeping short-circuits before
    any cognition turn runs) - this is a real, honest simulation of the
    documented contract's cost shape, not a fake pass; see module
    docstring point 5.
    """

    def __init__(self, depth: RetrievalDepthConfig, rng: random.Random) -> None:
        self.depth = depth
        self.rng = rng

    async def send_tick(
        self,
        agent_id: str,
        tick: int,
        is_sleeping: bool,
        perception: str,
        call_budget: int | None = None,
        game_day: int | None = None,
    ) -> TickOutcome:
        if is_sleeping:
            return TickOutcome(
                agent_id=agent_id,
                reply="",
                learning_ran=self.rng.random() < 0.1,
                is_sleeping=True,
                input_tokens=0,
                output_tokens=0,
                cost_usd_cents=0,
                succeeded=True,
            )
        input_tokens = self.depth.estimated_input_tokens
        output_tokens = 291
        cost_cents = self.depth.estimated_cost_per_call_cents
        return TickOutcome(
            agent_id=agent_id,
            reply=f"[mock turn at tick {tick}, perceived {len(perception)} chars]",
            learning_ran=False,
            is_sleeping=False,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd_cents=cost_cents,
            succeeded=True,
        )

    async def drain_learning(self) -> dict[str, bool]:
        """No engines, so nothing to drain. Present so `shutdown()` can call
        it without asking which gateway it holds - a dry run must exercise
        the same shutdown path a real one does."""
        return {}

    async def aclose(self) -> None:
        return None


class LedgerCheckpointAdapter:
    """Checkpointable wrapper over world.ledger.core.Ledger, which has no
    snapshot()/restore() of its own (module docstring's build path: this
    runner is the first caller that needs the ledger's own state
    round-tripped through a checkpoint, so the adapter lives here rather
    than in ledger/core.py, which this track does not own)."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def snapshot(self) -> dict[str, Any]:
        accounts = {
            account_id: {
                "account_id": account.account_id,
                "kind": account.kind.value,
                "usd_cents": account.usd_cents,
                "sim_units": account.sim_units,
                "is_minor": account.is_minor,
                "parent_id": account.parent_id,
                "closed": account.closed,
            }
            for account_id, account in self.ledger.accounts.items()
        }
        return {
            "accounts": accounts,
            "transactions": [tx.model_dump(mode="json") for tx in self.ledger.transactions],
            "halted": self.ledger.halted,
            "halt_reason": self.ledger.halt_reason,
        }

    def restore(self, data: dict[str, Any]) -> None:
        self.ledger.accounts = {
            account_id: Account(
                account_id=row["account_id"],
                kind=AccountKind(row["kind"]),
                usd_cents=row["usd_cents"],
                sim_units=row["sim_units"],
                is_minor=row["is_minor"],
                parent_id=row["parent_id"],
                closed=row["closed"],
            )
            for account_id, row in data["accounts"].items()
        }
        self.ledger.transactions = [Transaction.model_validate(t) for t in data["transactions"]]
        self.ledger.halted = data["halted"]
        self.ledger.halt_reason = data["halt_reason"]


def classify_world_state_fields() -> dict[str, tuple[str, type[BaseModel] | None]]:
    """Maps every name in world.tools.state.OWNED_STATE_FIELDS to one of
    ("dict_model", ModelType), ("list_model", ModelType), or
    ("plain", None), by inspecting WorldState's real, resolved type hints
    (typing.get_type_hints resolves state.py's `from __future__ import
    annotations` string annotations the same way pydantic/dataclasses
    themselves resolve annotations - not a guess). "plain" covers any
    owned field that isn't a dict/list of exactly one pydantic model -
    today only "skills" (dict[str, dict[str, int]]); JSON-round-trips
    via to_jsonable/plain dict access, same as before.
    """
    hints = typing.get_type_hints(WorldState)
    classified: dict[str, tuple[str, type[BaseModel] | None]] = {}
    for name in OWNED_STATE_FIELDS:
        hint = hints[name]
        origin = typing.get_origin(hint)
        args = typing.get_args(hint)
        if origin is dict and len(args) == 2:
            _, value_type = args
            if isinstance(value_type, type) and issubclass(value_type, BaseModel):
                classified[name] = ("dict_model", value_type)
                continue
        if origin is list and len(args) == 1:
            (item_type,) = args
            if isinstance(item_type, type) and issubclass(item_type, BaseModel):
                classified[name] = ("list_model", item_type)
                continue
        classified[name] = ("plain", None)
    return classified


WORLD_STATE_FIELD_TYPES: dict[str, tuple[str, type[BaseModel] | None]] = classify_world_state_fields()

WORLD_STATE_FIELD_DEFAULTS: dict[str, Callable[[], Any]] = {
    f.name: f.default_factory
    for f in dataclasses.fields(WorldState)
    if f.name in OWNED_STATE_FIELDS
}


class WorldStateCheckpointAdapter:
    """Checkpointable wrapper over world.tools.state.WorldState's owned
    fields, converting to/from plain JSON-safe data (see to_jsonable's
    docstring for why WorldState.snapshot()'s own deepcopy-of-model-
    instances shape is not, by itself, safe to json.dumps).

    Field coverage comes from WORLD_STATE_FIELD_TYPES (derived from
    world.tools.state.OWNED_STATE_FIELDS, see above), not a hand-listed
    table - the STATUS.md risk #3 fix: this adapter now cannot drift from
    WorldState's real owned fields the way the old two-dict version did.
    """

    def __init__(self, world_state: WorldState) -> None:
        self.world_state = world_state

    def snapshot(self) -> dict[str, Any]:
        raw = self.world_state.snapshot()
        data: dict[str, Any] = {}
        for name, (shape, model_type) in WORLD_STATE_FIELD_TYPES.items():
            value = raw[name]
            if shape == "dict_model":
                data[name] = {k: v.model_dump(mode="json") for k, v in value.items()}
            elif shape == "list_model":
                data[name] = [v.model_dump(mode="json") for v in value]
            else:
                data[name] = to_jsonable(value)
        data["inventory"] = {"balances": to_jsonable(raw["inventory"].balances)}
        return data

    def restore(self, data: dict[str, Any]) -> None:
        rebuilt: dict[str, Any] = {}
        for name, (shape, model_type) in WORLD_STATE_FIELD_TYPES.items():
            if shape == "dict_model":
                rebuilt[name] = {k: model_type.model_validate(v) for k, v in data.get(name, {}).items()}
            elif shape == "list_model":
                rebuilt[name] = [model_type.model_validate(v) for v in data.get(name, [])]
            else:
                rebuilt[name] = data[name] if name in data else WORLD_STATE_FIELD_DEFAULTS[name]()
        rebuilt["inventory"] = Inventory(balances=data["inventory"]["balances"])
        self.world_state.restore(rebuilt)


class CrimeStoreCheckpointAdapter:
    """GOV-12. The crime store, across a resume.

    A thin pass-through, like `LedgerCheckpointAdapter`: the store owns
    the shape and this owns only the name it is filed under. Kept as a
    class rather than registering the store directly so the store never
    has to know a `CheckpointRegistry` exists.
    """

    def __init__(self, store: Any) -> None:
        self.store = store

    def snapshot(self) -> dict[str, Any]:
        return self.store.snapshot()

    def restore(self, data: dict[str, Any]) -> None:
        self.store.restore(data)


class StakesCheckpointAdapter:
    """Hunger, head-tax debt and the day-job ledger.

    CORE-08. Three stores the runner owns and no checkpoint carried:

      * `hunger_by_agent` - the starvation ladder. A resident three days
        into not paying its upkeep came back FED after a resume, so the
        ladder that ends in dormancy and death could never complete across
        a restart, and the roadmap's multi-day runs on spot hardware are
        exactly where it must.
      * `head_tax_debts` - `HeadTaxDebtStore` has had `snapshot`/`restore`
        since it was written and was never registered, so a resume emptied
        it while `world.arrears` (which IS owned state) kept the amounts.
        `repay_head_tax_debt` would then raise NoOutstandingDebtError for a
        debt `check_debts` was still showing the resident.
      * `day_jobs_ran` - what upkeep and tax have already been charged for
        today. Lost, a resume re-runs a day boundary it already ran, or
        `assert_the_day_was_charged` fails on a day that was.

    One adapter rather than three, because they are one fact - what this
    town owes and how long it has owed it - and a checkpoint that carried
    two of the three would be a subtler version of the same bug.
    """

    def __init__(self, runner: Any) -> None:
        self.runner = runner

    def snapshot(self) -> dict[str, Any]:
        hunger = {
            agent_id: (state.model_dump() if hasattr(state, "model_dump") else state)
            for agent_id, state in self.runner.hunger_by_agent.items()
        }
        return {
            "hunger_by_agent": hunger,
            "head_tax_debts": self.runner.head_tax_debts.snapshot(),
            "day_jobs_ran": {str(k): v for k, v in self.runner.day_jobs_ran.items()},
        }

    def restore(self, data: dict[str, Any]) -> None:
        from world.life.upkeep import HungerState

        self.runner.hunger_by_agent = {
            agent_id: (HungerState(**state) if isinstance(state, dict) else state)
            for agent_id, state in (data.get("hunger_by_agent") or {}).items()
        }
        if data.get("head_tax_debts") is not None:
            self.runner.head_tax_debts.restore(data["head_tax_debts"])
        self.runner.day_jobs_ran = {
            int(k): v for k, v in (data.get("day_jobs_ran") or {}).items()
        }


class MapCheckpointAdapter:
    """The map, and the movement system that has to keep walking on it.

    GROW-07: an expanded map was in no checkpoint, so a spot-instance
    restart silently undid every petition the town had ever won.

    `MapService` already implements snapshot/restore, so the interesting
    half is the second line of `restore()`: it REBUILDS `self.world` from
    tiled+blocks, deliberately, so a restored world can never drift from
    the tables that produced it. That is right for the service and fatal
    for GROW-05 - MovementSystem holds a direct reference to the old
    WorldMap, so after a resume the service would grow one map while every
    resident walked another, which is exactly the two-map bug the boot path
    was just fixed to avoid. Re-pointing movement here is what keeps the
    fix true across a restart as well as at boot.
    """

    def __init__(self, service: Any, world_state: Any) -> None:
        self.service = service
        self.world_state = world_state

    def snapshot(self) -> dict[str, Any]:
        return self.service.snapshot()

    def restore(self, data: dict[str, Any]) -> None:
        self.service.restore(data)
        movement = self.world_state.movement
        if movement is not None:
            movement.world_map = self.service.world


class RecordCheckpointAdapter:
    """The town's records of what HAPPENED - speech, and refusals.

    Same shape of bug as CharterCheckpointAdapter below, in a different
    place. `utterances` and `tool_refusals` are deliberately excluded from
    OWNED_STATE_FIELDS, and correctly so: ToolRegistry.execute() rolls owned
    state back when a call raises, and rolling back speech would erase words
    that really were spoken.

    But OWNED_STATE_FIELDS is also what WorldStateCheckpointAdapter above
    derives its coverage from, so "must not roll back" silently became "must
    not survive a restart". Observed: a run resumed from tick 1048 with its
    full tool history intact and its conversation empty, so the report said
    "Nobody spoke" about a town with fifteen recorded `say` calls. Every
    resume erased the record permanently, and nothing raised.

    These are plain dicts and pydantic records with no cross-references, so
    they need none of WorldStateCheckpointAdapter's model machinery.
    """

    def __init__(self, world_state: WorldState) -> None:
        self.world_state = world_state

    def snapshot(self) -> dict[str, Any]:
        return {
            "utterances": to_jsonable(self.world_state.utterances),
            "tool_refusals": [
                r.model_dump(mode="json") for r in self.world_state.tool_refusals
            ],
        }

    def restore(self, data: dict[str, Any]) -> None:
        self.world_state.utterances = list(data.get("utterances") or [])
        self.world_state.tool_refusals = [
            ToolRefusalRecord.model_validate(r) for r in (data.get("tool_refusals") or [])
        ]


class CharterCheckpointAdapter:
    """Checkpointable wrapper over world.tools.state.WorldState.charter (a
    CharterEngine) - the fix for STATUS.md risk #4.

    world/tools/state.py's WorldState deliberately never snapshots/
    restores `charter` (its own module docstring: `charter` is a sibling
    system that guarantees its own operations are all-or-nothing, the same
    reason `ledger`/`event_bus`/`locations` are excluded - correct for
    ToolRegistry's per-call rollback, but that same exclusion meant
    nothing else in this file was rolling governance state forward through
    a day-boundary checkpoint either). GenesisRunner.boot() always calls
    build_world() -> create_genesis_charter_engine(), which unconditionally
    seeds a *brand new* genesis charter and vacant government seats - fine
    for a fresh run, silently wrong for a resume, since nothing overwrote
    that fresh seed with the real state afterward. A crash mid-election
    resumed into a fresh charter with no election, no candidates, no
    ballots, and a vacant government - DESIGN.md's single most important
    research variable (political evolution) gone with no error raised.

    Registering this adapter under CheckpointManager fixes it structurally
    the same way "ledger" and "world_state" already are: restore() runs
    inside GenesisRunner.boot()'s existing
    `await self.tick_engine.restore_from_checkpoint(latest)` call, which
    overwrites whatever seed_genesis's charter left behind with the
    checkpointed real values - no special-casing needed in boot() itself.

    Covers CharterStore's entire owned state (world/charter/store.py's
    CharterStore.snapshot()/restore(): charter_history - every amendment-
    produced version, not just the current one - policy_history, the
    sitting government, every election in whatever phase it's in, jobs,
    licenses, tenders, the patent-office queue, land petitions,
    organizations, and the immutable complaint ledger) plus
    AmendmentEngine.proposals (world/charter/amendments.py's
    AmendmentEngine.snapshot()/restore()), the one piece of governance
    state charter/ keeps on a sub-engine instance rather than in
    CharterStore itself.
    """

    def __init__(self, charter: CharterEngine) -> None:
        self.charter = charter

    def snapshot(self) -> dict[str, Any]:
        return {
            "store": self.charter.store.snapshot(),
            "amendment_proposals": self.charter.amendments.snapshot(),
        }

    def restore(self, data: dict[str, Any]) -> None:
        self.charter.store.restore(data["store"])
        self.charter.amendments.restore(data["amendment_proposals"])


@dataclass
class RunnerCounters:
    """This runner's own live-metric bookkeeping, checkpointed alongside
    the world so a resume picks counts back up instead of resetting them
    to zero (a resumed run reporting "0 deaths so far" after 40 real
    deaths would be a silently wrong metric, exactly the kind of thing
    app/CONVENTIONS.md's ledger-invariant tripwire philosophy warns
    against for money - the same discipline applies here)."""

    births: int = 0
    deaths: int = 0
    elections: int = 0
    crimes: int = 0
    businesses_registered: int = 0
    rng_state: Any = None
    retrieval_depth_fraction: float = 0.0
    last_life_hour_processed: float = 0.0
    last_life_day_processed: int = -1
    mock_cost_remainder_usd: float = 0.0


class RunnerCountersCheckpointAdapter:
    def __init__(self, counters: RunnerCounters) -> None:
        self.counters = counters

    def snapshot(self) -> dict[str, Any]:
        return {
            "births": self.counters.births,
            "deaths": self.counters.deaths,
            "elections": self.counters.elections,
            "crimes": self.counters.crimes,
            "businesses_registered": self.counters.businesses_registered,
            "rng_state": to_jsonable(self.counters.rng_state),
            "retrieval_depth_fraction": self.counters.retrieval_depth_fraction,
            "last_life_hour_processed": self.counters.last_life_hour_processed,
            "last_life_day_processed": self.counters.last_life_day_processed,
            "mock_cost_remainder_usd": self.counters.mock_cost_remainder_usd,
        }

    def restore(self, data: dict[str, Any]) -> None:
        self.counters.births = data["births"]
        self.counters.deaths = data["deaths"]
        self.counters.elections = data["elections"]
        self.counters.crimes = data["crimes"]
        self.counters.businesses_registered = data["businesses_registered"]
        raw_rng_state = data.get("rng_state")
        if raw_rng_state is not None:
            version, twister_state, gauss_next = raw_rng_state
            self.counters.rng_state = (version, tuple(twister_state), gauss_next)
        else:
            self.counters.rng_state = None
        self.counters.retrieval_depth_fraction = data["retrieval_depth_fraction"]
        self.counters.last_life_hour_processed = data["last_life_hour_processed"]
        self.counters.last_life_day_processed = data["last_life_day_processed"]
        self.counters.mock_cost_remainder_usd = data.get("mock_cost_remainder_usd", 0.0)


class LocalCheckpointStore:
    """Writes/reads WorldCheckpoints as gzip JSON files on local disk.

    This is what resume() actually reads from (see module docstring point
    4) - a plain, dependency-free mechanism that works identically whether
    or not a shared Supabase/S3 store is configured, so a solo local dev
    run and the real genesis run share the exact same resume path.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, tick_index: int) -> Path:
        return self.directory / f"checkpoint_{tick_index:010d}.json.gz"

    def write(self, checkpoint: WorldCheckpoint) -> Path:
        path = self.path_for(checkpoint.tick_index)
        payload = json.dumps(checkpoint.model_dump(mode="json"), sort_keys=True).encode("utf-8")
        compressed = gzip.compress(payload, compresslevel=6)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        fd = os.open(tmp_path, flags, 0o644)
        try:
            os.write(fd, compressed)
            os.fsync(fd)
        finally:
            os.close(fd)
        tmp_path.replace(path)
        if os.name != "nt":
            dir_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        return path

    def list_checkpoints(self) -> list[Path]:
        return sorted(self.directory.glob("checkpoint_*.json.gz"))

    def latest(self) -> WorldCheckpoint | None:
        paths = self.list_checkpoints()
        if not paths:
            return None
        return self.read(paths[-1])

    def read(self, path: Path) -> WorldCheckpoint:
        payload = gzip.decompress(path.read_bytes())
        return WorldCheckpoint.model_validate(json.loads(payload))


async def mirror_checkpoint_to_shared_store(
    checkpoint: WorldCheckpoint, local_path: Path, bucket: str, client: Any = None
) -> None:
    """Best-effort secondary copy through the shared trace-store client
    (supabase.factory.get_client() + supabase.archiver.PayloadArchiver),
    landing in the same world_snapshots table every other world/ writer
    uses (app/CONVENTIONS.md: world/ is the single writer for it). SQLite
    locally, S3 once that track's Storage backend is S3-backed (module
    docstring point 4) - never raises, a transient outage here must never
    take down a live genesis run.
    """
    try:
        from supabase.archiver import PayloadArchiver, build_world_snapshot_archive_key
        from supabase.factory import get_client

        if client is None:
            logger.warning(
                "no trace-store client passed to the checkpoint mirror: "
                "falling back to get_client(), which resolves to the env's "
                "sqlite_path and may be ':memory:'. The local checkpoint at "
                "%s is unaffected and is the one resume reads.",
                local_path,
            )
            client = get_client()
            owns_client = True
        else:
            owns_client = False
        try:
            archiver = PayloadArchiver(client, bucket=bucket)
            raw = local_path.read_bytes()
            key = build_world_snapshot_archive_key(tick=checkpoint.tick_index, reason="daily")
            result = await archiver.archive_bytes(key=key, raw=raw, content_type="application/gzip")

            usd_wallets_total = sum(
                row["usd_cents"]
                for row in checkpoint.components.get("ledger", {}).get("accounts", {}).values()
                if row["kind"] == "agent"
            )
            usd_treasury_total = (
                checkpoint.components.get("ledger", {})
                .get("accounts", {})
                .get(TREASURY_ACCOUNT_ID, {})
                .get("usd_cents", 0)
            )
            usd_burned_total = (
                checkpoint.components.get("ledger", {})
                .get("accounts", {})
                .get(BURNED_ACCOUNT_ID, {})
                .get("usd_cents", 0)
            )
            agent_count = len(checkpoint.components.get("world_state", {}).get("agents", {}))

            await client.insert(
                "world_snapshots",
                [
                    {
                        "reason": "daily",
                        "state_archive_path": result.storage_path,
                        "state_size_bytes": result.compressed_size_bytes,
                        "agent_count": agent_count,
                        "usd_wallets_total_cents": usd_wallets_total,
                        "usd_treasury_total_cents": usd_treasury_total,
                        "usd_burned_total_cents": usd_burned_total,
                        "tick": checkpoint.tick_index,
                    }
                ],
            )
        finally:
            if owns_client:
                await client.close()
    except Exception as exc:  # noqa: BLE001 - best-effort mirror, see docstring
        logger.warning("shared checkpoint mirror failed for tick %s: %s", checkpoint.tick_index, exc)


@dataclass
class BurnSample:
    at: float
    burned_cents: int


class GenesisMetrics:
    """Live metrics: spend, burn rate, game-day, population, births,
    deaths, elections, crimes, businesses (task's exact list). Spend comes
    straight off the ledger's burned account, the one number
    app/CONVENTIONS.md's invariant makes authoritative regardless of which
    gateway (mock or real) produced it. Everything else comes from
    subscribing to the world event bus, which every world/ mutation
    already publishes to (world/core/events.py) - this class never
    computes anything world/ itself does not already report as an event.
    """

    def __init__(
        self,
        ledger: Ledger,
        world_state: WorldState,
        counters: RunnerCounters,
        budget_cap_cents: int | None = None,
    ) -> None:
        self.ledger = ledger
        self.world_state = world_state
        self.counters = counters
        self.budget_cap_cents = budget_cap_cents
        self.started_at = time.time()
        self.burn_samples: deque[BurnSample] = deque(maxlen=200)
        self.attach()

    def attach(self) -> None:
        bus = self.world_state.event_bus

        async def on_health(event: WorldEvent) -> None:
            if event.type == "health.agent.died":
                self.counters.deaths += 1

        async def on_agent(event: WorldEvent) -> None:
            if event.type == "agent.spawned" and event.payload.get("reason") == "birth":
                self.counters.births += 1

        async def on_politics(event: WorldEvent) -> None:
            if event.type.startswith("politics.election."):
                self.counters.elections += 1

        async def on_crime(event: WorldEvent) -> None:
            self.counters.crimes += 1

        async def on_business(event: WorldEvent) -> None:
            if event.type == "business.registered":
                self.counters.businesses_registered += 1

        bus.subscribe("health", on_health)
        bus.subscribe("agent", on_agent)
        bus.subscribe("politics", on_politics)
        bus.subscribe("crime", on_crime)
        bus.subscribe("business", on_business)

    def record_burn_sample(self) -> None:
        self.burn_samples.append(
            BurnSample(at=time.time(), burned_cents=self.ledger.usd_balance(BURNED_ACCOUNT_ID))
        )

    @property
    def spend_usd_cents(self) -> int:
        return self.ledger.usd_balance(BURNED_ACCOUNT_ID)

    @property
    def burn_rate_usd_per_hour(self) -> float:
        """Trailing burn rate computed from the sample window, not the
        whole run's average - so a live-tuned retrieval depth change (or a
        governor near its cap) shows up in this number within a few
        samples rather than being averaged away by history."""
        if len(self.burn_samples) < 2:
            return 0.0
        first, last = self.burn_samples[0], self.burn_samples[-1]
        elapsed_hours = (last.at - first.at) / 3600.0
        if elapsed_hours <= 0:
            return 0.0
        delta_cents = last.burned_cents - first.burned_cents
        return (delta_cents / 100.0) / elapsed_hours

    @property
    def population(self) -> int:
        return sum(1 for agent in self.world_state.agents.values() if agent.alive)

    def snapshot(self, game_day: int) -> dict[str, Any]:
        return {
            "game_day": game_day,
            "spend_usd_cents": self.spend_usd_cents,
            "spend_usd": round(self.spend_usd_cents / 100.0, 4),
            "budget_cap_usd_cents": self.budget_cap_cents,
            "spend_fraction_of_cap": (
                round(self.spend_usd_cents / self.budget_cap_cents, 4)
                if self.budget_cap_cents
                else None
            ),
            "burn_rate_usd_per_hour": round(self.burn_rate_usd_per_hour, 4),
            "population": self.population,
            "births": self.counters.births,
            "deaths": self.counters.deaths,
            "elections": self.counters.elections,
            "crimes": self.counters.crimes,
            "businesses": self.counters.businesses_registered + len(self.world_state.businesses),
            "uptime_s": round(time.time() - self.started_at, 1),
        }


HALT_RESERVE_FRACTION = 0.05
HALT_RESERVE_FLOOR_CENTS = 5


def halt_reserve_cents(broker_cap_cents: int) -> int:
    """How much of the broker's cap to leave unspent so the world can stop
    itself while the broker will still answer."""
    return max(HALT_RESERVE_FLOOR_CENTS, int(broker_cap_cents * HALT_RESERVE_FRACTION))


def cap_with_room_for_the_drain(requested_cents: int, env: "Mapping[str, str]") -> int:
    """An explicit runner cap, lowered if it would leave the broker no room.

    Run 29 passed `--budget-cap-cents 2000` against a $20.00 broker cap. The
    halt reserve CORE-25 built - stop the world a few cents short of the
    broker, so it stops cleanly and the end-of-run learning drain still gets
    answers - was applied only when the cap came from SPEND_CAP_USD, so the
    flag skipped it. The broker halted first, all 14 end-of-run
    `auto:learning` calls were refused, and 0 of 20 residents consolidated a
    morning of work.

    Lowered only when the broker's cap is KNOWN (SPEND_CAP_USD is set):
    inventing a reserve against a number nobody gave would be a guess. And
    never when KASUKABE_BUDGET_CAP_CENTS is set, which CORE-25 documents as
    the way to make the two caps equal on purpose.
    """
    raw = env.get("SPEND_CAP_USD")
    if raw is None or env.get("KASUKABE_BUDGET_CAP_CENTS") is not None:
        return requested_cents
    try:
        broker_cap = int(round(float(raw) * 100))
    except ValueError:
        return requested_cents
    ceiling = max(1, broker_cap - halt_reserve_cents(broker_cap))
    if requested_cents <= ceiling:
        return requested_cents
    logger.warning(
        "--budget-cap-cents %d leaves the broker (SPEND_CAP_USD=%s) no room for the "
        "end-of-run learning drain; running to %d cents instead, %d below the broker's "
        "cap. Set KASUKABE_BUDGET_CAP_CENTS to make them equal on purpose.",
        requested_cents, raw, ceiling, broker_cap - ceiling,
    )
    return ceiling


READING_EVERY_TICKS = max(0, int(os.environ.get("KASUKABE_READING_EVERY_TICKS", "144") or 0))

SNAPSHOT_HOMES = os.environ.get("KASUKABE_SNAPSHOT_HOMES", "1") not in ("0", "false", "")

FINAL_READING_ROUTES = {
    "state": "/world/state",
    "agents": "/agents",
    "authored": "/world/authored",
    "vitals": "/world/vitals",
    "refusals": "/world/refusals",
    "justice": "/world/justice",
    "utterances": "/world/utterances?limit=5000",
    "tool_invocations": "/world/tool_invocations",
    "registry": "/tools/registry",
    "preflight": "/world/preflight",
}


async def capture_final_reading(
    *,
    app: Any = None,
    base_url: "str | None" = None,
    checkpoint_dir: "str | Path",
    tick: "int | None" = None,
) -> "Path | None":
    """Ask the world everything the report will want, while it can answer.

    The 50-agent run (`teambox-50-100usd-2026-09-16`) ended with a report
    that said "The world reported no roster" about fifty residents,
    "Refusals ... are not recorded here", and "Authoring tools MISSING
    skillify" in a run where three residents had used `skillify`. None of
    that was true; the world had simply stopped by the time anybody asked.

    A final report is ALWAYS written after the stop, so this is read once,
    on the way down, and written beside the checkpoints. Over the ASGI app
    when the world is embedded - no port, no network, and it works in a
    test - and over HTTP when it is a separate process.

    Never raises: a run must be able to end even if describing it fails.
    """
    import httpx

    if app is None and not base_url:
        return None
    readings: dict[str, Any] = {}
    try:
        if app is not None:
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://world")
        else:
            client = httpx.AsyncClient(base_url=str(base_url).rstrip("/"))
        async with client:
            for name, route in FINAL_READING_ROUTES.items():
                try:
                    response = await client.get(route, timeout=30)
                except Exception as exc:  # noqa: BLE001 - one dead route, not the reading
                    logger.debug("final reading: %s did not answer: %s", route, exc)
                    continue
                if response.status_code == 200:
                    try:
                        readings[name] = response.json()
                    except ValueError:
                        continue
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("could not take the final reading: %s", exc)
        return None
    if not readings:
        return None

    target = Path(checkpoint_dir)
    path = target / "final-reading.json"
    try:
        target.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "captured_at": time.time(),
            "tick": int(tick) if tick is not None else -1,
            "routes": sorted(readings),
            "readings": readings,
        }, default=str), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not write the final reading to %s: %s", path, exc)
        return None
    logger.info(
        "final reading taken at tick %s: %s - the roster, the refusals and the "
        "tools being served, which the trace does not hold and a stopped world "
        "cannot be asked for",
        tick, path,
    )
    return path


def restore_engine_homes(home_root: "str | Path", checkpoint_dir: "str | Path") -> "Path | None":
    """Put a previous run's memories back, when this run has none.

    `archive_engine_homes` has packed every resident's `brain.sqlite` and
    session log into the checkpoint directory since INFRA-09, and nothing
    ever unpacked it. The default home root is under `/tmp`, which macOS
    clears, so continuing a town from its checkpoint produced residents who
    remembered the whole run in the world state and nothing at all in their
    own heads.

    Only when the home root is EMPTY. Live homes are the newer ones, and
    restoring over them would hand a resident an older self - so an
    existing town is left exactly as it is and this returns None.

    The archive's top directory is dropped, because runs are booted with
    their own home roots (`kasukabe-agents-run29`,
    `-teambox50`) and a continuation must not have to match the name. A
    member that would land outside the home root is skipped: a tar is an
    untrusted file format, and this one is read on boot.

    Never raises - a town that starts without its memories is worse off
    than one that starts, and better off than one that does not start.
    """
    root = Path(home_root)
    if root.is_dir() and any(root.iterdir()):
        return None
    archive = Path(checkpoint_dir) / "engine-homes.tar.gz"
    if not archive.is_file():
        return None
    try:
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve()
        with tarfile.open(archive, "r:gz") as tar:
            wanted = []
            for member in tar.getmembers():
                parts = Path(member.name).parts[1:]
                if not parts:
                    continue
                member.name = str(Path(*parts))
                target = (resolved_root / member.name).resolve()
                if not target.is_relative_to(resolved_root):
                    logger.warning("skipping %r in %s: it points outside the home root",
                                   member.name, archive)
                    continue
                wanted.append(member)
            if not wanted:
                return None
            tar.extractall(root, members=wanted, filter="data")
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("could not restore the engine homes from %s: %s", archive, exc)
        return None
    logger.info(
        "engine homes restored from %s into %s - the residents of the town this "
        "run continues keep what they remember",
        archive, root,
    )
    return root


def archive_engine_homes(home_root: "str | Path", checkpoint_dir: "str | Path") -> "Path | None":
    """Pack every engine home into the run's checkpoint directory.

    INFRA-09's second half. Each home holds the resident's session log and
    `brain.sqlite` - the only record of its turns and memory - and the
    default home root is under `/tmp`, which macOS clears. Run 29's 20 homes
    (135 MB) were gone within a day, so nothing that needs them can be run
    against that run again: not a replay, not a history rebuild, not a
    re-analysis of what a resident was shown.

    Called once the engines have STOPPED, so every SQLite file is closed and
    the copy is whole. Returns the archive, or None when there was nothing to
    keep or it could not be written - and never raises, because keeping the
    evidence must not cost the rest of the stop path.
    """
    root = Path(home_root)
    if not root.is_dir() or not any(root.iterdir()):
        return None
    target_dir = Path(checkpoint_dir)
    archive = target_dir / "engine-homes.tar.gz"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(root, arcname=root.name)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("could not archive the engine homes under %s: %s", root, exc)
        return None
    logger.info(
        "engine homes archived: %s (%.1f MB) - session logs and brain.sqlite for every "
        "resident, kept beside the checkpoints rather than left in %s",
        archive, archive.stat().st_size / 1_048_576, root,
    )
    return archive


def snapshot_engine_homes(home_root: "str | Path", checkpoint_dir: "str | Path") -> "Path | None":
    """SG7: the engine homes, kept while the engines are still running.

    `archive_engine_homes` runs once the engines have stopped - which a run
    that is killed never reaches, and killed is how most runs end. So a
    killed run lost every session log and `brain.sqlite`, the only record of
    what its residents were shown and remembered.

    This is taken with the periodic reading (once a game-day). A live SQLite
    file cannot be copied byte for byte - the copy can land mid-write - so
    each database goes through SQLite's own online backup, which gives a
    consistent copy while the engine keeps writing; `-wal`/`-shm`/`-journal`
    side files are left out because the backup already folded them in. Other
    files (session logs, append-only) are copied as they stand.

    Written beside the checkpoints under the same name the stop path uses,
    through a temporary file and an atomic rename, so `restore_engine_homes`
    always finds a whole archive and the newest one wins. Never raises.
    """
    import shutil
    import sqlite3
    import tempfile

    root = Path(home_root)
    if not root.is_dir() or not any(root.iterdir()):
        return None
    target_dir = Path(checkpoint_dir)
    archive = target_dir / "engine-homes.tar.gz"
    partial = target_dir / "engine-homes.tar.gz.part"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target_dir) as tmp:
            stage = Path(tmp) / root.name
            stage.mkdir()
            for src in sorted(root.rglob("*")):
                dst = stage / src.relative_to(root)
                if src.is_dir():
                    dst.mkdir(parents=True, exist_ok=True)
                    continue
                if src.name.endswith(("-wal", "-shm", "-journal")):
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.suffix in (".sqlite", ".sqlite3", ".db"):
                    source = sqlite3.connect(str(src), timeout=10)
                    copy = sqlite3.connect(str(dst))
                    try:
                        source.backup(copy)
                    finally:
                        copy.close()
                        source.close()
                else:
                    shutil.copy2(src, dst)
            with tarfile.open(partial, "w:gz") as tar:
                tar.add(stage, arcname=root.name)
        os.replace(partial, archive)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("could not snapshot the engine homes under %s: %s", root, exc)
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    logger.info("engine homes snapshotted while running: %s (%.1f MB)",
                archive, archive.stat().st_size / 1_048_576)
    return archive


def dispatch_had_halted(*, governor_tripped: bool, broker_state: "str | None") -> bool:
    """Had model calls stopped being answered by the time the drain ran?

    Either counter can say so, and run 29 shows why both must be asked: the
    broker counts what it dispatched, the runner's governor counts what the
    ledger burned, the broker's gets there first - and the governor never
    tripped. The warning that exists to say "the learning was refused" was
    keyed to the governor alone and stayed silent over 14 refused calls.
    `broker_state` None means the broker could not be asked, which says
    nothing either way.
    """
    return governor_tripped or broker_state == "halted"


class SpendGovernor:
    """Trips the run to a clean stop once burned spend reaches the
    operator's hard cap - independent of, and tighter than, the ledger's
    own $1000 invariant (app/CONVENTIONS.md), which is a halt-on-violation
    tripwire, not a budget. Task prompt: "HARD BUDGET CAP $400."""

    def __init__(self, ledger: Ledger, budget_cap_cents: int) -> None:
        self.ledger = ledger
        self.budget_cap_cents = budget_cap_cents
        self.tripped = False
        self.tripped_reason = ""

    def check(self) -> bool:
        if self.tripped:
            return True
        spend = self.ledger.usd_balance(BURNED_ACCOUNT_ID)
        if spend >= self.budget_cap_cents:
            self.tripped = True
            self.tripped_reason = (
                f"spend {spend} cents reached the {self.budget_cap_cents}-cent budget cap"
            )
            logger.warning("spend governor tripped: %s", self.tripped_reason)
        if self.ledger.halted:
            self.tripped = True
            self.tripped_reason = self.ledger.halt_reason or "ledger halted"
        return self.tripped


def build_embedded_world_app(
    world_state: WorldState,
    registry: ToolRegistry,
    ledger: Ledger,
    metrics: GenesisMetrics,
    tick_engine: TickEngine | None = None,
    admin_extras: AdminExtras | None = None,
    get_population_dials: Callable[[], PopulationDials] | None = None,
    set_population_dials: Callable[[dict[str, Any]], PopulationDials] | None = None,
    genesis: Any | None = None,
    cognition: str = "unknown",
    broker_base_url: str = "",
    engine_liveness: Callable[[], Awaitable[dict[str, int]]] | None = None,
    engine_output: Callable[[], dict[str, list[str]]] | None = None,
    wire_health: Callable[[], Awaitable[dict[str, int]]] | None = None,
    recorder_stats: "Callable[[], dict[str, Any]] | None" = None,
    map_service: Any | None = None,
    trace_client: Any | None = None,
    run_cap_usd_cents_getter: "Callable[[], int | None] | None" = None,
) -> FastAPI:
    """Implements exactly the two ASSUMED CONTRACTs
    engine/src/sim/world_tools.py and broker/ledger.py already documented
    (module docstring point 2), plus /health and /metrics for operator
    tooling. Bound to GenesisRunnerConfig.world_base_url() when
    world_mode="embedded" (the default).

    STATUS.md risk #2 ("console-contract" task): this is the app a LIVE
    genesis run actually boots (module docstring point 2 - the 250 engine
    processes and the operator console both talk to this exact process,
    not world/main.py), so it is the one that matters most for the
    President/God console to be able to steer a run in progress, not just
    watch it. When `tick_engine` is supplied (GenesisRunner.
    start_embedded_world_server always supplies one; the four positional-
    only call sites in world/tests/test_run_genesis.py's
    TestEmbeddedWorldApp predate this and do not, so they keep exercising
    exactly the four routes they always have, unchanged) this also mounts
    world/admin_api.py's full /admin/* router - the SAME router
    world/main.py mounts, so the console's contract is served identically
    by both hosts (admin_api.py's own module docstring). `admin_extras`/
    `get_population_dials`/`set_population_dials` default to a fresh,
    freestanding admin state when omitted alongside a supplied
    tick_engine (e.g. a test that wants the admin routes without wiring a
    full GenesisRunner); GenesisRunner.start_embedded_world_server passes
    its own real ones so pause/resume and population-dial changes
    actually reach its tick loop (see GenesisRunner.run_tick and
    run_life_ticks).
    """

    app = FastAPI(title="kasukabe-genesis-world")

    if tick_engine is not None:
        if admin_extras is not None:
            resolved_extras = admin_extras
        else:
            token, was_generated = resolve_operator_token()
            if was_generated:
                logger.warning(
                    "OPERATOR_TOKEN not set - generated an ephemeral admin token for this "
                    "embedded world app. Ephemeral operator token: %s",
                    token,
                )
            resolved_extras = AdminExtras(operator_token=token)
        dials_store = PopulationDials()

        def default_get_dials() -> PopulationDials:
            return dials_store

        def default_set_dials(updates: dict[str, Any]) -> PopulationDials:
            nonlocal dials_store
            dials_store = dials_store.model_copy(update=updates)
            return dials_store

        resolved_get_dials = get_population_dials if get_population_dials is not None else default_get_dials
        resolved_set_dials = set_population_dials if set_population_dials is not None else default_set_dials
        resolved_get_run_cap = run_cap_usd_cents_getter

        def build_deps() -> AdminDeps:
            return AdminDeps(
                world=world_state,
                tick_engine=tick_engine,
                map_service=map_service,
                extras=resolved_extras,
                get_population_dials=resolved_get_dials,
                set_population_dials=resolved_set_dials,
                get_run_cap_usd_cents=resolved_get_run_cap,
            )

        install_admin_exception_handlers(app)
        app.include_router(build_admin_router(build_deps))

    if genesis is not None:
        from world.agents_api import AgentsDeps, build_agents_router
        from world.family_api import FamilyDeps, build_family_router
        from world.governance_api import GovernanceDeps, build_governance_router
        from world.location_api import LocationDeps, build_location_router
        from world.skills_api import SkillsDeps, build_skills_router
        from world.workshop_api import WorkshopDeps, build_workshop_router

        app.include_router(
            build_agents_router(
                lambda: AgentsDeps(
                    world=world_state, genesis=genesis, map_service=map_service
                )
            )
        )
        app.include_router(
            build_location_router(
                lambda: LocationDeps(
                    world=world_state, map_service=map_service, registry=registry
                )
            )
        )
        from world.agent_viewer_api import AgentViewerDeps, build_agent_viewer_router
        from world.social_api import SocialDeps, build_social_router

        app.include_router(
            build_social_router(
                lambda: SocialDeps(
                    world=world_state, genesis=genesis, trace=trace_client
                )
            )
        )
        app.include_router(
            build_agent_viewer_router(
                lambda: AgentViewerDeps(
                    world=world_state,
                    genesis=genesis,
                    tick_engine=tick_engine,
                    trace=trace_client,
                )
            )
        )

        app.include_router(
            build_governance_router(
                lambda: GovernanceDeps(world=world_state, genesis=genesis)
            )
        )
        app.include_router(
            build_family_router(lambda: FamilyDeps(world=world_state, genesis=genesis))
        )
        app.include_router(
            build_skills_router(lambda: SkillsDeps(world=world_state, genesis=genesis))
        )
        app.include_router(
            build_workshop_router(lambda: WorkshopDeps(world=world_state, genesis=genesis))
        )

    inference_cache: dict[str, str] = {}

    async def probe_inference() -> str | None:
        """Ask the broker whether completions are real Bedrock or canned.

        Neither this world nor game/ can answer it - only the broker knows
        which gateway it built. Without this hop a run with real engines
        and a real world still cannot tell anyone that every completion
        was the literal string "mock response".
        """
        if "value" in inference_cache:
            return inference_cache["value"]
        if not broker_base_url:
            return None
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(broker_base_url.rstrip("/") + "/health")
            if resp.status_code != 200:
                return None
            value = resp.json().get("inference")
        except Exception:  # noqa: BLE001 - provenance must never break /health
            return None
        if isinstance(value, str):
            inference_cache["value"] = value
            return value
        return None

    @app.get("/health")
    async def health(response: Response) -> dict[str, Any]:
        response.headers["Access-Control-Allow-Origin"] = "*"
        return {
            "status": "ok",
            "service": "world",
            "mode": "genesis-embedded",
            "cognition": cognition,
            "inference": await probe_inference(),
        }

    @app.get("/tools/registry")
    async def tools_registry(request: Request, agent_id: str | None = None) -> Response:
        if agent_id and local_tool_schemas():
            rows = tool_registry_rows(registry, world_state, agent_id)
        else:
            rows = tool_registry_rows(registry)
        body = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
        etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(
            content=body, media_type="application/json", headers={"ETag": etag}
        )

    def _genesis_names() -> dict[str, str]:
        """agent_id -> the name the RUNNING genesis gave them.

        Not the build-time cast in game/static/js/township/agents.js: that
        file is a different draw of the town and assigns different names to
        the same ids, so a viewer resolving names client-side showed a0064 as
        "Hector Stone" while the Workshop, which reads genesis, called the
        same agent "Lior Jarrah". One agent, two names, and no way for a
        reader to know which was real.
        """
        if genesis is None:
            return {}
        return {a.agent_id: a.name for a in genesis.agents}

    @app.get("/world/preflight")
    async def world_preflight() -> dict[str, Any]:
        """The fifteen checks, live. Read by the viewer and the run report.

        A failing check is not advice: `run_genesis` refuses to start on one,
        and the viewer shows the red line so it can be fixed before any money
        is spent. Re-runnable during a run, so a broker that dies at hour two
        shows up as a red line rather than as silence.
        """
        from world.preflight import run as preflight_run

        pf = preflight_run(
            broker_url=broker_base_url or os.environ.get("BROKER_BASE_URL", ""),
            world_url=None,
            expected_agents=len(world_state.agents),
            genesis_path=os.environ.get("KASUKABE_GENESIS_PATH", "world/genesis.json"),
            home_root=os.environ.get("KASUKABE_HOME_ROOT", "~/.kasukabe"),
            trace_db=os.environ.get("TRACE_SQLITE_PATH", "data/kasukabe.db"),
            engine_repo=os.environ.get("KASUKABE_ENGINE_REPO", "../engine"),
            ports_must_be_free=False,
            engines_running=True,
        )

        from world.preflight import REQUIRED_TOOLS, Check

        served = set(registry.tools)
        missing = [t for t in REQUIRED_TOOLS if t not in served]
        pf.checks.append(Check(
            "Authoring tools reachable by agents", not missing,
            f"{len(served)} tools served; missing: {missing or 'none'}",
            "an agent that cannot call skillify/toolify makes the run pointless",
        ))
        dropped = 0
        if recorder_stats is not None:
            dropped = recorder_stats().get("rows_dropped", 0)
        pf.checks.append(Check(
            "The recorder has lost nothing", dropped == 0,
            f"{dropped} row(s) dropped",
            "a run measured against an incomplete record is not evidence; "
            "check the trace store's last failure and restart",
        ))
        alive = 0
        if engine_liveness is not None:
            try:
                counts = await engine_liveness()
                alive, want = counts.get("alive", 0), counts.get("expected", 0)
                pf.checks.append(Check(
                    "Every engine booted", alive == want and alive > 0,
                    f"{alive}/{want} alive", "check the run log for boot failures",
                ))
            except Exception as exc:  # noqa: BLE001
                pf.checks.append(Check("Every engine booted", False,
                                       f"{type(exc).__name__}: {exc}", ""))
        pf.checks.append(Check(
            "World is ticking", world_state.tick() > 0,
            f"tick {world_state.tick()}",
            "a world stuck at tick 0 means no engine has acted",
            critical=False,
        ))
        return pf.to_json()

    @app.get("/world/vitals")
    async def world_vitals() -> dict[str, Any]:
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
        """The tail of what the town has said, for the viewer's Talk feed.

        Agent-to-agent speech was the one thing a live run produced and no
        surface showed: `say` emitted an event the trace store consumed and
        nothing read back.
        """
        names = _genesis_names()
        rows = []
        for record in world_state.utterances[-limit:]:
            row = dict(record)
            row["speaker_name"] = names.get(row.get("speaker_agent_id") or "") or row.get("speaker_agent_id")
            if row.get("listener_agent_id"):
                row["listener_name"] = names.get(row["listener_agent_id"]) or row["listener_agent_id"]
            rows.append(row)
        return {"count": len(rows), "utterances": rows}

    def _phrase_activity(row: dict[str, Any] | None) -> str:
        """One readable line for the most recent thing an agent did.

        Deliberately built from the record and nothing else: no adjectives
        the town did not earn, no invented intent. If a tool is not one of
        the named few, the line is the tool's own name, which is still true.
        """
        if not row:
            return "has not acted yet this run"
        if row["kind"] == "said":
            named = [name for name in (row.get("to_names") or []) if name]
            if not named and row.get("to_name"):
                named = [row["to_name"]]
            if len(named) > 3:
                named = named[:3] + [f"and {len(row['to_names']) - 3} more"]
            return f"talking to {', '.join(named)}" if named else "speaking to everyone here"

        tool = row.get("tool") or "?"
        p = row.get("params") or {}

        def n(key: str) -> str:
            v = p.get(key)
            return "" if v in (None, "") else f"{v} "

        if tool == "say":
            return "speaking"
        if tool in ("skillify", "revise_skill"):
            return f"writing down a skill: {p.get('name') or '...'}"
        if tool in ("toolify", "revise_tool", "define_tool",
                    "define_business_tool", "define_organization_tool"):
            return f"inventing a tool: {p.get('name') or '...'}"
        if tool == "move":
            here = world_state.location_of(row.get("agent_id") or "")
            asked = p.get("destination_location_id")
            if here and asked and here != asked:
                return f"walking to {asked} — now at {here}"
            return f"walking to {here or asked or 'somewhere'}"
        if tool == "buy":
            return f"buying {n('quantity')}{p.get('item') or 'goods'}"
        if tool == "sell":
            return f"selling {n('quantity')}{p.get('item') or 'goods'}"
        if tool == "harvest":
            return f"harvesting {n('quantity')}{p.get('crop') or 'crops'}"
        if tool == "mine":
            return f"mining {n('quantity')}{p.get('metal') or 'ore'}"
        if tool == "fell_timber":
            return f"felling {n('quantity')}timber".strip()
        if tool == "craft":
            return f"crafting {p.get('recipe_id') or 'something'}"
        if tool == "cook":
            return f"cooking {p.get('recipe_id') or 'a meal'}"
        if tool == "build_structure":
            return f"building {p.get('purpose') or 'a structure'}"
        if tool == "teach_skill":
            return f"teaching {p.get('skill') or 'a skill'}"
        if tool == "pray":
            return "praying"
        if tool == "look_around":
            return "looking around"
        if tool == "describe_place":
            return "taking in the place"
        if tool == "describe_primitives":
            return "reading up on what the town can do"
        if tool == "quote_action_cost":
            return "checking what an action would cost"
        return tool.replace("_", " ")

    @app.get("/world/conversations")
    async def world_conversations(limit: int = 40) -> dict[str, Any]:
        """Every conversation the town is holding, with its roster.

        The Talk panel could group utterances by `conversation_id` but had
        no way to say who was *in* a conversation - only who had produced
        text. A resident standing in the room, addressed by name, saying
        nothing, was invisible. This is the roster, the place, whether the
        conversation is still open, and the remarks, so the panel can show
        a group chat as a group.
        """
        names = _genesis_names()
        lines_by_conversation: dict[str, list[dict[str, Any]]] = {}
        for record in world_state.utterances:
            conversation_id = record.get("conversation_id")
            if conversation_id:
                lines_by_conversation.setdefault(str(conversation_id), []).append(record)

        world_state.close_stale_conversations(world_state.tick())
        conversations = []
        for record in world_state.conversations.values():
            lines = lines_by_conversation.get(record["conversation_id"], [])
            conversations.append({
                "conversation_id": record["conversation_id"],
                "topic": record["topic"],
                "place": record["place"],
                "open": record["open"],
                "opened_tick": record["opened_tick"],
                "last_tick": record["last_tick"],
                "turns": len(lines),
                "participants": [
                    {"agent_id": a, "name": names.get(a) or a}
                    for a in record["participants"]
                ],
                "deals": list(record.get("deals") or []),
                "outcome": record.get("outcome") or conversation_outcome(record),
                "lines": [
                    {
                        "tick": line.get("tick"),
                        "said_at": line.get("said_at"),
                        "speaker_agent_id": line.get("speaker_agent_id"),
                        "speaker_name": (
                            names.get(line.get("speaker_agent_id") or "")
                            or line.get("speaker_name")
                            or line.get("speaker_agent_id")
                        ),
                        "listener_name": (
                            names.get(line.get("listener_agent_id") or "")
                            or line.get("listener_name")
                        ),
                        "content": line.get("content"),
                    }
                    for line in lines
                ],
            })
        conversations.sort(key=lambda c: (c["last_tick"], c["turns"]), reverse=True)
        conversations = conversations[:limit]
        return {
            "count": len(conversations),
            "open_count": sum(1 for c in conversations if c["open"]),
            "group_count": sum(1 for c in conversations if len(c["participants"]) > 2),
            "conversations": conversations,
        }

    @app.get("/world/recorder")
    async def world_recorder() -> dict[str, Any]:
        """What the recorder has seen, and what it kept (CORE-24).

        `TraceStore.stats()` has existed since the recorder was written and
        had no route, so a run's drop count lived only in a WARNING line.

        The per-type counts below exist for a specific unsolved problem:
        run 24 made eleven successful `post_price` calls and the `events`
        table held zero `economy.price.posted` rows, while other tool
        events from the same run (`politics.candidacy.declared`) recorded
        normally. Published-versus-recorded, per type, is the measurement
        that tells the two apart - whether the event never reached the bus,
        or reached it and was dropped on the way to the table.
        """
        published = dict(sorted(PUBLISHED_BY_TYPE.items()))
        recorded = dict(sorted(RECORDED_BY_TYPE.items()))
        return {
            "published_by_type": published,
            "recorded_by_type": recorded,
            "published_but_not_recorded": {
                t: n for t, n in published.items() if t not in recorded
            },
            "published_total": sum(published.values()),
            "recorded_total": sum(recorded.values()),
        }

    @app.get("/world/link")
    async def world_link() -> dict[str, Any]:
        """WIRE-02. How the engines' link to this world is behaving, now.

        The counters live on each engine's `WorldClient` and are served
        on its own `GET /health`. Nothing read them: the runner polls
        that endpoint once, at startup, and looks only at the status
        code. An instrument with no consumer is ENG-16 - a fix that
        reaches nobody is indistinguishable from no fix.

        This is the consumer, and it is a ROUTE rather than only a
        shutdown line because 97 lost calls in run `deni-a6f9eb4-5h`
        would have been worth knowing at tick 40 rather than at the end.

        `lost_share` is the number to watch: a lost call is
        indistinguishable from a refusal to the resident that made it, so
        a run with losses has residents reasoning from "the world would
        not let me" about acts the world never heard of.
        """
        if wire_health is None:
            return {"engines_reporting": 0, "engines_expected": 0, "lost_share": 0.0}
        totals = await wire_health()
        attempted = totals.get("world_calls_attempted", 0)
        lost = totals.get("world_transport_failures", 0)
        return {
            **totals,
            "lost_share": round(lost / attempted, 4) if attempted else 0.0,
            "alarm_at": WIRE_LOSS_ALARM,
            "above_alarm": bool(attempted and lost / attempted >= WIRE_LOSS_ALARM),
        }

    @app.get("/world/tool_invocations")
    async def world_tool_invocations() -> dict[str, Any]:
        """Every tool call this run has made, for the report's C1 join.

        EVAL-03. `task_label` is a fact about a TURN, decided by which tools
        the turn ended up calling - which nobody knows at the moment the
        model call is made, so the broker cannot honestly write the column.
        `stakes_report.label_calls` joins it afterwards by (agent_id, tick).

        That function had four tests and no production caller, and
        `tool_invocations` is an in-memory ring with no table behind it, so
        the join had no source either. This is the source. It is the same
        "report reads live state" shape the money and price sections already
        use, and it carries the same limit: it must be called while the
        world is still running.

        Unbounded by design within the ring (5000 entries) - a truncated
        join would silently mislabel turns rather than fail.
        """
        return {
            "invocations": [
                {
                    "agent_id": record.actor_id,
                    "tick": record.tick,
                    "tool_name": record.tool_name,
                    "program_id": record.program_id,
                    "program_step": record.program_step,
                }
                for record in world_state.tool_invocations
            ]
        }

    @app.get("/world/activity")
    async def world_activity(per_agent: int = 6) -> dict[str, Any]:
        """What each resident is actually doing, right now.

        The viewer used to answer "click an agent, see what they are doing"
        out of dormancy and health alone - so a healthy, busy farmer read as
        a blank card. The town already knew: every completed call appends a
        ToolInvocationRecord, and `say` appends an utterance. This joins the
        two per agent and hands back the tail, newest last.

        Cheap on purpose. It walks the two in-memory rings backwards and
        stops at `per_agent` rows for each resident, so the cost is bounded
        by the rings (5000 / capped utterances), not by the length of the run.
        """
        names = _genesis_names()
        recent: dict[str, list[dict[str, Any]]] = {}

        for record in reversed(world_state.tool_invocations):
            actor = record.actor_id
            rows = recent.setdefault(actor, [])
            if len(rows) >= per_agent:
                continue
            rows.append({
                "kind": "tool",
                "agent_id": actor,
                "tick": record.tick,
                "tool": record.tool_name,
                "params": {k: str(v)[:120] for k, v in list(record.params.items())[:6]},
                "namespace": record.namespace,
            })

        for record in reversed(world_state.utterances):
            actor = record.get("speaker_agent_id") or ""
            if not actor:
                continue
            rows = recent.setdefault(actor, [])
            if len(rows) >= per_agent * 2:
                continue
            rows.append({
                "kind": "said",
                "tick": record.get("tick"),
                "text": (record.get("text") or "")[:400],
                "to": record.get("listener_agent_id"),
                "to_name": names.get(record.get("listener_agent_id") or ""),
                "to_names": [
                    names.get(other) or other
                    for other in (record.get("addressed_agent_ids") or [])
                    if other
                ],
            })

        out = {}
        for agent_id, rows in recent.items():
            rows.sort(key=lambda r: r.get("tick") or 0)
            latest = rows[-1] if rows else None
            out[agent_id] = {
                "name": names.get(agent_id) or agent_id,
                "doing": _phrase_activity(latest),
                "at_tick": (latest or {}).get("tick"),
                "recent": rows[-per_agent:],
            }

        for agent_id in world_state.agents:
            out.setdefault(agent_id, {
                "name": names.get(agent_id) or agent_id,
                "doing": "has not acted yet this run",
                "at_tick": None,
                "recent": [],
            })

        return {"tick": world_state.tick(), "count": len(out), "agents": out}

    @app.get("/world/authored")
    async def world_authored() -> dict[str, Any]:
        """What the town has invented and written down - `toolify` and
        `skillify` output, per agent, so the viewer can show which residents
        are authoring rather than only that something was authored."""
        skills = [
            {
                "name": d.name,
                "author_agent_id": d.author_agent_id,
                "steps": len(d.steps),
                "discounts_tools": d.discounts_tools,
                "governmental": d.governmental,
                "status": d.status,
                "version": d.version,
                "created_tick": d.created_tick,
                "holders": [
                    aid for aid, held in world_state.skills.items() if d.name in held
                ],
            }
            for d in world_state.skill_definitions.values()
        ]
        tools = [
            {
                "tool_name": t.tool_name,
                "creator_agent_id": t.creator_agent_id,
                "steps": [s.tool_name for s in t.steps],
                "version": t.version,
                "patented": t.patent_application_id is not None,
            }
            for t in world_state.custom_tools.values()
        ]
        names = _genesis_names()
        for row in skills:
            row["author_name"] = names.get(row["author_agent_id"]) or row["author_agent_id"]
        for row in tools:
            row["creator_name"] = names.get(row["creator_agent_id"]) or row["creator_agent_id"]
        return {
            "skills": skills,
            "tools": tools,
            "names": names,
            "skill_count": len(skills),
            "tool_count": len(tools),
            "proficiency": world_state.skills,
            "repetitions": world_state.skill_reps,
        }

    @app.get("/world/refusals")
    async def world_refusals(limit: int = 5000) -> dict[str, Any]:
        """Every intention the world refused, for the observatory (:8500).

        Read-only and deliberately dumb: it returns rows, and the grouping
        into named capability gaps happens in the observatory, which owns no
        state and can therefore be restarted, rewritten or pointed at a
        different run without touching the experiment.
        """
        rows = world_state.tool_refusals[-limit:]
        return {"count": len(rows), "refusals": [r.model_dump() for r in rows]}

    @app.get("/world/justice")
    async def world_justice() -> dict[str, Any]:
        """GOV-10: the false-conviction rate. Research-side only - see
        `institutions.justice_reading`; no tool reads it."""
        from world.tools.primitives.institutions import justice_reading

        return justice_reading(world_state)

    @app.get("/tools/registry/version")
    async def tools_registry_version() -> dict[str, int]:
        return {"version": registry.version}

    @app.get("/world/engines")
    async def world_engines(verbose: int = 0) -> dict[str, Any]:
        """Engine liveness, for the observatory's believability panel.

        observatory/app.py's `probe_engines` has asked for this route since
        Part C and nothing ever served it, so "engines alive" read 0 / 0 on
        a run with fourteen healthy engines - the one number on that page
        that says whether the instrument itself is working.
        """
        if engine_liveness is None:
            return {"alive": 0, "expected": 0, "dormant": 0, "down": []}
        body = await engine_liveness()
        if verbose:
            body["output"] = engine_output() if engine_output else {}
        return body

    @app.post("/agents/{agent_id}/tool_calls")
    async def tool_calls(agent_id: str, req: Request) -> dict[str, Any]:
        """The one place a tool result leaves the world for a resident to read.

        Which makes it the one place the names go back on. `world/tools/
        names.py` resolved a name INTO an id on the way in and nothing put a
        name back on the way out, so the town kept two words for every person
        and joined them nowhere: the perception said "Marta Thorne (n07)",
        the roster said "Marta Thorne (baker)", and every result and refusal
        said "n07". Run 28b, Silas at the mill, reading `people_here:
        ["n02"]`: *"Someone is here - n02"*, then `say(content="You there.")`
        to a neighbour of forty years.

        Here rather than in each handler, for the reason the audit gives:
        ninety-five tools would each have to remember, and the one that
        forgot would be found in a run report. And here rather than deeper,
        because the raw result is what a program binds, what the trace
        stores and what the game draws - the id is right for all three.
        """
        body = await req.json()
        tool_name = body.get("tool", "")
        tool_input = body.get("input", {}) or {}
        try:
            result = await registry.execute(world_state, agent_id, tool_name, tool_input)
            spoken = names.name_the_numbers(world_state, result.result)
            return {"result": json.dumps(spoken, default=str)}
        except ToolError as exc:
            detail: dict[str, Any] = {
                "error": names.name_mentions(world_state, str(exc)),
            }
            if getattr(exc, "context", None):
                detail["context"] = names.name_the_numbers(world_state, exc.context)
            detail = json.loads(json.dumps(detail, default=str))
            raise HTTPException(status_code=400, detail=detail) from exc
        except Exception as exc:  # noqa: BLE001 - sibling-system errors, see registry.py
            raise HTTPException(
                status_code=400,
                detail={"error": names.name_mentions(world_state, str(exc))},
            ) from exc

    @app.post("/agents/{agent_id}/memories")
    async def record_memories(agent_id: str, req: Request) -> dict[str, Any]:
        """Intake for the engine's own memory stream (engine/src/sim/
        memory_stream.py).

        The engine writes observation/plan/reflection records as it thinks;
        this publishes each one on the world event bus as
        `agent.memory.recorded`, which world/wiring.py's TraceStore already
        subscribes to and turns into a `memories` row. Without this endpoint
        the producer and the consumer both existed and were never connected -
        the stream reached a file on the engine's disk and stopped there.

        Deliberately NOT a tool: recording what you just did is not a
        decision an agent makes, and putting it in the tool registry would
        let a model choose to lie about its own memory or skip writing one.
        """
        body = await req.json()
        records = body if isinstance(body, list) else [body]
        published = 0
        for record in records:
            memory_type = record.get("memory_type")
            if memory_type not in ("observation", "reflection", "plan"):
                continue
            await world_state.event_bus.publish(
                WorldEvent(
                    type="agent.memory.recorded",
                    tick=int(record.get("tick", world_state.tick())),
                    payload={
                        "agent_id": agent_id,
                        "memory_type": memory_type,
                        "content": record.get("content", ""),
                        "importance": record.get("importance", 3),
                        "related_memory_ids": record.get("related_memory_ids") or [],
                    },
                )
            )
            published += 1
        return {"recorded": published}

    @app.get("/ledger/payer/{agent_id}")
    async def ledger_payer(agent_id: str, purpose: str = "", amount_cents: int = 0) -> dict[str, Any]:
        try:
            payer_id = ledger.payer_for(agent_id, amount_cents, purpose)
            account = ledger.get_account(agent_id)
        except (AccountNotFoundError, ParentNotConfiguredError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "agent_id": agent_id,
            "payer_agent_id": payer_id,
            "balance_cents": ledger.usd_balance(payer_id),
            "is_minor": account.is_minor,
            "tick": world_state.current_tick,
        }

    @app.post("/ledger/debit")
    async def ledger_debit(req: Request) -> dict[str, Any]:
        body = await req.json()
        agent_id = body["agent_id"]
        amount_cents = int(body["amount_cents"])
        purpose = body.get("purpose", "")
        try:
            tx = await ledger.burn_for_inference(
                agent_id, amount_cents, world_state.current_tick, memo=purpose
            )
        except AccountNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LedgerInsufficientFundsError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        except LedgerHaltedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        payer_id = tx.from_account
        metrics.record_burn_sample()
        return {"payer_agent_id": payer_id, "balance_cents_after": ledger.usd_balance(payer_id)}

    @app.get("/metrics")
    async def metrics_endpoint() -> dict[str, Any]:
        return metrics.snapshot(game_day=timestamp_for(world_state.current_tick).game_day)

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket) -> None:
        """The event relay the game front end has always expected here.

        UI-06. DESIGN section 9 says "the game app is fed by the world
        server's own WebSocket relay (no external realtime)", and
        `world/main.py` has served exactly that for a long time. The
        EMBEDDED app - the one every actual run boots - never had the
        route.

        So `game/relay.py` connected to `ws://<world>/ws/events`, got a
        404, and `HttpWorldClient.stream_events` emitted
        `world.connection.lost` every two seconds forever. No
        `agent.moved` and no `world.tick.advanced` ever arrived, so on the
        Phaser page the sprites never moved and the clock never advanced,
        for the whole life of the project. Two composition roots, one of
        them wired.

        The same route, on the host that runs.
        """
        await websocket.accept()

        async def on_event(event: Any) -> None:
            await websocket.send_json(event.model_dump(mode="json"))

        catch_up = list(world_state.event_bus.recent())[-WS_REPLAY_EVENTS:]
        world_state.event_bus.subscribe("*", on_event)
        for event in catch_up:
            try:
                await on_event(event)
            except (TypeError, ValueError):
                logger.exception("ws replay could not encode %s", event.type)
            except Exception:  # noqa: BLE001
                logger.debug("ws client left during catch-up")
                break
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            world_state.event_bus.unsubscribe("*", on_event)

    return app


WS_REPLAY_EVENTS = 500


OPENING_GATHERING_PLACE = "Kasukabe Market Square"


def describe_drain(drained: dict[str, bool], *, halted: bool) -> str:
    """One line saying what the end-of-run drain actually consolidated.

    Run 22 printed "end-of-run learning drain: 20 of 20 resident(s)
    consolidated" 235 milliseconds after "the broker has halted dispatch
    ... every further call would be refused". It had written zero
    knowledge rows and left 7,138 signals unconsolidated: every model call
    the pipeline made was refused, the pipeline returned a result object
    anyway, and `result is not None` read that as success.

    A green line over work that did not happen is worse than a red one,
    because nobody investigates it. This is the same defect as
    `batches_failed: 0` over a deleted inode and a memory check passing one
    second before the runner refused the same numbers - and it appeared in
    code written to fix a different instance of it.

    So: count residents who actually learned something (the engine now
    reports `knowledge_written`, not "a batch was attempted"), and name the
    halt when there was one, because a drain against a halted broker is a
    run whose last stretch was thrown away however many engines answered.
    """
    if not drained:
        return "end-of-run learning drain: no engines left to consolidate"
    learned = sum(1 for ran in drained.values() if ran)
    line = f"end-of-run learning drain: {learned} of {len(drained)} resident(s) consolidated"
    if halted:
        line += (
            " - the broker had already halted dispatch, so any batch that "
            "needed a model call was refused"
        )
    return line


def household_location_id(household_id: str) -> str:
    """Placeholder location scheme (module docstring's known-gaps section):
    real tile placement is world/map/'s job, not built here. Every agent
    starts at their own household so location-gated tools and perception
    have something real to key off; agents `move` themselves from there."""
    return f"home:{household_id}"


def business_location_id(business_id: str) -> str:
    return f"business:{business_id}"


class GenesisRunner:
    """Owns one live genesis run end to end: boot, launch, tick, checkpoint,
    resume, stop. See module docstring for the architecture decisions this
    class embodies."""

    def __init__(self, config: GenesisRunnerConfig) -> None:
        self.config = config
        self.scenario = Scenario.load(config.scenario_path) if config.scenario_path else None
        if self.scenario is not None:
            plan = self.scenario.world
            differs = [
                f"{name}: config {mine!r}, scenario {theirs!r}"
                for name, mine, theirs in (
                    ("start_tick", config.start_tick, plan.start_tick),
                    ("max_ticks", config.max_ticks, plan.stop_tick),
                )
                if theirs is not None and mine != theirs
            ]
            if differs:
                logger.warning(
                    "scenario %s is not what this run is doing (%s)",
                    self.scenario.name, "; ".join(differs),
                )
        self.depth = RetrievalDepthConfig(fraction=config.retrieval_depth_fraction)
        self.counters = RunnerCounters(retrieval_depth_fraction=config.retrieval_depth_fraction)
        self.rng = random.Random(config.population_seed)

        self.world_state: WorldState | None = None
        self.registry: ToolRegistry | None = None
        self.tick_engine: TickEngine | None = None
        self.ledger: Ledger | None = None
        self.metrics: GenesisMetrics | None = None
        self.governor: SpendGovernor | None = None
        self.recent_steps_used: deque[int] = deque(maxlen=200)
        self.barren_ticks = 0
        self.first_barren_tick_at: float | None = None
        self.timed_out_ticks = 0
        self.dispatch_blocked_until: dict[str, float] = {}
        self.dispatch_skipped_ticks = 0
        self._last_drained_invocation_id: str | None = None
        self.invocations_lost = 0
        self._drained_before_halt = False
        self._controls: "list[Any] | None" = None
        self.learning_drained = 0
        self.day_jobs_ran: dict[int, dict[str, int]] = {}

        self.charter_cache: dict[str, Any] = {}
        self.budget_hit_last_turn: dict[str, int] = {}
        self.halt_reason: str | None = None
        from world.ledger.head_tax import HeadTaxDebtStore

        self.head_tax_debts = HeadTaxDebtStore()
        self.fed_today: set[str] = set()
        self.turns_lost_to_errors = 0
        self.hunger_by_agent: dict[str, Any] = {}
        self._tick_had_a_success = False
        self._tick_had_a_dispatch = False
        self.checkpoint_store = LocalCheckpointStore(config.checkpoint_dir)

        self.supervisor: Supervisor | None = None
        self.resources: ResourceSampler | None = None
        self.watchdog: SupervisorWatchdog | None = None
        self.groundskeeper: Any | None = None
        self.gateway: AgentGateway | None = None
        self.embedded_world_server: uvicorn.Server | None = None
        self.embedded_world_server_task: asyncio.Task[None] | None = None
        self.world_app: FastAPI | None = None
        self.ports_by_agent: dict[str, int] = {}
        self.agent_bios: dict[str, str] = {}
        self.agent_names: dict[str, str] = {}
        self.genesis_by_id: dict[str, Any] = {}
        self.trace_store: Any = None
        self.crime: Any = None
        self.crime_engine_wired = False
        self.run_manifest_path: Path | None = None
        self.buildings_completed = 0
        self.pensions_paid = 0
        self.pensions_without_a_salary = 0
        self.wire_health: dict[str, int] = {}
        self.payroll_paid_sim = 0
        self.payroll_lines_paid = 0
        self.payroll_unpayable = 0
        self.payroll_skipped_confined = 0
        self.map_service: Any = None
        self._home_tiles: dict[str, tuple[int, int]] = {}
        self._house_names: list[str] | None = None
        self.ticks_that_overran = 0
        self.dormant_skips = 0
        self.max_tick_spend_cents = 0.0
        self._warned_no_duckdb = False
        self.roster_seeded: set[str] = set()
        self.roster_seen_by: dict[str, set[str]] = {}
        self.night_skips = 0

        self.population_dials = PopulationDials(
            target_band_min=DEFAULT_TARGET_BAND[0], target_band_max=DEFAULT_TARGET_BAND[1]
        )
        self.sim_control = SimControl()
        self.admin_extras: AdminExtras | None = None

        self.stop_requested = False

    def set_population_dials(self, **updates: Any) -> PopulationDials:
        """Mirrors wiring.py's WorldServer.set_population_dials exactly
        (same name, same shape) - world/admin_api.py's AdminDeps.
        set_population_dials closure calls this directly for a live run,
        the same way world/main.py's calls WorldServer's copy."""
        self.population_dials = self.population_dials.model_copy(update=updates)
        return self.population_dials


    async def boot(self, resume: bool = True) -> None:
        """Assembles the world (build_world() + genesis.json), and either
        restores it from the newest local checkpoint (resume=True and one
        exists) or seeds it fresh from genesis."""
        restore_engine_homes(self.config.home_root, self.config.checkpoint_dir)
        self.build_the_map()
        self.world_state, self.registry = await build_world(
            genesis_tick=0,
            seed=self.config.population_seed,
            world_map=self.map_service.world if self.map_service else None,
            charter_fields=self.scenario.charter_overrides() if self.scenario else None,
        )
        self.ledger = self.world_state.ledger
        self.world_state.hunger_by_agent = self.hunger_by_agent
        self.head_tax_debts = self.world_state.head_tax_debts
        self.wire_the_charter_to_the_bus()
        self.build_the_crime_engine()

        scheduler_config = SchedulerConfig(target_fraction=self.config.scheduler_target_fraction)
        pacing_config = PacingConfig(mode=PacingMode.CONTINUOUS, continuous_interval_s=0.0)
        checkpoint_sink = self.build_checkpoint_sink()
        self.tick_engine = TickEngine(
            event_bus=self.world_state.event_bus,
            scheduler_config=scheduler_config,
            checkpoint_sink=checkpoint_sink,
            pacing_config=pacing_config,
            tick_index=self.config.effective_start_tick(),
            hearing_window_ticks=self.depth.hearing_window_ticks,
            movement_system=self.world_state.movement,
        )
        self.tick_engine.checkpoints.register("ledger", LedgerCheckpointAdapter(self.ledger))
        self.tick_engine.checkpoints.register(
            "world_state", WorldStateCheckpointAdapter(self.world_state)
        )
        self.tick_engine.checkpoints.register("world_locations", self.world_state.locations)
        self.tick_engine.checkpoints.register(
            "runner_counters", RunnerCountersCheckpointAdapter(self.counters)
        )
        self.tick_engine.checkpoints.register(
            "charter", CharterCheckpointAdapter(self.world_state.charter)
        )
        self.tick_engine.checkpoints.register(
            "records", RecordCheckpointAdapter(self.world_state)
        )

        self.tick_engine.checkpoints.register(
            "stakes", StakesCheckpointAdapter(self)
        )

        if self.crime is not None:
            self.tick_engine.checkpoints.register(
                "crime", CrimeStoreCheckpointAdapter(self.crime.store)
            )

        if self.map_service is not None:
            self.tick_engine.checkpoints.register(
                "map", MapCheckpointAdapter(self.map_service, self.world_state)
            )

        self.metrics = GenesisMetrics(
            self.ledger, self.world_state, self.counters,
            budget_cap_cents=self.config.budget_cap_cents,
        )
        self.governor = SpendGovernor(self.ledger, self.config.budget_cap_cents)

        from world.life.genesis import RichGenesis

        self.genesis = RichGenesis.model_validate_json(
            self.config.genesis_path.read_text(encoding="utf-8")
        )
        prune_ties_to_people_not_in_the_cast(self.genesis)

        os.environ["KASUKABE_GENESIS_PATH"] = str(self.config.genesis_path)
        os.environ["KASUKABE_ENGINE_REPO"] = str(self.config.engine_repo)

        for a in self.genesis.agents:
            self.agent_names[a.agent_id] = a.name
            self.agent_bios[a.agent_id] = a.bio
            self.genesis_by_id[a.agent_id] = a

        if self.world_state is not None:
            self.world_state.agent_names.update(self.agent_names)
            for a in self.genesis.agents:
                occupation = str(getattr(a, "occupation", "") or "")
                if not occupation:
                    continue
                self.world_state.occupations[a.agent_id] = occupation
                workplace = WORKPLACE_BY_OCCUPATION.get(occupation)
                if workplace:
                    self.world_state.workplaces[a.agent_id] = workplace

        await self.attach_persistence()

        latest = self.checkpoint_store.latest() if resume else None
        target = self.config.resume_at_tick
        if target is not None and latest is None:
            raise ValueError(
                f"resume_at_tick={target} asks to continue a run, but there is no "
                f"checkpoint to resume in {self.config.checkpoint_dir}"
                + ("" if resume else " (and resume was switched off)")
            )
        if latest is not None:
            logger.info("resuming from checkpoint at tick %s", latest.tick_index)
            await self.tick_engine.restore_from_checkpoint(latest)
            if self.counters.rng_state is not None:
                self.rng.setstate(self.counters.rng_state)
            self.depth.set_fraction(self.counters.retrieval_depth_fraction)
            self.world_state.current_tick = latest.tick_index
            await self.register_the_restored_cast(latest.tick_index)
            restored = self.world_state.tool_invocations
            self._last_drained_invocation_id = (
                restored[-1].invocation_id if restored else None
            )
            if os.environ.get("KASUKABE_ELECTION_NOW", "").strip() == "1":
                existing = await self.world_state.charter.store.list_elections()
                live = [e for e in existing if e.phase is not ElectionPhase.TALLIED]
                waiting = [
                    e for e in live
                    if e.phase is ElectionPhase.SCHEDULED
                    and e.candidacy_opens_tick > latest.tick_index
                ]
                if live and not waiting:
                    logger.info(
                        "KASUKABE_ELECTION_NOW: a ballot is already under way, "
                        "leaving it alone"
                    )
                elif waiting:
                    w = max(1, int(os.environ.get("KASUKABE_ELECTION_WINDOW", "6")))
                    start = latest.tick_index
                    for e in waiting:
                        e.candidacy_opens_tick = start
                        e.candidacy_closes_tick = start + w
                        e.voting_opens_tick = start + 2 * w
                        e.voting_closes_tick = start + 3 * w
                        await self.world_state.charter.store.put_election(e)
                    shift = 3 * w
                    logger.warning(
                        "KASUKABE_ELECTION_NOW: election %s runs now over %s ticks "
                        "- candidacy %s-%s, voting %s-%s, counted %s. An operator "
                        "moved and shortened this election.",
                        waiting[0].election_id, shift,
                        waiting[0].candidacy_opens_tick,
                        waiting[0].candidacy_closes_tick,
                        waiting[0].voting_opens_tick,
                        waiting[0].voting_closes_tick,
                        waiting[0].voting_closes_tick,
                    )
                else:
                    await self.world_state.charter.elections.schedule_election(
                        latest.tick_index
                    )
                    logger.warning(
                        "KASUKABE_ELECTION_NOW: opened a ballot at tick %s rather "
                        "than waiting for the charter's next one - an operator "
                        "brought this election forward",
                        latest.tick_index,
                    )
            if target is not None:
                if target < latest.tick_index:
                    raise ValueError(
                        f"resume_at_tick={target} is before the checkpoint's tick "
                        f"{latest.tick_index}; a resumed run cannot go back in time"
                    )
                self.tick_engine.clock.tick_index = target
                self.world_state.current_tick = target
                then = timestamp_for(target)
                logger.info(
                    "resumed from tick %s and moved the clock to tick %s "
                    "(day %s %02d:%02d): %s ticks skipped",
                    latest.tick_index, target, then.game_day, then.hour,
                    then.minute, target - latest.tick_index,
                )
        else:
            logger.info("no checkpoint found, seeding fresh genesis")
            await self.seed_genesis()
            self.brief_staff_to_their_workplace()
            self.counters.last_life_day_processed = timestamp_for(
                self.world_state.current_tick
            ).game_day

        self.world_state.current_tick = self.tick_engine.clock.tick_index

        self.world_state.event_bus.size_for_population(len(self.world_state.agents))

        await self.apply_tick_pacing()

        self.metrics.record_burn_sample()
        self.write_the_run_manifest()

    async def register_the_restored_cast(self, tick: int) -> None:
        """Tell the trace recorder who lives in a resumed town.

        Run 28b resumed into a fresh trace store with `known_agent_ids`
        empty, because `TraceStore.seed_agent` - the only place it is
        filled - runs on the fresh-genesis path. Every utterance was dropped
        as "speaker was never seeded", and every transaction and event was
        written with no resident on it. Resuming into the SAME store had
        the same hole: the rows were there and the recorder still did not
        know them.

        `seed_agent` registers a resident whose row already exists
        (CORE-13), so this one call is right for either store. A business
        already present is left as it is.
        """
        if self.trace_store is None or self.world_state is None:
            return
        failures: list[str] = []
        for agent_id, record in self.world_state.agents.items():
            try:
                await self.trace_store.seed_agent(
                    record, self.genesis_by_id.get(agent_id), tick=tick
                )
            except Exception as exc:  # noqa: BLE001 - counted and reported below
                failures.append(f"{agent_id}: {exc}")
        for business in self.world_state.businesses.values():
            try:
                await self.trace_store.seed_business(business, tick=tick)
            except Exception:  # noqa: BLE001 - already present on a same-store resume
                pass
        logger.info(
            "registered the restored cast with the trace recorder: %d of %d residents",
            len(self.trace_store.known_agent_ids), len(self.world_state.agents),
        )
        if failures:
            logger.warning(
                "could not register %d restored resident(s) with the recorder: %s",
                len(failures), "; ".join(failures[:3]),
            )

    def brief_staff_to_their_workplace(self) -> int:
        """Point every business's owner and hands at that business.

        GROW-10. `WORKPLACE_BY_OCCUPATION` is keyed on TRADE, so it sent
        every `construction_worker` to the abstract "carpentry" while the
        company's premises are `business:b_yard` - a genesis-specific id
        no static table can name. The hands were briefed to a place their
        employer was not, and run 23 has Silas Vance standing alone at the
        yard saying "the carpenter needs timber" with nobody there to hear
        him.

        The premises win because they are strictly MORE: TOOL-02 gave the
        yard `provides_workshops={"carpentry", "sawmill"}`, so
        `satisfies_gate_in` lets a resident standing there do carpentry
        and be with the people they work with. Briefing them to
        "carpentry" bought the first and threw away the second.

        Returns how many were re-briefed, so a run can say so instead of
        leaving this as one more mechanism nobody can tell fired.
        """
        if self.world_state is None:
            return 0
        moved = 0
        for business in self.world_state.businesses.values():
            if not isinstance(business, Business) or not business.location_id:
                continue
            staff = set(business.employee_agent_ids or set())
            if business.owner_agent_id:
                staff.add(business.owner_agent_id)
            for agent_id in staff:
                if self.world_state.workplaces.get(agent_id) == business.location_id:
                    continue
                self.world_state.workplaces[agent_id] = business.location_id
                moved += 1
        if moved:
            logger.info(
                "GROW-10: briefed %d resident(s) to their employer's premises "
                "rather than to their trade's generic workplace", moved,
            )
        return moved

    def employer_premises_for(self, agent_id: str) -> "str | None":
        """Where this resident's employer stands, if it has one.

        GROW-10. Owner or hand, either counts: the point is that the
        people of one business are briefed to the same address, so they
        can find each other. Returns None for a resident who staffs no
        business, and the trade table answers for them as it always did.

        The business's own `location_id`, not a guess: that is the string
        `business_location_id()` minted when the business was seeded, it
        is what `is_real_destination` accepts, and it is what the premises
        gate folds onto a workshop.
        """
        if self.world_state is None:
            return None
        for business in self.world_state.businesses.values():
            if not isinstance(business, Business):
                continue
            if agent_id == business.owner_agent_id or agent_id in (
                business.employee_agent_ids or set()
            ):
                return business.location_id
        return None

    def may_dispatch_to(self, agent_id: str) -> bool:
        """Whether this resident's engine is worth asking right now.

        INFRA-29. False only while a previous turn for this resident timed
        out recently enough that it is probably still running. The engine
        serves one turn at a time behind `_turn_lock`, so a tick sent into
        that window does not run - it waits, times out, and costs the run
        another `tick_timeout_s` of wall clock for a turn that was never
        going to happen.

        Time-based, and no longer the only answer. CORE-11 added the real
        one: `/tick` returns 409 while a turn is in flight, so the engine
        now says whether it is busy instead of being guessed at. This
        remains as the cheap pre-filter - it skips a resident without
        making the HTTP call at all - and the 409 is what decides the
        cases it gets wrong.

        The comment that used to sit here said the engine "has no route
        that answers 'are you busy', and inventing one would put the fix in
        frozen code". The second half was false: `agent_adapter.py` and
        `pinned_boot.py` are both `engine/src/sim/`, the seam ROADMAP Law 1
        leaves writable, and the review's own fix for CORE-11 says so. A
        wrong sentence in a docstring is how a fixable thing stays filed as
        impossible.
        """
        until = self.dispatch_blocked_until.get(agent_id)
        if until is None:
            return True
        if time.monotonic() >= until:
            del self.dispatch_blocked_until[agent_id]
            return True
        return False

    async def act_the_controls(self, tick: int) -> list[str]:
        """One tick for every scripted control business. Never raises.

        EVAL-15, and ECON-25 in its own words ("`control_bakery.py` and
        `stakes_report.py` are finished and unwired"). DESIGN section 10
        puts ten rule-based businesses in the town as a baseline to
        compare agent-run ones against, and
        `control_bakery.py` implements one completely - six rules, a
        refusal taxonomy for R7, its own tests. Nothing called it. The
        comparison the controls exist for has never been made.

        Built lazily and once: the bakeries are stateful (they remember
        what they posted today and who they have paid through), so
        rebuilding them per tick would reset exactly the memory the rules
        turn on.

        A control that can end the run is a control that decides what the
        treatment's data looks like, so a failure here is logged and
        counted rather than raised - the same rule `act()` itself follows,
        for the same reason.
        """
        if self.world_state is None or self.registry is None:
            return []
        if self._controls is None:
            self._controls = self.build_controls()
        fired: list[str] = []
        for control in self._controls:
            try:
                fired.extend(await control.act(self.world_state, self.registry, tick))
            except Exception as exc:  # noqa: BLE001
                logger.warning("control %s failed at tick %d: %s",
                               control.business_id, tick, exc)
        return fired

    def register_control_owner(self, business: Any) -> str:
        """Stand up the scripted owner of one control business. Returns its id.

        EVAL-15. This used to pass `owner_id=business.business_id`, so the
        control bakery acted as `cb01` - not an agent - and every
        `registry.execute` it made was refused at the actor gate. Run 26
        logged 151 refused `post_price` calls and the control never once did
        anything, so the comparison R1 to R7 exist for had nothing on one
        side. `test_control_bakery.py` passed throughout because it spawned a
        real owner agent, which production never created.

        The owner is registered in `world_state.control_actors`, NOT
        `agents`: DESIGN section 10 makes these businesses "owner-less by
        design" and they are not operated by any persona. Everything that
        counts, bills, houses, elects or wakes residents iterates `agents`,
        so none of it sees this record. It gets exactly what acting through
        the registry needs and nothing more - a ledger account to pay action
        costs from, the business it owns, and a place to stand.

        Idempotent: a resumed world already has the accounts and the business
        row, and only the in-memory actor has to be put back.
        """
        from world.life.models import AgentLifeRecord, Sex

        assert self.world_state is not None and self.ledger is not None
        world = self.world_state
        owner_id = f"control:{business.business_id}"
        location_id = business_location_id(business.business_id)

        world.control_actors[owner_id] = AgentLifeRecord(
            agent_id=owner_id, age_years=40.0, sex=Sex.FEMALE,
        )
        world.agent_names.setdefault(owner_id, f"the keeper of {business.name}")
        try:
            self.ledger.get_account(owner_id)
        except Exception:  # noqa: BLE001 - absent is the ordinary first boot
            from world.life.constants import GENESIS_AGENT_STARTING_SIM_BALANCE

            self.ledger.open_agent(
                owner_id,
                starting_usd_cents=0,
                starting_sim=GENESIS_AGENT_STARTING_SIM_BALANCE,
            )
        try:
            self.ledger.get_account(business.business_id)
        except Exception:  # noqa: BLE001
            self.ledger.open_business(business.business_id, owner_id)
            self.ledger.get_account(business.business_id).sim_units = business.starting_sim_balance
        if business.business_id not in world.businesses:
            world.businesses[business.business_id] = Business(
                business_id=business.business_id,
                name=business.name,
                business_type=business.business_type,
                owner_agent_id=owner_id,
                location_id=location_id,
                provides_workshops=workshops_for_business_type(business.business_type),
            )
        world.locations.place(owner_id, location_id)
        return owner_id

    def build_controls(self) -> list[Any]:
        """The control businesses this genesis declares, as running objects.

        EVAL-15. Only the bakery has a script today - `control_bakery.py`
        is the one implemented - so a genesis naming ten controls yields
        one runner and nine that are declared and inert. Said out loud
        rather than silently skipped: nine unrun controls is nine baselines
        the agent-run businesses are not being compared against, which is
        the same defect this finding is about, one layer down.
        """
        from world.control_bakery import ControlBakery

        if self.genesis is None:
            return []
        declared = list(self.genesis.control_businesses or [])
        if not declared:
            return []

        controls: list[Any] = []
        unscripted: list[str] = []
        for business in declared:
            if business.business_type == "bakery":
                controls.append(ControlBakery(
                    business_id=business.business_id,
                    owner_id=self.register_control_owner(business),
                ))
            else:
                unscripted.append(f"{business.business_id} ({business.business_type})")
        if unscripted:
            logger.info(
                "EVAL-15: %d control business(es) are declared and have no "
                "script, so they are baselines nothing is compared against: %s",
                len(unscripted), ", ".join(unscripted),
            )
        return controls

    def _undrained(self, records: list) -> list:
        """The records this mirror has not written yet.

        Searched from the newest end, because in a healthy run the cursor is
        within a tick's worth of calls of it and this is on the per-tick
        path. A cursor that is no longer in the ring at all means the ring
        lapped past it between drains: those records are gone for good, so it
        counts them, says so once per occurrence, and writes what survives
        rather than giving up on the rest of the run.
        """
        cursor = self._last_drained_invocation_id
        if cursor is None:
            return list(records)
        for i in range(len(records) - 1, -1, -1):
            if records[i].invocation_id == cursor:
                return records[i + 1:]
        self.invocations_lost += len(records)
        logger.warning(
            "EVAL-25: the invocation ring lapped past the mirror's cursor %s - "
            "at least %d call(s) fell off the far end before they could be "
            "written, so this run's tool log has holes. Total lost so far: %d",
            cursor, len(records), self.invocations_lost,
        )
        return list(records)

    def _post_opening_prices(
        self, holder_id: str, prices: "dict[str, dict[str, int]] | None"
    ) -> None:
        """Hang this holder's opening price board.

        Stock with no price is the same dead end as no stock - `buy` answers
        "has not posted a price" and the shelf might as well be empty. The
        BID side is what gives a producer somewhere to sell, which is where
        income comes from in a town with no outside market.

        Written straight onto the board rather than through `post_price`,
        because that verb is an ACT by a resident standing in a shop and this
        is the board the shop opens with. `poster_id` is the holder itself,
        so anything reading the board can tell a genesis price from one a
        resident chose.
        """
        for item, sides in (prices or {}).items():
            ask, bid = sides.get("ask"), sides.get("bid")
            if ask is None and bid is None:
                continue
            self.world_state.put_price(
                holder_id,
                PricePost(
                    item=item, ask_sim=ask, bid_sim=bid,
                    posted_tick=0, poster_id=holder_id,
                ),
            )

    def _credit_opening_stock(self, holder_id: str, stock: "dict[str, int] | None") -> None:
        """Put a holder's declared opening stock into its hands.

        One place for all three kinds of holder - resident, shop and yard -
        because the rule is the same for each: a holder is given what genesis
        DECLARES it has, and nothing else. A holder that declares nothing
        gets nothing, which is what keeps this from being a subsidy.

        Goods are not money, so none of this touches the $1,000 USD ledger
        invariant.
        """
        for item, quantity in (stock or {}).items():
            if quantity > 0:
                self.world_state.inventory.credit(holder_id, item, quantity)

    async def drain_tool_invocations(self) -> int:
        """Mirror new tool calls into the trace store. Never raises.

        EVAL-25. `world/tools/state.py` promised this in a comment -
        "`tool_invocations` is mirrored into the trace store" - and
        nothing did it. The ring it describes holds 5,000 records and dies
        with the process, so every downstream figure came from scraping
        `session_log.jsonl` instead.

        Cursor-based rather than event-driven because there is no
        invocation EVENT to subscribe to: `registry.dispatch` appends to
        the list and emits nothing, and giving it a trace-store reference
        would put persistence inside the dispatch path that
        `ToolRegistry.execute` snapshots and rolls back. Reading the list
        from outside keeps the rollback meaning what it means - a record
        that was rolled back was never in the list to be drained.

        The ring is capped at 5,000. If a drain is ever skipped for longer
        than the town takes to make 5,000 calls, records fall off the far
        end and are simply lost - so this runs every tick, and says so
        when it notices the gap.

        The cursor is the id of the last record written, not an index into
        the list: the list is a ring `registry.dispatch` trims from the
        front, so an index silently stops meaning anything the moment the
        ring fills. See `_last_drained_invocation_id` for what that cost.
        """
        if self.trace_store is None or self.world_state is None:
            return 0
        records = self.world_state.tool_invocations
        if not records:
            return 0
        pending = self._undrained(records)
        if not pending:
            return 0
        try:
            from world.agents_api import RUN_ID

            written = await self.trace_store.record_tool_invocations(pending, RUN_ID)
            self._last_drained_invocation_id = pending[-1].invocation_id
            return written
        except Exception as exc:  # noqa: BLE001 - bookkeeping may not end a tick
            logger.warning("could not mirror tool invocations: %s", exc)
            return 0

    async def record_manifest(self) -> None:
        """One row saying what this run is, and a JSON copy beside its
        checkpoints. Never raises.

        INFRA-09. The trace recorded results in detail and almost nothing
        about the run itself - no app or engine commit, no genesis hash,
        no price table, no cap, no knobs - so runs 19-24 are already
        uncomparable in ways nobody can now reconstruct.

        Best-effort, like everything else on this path: a run must not die
        because it could not describe itself. But it says so loudly, since
        a run with no manifest is one whose results cannot be quoted
        against another's.
        """
        from world.agents_api import RUN_ID
        from world.run_manifest import build_manifest, write_manifest

        try:
            app_root = Path(__file__).resolve().parent.parent
            engine_root = Path(self.config.engine_repo) if self.config.engine_repo else None
            manifest = build_manifest(
                run_id=RUN_ID,
                config=self.config,
                app_root=app_root,
                engine_root=engine_root,
                started_at=time.time(),
            )
            write_manifest(manifest, Path(self.config.checkpoint_dir))
            if self.trace_store is not None:
                await self.trace_store.record_run_manifest(manifest)
            logger.info(
                "run %s: app=%s engine=%s genesis=%s",
                RUN_ID,
                manifest["app_commit"] or "unknown",
                manifest["engine_commit"] or "unknown",
                (manifest["genesis_sha256"] or "unknown")[:12],
            )
        except Exception as exc:  # noqa: BLE001 - describing a run may not end it
            logger.warning(
                "INFRA-09: this run could not record its own manifest, so its "
                "results cannot be compared with another run's: %s", exc
            )

    async def attach_persistence(self) -> None:
        """Give this runner the world-side trace writes it has never had.

        run_genesis is a second composition root: it builds its world
        through tools/bootstrap.build_world() and never calls
        wiring.build_world_server(), so TraceStore - the only thing that
        writes `agents`, `utterances` and `transactions` - was never
        constructed for a genesis run at all. Every conversation the town
        held lived in the bounded in-memory tail behind /world/utterances
        and was gone the moment the process exited, which is why a report
        written after a run could truthfully say it found no conversations
        while the run itself had been talking the whole time. The
        `utterances` table has had a writer since RC-2; this run just
        never had the writer.

        Scope, chosen rather than inherited: agents (the FK every other
        row hangs off), utterances, and transactions. Deliberately NOT
        record_event(), which mirrors every WorldEvent payload into
        `events` - that is an unbounded write proportional to tick count,
        and it is what grew an earlier run's trace store to 21 GB. The
        report needs conversations and money movements; it does not need
        a second copy of every perception.
        """
        if self.trace_store is not None:
            return
        try:
            from supabase.config import SupabaseConfig
            from supabase.factory import get_client

            from world.wiring import TraceStore

            trace_db = os.environ.get("TRACE_SQLITE_PATH", "data/kasukabe.db")
            config = SupabaseConfig.from_env().model_copy(
                update={"sqlite_path": trace_db}
            )
            self.trace_store = TraceStore(get_client(config))
        except Exception as exc:  # noqa: BLE001
            logger.warning("world-side persistence unavailable, not recording: %s", exc)
            self.trace_store = None
            return

        await self.record_manifest()

        store = self.trace_store
        ledger = self.ledger
        self.attach_eating()

        async def persist(event: WorldEvent) -> None:
            PUBLISHED_BY_TYPE[event.type] = PUBLISHED_BY_TYPE.get(event.type, 0) + 1
            try:
                await store.record_transaction_if_new(ledger, event)
                await store.record_utterance_if_new(event)
                await store.record_skill_if_new(event)
                await store.record_petition_if_new(event)
                await store.record_building_if_new(event)
                await store.record_case_if_new(event)
                await store.record_organization_if_new(event)
                await store.record_invented_tool_if_new(event)
                await store.record_memory_if_new(event)
                if is_worth_recording(event.type):
                    await store.record_event(event)
                    RECORDED_BY_TYPE[event.type] = RECORDED_BY_TYPE.get(event.type, 0) + 1
            except Exception as exc:  # noqa: BLE001
                await store.record_write_failure(event.type, exc)
                if store.is_halted:
                    logger.error(
                        "stopping: the world recorder has failed %d writes in a row "
                        "(last: %s on %s) and has dropped %d record(s) this run. "
                        "Everything the town does from here would go unrecorded.",
                        store.consecutive_failures, store.last_failure_message,
                        store.last_failure_type, store.rows_dropped,
                    )
                    self.request_stop()
            else:
                store.note_write_success()

        self.world_state.event_bus.subscribe("*", persist)
        logger.info("world-side persistence attached: agents, utterances, transactions")

    def attach_eating(self) -> None:
        """Make `eat` mean something: clear that resident's hunger.

        Hunger lives here, on the run loop, not on world state - life/
        upkeep.py owns HungerState and this dict is where it is kept - so
        the `eat` tool cannot reach it and does not try. It debits the food
        and publishes `agent.ate`; this is the subscriber that turns that
        event into a fed resident.

        Without this the tool would be the project's signature bug all over
        again: a thing that exists, runs, returns a result, and changes
        nothing. A resident could eat all day and still starve.
        """
        if getattr(self, "_eating_attached", False):
            return
        self._eating_attached = True

        async def on_ate(event: WorldEvent) -> None:
            if event.type != "agent.ate":
                return
            agent_id = event.payload.get("agent_id")
            if not agent_id:
                return
            from world.life.upkeep import HungerState

            current = self.hunger_by_agent.get(agent_id, HungerState())
            self.hunger_by_agent[agent_id] = HungerState(
                consecutive_unpaid_days=current.consecutive_unpaid_days,
                days_since_meal=0,
            )
            self.fed_today.add(agent_id)
            logger.info("%s ate %s", agent_id, event.payload.get("item"))

        self.world_state.event_bus.subscribe("agent", on_ate)

    async def record_the_scenario(self) -> None:
        """Put the run's own setup in the trace, where the report will find it.

        A report that says "3 of 42 items held" and not what the town was set
        up to do invites the reader to compare it with a run that started
        somewhere else entirely. The manifest on disk carries the same facts,
        but the trace is what a report reads, and evidence nobody can reach
        from the report is evidence that will be misread.
        """
        if not self.scenario:
            return
        await self.world_state.event_bus.publish(
            WorldEvent(
                type="system.scenario.set",
                tick=self.world_state.tick(),
                payload={
                    "name": self.scenario.name,
                    "path": self.config.scenario_path or "",
                    "start_tick": self.config.effective_start_tick(),
                    "stop_tick": self.config.max_ticks,
                    "says": self.scenario.describe(),
                },
            )
        )

    async def seat_the_scenarios_incumbents(self) -> None:
        """Put the scenario's government in its seats before the first tick.

        An election nobody holds a seat in asks "will anyone stand?". An
        election held by a sitting government asks the sharper question -
        will the town keep them? - and it asks it of residents who have been
        governed by them since the run began. That is the whole reason a
        scenario can name incumbents.

        `caretaker=True` and an empty `terms_served`, exactly as GROW-12's
        caretaker is marked: nobody voted for these seats, so DESIGN section
        10's regime-transition counts must not read them as a term served.
        The seat still acts - `is_caretaker_seat`'s own docstring says
        anything asking "may this resident act" should not care.
        """
        if not self.scenario or not self.scenario.incumbents:
            return
        pm = self.scenario.prime_minister()
        ministers = [who for who in self.scenario.ministers()]
        missing = [who for who in ([pm] if pm else []) + ministers if who not in self.world_state.agents]
        if missing:
            raise ConfigError([
                f"scenario {self.scenario.name}: incumbent(s) {', '.join(missing)} are not in "
                f"this town ({len(self.world_state.agents)} residents seeded)"
            ])
        await self.world_state.charter.store.set_government_state(
            GovernmentState(
                pm_agent_id=pm,
                minister_agent_ids=ministers,
                terms_served={},
                caretaker=True,
            )
        )
        logger.info(
            "scenario %s: %s holds the government (ministers: %s) when the run starts, "
            "and may stand in the election it is running",
            self.scenario.name, pm or "nobody", ", ".join(ministers) or "none",
        )

    async def seed_genesis(self) -> None:
        from world.life.genesis import RichGenesis

        raw = self.config.genesis_path.read_text(encoding="utf-8")
        genesis = RichGenesis.model_validate_json(raw)
        prune_ties_to_people_not_in_the_cast(genesis)
        self.genesis = genesis

        if genesis.caretaker_pm_agent_id:
            await self.world_state.charter.store.set_government_state(
                GovernmentState(
                    pm_agent_id=genesis.caretaker_pm_agent_id,
                    minister_agent_ids=[],
                    terms_served={},
                    caretaker=True,
                )
            )
            logger.info(
                "GROW-12: %s holds the government as caretaker until the "
                "first election - the town can petition for land",
                genesis.caretaker_pm_agent_id,
            )

        seeded_loans = 0
        unknown_debt_roles: set[str] = set()
        for a in genesis.agents:
            persona = a.persona
            if persona is None:
                continue
            for opinion in persona.opinions:
                if opinion.kind != "debt":
                    continue
                if opinion.role not in DEBT_OWER_ROLES:
                    if opinion.role not in DEBT_LENDER_ROLES:
                        unknown_debt_roles.add(str(opinion.role))
                    continue
                if not opinion.sim_amount:
                    continue
                self.world_state.record_loan(
                    lender_id=opinion.target_agent_id,
                    borrower_id=a.agent_id,
                    amount=int(opinion.sim_amount),
                    tick=0,
                )
                seeded_loans += 1
        declared = sum(
            1
            for a in genesis.agents
            if a.persona is not None
            for o in a.persona.opinions
            if o.kind == "debt" and o.role in DEBT_OWER_ROLES and o.sim_amount
        )
        if seeded_loans:
            logger.info(
                "TOOL-18: seeded %d genesis debt(s) into world.loans, so "
                "check_debts has something to answer with", seeded_loans,
            )
        if unknown_debt_roles:
            logger.error(
                "genesis declares debts with unrecognised role(s) %s - those "
                "debts are NOT in world.loans, and check_debts will tell a "
                "resident it owes nobody while its persona says otherwise. "
                "Known ower roles: %s",
                sorted(unknown_debt_roles), sorted(DEBT_OWER_ROLES),
            )
        elif declared and not seeded_loans:
            logger.error(
                "genesis declares %d debt(s) and none reached world.loans", declared,
            )

        agent_ids = [a.agent_id for a in genesis.agents]
        assert self.genesis is not None
        await self.ledger.genesis(
            agent_ids,
            agent_usd_cents=(
                self.genesis.usd_ledger.agent_wallets_cents // self.genesis.population
            ),
            treasury_usd_cents=self.genesis.usd_ledger.treasury_cents,
            tick=0,
        )

        reseed_failures: list[str] = []
        for agent in genesis.agents:
            self.agent_bios[agent.agent_id] = agent.bio
            self.agent_names[agent.agent_id] = agent.name
            self.genesis_by_id[agent.agent_id] = agent
            life_record = AgentLifeRecord(
                agent_id=agent.agent_id,
                age_years=agent.age_years,
                sex=agent.sex,
                household_id=agent.household_id,
                spouse_id=agent.spouse_id,
                marital_status=agent.marital_status,
                parent_ids=list(agent.parent_ids),
                children_ids=list(agent.children_ids),
                sponsor_parent_id=agent.sponsor_parent_id,
                health=HealthState(),
                dormancy=DormancyState(),
                alive=True,
            )
            self.world_state.agents[agent.agent_id] = life_record
            if self.trace_store is not None:
                try:
                    await self.trace_store.seed_agent(life_record, agent, tick=0)
                except Exception as exc:  # noqa: BLE001
                    reseed_failures.append(f"{agent.agent_id}: {exc}")
            location_id = (
                OPENING_GATHERING_PLACE if self.config.opening == "gathering"
                else household_location_id(agent.household_id)
            )
            self.world_state.locations.place(agent.agent_id, location_id)
            self.tick_engine.register_agent(agent.agent_id, location_id=location_id)
            self.stand_them_on_a_tile(agent.agent_id, location_id)
            await self.world_state.event_bus.emit(
                "agent.spawned",
                tick=self.world_state.tick(),
                payload={"agent_id": agent.agent_id, "location_id": location_id},
            )
            if agent.starting_sim_balance:
                self.ledger.get_account(agent.agent_id).sim_units = agent.starting_sim_balance
            self._credit_opening_stock(agent.agent_id, agent.opening_stock)

        for household in genesis.households:
            self.world_state.households[household.household_id] = LifeHousehold(
                household_id=household.household_id, member_ids=list(household.member_ids)
            )

        def _seed_wages(business: Business) -> None:
            """Give every seeded employee a wage that actually accrues.

            Genesis has always written `employee_agent_ids` and never a wage,
            so a job was a label: the world counts `accrued_sim` against a
            WagePromise and there was no promise to count against. ECON-5 has
            read `0 hire(s), 0 wage payment(s)` in every run, and a miller in
            the last one said "come to the mill and we can count a specific
            task against what the till can pay" with nothing in front of her
            to count it with.

            This is the same argument as opening stock. A town whose shops
            open empty starves before it can trade; a town whose employees
            open unwaged has no income before it can earn one, and bread at
            12 sim is unaffordable to everybody at once. Seeding the promise
            does not pay it - `paid_sim` still moves only when somebody calls
            `pay_wage`, which is the measurement ECON-5 exists to take. It
            only means the promise is there to be paid.

            OPENING_WAGE_SIM covers a day of living rather than a token: the
            head tax took 30 sim a head a day in the last run and the grocery
            asks 12 for bread, so a wage under about 45 buys nothing after tax.
            """
            for employee_id in sorted(business.employee_agent_ids or ()):
                key = f"{business.business_id}|{employee_id}"
                if key in self.world_state.wage_promises:
                    continue
                self.world_state.wage_promises[key] = WagePromise(
                    business_id=business.business_id,
                    employee_id=employee_id,
                    wage_sim=OPENING_WAGE_SIM,
                    every_days=1,
                    promised_tick=self.world_state.tick(),
                )

        async def _seed_business(business: Business) -> None:
            _seed_wages(business)
            if self.trace_store is None:
                return
            try:
                await self.trace_store.seed_business(business, tick=0)
            except Exception as exc:  # noqa: BLE001
                reseed_failures.append(f"{business.business_id}: {exc}")

        cc = genesis.construction_company
        self.ledger.open_business(cc.business_id, cc.owner_agent_id)
        self.ledger.get_account(cc.business_id).sim_units = cc.starting_sim_balance
        self._credit_opening_stock(cc.business_id, cc.opening_stock)
        self._post_opening_prices(cc.business_id, cc.opening_prices)
        self.world_state.businesses[cc.business_id] = Business(
            business_id=cc.business_id,
            name=cc.name,
            business_type="construction_company",
            owner_agent_id=cc.owner_agent_id,
            employee_agent_ids=set(cc.hand_agent_ids),
            location_id=business_location_id(cc.business_id),
            provides_workshops={"carpentry", "sawmill"},
        )
        await _seed_business(self.world_state.businesses[cc.business_id])
        for business in genesis.small_businesses:
            self.ledger.open_business(business.business_id, business.owner_agent_id)
            self.ledger.get_account(business.business_id).sim_units = business.starting_sim_balance
            self.world_state.businesses[business.business_id] = Business(
                business_id=business.business_id,
                name=business.name,
                business_type=business.business_type,
                owner_agent_id=business.owner_agent_id,
                employee_agent_ids=set(business.employee_agent_ids),
                location_id=business_location_id(business.business_id),
                provides_workshops=workshops_for_business_type(
                    business.business_type
                ),
            )
            self._credit_opening_stock(business.business_id, business.opening_stock)
            self._post_opening_prices(business.business_id, business.opening_prices)
            await _seed_business(self.world_state.businesses[business.business_id])

        opening_tick = (
            self.scenario.election_opening_tick()
            if self.scenario
            else self.tick_engine.clock.tick_index
        )
        await self.world_state.charter.elections.schedule_election(opening_tick)
        await self.seat_the_scenarios_incumbents()
        await self.record_the_scenario()

        await self.apply_tick_pacing()

        if reseed_failures:
            logger.info(
                "%d of %d agent rows were already in the trace store "
                "(re-run against an existing store); first: %s",
                len(reseed_failures),
                len(genesis.agents),
                reseed_failures[0],
            )
        logger.info(
            "genesis seeded: %d agents, %d households, %d businesses",
            len(genesis.agents),
            len(genesis.households),
            len(self.world_state.businesses),
        )

    MAX_OBLIGATIONS_SHOWN = 6

    EDIBLE = frozenset({
        "wheat", "rice", "corn", "potato", "tomato", "carrot", "cabbage",
        "soybean", "barley", "flour", "bread", "meal", "apple", "milk",
        "egg", "fish",
    })

    def todays_obligations(self, agent_id: str, day: int) -> tuple[int, int, Any]:
        """What this resident's household owes today: upkeep, head tax,
        and who depends on it.

        ECON-20. Never raises and never guesses - the same rule
        `wallet_line` follows, because these figures decorate a turn and
        must never cost one. Returns (0, 0, None) for anything it cannot
        work out, which leaves both volatile-line branches falsy and the
        line silent rather than confidently wrong about a resident's debts.
        """
        from world.life.upkeep import compute_household_bill
        from world.ledger.head_tax import head_tax_sim_per_head
        from world.tools.cost_disclosure import HouseholdUpkeepView

        world = self.world_state
        found = household_members(world, agent_id)
        if world is None or found is None:
            return 0, 0, None
        household_id, members = found

        try:
            bill = compute_household_bill(household_id, day, members)
        except Exception:  # noqa: BLE001 - a readout may not end a turn
            return 0, 0, None

        try:
            rate_bps = int(world.charter.store.current_policy().tax_rate_bps or 0)
        except Exception:  # noqa: BLE001 - no policy seeded yet
            rate_bps = 0
        head_tax_due = head_tax_sim_per_head(rate_bps) * len(members)

        view = HouseholdUpkeepView(
            household_id=household_id,
            member_count=len(members),
            daily_upkeep_sim=bill.total_sim,
            dependent_agent_ids=tuple(
                b.agent_id for b in bill.breakdowns if b.is_dependent
            ),
        )
        return bill.total_sim, head_tax_due, view

    def wallet_line(self, agent_id: str, tick: int) -> dict[str, Any] | None:
        """The figures A6's volatile line prints, straight off the ledger.

        Never raises and never guesses: a wallet reading that breaks a
        tick would trade a resident's whole turn for a cosmetic line, and
        an invented runway is worse than none - see
        `estimate_ticks_until_dormancy`, whose own docstring says
        "unknown" is the honest answer rather than "forever".
        """
        from world.tools.cost_disclosure import (
            build_economic_context,
            estimate_ticks_until_dormancy,
        )

        upkeep_due_sim, head_tax_due_sim, household = self.todays_obligations(
            agent_id, timestamp_for(tick).game_day
        )
        try:
            snapshot = build_economic_context(
                self.ledger,
                agent_id,
                tick,
                accrued_upkeep_today_sim=upkeep_due_sim,
                household=household,
            )
        except Exception:  # noqa: BLE001 - a cosmetic line may not end a turn
            return None

        ticks = estimate_ticks_until_dormancy(
            snapshot.usd_wallet_cents,
            snapshot.recent_burn_usd_cents,
            snapshot.burn_window_ticks,
        )
        return {
            "usd_wallet_cents": snapshot.usd_wallet_cents,
            "sim_balance": snapshot.sim_balance,
            "last_turn_usd_cents": (
                snapshot.recent_burn_usd_cents / snapshot.burn_window_ticks
                if snapshot.burn_window_ticks else 0
            ),
            "runway_game_days": (ticks / 144.0) if ticks is not None else None,
            "ran_out_after_steps": self.budget_hit_last_turn.pop(agent_id, None),
            "upkeep_due_sim": upkeep_due_sim,
            "head_tax_due_sim": head_tax_due_sim,
            "dependents": list(household.dependent_agent_ids) if household else [],
            "household_size": household.member_count if household else 1,
        }

    def call_budget_for(self, agent_id: str) -> int:
        """B3: how many model calls this resident's tick may make.

        Computed HERE because the wallet is here. The engine is handed the
        number and honours it through `RequestOptions.max_turns`, so
        nothing in engine_core changes - see world/call_budget.py for why
        the bound exists at all.
        """
        from world.call_budget import budget_for

        try:
            wallet_cents = self.ledger.usd_balance(agent_id)
        except Exception:  # noqa: BLE001 - a missing account must not end a turn
            wallet_cents = 0
        return budget_for(wallet_cents).calls

    def standing_brief(self, agent_id: str) -> dict[str, Any]:
        """What this agent knows about its own situation, this tick.

        Every value is read from world state. Nothing here encourages the
        agent to do anything - it is told its trade, where its work is, and
        how much food it holds, and what it does with that is the experiment.
        """
        assert self.world_state is not None
        record = self.world_state.agents.get(agent_id)
        if record is None:
            return {}

        persona = self.genesis_by_id.get(agent_id) if self.genesis else None

        food = 0
        food_items: dict[str, int] = {}
        try:
            held = self.world_state.inventory.balances.get(agent_id, {})
            edible = {
                item: qty
                for item, qty in held.items()
                if item in self.EDIBLE and qty > 0
            }
            food = sum(edible.values())
            food_items = dict(
                sorted(edible.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_HOLDINGS_SHOWN]
            )
        except Exception:  # noqa: BLE001 - a brief must never break a tick
            food = 0
            food_items = {}

        balance = None
        try:
            balance = self.ledger.sim_balance(agent_id)
        except Exception:  # noqa: BLE001 - same
            balance = None

        workplace = None
        business_id = getattr(persona, "business_id", None)
        if business_id:
            business = self.world_state.businesses.get(business_id)
            workplace = getattr(business, "location_id", None) or f"business:{business_id}"
        else:
            workplace = self.resolve_workplace(getattr(persona, "occupation", None))

        household_id = getattr(record, "household_id", None)
        household: list[str] = []
        others: list[str] = []
        if agent_id not in self.roster_seeded:
            for other_id, other in self.world_state.agents.items():
                if other_id == agent_id or not getattr(other, "alive", True):
                    continue
                entry = self.roster_entry(other_id)
                if entry is None:
                    continue
                if household_id and other.household_id == household_id:
                    household.append(entry)
                else:
                    others.append(entry)

        return names.name_the_numbers(self.world_state, {
            "name": self.agent_names.get(agent_id),
            "occupation": getattr(persona, "occupation", None),
            "location_id": self.world_state.location_of(agent_id),
            "workplace": workplace,
            "food_units": food,
            "food_items": food_items,
            "holding": holdings_line(self.world_state, agent_id),
            "sim_balance": balance,
            "household": household,
            "townspeople": others,
            "roster_changes": self.roster_changes_for(agent_id),
            **self.ledger_of_self(agent_id),
        })

    def town_roster_text(self, agent_id: str) -> str:
        """Who else is in this town, for the once-written persona seed.

        CORE-16. The same names the standing brief used to repeat every
        tick, written once into `persona.txt` where the engine reads them
        at boot.

        Names and trades only - who exists and what they do. NOT where
        anybody is: that is what perception and `look_around` are for, and
        a roster carrying positions would hand every resident a live map
        of the whole town for free.

        The roster exists because of a real failure, and it must keep
        preventing it: without it a resident looking for a buyer reached
        for whatever names were within reach, which were the ones written
        on the map's buildings - a cast of thirty who are not in this run.
        Tobias spent a morning hailing "Harvey" and "Isabella"; Bram
        walked to "sh_silva". No such people, so the words reached nobody.
        """
        assert self.world_state is not None
        record = self.world_state.agents.get(agent_id)
        household_id = record.household_id if record is not None else None

        household: list[str] = []
        others: list[str] = []
        for other_id, other in self.world_state.agents.items():
            if other_id == agent_id or not other.alive:
                continue
            entry = self.roster_entry(other_id)
            if entry is None:
                continue
            if household_id and other.household_id == household_id:
                household.append(entry)
            else:
                others.append(entry)

        lines = ["THE PEOPLE OF THIS TOWN"]
        if household:
            lines.append("Your household: " + ", ".join(household) + ".")
        if others:
            lines.append(
                "The rest of the town: " + ", ".join(others) + "."
                " These are the only other people in it."
            )
        return "\n".join(lines)

    def roster_changes_for(self, agent_id: str) -> list[str]:
        """Arrivals and departures since this resident's roster was
        written, and nothing else.

        CORE-16. The seed is a snapshot; the town is not. Without this a
        resident born on day three exists in nobody's list - the exact
        failure the roster was added to prevent, arriving by a different
        door - and one who died stays in everybody's forever.

        Empty for a resident whose brief still carries the whole roster,
        because for them the live list already IS the answer.
        """
        if agent_id not in self.roster_seeded or self.world_state is None:
            return []
        known = self.roster_seen_by.get(agent_id)
        if known is None:
            return []
        changes: list[str] = []
        living = {
            other_id
            for other_id, other in self.world_state.agents.items()
            if other.alive and other_id != agent_id
        }
        for arrived in sorted(living - known):
            entry = self.roster_entry(arrived)
            if entry:
                changes.append(f"{entry} has since been born into the town")
        for gone in sorted(known - living):
            entry = self.roster_entry(gone)
            if entry:
                changes.append(f"{entry} has since died")
        return changes

    def roster_entry(self, agent_id: str) -> "str | None":
        """"Name (id), trade", or None for a resident with no known name.

        The id is here because the roster is where the join belongs. This
        list is the only place a resident is told the whole cast exists, and
        it used to read "Marta Thorne (baker)" - a name and a trade, no
        number anywhere. Every tool then answered in numbers, and nothing in
        a resident's whole context said the two words meant one person. Run
        28b: Silas Vance read `people_here: ["n02"]` at the mill and wrote
        *"Someone is here - n02"* about Ainsley Holloway, who he had known
        since genesis.

        `world/tools/names.py` now writes ids back as names on the way out,
        so the join is in every result too. It is still worth the roster's
        twenty-odd tokens once: this is the list a resident reads before
        anybody is in the room with it, and addressing somebody who is
        elsewhere is exactly what a market needs.

        The shape is `retrieval_depth._named`'s, not a second one - the
        brackets hold the id everywhere in this town, and the trade follows
        after a comma rather than competing for them.
        """
        name = self.agent_names.get(agent_id)
        if not name:
            return None
        persona = self.genesis_by_id.get(agent_id) if self.genesis else None
        trade = str(getattr(persona, "occupation", "") or "").replace("_", " ")
        entry = f"{name} ({agent_id})"
        return f"{entry}, {trade}" if trade else entry

    def build_admin_extras(self) -> AdminExtras:
        """What the operator console is handed.

        Its own method, not four lines inside `start_embedded_world_server`,
        because UI-05 turned on exactly what is in it and that was
        unreachable from a test without booting a uvicorn server.
        """
        token, was_generated = resolve_operator_token()
        if was_generated:
            logger.warning(
                "OPERATOR_TOKEN not set - generated an ephemeral admin token for this "
                "genesis run's embedded world app. Ephemeral operator token: %s",
                token,
            )
        assert self.world_state is not None
        return AdminExtras(
            operator_token=token,
            sim_control=self.sim_control,
            birth_requests=self.world_state.birth_requests,
        )

    def ledger_of_self(self, agent_id: str) -> dict[str, Any]:
        """What this resident owes, is owed, keeps, and is walking towards.

        CORE-16. The standing brief told a resident its trade, its
        workplace, its food and its balance, and nothing whatever about
        its obligations. Every fact below was already computed, and
        rendered nowhere:

          * `WorldState.debts_of` - wages either way, loans either way,
            head-tax and upkeep arrears - had ONE caller: the `check_debts`
            tool, which a resident has to think of. One that never thinks
            of it is never told it owes anything, and cannot act on a debt
            it has no way to know about.
          * `movement.journey_for` was read only inside the movement
            tools, so `look_around` mid-walk showed the ORIGIN and the
            brief said nothing about being in transit (run 23). A resident
            three ticks into a walk had no way to tell it was walking.
          * A shop owner was told nothing about their own till, stock or
            staff, so "run your business" meant guessing at the state of
            it.
          * Storage was invisible - only what was in hand counted - so a
            resident with a full granary read itself as destitute.

        The volatile line's `upkeep_due_sim` / `head_tax_due_sim` keys
        (`retrieval_depth.py:276-281`) had no producer anywhere: a branch
        that could never render.

        NO ADVICE. Every value is a fact from world state with no verb
        attached and nothing suggested. What a resident does about a debt
        is the experiment; not knowing it has one is a missing sense.

        Never raises. A readout that breaks a tick trades a whole turn for
        a line - the rule `wallet_line` and `standing_brief` both follow.
        """
        assert self.world_state is not None
        state = self.world_state
        out: dict[str, Any] = {}

        try:
            debts = state.debts_of(agent_id)
            owed_by = debts["owed_by_you"]
            owed_to = debts["owed_to_you"]
            if owed_by:
                out["owe"] = owed_by[: self.MAX_OBLIGATIONS_SHOWN]
            if owed_to:
                out["owed_to_me"] = owed_to[: self.MAX_OBLIGATIONS_SHOWN]
            if owed_by or owed_to:
                out["due_today_sim"] = debts["due_today_sim"]
            for row in owed_by:
                if row["kind"] == "upkeep":
                    out["upkeep_due_sim"] = out.get("upkeep_due_sim", 0) + int(row["sim"])
                elif row["kind"] == "head_tax":
                    out["head_tax_due_sim"] = out.get("head_tax_due_sim", 0) + int(row["sim"])
        except Exception:  # noqa: BLE001 - a readout may not end a turn
            pass

        try:
            stored: dict[str, int] = {}
            for storage in state.storages.values():
                if storage.owner_id != agent_id:
                    continue
                for item, qty in state.inventory.balances.get(storage.storage_id, {}).items():
                    if qty > 0:
                        stored[item] = stored.get(item, 0) + int(qty)
            if stored:
                out["in_storage"] = dict(
                    sorted(stored.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_HOLDINGS_SHOWN]
                )
        except Exception:  # noqa: BLE001
            pass

        try:
            for business_id, business in state.businesses.items():
                if business.owner_agent_id != agent_id or not business.active:
                    continue
                shop: dict[str, Any] = {"business_id": business_id, "name": business.name}
                try:
                    shop["till_sim"] = self.ledger.sim_balance(business_id)
                except Exception:  # noqa: BLE001 - a business with no account yet
                    pass
                stock = {
                    item: qty
                    for item, qty in state.inventory.balances.get(business_id, {}).items()
                    if qty > 0
                }
                if stock:
                    shop["stock"] = dict(
                        sorted(stock.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_HOLDINGS_SHOWN]
                    )
                staff = [
                    self.agent_names.get(worker) or worker
                    for worker in sorted(business.employee_agent_ids)
                ]
                if staff:
                    shop["staff"] = staff
                out.setdefault("my_business", []).append(shop)
        except Exception:  # noqa: BLE001
            pass

        try:
            journey = state.movement.journey_for(agent_id) if state.movement else None
            if journey is not None:
                out["walking"] = {
                    "to": journey.destination_location_id,
                    "eta_ticks": journey.ticks_remaining,
                }
        except Exception:  # noqa: BLE001
            pass

        try:
            commitment = state.continuing.get(agent_id)
            if commitment is not None:
                out["keeping_at"] = {
                    "action": commitment.tool,
                    "times_done": commitment.repetitions,
                    "until": commitment.until,
                }
        except Exception:  # noqa: BLE001
            pass

        return out

    def engine_output_tail(self, agent_id: str) -> list[str]:
        """One engine's recent stdout, for a tick that just failed. Unit id is the agent id."""
        if self.supervisor is None or not hasattr(self.supervisor, "recent_output"):
            return []
        return list(self.supervisor.recent_output(agent_id))

    def engine_output(self) -> dict[str, list[str]]:
        """Each engine's recent stdout, by unit id.

        The supervisor already drains it into a bounded tail; nothing read
        that for a HEALTHY unit, so an engine misbehaving without dying left
        no trace anywhere a person could reach.
        """
        recent = getattr(self.supervisor, "recent_output", None)
        if self.supervisor is None or recent is None:
            return {}
        out: dict[str, list[str]] = {}
        try:
            for status in await_nothing(self.supervisor):
                out[status] = recent(status, 25)
        except Exception:  # noqa: BLE001 - diagnostics must not 500
            return out
        return out

    async def engine_liveness(self) -> dict[str, Any]:
        """How many engines are up, for GET /world/engines.

        Counts the supervisor's own view rather than the genesis roster:
        the question the observatory is asking is whether the instrument is
        working, and an engine that died is exactly what would make it not.
        Never raises - a supervisor mid-restart returning nothing is worth
        reporting as zero alive, not as a 500 on the health page.
        """
        if self.supervisor is None:
            return {"alive": 0, "expected": 0, "dormant": 0, "down": []}
        try:
            statuses = await self.supervisor.list()
        except Exception:  # noqa: BLE001 - liveness must never break the page
            return {"alive": 0, "expected": 0, "dormant": 0, "down": []}
        alive = sum(1 for st in statuses if st.state is UnitState.RUNNING)
        recent = getattr(self.supervisor, "recent_output", None)
        down = []
        for st in statuses:
            if st.state is UnitState.RUNNING:
                continue
            row: dict[str, Any] = {
                "agent_id": st.unit_id, "state": st.state.value,
                "detail": (st.detail or "")[:200],
            }
            if recent is not None:
                try:
                    row["output"] = recent(st.unit_id, 12)
                except Exception:  # noqa: BLE001 - diagnostics must not 500
                    pass
            down.append(row)
        return {
            "alive": alive, "expected": len(statuses), "dormant": 0,
            "down": down,
            "timed_out_ticks": self.timed_out_ticks,
            "dispatch_skipped_ticks": self.dispatch_skipped_ticks,
            "invocations_lost": self.invocations_lost,
            "tick_timeout_s": self.config.tick_timeout_s,
        }

    def resolve_workplace(self, occupation: str | None) -> str | None:
        """Where this trade's work actually is, as a place `move` accepts.

        GROW-29. `WORKPLACE_BY_OCCUPATION` maps three trades onto workshop
        CONSTANTS - `carpentry`, `kitchen`, `sawmill` - and the brief
        renders them as "your work". They are not doors: `known_places`
        does not offer them, so the resident was told to go somewhere
        `list_places` never names and `move` cannot route to.

        The entries were correct when they were written. GROW-28 then
        stopped listing a workshop a business provides as its own door -
        rightly, they are not places you can stand in on their own - and
        left these three pointing at the doors it had removed.

        So the workshop is resolved to the PREMISES that provides it,
        which is exactly the relationship TOOL-02 built
        (`Business.provides_workshops`), read from the other end. Resolved
        against the live world rather than hard-coded: which business
        provides which workshop is genesis's choice and differs between
        the 10-, 20- and 50-resident casts.

        Returns None rather than a guess when nothing provides it. Absent
        is a fine answer; a phantom is what sent a grocery clerk to stand
        in a room no customer could walk into for a whole run.
        """
        workplace = WORKPLACE_BY_OCCUPATION.get(str(occupation or ""))
        if not workplace or self.world_state is None:
            return None

        from world.tools.primitives.places import known_places

        try:
            doors = {place["place_id"] for place in known_places(self.world_state)}
        except Exception:  # noqa: BLE001 - a brief must never break a tick
            return workplace
        if workplace in doors:
            return workplace

        for business in self.world_state.businesses.values():
            provides = business.provides_workshops
            if workplace in provides and business.location_id:
                return business.location_id
        return None

    def stand_them_on_a_tile(self, agent_id: str, location_id: str) -> None:
        """Give a resident a real (x, y) before its first move.

        CORE-31. `move` walks only when BOTH the destination is a map
        sector AND the mover own position resolves to a tile
        (`movement.resolve_current_tile`). `world.agent_tiles` is empty
        until a walk COMPLETES, and every genesis resident starts at
        `home:<household>` - so the first move of every run took the
        opaque-jump fallback and the resident was teleported without being
        told which it got. Run 23: over 196 `move` calls, 170 `look_around`
        results came back with `"tile": null` and three with a tile.

        WHY THIS IS NOT THE ENTRY POINT OF THE HOME. The review proposed
        seeding "from each home entry point", and there is none: the map
        house sectors are named for the Ville cast - "Adam Smith house",
        "Carlos Gomez apartment" - and a Kasukabe `home:h0050` resolves to
        nothing at all. That mismatch is the two-geographies finding
        (TOOL-01) and remapping household ids onto sectors is decision 2
        work, not a tile lookup.

        So the LOCATION STRING IS LEFT ALONE and only the tile is seeded.
        `resolve_current_tile` reads `world.agent_tiles` at step 2, before
        it ever tries to resolve the location string at step 3, so a
        resident gets a real walkable start without anybody being moved and
        without a single gate, `look_around` or viewer changing what it
        says.

        Households are spread deterministically across the map own house
        tiles, so two runs of one seed start identically and neighbours
        share a doorstep.
        """
        if self.map_service is None or self.world_state is None:
            return
        try:
            tile = self._home_tile_for(location_id)
            if tile is not None:
                self.world_state.agent_tiles[agent_id] = tile
        except Exception:  # noqa: BLE001 - genesis must not fail on geometry
            logger.debug("no genesis tile for %s at %s", agent_id, location_id)

    def _home_tile_for(self, location_id: str) -> "tuple[int, int] | None":
        """A stable walkable tile for one household (CORE-31).

        Cached per household so every member of a family starts on the same
        doorstep, and keyed off the household id so the assignment does not
        move between runs of the same seed.
        """
        if location_id in self._home_tiles:
            return self._home_tiles[location_id]

        world_map = self.map_service.world
        houses = self._house_sector_names()
        if not houses:
            return None

        index = abs(hash(location_id)) % len(houses)
        for offset in range(len(houses)):
            name = houses[(index + offset) % len(houses)]
            tile = world_map.entry_point(name)
            if tile is None:
                continue
            if not world_map.is_walkable(tile):
                tile = world_map.nearest_walkable(tile)
            if tile is None:
                continue
            self._home_tiles[location_id] = (int(tile.x), int(tile.y))
            return self._home_tiles[location_id]
        return None

    def _house_sector_names(self) -> list[str]:
        """Every sector on the map a person could live in, sorted."""
        if self._house_names is not None:
            return self._house_names
        from world.map.model import BuildingCategory

        names: list[str] = []
        for sector in self.map_service.world.sectors.values():
            if BuildingCategory.HOUSE in sector.categories:
                names.append(sector.name)
        self._house_names = sorted(names)
        return self._house_names

    def build_the_map(self) -> None:
        """Load the town's real geometry, once, before anything asks for it.

        GROW-06. The counterpart to `build_the_crime_engine` below and to
        `wiring.build_world_server` step 2, which has done this since it was
        written. This runner passed `map_service=None` into AdminDeps,
        AgentsDeps and LocationDeps instead, which is why every run this
        project has done had no map service at all.

        NOT fatal when the assets are missing. `smallville/` is gitignored,
        so a checkout without it must still boot a town that talks, trades
        and governs - it simply cannot grow or stamp a building, and says
        so once rather than raising at tick 0. That is the same degradation
        `tools/bootstrap.load_movement_world_map` already chose.
        """
        from world.map.service import MapService
        from world.tools.bootstrap import DEFAULT_VILLE_DIR

        ville_dir = DEFAULT_VILLE_DIR
        if not ville_dir.exists():
            logger.warning(
                "Ville assets not at %s: booting without a MapService. The town "
                "cannot grow and a completed building cannot become a walkable "
                "place (GROW-06 degrades, it does not fail).",
                ville_dir,
            )
            self.map_service = None
            return

        try:
            self.map_service, _zone_ids = MapService.from_kasukabe_genesis(ville_dir)
        except Exception as exc:  # noqa: BLE001 - a bad map must not end a run
            logger.warning("could not build the MapService: %s", exc)
            self.map_service = None
            return
        logger.info("map service ready from %s", ville_dir)

    def build_the_crime_engine(self) -> None:
        """GOV-04. Give this runner the crime engine no run has ever had.

        `build_world_server` constructs one at its step 8; this runner - the
        host every one of the 23 runs used - constructed none. So the crime
        package existed as a complete, tested library that had never been
        instantiated on a running world: `compute_metrics` had exactly one
        caller and it was on the other host, and the justice pipeline the
        review traced was never built at all.

        The adapters come from wiring.py rather than being duplicated here,
        which is the same choice `wire_the_charter_to_the_bus` makes and the
        reason the operator's decision was "the runner absorbs the server's
        wiring" rather than "the runner reimplements it".

        A caveat worth writing down: the crime TOOLS append to
        `world.crime_cases` directly and do not route through this engine
        today. So this closes the composition gap - the engine now exists,
        its events reach the bus, and its metrics are computable on the host
        that runs - but making the tools use it is a separate, world-side
        piece of work (GOV-05, Deni's lane).
        """
        from world.wiring import (
            CharterGovernmentAdapter,
            CrimeLedgerAdapter,
            LifeHealthAdapter,
            crime_event_to_world_event,
        )
        from world.crime.engine import create_crime_engine

        world = self.world_state

        async def to_bus(event: dict[str, Any]) -> None:
            await world.event_bus.publish(crime_event_to_world_event(event, world.tick()))

        self.crime = create_crime_engine(
            economy=CrimeLedgerAdapter(world.ledger),
            health=LifeHealthAdapter(world),
            government=CharterGovernmentAdapter(world.charter),
            location=world.locations,
            event_sink=to_bus,
        )
        self.crime_engine_wired = True

    def wire_the_charter_to_the_bus(self) -> None:
        """CORE-02. Close the seam `build_world()` deliberately leaves open.

        `create_genesis_charter_engine()` runs inside `build_world()` with
        no sink, so every charter sub-engine - elections, amendments,
        government, land, justice, treasury, temple - publishes into
        `noop_sink`. `build_world_server()` closes that seam at wiring.py's
        step 4. This runner never did, and this runner is the host every
        one of the 23 runs used. The consequence was not subtle: an
        election could be scheduled and tallied and NOTHING would reach the
        bus, so no `politics.*` row was ever recorded, no report could
        mention a government, and the crime and land engines fired into a
        void.

        Rewiring rather than reconstructing is deliberate and is what the
        sub-engines were written for - `event_sink` is a plain reassignable
        attribute on each. See `wiring.rewire_charter_event_sink`.
        """
        from world.wiring import charter_event_to_world_event, rewire_charter_event_sink

        world = self.world_state

        async def to_bus(event: dict[str, Any]) -> None:
            await world.event_bus.publish(charter_event_to_world_event(event, world.tick()))

        rewire_charter_event_sink(world.charter, to_bus)
        self.charter_sink_wired = True

    def write_the_run_manifest(self) -> Path | None:
        """CORE-24. Record what this run IS, before it does anything.

        DESIGN's claim is that every result traces to a row and a run is
        reproducible. It was not: a run's config, seed, genesis hash, git
        sha, model id and knob settings existed only in the shell history of
        whoever launched it. Two runs with different casts, different
        prices and different code produced reports that looked alike and
        could not be told apart afterwards.

        Written at BOOT rather than at shutdown, on purpose: a run that
        crashes at tick 3 is exactly the run somebody will want to identify
        later, and a manifest written at the end is the one that never
        exists when it is needed.

        Best-effort about its own contents - a missing git sha must not stop
        a town from booting - but never silent: whatever could not be read
        is recorded as null, so a reader can tell "not captured" from
        "captured as empty".
        """
        from world.tools import program as _program_module
        from world.tools.primitives.civic import ENERGY_PURCHASE_MAX_SHARE_PERCENT

        _program_caps = {
            "max_calls": _program_module.PROGRAM_MAX_CALLS,
            "max_steps": _program_module.PROGRAM_MAX_STEPS,
            "max_repeat": _program_module.PROGRAM_MAX_REPEAT,
            "wallet_share_percent": _program_module.PROGRAM_WALLET_SHARE_PERCENT,
        }
        manifest: dict[str, Any] = {
            "run_id": _run_id(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "git_sha": _git_sha(),
            "genesis_path": str(self.config.genesis_path or ""),
            "genesis_sha256": _sha256_of(self.config.genesis_path),
            "population_seed": self.config.population_seed,
            "agents": len(self.world_state.agents) if self.world_state else 0,
            "budget_cap_cents": self.config.budget_cap_cents,
            "mock_mode": self.config.mock_mode,
            "tick_interval_s": self.config.tick_interval_s,
            "start_at_dawn": self.config.start_at_dawn,
            "scenario_path": self.config.scenario_path or "",
            "scenario": self.scenario.name if self.scenario else "",
            "scenario_says": self.scenario.describe() if self.scenario else [],
            "start_tick": self.config.effective_start_tick(),
            "resume_at_tick": self.config.resume_at_tick,
            "veto_window_ticks": self.config.veto_window_ticks,
            "sim_per_usd": SIM_PER_USD,
            "model_id": os.environ.get("MODEL_ID", ""),
            "context_window": os.environ.get("KASUKABE_CONTEXT_WINDOW", ""),
            "full_schema_budget": os.environ.get("KASUKABE_FULL_SCHEMA_BUDGET", ""),
            "local_tool_schemas": os.environ.get("KASUKABE_LOCAL_TOOL_SCHEMAS", ""),
            "trace_db": os.environ.get("TRACE_SQLITE_PATH", ""),
            "program_max_calls": _program_caps["max_calls"],
            "program_max_steps": _program_caps["max_steps"],
            "program_max_repeat": _program_caps["max_repeat"],
            "program_wallet_share_percent": _program_caps["wallet_share_percent"],
            "energy_purchase_max_share_percent": ENERGY_PURCHASE_MAX_SHARE_PERCENT,
        }
        try:
            self.config.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            path = self.config.checkpoint_dir / "run_manifest.json"
            path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("could not write the run manifest: %s", exc)
            return None
        self.run_manifest_path = path
        logger.info("run manifest: %s (run_id=%s)", path, manifest["run_id"])
        return path

    async def export_the_day(self, game_day: int) -> "Path | None":
        """One Parquet file per table for the game day just ended.

        INFRA-10. `export_game_day` had no caller, so the Parquet research
        surface has never existed on any run - the roadmap's analysis
        plans all assume it does.

        On the checkpoint sink because that is the one place that knows a
        day has ended, and because the checkpoint is already the day's
        "this is what the world was" moment.

        Never raises, and never blocks the run for long: an export that
        fails is a research inconvenience, and a run that dies at a day
        boundary because DuckDB could not reach the network is a lost
        night. Missing duckdb is the normal case on a machine that only
        runs the town, and is reported once rather than every day.
        """
        directory = Path(self.config.checkpoint_dir) / "parquet" / f"day-{game_day:03d}"
        trace_db = os.environ.get("TRACE_SQLITE_PATH", "data/kasukabe.db")
        if not Path(trace_db).exists():
            return None
        try:
            from research.parquet_export import export_game_day

            await export_game_day(
                sqlite_path=trace_db, game_day=game_day, output_dir=directory
            )
            return directory
        except ImportError:
            if not self._warned_no_duckdb:
                self._warned_no_duckdb = True
                logger.info(
                    "INFRA-10: duckdb is not installed, so this run writes no "
                    "Parquet slices. The trace store itself is unaffected."
                )
            return None
        except Exception as exc:  # noqa: BLE001 - a research export may not end a run
            logger.warning("day %s Parquet export failed: %s", game_day, exc)
            return None

    def build_checkpoint_sink(self) -> Callable[[WorldCheckpoint], Awaitable[None]]:
        async def sink(checkpoint: WorldCheckpoint) -> None:
            self.counters.rng_state = self.rng.getstate()
            self.counters.retrieval_depth_fraction = self.depth.fraction
            local_path = self.checkpoint_store.write(checkpoint)
            await mirror_checkpoint_to_shared_store(
                checkpoint,
                local_path,
                bucket=self.config.supabase_bucket,
                client=self.trace_store.client if self.trace_store is not None else None,
            )
            await self.export_the_day(checkpoint.game_day)
            logger.info(
                "checkpoint written: tick=%s game_day=%s -> %s",
                checkpoint.tick_index,
                checkpoint.game_day,
                local_path,
            )

        return sink


    def start_watchdog(self) -> None:
        """B1/B2 (The 250 section 33): start the loop that restarts engines
        that die, instead of a FAILED unit sitting failed until a person
        notices. One call, after the units exist - the watchdog reads the
        supervisor it was handed and needs nothing else from this class."""
        if self.supervisor is None:
            return
        self.watchdog = SupervisorWatchdog(
            self.supervisor,
            poll_interval_s=self.config.watchdog_poll_interval_s,
            silence_timeout_s=self.config.watchdog_silence_timeout_s,
        )
        self.watchdog.start()
        logger.info(
            "watchdog started: polling every %.0fs, restarting anything silent past %.0fs",
            self.config.watchdog_poll_interval_s,
            self.config.watchdog_silence_timeout_s,
        )

        from world.signal_groundskeeper import SignalGroundskeeper

        self.groundskeeper = SignalGroundskeeper(self.config.home_root)
        self.groundskeeper.start()

        async def _remember_engine_memory() -> None:
            from world.memory_budget import measured_mb_per_engine, remember_measurement

            await asyncio.sleep(120)
            sample = await asyncio.to_thread(measured_mb_per_engine)
            if sample:
                remember_measurement(*sample)
                logger.info("engine memory on this machine: %d MB each (%d engines)",
                            sample[1], sample[0])

        self._memory_sample_task = asyncio.create_task(
            _remember_engine_memory(), name="engine-memory-sample")

    async def start_agents(self) -> None:
        """Launches one unit per genesis agent, via whichever Supervisor
        this run is configured with (module docstring point 3)."""
        assert self.world_state is not None
        agent_ids = sorted(self.world_state.agents.keys())
        failures: list[tuple[str, str | None]] = []

        if self.config.mock_mode:
            self.supervisor = MockSupervisor()
            self.gateway = MockAgentGateway(self.depth, random.Random(self.config.population_seed))
        elif self.config.supervisor_backend == "namespace":
            from world.namespace_supervisor import NamespaceSupervisor

            self.supervisor = NamespaceSupervisor(
                home_root=self.config.home_root,
                world_base_url=self.config.world_base_url(),
                engine_repo=self.config.engine_repo,
            )
            self.gateway = NamespaceAgentGateway(self.supervisor)
        else:
            command_builder = default_engine_command_builder(self.config.engine_repo)
            self.supervisor = ProcessSupervisor(command_builder, cwd=self.config.engine_repo)
            if self.config.resource_log_path is not None:
                self.resources = ResourceSampler(self.config.resource_log_path, {})
                logger.info(
                    "per-agent CPU and memory -> %s", self.config.resource_log_path
                )
            for index, agent_id in enumerate(agent_ids):
                self.ports_by_agent[agent_id] = self.config.sim_agent_port_base + index
            self.gateway = HttpAgentGateway(
                self.ports_by_agent,
                timeout_s=self.config.tick_timeout_s,
                output_tail=self.engine_output_tail,
            )

            from world.life.soul_seed import build_persona_prompt, build_soul_seed

            assert self.genesis is not None
            genesis_by_id = {a.agent_id: a for a in self.genesis.agents}
            for agent_id in agent_ids:
                rich_agent = genesis_by_id[agent_id]
                persona_text = "\n\n".join([
                    build_persona_prompt(rich_agent),
                    self.town_roster_text(agent_id),
                    build_soul_seed(rich_agent),
                ])
                agent_home = Path(self.config.home_root).expanduser() / agent_id
                agent_home.mkdir(parents=True, exist_ok=True)
                (agent_home / "persona.txt").write_text(persona_text, encoding="utf-8")
                self.roster_seeded.add(agent_id)
                self.roster_seen_by[agent_id] = {
                    other_id
                    for other_id, other in self.world_state.agents.items()
                    if other.alive and other_id != agent_id
                }

        if not self.config.mock_mode and not os.environ.get("KASUKABE_SKIP_PREFLIGHT"):
            from world.preflight import run as preflight_run

            pf = preflight_run(
                broker_url=self.config.broker_base_url,
                world_url=None,
                expected_agents=len(agent_ids),
                genesis_path=str(self.config.genesis_path),
                home_root=self.config.home_root,
                trace_db=os.environ.get("TRACE_SQLITE_PATH", "data/kasukabe.db"),
                engine_repo=str(self.config.engine_repo),
            )
            logger.info("preflight:\n%s", pf.render())
            if not pf.ready:
                raise SystemExit(
                    "\nPreflight failed - the run has NOT started.\n\n"
                    + pf.render()
                    + "\n\nFix the lines marked [✗], or set "
                    "KASUKABE_SKIP_PREFLIGHT=1 to start anyway.\n"
                )

        spawns_real_processes = not isinstance(self.supervisor, MockSupervisor)
        sample = memory_budget.measured_mb_per_engine()
        per_engine = sample[1] if sample else memory_budget.last_measurement()
        verdict = memory_budget.check(
            len(agent_ids) if spawns_real_processes else 0,
            backend=self.config.supervisor_backend,
            mb_per_engine=per_engine,
        )
        if spawns_real_processes and not verdict.fits and not os.environ.get(
            "KASUKABE_SKIP_MEMORY_CHECK"
        ):
            raise SystemExit(
                "\nNot enough free memory to start this run.\n\n"
                + verdict.explain()
                + "\n\nSet KASUKABE_SKIP_MEMORY_CHECK=1 to start anyway.\n"
            )
        if spawns_real_processes and not verdict.fits:
            logger.warning(
                "run_genesis: starting %d engines with only %d MB free - "
                "KASUKABE_SKIP_MEMORY_CHECK is set, so this is your call",
                verdict.requested, verdict.available_mb,
            )
        elif spawns_real_processes:
            logger.info(
                "run_genesis: %d engines need ~%d MB, %d MB free - fits",
                verdict.requested, verdict.needed_mb, verdict.available_mb,
            )

        world_base_url = self.config.world_base_url()
        for index, agent_id in enumerate(agent_ids):
            extra_env = {
                "AGENT_ID": agent_id,
                "WORLD_BASE_URL": world_base_url,
                "KASUKABE_HOME_ROOT": self.config.home_root,
                "SIM_AGENT_PORT": str(self.config.sim_agent_port_base + index),
                "BROKER_BASE_URL": self.config.broker_base_url,
                "BROKER_API_KEY": sign_broker_token(agent_id, self.config.broker_api_key),
                "KASUKABE_ABLATION_ARM": self.ablation_arm_for(agent_id),
            }
            spec = UnitSpec(
                unit_id=agent_id,
                engine_id=agent_id,
                cdt_url=self.config.broker_base_url,
                cdt_key=sign_broker_token(agent_id, self.config.broker_api_key),
                extra_env=extra_env,
            )
            status = await self.supervisor.start(spec)
            if status.state is UnitState.FAILED:
                failures.append((agent_id, status.detail))
                logger.error("unit %s failed to start: %s", agent_id, status.detail)

        booted = len(agent_ids) - len(failures)
        logger.info("launched %d of %d agent units (mock_mode=%s)",
                    booted, len(agent_ids), self.config.mock_mode)
        if failures:
            reasons = sorted({(d or "unknown").splitlines()[0][:120] for _, d in failures})
            logger.error("%d unit(s) did not start: %s", len(failures), reasons)
        if not booted and not self.config.mock_mode:
            raise SystemExit(
                f"\nNo engine started - all {len(agent_ids)} units failed.\n"
                f"  {reasons[0] if failures else ''}\n\n"
                "With --supervisor-backend namespace this is expected: that "
                "backend cannot host engines yet (see GenesisConfig."
                "supervisor_backend). Use the default 'process' backend.\n"
            )
        await self.wait_for_engines_ready(agent_ids)

    async def apply_tick_pacing(self) -> float:
        """Set the tick cadence now that the population actually exists.

        Deliberately separate from TickEngine construction: the engine is
        built before genesis is seeded, so anything derived from the agent
        count has to happen afterwards or it sizes the run for an empty
        town.
        """
        interval = await self.resolve_tick_interval(len(self.world_state.agents))
        if self.tick_engine is not None:
            self.tick_engine.pacing.config.continuous_interval_s = interval
        return interval

    def calls_per_turn_estimate(self) -> float:
        """How many model calls one resident's turn really costs.

        CORE-12 / INFRA-12. The pacing arithmetic treated a turn as one
        request. It is 2-12 model calls plus a summariser, so every
        interval this runner computed was out by that factor and the town
        was paced to outrun the broker it was talking to.

        Averaged over the turns this run has actually had - the engines
        report `steps_used` on every `/tick` response and nothing was
        reading it - so a town of quiet residents is not paced as though
        everybody thinks twelve times. `+1` for the post-turn summariser,
        which is a real call on the same lane.
        """
        samples = self.recent_steps_used
        if samples:
            return max(1.0, sum(samples) / len(samples) + 1.0)
        return DEFAULT_CALLS_PER_TURN

    async def probe_broker_capacity(self) -> "float | None":
        """Total RPM the broker can actually serve, from GET /admin/lanes.

        Summed over the lanes it BUILT, not the lanes the design assumes:
        an account whose inference profiles exist in one region only gets
        two lanes, not six, and 200 RPM, not 600. Asking the broker beats
        hardcoding, because that number is a property of the deployment.

        None when the broker cannot be reached or says nothing useful -
        the caller then leaves pacing alone rather than guessing.
        """
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(self.config.broker_base_url.rstrip("/") + "/admin/lanes")
            if resp.status_code != 200:
                return None
            lanes = resp.json()
        except Exception:  # noqa: BLE001 - pacing must never block boot
            return None
        if not isinstance(lanes, list) or not lanes:
            return None
        total = sum(lane.get("capacity", 0) for lane in lanes if isinstance(lane, dict))
        return float(total) or None

    async def broker_terminal_reason(self) -> "str | None":
        """Ask the broker whether continuing is pointless. None if not, or
        if it cannot be asked.

        Polled only on a tick that actually saw a dispatch failure (see
        run_continuous), so a healthy run adds no requests at all. The
        probe is local HTTP against a service this process already depends
        on, and its own failure is swallowed for the reason
        terminal_reason_from_spend documents: an unreachable broker is the
        barren-tick guard's business, not this one's.
        """
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(
                    self.config.broker_base_url.rstrip("/") + "/admin/spend"
                )
            if resp.status_code != 200:
                return None
            reason = terminal_reason_from_spend(resp.json())
            if reason:
                return reason

            trace = await client.get(
                self.config.broker_base_url.rstrip("/") + "/admin/trace"
            )
            if trace.status_code == 200:
                body = trace.json()
                if body.get("dispatch_halted"):
                    writer = body.get("trace_writer") or {}
                    detail = writer.get("last_failure_message") or "no detail reported"
                    return (
                        "the broker has stopped recording and is refusing every "
                        f"call (503): {detail}"
                    )
            return None
        except Exception:  # noqa: BLE001 - a probe must never end a run by failing
            return None

    async def resolve_tick_interval(self, agent_count: int) -> float:
        """Seconds per tick, sized so the town cannot outrun its broker.

        The arithmetic this exists to enforce:

            requests_per_minute = agents * target_fraction * (60 / interval)

        Rearranged for the interval that exactly saturates the lanes:

            interval = agents * target_fraction * 60 / capacity_rpm

        An explicit --tick-interval-s always wins - an operator throttling
        a run deliberately must not be second-guessed - but is warned about
        when it asks for more than the broker can serve, because the
        symptom otherwise is latency and circuit-breaker trips a long way
        from the cause.
        """
        explicit = self.config.tick_interval_s

        if self.config.mock_mode:
            return max(explicit, 0.0)

        capacity = await self.probe_broker_capacity()
        due_per_tick = max(1.0, agent_count * self.config.scheduler_target_fraction)
        calls_per_turn = self.calls_per_turn_estimate()

        if capacity is None:
            if explicit <= 0:
                logger.warning(
                    "could not read broker lane capacity - running UNPACED. If this is a real "
                    "run, set --tick-interval-s explicitly or the town will outrun the broker."
                )
            return max(explicit, 0.0)

        safe = due_per_tick * calls_per_turn * 60.0 / capacity
        if explicit > 0:
            if explicit < safe:
                logger.warning(
                    "--tick-interval-s=%.2fs asks for ~%.0f RPM but the broker serves %.0f "
                    "(%.2fs/tick). Obeying you; expect queueing and circuit-breaker trips.",
                    explicit, due_per_tick * calls_per_turn * 60.0 / explicit, capacity, safe,
                )
            return explicit

        logger.info(
            "pacing ticks at %.2fs: %d agents x %.2f due = %.0f calls/tick against %.0f RPM "
            "of broker capacity (~%.0f RPM requested)",
            safe, agent_count, self.config.scheduler_target_fraction, due_per_tick,
            capacity, due_per_tick * 60.0 / safe,
        )
        return safe

    async def collect_wire_health(self) -> dict[str, int]:
        """Ask every engine how its link to the world has been behaving.

        WIRE-02, second half. The first half put counters on the engine's
        `WorldClient` and served them on `GET /health` - and NOTHING READ
        THEM. The runner polls that endpoint once, at startup, and looks
        only at the status code. So the instrument existed and no
        consumer did, which is ENG-16 exactly: a fix that reaches nobody
        is indistinguishable from no fix.

        Run `deni-a6f9eb4-5h` lost 97 calls - 8% of every tool call the
        town made - and the loss was discoverable only by grepping
        session logs after the run had ended. This is what makes it a
        number the run itself reports.

        Never raises and never blocks a shutdown: an engine that has
        already exited cannot answer, and a run that failed to collect
        its own telemetry must still finish writing everything else.
        """
        totals = {
            "world_calls_attempted": 0,
            "world_calls_refused": 0,
            "world_transport_failures": 0,
            "world_transport_recovered": 0,
            "engines_answering": 0,
        }
        if self.config.mock_mode or not self.ports_by_agent:
            return totals

        async def ask(agent_id: str, port: int, client: httpx.AsyncClient) -> None:
            try:
                response = await client.get(f"http://127.0.0.1:{port}/health")
                if response.status_code != 200:
                    return
                body = response.json()
            except Exception:  # noqa: BLE001 - a dead engine is not a crash
                return
            totals["engines_answering"] += 1
            for key in totals:
                if key == "engines_answering":
                    continue
                value = body.get(key)
                if isinstance(value, int):
                    totals[key] += value

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await asyncio.gather(*(
                    ask(agent_id, port, client)
                    for agent_id, port in self.ports_by_agent.items()
                ))
        except Exception:  # noqa: BLE001
            logger.warning("could not collect wire health from the engines")
        return totals

    def report_wire_health(self, totals: dict[str, int]) -> None:
        """Say it out loud, at the level the number deserves.

        A lost call is indistinguishable from a refusal to the resident
        that made it, so a run with losses has residents reasoning from
        "the world would not let me" about things the world never heard.
        That is worth an ERROR line, not a debug one.
        """
        attempted = totals.get("world_calls_attempted", 0)
        if not attempted:
            return
        lost = totals.get("world_transport_failures", 0)
        recovered = totals.get("world_transport_recovered", 0)
        share = lost / attempted
        line = (
            "engine->world link: %d calls, %d refused, %d lost to transport "
            "(%.1f%%), %d recovered by retry, across %d engines"
        )
        args = (
            attempted, totals.get("world_calls_refused", 0), lost, share * 100,
            recovered, totals.get("engines_answering", 0),
        )
        if share >= WIRE_LOSS_ALARM:
            logger.error(line + " - ABOVE THE ALARM LINE", *args)
        elif lost or recovered:
            logger.warning(line, *args)
        else:
            logger.info(line, *args)

    async def wait_for_engines_ready(self, agent_ids: list[str]) -> None:
        """Block until every real engine answers GET /health, or time out.

        Without this the runner launches 250 engine subprocesses, sleeps
        ProcessSupervisor.startup_grace_s (0.05s), and starts dispatching
        ticks. A September engine needs tens of seconds to boot - it builds
        its tool registry from the world, loads its persona and opens its
        memory store first - so EVERY dispatch failed, and because a failed
        dispatch costs no wall-clock the runner burned an entire game day
        (144 ticks) in 0.65 seconds and exited having done nothing at all.
        Measured, not theorised: that is exactly what a 10-agent embedded
        run did before this method existed.

        Mock mode returns immediately: MockAgentGateway is in-process and
        has no port to poll.

        A timeout is NOT fatal. Engines that are up still run, and the
        watchdog owns the ones that never came up - refusing to start the
        whole town because one engine is slow would be worse than starting
        with 249. What matters is that the decision is logged rather than
        silently producing a run where nobody ever thought.
        """
        if self.config.mock_mode or not agent_ids:
            return
        deadline = time.monotonic() + self.config.engine_ready_timeout_s
        pending = set(agent_ids)
        ready: set[str] = set()

        async def probe(agent_id: str, client: httpx.AsyncClient) -> str | None:
            """One engine, asked once. None when it did not answer."""
            port = self.ports_by_agent.get(agent_id)
            if port is None:
                return None
            try:
                resp = await client.get(f"http://127.0.0.1:{port}/health")
            except (httpx.HTTPError, OSError):
                return None
            return agent_id if resp.status_code == 200 else None

        async with httpx.AsyncClient(timeout=2.0) as client:
            while pending and time.monotonic() < deadline:
                answered = await asyncio.gather(
                    *(probe(agent_id, client) for agent_id in sorted(pending))
                )
                ready.update(a for a in answered if a)
                pending -= ready
                if pending:
                    await asyncio.sleep(1.0)

        if pending:
            sample = sorted(pending)[:5]
            detail = ""
            if isinstance(self.supervisor, ProcessSupervisor):
                lines = self.supervisor.recent_output(sample[0], 3)
                if lines:
                    detail = f" | {sample[0]} last said: {' / '.join(lines)}"
            logger.warning(
                "%d of %d engines never answered /health within %.0fs - continuing without them "
                "(examples: %s)%s",
                len(pending), len(agent_ids), self.config.engine_ready_timeout_s,
                ", ".join(sample), detail,
            )
        else:
            logger.info("all %d engines ready", len(ready))

    def build_world_app(self) -> "FastAPI":
        """This runner's world, as routes - built, not yet served.

        Its own method so the final reading (`capture_final_reading`) and a
        test can hold the very app a run serves, with this runner's own
        callables bound into it, without binding a port.
        """
        assert self.world_state is not None and self.registry is not None
        assert self.ledger is not None and self.metrics is not None
        assert self.tick_engine is not None
        if self.admin_extras is None:
            self.admin_extras = self.build_admin_extras()
        app = build_embedded_world_app(
            self.world_state,
            self.registry,
            self.ledger,
            self.metrics,
            tick_engine=self.tick_engine,
            admin_extras=self.admin_extras,
            get_population_dials=lambda: self.population_dials,
            set_population_dials=lambda updates: self.set_population_dials(**updates),
            genesis=getattr(self, "genesis", None),
            cognition="mock" if self.config.mock_mode else "engine",
            broker_base_url=self.config.broker_base_url,
            engine_liveness=self.engine_liveness,
            engine_output=self.engine_output,
            wire_health=self.collect_wire_health,
            recorder_stats=self.trace_store.stats if self.trace_store is not None else None,
            map_service=self.map_service,
            trace_client=self.trace_store.client if self.trace_store is not None else None,
            run_cap_usd_cents_getter=lambda: self.config.budget_cap_cents,
        )
        self.world_app = app
        return app

    async def start_embedded_world_server(self) -> None:
        if self.config.world_mode != "embedded":
            return
        app = self.build_world_app()
        uv_config = uvicorn.Config(
            app, host=self.config.world_host, port=self.config.world_port, log_level="warning"
        )
        server = uvicorn.Server(uv_config)
        self.embedded_world_server = server
        self.embedded_world_server_task = asyncio.create_task(server.serve())
        deadline = time.monotonic() + 5.0
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        logger.info(
            "embedded world server listening on %s:%s", self.config.world_host, self.config.world_port
        )


    async def run_tick(self) -> TickResult:
        assert self.tick_engine is not None and self.world_state is not None
        assert self.gateway is not None and self.metrics is not None and self.governor is not None

        await self.sim_control.wait_if_paused()

        self.tick_engine.perception.hearing_window_ticks = self.depth.hearing_window_ticks

        result = await self.tick_engine.tick()
        self.world_state.current_tick = result.tick.tick_index

        await self.dispatch_due_agents(result)
        await self.run_life_ticks(result.tick)

        if self.config.checkpoint_every_day and result.checkpoint is not None:
            pass

        self.metrics.record_burn_sample()
        self.sample_resources(result.tick.tick_index)
        await self.capture_reading_if_due(result.tick.tick_index)
        self.governor.check()
        return result

    async def capture_reading_if_due(self, tick: int) -> None:
        """Read the world into the checkpoint directory, every so often.

        Same reading the stop path takes (`capture_final_reading`), for the
        runs that never reach it. Overwrites: a later reading describes a
        town that has lived longer, and the report wants the newest.

        Never raises - describing a run must not be able to end it.
        """
        if READING_EVERY_TICKS <= 0 or tick <= 0 or tick % READING_EVERY_TICKS:
            return
        try:
            await capture_final_reading(
                app=self.world_app,
                base_url=self.config.world_base_url(),
                checkpoint_dir=self.config.checkpoint_dir,
                tick=tick,
            )
        except Exception:  # noqa: BLE001 - see the docstring
            logger.exception("could not read the world at tick %s", tick)
        if SNAPSHOT_HOMES:
            await asyncio.to_thread(
                snapshot_engine_homes, self.config.home_root, self.config.checkpoint_dir
            )

    def sample_resources(self, tick: int) -> None:
        """What every engine costs the machine, this tick.

        Every memory figure this project has acted on is a single global
        average, and on Windows it is not even a measurement:
        `memory_budget.measured_mb_per_engine` shells out to `ps`, which
        is not there, so preflight falls back to a 550 MB constant taken
        on a Linux box. Its own comment says "the constant is not wrong;
        it is wrong HERE".

        Per agent, per tick, so the log can answer which resident is
        expensive, whether an engine grows over a run, and - when a run
        ends in ReadError - whether the machine was the reason.

        Never raises and never intervenes. See world/resource_log.py.
        """
        if self.resources is None:
            return
        self.resources.pids = {
            unit_id.removeprefix("u-"): process.pid
            for unit_id, process in getattr(self.supervisor, "processes", {}).items()
            if process is not None and process.returncode is None
        }
        self.resources.sample(tick)

    def record_upkeep_arrears(self, result: Any) -> int:
        """Book an unpaid upkeep bill against the household's members.

        ECON-15. Returns the total booked, so a run can say so rather
        than leaving this as one more mechanism nobody can tell fired.

        Never raises: this runs inside the day boundary, and a bookkeeping
        failure must not take the town down over money that was already
        not paid.
        """
        assert self.world_state is not None
        outcome = result.outcome
        shortfall = int(outcome.shortfall_sim)
        if shortfall <= 0:
            return 0

        contributions = {
            agent_id: amount
            for agent_id, amount in (outcome.contributions or {}).items()
            if amount > 0
        }
        if contributions:
            total = sum(contributions.values())
            shares = {
                agent_id: shortfall * amount // total
                for agent_id, amount in contributions.items()
            }
        else:
            members = [m.agent_id for m in result.updated_members]
            if not members:
                return 0
            shares = {agent_id: shortfall // len(members) for agent_id in members}

        booked = sum(shares.values())
        if booked < shortfall and shares:
            biggest = max(shares, key=lambda a: (shares[a], a))
            shares[biggest] += shortfall - booked

        for agent_id, amount in shares.items():
            if amount <= 0:
                continue
            bucket = self.world_state.arrears.setdefault(agent_id, {})
            bucket["upkeep"] = bucket.get("upkeep", 0) + amount
        logger.info(
            "ECON-15: booked %d sim of unpaid upkeep against %d resident(s)",
            shortfall, len([a for a in shares.values() if a > 0]),
        )
        return shortfall

    def who_pays_for(self, heads: list[str]) -> str:
        """Which member of a household is billed for its head tax.

        GOV-14. `heads[0]` - the first member as the world happened to
        seed them - was an accident of ordering, not a rule. It meant a household's tax
        fell on whoever `agents` iterated first, which in practice was
        often a child or a dependent with no income, so the household went
        into debt while an earning adult beside them held sim.

        The richest holder pays. Deterministic (the id breaks a tie, so two
        runs of the same seed bill the same person), and it is the only
        choice that makes a household's tax a fact about the household
        rather than about dictionary order.
        """
        world = self.world_state
        assert world is not None
        return max(heads, key=lambda a: (world.ledger.sim_balance(a), a))

    def sleeps_through(self, agent_id: str, timestamp: GameTimestamp) -> bool:
        """Whether this resident is left asleep on this tick (CORE-17).

        Night was dispatched exactly like day: a perception built for every
        resident on every one of the 48 night ticks, handed to the gateway,
        and thrown away there because `is_sleeping` short-circuits the turn.
        The whole night was paid for and nothing came of it, which is a
        large part of why no run in 23 has ever reached tick 144 - the
        budget ran out crossing a night nobody could observe.

        Three things still wake a resident, and they are the reason this is
        a predicate rather than a blanket skip:

          * the sleep-onset edge, so a resident gets one turn to finish
            what it was doing and go to bed rather than freezing mid-act;
          * a wake reason - somebody spoke to them, something arrived - so
            an overnight interrupt is not silently dropped;
          * dawn, which is daytime and never reaches this at all.
        """
        if timestamp.is_daytime:
            return False
        world = self.world_state
        assert world is not None
        if world.wake_reasons.get(agent_id):
            return False
        return timestamp.day_tick != NIGHT_ONSET_TICK

    def is_dormant(self, agent_id: str) -> bool:
        """Whether this resident has run out of money and stopped (INFRA-03).

        Reads the world's own dormancy record rather than the wallet, so
        revival (which clears the flag) immediately makes the resident
        dispatchable again without this having to know how revival works.
        """
        world = self.world_state
        if world is None:
            return False
        agent = world.agents.get(agent_id)
        if agent is None:
            return False
        return (not agent.alive) or agent.dormancy.is_dormant

    async def dispatch_due_agents(self, result: TickResult) -> None:
        assert self.gateway is not None and self.tick_engine is not None
        assert self.world_state is not None

        async def one(agent_id: str) -> None:
            assert self.world_state is not None
            if self.is_dormant(agent_id):
                self.dormant_skips += 1
                return
            if self.sleeps_through(agent_id, result.tick):
                self.night_skips += 1
                return
            if self.world_state.is_working(agent_id):
                await self.world_state.advance_continuing(self.registry, agent_id)
                if self.world_state.is_working(agent_id):
                    return

            perception = result.perceptions.get(agent_id)
            timestamp = result.tick
            is_sleeping = not timestamp.is_daytime
            self.world_state.note_turn(agent_id, timestamp.tick_index)
            await self.refresh_goals_for(agent_id, timestamp.tick_index)
            text = (
                render_perception_text(
                    perception,
                    depth=self.depth,
                    visible_agent_bios=self.agent_bios,
                    visible_agent_names=self.agent_names,
                    self_brief=self.standing_brief(agent_id),
                    skill_playbook=self.world_state.skill_playbook(agent_id),
                    authoring_hint=self.world_state.authoring_hint(agent_id),
                    open_questions=self.world_state.open_questions(
                        agent_id, timestamp.tick_index
                    ),
                    awaiting_reply=self.world_state.awaiting_reply(
                        agent_id, timestamp.tick_index
                    ),
                    wallet=self.wallet_line(agent_id, timestamp.tick_index),
                    stopped_working=self.world_state.wake_reasons.pop(agent_id, None),
                    conversation=self.world_state.conversation_here(
                        agent_id, timestamp.tick_index
                    ),
                    silent_turns=self.world_state.silent_turns_report(agent_id),
                    prices_here=self.prices_for(agent_id),
                    market=self.market_for(agent_id),
                    visible_agent_offers=self.offers_beside(agent_id),
                    intention=self.intention_line(agent_id),
                    intention_outcome=self.intention_outcome_line(agent_id),
                    goals=self.goal_lines(agent_id),
                    needs=self.need_lines(agent_id),
                    beliefs=self.belief_lines(agent_id),
                    goal_outcomes=self.goal_outcome_lines(agent_id),
                    life_stage=self.life_stage_line(agent_id),
                    civic_notice=self.civic_notice_for(agent_id),
                    convening_hint=self.world_state.convening_hint(agent_id),
                    gap_notice=(
                        self.world_state.gap_feedback.notice_for(self.world_state, agent_id)
                        if self.world_state.gap_feedback is not None
                        else None
                    ),
                )
                if perception is not None
                else ""
            )
            self._tick_had_a_dispatch = True
            try:
                outcome = await asyncio.wait_for(
                    self.gateway.send_tick(
                        agent_id, tick=timestamp.tick_index, is_sleeping=is_sleeping,
                        perception=text,
                        call_budget=self.call_budget_for(agent_id),
                        game_day=timestamp.game_day,
                    ),
                    timeout=self.config.tick_timeout_s,
                )
                if outcome.turn_in_flight:
                    self.dispatch_skipped_ticks += 1
                    logger.info(
                        "engine for %s refused tick %d: still running the "
                        "previous turn (%d skipped so far)",
                        agent_id, timestamp.tick_index,
                        self.dispatch_skipped_ticks,
                    )
                    return
                if outcome.succeeded:
                    self._tick_had_a_success = True
                    if outcome.steps_used:
                        self.recent_steps_used.append(int(outcome.steps_used))
                if outcome.budget_hit:
                    self.budget_hit_last_turn[agent_id] = outcome.steps_used
                await self.settle_outcome_cost(agent_id, outcome, timestamp.tick_index)
            except asyncio.TimeoutError:
                self.timed_out_ticks += 1
                self.dispatch_blocked_until[agent_id] = (
                    time.monotonic() + self.config.tick_timeout_s
                )
                logger.warning(
                    "tick dispatch timed out for %s after %.0fs (%d so far); "
                    "the engine is still running that turn and still being "
                    "billed for it - not dispatching again for %.0fs",
                    agent_id, self.config.tick_timeout_s, self.timed_out_ticks,
                    self.config.tick_timeout_s,
                )
            finally:
                self.tick_engine.mark_agent_decided(agent_id)

        if result.due_agents:
            due = [a for a in result.due_agents if self.may_dispatch_to(a)]
            skipped = len(result.due_agents) - len(due)
            if skipped:
                self.dispatch_skipped_ticks += skipped
                logger.info(
                    "skipped %d resident(s) whose previous turn has not "
                    "finished (%d skipped so far)",
                    skipped, self.dispatch_skipped_ticks,
                )
            if due:
                outcomes = await asyncio.gather(
                    *(one(agent_id) for agent_id in due), return_exceptions=True
                )
                for agent_id, outcome in zip(due, outcomes):
                    if isinstance(outcome, BaseException):
                        self.turns_lost_to_errors += 1
                        logger.warning(
                            "%s lost its turn to %s: %s (%d turn(s) lost so far)",
                            agent_id,
                            type(outcome).__name__,
                            outcome,
                            self.turns_lost_to_errors,
                            exc_info=outcome,
                        )

        await self.act_the_controls(result.tick.tick_index)

        await self.drain_tool_invocations()

        self.count_the_barren_tick(result)


    def count_the_barren_tick(self, result: TickResult) -> None:
        """Whether this tick counts against the CORE-32 halt.

        Split out of `dispatch_due_agents` so it can be driven
        directly: the difference between a sleeping town and a dead
        one is a single boolean, and it stopped a 5-hour run at fifty
        minutes.
        """
        if result.due_agents and self._tick_had_a_dispatch:
            if self._tick_had_a_success:
                self.barren_ticks = 0
                self.first_barren_tick_at = None
            else:
                self.barren_ticks += 1
                if self.first_barren_tick_at is None:
                    self.first_barren_tick_at = time.monotonic()
        self._tick_had_a_success = False
        self._tick_had_a_dispatch = False

    async def settle_outcome_cost(self, agent_id: str, outcome: TickOutcome, tick: int) -> None:
        """Burns a mock cognition turn's cost against the ledger.

        This is the missing half of module docstring point 5's "both paths
        drive the exact same GenesisRunner tick loop, checkpointing,
        metrics, and spend-governor code": the spend governor and
        GenesisMetrics.spend_usd_cents both read straight off Ledger's
        BURNED_ACCOUNT_ID balance (see GenesisMetrics and SpendGovernor
        above), so a dry run that never burns anything would silently
        never spend a cent no matter how the budget cap or retrieval depth
        were tuned, defeating the whole point of rehearsing the real run's
        stop conditions in mock mode - and MockAgentGateway's own docstring
        promises "a real, honest simulation of the documented contract's
        cost shape, not a fake pass."

        A single mock call costs a fraction of a cent (RetrievalDepthConfig
        .estimated_cost_per_call_usd, ~$0.0018-0.0023) but the ledger only
        holds integer cents, so this method does NOT use
        TickOutcome.cost_usd_cents (already rounded to the nearest cent
        per-call - at these amounts, round() sends every single call to
        zero, which is exactly the bug this method fixes). Instead it
        accumulates the true fractional USD in
        self.counters.mock_cost_remainder_usd and only burns a whole cent
        once the running total crosses one, the same sub-cent rounding
        reserve technique real billing systems use - see RunnerCounters'
        own docstring for why this field is checkpointed. The increment
        and the whole-cent extraction below are synchronous with no
        `await` between them, so concurrent callers from the same tick's
        asyncio.gather() (dispatch_due_agents.one()) cannot race on it -
        Python's single-threaded event loop only switches coroutines at an
        `await` point.

        Only fires for the mock gateway: HttpAgentGateway (real mode)
        reports cost_usd_cents=0 by contract (its own docstring - the real
        burn happens through broker's POST to build_embedded_world_app()'s
        /ledger/debit route, driven by the engine subprocess's own herald
        call, independently of this method), and a sleeping agent's mock
        outcome carries zero cost by design (MockAgentGateway.send_tick),
        so both are correctly no-ops here without needing a special case.

        Insufficient funds is DESIGN.md section 3's cognitive poverty, not
        a runner failure: the agent goes dormant rather than crashing the
        tick loop over it. A halted ledger is caught the same way for the
        same reason - SpendGovernor.check() already treats ledger.halted as
        a trip condition on the very next call, so swallowing it here just
        lets that check (not an unhandled exception mid-gather) be the
        thing that stops the run.
        """
        if not outcome.succeeded or outcome.is_sleeping:
            return
        if not isinstance(self.gateway, MockAgentGateway):
            return
        self.counters.mock_cost_remainder_usd += self.depth.estimated_cost_per_call_usd
        whole_cents = int(self.counters.mock_cost_remainder_usd * 100)
        if whole_cents <= 0:
            return
        self.counters.mock_cost_remainder_usd -= whole_cents / 100.0
        try:
            await self.ledger.burn_for_inference(
                agent_id, whole_cents, tick, memo="mock inference dry-run"
            )
        except (AccountNotFoundError, ParentNotConfiguredError) as exc:
            logger.warning("cannot burn inference cost for %s: %s", agent_id, exc)
        except LedgerInsufficientFundsError:
            logger.info("agent %s is cognitively poor, inference cost not burned", agent_id)
        except LedgerHaltedError as exc:
            logger.warning("ledger halted while burning inference cost for %s: %s", agent_id, exc)

    async def run_life_ticks(self, timestamp: GameTimestamp) -> None:
        """Hourly aging/dormancy-death and daily health/death rolls (life/
        tick.py's own docstring invites "world's tick engine" to call
        these once nobody else has wired them in - see that module's
        docstring and this file's module docstring for why that caller is
        this runner)."""
        assert self.world_state is not None

        total_hours = timestamp.tick_index * (10.0 / 60.0)
        dials = self.population_dials

        await self.mark_the_broke_dormant(timestamp)
        await self.release_served_sentences(timestamp)
        await self.finish_what_the_town_built(timestamp)
        await self.charter_tick(timestamp)

        regenerate_zones(self.world_state)
        await self.close_the_veto_window(timestamp)

        if total_hours - self.counters.last_life_hour_processed >= 1.0:
            elapsed = total_hours - self.counters.last_life_hour_processed
            now = LifeGameClock(total_hours=total_hours)
            for agent_id, agent in list(self.world_state.agents.items()):
                if not agent.alive:
                    continue
                agent, life_events = aging.advance_age(agent, elapsed)
                if any(e.type == "agent.aged_to_adult" for e in life_events):
                    agent, coming_of_age_events = await self.bring_of_age(agent, total_hours)
                    life_events = [*life_events, *coming_of_age_events]
                if death.check_dormancy_death(self.rng, agent.dormancy, now, dials):
                    agent, death_events = await self.apply_death(
                        agent, "usd_zero_unrevived", timestamp.tick_index
                    )
                    life_events = [*life_events, *death_events]
                self.world_state.agents[agent_id] = agent
                await self.publish_life_events(life_events, timestamp.tick_index)
            self.counters.last_life_hour_processed = total_hours

        if timestamp.game_day != self.counters.last_life_day_processed:
            for agent_id, agent in list(self.world_state.agents.items()):
                if not agent.alive:
                    continue
                current_health, recovery_events = life_health.advance_recovery(
                    agent.agent_id, agent.health, timestamp.game_day
                )
                agent = agent.model_copy(update={"health": current_health})
                life_events = list(recovery_events)

                if death.check_terminal_death(self.rng, agent.health, timestamp.game_day, dials):
                    agent, death_events = await self.apply_death(
                        agent, "terminal", timestamp.tick_index
                    )
                    life_events.extend(death_events)
                elif death.check_chronic_death(self.rng, agent.health, dials):
                    agent, death_events = await self.apply_death(
                        agent, "untreated_chronic", timestamp.tick_index
                    )
                    life_events.extend(death_events)
                elif death.check_elderly_death(self.rng, agent.age_years, dials):
                    agent, death_events = await self.apply_death(
                        agent, "elderly_hazard", timestamp.tick_index
                    )
                    life_events.extend(death_events)
                else:
                    new_health, onset_events = life_health.roll_daily_health(
                        self.rng, agent.agent_id, agent.health, timestamp.game_day
                    )
                    agent = agent.model_copy(update={"health": new_health})
                    life_events.extend(onset_events)

                self.world_state.agents[agent_id] = agent
                await self.publish_life_events(life_events, timestamp.tick_index)

            await self.run_daily_upkeep(timestamp, dials)

            await self.charge_the_day_tax(timestamp)
            await self.pay_the_town_payroll(timestamp)
            await self.pay_the_pensions(timestamp)
            await self.reconcile_wages(timestamp)
            for agent_id, lapsed in self.world_state.expire_intentions():
                logger.debug(
                    "day %d: %s did not do what they set out to: %s",
                    timestamp.game_day, agent_id, lapsed.text,
                )
            for expiry in expire_goals(self.world_state):
                expiry.tick = timestamp.tick_index
                await self.world_state.event_bus.publish(expiry)
            self.assert_the_day_was_charged(timestamp)

            self.counters.last_life_day_processed = timestamp.game_day

    async def pay_the_pensions(self, timestamp: GameTimestamp) -> None:
        """Pay every retiree, once a game-day (ECON-09).

        `Ledger.pay_pension` implements DESIGN section 4 in full - treasury
        pays at 70+, a quarter of last salary for a private-sector retiree
        and half for a government one - and had **zero callers**. Nobody in
        this town has ever drawn a pension, so growing old was pure loss:
        a resident aged past working and then simply ran out of money.

        The caller has to supply age, last salary and employment type,
        because the ledger deliberately does not own demographics. Age comes
        from the life record. Last salary comes from the wage promise the
        resident last held; a retiree who never held one draws nothing,
        which is correct - there is no salary to take a quarter of.

        One household's failure must not stop the rest, and a pension that
        cannot be paid (an empty treasury) is a fact about the town worth
        seeing rather than an exception worth ending a tick for.
        """
        assert self.world_state is not None
        from world.ledger.money import PENSION_MINIMUM_AGE

        world = self.world_state
        retirement_age = PENSION_MINIMUM_AGE
        try:
            retirement_age = (await world.charter.store.get_policy()).pension_eligibility_age
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not read the retirement age: %s", exc)
        for agent_id, agent in list(world.agents.items()):
            if not agent.alive or agent.age_years < retirement_age:
                continue
            last_salary = max(
                (
                    promise.wage_sim
                    for key, promise in world.wage_promises.items()
                    if key.split("|", 1)[-1] == agent_id
                ),
                default=0,
            )
            if last_salary <= 0:
                self.pensions_without_a_salary += 1
                continue
            employment = "private"
            policy_pct: int | None = None
            policy_age: int | None = None
            try:
                employment = await self.world_state.charter.government.\
                    pension_employment_type(agent_id)
                policy = await self.world_state.charter.store.get_policy()
                policy_pct = (
                    policy.pension_government_pct
                    if employment == "government"
                    else policy.pension_private_pct
                )
                policy_age = policy.pension_eligibility_age
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not read pension policy: %s", exc)
            try:
                await self.ledger.pay_pension(
                    agent_id,
                    int(agent.age_years),
                    last_salary,
                    employment,
                    timestamp.tick_index,
                    memo="daily pension",
                    pension_pct=policy_pct,
                    minimum_age=policy_age,
                )
            except Exception as exc:  # noqa: BLE001 - an unpayable pension is
                logger.warning("could not pay %s's pension: %s", agent_id, exc)
                continue
            self.pensions_paid += 1

    async def pay_the_town_payroll(self, timestamp: GameTimestamp) -> None:
        """GOV-08. Pay everyone the town employs, once a game-day.

        The finding's line was that the treasury has revenue and no
        spending: head tax, registration fees, fines and donations flow
        in, and nothing at all flows out. `Job.salary_sim` was stored by
        `create_job` and read by nobody; `appoint_police`'s `wage_sim`
        was an event payload and nothing else. So a town could staff a
        police force, a school and a clinic, and every one of those
        people worked for free while `treasury_view` broke out a wage
        line it would never see.

        Half of the fix already existed and had no caller. Deni's
        ECON-10 work gave `GovernmentEngine` a real payroll ledger -
        `payroll_due` says who is owed what as of a tick,
        `record_payroll_paid` closes a game-day so it cannot be paid
        twice - and deliberately left the paying to somebody else,
        because `charter/` holds no reference to the money layer and
        importing one would be a cycle. This is that somebody: the
        charter decides, the caller pays, the caller reports back.

        Reported back only after the money moved. A payroll line settled
        before the transfer would be a day's wages the town believes it
        paid and did not - the same shape as the refusal log that used
        to be written inside the transaction that rolled it back.
        """
        assert self.world_state is not None
        world = self.world_state
        government = world.charter.government
        try:
            lines = await government.payroll_due(timestamp.tick_index)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not read the payroll: %s", exc)
            return

        for line in lines:
            if line.amount_sim <= 0 or line.agent_id not in world.agents:
                continue
            if line.agent_id in (world.confined or {}):
                self.payroll_skipped_confined += 1
                continue
            try:
                await self.ledger.pay_wage(
                    TREASURY_ACCOUNT_ID,
                    line.agent_id,
                    line.amount_sim,
                    timestamp.tick_index,
                    memo=f"government wage: {line.title}",
                )
            except Exception as exc:  # noqa: BLE001 - an empty treasury is a
                logger.warning(
                    "could not pay %s for %s: %s", line.agent_id, line.job_id, exc
                )
                self.payroll_unpayable += 1
                continue
            try:
                await government.record_payroll_paid(
                    line.job_id, line.periods, line.amount_sim, timestamp.tick_index
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "PAID %s sim to %s for %s and could not close the period: %s",
                    line.amount_sim, line.agent_id, line.job_id, exc,
                )
                continue
            self.payroll_paid_sim += line.amount_sim
            self.payroll_lines_paid += 1

    async def charge_the_day_tax(self, timestamp: GameTimestamp) -> None:
        """One head tax bill per household, per completed game-day.

        A7. Nobody in this town has ever paid for existing. Without a tax
        bill the rate is not a political question, `file_complaint` has
        nothing to hear, and `vote` is a surface with no cause - which is
        exactly what twenty runs found and read as apathy.

        A household that cannot cover every head pays for as many as it
        can, in order, and the rest becomes recorded debt. Never a
        negative balance, never a silent write-off: `check_debts` reads
        the arrears back and `charge_daily_head_tax` files the events.
        """
        assert self.world_state is not None and self.ledger is not None
        from world.ledger.head_tax import charge_daily_head_tax

        policy = await self.world_state.charter.store.get_policy()
        rate_bps = int(policy.tax_rate_bps or 0)

        by_household: dict[str, list[str]] = {}
        solo: list[str] = []
        for agent_id, agent in self.world_state.agents.items():
            if not agent.alive:
                continue
            household_id = agent.household_id
            if household_id:
                by_household.setdefault(household_id, []).append(agent_id)
            else:
                solo.append(agent_id)

        charged = 0
        settled_sim = 0
        for household_id, heads in [*by_household.items(), *((None, [a]) for a in solo)]:
            payer = self.who_pays_for(heads)
            try:
                result = await charge_daily_head_tax(
                    self.ledger,
                    self.head_tax_debts,
                    payer_agent_id=payer,
                    head_agent_ids=heads,
                    tax_rate_bps=rate_bps,
                    tick=timestamp.tick_index,
                    household_id=household_id,
                    event_sink=self.ledger.event_sink,
                )
            except Exception as exc:  # noqa: BLE001 - one household must not
                logger.warning("head tax failed for %s: %s", household_id or payer, exc)
                continue
            charged += 1
            settled_sim += int(result.total_paid_sim)
            for debt in result.new_debts:
                bucket = self.world_state.arrears.setdefault(debt.debtor_agent_id, {})
                bucket["head_tax"] = bucket.get("head_tax", 0) + debt.amount_sim
                settled_sim += int(debt.amount_sim)

        self.day_jobs_ran[timestamp.game_day] = {
            **self.day_jobs_ran.get(timestamp.game_day, {}),
            "head_tax": charged,
            "head_tax_sim": settled_sim,
            "head_tax_rate_bps": rate_bps,
            "households": len(by_household) + len(solo),
        }
        logger.info(
            "day %d: head tax charged for %d household(s) at %d bps",
            timestamp.game_day, charged, rate_bps,
        )

    async def reconcile_wages(self, timestamp: GameTimestamp) -> None:
        """Compare what was promised to what was paid, and tell both sides.

        A5's "the gap, rendered". The world never pays on its own - this
        is the counting half, and it is the only thing that makes a
        promise mean anything: an employer who never pays now has a line
        in its own perception every morning saying so, and so does the
        employee.
        """
        assert self.world_state is not None
        moved = self.world_state.accrue_wages(timestamp.game_day, timestamp.tick_index)

        owing = 0
        for promise in self.world_state.wage_promises.values():
            if promise.owed_sim <= 0:
                continue
            owing += 1
            business = self.world_state.businesses.get(promise.business_id)
            await self.world_state.event_bus.publish(
                WorldEvent(
                    type="business.wage.overdue",
                    tick=timestamp.tick_index,
                    payload={
                        "business_id": promise.business_id,
                        "business_name": business.name if business else promise.business_id,
                        "employer_id": business.owner_agent_id if business else None,
                        "employee_id": promise.employee_id,
                        "agent_id": promise.employee_id,
                        "owed_sim": promise.owed_sim,
                        "days_overdue": promise.days_overdue(timestamp.tick_index),
                        "involved_agent_ids": [
                            who for who in (
                                promise.employee_id,
                                business.owner_agent_id if business else None,
                            ) if who
                        ],
                    },
                )
            )

        self.day_jobs_ran[timestamp.game_day] = {
            **self.day_jobs_ran.get(timestamp.game_day, {}),
            "wages_accrued": len(moved),
            "wages_overdue": owing,
        }

    def assert_the_day_was_charged(self, timestamp: GameTimestamp) -> None:
        """The roadmap's T3 rule, applied to the economy.

        Evaluation spec S1: "Head tax and upkeep charged at every day
        boundary, for every household - 100%, gated, halts the run if
        missed." A run whose economy silently skipped a day is a run whose
        stakes figures are fiction, and reporting them would be worse than
        stopping.
        """
        record = self.day_jobs_ran.get(timestamp.game_day) or {}
        households = record.get("households", 0)
        if households and record.get("head_tax", 0) < households:
            self.request_stop()
            self.halt_reason = (
                f"day {timestamp.game_day}: head tax reached "
                f"{record.get('head_tax', 0)} of {households} household(s). "
                "A day the economy skipped makes every stakes figure fiction "
                "(evaluation spec S1)."
            )
            logger.error("%s", self.halt_reason)
            return

        rate_bps = record.get("head_tax_rate_bps", 0)
        if households and rate_bps > 0 and record.get("head_tax_sim", 0) <= 0:
            self.request_stop()
            self.halt_reason = (
                f"day {timestamp.game_day}: head tax ran at {rate_bps} bps "
                f"across {households} household(s) and moved 0 sim. The rate "
                "is not zero, so the money should not be either "
                "(evaluation spec S1)."
            )
            logger.error("%s", self.halt_reason)

    def intention_line(self, agent_id: str) -> dict[str, Any] | None:
        assert self.world_state is not None
        current = self.world_state.intention_of(agent_id)
        if current is None:
            return None
        return {"text": current.text, "set_clock": current.set_clock}

    async def refresh_goals_for(self, agent_id: str, tick: int) -> None:
        """ENG-24. Read this resident's pressures, then act on them.

        Called once per turn, immediately before the perception is
        built, so a goal spawned by hunger this tick is in the prompt
        this tick rather than the next one. The events are published
        rather than returned because a goal that appeared without a row
        in the trace could not be told from one a report invented.
        """
        assert self.world_state is not None
        from world.tools.goal_lifecycle import refresh_and_spawn

        try:
            events = refresh_and_spawn(self.world_state, agent_id)
        except Exception:
            logger.exception("could not refresh needs for %s", agent_id)
            return
        for event in events:
            event.tick = tick
            await self.world_state.event_bus.publish(event)

    def goal_lines(self, agent_id: str) -> list[dict[str, Any]]:
        """The resident's live goals, most important first."""
        assert self.world_state is not None
        from world.tools.goal_lifecycle import active_goals

        return [
            {
                "text": goal.text,
                "horizon": goal.horizon.value,
                "progress": goal.progress,
                "priority": goal.priority,
                "goal_id": goal.goal_id,
            }
            for goal in active_goals(self.world_state, agent_id)
        ]

    def goal_outcome_lines(self, agent_id: str) -> list[dict[str, Any]]:
        """What became of this resident's goals, delivered once.

        Popped rather than read - see `intention_outcome_line`, which
        does the identical thing for the identical reason.
        """
        assert self.world_state is not None
        outcomes = self.world_state.last_goal_outcome.pop(agent_id, None) or []
        return [{"text": goal.text, "reason": reason} for goal, reason in outcomes]

    def life_stage_line(self, agent_id: str) -> str | None:
        """One sentence naming the stage of life this resident is in."""
        assert self.world_state is not None
        from world.life.stages import describe, life_stage

        return describe(life_stage(self.world_state, agent_id))

    def need_lines(self, agent_id: str) -> list[dict[str, Any]]:
        """The pressures worth telling the resident about."""
        assert self.world_state is not None
        from world.tools.needs import shown_needs

        return [
            {"kind": need.kind.value, "level": need.level, "since_tick": need.since_tick}
            for need in shown_needs(self.world_state, agent_id)
        ]

    def belief_lines(self, agent_id: str) -> list[dict[str, Any]]:
        """What this resident has learned, most confident and most recent first.

        Bounded to what `format_goals` will show anyway: sending ten
        beliefs so the formatter can drop seven would spend the token
        budget twice.
        """
        assert self.world_state is not None
        from world.retrieval_depth import MAX_BELIEFS_SHOWN

        mine = [
            b for b in (self.world_state.beliefs or {}).values() if b.agent_id == agent_id
        ]
        mine.sort(key=lambda b: (-b.confidence, -b.last_updated_tick))
        return [
            {"subject": b.subject, "statement": b.statement, "confidence": b.confidence}
            for b in mine[:MAX_BELIEFS_SHOWN]
        ]

    def intention_outcome_line(self, agent_id: str) -> dict[str, Any] | None:
        """What became of the last intention, delivered once.

        Popped rather than read, for the same reason `wake_reasons` is: it
        is a message about something that happened, and a message that
        keeps arriving stops being read.
        """
        assert self.world_state is not None
        outcome = self.world_state.last_intention_outcome.pop(agent_id, None)
        if outcome is None:
            return None
        intention, reason = outcome
        return {
            "text": intention.text,
            "reason": reason,
            "set_clock": intention.set_clock,
        }

    def ablation_arm_for(self, agent_id: str) -> str:
        """Which research arm this resident is in, from the genesis cast.

        "full" for anybody genesis does not name, which is the honest
        default: a resident whose arm nobody recorded is not a control for
        anything, and treating it as one would quietly put untreated
        residents in the treated group.
        """
        if self.genesis is None:
            return "full"
        for persona in self.genesis.agents:
            if persona.agent_id == agent_id:
                return persona.ablation_arm.value
        return "full"

    def prices_for(self, agent_id: str) -> dict[str, list[str]]:
        """The board a resident standing here can read - A3.

        Built from the same `describe_prices` `look_around` uses, so the
        prompt and the tool can never disagree about what is for sale.
        """
        assert self.world_state is not None
        from world.tools.primitives.places import describe_prices

        return describe_prices(self.world_state, self.world_state.location_of(agent_id))

    def market_for(self, agent_id: str) -> list[dict]:
        """What is for sale ANYWHERE in town, for this resident's turn.

        `prices_for` above is place-scoped and stays that way - it is the
        shelf beside you. This is the market: over the 50-resident run a
        resident could only ever see what it was standing next to, the
        perception's price board fired not once in 56,623 lines, and 87 of
        140 refused purchases were somebody asking whoever was beside them
        for a thing that person had never priced.
        """
        assert self.world_state is not None
        from world.tools.primitives.places import market_offers

        return market_offers(self.world_state)

    def offers_beside(self, agent_id: str) -> dict[str, str]:
        """What each person standing here is selling, one phrase each.

        An individual's post travels with them, so it belongs beside the
        person rather than in a board. "Wren Thorne, farmer, selling
        cabbage at 12" is the addendum's own line.
        """
        assert self.world_state is not None
        here = self.world_state.location_of(agent_id)
        if here is None:
            return {}
        offers: dict[str, str] = {}
        for other_id, board in self.world_state.price_board.items():
            if other_id == agent_id or other_id not in self.world_state.agents:
                continue
            if self.world_state.location_of(other_id) != here:
                continue
            selling = [
                f"{item} at {post.ask_sim}"
                for item, post in sorted(board.items())
                if post.ask_sim is not None
            ]
            buying = [
                f"{item} at {post.bid_sim}"
                for item, post in sorted(board.items())
                if post.bid_sim is not None
            ]
            parts = []
            if selling:
                parts.append("selling " + ", ".join(selling))
            if buying:
                parts.append("buying " + ", ".join(buying))
            if parts:
                offers[other_id] = "; ".join(parts)
        return offers

    async def mark_the_broke_dormant(self, timestamp: GameTimestamp) -> None:
        """Any living resident whose dollar wallet has reached zero stops
        thinking, and the town is told where they fell.

        The event carries the place and how long they have before the
        24-game-hour deadline, because the only thing anybody can do about
        it is walk there and pay - and a household that is not told where
        cannot. See `revive`.
        """
        assert self.world_state is not None
        from world.life.constants import DORMANCY_THRESHOLD_USD_CENTS
        from world.life.death import DORMANCY_CAUSES, mark_dormant

        now = LifeGameClock(total_hours=timestamp.tick_index * (TICK_MINUTES / 60.0))
        for agent_id, agent in list(self.world_state.agents.items()):
            if not agent.alive or agent.dormancy.is_dormant:
                continue
            if self.ledger.usd_balance(agent_id) > DORMANCY_THRESHOLD_USD_CENTS:
                continue
            dormancy, events = mark_dormant(
                agent.dormancy, now, cause=DORMANCY_CAUSES["swept"])
            if not events:
                continue
            self.world_state.agents[agent_id] = agent.model_copy(
                update={"dormancy": dormancy}
            )
            self.world_state.continuing.pop(agent_id, None)
            household = agent.household_id
            for event in events:
                event.agent_id = agent_id
                event.payload["location_id"] = self.world_state.location_of(agent_id)
                event.payload["household_id"] = household
                event.payload["deadline_hours"] = DORMANCY_DEATH_HOURS
                event.payload["involved_agent_ids"] = [
                    other_id
                    for other_id, other in self.world_state.agents.items()
                    if household and other.household_id == household
                ] or [agent_id]
            await self.publish_life_events(events, timestamp.tick_index)
            logger.info(
                "%s fell dormant at %s (tick %d): the wallet reached zero",
                agent_id, self.world_state.location_of(agent_id), timestamp.tick_index,
            )

    async def close_the_veto_window(self, timestamp: GameTimestamp) -> None:
        """Sign what the President has not vetoed in time.

        The operator's decision of 2026-09-03: the President is a veto
        WINDOW, not a hard gate. As a hard gate, an unattended run can
        never amend its own charter or approve its own expansion, because
        the only person who could is not there - so DESIGN section 7's
        "collapse to one-person rule is reachable" and every question about
        what a town does with its own constitution are unanswerable by
        construction rather than by the town's own choice.

        `--veto-window-ticks` sets the wait. Zero signs as soon as the
        seats carry it, which is what an unattended run wants; a large
        number restores the old behaviour of waiting for a human; negative
        disables auto-signing entirely.

        The veto itself is untouched. An operator who vetoes inside the
        window still stops the amendment.
        """
        assert self.world_state is not None
        window = self.config.veto_window_ticks
        if window < 0:
            return
        now = timestamp.tick_index
        amendments = self.world_state.charter.amendments
        for proposal in list(amendments.proposals.values()):
            if proposal.status is not AmendmentStatus.PASSED_AWAITING_PRESIDENT:
                continue
            passed_at = proposal.passed_tick
            if passed_at is None or now - passed_at < window:
                continue
            try:
                enacted = await amendments.sign_amendment(proposal.proposal_id, now)
            except Exception as exc:  # noqa: BLE001 - a charter value the
                logger.warning(
                    "auto-sign failed for %s: %s", proposal.proposal_id, exc
                )
                continue
            logger.info(
                "amendment %s auto-signed after the veto window: %s -> %s (charter v%s)",
                enacted.proposal_id,
                enacted.field_name,
                enacted.new_value,
                enacted.resulting_charter_version,
            )
            await self.world_state.event_bus.publish(
                WorldEvent(
                    type="politics.amendment.enacted",
                    tick=now,
                    payload={
                        "proposal_id": enacted.proposal_id,
                        "field_name": enacted.field_name,
                        "new_value": enacted.new_value,
                        "charter_version": enacted.resulting_charter_version,
                        "signed_by": "veto_window_expired",
                    },
                )
            )

    def civic_notice_for(self, agent_id: str) -> dict[str, Any] | None:
        """The town's institutional state, for one resident, as facts.

        GOV-17. Nothing has ever told a resident that an election exists.
        No tool returns an election id, a phase, a candidate list, or who
        holds a seat - and both civic verbs the town has (`vote`,
        `declare_candidacy`) take an `election_id` as a required parameter.
        `look_around` at the complex said "vote" without ever saying there
        was something to vote in.

        Free and per-tick, which is what makes it the honest replacement
        for GOV-20's wake fan-out: a candidacy used to interrupt every
        resident in the town to announce itself, buying a model call each.

        Never raises. A perception that breaks takes the tick with it, and
        a resident is better off not knowing about an election than not
        having a turn.
        """
        if self.world_state is None:
            return None
        try:
            store = self.world_state.charter.store
            notice: dict[str, Any] = {}

            elections = self.charter_cache.get("elections") or []
            open_election = next(
                (e for e in elections if e.phase is not ElectionPhase.TALLIED), None
            )
            if open_election is not None:
                notice["election_id"] = open_election.election_id
                notice["phase"] = open_election.phase.value
                notice["candidates"] = [
                    f"{self.agent_names.get(who, who)} ({who})"
                    for who in sorted(open_election.candidates)
                ]
                notice["you_have_voted"] = agent_id in (open_election.ballots or {})
                from world.tools.locations import LOCATION_TOWN_HALL
                from world.tools.place_aliases import display_place

                notice["where"] = display_place(LOCATION_TOWN_HALL)
                notice["candidacy_closes"] = game_time_label(open_election.candidacy_closes_tick)
                notice["voting_opens"] = game_time_label(open_election.voting_opens_tick)
                notice["voting_closes"] = game_time_label(open_election.voting_closes_tick)
                charter = self.charter_cache.get("charter")
                if charter is not None:
                    notice["min_age"] = charter.suffrage_min_age
                    me = self.world_state.agents.get(agent_id)
                    eligible = (
                        me is not None
                        and me.alive
                        and me.age_years >= charter.suffrage_min_age
                    )
                    if eligible and not notice["you_have_voted"]:
                        notice["you_have_not_voted"] = True
                        notice["voting_is_compulsory"] = charter.compulsory_voting
                        notice["you_are_at"] = display_place(
                            self.world_state.location_of(agent_id) or ""
                        )

            state = self.charter_cache.get("government")
            if state is not None:
                seated = [
                    who
                    for who in ([state.pm_agent_id, *state.minister_agent_ids])
                    if who
                ]
                if seated:
                    notice["government"] = ", ".join(
                        self.agent_names.get(who, who) for who in seated
                    )
                else:
                    notice["seats_vacant"] = True

                if state.is_sitting(agent_id):
                    notice["your_seat"] = (
                        "Prime Minister"
                        if agent_id == state.pm_agent_id
                        else "minister"
                    )
                    notice["your_seat_is_caretaker"] = state.is_caretaker_seat(
                        agent_id
                    )

            policy = self.charter_cache.get("policy")
            if policy is not None:
                notice["tax_rate_bps"] = policy.tax_rate_bps
                from world.ledger.head_tax import head_tax_sim_per_head

                notice["head_tax_sim_per_head"] = head_tax_sim_per_head(
                    policy.tax_rate_bps
                )
            return notice or None
        except Exception:  # noqa: BLE001 - see the docstring
            return None

    async def charter_tick(self, timestamp: GameTimestamp) -> None:
        """Advance and conclude the town's elections.

        GOV-01, and it is the single largest reason this town has no
        politics. `schedule_election(0)` runs once at boot
        (`seed_genesis`); `tally()` has no caller anywhere in the tree
        outside the charter's own tests. So no election has ever reached
        TALLIED, `GovernmentState.pm_agent_id` has been None since genesis,
        and `registry.is_visible` hides - and `check_actor_requirements`
        refuses - every GOVERNMENT-namespace tool for every resident, in
        every run:

            petition, set_tax_rate, convene, appoint_police, rule,
            approve_patent, reject_patent, revoke_patent, file_subpoena,
            grant_subpoena, deny_subpoena

        Eleven tools uncallable by construction rather than by apathy. The
        run reports read that as "the town never governed itself"; it was
        one missing call.

        Nor was a second election ever scheduled, so DESIGN section 7's
        "elections every 3 game-days" was a sentence about a cycle that
        could not turn once.

        Cheap enough to run every tick: `advance_phase` is idempotent by
        design (`elections.py:13-22`) and returns early when the computed
        phase already matches, and `list_elections` is an in-memory read.
        """
        assert self.world_state is not None
        now = timestamp.tick_index
        store = self.world_state.charter.store
        elections = await store.list_elections()

        self.charter_cache = {
            "elections": elections,
            "government": await store.get_government_state(),
            "policy": await store.get_policy(),
            "charter": await store.get_charter(),
        }
        if not elections:
            return

        open_elections = [
            e for e in elections if e.phase is not ElectionPhase.TALLIED
        ]
        if not open_elections:
            return

        for election in open_elections:
            try:
                if now >= election.voting_closes_tick:
                    tallied = await self.world_state.charter.elections.tally(
                        election.election_id, now
                    )
                    self.counters.elections += 1
                    result = tallied.result
                    logger.info(
                        "day %d: election %s tallied - pm=%s ministers=%s",
                        timestamp.game_day,
                        tallied.election_id,
                        result.pm_agent_id if result else None,
                        result.minister_agent_ids if result else [],
                    )
                    await self.schedule_the_next_election(now)
                else:
                    await self.world_state.charter.elections.advance_phase(
                        election.election_id, now
                    )
            except Exception as exc:  # noqa: BLE001 - one election must not
                logger.warning(
                    "charter tick failed for election %s: %s",
                    election.election_id,
                    exc,
                )
        self.charter_cache["elections"] = await store.list_elections()
        self.charter_cache["government"] = await store.get_government_state()

    async def schedule_the_next_election(self, now_tick: int) -> None:
        """Keep the cycle turning.

        A tallied election with no successor is a one-off, and DESIGN
        section 7 asks for a rhythm. Scheduled so that the NEXT vote closes
        one interval from now: `schedule_election` lays the three windows
        out forward from the tick it is given, so handing it `now` would
        open candidacy immediately and hold the vote three days early.
        """
        assert self.world_state is not None
        charter = await self.world_state.charter.store.get_charter()
        windows = (
            charter.candidacy_window_days
            + charter.campaign_period_days
            + charter.voting_window_days
        )
        lead = max(0, int((charter.election_interval_days - windows) * TICKS_PER_DAY))
        await self.world_state.charter.elections.schedule_election(now_tick + lead)

    async def finish_what_the_town_built(self, timestamp: GameTimestamp) -> None:
        """Let a building that is due actually finish (GROW-04).

        `complete_construction` and `buildings_due_for_completion` are both
        written, both tested, and had ZERO callers. The second one's own
        docstring describes the caller - "the pure filter a real tick loop
        runs every tick before calling complete_construction on each id" -
        and that loop was never written.

        So a resident could start a building, pay for it, wait out its
        completion_tick, and it would stay UNDER_CONSTRUCTION for the rest
        of the run. Nothing in twenty-three runs has ever been finished,
        which is one of the four reasons nobody in this town can build
        anything (GROW-01 to GROW-04).

        Read-only when nothing is due, so it is safe on every tick.
        """
        assert self.world_state is not None
        from world.tools.primitives.construction import (
            buildings_due_for_completion,
            complete_construction,
        )

        due = buildings_due_for_completion(self.world_state)
        for building_id in due:
            try:
                event = await complete_construction(
                    self.world_state,
                    building_id,
                    map_service=self.map_service,
                )
            except Exception as exc:  # noqa: BLE001 - one bad building must
                logger.warning("could not complete %s: %s", building_id, exc)
                continue
            await self.world_state.event_bus.publish(event)
            self.buildings_completed += 1
            logger.info("building complete: %s", building_id)

    async def release_served_sentences(self, timestamp: GameTimestamp) -> None:
        """Let a jail sentence end when it is served.

        GOV-16 / TOOL-22. `rule(verdict="jail")` sets
        `world.confined[agent]["until_tick"]` and NOTHING has ever read it -
        the only other mention of the field in the tree is the refusal
        message `move` prints. So a sentence lasted until a seat holder or
        an officer remembered to call `release`, which in a town whose
        government has never been seated (GOV-01) means forever.
        `world/crime/outcomes.py` has the sweep for this, written and
        unwired, in the package no run constructs.

        A sentence that does not end is not a sentence, it is a
        disappearance, and it would have silently removed a resident from
        every measurement the run makes.
        """
        assert self.world_state is not None
        now = timestamp.tick_index
        served = [
            agent_id
            for agent_id, held in self.world_state.confined.items()
            if held.get("until_tick") is not None and held["until_tick"] <= now
        ]
        for agent_id in served:
            held = self.world_state.confined.pop(agent_id, {}) or {}
            await self.world_state.event_bus.publish(
                WorldEvent(
                    type="crime.release.recorded",
                    tick=now,
                    payload={
                        "agent_id": agent_id,
                        "case_id": held.get("case_id"),
                        "since_tick": held.get("since_tick"),
                        "released_by": "sentence_served",
                        "involved_agent_ids": [agent_id],
                    },
                )
            )
        if served:
            logger.info("released %d resident(s) whose sentence was served", len(served))

    async def run_daily_upkeep(self, timestamp: GameTimestamp, dials: PopulationDials) -> None:
        """Charge every household its day's living costs, and escalate.

        One call per household per game-day, which is the shape
        world/life/upkeep.py's run_daily_upkeep_tick was written for. Hunger
        state lives on the runner rather than on AgentLifeRecord because that
        record deliberately owns no such field (see its own docstring) - the
        module's report names this dict as the intended home.
        """
        assert self.world_state is not None
        from world.life.upkeep import run_daily_upkeep_tick

        by_household: dict[str, list[Any]] = {}
        for agent in self.world_state.agents.values():
            key = agent.household_id or f"solo:{agent.agent_id}"
            by_household.setdefault(key, []).append(agent)

        for household_id, members in by_household.items():
            try:
                result = await run_daily_upkeep_tick(
                    self.rng,
                    household_id,
                    timestamp.game_day,
                    members,
                    self.hunger_by_agent,
                    LedgerEconomyPort(self.ledger, self.world_state.tick, self.world_state),
                    dials,
                    fed_agent_ids=self.fed_today,
                )
            except Exception as exc:  # noqa: BLE001 - one household must not
                logger.warning("upkeep failed for %s: %s", household_id, exc)
                continue
            for member in result.updated_members:
                self.world_state.agents[member.agent_id] = member
            self.hunger_by_agent.update(result.updated_hunger)
            self.record_upkeep_arrears(result)
            await self.publish_life_events(result.events, timestamp.tick_index)

        self.fed_today.clear()

        unpaid = sum(
            1 for h in self.hunger_by_agent.values()
            if getattr(h, "consecutive_unpaid_days", 0) > 0
        )
        if unpaid:
            logger.info("upkeep day %d: %d resident(s) with unpaid days",
                        timestamp.game_day, unpaid)

    async def apply_death(
        self, agent: AgentLifeRecord, cause: str, tick: int = 0
    ) -> tuple[AgentLifeRecord, list[LifeEvent]]:
        from world.life.models import DeathCause

        updated = agent.model_copy(update={"alive": False, "death_cause": DeathCause(cause)})
        events = [
            LifeEvent(type="health.agent.died", agent_id=agent.agent_id, payload={"cause": cause}),
            LifeEvent(type="agent.died", agent_id=agent.agent_id, payload={"cause": cause}),
        ]
        self.settle_estate_land(updated)
        events.extend(self.settle_estate_tools(updated))
        await self.settle_money_estate(updated, tick)
        scheduler = getattr(self.tick_engine, "scheduler", None)
        if scheduler is not None:
            scheduler.unregister_agent(agent.agent_id)
        return updated, events

    def settle_estate_tools(self, deceased: AgentLifeRecord) -> list[LifeEvent]:
        """Move the deceased's invented tools and skills on.

        By their will where they named one, otherwise to whoever the land
        rule would have chosen - the same order, because a town that split
        a person's house and their trade between two rules would be
        answering the same question twice.
        """
        assert self.world_state is not None
        world = self.world_state
        will = world.wills.get(deceased.agent_id)

        moved: dict[str, list[str]] = {}
        if will is not None:
            for bequest in will.bequests:
                heir = world.agents.get(bequest.heir_id)
                if heir is None or not heir.alive:
                    continue
                for tool_name in bequest.tools:
                    if world.tool_owner(tool_name) != deceased.agent_id:
                        continue
                    world.transfer_tool_ownership(tool_name, bequest.heir_id)
                    moved.setdefault(bequest.heir_id, []).append(tool_name)
                for skill in bequest.skills:
                    level = world.skills.get(deceased.agent_id, {}).get(skill, 0)
                    if level:
                        held = world.skills.setdefault(bequest.heir_id, {})
                        held[skill] = max(held.get(skill, 0), level)

        heir_id = self.nearest_living_relative(deceased)
        if heir_id:
            for tool_name in world.inherit_tools(deceased.agent_id, heir_id):
                moved.setdefault(heir_id, []).append(tool_name)

        return [
            LifeEvent(
                type="business.tools_inherited",
                agent_id=heir,
                payload={
                    "from_agent_id": deceased.agent_id,
                    "heir_id": heir,
                    "tool_names": tools,
                },
            )
            for heir, tools in moved.items()
            if tools
        ]

    async def settle_money_estate(self, deceased: AgentLifeRecord, tick: int) -> None:
        """Hand on the deceased's sim and USD, and close the account.

        ECON-07. Never raises: a death that half-settles is worse than a
        death that leaves a note, and `apply_death` is called from the
        tick loop where an exception takes the whole run down.

        Heirs come from the will's MONEY bequests (`sim_share > 0`) when
        it names any - a will that leaves only tiles or tools is not a
        statement about money - and otherwise from `living_relatives`,
        the same section 31.3 order the land follows.
        """
        assert self.world_state is not None
        will = self.world_state.wills.get(deceased.agent_id)
        heirs: list[str] = []
        if will is not None:
            heirs = [
                b.heir_id for b in will.bequests
                if b.sim_share > 0
                and b.heir_id in self.world_state.agents
                and self.world_state.agents[b.heir_id].alive
            ]
        if not heirs:
            heirs = self.living_relatives(deceased)

        try:
            moved = await self.ledger.settle_inheritance(
                deceased.agent_id, heirs, tick,
                memo=f"estate of {deceased.agent_id}",
            )
        except Exception as exc:  # noqa: BLE001 - a death must not end the run
            logger.warning(
                "ECON-07: could not settle the money estate of %s: %s",
                deceased.agent_id, exc,
            )
            return
        logger.info(
            "ECON-07: settled %s's estate across %d heir(s) in %d move(s)%s",
            deceased.agent_id, len(heirs), len(moved),
            "" if heirs else " - no living heir, escheated to treasury",
        )

    def living_relatives(self, deceased: AgentLifeRecord) -> list[str]:
        """Spouse, then children, then parents - section 31.3's own order,
        filtered to whoever is still alive to receive anything."""
        assert self.world_state is not None
        candidates = [deceased.spouse_id, *deceased.children_ids, *deceased.parent_ids]
        return [
            heir_id for heir_id in candidates
            if heir_id
            and heir_id in self.world_state.agents
            and self.world_state.agents[heir_id].alive
        ]

    def nearest_living_relative(self, deceased: AgentLifeRecord) -> str | None:
        relatives = self.living_relatives(deceased)
        return relatives[0] if relatives else None

    def settle_estate_land(self, deceased: AgentLifeRecord) -> None:
        """Move the deceased's 1x1 land units on: by their will if they set
        one, otherwise to the nearest living relative, otherwise escheat to
        the town - the same order Ledger.settle_inheritance uses for money.
        """
        assert self.world_state is not None
        from world.tools.primitives.property import settle_property_estate

        heirs = self.living_relatives(deceased)
        try:
            settle_property_estate(self.world_state, deceased.agent_id, heirs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not settle land for %s: %s", deceased.agent_id, exc)

    async def bring_of_age(
        self, agent: AgentLifeRecord, total_hours: float
    ) -> tuple[AgentLifeRecord, list[LifeEvent]]:
        """Run one former minor's transition (The 250 section 26).

        The CHOICE belongs to the agent - `come_of_age` is a registered tool
        it can call on its own turn. This is the floor under that: the
        parent-billing relationship ends the moment they turn eighteen
        whether or not they ever think about it, because the alternative is
        an adult whose parent funds their cognition indefinitely simply
        because the agent never got round to deciding anything. An agent that
        does call the tool overrides the default household outcome; it cannot
        un-end the billing, which is not its decision to make.
        """
        assert self.world_state is not None
        from world.life.household import ComingOfAgeChoice, come_of_age
        from world.tools.economy_bridge import LedgerGuardianshipPort

        household = (
            self.world_state.households.get(agent.household_id) if agent.household_id else None
        )
        choice = (
            ComingOfAgeChoice.STAY_AND_CONTRIBUTE
            if household is not None
            else ComingOfAgeChoice.MOVE_OUT_ALONE
        )
        try:
            outcome = await come_of_age(
                agent,
                household,
                choice,
                at=LifeGameClock(total_hours=total_hours),
                guardianship=LedgerGuardianshipPort(self.ledger),
                new_household_id=f"hh-{agent.agent_id}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not bring %s of age: %s", agent.agent_id, exc)
            return agent, []

        self.world_state.households[outcome.household.household_id] = outcome.household
        left = outcome.childhood_household
        if left is not None and left.household_id != outcome.household.household_id:
            if left.member_ids:
                self.world_state.households[left.household_id] = left
            else:
                self.world_state.households.pop(left.household_id, None)
        return outcome.agent, outcome.events

    async def publish_life_events(self, events: list[LifeEvent], tick: int) -> None:
        assert self.world_state is not None
        for event in events:
            await self.world_state.event_bus.publish(life_event_to_world_event(event, tick))


    async def run_continuous(self) -> None:
        """Ticks until stop_requested, the spend governor trips, or
        max_game_days is reached (dry-run/test bound)."""
        self.stop_requested = False
        while not self.stop_requested:
            started_at = time.monotonic()
            spend_before_tick = self.ledger.usd_balance(BURNED_ACCOUNT_ID)
            result = await self.run_tick()
            assert self.governor is not None
            if self.governor.tripped:
                logger.warning("stopping: %s", self.governor.tripped_reason)
                break
            if self.barren_ticks:
                terminal = await self.broker_terminal_reason()
                if terminal is not None:
                    logger.error("stopping: %s", terminal)
                    break
            barren_for_s = (
                (time.monotonic() - self.first_barren_tick_at)
                if self.first_barren_tick_at is not None
                else 0.0
            )
            if self.barren_ticks and barren_for_s >= BARREN_SECONDS_BEFORE_STOP:
                logger.error(
                    "stopping: %.0fs of consecutive barren ticks (%d of them). "
                    "The town is not thinking - check the broker, the spend "
                    "cap, and GET /world/engines.",
                    barren_for_s, self.barren_ticks,
                )
                break
            if self.barren_ticks >= BARREN_TICKS_BEFORE_STOP:
                logger.error(
                    "stopping: %d consecutive ticks where every dispatch "
                    "failed. The town is not thinking - check the broker, the "
                    "spend cap, and GET /world/engines.",
                    self.barren_ticks,
                )
                break
            if (
                self.config.max_game_days is not None
                and result.tick.game_day >= self.config.max_game_days
            ):
                logger.info("stopping: reached max_game_days=%s", self.config.max_game_days)
                break
            if (
                self.config.max_ticks is not None
                and result.tick.tick_index >= self.config.max_ticks
            ):
                logger.info("stopping: reached max_ticks=%s", self.config.max_ticks)
                break
            self.note_tick_spend(self.ledger.usd_balance(BURNED_ACCOUNT_ID) - spend_before_tick)
            await self.drain_before_halt()
            await self.wait_out_the_tick(started_at)

    def night_is_quiet(self) -> bool:
        """True when it is night and nobody was woken (CORE-17)."""
        world = self.world_state
        if world is None or self.tick_engine is None:
            return False
        timestamp = timestamp_for(world.current_tick)
        if timestamp.is_daytime:
            return False
        return not any(world.wake_reasons.values())

    async def wait_out_the_tick(self, started_at: float) -> None:
        """CORE-01. Sleep the remainder of the tick interval.

        `--tick-interval-s` was a dead knob: the loop ended on
        `asyncio.sleep(0)`, which yields to the event loop and returns
        immediately. So the town ran at "as fast as the slowest turn" and
        the clock advanced at whatever rate Bedrock happened to answer -
        one game-hour might take four seconds or ninety, and no two runs
        were comparable. `apply_tick_pacing()` had already computed the
        right interval; nothing ever waited for it.

        The remainder, not the whole interval: a tick that already spent
        3s of a 3s budget should start the next one now. That keeps the
        game clock honest under load instead of compounding the overrun.
        """
        interval = float(self.config.tick_interval_s or 0.0)
        if self.night_is_quiet():
            await asyncio.sleep(0)
            return
        if interval <= 0:
            await asyncio.sleep(0)
            return
        remaining = interval - (time.monotonic() - started_at)
        if remaining <= 0:
            self.ticks_that_overran += 1
            await asyncio.sleep(0)
            return
        await asyncio.sleep(remaining)

    async def run_burst(self, num_ticks: int) -> list[TickResult]:
        results: list[TickResult] = []
        for _ in range(num_ticks):
            results.append(await self.run_tick())
            assert self.governor is not None
            await self.drain_before_halt()
            if self.governor.tripped or self.stop_requested:
                break
        return results

    def request_stop(self) -> None:
        self.stop_requested = True

    DRAIN_RESERVE_CENTS = 10

    def note_tick_spend(self, cents: float) -> None:
        """Remember the most expensive tick, for the drain's reserve."""
        if cents > self.max_tick_spend_cents:
            self.max_tick_spend_cents = float(cents)

    DRAIN_COST_PER_RESIDENT_CENTS = 4

    def drain_reserve_cents(self) -> int:
        """How much of the cap to keep back for consolidation (CORE-05).

        Three things, whichever is largest:

          * a floor, so there is always something;
          * the most expensive tick this run has had, so the cap cannot be
            crossed between two observations;
          * **what the drain itself will cost** - one consolidation call per
            resident.

        The third was missing and it is why run 24's drain fired at exactly
        the right moment and still consolidated 0 of 20. The reserve was 10
        cents; the drain needed to make twenty learning calls at a few cents
        each, so every one of them was refused by a broker that had already
        halted. A drain that fires in time and cannot afford itself is the
        same as no drain, and it reports the same "0 of 20".

        Measured rather than configured, because the right number depends on
        population, cadence and model price, and all three move.
        """
        residents = len(self.world_state.agents) if self.world_state is not None else 0
        drain_cost = residents * self.DRAIN_COST_PER_RESIDENT_CENTS
        return max(
            self.DRAIN_RESERVE_CENTS,
            int(self.max_tick_spend_cents),
            drain_cost,
        )

    async def broker_spend_state(self) -> "str | None":
        """The broker's own verdict on dispatch - "normal", "warning" or
        "halted" - or None when it cannot be asked. Read once, at the drain,
        because the runner's governor and the broker count different things
        and the broker's is the one that decides whether a call is answered.
        """
        base = (self.config.broker_base_url or "").rstrip("/")
        if not base:
            return None
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{base}/admin/spend")
            return str(response.json().get("state")) if response.status_code == 200 else None
        except Exception:  # noqa: BLE001 - a status read must never stop a shutdown
            return None

    async def drain_before_halt(self) -> None:
        """Consolidate once, while the broker will still answer.

        Fires at most once per run, when spend crosses the cap less the
        reserve. Best-effort by construction: a resident whose batch fails
        must not stop the run, and the end-of-shutdown drain still runs
        afterwards for anything this missed.
        """
        if self._drained_before_halt or self.gateway is None:
            return
        if self.governor is None or self.governor.tripped:
            return
        spent = self.ledger.usd_balance(BURNED_ACCOUNT_ID)
        if spent < self.governor.budget_cap_cents - self.drain_reserve_cents():
            return
        self._drained_before_halt = True
        reserve = self.drain_reserve_cents()
        logger.info(
            "spend is within %d cents of the cap (reserve = max(floor %d, "
            "priciest tick %d)): consolidating now, while the broker will "
            "still answer",
            reserve, self.DRAIN_RESERVE_CENTS, int(self.max_tick_spend_cents),
        )
        try:
            drained = await self.gateway.drain_learning()
        except Exception as exc:  # noqa: BLE001 - a drain may not end a run
            logger.warning("pre-halt learning drain failed: %s", exc)
            return
        self.learning_drained = sum(1 for ran in drained.values() if ran)
        logger.info("%s", describe_drain(drained, halted=False))

    async def shutdown(self) -> None:
        logger.info("shutting down genesis runner")
        try:
            await capture_final_reading(
                app=self.world_app,
                base_url=self.config.world_base_url(),
                checkpoint_dir=self.config.checkpoint_dir,
                tick=self.world_state.tick() if self.world_state is not None else None,
            )
        except Exception:  # noqa: BLE001 - describing a run cannot end it
            logger.exception("could not take the final reading")
        try:
            self.wire_health = await self.collect_wire_health()
            self.report_wire_health(self.wire_health)
        except Exception:  # noqa: BLE001 - telemetry must not stop a shutdown
            logger.exception("could not collect the engine link health")
        if self.watchdog is not None:
            await self.watchdog.stop()
            self.watchdog = None
        if getattr(self, "groundskeeper", None) is not None:
            await self.groundskeeper.stop()
            self.groundskeeper = None
        if self.gateway is not None:
            drained = await self.gateway.drain_learning()
            self.learning_drained = sum(1 for ran in drained.values() if ran)
            halted = dispatch_had_halted(
                governor_tripped=bool(self.governor is not None and self.governor.tripped),
                broker_state=await self.broker_spend_state(),
            )
            logger.info("%s", describe_drain(drained, halted=halted))
        if self.supervisor is not None:
            await self.supervisor.shutdown_all()
        archive_engine_homes(self.config.home_root, self.config.checkpoint_dir)
        if self.gateway is not None:
            await self.gateway.aclose()
        if self.embedded_world_server_task is not None:
            if self.embedded_world_server is not None:
                self.embedded_world_server.should_exit = True
            try:
                await asyncio.wait_for(self.embedded_world_server_task, timeout=5.0)
            except asyncio.TimeoutError:
                self.embedded_world_server_task.cancel()
                try:
                    await self.embedded_world_server_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self.tick_engine is not None and self.world_state is not None:
            try:
                checkpoint = await self.tick_engine.checkpoints.write(
                    self.world_state.current_tick, timestamp_for(self.world_state.current_tick).game_day
                )
                logger.info(
                    "final checkpoint written at tick %s - a continuation starts "
                    "here rather than at the last day boundary the run passed",
                    checkpoint.tick_index,
                )
            except CheckpointError as exc:
                logger.error("final checkpoint failed: %s", exc)
            except Exception:  # noqa: BLE001
                logger.exception("final checkpoint failed and the run stopped anyway")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kasukabe genesis runner: boot the world, launch 250 engines, run."
    )
    parser.add_argument(
        "--mock", action="store_true", default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--live", action="store_true", default=None,
        help="no-op; real engines and real inference are the only mode",
    )
    parser.add_argument("--genesis-path", type=Path, default=None)
    parser.add_argument("--checkpoint-dir", type=Path, default=None)
    parser.add_argument("--budget-cap-cents", type=int, default=None)
    parser.add_argument("--retrieval-depth", type=float, default=None)
    parser.add_argument("--world-mode", choices=["embedded", "external"], default=None)
    parser.add_argument("--world-host", default=None)
    parser.add_argument("--world-port", type=int, default=None)
    parser.add_argument("--broker-base-url", default=None)
    parser.add_argument("--engine-repo", type=Path, default=None)
    parser.add_argument("--supervisor-backend", choices=["namespace", "process", "firecracker"], default=None)
    parser.add_argument(
        "--opening", choices=["homes", "gathering"], default=None,
        help=(
            "where the cast wakes up: 'homes' (each at their own household, "
            "dispersed) or 'gathering' (all in the market square). A world "
            "fact, not an instruction - no resident is told to talk or work."
        ),
    )
    parser.add_argument("--max-game-days", type=int, default=None)
    parser.add_argument(
        "--max-ticks", type=int, default=None,
        help="stop after this tick; finer than --max-game-days, which stops only at midnight",
    )
    parser.add_argument(
        "--resource-log",
        type=Path,
        default=None,
        help=(
            "write per-agent CPU and memory to this JSONL file, one line "
            "per engine per tick. Off unless asked for: it is a diagnostic, "
            "and a run that does not want it should not pay for the file."
        ),
    )
    parser.add_argument(
        "--start-at-dawn",
        action="store_true",
        default=False,
        help=(
            "begin at tick 36 (06:00) instead of midnight, so the run does "
            "not spend its first nine minutes of wall clock with everyone "
            "asleep. See DAWN_TICK."
        ),
    )
    parser.add_argument(
        "--resume-at",
        type=resume_at_tick,
        default=None,
        metavar="'DAY HH:MM'",
        help=(
            "resume from the latest checkpoint and move the clock forward to "
            "this moment, e.g. '0 18:00' for day 0's evening. Forward only."
        ),
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help=(
            "a scenario file (world/scenarios/*.json): which cast, which tick the world "
            "starts on, when it stops, when the election opens and who is already in "
            "government. Its settings are applied first; any flag given as well wins."
        ),
    )
    parser.add_argument("--no-resume", action="store_true", default=False)
    parser.add_argument(
        "--tick-interval-s",
        type=float,
        default=None,
        help="seconds between ticks; 0/omitted derives a rate the broker can serve",
    )
    parser.add_argument("--burst-ticks", type=int, default=None, help="run N ticks then stop (tests)")
    return parser


def resume_at_tick(value: str) -> int:
    """`'DAY HH:MM'` -> the tick index of that moment. For `--resume-at`.

    Refuses anything the clock cannot land on: ticks are ten game-minutes,
    so 18:05 is not a moment the town has, and a refusal says so rather than
    rounding an operator's evening to a time they did not ask for.
    """
    import re

    from world.core.clock import TICK_MINUTES, TICKS_PER_DAY

    refusal = (
        f"{value!r} is not a moment on the town's clock: give 'DAY HH:MM' on a "
        f"ten-minute tick, e.g. '0 18:00'"
    )
    match = re.fullmatch(r"\s*(\d+)\s+(\d{1,2}):(\d{2})\s*", value)
    if match is None:
        raise argparse.ArgumentTypeError(refusal)
    day, hour, minute = (int(part) for part in match.groups())
    if hour > 23 or minute > 59 or minute % TICK_MINUTES:
        raise argparse.ArgumentTypeError(refusal)
    return day * TICKS_PER_DAY + (hour * 60 + minute) // TICK_MINUTES


def apply_scenario(config: GenesisRunnerConfig, path: str) -> GenesisRunnerConfig:
    """Fold a scenario file's run settings into a config.

    Only the run-shaped ones: which cast, from which tick to which, how often
    residents take turns, what it may spend. The rest of a scenario - the
    charter's rules, the election's opening, who is already in office - is
    the runner's to apply at genesis, because it is world state rather than
    configuration. `world/scenario.py` has already refused anything that
    could not run by the time this is called.
    """
    scenario = Scenario.load(path)
    plan = scenario.world
    overrides: dict[str, Any] = {"scenario_path": path, "start_tick": plan.start_tick}
    genesis = scenario.genesis_path()
    if genesis is not None:
        overrides["genesis_path"] = genesis
    if plan.stop_tick is not None:
        overrides["max_ticks"] = plan.stop_tick
    if plan.cadence is not None:
        overrides["scheduler_target_fraction"] = plan.cadence
    if plan.budget_cap_usd is not None:
        overrides["budget_cap_cents"] = int(round(plan.budget_cap_usd * 100))
    if plan.max_game_days is not None:
        overrides["max_game_days"] = plan.max_game_days
    if plan.tick_interval_s is not None:
        overrides["tick_interval_s"] = plan.tick_interval_s
    config = dataclasses.replace(config, **overrides)
    errors = config.validate()
    if errors:
        raise ConfigError(errors)
    return config


def config_from_args(args: argparse.Namespace) -> GenesisRunnerConfig:
    config = GenesisRunnerConfig.from_env()
    if args.scenario:
        config = apply_scenario(config, args.scenario)
    overrides: dict[str, Any] = {}
    if args.mock:
        raise SystemExit(
            "--mock is no longer supported: this runner only spawns real engines "
            "against real Bedrock. Drop the flag (and mind the spend cap)."
        )
    if args.live:
        overrides["mock_mode"] = False
    if args.genesis_path is not None:
        overrides["genesis_path"] = args.genesis_path
    if args.checkpoint_dir is not None:
        overrides["checkpoint_dir"] = args.checkpoint_dir
    if args.budget_cap_cents is not None:
        overrides["budget_cap_cents"] = cap_with_room_for_the_drain(
            args.budget_cap_cents, os.environ
        )
    if args.retrieval_depth is not None:
        overrides["retrieval_depth_fraction"] = args.retrieval_depth
    if args.tick_interval_s is not None:
        overrides["tick_interval_s"] = args.tick_interval_s
    if args.world_mode is not None:
        overrides["world_mode"] = args.world_mode
    if args.world_host is not None:
        overrides["world_host"] = args.world_host
    if args.world_port is not None:
        overrides["world_port"] = args.world_port
    if args.broker_base_url is not None:
        overrides["broker_base_url"] = args.broker_base_url
    if args.engine_repo is not None:
        overrides["engine_repo"] = args.engine_repo
    if args.supervisor_backend is not None:
        overrides["supervisor_backend"] = args.supervisor_backend
    if args.opening is not None:
        overrides["opening"] = args.opening
    if args.max_game_days is not None:
        overrides["max_game_days"] = args.max_game_days
    if args.max_ticks is not None:
        overrides["max_ticks"] = args.max_ticks
    if args.resource_log is not None:
        overrides["resource_log_path"] = args.resource_log
    if getattr(args, "start_at_dawn", False):
        overrides["start_at_dawn"] = True
    if args.resume_at is not None:
        if args.no_resume:
            raise SystemExit("--resume-at continues a run; it cannot be combined with --no-resume")
        overrides["resume_at_tick"] = args.resume_at
    if overrides:
        config = dataclasses.replace(config, **overrides)
        errors = config.validate()
        if errors:
            raise ConfigError(errors)
    return config


async def main_async(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    config = config_from_args(args)

    if config.supervisor_backend == "firecracker":
        raise NotImplementedError(
            "supervisor_backend='firecracker' is not wired from run_genesis.py: "
            "infra/ owns VMManager and this package must never import infra/ "
            "(app/CONVENTIONS.md module boundaries). Drive a Firecracker genesis "
            "run from an infra/ script that constructs GenesisRunner directly and "
            "injects a VMManager-backed Supervisor into it instead."
        )

    runner = GenesisRunner(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, runner.request_stop)
        except NotImplementedError:
            pass

    await runner.boot(resume=not args.no_resume)
    await runner.start_embedded_world_server()
    await runner.start_agents()
    runner.start_watchdog()

    try:
        if args.burst_ticks is not None:
            await runner.run_burst(args.burst_ticks)
        else:
            await runner.run_continuous()
    finally:
        await runner.shutdown()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
