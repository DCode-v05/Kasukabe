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


# Where a trade is worked, for agents who staff no business of their own.
#
# A miner with no employer still has an ore outcrop to walk to, and telling
# it so is the difference between a miner and a resident standing in a
# kitchen all day. Every value is a location string this world already
# mints - see world/tools/locations.py and world/map/expansion.py - never a
# name invented here, which would read as a real place and route nobody.
#
# An occupation missing from this table gets no workplace line rather than a
# guessed one: "your work is at the town hall" told to a barista is worse
# than silence.
WORKPLACE_BY_OCCUPATION: dict[str, str] = {
    "farmer": "Kasukabe Farmland",
    "miner": "Kasukabe Ore Outcrop",
    "forester": "Kasukabe Forest",
    "day_laborer": "Kasukabe Farmland",
    # These four named rooms the map does not draw. A brief that tells a
    # resident its work is at "Kasukabe Grocery" sends it somewhere
    # `list_places` never offers and `move` cannot route to - run 19 left
    # Junia Vance, the grocery clerk, standing at `business:b_grocery`,
    # a room no customer could walk into, for the whole run. Pointed at
    # the walkable places that actually do the job: the town's supply
    # store, and the carpentry where building work happens.
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
    # The trades the twenty-resident cast added, each pointed at the
    # workshop it actually needs. These are the places `craft` and `cook`
    # gate on, and until they were listed by `list_places` no resident
    # could find any of them - so an occupation naming one would have sent
    # its holder somewhere it could not reach.
    "miller": "mill",
    "sawyer": "sawmill",
    "baker": "kitchen",
    "artisan": "carpentry",
    "pharmacist_assistant": "The Willows Market and Pharmacy",
    # Deliberately absent: "child" has no work.
}


logger = logging.getLogger("kasukabe.genesis_runner")

# ── defaults (app/CONVENTIONS.md: "Default dev port: 8100" for world;
# 8200 for broker) ────────────────────────────────────────────────────
#: What a seeded employee is promised a day, before anybody negotiates.
#:
#: Sized against the cost of a day rather than picked round: the head tax took
#: 30 sim a head a day in the last 50-resident run and the grocery asks 12 for
#: bread, so a wage under about 45 buys nothing once the tax is paid. 60 leaves
#: a margin to save or spend, and the smallest till in genesis_50_town (the
#: grocery's 4,000) carries one employee for 66 days.
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
DEFAULT_BROKER_API_KEY = "kasukabe-sim-broker"  # matches src/sim/sim_main.py's own default
DEFAULT_HOME_ROOT = "/tmp/kasukabe-agents"  # matches src/sim/sim_main.py's own default
DEFAULT_SIM_AGENT_PORT_BASE = 9100

# The first tick on which anybody is awake. core/clock.py: TICK_MINUTES is
# 10 and DAY_START_HOUR is 6, so 06:00 is tick 36.
#
# WHY THIS IS A NAMED CONSTANT AND A FLAG. A run starts at midnight, and
# `is_sleeping = not timestamp.is_daytime` short-circuits every resident's
# turn until dawn - so the first 36 ticks of every run this project has
# ever made were the town asleep. At the default 15 seconds per tick that
# is NINE MINUTES of wall clock. Measured, in two independent runs of the
# series: run2's utterances all fall in ticks 36..43 and it ended at 44;
# short1's in 36..56 and it ended at 56. The whole of run 2's conversation
# happened in its last hundred seconds and was then graded on how many
# questions had been answered - and the scheduler wakes each resident
# every three ticks, so a question asked at tick 40 could not have been
# answered before 43.
#
# The reply-rate and exchange-depth gates have therefore never once been
# measured on a town with time to hold a conversation.
DAWN_TICK = 36
# The first tick of the night, by the same arithmetic as DAWN_TICK:
# NIGHT_START_HOUR is 22 and TICK_MINUTES is 10, so 22:00 is tick 132.
# A resident gets exactly this one night tick to wind down (CORE-17).
NIGHT_ONSET_TICK = 132
# How many lines of each engine's output to keep for post-mortems. Bounded
# because 250 engines log for the length of the run - see
# ProcessSupervisor.drain_output.
ENGINE_OUTPUT_TAIL_LINES = 50

# How much of a failing engine's response body to keep. A September
# traceback runs to thousands of characters and this is written once per
# failing agent per tick; the exception type and message are at the front.
ENGINE_ERROR_DETAIL_CHARS = 600

# How many of a failing engine's last printed lines to attach to the warning.
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
# Task prompt: "HARD BUDGET CAP $400" = 40,000 cents, well inside the
# $1000 (100,000 cent) ledger invariant DESIGN.md section 3 fixes.
DEFAULT_BUDGET_CAP_CENTS = 40_000

# How many consecutive ticks where EVERY dispatch failed before the run stops
# itself. Twenty is roughly a game-hour at the default pacing - long enough to
# ride out a broker restart or a throttling burst, short enough that a run
# whose money has run out does not spend another forty minutes discovering it.
BARREN_TICKS_BEFORE_STOP = int(os.environ.get("KASUKABE_BARREN_TICKS", "20"))

# CORE-12 / INFRA-12: model calls per resident turn, before this run has
# measured its own. Run 23 observed 2-12 plus a summariser; five is the
# middle of that and is used only until real `steps_used` arrive.
DEFAULT_CALLS_PER_TURN = 5.0

# CORE-32. The same halt, bounded in seconds as well as ticks.
# Twenty ticks was "roughly a game-hour" at an assumed 15s tick;
# run 23 measured 63s and an all-timeout tick costs 120s, so the
# tick bound alone is between five and forty minutes of paying for
# a town that is not thinking. Twenty minutes is the outer edge of
# what twenty ticks was ever meant to mean.
BARREN_SECONDS_BEFORE_STOP = float(
    os.environ.get("KASUKABE_BARREN_SECONDS", "1200")
)
DEFAULT_GENESIS_PATH = Path(__file__).resolve().parent / "genesis.json"
DEFAULT_ENGINE_REPO = Path(__file__).resolve().parents[2] / "engine"

# Broker states from which no further dispatch can ever succeed. Only an
# explicit halt qualifies: see terminal_reason_from_spend on why anything
# less certain must not stop a run.
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


# ── JSON-safe conversion for checkpoint payloads ───────────────────────
# core/checkpoint.py's established convention (clock.py, scheduler.py) is
# that Checkpointable.snapshot() returns plain, already-JSON-safe data,
# not raw pydantic/dataclass instances - WorldState.snapshot() is the one
# exception (it deepcopies real model instances, since its own restore()
# just setattrs them back for in-process use). This runner needs the
# whole WorldCheckpoint to survive a real json.dumps() (for the local
# checkpoint file and the Supabase archive), so its own adapters convert
# explicitly rather than hoping pydantic's Any-typed serialization does
# the right thing implicitly.


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


# ── config ──────────────────────────────────────────────────────────


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


# ── EVAL-02 / INFRA-16: which events reach the `events` table ───────
#
# Recording every WorldEvent is unbounded in tick count and once produced
# a 21 GB trace store. Recording none of them - which is what every run so
# far did - leaves the table empty, and an empty table reads as a quiet
# town rather than as a missing writer.
#
# INFRA-16 is the same finding from the other end: with `record_event`
# skipped entirely, DESIGN section 2's "complete causal record" had no
# `agent.moved`, `economy.wallet.debited`, `agent.dormant`, `politics.*`
# or `health.*` rows at all, and the report was reading them out of
# in-memory `records` and checkpoints - neither of which survives the
# process. The selective rule below is what makes that claim true without
# bringing back the 21 GB.
#
# The rule: record the domains where something was DECIDED or a resource
# MOVED, and exclude, by name, the high-frequency per-tick events inside
# those domains. The exclusions carry the reason they are excluded, so a
# future reader can weigh the cost rather than guess at it.
RECORDED_EVENT_DOMAINS = frozenset(
    {"economy", "politics", "business", "health", "crime", "broker"}
)

# ENG-24. Types that earn a row despite their domain not being on the
# list. `agent.*` is excluded wholesale because `agent.moved` alone was
# the 21 GB, but goals and beliefs are not movement - each row is a
# resident deciding, finishing or abandoning something, and they are the
# only evidence that the goal substrate did anything at all.
#
# Without this the events would publish and never record, which is
# precisely the CORE-24 split the recorder counters were built to catch.
RECORDED_EVENT_TYPES = frozenset(
    {
        "agent.goal.set",
        "agent.goal.progressed",
        "agent.goal.retired",
        "agent.goal.superseded",
        "agent.belief.updated",
        # B2's pair, recorded for the same reason and never previously
        # reaching the table either: the intentions-set-to-fulfilled ratio
        # is quoted in every run report and was being counted from the
        # session logs rather than from the town.
        "agent.intention.set",
        "agent.intention.dropped",
        # One row per run, and the only one that says what the run was set
        # up to be: which cast, which tick it started on, when the election
        # opened, who was already in office. A score with no situation
        # beside it invites a comparison with a run that began somewhere
        # else entirely, and the manifest on disk is not what a report
        # reads. See `GenesisRunner.record_the_scenario`.
        "system.scenario.set",
    }
)

# Inside an allowed domain, these still do not earn a row.
NOISY_EVENT_TYPES = frozenset(
    {
        # One per resident per tile per tick. The 21 GB.
        "agent.moved",
        # Emitted on every perception build, carries no decision.
        "agent.perceived",
    }
)


# TOOL-18. Which side of a genesis debt an opinion describes.
#
# Two spellings each, because a genesis file is hand-written data and the
# seeder that accepted only "ower" matched none of the four debts in
# genesis_20.json - every one of which says "owing".
DEBT_OWER_ROLES = frozenset({"owing", "ower", "borrower", "debtor"})
DEBT_LENDER_ROLES = frozenset({"owed", "lender", "creditor"})


# WIRE-02. The share of engine->world calls lost to transport that turns
# the end-of-run line from a warning into an error.
#
# One percent. Run `deni-a6f9eb4-5h` lost eight, and a lost call is
# indistinguishable from a refusal to the resident that made it - so a
# run above this line has residents reasoning from "the world would not
# let me" about acts the world never heard of. That contaminates the
# behaviour the run exists to measure, which is a different kind of
# problem from a slow tick.
WIRE_LOSS_ALARM = 0.01


# CORE-24 / the run-24 price mystery. Two tallies of the same stream:
# what reached the event bus, and what reached the `events` table. When
# they disagree the difference names the failure - an event that was never
# published is a world bug, one published and not recorded is a recorder
# bug, and for eleven `post_price` calls in run 24 we could not tell which.
#
# Module level because the app and the runner are built separately and both
# have to reach them. Bounded by the number of event TYPES, not events.
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

    # REAL ENGINES ARE THE ONLY WAY THIS RUNS.
    #
    # Defaults False and is no longer readable from KASUKABE_MOCK_MODE.
    # MockSupervisor + MockAgentGateway fake a turn in-process: ticks flow,
    # metrics move and checkpoints are written while no engine ever starts,
    # which is indistinguishable from a healthy run unless you go looking.
    #
    # Still a field: world/tests/*.py construct GenesisRunnerConfig(
    # mock_mode=True) to drive the tick loop, checkpointing and the spend
    # governor without spawning 314 subprocesses.
    mock_mode: bool = False
    population_seed: int = 2026
    genesis_path: Path = DEFAULT_GENESIS_PATH
    # A scenario says WHEN things happen - see world/scenarios/loader.py.
    # Empty means "no scenario", and the run behaves exactly as it did
    # before scenarios existed.
    checkpoint_dir: Path = Path(DEFAULT_CHECKPOINT_DIR)
    budget_cap_cents: int = DEFAULT_BUDGET_CAP_CENTS
    retrieval_depth_fraction: float = 0.0

    world_mode: str = "embedded"  # "embedded" | "external"
    world_host: str = DEFAULT_WORLD_HOST
    world_port: int = DEFAULT_WORLD_PORT
    world_base_url_override: str = ""

    broker_base_url: str = DEFAULT_BROKER_BASE_URL
    broker_api_key: str = DEFAULT_BROKER_API_KEY

    engine_repo: Path = DEFAULT_ENGINE_REPO
    home_root: str = DEFAULT_HOME_ROOT
    sim_agent_port_base: int = DEFAULT_SIM_AGENT_PORT_BASE

    # "namespace" is the default because it is the only one that fits a
    # laptop: a separate OS process costs ~110 MB per engine (50 agents =
    # 5.5 GB), while a namespaced engine sharing this process costs 2.5 MB
    # (50 agents = ~210 MB, measured with 30 real engines on an 8 GB M1).
    # Isolation is still one SEPTEMBER_HOME per agent - the same directory
    # layout "process" writes, so a run can move between them untouched.
    # "process" is the default because it is the backend that works.
    #
    # "namespace" is cheaper on paper - ~4 MB per engine against ~48 - and
    # world/namespace_supervisor.py is built and tested. It cannot host a
    # real engine: the app venv is Python 3.12.5, whose sqlite3 has no
    # enable_load_extension, and the engine needs it for sqlite-vec. Running
    # engines inside this process therefore needs a host subprocess in the
    # ENGINE's own 3.11 venv, which is not built.
    #
    # It was briefly the default, and a run then booted zero engines while
    # logging "launched 30 agent units". See start_agents below for the
    # count that now makes that impossible.
    supervisor_backend: str = "process"  # "process" | "namespace" | "firecracker"
    # "homes" | "gathering" - where the cast wakes up. See where this is
    # read in apply_genesis_population for why this is a world fact and
    # not an instruction to the agents.
    opening: str = "homes"
    # B1/B2 (The 250 section 33). The silence timeout is deliberately much
    # longer than a tick: an engine mid-inference on a slow model is quiet
    # but perfectly healthy, and restarting it would throw away the turn.
    # How long to wait for freshly-spawned engines to answer GET /health
    # before ticking them. See GenesisRunner.wait_for_engines_ready.
    engine_ready_timeout_s: float = 120.0
    watchdog_poll_interval_s: float = 10.0
    watchdog_silence_timeout_s: float = 180.0
    checkpoint_every_day: bool = True
    max_game_days: int | None = None
    # Stop after this tick (inclusive). Finer than max_game_days, which can
    # only stop at a midnight - and the first election is counted AT a
    # midnight (tick 432), so a day-granular stop ended the run on the very
    # tick of the count and the world went away before anybody - resident,
    # viewer or camera - saw the result (found in the UI rehearsal,
    # 2026-09-22). The election run stops at 480: through the night, which
    # costs next to nothing, to 08:00 with the new government in office.
    max_ticks: int | None = None
    # Skip the night the run opens in. Off by default: the night is real,
    # the learning pipeline runs in it (DESIGN.md section 1), and a run
    # that wants to study it starts at midnight as it always has. This is
    # for a short diagnostic run, where nine minutes of wall clock spent
    # watching ten people sleep is the difference between measuring a
    # conversation and measuring the startup. See DAWN_TICK.
    start_at_dawn: bool = False
    # A scenario file (world/scenario.py): which cast, which tick the world
    # starts on, when it stops, when the election opens and who is already
    # sitting in government. None is every run this project made before
    # scenarios existed. The file is validated when the config is built, so a
    # scenario that could not run is refused before anything boots.
    scenario_path: str | None = None
    # Where a FRESH town's clock starts. 0 keeps `start_at_dawn`'s two
    # choices (0 or DAWN_TICK); anything else is a scenario placing the town
    # at an hour that matters - the point being that the thing a run is
    # measuring should happen inside the hour the budget buys.
    start_tick: int = 0
    # Where a RESUMED run picks the day up, as a tick index, or None for the
    # checkpoint's own tick. `start_at_dawn` places a fresh town; this places
    # a continued one - run 28 stopped at 11:30 and was continued from 18:00.
    # Only forward, and only from a checkpoint: see GenesisRunner.boot.
    resume_at_tick: int | None = None
    # Where per-agent CPU and memory go, or None for not recorded. See
    # GenesisRunner.sample_resources and world/resource_log.py.
    resource_log_path: Path | None = None
    # How long one agent gets to finish a turn before the runner gives up.
    #
    # Was 30s. A turn is not one call: an agent that looks around, walks,
    # harvests and then speaks makes five or six, and calls average 5.8s
    # against this broker - so a resident doing real work ran out of time by
    # arithmetic. Measured on one run: 126 of 367 turns (34%) ended
    # `cancelled` with 0 turns recorded and nothing to show, having already
    # paid for every call they made.
    #
    # 120s is four times the observed mean turn, which leaves room for a
    # long one without letting a wedged engine hold the tick open - the
    # watchdog's silence timeout is 180s, so a genuinely stuck unit is still
    # caught by the layer whose job that is.
    tick_timeout_s: float = 120.0
    # GOV-03. How long the President has to veto an amendment the seats
    # have already carried, before it signs itself.
    #
    # 0 is the default because the alternative is that an unattended run
    # can never amend its own charter - the operator is the only signature
    # and the operator is not there. A hard gate makes "what does a town do
    # with its own constitution" a question about the operator's
    # availability rather than about the town. Set it high to wait for a
    # human; set it negative to disable auto-signing entirely and keep the
    # old behaviour.
    veto_window_ticks: int = 0
    scheduler_target_fraction: float = 0.3
    # Seconds of wall clock between ticks. 0 means "derive one from what
    # the broker can actually serve" (see resolve_tick_interval); a
    # positive value is an explicit operator override and is obeyed even
    # if it is too fast, with a warning.
    #
    # This is THE request-rate control. Requests per minute is
    #     agents * scheduler_target_fraction * (60 / tick_interval_s)
    # so at 250 agents and the default 0.3 fraction, a 1s tick asks for
    # 4500 RPM against a broker that can serve 200. Before this existed
    # the interval was 0 - no pacing at all - and the town simply
    # outran its own broker.
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
            # CORE-27. Accepted, and cannot work.
            #
            # `--world-mode external` skips the embedded server and points
            # every engine at a separate host - which is `world/main.py`,
            # and `main.py` does not serve the engine contract: no
            # `/tools/registry`, no `/ledger/payer`, no `/ledger/debit`.
            # So the flag booted a run whose engines could not fetch a tool
            # or spend a cent, and it failed at the first dispatch rather
            # than at validation, after the supervisor had started N
            # processes.
            #
            # Refused here, loudly, until that host serves the contract.
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
        # CORE-20: refused HERE, not after paying to find out.
        #
        # `NamespaceAgentGateway` defines `tick(agent_id, perception_text)`
        # and the runner calls `send_tick(agent_id, tick=, is_sleeping=,
        # perception=, call_budget=)`, so the first due agent raises
        # AttributeError. The existing refusal only fires when NO unit
        # boots at all - a backend that half-boots reaches dispatch and
        # dies there, after genesis, after the engines were spawned.
        #
        # `--supervisor-backend firecracker` is already refused by the
        # runner for the same class of reason (the kernel was never
        # compiled and the boot-args contract has never been tried); this
        # ENG-11 as well as CORE-20. The review's fix was "give
        # NamespaceAgentGateway.send_tick the same signature and route
        # through agent_adapter's logic"; refusing the backend is the
        # honest version of that. Its supervisor cannot host an engine at
        # all, so a corrected signature would forward to a tick that has
        # no sleep gate, no learning gate and no call budget - three
        # silent behaviour changes rather than one loud refusal.
        # puts the namespace backend on the same footing rather than
        # leaving one dead backend refused early and the other late.
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

        # KASUKABE_MOCK_MODE is deliberately NOT read - see mock_mode above.
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
            # The broker and the runner had SEPARATE caps that never spoke.
            #
            # The broker refuses dispatch at SPEND_CAP_USD; this governor
            # stops the world at budget_cap_cents, which defaulted to $400.
            # Set the broker to $40 and the run does not stop at $40 - it
            # keeps ticking against a broker that refuses every call. Measured:
            # 10,465 SpendCapExceededError over 44 minutes, 34% of the run's
            # calls, all of them after the money was gone.
            #
            # CORE-25: a reserve BELOW the broker's cap, not equal to it.
            # Making the two numbers equal fixed the first bug and left a
            # smaller one behind. Two counters race - the broker counts
            # what it dispatched, the ledger counts what was burned - and
            # the broker's is the one that gets there first. The observed
            # ending is always the same: the broker halts, every engine
            # turn 500s, one whole barren tick passes, and only then does
            # the probe stop the run. Every one of those refusals is still
            # a round trip and still a trace row.
            #
            # A run that stops itself a few cents early stops CLEANLY, and
            # the drain (CORE-05) gets a broker that will still answer.
            # KASUKABE_BUDGET_CAP_CENTS still wins when set, so a run that
            # deliberately wants the two equal can still say so.
            try:
                broker_cap = int(round(float(raw) * 100))
                kwargs["budget_cap_cents"] = max(1, broker_cap - halt_reserve_cents(broker_cap))
            except ValueError:
                pass  # a malformed cap is the broker's error to report
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
        # KASUKABE_SCENARIO decides the same things --scenario does. Folded
        # here so a run started from the environment alone (the launcher
        # does exactly that) is shaped by its scenario too.
        if config.scenario_path:
            config = apply_scenario(config, config.scenario_path)
        return config


# ── Supervisor: process supervision shaped to match
# infra/firecracker/supervisor.py's protocol (see module docstring point 3
# for why this is a parallel definition, not an import) ────────────────


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

    # B1/B2 (The 250 section 33). That section says both Supervisor protocols
    # "already define... a restart(unit_id) method" - true of
    # infra/firecracker/supervisor.py, but this one never declared it, and
    # neither backend below implemented it. The watchdog cannot restart what
    # the backend cannot restart, so the protocol the runner actually boots
    # on grows the method here.
    async def restart(self, unit_id: str) -> UnitStatus: ...

    async def status(self, unit_id: str) -> UnitStatus: ...

    async def list(self) -> list[UnitStatus]: ...

    async def health(self, unit_id: str) -> bool: ...

    async def shutdown_all(self, timeout_s: float = 30.0) -> None: ...


# ── The watchdog (The 250 section 33), local to this file for the same
# reason the Supervisor protocol above is: infra/README.md states plainly
# that infra/ is "Never imported by world/, broker/, game/, or research/",
# and module docstring point 3 already explains why this file duplicates a
# structurally-identical shape rather than reaching across that line. The
# canonical implementation lives in infra/firecracker/supervisor.py (B1) and
# drives the Firecracker backend; this one drives the process backend that
# actually boots today. world/tests/test_track_wiring.py asserts the two make
# the SAME decision on the same input, so a deliberate duplicate cannot
# quietly become two different watchdogs.


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
            return None                      # stopped on purpose
        if status.last_health_check_at is None:
            return None                      # never health-checked yet
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

        # CORE-18. Ask before judging.
        #
        # `needs_restart`'s silence arm - the ONLY arm that can catch an
        # engine that is alive and no longer answering, which is the case a
        # watchdog exists for - reads `last_health_check_at`, and nothing
        # ever wrote it. `ProcessSupervisor.start` sets it to None, `status`
        # does not touch it, and `health()` had no caller anywhere. So the
        # field was None for the whole life of every unit and the arm was
        # unreachable: only a process that actually EXITED was ever
        # restarted, which is the half that needs a watchdog least.
        #
        # Probed here rather than inside `status()` because a status read
        # happens on every route and every list; a probe is a round trip
        # per unit and belongs on the watchdog's own cadence.
        for status in statuses:
            if status.state is not UnitState.RUNNING:
                continue
            try:
                await self.supervisor.health(status.unit_id)
            except Exception as exc:  # noqa: BLE001 - see this method's own
                # docstring: a watchdog that dies on a backend hiccup is
                # worse than none. A failed probe is silence, which the
                # timeout below is exactly the right way to judge.
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
    # A venv keeps its interpreter at a different path per platform:
    # POSIX .venv/bin/python, Windows .venv/Scripts/python.exe. Probing
    # only the POSIX one meant a Windows run silently ignored the engine
    # venv sitting right there and fell through to the "python3" fallback
    # - a name that usually resolves to nothing on Windows (or to the
    # Store shim, which is worse) - so every unit died with
    # FileNotFoundError before executing a line of engine code.
    # THIS platform's layout only - never "whichever of the two exists".
    # engine/ is bind-mounted into the Linux world container by
    # start-genesis.sh, so a .venv built on a Windows host is visible
    # inside that container: probing both layouts would find
    # .venv/Scripts/python.exe there and try to exec a Windows binary on
    # Linux. A venv is not portable across operating systems, so one that
    # does not match the running OS must be ignored, not preferred.
    if os.name == "nt":
        venv_python = engine_repo / ".venv" / "Scripts" / "python.exe"
        fallback = sys.executable  # "python3" is usually not a PATH name on Windows
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
        # CORE-18. Short on purpose: `health()` is a liveness probe on the
        # watchdog's cadence, not a request. A slow answer is a wedged
        # engine, which is the thing being looked for.
        self.health_timeout_s = health_timeout_s
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.statuses: dict[str, UnitStatus] = {}
        # See drain_output: every unit gets a reader task, and a bounded
        # tail of its output kept for post-mortems.
        self.output_tails: dict[str, deque[str]] = {}
        self.drain_tasks: dict[str, asyncio.Task[None]] = {}
        # The spec each unit was started from, kept so restart() can bring
        # the SAME unit back rather than needing the caller to hand the spec
        # in again - the protocol's promise that "unit_id is the stable
        # identity across restarts" only holds if the backend remembers it.
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
                if not line:  # EOF: the process closed its end
                    break
                tail.append(line.decode("utf-8", errors="replace").rstrip())
            # EOF means the engine is finished or dead. REAP IT: until the
            # child is waited on, process.returncode stays None, and
            # status() only downgrades a unit to FAILED when returncode is
            # not None. Without this a crashed engine reports RUNNING
            # forever - so health() lies, and the watchdog whose entire job
            # is restarting a unit "the instant it goes FAILED" never sees
            # one fail. Observed directly: an engine that died two seconds
            # in still reported alive=True twenty-five seconds later.
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

        # Before the grace sleep, deliberately: the engine starts logging
        # the instant it execs, and this is the window the pipe would fill in.
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
        # Clear the live handle so start()'s already-running guard cannot
        # fire on the corpse of the process we just reaped.
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
            # No port to ask. Fall back to the old answer rather than
            # reporting a silence this supervisor cannot actually observe.
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
        # Every restart this backend was asked for, so a test can assert the
        # watchdog actually acted rather than inferring it from state.
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


# ── agent gateway: how one due agent's turn actually gets executed and
# costed (module docstring point 5) ────────────────────────────────────


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
    # B3: what the turn used, and whether it ended because it ran out of
    # thinking rather than because it was finished. The world computes the
    # budget (it owns the wallet); the engine reports back what happened.
    steps_used: int = 0
    budget_hit: bool = False
    # ENG-24. Whether this resident thought about its goals tonight, and
    # how many it actually changed. Reported by the engine, never
    # inferred here - "the reflection ran" and "the reflection did
    # something" are different claims and a run report needs both.
    reflected: bool = False
    goal_revisions: int = 0
    # CORE-11. The engine refused this tick because it is still running the
    # previous one. Distinct from `succeeded=False`, and the distinction is
    # the whole finding: a refusal costs nothing, means nothing is wrong,
    # and must not be counted as an engine failure - but it also must not
    # be counted as a turn that happened.
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
        # Run 26: three engines answered /tick with a bare 500, and the body
        # was uvicorn's "Internal Server Error" - no traceback, because the
        # traceback went to the ENGINE's stdout, which the supervisor keeps
        # in a bounded tail and nothing read at the moment of failure. Two of
        # the three are unexplained for exactly that reason.
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
                    # ENG-24: which night this is, so the sleep reflection
                    # runs once per resident per NIGHT and not once per
                    # sleeping tick. The engine falls back to tick // 144
                    # if this is absent; sending it means the world's
                    # clock stays the only clock.
                    "game_day": game_day,
                },
            )
            # CORE-11: 409 means the engine is still running the previous
            # turn and has REFUSED this one rather than queueing it behind
            # its own `_turn_lock`. Not an error - the correct answer to a
            # question that should not have been asked - so it is read
            # before `raise_for_status` turns it into one.
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
            # The class, the message, AND the engine's own answer. See
            # engine_failure_detail: a 500 arrives with the reason in its
            # body and this line used to keep only the sentence about the
            # status code.
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
                # engine_failure_detail, not str(exc): run 28 logged nine of
                # these with a blank reason, because httpx timeout and
                # transport errors carry no message and the class was dropped.
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
        output_tokens = 291  # measured baseline, task prompt's "MEASURED FACTS"
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


# ── checkpoint adapters ─────────────────────────────────────────────


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


# ── WorldState checkpoint field classification (STATUS.md risk #3) ─────
#
# This used to be two hand-maintained dicts (WORLD_STATE_DICT_MODEL_TYPES /
# WORLD_STATE_LIST_MODEL_TYPES) - a second, parallel listing of "which
# owned field holds which pydantic model" that had already drifted once
# from world/tools/state.py's own OWNED_STATE_FIELDS tuple: "subpoenas"
# and "tool_invocations" were added there and silently missing from both
# dicts here, so WorldStateCheckpointAdapter.snapshot() dropped them from
# every checkpoint and .restore() left them at build_world()'s fresh empty
# defaults, with no error raised anywhere (STATUS.md risk #3's own
# writeup; the fix landed inline in those two dicts first, then was
# replaced with this derivation so the same class of bug cannot recur).
#
# classify_world_state_fields() below is the structural fix: it derives
# the same "dict of models / list of models / plain" classification by
# inspecting WorldState's own real type hints instead of hand-copying
# them, so a future field appended to OWNED_STATE_FIELDS is automatically
# covered by this adapter with zero changes here - there is no second
# list left to forget to update.


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

# Default-value factory per owned field (sourced from WorldState's own
# dataclass field defaults, not re-declared here), used as the fallback
# for a "plain" field missing from an older checkpoint - see restore()'s
# own comment for why dict_model/list_model fields can hardcode {}/[]
# instead but a future non-dict "plain" field cannot.
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
            else:  # "plain"
                data[name] = to_jsonable(value)
        data["inventory"] = {"balances": to_jsonable(raw["inventory"].balances)}
        return data

    def restore(self, data: dict[str, Any]) -> None:
        rebuilt: dict[str, Any] = {}
        for name, (shape, model_type) in WORLD_STATE_FIELD_TYPES.items():
            # .get(...)-style tolerance for a checkpoint written before a
            # field existed - RunnerCountersCheckpointAdapter.restore()'s
            # established legacy-checkpoint pattern, reused here.
            if shape == "dict_model":
                rebuilt[name] = {k: model_type.model_validate(v) for k, v in data.get(name, {}).items()}
            elif shape == "list_model":
                rebuilt[name] = [model_type.model_validate(v) for v in data.get(name, [])]
            else:  # "plain"
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
            # JSON turns int keys into strings; restore puts them back.
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
        # Direct access, not a getattr default: `movement` is a declared
        # field on WorldState (state.py:390) and a default here would
        # silently answer "no movement system" if it were ever renamed -
        # which would put GROW-05's two-map bug back on the resume path
        # with nothing failing.
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
        # Tolerant of a checkpoint written before this adapter existed -
        # RunnerCountersCheckpointAdapter's established legacy pattern.
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
    # A single mock cognition turn costs a fraction of a cent (measured
    # baseline $0.001764, ceiling ~$0.0023 at max retrieval depth - see
    # RetrievalDepthConfig.estimated_cost_per_call_usd) and the ledger only
    # ever holds integer cents (app/CONVENTIONS.md: "Money is always an
    # integer count of cents ... never a float"). Rounding each call to the
    # nearest cent before burning it would round every single call to zero
    # and the mock dry run would never spend a cent no matter how long it
    # ran - GenesisRunner.settle_outcome_cost accumulates the true
    # fractional USD here and only burns a whole cent once the running
    # total crosses one, carrying the remainder forward exactly like a
    # real accounting system's sub-cent rounding reserve. Checkpointed so a
    # resumed dry run does not lose or double-count partial cents.
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
        # random.Random.getstate() returns a 3-tuple - (version, a 625-int
        # internal Mersenne Twister state tuple, gauss_next) - confirmed by
        # inspection (`random.Random().getstate()` has len 3, not 2) rather
        # than assumed; setstate() requires exactly that shape back,
        # version included, or it raises. to_jsonable()/json round-trip
        # turns every tuple into a list, so this reconstructs the version
        # int and the inner state tuple explicitly rather than passing the
        # list through - the previous 2-value unpack here
        # (`twister_state, gauss_next = raw_rng_state`) raised
        # "ValueError: too many values to unpack" the first time any run
        # actually reached a day-boundary checkpoint with a real rng_state
        # on it (world/tests/test_run_genesis.py's
        # TestRunnerCountersCheckpointAdapter reproduces this directly).
        if raw_rng_state is not None:
            version, twister_state, gauss_next = raw_rng_state
            self.counters.rng_state = (version, tuple(twister_state), gauss_next)
        else:
            self.counters.rng_state = None
        self.counters.retrieval_depth_fraction = data["retrieval_depth_fraction"]
        self.counters.last_life_hour_processed = data["last_life_hour_processed"]
        self.counters.last_life_day_processed = data["last_life_day_processed"]
        # get() with a 0.0 default: tolerates resuming from a checkpoint
        # written before this field existed rather than raising KeyError.
        self.counters.mock_cost_remainder_usd = data.get("mock_cost_remainder_usd", 0.0)


# ── local checkpoint store (resume's source of truth) ──────────────────


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
        # write -> fsync(file) -> rename -> fsync(dir): the temp-file-then-
        # rename dance alone (the prior version of this method) is atomic
        # w.r.t. *visibility* - a reader never observes a half-written
        # file - but atomicity is not durability. DESIGN.md section 9's own
        # words for this host are "spot ... can be reclaimed at any time":
        # an instance reclaim (or any hard power-loss) can happen the
        # instant after rename() returns, before the kernel has flushed
        # either the file's data blocks or the directory's updated entry
        # to physical disk, and POSIX gives no ordering guarantee between
        # "rename() returned" and "those bytes survive a power cut" without
        # an explicit fsync. Both fsyncs below close that gap: the first
        # guarantees the checkpoint's actual bytes are on disk before it is
        # ever linked in under its real name; the second guarantees the
        # rename itself (the directory-entry update) is durable too, so a
        # reclaim right after this method returns can never resurrect the
        # pre-checkpoint directory listing with the new file's bytes
        # missing. This is the same reason `sync_dir` exists in every
        # battle-tested atomic-file-write recipe (PostgreSQL's fsync.c,
        # SQLite's os_unix.c, Python's own `tempfile` docs on durability)
        # - fsync-then-rename-then-fsync-the-dir, not rename alone.
        # O_BINARY matters and only exists on Windows, where a descriptor
        # opened without it is in TEXT mode and silently rewrites every
        # 0x0A byte to 0x0D 0x0A on the way out. `compressed` is gzip
        # bytes, so that corrupts the checkpoint: it writes without error
        # and fails later at read time with a bare "zlib.error: Error -3
        # while decompressing data", pointing at the reader instead of the
        # writer that actually broke it. getattr(..., 0) because the flag
        # is absent on POSIX, where the distinction does not exist.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        fd = os.open(tmp_path, flags, 0o644)
        try:
            os.write(fd, compressed)
            os.fsync(fd)
        finally:
            os.close(fd)
        tmp_path.replace(path)  # atomic on POSIX: a crash mid-write never leaves a half file
        # The directory fsync is POSIX-only. Windows has no file descriptor
        # for a directory at all, so os.open(dir, O_RDONLY) there raises
        # PermissionError unconditionally - not intermittently, not fixable
        # by permissions - and this whole method used to die on it, taking
        # every checkpoint write with it.
        #
        # The guard is an explicit os.name check rather than a try/except
        # on purpose: on POSIX a failing directory fsync must still raise
        # loudly, because a silently skipped fsync is precisely the bug the
        # rest of this method exists to prevent. Swallowing OSError would
        # "fix" Windows by quietly downgrading durability everywhere.
        #
        # What Windows gives up is the durability of the RENAME, not of the
        # data: the file fsync above still runs, so a reclaim can never
        # produce a half-written checkpoint - at worst the directory entry
        # for a fully-written one is lost. That trade is acceptable because
        # the real run is Linux (DESIGN.md section 9's host, and
        # start-genesis.sh runs the runner in a Linux container); Windows is
        # a development platform here, never the platform of record.
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

        # INFRA-01. `client` is the runner's own trace-store client, and
        # passing it is the whole fix.
        #
        # `get_client()` with no argument falls through to
        # `SupabaseConfig.from_env()`, whose `sqlite_path` defaults to
        # ":memory:". So every `world_snapshots` row and every checkpoint
        # archive on every real run went into a throwaway database that was
        # discarded at process exit - while the line below logged
        # "checkpoint written" with a path, because the LOCAL write (which
        # is the authoritative one) had succeeded. Nothing ever failed.
        #
        # The fallback stays, for the callers that legitimately have no
        # store, but it now says so rather than pretending.
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
            raw = local_path.read_bytes()  # already-gzipped bytes from LocalCheckpointStore
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
            # Only close what we opened. The runner's client is shared with
            # the recorder and every other writer for the rest of the run;
            # closing it here would take the whole trace store down at the
            # first day boundary.
            if owns_client:
                await client.close()
    except Exception as exc:  # noqa: BLE001 - best-effort mirror, see docstring
        logger.warning("shared checkpoint mirror failed for tick %s: %s", checkpoint.tick_index, exc)


# ── live metrics ────────────────────────────────────────────────────


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
        # CORE-25: what the run is allowed to spend, so `/metrics` can say
        # how close it is. `spend_usd_cents` alone is a number with no
        # scale - the operator had to know the cap from somewhere else to
        # read it, and on every run so far that "somewhere else" was a
        # different number than the one actually in force.
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
            # CORE-25. The cap this run stops at, and how far through it
            # the town is. `None` when the metrics object was built without
            # one, which is honest: an unknown cap is not an infinite cap
            # and must not render as 0% used.
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


# CORE-25. How far below the broker's cap the world stops itself.
#
# It has to cover the priciest single tick, because spend is only observed
# between ticks and a run cannot stop inside one. That number is measured
# during a run (`max_tick_spend_cents`) but unknown when the config is
# parsed, so this is a proportion with a floor: 5% of the cap, at least 5
# cents. On run 24's $2.50 cap that is 12 cents - about two turns - and on
# a $400 cap it is $20.
#
# Deliberately NOT larger. Reserve is budget the run does not get to
# spend, and the point is a clean stop, not a safety margin.
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


# What only the RUNNING world knows, and what the report asks it for. The
# trace holds the events, the calls, the speech and the skills; it holds no
# roster, no refused intentions and no list of which tools were being
# served, because those live in the world's memory and die with it.
# How often a run reads itself WHILE it is going, in ticks. 144 is one
# game-day. A run that is killed rather than stopped never reaches
# `shutdown()`, and killed is how runs end: a Ctrl-C at the wrong moment, an
# OOM, a closed laptop. Without this, such a run leaves no roster, no
# refusals and no record of which tools it was serving - the three things no
# table holds. 0 switches it off.
READING_EVERY_TICKS = max(0, int(os.environ.get("KASUKABE_READING_EVERY_TICKS", "144") or 0))

# SG7. Snapshot the engine homes with each periodic reading, so a killed
# run keeps its residents' session logs and memories. 0 switches it off.
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
            # A reading from tick 3 of a run that reached 194 is a different
            # claim from one taken at the end, and the reader must be able
            # to tell which it is holding.
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
                parts = Path(member.name).parts[1:]      # drop the archive's own root
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


# ── embedded world HTTP surface (module docstring point 2) ────────────


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
    # WIRE-02. Passed in the same shape as `engine_liveness` above, and for
    # the same reason: the factory has no runner, and `/world/link` used to
    # close over a bare `runner` name that exists in no scope here. That is
    # a NameError on every request, and because nothing in the suite called
    # the ROUTE - only `collect_wire_health` directly - it shipped and only
    # run 25 found it, with a 500 where the wire numbers should have been.
    wire_health: Callable[[], Awaitable[dict[str, int]]] | None = None,
    # The recorder's own counters, for `/world/preflight`'s "has lost
    # nothing" check. Passed in for the same reason `wire_health` is: this
    # factory is a function, not a method. That route read
    # `self.trace_store.stats()` - there is no `self` here - so since
    # 175f87e1 every call to `/world/preflight` on an embedded world raised
    # NameError and returned 500. Run 27's first report refresh logged it.
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
        # UI-16. None on a host with no governor - a fixture, or main.py -
        # so the console can say "unknown" instead of drawing its bar
        # against a number nobody set.
        resolved_get_run_cap = run_cap_usd_cents_getter

        def build_deps() -> AdminDeps:
            return AdminDeps(
                world=world_state,
                tick_engine=tick_engine,
                # GROW-06/GROW-11. Was hard-coded None, which is why
                # `approve_land_petition` logged "decision recorded, no
                # physical map growth applied" in every run ever made.
                map_service=map_service,
                extras=resolved_extras,
                get_population_dials=resolved_get_dials,
                set_population_dials=resolved_set_dials,
                # UI-16: the run's real cap, so the console stops drawing
                # its spend bar against a hard-coded $400.
                get_run_cap_usd_cents=resolved_get_run_cap,
            )

        install_admin_exception_handlers(app)
        app.include_router(build_admin_router(build_deps))

    # The live run boots THIS app, not world/main.py. Mounting the viewer
    # routers here is what makes /agents, /places and /world/state real
    # instead of 404 -> game server mock fallback.
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
        # GOV-19 rides here too: `social_api` was mounted only by
        # `main.py`, which no run uses, so DESIGN section 8's
        # scenario/special-event feed was unreachable on the only host
        # that boots. Same class of gap as the rest of this block and
        # closed the same way.
        #
        # B3/B5/B6 read paths. world/main.py mounts these too; this host has
        # to as well or the console's Town tab 404s against a real genesis
        # run - which is the ONLY host that boots in production. The
        # /businesses/pnl routes (B4) ride along on the agents router above.
        # UI-02 / CORE-26. DESIGN section 8's research viewers - the
        # reasoning viewer the docs call the research centrepiece, and the
        # scenario/conversation feed - mounted only on `main.py`, the host
        # no run uses. So `agents.html`'s three tabs and `views.html`'s
        # Scenarios pane have 404'd against every run this project has
        # done, and `run_report.py` already carries a note about the 404.
        #
        # The reasoning viewer needs `llm_calls`, which the broker writes
        # into the trace store this runner already opens - so the data was
        # there the whole time and only the mount was missing.
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
        # Part E: /workshop and /council. Same deps shape as the routers
        # above - both read live world state, neither owns any.
        app.include_router(
            build_workshop_router(lambda: WorkshopDeps(world=world_state, genesis=genesis))
        )

    # Cached because a broker cannot change mode without restarting, and
    # /health is polled every 2s by the viewer. Only SUCCESSES are cached:
    # a failed probe (broker still booting) must be retried, not frozen
    # into a permanent "cannot say".
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
        # UI-22: cross-origin, like the read-only routers around it.
        #
        # `views.html` and `agents.html` are served from the game server on
        # :8400 and read the world on :8100, and `/health` is the FIRST
        # call they make - the reachability check. Without this header
        # that check fails and the page reports "could not reach world
        # API", which reads as a dead world rather than a missing header.
        response.headers["Access-Control-Allow-Origin"] = "*"
        # `cognition` is the ONE thing only this host can answer: whether
        # the agents in this world are backed by real September engine
        # subprocesses or by MockAgentGateway. The runner owns that choice
        # (GenesisRunnerConfig.mock_mode) and builds this app, so this is
        # the only place downstream tooling can learn it. game/ surfaces it
        # to the viewer's badge - without it a map bound to a real world
        # server still cannot tell a thinking town from a faked one.
        return {
            "status": "ok",
            "service": "world",
            "mode": "genesis-embedded",
            "cognition": cognition,
            # "bedrock" | "mock" | None (broker did not say). Distinct from
            # `cognition`: real engines can be fed canned completions.
            "inference": await probe_inference(),
        }

    @app.get("/tools/registry")
    async def tools_registry(request: Request, agent_id: str | None = None) -> Response:
        # Shared with world/main.py's route - see tool_registry_rows.
        #
        # B3's companion setting: an engine that names itself gets full
        # schemas only for the tools it could call from where it is
        # standing, and a one-line index for the rest. A fresh context is
        # about 8,000 tokens before a word of perception and most of that
        # is verbs for somewhere else. Anonymous callers - the observatory,
        # a boot before anybody has a location - get the whole thing.
        #
        # ENG-19: answers a conditional GET.
        #
        # `refresh_world_tool_registry(force=True)` pulled all 53 KB and
        # rebuilt 76 ToolDefs every tick per engine, because the version
        # route cannot answer this question: schemas are served BY
        # LOCATION, so the payload changes when the resident walks and the
        # world's version counter does not move.
        #
        # THIS route is the one every real run serves - `world/main.py`'s
        # is the standalone server - so without this the saving would have
        # existed only in a host no simulation uses. A digest of the body,
        # not a (version, location, seat) tuple: those three are the only
        # inputs `tool_registry_rows` has, so a tuple is a hand-maintained
        # restatement of the body whose failure mode is a resident stuck on
        # a schema the town no longer serves.
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

        # Built from what this app actually has in scope plus the environment
        # the run was started with. The alternative - threading the whole
        # config through build_embedded_world_app - would change a signature
        # six call sites share for one route's benefit.
        pf = preflight_run(
            broker_url=broker_base_url or os.environ.get("BROKER_BASE_URL", ""),
            world_url=None,  # already inside the world; the three live checks
                             # below are answered directly instead
            expected_agents=len(world_state.agents),
            genesis_path=os.environ.get("KASUKABE_GENESIS_PATH", "world/genesis.json"),
            home_root=os.environ.get("KASUKABE_HOME_ROOT", "~/.kasukabe"),
            trace_db=os.environ.get("TRACE_SQLITE_PATH", "data/kasukabe.db"),
            engine_repo=os.environ.get("KASUKABE_ENGINE_REPO", "../engine"),
            # This world's own engines are holding those ports. Of course
            # they are - they are the run this route is reporting on.
            ports_must_be_free=False,
            # ...and they have had time to write a session log, which before
            # they boot they cannot have done.
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
        # `14`.7. The recorder's drop counter blocks, rather than stopping
        # the run three failures later.
        #
        # A recorder that has already lost a row does not deserve three more
        # chances: run 16 dropped 125 records - one resident's entire
        # existence - and the run finished, reported, and was graded, with a
        # WARNING buried in a 4,000-line log as the only sign. Whatever the
        # town does after the first drop is being measured against a record
        # that is already known to be incomplete.
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

        # ── health ──────────────────────────────────────────────────────
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

        # ── what the town has actually built ────────────────────────────
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

        # ── food: what is actually held, by whom ────────────────────────
        # The Inventory is the town's real stock. Edible goods only, because
        # "the town holds 40 timber" is not a food figure.
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

        # ── treasury: the ledger's own figure ───────────────────────────
        treasury_sim = None
        try:
            from world.ledger.money import TREASURY_ACCOUNT_ID

            treasury_sim = world_state.ledger.sim_balance(TREASURY_ACCOUNT_ID)
        except Exception:  # noqa: BLE001
            treasury_sim = None

        # The town's real money, from the ledger - not a client-side model.
        # `hudWealth` was computed in the browser from build-time balances and
        # showed NaN once the cast changed under it; `hudTreasury` came from a
        # fiscal projection off the mock clock and went negative, which the
        # ledger never does.
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
            # CORE-28: everyone the remark named, not just the primary
            # listener. Capped, because a caption is a caption.
            named = [name for name in (row.get("to_names") or []) if name]
            if not named and row.get("to_name"):
                named = [row["to_name"]]
            if len(named) > 3:
                named = named[:3] + [f"and {len(row['to_names']) - 3} more"]
            # "speaking to the room" read as though "the room" were a
            # person somebody was in conversation with.
            return f"talking to {', '.join(named)}" if named else "speaking to everyone here"

        tool = row.get("tool") or "?"
        p = row.get("params") or {}

        def n(key: str) -> str:
            v = p.get(key)
            return "" if v in (None, "") else f"{v} "

        # Every name and every parameter key below was read off this world's
        # own /tools/registry. A tool name invented here would read as a real
        # activity on the card and never once fire.
        if tool == "say":
            return "speaking"
        if tool in ("skillify", "revise_skill"):
            return f"writing down a skill: {p.get('name') or '...'}"
        if tool in ("toolify", "revise_tool", "define_tool",
                    "define_business_tool", "define_organization_tool"):
            return f"inventing a tool: {p.get('name') or '...'}"
        if tool == "move":
            # Where they ARE, not where they asked to go.
            #
            # The requested destination is what the model typed, and `move`
            # is permissive about it: an agent that asked for "loc_college"
            # and ended up at Oak Hill College was reported as "walking to
            # loc_college" - a place that does not exist, presented as its
            # current activity, on the map and the resident card.
            #
            # The world's own location for that agent is authoritative and
            # cannot disagree with itself.
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
        # Everything else: the verb is the tool's own name, unglamorised.
        # `register_business` reads perfectly well as "register business"
        # and claims exactly as much as the record supports.
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
                # A2A: what the conversation settled - every deal agreed in
                # it, and (once closed) the summary. See
                # WorldState.record_conversation_deal / conversation_outcome.
                "deals": list(record.get("deals") or []),
                "outcome": record.get("outcome") or conversation_outcome(record),
                "lines": [
                    {
                        "tick": line.get("tick"),
                        "said_at": line.get("said_at"),
                        "speaker_agent_id": line.get("speaker_agent_id"),
                        # Resolved here, exactly as /world/utterances does
                        # it. The stored record's own name fields are
                        # filled by tools/bootstrap from world.agent_names,
                        # which the running world does not populate - so
                        # trusting them put "n06" and "n09" in a panel
                        # whose entire job is showing who is talking to
                        # whom. The roster beside them read "Wren Thorne",
                        # from this same map, which is how it was spotted.
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
        # Most recently spoken in first: a live run's reader wants the
        # conversation happening now, not the one that opened first.
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
            # The whole point: types that reached the bus and not the table.
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
            # A host that wired no engines (world/main.py, and the tests
            # that build this app standalone) has no link to report on.
            # Answered explicitly rather than 500ing, so "no engines" and
            # "the instrument is broken" stay distinguishable.
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
                    # PROG-01. Served, because `run_report.programs_section`
                    # reads them from here and a reader keyed to a field
                    # this dict does not carry is EVAL-01 - the shape that
                    # printed eleven real price posts as `| None | None |`.
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
                # The parameters are what makes the line readable - "bought
                # 3 bread" rather than "called buy" - but they are agent-
                # authored text on an agent-authored tool, so the viewer
                # must render them as data. Truncated here so one enormous
                # param cannot dominate the payload.
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
                # CORE-28. A remark can name several people;
                # `listener_agent_id` holds one of them. 172 of run 23's 229
                # utterances carried more, and every one of them rendered as
                # "speaking to everyone here" - a broadcast to the room, when
                # it was aimed at named residents.
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
                # One line, already phrased. The viewer should not have to
                # know the shape of a tool record to caption a sprite.
                "doing": _phrase_activity(latest),
                "at_tick": (latest or {}).get("tick"),
                "recent": rows[-per_agent:],
            }

        # A resident who has done nothing yet is still a resident. Saying so
        # explicitly beats a missing key the viewer reads as a broken feed.
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
            # Per-agent proficiency, so a character card can show what this
            # resident actually knows rather than what the town knows.
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
        # Mirrors world/main.py's route - see there for why it is separate.
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
            # Every unit's recent stdout, not just the dead ones.
            #
            # The supervisor drains each engine's output into a bounded tail
            # for exactly this, and it was only readable for units that had
            # already failed - so a HEALTHY engine misbehaving (tools missing
            # from its requests, say) left nothing to read anywhere.
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
            # The half of a refusal that is worth a turn. See
            # ToolError.context - skillify refused six steps with "no tool
            # named 'world_move'" and the author's next attempt was as
            # blind as its first.
            if getattr(exc, "context", None):
                detail["context"] = names.name_the_numbers(world_state, exc.context)
            # Whatever a refusal's help carries, the refusal itself must
            # arrive: anything JSON cannot hold is written as text rather
            # than turning the 400 a resident can act on into a 500 it
            # cannot. See `registry.location_label` for the case that did.
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
                continue                      # dream probes can never land here
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
            # The same answer the debit route gets, so the check before a
            # call and the charge after it cannot disagree about who pays
            # (`Ledger.payer_for`). Without the purpose a broke resident's
            # learning was refused here, before the fallback could run.
            payer_id = ledger.payer_for(agent_id, amount_cents, purpose)
            account = ledger.get_account(agent_id)
        except (AccountNotFoundError, ParentNotConfiguredError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "agent_id": agent_id,
            "payer_agent_id": payer_id,
            "balance_cents": ledger.usd_balance(payer_id),
            "is_minor": account.is_minor,
            # INFRA-02. The broker has no clock of its own, so every
            # `llm_calls` row was written with tick NULL - and `game_day` is
            # derived from tick, so that was NULL too. C1 (calls per
            # resident-day) could not be computed at all, and the Parquet
            # export of calls came out empty by construction because it
            # partitions on game_day.
            #
            # This route is already called once before every model call, to
            # find out who pays. Carrying the tick on the same response
            # costs nothing and is the only place in the system where the
            # broker and the world clock meet. See broker/trace.py's own
            # note, which specifies exactly this.
            "tick": world_state.current_tick,
        }

    @app.post("/ledger/debit")
    async def ledger_debit(req: Request) -> dict[str, Any]:
        body = await req.json()
        agent_id = body["agent_id"]
        amount_cents = int(body["amount_cents"])
        purpose = body.get("purpose", "")
        # broker/ledger.py's HttpLedgerClient may send a "metadata" field
        # (token counts, model id) on this request; burn_for_inference()
        # only accepts a memo string, so there is nowhere real to route
        # it yet - read only what this route actually uses rather than
        # binding an unused local for it.
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

        # A bounded catch-up, NOT event_bus.replay().
        #
        # `main.py` replays the whole buffer, which was 2,000 events. CORE-09
        # grows that ring to `agents * 200` - 50,000 at the 250-resident
        # target - and a browser tab opening mid-run would be sent every one
        # of them before its first live frame. What a newly-opened dashboard
        # needs is enough to draw the town as it stands, not the run's whole
        # history; the trace store is where history lives.
        #
        # SUBSCRIBE FIRST, then send the catch-up from a snapshot taken
        # before subscribing. `main.py` replays and then subscribes, which
        # drops every event raised DURING the replay - a window of 500
        # frames on a live town, and the events lost are exactly the ones
        # a freshly-opened tab most needs. Sending the snapshot after the
        # subscription can interleave one live frame among the tail of the
        # old ones; every frame carries its own event_id and tick, so a
        # viewer applying them idempotently is unbothered, and a frame out
        # of order is strictly better than a frame never sent.
        catch_up = list(world_state.event_bus.recent())[-WS_REPLAY_EVENTS:]
        world_state.event_bus.subscribe("*", on_event)
        for event in catch_up:
            try:
                await on_event(event)
            except (TypeError, ValueError):
                # A payload that will not serialise. One bad frame is not
                # a reason to drop a working socket.
                logger.exception("ws replay could not encode %s", event.type)
            except Exception:  # noqa: BLE001
                # Anything else means the socket is gone. Stop sending into
                # it: without this, a browser that closes during the
                # catch-up leaves this handler pushing up to 500 frames at
                # a dead connection AND leaves its subscriber on the bus
                # until it finishes - so every event in the town is
                # delivered to nobody, slowly.
                logger.debug("ws client left during catch-up")
                break
        try:
            while True:
                # One-directional (world -> browser). Reading only to
                # notice the disconnect promptly, same as main.py.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            world_state.event_bus.unsubscribe("*", on_event)

    return app


# UI-06: how much history a freshly-connected browser tab is caught up
# with. See the ws_events docstring - the ring is population-scaled now,
# so "replay everything" is up to 50,000 frames before the first live one.
WS_REPLAY_EVENTS = 500


# ── the runner ──────────────────────────────────────────────────────


# Where a "gathering" opening puts the whole cast. The market square is
# the one place on the map every resident can reach and none of them owns.
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
        # Loaded once, here, so a scenario that cannot run fails at
        # construction rather than forty minutes into a paid run. Validation
        # lives in world/scenario.py; this only holds the answer.
        self.scenario = Scenario.load(config.scenario_path) if config.scenario_path else None
        if self.scenario is not None:
            # A config can NAME a scenario without having been shaped by one:
            # `apply_scenario` does the folding, and constructing a config by
            # hand skips it. Deliberate overrides are legitimate (a flag beats
            # the file), so this says so rather than refusing - a silent
            # divergence between the file a report names and the run it
            # describes is the thing worth preventing.
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
        # CORE-12: the last N turns' `steps_used`, as reported by the
        # engines. Bounded, because pacing wants a recent average and not a
        # history of the run.
        self.recent_steps_used: deque[int] = deque(maxlen=200)
        self.barren_ticks = 0
        # CORE-32: when the current barren streak started, in
        # monotonic seconds. None when the town is thinking.
        self.first_barren_tick_at: float | None = None
        self.timed_out_ticks = 0
        # INFRA-29: agent_id -> the monotonic time before which dispatching
        # again is pointless, because the engine is still working through
        # the turn that timed out.
        self.dispatch_blocked_until: dict[str, float] = {}
        self.dispatch_skipped_ticks = 0
        # EVAL-25: where in `world_state.tool_invocations` the mirror got
        # to, as the id of the last record it wrote.
        #
        # This was an index, on the stated grounds that "the list is
        # append-only within a run". It is not: `registry.dispatch` trims it
        # from the FRONT once it reaches MAX_TOOL_INVOCATIONS_KEPT, so every
        # record shifts down and an index stops pointing at what it pointed
        # at. Once the ring filled, the index equalled the length, the slice
        # `records[index:]` was empty, and NOTHING WAS EVER DRAINED AGAIN -
        # measured at 12,000 calls in, 5,000 written and 7,000 lost, which is
        # the election30 report's "334 -> 0 -> 0" output and its 6,639 day-1
        # calls filed as idle.
        #
        # One id, not a set of them, so the memory argument above still
        # holds: the cursor stays a single string however long the run is.
        self._last_drained_invocation_id: str | None = None
        # How many records the ring dropped before the mirror could write
        # them. Non-zero means this run's tool log has holes and the report
        # should not be read as complete.
        self.invocations_lost = 0
        # Declared here rather than sprung into existence by the getattr
        # that used to read it: `drain_learning` is what recovers the last
        # stretch of a run, and a reader that defaults its own flag runs it
        # on every halt the moment the assignment below is renamed.
        self._drained_before_halt = False
        # EVAL-15: the scripted control businesses, built once. They
        # remember what they posted today and who they have paid through,
        # so rebuilding per tick would reset the memory the rules turn on.
        self._controls: "list[Any] | None" = None
        # How many residents consolidated on the end-of-run drain. A report
        # figure, not a gate: an engine that died mid-run cannot drain, and
        # counting the ones that did is the honest number.
        self.learning_drained = 0
        # A7. game_day -> what each day job actually did. Read by
        # `assert_the_day_was_charged`, which is evaluation spec S1's gate:
        # a run that reaches a day boundary and does not charge halts.
        self.day_jobs_ran: dict[int, dict[str, int]] = {}

        # GOV-17. The charter store is async and the perception render is
        # sync, so the civic notice reads a snapshot rather than awaiting
        # per resident: one read per tick in `charter_tick`, one dict
        # lookup per resident. At 250 residents the alternative is 250
        # awaits of the same three rows.
        self.charter_cache: dict[str, Any] = {}
        # B3: agent_id -> steps used, for a turn that ended because it ran
        # out of thinking rather than because it was done. Popped by
        # `wallet_line`, so the sentence arrives on exactly one turn.
        self.budget_hit_last_turn: dict[str, int] = {}
        self.halt_reason: str | None = None
        # The running balance of unpaid head tax. ledger/head_tax.py owns
        # the arithmetic and the event log; this is where the one instance
        # lives for the run.
        from world.ledger.head_tax import HeadTaxDebtStore

        self.head_tax_debts = HeadTaxDebtStore()
        # ECON-16: who has eaten since the last day boundary. Cleared each
        # day in `run_daily_upkeep`, so "fed" means fed TODAY.
        self.fed_today: set[str] = set()
        # Turns lost to an exception inside `one(agent_id)`. Counted so a
        # run that quietly stopped simulating half its town is visible as
        # that, rather than as residents who chose to do nothing.
        self.turns_lost_to_errors = 0
        # agent_id -> HungerState. AgentLifeRecord owns no such
        # field by design; upkeep.py's report names this dict as
        # the intended home for it.
        self.hunger_by_agent: dict[str, Any] = {}
        self._tick_had_a_success = False
        # CORE-32: whether any resident was actually asked this tick.
        self._tick_had_a_dispatch = False
        self.checkpoint_store = LocalCheckpointStore(config.checkpoint_dir)

        self.supervisor: Supervisor | None = None
        # Per-agent CPU and memory, written beside the run's other logs.
        # None until boot decides where the run's output goes.
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
        # The world's own recorder. None means "this run is not writing
        # world rows" - a real state (attach_persistence says so in the
        # log when it happens), not a placeholder.
        self.trace_store: Any = None
        # GOV-04: the crime engine, absent from this host for 23 runs.
        self.crime: Any = None
        self.crime_engine_wired = False
        # CORE-24: where this run wrote down what it is.
        self.run_manifest_path: Path | None = None
        # GROW-04: buildings that actually finished this run.
        self.buildings_completed = 0
        # ECON-09: pensions actually paid, and retirees who had no
        # salary to base one on.
        self.pensions_paid = 0
        self.pensions_without_a_salary = 0
        # GOV-08. What the treasury actually spent on its own staff, and
        # what it could not. Four counters rather than one, because
        # "nobody was owed", "the treasury was empty" and "the money moved"
        # are three different facts about a town and a single number
        # cannot tell them apart - which is how a payroll of zero read as
        # healthy for the whole life of the project.
        # WIRE-02: filled at shutdown by `collect_wire_health`.
        self.wire_health: dict[str, int] = {}
        self.payroll_paid_sim = 0
        self.payroll_lines_paid = 0
        self.payroll_unpayable = 0
        self.payroll_skipped_confined = 0
        # GROW-06: not wired on this host yet. Declared rather than
        # read through a getattr default, so a reader can see it is
        # absent on purpose and `finish_what_the_town_built` says why.
        self.map_service: Any = None
        # CORE-31: one walkable doorstep per household, and the map house
        # sectors to draw them from. Declared here rather than created on
        # first use so nothing has to getattr them into existence.
        self._home_tiles: dict[str, tuple[int, int]] = {}
        self._house_names: list[str] | None = None
        # CORE-01: ticks that used more wall time than their interval.
        # A run where this is high is a run whose game clock is a
        # fiction, and the report should say so.
        self.ticks_that_overran = 0
        # INFRA-03: dispatches skipped because the resident was dormant.
        self.dormant_skips = 0
        # CORE-05: the priciest single tick so far, in cents.
        self.max_tick_spend_cents = 0.0
        # INFRA-10: said once, not once per game day.
        self._warned_no_duckdb = False
        # CORE-16: which residents have had the town roster written into
        # their persona seed, and who was in it. A resident NOT in here
        # still gets the whole roster in every standing brief - dropping
        # it from the brief is only safe where it is provably somewhere
        # else, and mock mode seeds no persona at all.
        self.roster_seeded: set[str] = set()
        self.roster_seen_by: dict[str, set[str]] = {}
        # CORE-17: dispatches skipped because the resident was asleep.
        self.night_skips = 0

        # STATUS.md risk #2 ("console-contract" task): real state the
        # embedded world app's /admin/god/population-dials and
        # /admin/control/* routes read and mutate directly (world/
        # admin_api.py's AdminDeps closures below point straight at these
        # two attributes, not a copy), so an operator's change actually
        # reaches this runner's own tick loop - see run_life_ticks (dials)
        # and run_tick/run_continuous (sim_control, pacing).
        self.population_dials = PopulationDials(
            target_band_min=DEFAULT_TARGET_BAND[0], target_band_max=DEFAULT_TARGET_BAND[1]
        )
        self.sim_control = SimControl()
        # Resolved lazily in start_embedded_world_server() (only a run
        # that actually serves HTTP needs an operator token generated and
        # logged) rather than here - every GenesisRunner() in the test
        # suite would otherwise mint and log one whether or not it ever
        # boots the embedded app.
        self.admin_extras: AdminExtras | None = None

        self.stop_requested = False

    def set_population_dials(self, **updates: Any) -> PopulationDials:
        """Mirrors wiring.py's WorldServer.set_population_dials exactly
        (same name, same shape) - world/admin_api.py's AdminDeps.
        set_population_dials closure calls this directly for a live run,
        the same way world/main.py's calls WorldServer's copy."""
        self.population_dials = self.population_dials.model_copy(update=updates)
        return self.population_dials

    # -- boot ------------------------------------------------------------

    async def boot(self, resume: bool = True) -> None:
        """Assembles the world (build_world() + genesis.json), and either
        restores it from the newest local checkpoint (resume=True and one
        exists) or seeds it fresh from genesis."""
        # GROW-06 / GROW-05 / UI-01. The map comes FIRST, and its WorldMap
        # is handed to build_world rather than left to load a second copy.
        #
        # GOV-11's second half as well: "land expansion is operator-only
        # and, on the production host, DECISION-ONLY". This host
        # hard-coded `map_service=None` into every dependency bundle it
        # built, so on the only host any run has ever used, land
        # approval logged "no physical map growth applied", a completed
        # building was never stamped onto a sector, and `/map/tiled`
        # answered 503. `build_world_server` - the host the roadmap said to
        # retire - has had this since it was written.
        #
        # `world_map=` is the GROW-05 half and it is the one that bites
        # quietly: without it `tools/bootstrap.py` loads its own WorldMap
        # off disk for MovementSystem, so the service grows one map and
        # every resident walks another. Expansion succeeds and `move`
        # refuses the new sector.
        # Before anything opens a resident's home: when this run continues a
        # town whose homes are gone (the default root is under `/tmp`), the
        # memories are in the checkpoint directory and nothing ever took
        # them out of the tarball. See `restore_engine_homes` - it is a
        # no-op when the homes are already there.
        restore_engine_homes(self.config.home_root, self.config.checkpoint_dir)
        self.build_the_map()
        self.world_state, self.registry = await build_world(
            genesis_tick=0,
            seed=self.config.population_seed,
            world_map=self.map_service.world if self.map_service else None,
            # A scenario's charter rules are seeded WITH the charter, not
            # amended after it: an election whose windows changed at version
            # 1 would read as the town having amended its own constitution
            # before anybody was awake.
            charter_fields=self.scenario.charter_overrides() if self.scenario else None,
            # GROW-12's caretaker is NOT passed here: `self.genesis` is not
            # loaded until ~100 lines below this call, so reading it would
            # raise. It is applied in `seed_genesis()` instead, where the
            # genesis data actually exists. `build_world` keeps the
            # parameter for callers that have their genesis in hand.
        )
        self.ledger = self.world_state.ledger
        # ECON-06: the hunger dict the day boundary keeps IS the one the
        # tools read. Two dicts that agree today drift tomorrow.
        self.world_state.hunger_by_agent = self.hunger_by_agent
        # ECON-14: ADOPT the world's store rather than keeping the one
        # made in __init__. The tool writes to `world.head_tax_debts`, so
        # the day job and snapshot/restore must read that same object -
        # two stores that merely agree are how a paid debt comes back.
        self.head_tax_debts = self.world_state.head_tax_debts
        self.wire_the_charter_to_the_bus()
        self.build_the_crime_engine()

        scheduler_config = SchedulerConfig(target_fraction=self.config.scheduler_target_fraction)
        # Unpaced at construction ON PURPOSE: the population is seeded
        # AFTER this point, so asking here reports 0 agents and derives a
        # meaningless interval (measured: "0 agents x 0.30 due = 1 calls/
        # tick", 0.30s, for a town of 250). apply_tick_pacing() sets the
        # real value once the agents exist.
        pacing_config = PacingConfig(mode=PacingMode.CONTINUOUS, continuous_interval_s=0.0)
        checkpoint_sink = self.build_checkpoint_sink()
        self.tick_engine = TickEngine(
            event_bus=self.world_state.event_bus,
            scheduler_config=scheduler_config,
            checkpoint_sink=checkpoint_sink,
            pacing_config=pacing_config,
            tick_index=self.config.effective_start_tick(),
            hearing_window_ticks=self.depth.hearing_window_ticks,
            # Task "walking": without this the MovementSystem that
            # build_world() constructs is never advanced, so a route `move`
            # starts is never stepped and no agent ever walks a tile. Every
            # downstream consumer of a real position - world.agent_tiles,
            # GET /agents' tile_x/tile_y, the viewer - then only ever sees
            # move_handler's instant-jump fallback. This is the one line
            # world.core.movement's own docstring says the composition roots
            # still need. `world_state.movement` is None when the Ville map
            # assets are absent, which TickEngine already treats as "no
            # movement wiring at all", exactly as before.
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
        # STATUS.md risk #4: charter/governance state (charter history,
        # elections, government membership, organizations, the
        # patent-office queue, amendments) must round-trip a checkpoint
        # exactly, not re-seed a fresh genesis charter on resume.
        self.tick_engine.checkpoints.register(
            "charter", CharterCheckpointAdapter(self.world_state.charter)
        )
        # What the town said and what it was refused. Excluded from owned
        # state so a failed call cannot erase them, which also excluded them
        # from every checkpoint until this line existed.
        self.tick_engine.checkpoints.register(
            "records", RecordCheckpointAdapter(self.world_state)
        )

        # CORE-08: the stakes. Hunger, tax debt and which day jobs have
        # already run - three stores the runner owns that no checkpoint
        # carried, so a resume fed the hungry and forgave the indebted.
        self.tick_engine.checkpoints.register(
            "stakes", StakesCheckpointAdapter(self)
        )

        # GOV-12. The last store the runner owns that no checkpoint
        # carried. A resume kept the ledger, the charter, the hunger
        # ladder and the tax debts, and forgot every open case, every
        # warrant and every unreleased confinement - so a resident
        # serving a sentence walked free at the resume and the case it
        # was serving for was gone.
        #
        # Registered only once the engine exists, which it now always
        # does on this host (GOV-04); the guard is for the tests that
        # build a runner without one.
        if self.crime is not None:
            self.tick_engine.checkpoints.register(
                "crime", CrimeStoreCheckpointAdapter(self.crime.store)
            )

        # GROW-07. Only when there IS a map: registering an adapter over
        # None would turn a missing gitignored asset directory into a
        # checkpoint failure, and build_the_map deliberately degrades.
        if self.map_service is not None:
            self.tick_engine.checkpoints.register(
                "map", MapCheckpointAdapter(self.map_service, self.world_state)
            )

        self.metrics = GenesisMetrics(
            self.ledger, self.world_state, self.counters,
            budget_cap_cents=self.config.budget_cap_cents,
        )
        self.governor = SpendGovernor(self.ledger, self.config.budget_cap_cents)

        # The genesis record itself, on BOTH paths.
        #
        # It used to be read only inside seed_genesis(), which runs only when
        # there is no checkpoint. start_agents() needs it to write each
        # engine its persona, so resuming a run crashed with
        # `'GenesisRunner' object has no attribute 'genesis'` before a single
        # engine started - the process backend simply could not resume.
        #
        # The checkpoint carries the world's STATE; the genesis file carries
        # who these people are, which no checkpoint replaces.
        from world.life.genesis import RichGenesis

        self.genesis = RichGenesis.model_validate_json(
            self.config.genesis_path.read_text(encoding="utf-8")
        )
        prune_ties_to_people_not_in_the_cast(self.genesis)

        # So GET /world/preflight can name the cast this run is actually
        # against. It reads the environment because it is a route on an app
        # built by a function that never receives the config - and reading
        # the wrong genesis made it report "genesis has 314, run wants 30"
        # against a perfectly correct 30-agent run.
        os.environ["KASUKABE_GENESIS_PATH"] = str(self.config.genesis_path)
        os.environ["KASUKABE_ENGINE_REPO"] = str(self.config.engine_repo)

        # Filled here, on BOTH paths, not in seed_genesis() - which runs only
        # when there is no checkpoint. standing_brief() reads these every
        # tick, so a resumed run would otherwise tell thirty agents nothing
        # about who they are.
        for a in self.genesis.agents:
            self.agent_names[a.agent_id] = a.name
            self.agent_bios[a.agent_id] = a.bio
            self.genesis_by_id[a.agent_id] = a

        # The world needs the names too, not just the runner: `say` resolves
        # who a remark is addressed to out of its words, and AgentLifeRecord
        # carries no name to match against.
        if self.world_state is not None:
            self.world_state.agent_names.update(self.agent_names)
            # And what each of them does for a living, and where. The data
            # half of world/tools/cards.py: without it `find_people` can
            # rank nobody, because AgentLifeRecord carries no occupation and
            # nothing else in world state does either. On BOTH paths for the
            # same reason the names are - a resumed run whose residents have
            # no trades is a town where nobody can find a buyer.
            for a in self.genesis.agents:
                occupation = str(getattr(a, "occupation", "") or "")
                if not occupation:
                    continue
                self.world_state.occupations[a.agent_id] = occupation
                # The trade's workplace. GROW-10 overrides this for anyone
                # who staffs a business - but NOT here: the businesses are
                # seeded by `seed_genesis()` further down this method, so
                # asking about employers at this point could only ever
                # answer None and the override would be inert on every
                # single run. That is the defect this whole review finds
                # most often, and it very nearly went in as the fix for it.
                workplace = WORKPLACE_BY_OCCUPATION.get(occupation)
                if workplace:
                    self.world_state.workplaces[a.agent_id] = workplace

        # Before either branch: a resumed run holds conversations too, and
        # seed_genesis() below needs the store to exist to seed `agents`.
        await self.attach_persistence()

        latest = self.checkpoint_store.latest() if resume else None
        target = self.config.resume_at_tick
        if target is not None and latest is None:
            # An operator who asked to continue a run, with no run to
            # continue. Seeding a fresh town here would answer the wrong
            # question without saying so.
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
            # EVAL-25's cursor starts at 0, and the ring it drains was just
            # restored holding the checkpointed run's calls - which that run
            # already filed. Start after them, or a continuation files its
            # parent's whole log again (run 28b: 1,204 rows).
            restored = self.world_state.tool_invocations
            self._last_drained_invocation_id = (
                restored[-1].invocation_id if restored else None
            )
            # KASUKABE_ELECTION_NOW: open a ballot at the tick we resume on,
            # instead of waiting for the next one on the charter's rhythm.
            #
            # Off by default and opt-in, because a town whose elections move
            # when an operator wants one is not holding elections. It exists
            # for the case this run hit: the wording that actually gets a
            # ballot cast was proved (8/8 against the live model) with $14.20
            # left and the next scheduled count 130 ticks and $27 away. The
            # fix would have gone untested for want of an election to test it
            # in - so the operator may bring one forward, once, and the run
            # says so in its log.
            if os.environ.get("KASUKABE_ELECTION_NOW", "").strip() == "1":
                existing = await self.world_state.charter.store.list_elections()
                live = [e for e in existing if e.phase is not ElectionPhase.TALLIED]
                # A ballot already TAKING PLACE is left alone - moving one
                # mid-vote would discard the ballots already in it. One merely
                # SCHEDULED, whose candidacy has not opened, is the case this
                # exists for: it sat 130 ticks and $27 away with $14 left.
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
                    # Brought forward AND compressed, to the number of ticks
                    # the budget has left rather than the charter's rhythm.
                    #
                    # KASUKABE_ELECTION_WINDOW is the length of EACH of the
                    # three phases in ticks. The default 6 is one game-hour:
                    # candidacy, campaigning and voting in eighteen ticks
                    # instead of fifty-four. The run that needed this had
                    # $7.86 left and a full cycle costs about $11, so the
                    # choice was a short election or none - and an election
                    # nobody can afford to finish teaches nothing.
                    #
                    # It is the phases that are shortened, never skipped: a
                    # ballot with no candidacy window is not a fast election,
                    # it is a different thing wearing the name.
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
                # Forward only. Earlier than the checkpoint would replay ticks
                # the ledger has already charged for.
                if target < latest.tick_index:
                    raise ValueError(
                        f"resume_at_tick={target} is before the checkpoint's tick "
                        f"{latest.tick_index}; a resumed run cannot go back in time"
                    )
                # The clock is one integer (world/core/clock.py), so the jump
                # is one assignment. What it leaves safe: hourly life jobs
                # catch up in a single step by elapsed hours, daily jobs fire
                # only on a change of game day, and every resident whose next
                # decision is now in the past is due on the next tick.
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
            # GROW-10: now that the businesses exist, brief their people to
            # their own address. Must be after seeding - see the note on
            # the trade table above for what happens if it is not.
            self.brief_staff_to_their_workplace()
            # CORE-04, and ECON-02 word for word: "day-0 upkeep is
            # charged at the first tick of every run, silently". The boot
            # day has already happened; do not bill for it.
            #
            # `last_life_day_processed` defaults to -1 and the boot day is 0,
            # so `game_day != last_life_day_processed` was true on the very
            # first tick of every run: 90 sim of upkeep per household and a
            # head-tax pass, charged for a day nobody had lived yet. It was
            # invisible because upkeep moves money as a transfer and emits
            # no event, so the report could not see it either (EVAL-01).
            #
            # Only on the fresh path: a resume restores this counter from
            # the checkpoint, and overwriting it there would skip a real
            # day boundary.
            self.counters.last_life_day_processed = timestamp_for(
                self.world_state.current_tick
            ).game_day

        # The world's tick and the clock agree from the moment boot ends. The
        # resume path already set both; a fresh town booted with
        # `start_at_dawn` built its clock at DAWN_TICK and left
        # `world_state.current_tick` at 0, so `world.tick()` said midnight
        # while the clock said 06:00, and everything done before the first
        # tick was stamped with the wrong time.
        self.world_state.current_tick = self.tick_engine.clock.tick_index

        # CORE-09: the speech ring has to fit the town. Sized here because
        # the bus is built before anybody is in it, and it is 
        # that decides what a resident is told was said to it.
        self.world_state.event_bus.size_for_population(len(self.world_state.agents))

        # CORE-07. Both paths, not just the fresh one.
        #
        # `apply_tick_pacing()` was called from inside `seed_genesis()`, so a
        # resumed run came back UNPACED: the resume branch above restores and
        # returns, and CORE-01's `pace_the_tick` then reads an interval
        # nobody ever computed. Idempotent and cheap, and it must run after
        # the branch because it sizes the interval off the population, which
        # only exists once one of the two arms has put people in the world.
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
            # The owner's own pocket, separate from the till. Every action is
            # charged to the ACTOR, not the business, so an owner with 0 sim
            # is refused its first `post_price` for want of 1 sim - which is
            # what the first draft of this did. It starts with exactly what a
            # genesis resident starts with, so the scripted owner and an
            # engine-run owner face the same action prices from the same
            # pocket, and a difference between them is not a difference in
            # starting money. No dollars: the script does not think.
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

            # The trace store on DISK, named the same way this runner
            # already names it for preflight and the disk-headroom check.
            #
            # get_client() with no config falls through to
            # SupabaseConfig.from_env(), whose sqlite_path defaults to
            # ":memory:". Attaching the recorder to that is worse than not
            # attaching it: every write succeeds, nothing warns, and the
            # rows vanish with the process - which is exactly the symptom
            # (an empty utterances table after a talkative run) this
            # method exists to fix. SqliteClient applies
            # supabase/sqlite_schema.sql on first use, so pointing at a
            # path that does not exist yet creates the tables.
            trace_db = os.environ.get("TRACE_SQLITE_PATH", "data/kasukabe.db")
            config = SupabaseConfig.from_env().model_copy(
                update={"sqlite_path": trace_db}
            )
            self.trace_store = TraceStore(get_client(config))
        except Exception as exc:  # noqa: BLE001
            # A run must not die because its recorder would not open. It
            # says so plainly instead: a silent None here would look
            # exactly like the bug this method exists to fix.
            logger.warning("world-side persistence unavailable, not recording: %s", exc)
            self.trace_store = None
            return

        # INFRA-09 / EVAL-24: what this run IS, recorded before it does
        # anything. Here rather than in `boot()` because this is the first
        # point at which the trace store exists to write into, and at BOOT
        # rather than at stop because the runs that most need explaining
        # are the ones that did not finish.
        await self.record_manifest()

        store = self.trace_store
        ledger = self.ledger
        self.attach_eating()

        async def persist(event: WorldEvent) -> None:
            PUBLISHED_BY_TYPE[event.type] = PUBLISHED_BY_TYPE.get(event.type, 0) + 1
            try:
                await store.record_transaction_if_new(ledger, event)
                await store.record_utterance_if_new(event)
                # What the town MAKES, which is the point of the experiment.
                #
                # These three existed and nothing called them, so `skills`,
                # `tools_registry` and `memories` took zero rows however the
                # town behaved. Two skills were authored on one audited run
                # and neither survived the process - the same failure as the
                # utterances table, in the code written to fix the utterances
                # table.
                await store.record_skill_if_new(event)
                # INFRA-17 / GROW-19. Four tables the schema declared and
                # nothing wrote: petitions, buildings, cases, organizations.
                # Build Spec 4.3 asks for a test that fails on any table
                # with no live writer - these were four of the six it would
                # have caught, and the expansion, construction and justice
                # tracks all had to be measured out of generic `events`
                # rows instead of the tables built for them.
                await store.record_petition_if_new(event)
                await store.record_building_if_new(event)
                await store.record_case_if_new(event)
                await store.record_organization_if_new(event)
                await store.record_invented_tool_if_new(event)
                await store.record_memory_if_new(event)
                # EVAL-02. The `events` table took zero rows in every genesis
                # run, so the report, the evals and the observatory all read
                # an empty table and said nothing was wrong.
                #
                # It was left out ON PURPOSE - mirroring every WorldEvent is
                # a write proportional to tick count, and that is what grew
                # an earlier trace store to 21 GB. So this is an ALLOWLIST,
                # not a blanket subscribe: the domains that carry a decision
                # or move a resource, never the per-tick chatter
                # (`agent.moved` alone was 20 residents x 40 tiles x N ticks,
                # and is exactly what did the damage). See
                # RECORDED_EVENT_DOMAINS for the rule and the exclusions.
                if is_worth_recording(event.type):
                    await store.record_event(event)
                    RECORDED_BY_TYPE[event.type] = RECORDED_BY_TYPE.get(event.type, 0) + 1
            except Exception as exc:  # noqa: BLE001
                # Counted, not just logged. Run 16 printed this warning 125
                # times - one resident's whole existence - and finished
                # reporting nothing wrong, because a WARNING in a 4,000-line
                # log is not a surface anybody reads. The recorder now keeps
                # a running total, and three consecutive rejections stop the
                # run the way three failed batches stop broker dispatch: a
                # town whose record is broken is not producing anything worth
                # paying for.
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

            # ECON-16. A meal answers the day's HUNGER and settles no
            # part of the bill.
            #
            # This used to write `HungerState(consecutive_unpaid_days=0)`,
            # which is the money counter - so the comment above ("eating is
            # not a way to dodge the bill") described the opposite of what
            # the line did. A household could skip upkeep indefinitely as
            # long as each member ate one apple a day: the escalation is
            # the ONLY consequence of not paying, and a meal reset it.
            #
            # The arrears streak is carried across untouched. Only paying
            # clears it, in `advance_hunger`.
            current = self.hunger_by_agent.get(agent_id, HungerState())
            self.hunger_by_agent[agent_id] = HungerState(
                consecutive_unpaid_days=current.consecutive_unpaid_days,
                days_since_meal=0,
            )
            # Remembered for the day boundary, which is where hunger
            # actually advances - `run_daily_upkeep_tick` needs to know
            # this resident fed itself even though the household did not
            # pay, or the ladder would starve someone who ate today.
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
        # RichGenesis (not the base Genesis) so self.genesis.agents carry
        # each agent's full `persona` block - start_agents() below reads
        # it to write every engine process its system-prompt seed (RC-1,
        # ROADMAP.md section 1.2: "the persona was never delivered").
        # RichGenesis is an additive-only extension of Genesis (same
        # fields, same types, plus `persona`), so every other reader of
        # self.genesis in this file keeps working unchanged.
        from world.life.genesis import RichGenesis

        raw = self.config.genesis_path.read_text(encoding="utf-8")
        genesis = RichGenesis.model_validate_json(raw)
        prune_ties_to_people_not_in_the_cast(genesis)
        # Retained so build_embedded_world_app() can mount the viewer
        # routers, which need the personas. Without this the live run's
        # world served no /agents, /places or /world/state at all, the
        # game server's every call 404'd, and it silently fell back to
        # mock_world - which is how the operator spent a run watching
        # fabricated "mock-agent-011" rows in fabricated places.
        self.genesis = genesis

        # GROW-12: whoever this genesis names to hold the government until
        # the first election. `None` is the default and is exactly the
        # documented behaviour - seats vacant.
        #
        # Applied HERE and not at `build_world()`: that call happens ~100
        # lines before `self.genesis` exists, so reading the field there
        # raised AttributeError on every boot. `build_world` still takes
        # the parameter, for callers that have their genesis in hand.
        #
        # Read directly rather than through a defaulting getattr: the
        # field is declared on `Genesis`, and a default would answer "no
        # caretaker" the day it is renamed - putting the expansion
        # pipeline's only in-world entry back out of reach silently.
        if genesis.caretaker_pm_agent_id:
            await self.world_state.charter.store.set_government_state(
                GovernmentState(
                    pm_agent_id=genesis.caretaker_pm_agent_id,
                    minister_agent_ids=[],
                    # Not a term. An appointment nobody voted for must not
                    # reach DESIGN section 10's regime-transition counts.
                    terms_served={},
                    caretaker=True,
                )
            )
            logger.info(
                "GROW-12: %s holds the government as caretaker until the "
                "first election - the town can petition for land",
                genesis.caretaker_pm_agent_id,
            )

        # ECON-13. The town's debts become debts, not prose.
        #
        # The persona generator gives neighbours real obligations -
        # `NeighbourOpinion(kind="debt", sim_amount=...)` on BOTH sides of
        # each pair - and `seed_genesis` read none of them. So
        # `world.loans` was empty at tick 0, `debts_of` had nothing to
        # show, and run 23 recorded twenty `check_debts` calls and twenty
        # empty answers. The best social material the town produces was
        # invisible to the one instrument built to read it.
        #
        # Only the OWER side is recorded. Each debt exists twice in the
        # genesis file, once from each neighbour's point of view, and
        # writing both would double every loan in the town.
        #
        # The role is "ower". The finding calls it "owing"; the code does
        # not, and taking the finding's word for it would have seeded
        # nothing while looking correct.
        # The opinions live on the PERSONA, not on the agent - `persona`
        # is what `RichGenesisAgent` adds, and `opinions` is a field of
        # `PersonaDepth`. The first draft of this read `agent.opinions`
        # through a three-argument getattr and would have seeded exactly
        # nothing, silently, while looking correct. Direct access here so
        # a rename is a loud AttributeError.
        seeded_loans = 0
        unknown_debt_roles: set[str] = set()
        for a in genesis.agents:
            persona = a.persona
            if persona is None:
                continue
            for opinion in persona.opinions:
                if opinion.kind != "debt":
                    continue
                # TOOL-18. The genesis file says role "owing"; this read
                # "ower" and matched nothing. The seeder ran on every boot,
                # logged nothing, and seeded ZERO debts - while
                # `check_debts` answered "you owe nobody" to a resident
                # whose own persona said it owed 400 sim. Twenty calls came
                # back empty and one debt got paid twice by two people.
                #
                # Both spellings are accepted rather than one corrected: a
                # genesis file is hand-written data, and this loop must not
                # go quiet on a synonym again. The counter below is what
                # makes the next mismatch loud.
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
            # A debt written with a role this build does not know is a debt
            # the town will never see. Loud, because the silent version of
            # exactly this shipped and cost twenty empty `check_debts`.
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
        # Seed from the GENESIS FILE, not Ledger.genesis's defaults. Those
        # defaults encode a 250-resident town with a 50000-cent treasury;
        # the file now sizes the treasury as the remainder of a fixed $1000
        # once every resident has a purse. Taking the defaults funded 314
        # wallets AND a 250-town treasury, booting a 112800-cent town and
        # breaking the one invariant the ledger exists to hold.
        #
        # world/wiring.py already derives both the same way - this is the
        # other composition root catching up.
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
                    # Almost always "UNIQUE constraint failed: agents.slug" -
                    # this cast is already in this trace store because the
                    # last run seeded it. That is a re-run, not a fault: the
                    # row we would write is the row already there, and the
                    # utterances about to reference it resolve either way.
                    # Counted and reported once below rather than per agent,
                    # so a genuinely broken store still says so without
                    # printing the same line ten times.
                    reseed_failures.append(f"{agent.agent_id}: {exc}")
            # Where the town wakes up. Two openings, because the two runs
            # this supports ask different questions of the same cast:
            #
            #   homes      - everyone at their own household, dispersed. What
            #                a resident does with a map and a trade.
            #   gathering  - everyone in the market square, in one room.
            #
            # This is a fact of the world, not an instruction. Nothing in the
            # standing brief changes between the two and no resident is told
            # to talk or to work - see standing_brief's docstring, which is
            # deliberate about never encouraging anything. Putting twenty
            # people in one place and putting them in four places are both
            # honest starting states; what they then do is the experiment.
            location_id = (
                OPENING_GATHERING_PLACE if self.config.opening == "gathering"
                else household_location_id(agent.household_id)
            )
            self.world_state.locations.place(agent.agent_id, location_id)
            self.tick_engine.register_agent(agent.agent_id, location_id=location_id)
            self.stand_them_on_a_tile(agent.agent_id, location_id)
            # CORE-21. Say it out loud, once.
            #
            # These two lines write the two LocationIndexes BY HAND -
            # `world_state.locations` and `tick_engine.locations` are
            # separate instances subscribed to the same bus, and genesis was
            # the one place that set them without an event. So no
            # `agent.spawned` existed for a genesis resident: the trace had
            # no record that anybody arrived, `GenesisMetrics` could only
            # count births, and a third subscriber added later would start
            # life not knowing where anyone was.
            #
            # `build_world_server` has published this since it was written
            # (wiring.py:443) - the same absorb-the-other-host's-wiring gap
            # as CORE-02 and GROW-06. Published AFTER the direct writes, not
            # instead of them, so the indexes are correct even if nothing is
            # listening yet.
            await self.world_state.event_bus.emit(
                "agent.spawned",
                tick=self.world_state.tick(),
                payload={"agent_id": agent.agent_id, "location_id": location_id},
            )
            if agent.starting_sim_balance:
                self.ledger.get_account(agent.agent_id).sim_units = agent.starting_sim_balance
            # And what is in the larder. See GenesisAgent.opening_stock:
            # genesis has always handed out money and never food.
            self._credit_opening_stock(agent.agent_id, agent.opening_stock)

        for household in genesis.households:
            self.world_state.households[household.household_id] = LifeHousehold(
                household_id=household.household_id, member_ids=list(household.member_ids)
            )

        # The business roster, for the same reason as the agents above: the
        # `businesses` table is the FK other rows hang off, and it took zero
        # rows because nothing called seed_business.
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
        # Materials to break ground with, for the reason in
        # ConstructionCompany.opening_stock: a crew that has to wait for a
        # supply chain before it can start is a crew that never starts.
        self._credit_opening_stock(cc.business_id, cc.opening_stock)
        # The yard BIDS for timber, planks and ore: election30's miner was
        # told three times that nobody bought ore, and 52 fellings of timber
        # found no buyer at all.
        self._post_opening_prices(cc.business_id, cc.opening_prices)
        self.world_state.businesses[cc.business_id] = Business(
            business_id=cc.business_id,
            name=cc.name,
            business_type="construction_company",
            owner_agent_id=cc.owner_agent_id,
            employee_agent_ids=set(cc.hand_agent_ids),
            location_id=business_location_id(cc.business_id),
            # TOOL-02: the yard is where timber is worked and frames are
            # raised, so its premises IS the carpentry and the sawmill.
            # Run 23 had Silas standing alone at `business:b_yard` saying
            # "the carpenter needs timber", unable to cut a plank there.
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
                # TOOL-02: the mill provides "mill", the bakery "kitchen".
                # Without this the miller could not mill in her own mill and
                # the baker's only oven was an opaque string-room no
                # customer could walk to.
                provides_workshops=workshops_for_business_type(
                    business.business_type
                ),
            )
            # Open the doors with something on the shelves. See
            # SmallBusiness.opening_stock: every shop in the town has been
            # empty in every run ever made, and `buy` answered "b_bakery
            # needs 2 bread, has 0" to a resident who had walked there to
            # buy breakfast.
            self._credit_opening_stock(business.business_id, business.opening_stock)
            self._post_opening_prices(business.business_id, business.opening_prices)
            await _seed_business(self.world_state.businesses[business.business_id])

        # First election, per charter/engine.py's own invitation to
        # whichever caller owns the tick-loop decision of "when genesis
        # hands off to the clock is running" - see module docstring's
        # known-gaps section for the (deliberate) scope boundary here.
        # NOT tick 0. `schedule_election(0)` laid the candidacy window from
        # midnight however late the world actually started, so a run begun at
        # dawn was 36 ticks into its own candidacy before a resident drew
        # breath, and a run begun later still could miss the window it was
        # booted to watch. The clock's own tick is the honest answer; a
        # scenario may name another (`election.opens_at`, "genesis" for the
        # old behaviour).
        # The CLOCK, not `world_state.current_tick`: genesis is seeded before
        # the world's tick is reconciled to the clock (see boot()), so the
        # world still reads 0 here while the clock already says 36.
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

    # Edible goods only - "you are holding 40 timber" is not a food figure.
    # Same set /world/vitals uses, so the agent's own view of its larder and
    # the town's food panel can never disagree about what counts as food.
    # CORE-16: how many obligation rows a brief prints. A resident with
    # forty outstanding loans is in a situation no list will fix, and the
    # brief is a readout rather than a statement of account - `check_debts`
    # still returns every one of them.
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
        # ECON-31 made a household-less resident a household of one for
        # billing; the readout has to agree with the biller or a resident
        # is shown a bill nobody will charge, or charged one nobody showed.
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

        # ECON-20 / TOOL-25: `build_economic_context` is the assembler,
        # and this is its first caller outside its own tests. It wraps
        # `gather_wallet_and_burn` (which this used to call directly) and
        # carries the household figures alongside the wallet, so the two
        # halves of a resident's economy cannot drift apart on the way to
        # the same line.
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
            # Per tick, from the same window the burn rate is measured over.
            "last_turn_usd_cents": (
                snapshot.recent_burn_usd_cents / snapshot.burn_window_ticks
                if snapshot.burn_window_ticks else 0
            ),
            # TICKS_PER_GAME_DAY is 144 (DESIGN section 1, ten game-minutes
            # a tick), and a runway in days is what a person plans against.
            "runway_game_days": (ticks / 144.0) if ticks is not None else None,
            # B3, addendum section 10: one more sentence, and only when the
            # last turn actually ran out. Past tense, a number, and NO verb
            # - whether a resident finds `buy_energy` from this is the
            # measurement (S2), not something to give away in the prompt.
            "ran_out_after_steps": self.budget_hit_last_turn.pop(agent_id, None),
            # ECON-20: the two keys `format_volatile_line` has always
            # rendered and nothing ever set. DESIGN section 3 puts today's
            # upkeep and tax in context every tick; until now a resident
            # could read its wallet, its burn and its runway, and had no
            # way to learn what was about to be taken out of it - then
            # went hungry for a bill it was never shown.
            "upkeep_due_sim": upkeep_due_sim,
            "head_tax_due_sim": head_tax_due_sim,
            # "who depends on it", the third clause of the same sentence.
            "dependents": list(household.dependent_agent_ids) if household else [],
            # Run 28: the two figures above are the HOUSEHOLD's, and without
            # its size the line read as a personal bill. See
            # test_a_household_bill_is_not_a_personal_one.py.
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

        # Occupation and business come from the GENESIS persona, not the life
        # record: AgentLifeRecord is deliberately narrow - "no wallet
        # balances, no location, no memory" per its own docstring - and
        # carries neither. world/agents_api.py reads them the same way, so
        # the agent's own view and the town's roster cannot disagree.
        persona = self.genesis_by_id.get(agent_id) if self.genesis else None

        food = 0
        # Doc 01 blocker 8: the same sum, kept broken out by name. The
        # brief used to render only the total and call it "food", which is
        # the one word `eat` refuses - so a resident reading its own brief
        # was taught a vocabulary the world does not accept. Biggest pile
        # first, capped, for the reason holdings_line is capped.
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

        # Where this agent's work actually is: its own business if it owns
        # or staffs one, otherwise the place its trade is worked. A miner
        # with no business still has an ore outcrop to walk to.
        workplace = None
        business_id = getattr(persona, "business_id", None)
        if business_id:
            business = self.world_state.businesses.get(business_id)
            workplace = getattr(business, "location_id", None) or f"business:{business_id}"
        else:
            # GROW-29: resolved, not looked up. Three of the twelve values
            # are workshop constants that `known_places` does not offer.
            workplace = self.resolve_workplace(getattr(persona, "occupation", None))

        # Who else there is.
        #
        # The brief named the agent its own trade and never once said who
        # shared the town with it. So a resident who wanted to sell wheat
        # reached for a name, and the only names within reach were the ones
        # written on the map's buildings - "Carlos Gomez's apartment",
        # "Harvey Oak Supply Store", "Hobbs Cafe" - which belong to a cast
        # of thirty who are not in this run. Observed live: Tobias hailing
        # "Harvey" and "Isabella", Bram walking to "sh_silva". Nobody by
        # those names exists, so the words reached no one and the walk
        # ended nowhere.
        #
        # Household first, because that is who a person actually knows, then
        # the rest of the town. Names and trades only - who exists and what
        # they do, which is what the map was being misread to supply. Not
        # where they are: that is what perception and `look_around` are for,
        # and a roster that tracked everyone's position would hand every
        # agent a live map of the whole town for free.
        # CORE-16: only for a resident whose persona seed did NOT carry
        # the roster.
        #
        # `town_roster_text` writes these same names once into
        # `persona.txt` at boot, so repeating them here costs
        # O(residents) tokens per resident per tick for a list that has
        # not changed. But the roster prevents a real observed failure -
        # residents hailing names off the map's buildings, a cast of
        # thirty who are not in this run - so it is dropped from the brief
        # only where it is provably somewhere else. Mock mode seeds no
        # persona at all, and a resident born mid-run was never seeded, so
        # both keep the full list.
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

        # `name_the_numbers` last, over the whole brief. `ledger_of_self`
        # renders a debt as `{"to": "n07"}` - `WorldState.debts_of` writes
        # the lender's id and the ledger needs it that way - so a resident
        # was told every morning that it owed 400 sim to a number. Bram
        # Holloway's memory in run 28b had to reconstruct the join by hand:
        # *"I owe 400 sim to n07 (Marta)"*. It is done here rather than in
        # `debts_of` because that method's rows are also the ledger's, the
        # evals' and the trace's, and those want the id.
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
            # CORE-16: the part of the roster the seed cannot know. A
            # resident born on day three is in nobody's persona.txt, and a
            # resident who died is still in everybody's - so the changes
            # ride the tick and the unchanging bulk does not.
            "roster_changes": self.roster_changes_for(agent_id),
            # CORE-16: obligations, stores, the shop, the walk in progress
            # and the thing being repeated. Merged flat and only when there
            # is something to say, so the brief for a resident who owes
            # nothing and is standing still is exactly what it was before.
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
            # UI-05: the SAME dict the world writes to, not a second one.
            # `AdminExtras` built its own empty `birth_requests`, so the
            # console's blessing queue and the town's requests were two
            # objects that could never meet - and since nothing could write
            # to the console's one, the queue was empty in every run ever
            # made.
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

        # ── what is owed, either way ───────────────────────────────────
        try:
            debts = state.debts_of(agent_id)
            owed_by = debts["owed_by_you"]
            owed_to = debts["owed_to_you"]
            # Each side rendered only if that side has something in it.
            #
            # This tested one condition and wrote two keys, so a resident
            # who was OWED money but owed none rendered `owe: []` - the
            # empty-key readout CORE-16 exists to keep out of the brief.
            # It could not fire before ECON-13, because `world.loans` was
            # empty in every run and neither branch was ever reachable;
            # seeding the genesis debts turned a latent violation into a
            # live one on the first tick.
            if owed_by:
                out["owe"] = owed_by[: self.MAX_OBLIGATIONS_SHOWN]
            if owed_to:
                out["owed_to_me"] = owed_to[: self.MAX_OBLIGATIONS_SHOWN]
            if owed_by or owed_to:
                out["due_today_sim"] = debts["due_today_sim"]
            # The two keys retrieval_depth has always tried to print,
            # broken out of the same rows rather than recomputed, so the
            # total and its parts cannot disagree.
            for row in owed_by:
                if row["kind"] == "upkeep":
                    out["upkeep_due_sim"] = out.get("upkeep_due_sim", 0) + int(row["sim"])
                elif row["kind"] == "head_tax":
                    out["head_tax_due_sim"] = out.get("head_tax_due_sim", 0) + int(row["sim"])
        except Exception:  # noqa: BLE001 - a readout may not end a turn
            pass

        # ── what is in the store, as well as in hand ───────────────────
        #
        # A Storage is an id and a place; what is IN it lives in the same
        # Inventory as everything else, keyed by storage_id (see
        # world/tools/inventory.py: "this module does not care which
        # namespace an id comes from").
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

        # ── the shop, for whoever owns it ──────────────────────────────
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

        # ── where the feet are, when they are moving ───────────────────
        try:
            journey = state.movement.journey_for(agent_id) if state.movement else None
            if journey is not None:
                out["walking"] = {
                    "to": journey.destination_location_id,
                    "eta_ticks": journey.ticks_remaining,
                }
        except Exception:  # noqa: BLE001
            pass

        # ── what is being repeated, and how many times so far ──────────
        #
        # Only the ENDING of a commitment was ever rendered, so a resident
        # under one could not see what it was committed to or how far in
        # it was - it simply found itself doing the same thing again.
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
        # Which ones, not just how many. "29/30 alive" is the start of a
        # question nobody could answer without reading thirty health ports
        # by hand, and on a five-hour run the answer decides whether to
        # restart one engine or stop everything.
        # ...and WHY. The supervisor already keeps each unit's merged
        # stdout/stderr tail for exactly this moment (see drain_output), and
        # it was going unread: twenty-three engines reported "exited with
        # code 3" and the reason was sitting in memory, one method call away.
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
            # Turns the runner gave up waiting for. Each one had already paid
            # for its calls and produced nothing, so this number is money
            # spent on nothing - it belongs beside "alive", not buried in a
            # log line.
            "timed_out_ticks": self.timed_out_ticks,
            # INFRA-29: turns the runner declined to send because the
            # previous one for that resident had not finished. High
            # alongside `timed_out_ticks` means the engines are slower than
            # the cadence, which is a pacing problem, not a broker one.
            "dispatch_skipped_ticks": self.dispatch_skipped_ticks,
            # EVAL-25: tool calls the invocation ring dropped before the
            # mirror could write them. Non-zero means this run's tool log has
            # holes, so every figure joined against it - cost per action,
            # verb reach, which turns changed the world - is a floor rather
            # than a count. Beside the other "money spent on nothing"
            # counters because that is what it is.
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

        # Not a door: it is a workshop. Whoever provides it is the work.
        for business in self.world_state.businesses.values():
            # Declared field (models.py:55), read directly - a getattr
            # default would answer "provides nothing" on a rename and send
            # every miller back to the phantom this method exists to fix.
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

        # Deterministic: the household id decides which house, so the same
        # seed lays the town out the same way twice.
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
        # Direct access: `sectors` is a declared field on WorldMap and
        # `categories` one on Sector, so a getattr default here would
        # answer "this town has no houses" on a rename and put every
        # resident back on no tile at all.
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
            # What situation this run was set up in, so a reader of two
            # reports knows whether they are comparable at all.
            "scenario_path": self.config.scenario_path or "",
            "scenario": self.scenario.name if self.scenario else "",
            "scenario_says": self.scenario.describe() if self.scenario else [],
            "start_tick": self.config.effective_start_tick(),
            # Where a resumed run picked the day up - two reports from one
            # checkpoint are not comparable if one skipped the afternoon.
            "resume_at_tick": self.config.resume_at_tick,
            "veto_window_ticks": self.config.veto_window_ticks,
            # The knobs that change what a number MEANS, so two reports can
            # be compared honestly or refused.
            "sim_per_usd": SIM_PER_USD,
            "model_id": os.environ.get("MODEL_ID", ""),
            "context_window": os.environ.get("KASUKABE_CONTEXT_WINDOW", ""),
            "full_schema_budget": os.environ.get("KASUKABE_FULL_SCHEMA_BUDGET", ""),
            "local_tool_schemas": os.environ.get("KASUKABE_LOCAL_TOOL_SCHEMAS", ""),
            "trace_db": os.environ.get("TRACE_SQLITE_PATH", ""),
            # PROG-01/02. The four program ceilings, as RESOLVED - the value
            # `world/tools/program.py` is actually enforcing, not the raw
            # env string. `KASUKABE_PROGRAM_MAX_CALLS` is not a safety knob
            # like the other three; docs/decisions/PROG-COST-MODEL.md calls
            # it "the answer to how many acts may one decision buy", and P1
            # is reported against it. A run whose acts-per-decision figure
            # has a ceiling nobody recorded is a figure nobody can compare -
            # the manifest already carried `full_schema_budget` for the
            # same reason and these were missing beside it.
            "program_max_calls": _program_caps["max_calls"],
            "program_max_steps": _program_caps["max_steps"],
            "program_max_repeat": _program_caps["max_repeat"],
            "program_wallet_share_percent": _program_caps["wallet_share_percent"],
            # The same kind of knob for converting sim to energy: one call may
            # not take more than this share of a resident's sim (run 26,
            # Giselle Ingram, 4,900 of 4,900).
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
            # Local write is authoritative (resume reads only this) and
            # must never fail silently - see module docstring point 4.
            local_path = self.checkpoint_store.write(checkpoint)
            await mirror_checkpoint_to_shared_store(
                checkpoint,
                local_path,
                bucket=self.config.supabase_bucket,
                # INFRA-01: the store this run is actually writing to. Without
                # it the mirror opened ":memory:" and reported success.
                client=self.trace_store.client if self.trace_store is not None else None,
            )
            # INFRA-10: the day's Parquet slice, from the one place that
            # knows a day has ended.
            #
            # `research/parquet_export.export_game_day` was written for
            # exactly this and had NO CALLER anywhere - no scheduler, no
            # cron, no systemd timer - so the research surface the roadmap
            # is built on has never had a single file written to it. This
            # is the sixth complete-and-unwired mechanism this review has
            # found, and the pattern is now the project's most common
            # defect by a wide margin.
            await self.export_the_day(checkpoint.game_day)
            logger.info(
                "checkpoint written: tick=%s game_day=%s -> %s",
                checkpoint.tick_index,
                checkpoint.game_day,
                local_path,
            )

        return sink

    # -- launching the 250 engines ---------------------------------------

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

        # Keeps prompts from growing without bound for the length of the run.
        # A five-hour run without it ends at 90.9% dispatch and 630 KB
        # requests; the same purge run by hand once took prompts from 643 KB
        # back to 97.5 KB. See world/signal_groundskeeper.py for why this is
        # a groundskeeper rather than the two-line fix in frozen code.
        from world.signal_groundskeeper import SignalGroundskeeper

        self.groundskeeper = SignalGroundskeeper(self.config.home_root)
        self.groundskeeper.start()

        # What engines actually cost on THIS machine, saved for the next
        # preflight. Without it a fresh boot falls back to a constant
        # measured on another platform - which refused a ten-engine run here
        # at "5500 MB needed" while the same engines run in a third of that.
        async def _remember_engine_memory() -> None:
            from world.memory_budget import measured_mb_per_engine, remember_measurement

            await asyncio.sleep(120)  # let them finish booting and settle
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
            # One process, one SEPTEMBER_HOME per agent. See
            # world/namespace_supervisor.py for the measurement that makes
            # this the default: ~110 MB per engine as a separate process
            # versus 2.5 MB namespaced, which is the difference between a
            # 50-agent run and a laptop that stops responding.
            #
            # No ports: a tick goes straight into the engine object, so this
            # also drops 50 listening sockets and a round trip per agent per
            # tick.
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
                # Constructed with an empty roster; sample_resources reads
                # the live pids off the supervisor on every tick, because
                # a restarted engine has a new one.
                self.resources = ResourceSampler(self.config.resource_log_path, {})
                logger.info(
                    "per-agent CPU and memory -> %s", self.config.resource_log_path
                )
            for index, agent_id in enumerate(agent_ids):
                self.ports_by_agent[agent_id] = self.config.sim_agent_port_base + index
            # The SAME budget the tick loop is willing to wait, not the
            # gateway's own 30s default.
            #
            # These were two different numbers: the loop guarded each tick
            # with asyncio.wait_for(..., tick_timeout_s=120) while the HTTP
            # client underneath gave up at 30. The inner one always won, so a
            # turn that honestly needed 40 seconds was thrown away as an
            # httpx.ReadTimeout - which carries no message, so it logged
            # "tick dispatch failed for n07: " and nothing else - while the
            # engine finished the work and returned 200 OK to a caller that
            # had already left. The outer guard never fired, so
            # `timed_out_ticks` sat at 0 and the run reported itself healthy.
            #
            # Measured: Marta Thorne, whose perception is the longest in the
            # cast at 1175 chars against a median of ~700, lost every single
            # turn of a 19-tick run this way - 18 cancelled sessions, no
            # actions, while the other nine residents completed all of theirs.
            self.gateway = HttpAgentGateway(
                self.ports_by_agent,
                timeout_s=self.config.tick_timeout_s,
                output_tail=self.engine_output_tail,
            )

            # RC-1 (ROADMAP.md section 1.2): pinned_boot.boot_sim_engine
            # has no genesis.json access of its own (it only ever sees
            # env vars), so this is the one place with both the real
            # engine process about to start AND this agent's full
            # RichGenesisAgent record. Writing the rendered persona +
            # law-of-the-town text to a fixed path under this agent's own
            # SEPTEMBER_HOME (engine/src/sim/config.py's
            # `SimProfile.september_home`, `<home_root>/<agent_id>`)
            # needs no new env var - pinned_boot.py already knows both
            # home_root and agent_id and can read the same path back.
            # Mock mode never spawns a real engine process (MockSupervisor
            # fakes ticks entirely), so there is nothing to seed.
            from world.life.soul_seed import build_persona_prompt, build_soul_seed

            assert self.genesis is not None
            genesis_by_id = {a.agent_id: a for a in self.genesis.agents}
            for agent_id in agent_ids:
                rich_agent = genesis_by_id[agent_id]
                # CORE-16: the town roster belongs HERE, in the stable
                # seed that is written once, not in the standing brief that
                # is rebuilt and re-sent on every tick.
                #
                # Who else exists is a fact about the town, not about this
                # moment, and it never changed between two ticks in any run
                # so far. Sending it per tick costs O(residents) tokens
                # per resident per tick: about 310 tokens a tick on the
                # 50-agent cast and roughly 1,550 at the 250-resident
                # design target - which over one 144-tick game day is tens
                # of millions of tokens spent re-stating a list that did
                # not move.
                # ORDER IS LOAD-BEARING, and the roster goes in the MIDDLE.
                #
                # `test_the_system_prompt_is_byte_identical_to_persona_dot_txt`
                # pins it: composed the other way round the verb-reach
                # harness scored eight fewer scenarios and `trade` fell from
                # 80% reach to 40%, because a prompt ending on "what you hold
                # to" leaves a model composing a character and one ending on
                # "only a tool call reaches the world" leaves it acting. The
                # roster appended LAST would end every resident's prompt on a
                # list of names - the same mistake in a new place, and that
                # test caught exactly this while CORE-16 was being fixed.
                persona_text = "\n\n".join([
                    build_persona_prompt(rich_agent),
                    self.town_roster_text(agent_id),
                    build_soul_seed(rich_agent),
                ])
                agent_home = Path(self.config.home_root).expanduser() / agent_id
                agent_home.mkdir(parents=True, exist_ok=True)
                (agent_home / "persona.txt").write_text(persona_text, encoding="utf-8")
                # Only now may the brief stop repeating it: this resident
                # has been told, durably, in a place it will read at boot.
                self.roster_seeded.add(agent_id)
                self.roster_seen_by[agent_id] = {
                    other_id
                    for other_id, other in self.world_state.agents.items()
                    if other.alive and other_id != agent_id
                }

        # ── Preflight: fifteen checks, and a refusal if any of them fail ──
        #
        # A five-hour run that dies at minute forty because the broker had no
        # credentials, or that finishes having proved nothing because
        # `skillify` was never registered, is worse than a run that refuses to
        # start. Every check corresponds to something that actually went wrong
        # in this project at least once.
        #
        # KASUKABE_SKIP_PREFLIGHT=1 overrules it, deliberately awkward to
        # type: an operator who means it can, and nobody does it by accident.
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

        # ── Will these engines fit in this machine's memory? ────────────
        #
        # Checked BEFORE the first spawn, because the failure it prevents is
        # not a clean one. Forty engines on an 8 GB laptop do not stop at
        # engine 12 with an error - the machine swaps, everything slows to a
        # crawl, and the OOM killer takes whichever process it likes, often
        # the world server. The run then dies looking like a world bug.
        #
        # Set KASUKABE_SKIP_MEMORY_CHECK=1 to proceed anyway: the estimate is
        # deliberately conservative and an operator who knows their machine
        # should be able to overrule it.
        # Only when something will actually be spawned. MockSupervisor fakes
        # ticks entirely and starts no processes, so applying a memory budget
        # to it would fail tests over memory nobody is going to allocate.
        spawns_real_processes = not isinstance(self.supervisor, MockSupervisor)
        # Same three sources preflight uses, in the same order: engines
        # running now, the last reading taken on this machine, then the
        # constant. Preflight was taught this and THIS guard was not, so a
        # run passed the checklist and was then refused two seconds later by
        # a second opinion nobody had updated - "10 engines need about 5500
        # MB" on a machine that had just measured them at 70 MB each.
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
                # The engine reads the broker from BROKER_BASE_URL /
                # BROKER_API_KEY (engine/src/sim/sim_main.py's
                # profile_from_env, which hard-fails when they are absent).
                # Passing them only as CDT_URL/CDT_KEY - herald's own names,
                # set by ProcessSupervisor.start - killed all 250 engines at
                # startup with "missing required env var(s): BROKER_BASE_URL"
                # while the runner reported "tick dispatch failed" for every
                # agent and spend sat at $0.
                "BROKER_BASE_URL": self.config.broker_base_url,
                "BROKER_API_KEY": sign_broker_token(agent_id, self.config.broker_api_key),
                # Evaluation spec section 5.2's within-town control arm.
                #
                # `genesis.py` has assigned every resident an arm since it
                # was written - 190 full, the rest split three ways - and
                # NOTHING HONOURED IT. A `no_learning` resident ran the
                # learning gate exactly like everybody else, so the field
                # was a label on a resident that behaved identically, and
                # C1 was being reported against a control that did not
                # differ from the treatment in any way.
                #
                # The spec is blunt about what that is worth: "a learning
                # claim without a control arm is a correlation". This is
                # the arm becoming real.
                "KASUKABE_ABLATION_ARM": self.ablation_arm_for(agent_id),
            }
            spec = UnitSpec(
                unit_id=agent_id,
                engine_id=agent_id,
                cdt_url=self.config.broker_base_url,
                # Signed for the same reason BROKER_API_KEY above is: herald
                # sends CDT_KEY as its bearer credential, so an unsigned value
                # here fails broker auth even when BROKER_API_KEY is correct.
                cdt_key=sign_broker_token(agent_id, self.config.broker_api_key),
                extra_env=extra_env,
            )
            status = await self.supervisor.start(spec)
            if status.state is UnitState.FAILED:
                failures.append((agent_id, status.detail))
                logger.error("unit %s failed to start: %s", agent_id, status.detail)

        # The count is of units that BOOTED, not of units attempted. The old
        # line said "launched 30 agent units" after all thirty failed with
        # ModuleNotFoundError, and the run went on to tick an empty town for
        # as long as anyone let it.
        booted = len(agent_ids) - len(failures)
        logger.info("launched %d of %d agent units (mock_mode=%s)",
                    booted, len(agent_ids), self.config.mock_mode)
        if failures:
            reasons = sorted({(d or "unknown").splitlines()[0][:120] for _, d in failures})
            logger.error("%d unit(s) did not start: %s", len(failures), reasons)
        if not booted and not self.config.mock_mode:
            # Nothing thinks. Every tick from here is a town of statues, and
            # every panel reads exactly as it would for a healthy run.
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
        # Before any turn has run. Deliberately conservative: pacing too
        # slowly costs wall clock, pacing too fast costs a queue that looks
        # like the broker failing.
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

            # INFRA-18. A halted recorder is terminal too.
            #
            # When both of the broker's BatchWriters halt, every
            # `/v1/messages` returns 503; herald retries 5xx three times and
            # the turn fails. The runner only learned about it as twenty
            # consecutive barren ticks - up to forty minutes of a town not
            # thinking, and every one of those failed calls still a round
            # trip. `/admin/trace` has reported `dispatch_halted` since it
            # was written and nothing asked it.
            #
            # Second request, on the same already-failing tick, so a healthy
            # run still adds nothing.
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

        # Mock mode never calls Bedrock, so there is no capacity to respect
        # and nothing to pace against. Returning early also keeps the test
        # suite fast: every booting test would otherwise pay the probe's
        # timeout against a broker that is not running (measured: 8s -> 99s
        # across world/tests/test_run_genesis.py).
        if self.config.mock_mode:
            return max(explicit, 0.0)

        capacity = await self.probe_broker_capacity()
        due_per_tick = max(1.0, agent_count * self.config.scheduler_target_fraction)
        # CORE-12 / INFRA-12. A turn is not one call.
        #
        # `safe` used to be `due_per_tick * 60 / capacity`, which prices a
        # resident's whole turn at ONE request. A turn may make up to
        # `MAX_CALLS_PER_TICK` model calls plus a post-turn summariser, and
        # run 23 measured 2-12. So at 250 residents and cadence 0.3 the real
        # demand is ~83 turns x ~5 = 415 calls a tick against 600 RPM, and
        # the interval computed here was out by that whole factor - the town
        # was paced to outrun its own broker and the queueing looked like
        # Bedrock being slow.
        #
        # Measured, not assumed: `calls_per_turn_estimate` averages the
        # `steps_used` the engines have actually reported, so a town of
        # quiet residents is not paced as though every one of them thinks
        # twelve times. Falls back to a conservative constant before any
        # turn has run.
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
                # CORE-33. All of them at once, not one after another.
                #
                # This looped `for agent_id in sorted(pending)` with a 2 s
                # client timeout on each, so a pass over 250 booting engines
                # could take 250 x 2 s = eight minutes - and the whole
                # readiness deadline is usually shorter than that, which
                # means at 250 residents the loop could not finish even one
                # pass before giving up and declaring everybody absent.
                #
                # `preflight.py:236` already parallelises its own port scan
                # for exactly this reason.
                answered = await asyncio.gather(
                    *(probe(agent_id, client) for agent_id in sorted(pending))
                )
                ready.update(a for a in answered if a)
                pending -= ready
                if pending:
                    await asyncio.sleep(1.0)

        if pending:
            # Name a few, and say what the supervisor captured for one of
            # them - a bare count sends the operator hunting through 250
            # processes for output nothing was reading before this.
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
            # "engine" = ProcessSupervisor + HttpAgentGateway (real
            # sim_main subprocesses). "mock" = MockSupervisor +
            # MockAgentGateway, entirely in-process. This is the only
            # place that knows, so it is the only place that can say.
            cognition="mock" if self.config.mock_mode else "engine",
            broker_base_url=self.config.broker_base_url,
            engine_liveness=self.engine_liveness,
            engine_output=self.engine_output,
            # WIRE-02: the consumer for the link counters, as a route.
            # Kept on `self.world_app` below so a test can exercise the
            # ROUTES this runner really built - the previous link test
            # asserted on the factory's source text and so never noticed
            # that `/world/link` raised NameError on every request.
            wire_health=self.collect_wire_health,
            recorder_stats=self.trace_store.stats if self.trace_store is not None else None,
            # GROW-06 / UI-01 / UI-18: land approval, `/map/tiled` and the
            # places geometry all resolve through this one object.
            map_service=self.map_service,
            # UI-02 / CORE-26: the reasoning viewer reads `llm_calls`,
            # which the broker writes into the store this runner already
            # opened for the recorder.
            trace_client=self.trace_store.client if self.trace_store is not None else None,
            # UI-16: this run's cap, from the config the governor uses.
            run_cap_usd_cents_getter=lambda: self.config.budget_cap_cents,
        )
        # The app this runner actually built, with this runner's callables
        # bound into it. Held so the routes can be exercised as routes.
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

    # -- the tick loop -----------------------------------------------------

    async def run_tick(self) -> TickResult:
        assert self.tick_engine is not None and self.world_state is not None
        assert self.gateway is not None and self.metrics is not None and self.governor is not None

        # STATUS.md risk #2 ("console-contract" task)'s "a real total
        # freeze: ticks halt at a boundary, broker holds dispatch,
        # timestamp frozen". world/admin_api.py's POST /admin/control/pause
        # flips self.sim_control (the SAME instance, not a copy - see
        # __init__'s own comment) and clears its resume event; blocking
        # HERE, before tick_engine.tick() ever runs, is what actually
        # delivers that guarantee: no new tick starts (so world.current_tick
        # stays exactly where it was - "timestamp frozen"), and since no
        # tick means no due_agents and no dispatch_due_agents() call below,
        # "broker holds dispatch" falls out for free without broker needing
        # to know anything changed. A tick already in flight when pause is
        # requested still finishes (halting AT a boundary, not mid-flight
        # with agents half-dispatched) - run_burst/run_continuous both loop
        # through this same method, so one wait point covers both pacing
        # modes.
        await self.sim_control.wait_if_paused()

        # Live-tunable depth (module docstring / retrieval_depth.py): the
        # very next tick honors whatever fraction is currently set.
        self.tick_engine.perception.hearing_window_ticks = self.depth.hearing_window_ticks

        result = await self.tick_engine.tick()
        self.world_state.current_tick = result.tick.tick_index

        await self.dispatch_due_agents(result)
        await self.run_life_ticks(result.tick)

        if self.config.checkpoint_every_day and result.checkpoint is not None:
            pass  # sink already ran inside tick_engine.tick() -> checkpoints.write()

        self.metrics.record_burn_sample()
        self.sample_resources(result.tick.tick_index)
        # So a run that is KILLED still left a roster, its refusals and the
        # tools it was serving behind it - see `capture_reading_if_due`.
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
        # SG7: and the residents' own records, for the run that is killed
        # before the stop path can archive them. Off the event loop - a
        # compressed copy of fifty homes is seconds, not a tick's worth.
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
        # Read fresh: the watchdog restarts silent engines and a restarted
        # engine has a new pid. A cached roster reports a process that has
        # gone, which is how a stale pointer always fails.
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

        # The integer division above loses the remainder. Give it to the
        # largest share rather than dropping it: a town whose debts do not
        # add up to the money it did not receive cannot be reconciled.
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
        # An interrupt raised at night is still an interrupt.
        if world.wake_reasons.get(agent_id):
            return False
        # The first night tick is the onset edge: one turn to wind down.
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
        # Read directly, not through getattr defaults: both are real fields,
        # and `mark_the_broke_dormant` a few hundred lines below reads them
        # exactly this way. A getattr default here would answer "not dormant"
        # for a renamed field and quietly restore the bug.
        return (not agent.alive) or agent.dormancy.is_dormant

    async def dispatch_due_agents(self, result: TickResult) -> None:
        assert self.gateway is not None and self.tick_engine is not None
        assert self.world_state is not None

        async def one(agent_id: str) -> None:
            # B1's scheduler change, and the whole economics of the verb:
            # a resident under a continuing action is not woken to think.
            # The world runs its next repetition instead, through the same
            # dispatch() at the same cost, and a farmer's afternoon becomes
            # twenty repetitions and one model call rather than twenty of
            # each. Nothing here is skipped once a wake reason is set - the
            # reason IS the next perception's first line.
            assert self.world_state is not None
            # INFRA-03: a dormant resident is not dispatched at all.
            #
            # Dormancy means the wallet hit zero: the broker refuses the
            # call, the turn ends in a refusal, and the resident is asked
            # again next tick. Build Spec 4.5 allows one refusal per broke
            # resident; what actually happened was one every third tick for
            # the rest of the run, each costing a perception build, a
            # dispatch and a log line. Nothing was learned from any of them,
            # because there was nothing to learn - the resident had no money.
            #
            # Skipping here rather than inside the gateway is deliberate:
            # the perception below is the expensive part, and it is built
            # before the broker ever sees the call.
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
            # A turn is beginning. `tool_invocations` already records every
            # call with its actor and tick; this is the other half, without
            # which "did anything happen last turn" cannot be asked. Noted
            # BEFORE the perception is built, so silent_turns_report reads a
            # window that ends here and covers the turn that just finished.
            self.world_state.note_turn(agent_id, timestamp.tick_index)
            # ENG-24: pressures read, and any goal they spawn created,
            # before the prompt that will carry them is assembled.
            await self.refresh_goals_for(agent_id, timestamp.tick_index)
            text = (
                render_perception_text(
                    perception,
                    depth=self.depth,
                    visible_agent_bios=self.agent_bios,
                    # Names, so an agent can address the person beside it.
                    # `say` still needs the id, which _named() keeps.
                    visible_agent_names=self.agent_names,
                    # Who this agent is, what its trade is, where its work
                    # is, what it holds and whether it has eaten. Without
                    # this the prompt was the time and a list of ids.
                    self_brief=self.standing_brief(agent_id),
                    # Part B: the agent's own procedures, with its place in
                    # them marked. Without this a skill is a discount that
                    # happens TO an agent rather than a plan it is following.
                    skill_playbook=self.world_state.skill_playbook(agent_id),
                    authoring_hint=self.world_state.authoring_hint(agent_id),
                    # Questions somebody is still waiting on. The hearing
                    # window is four ticks and the scheduler wakes an agent
                    # every three, so a directed question used to be visible
                    # for about one turn: 56 of 81 exchanges ended after a
                    # single remark, and 2 of 11 direct questions were
                    # answered. This outlives the window and expires on its
                    # own clock.
                    open_questions=self.world_state.open_questions(
                        agent_id, timestamp.tick_index
                    ),
                    # And what this resident asked that nobody answered -
                    # the signal whose absence had residents repeating
                    # themselves word for word. See awaiting_reply.
                    awaiting_reply=self.world_state.awaiting_reply(
                        agent_id, timestamp.tick_index
                    ),
                    # A6, the volatile line: what this resident's own
                    # thinking costs and how long its money lasts.
                    # cost_disclosure has computed all of it since it was
                    # written and rendered it nowhere, so no resident has
                    # ever been told its runway - which is what makes
                    # `buy_energy` a verb with a felt need behind it
                    # rather than a curiosity.
                    wallet=self.wallet_line(agent_id, timestamp.tick_index),
                    # Why the resident stopped working, as the first thing
                    # it reads. A resident that stops and does not know why
                    # has been handed a gap in its own morning.
                    stopped_working=self.world_state.wake_reasons.pop(agent_id, None),
                    # The conversation this resident is standing in, if any.
                    # Atlas announced its group session to every member
                    # (router.announce_group); this is that announcement,
                    # arriving as perception rather than as a tool result,
                    # because `look_around` shows the same fact and a
                    # resident who never looks never learns it. It names the
                    # subject, who is in it and its id, and gives no orders -
                    # see docs/A2A_CONFORMANCE.md section 4 for why the
                    # driving half is deliberately not ported.
                    conversation=self.world_state.conversation_here(
                        agent_id, timestamp.tick_index
                    ),
                    # What happened LAST turn, when nothing did. Measured
                    # in run short1: 26 of 71 turns produced a tool call,
                    # and n06 wrote three consecutive scenes in prose with
                    # actions [] - a direct question to Marta among them,
                    # while she was standing next to him. Law rule 6 and
                    # the turn-close line both told him the rule and both
                    # were read; what nothing told him was that the last
                    # one had reached nobody.
                    silent_turns=self.world_state.silent_turns_report(agent_id),
                    # A3. What is priced where this resident stands, and
                    # what the people beside it are selling. Nothing is for
                    # sale until somebody posts, so without these two a
                    # posted price is invisible unless a resident spends a
                    # `look_around` on the chance that one exists.
                    prices_here=self.prices_for(agent_id),
                    market=self.market_for(agent_id),
                    visible_agent_offers=self.offers_beside(agent_id),
                    # B2. What this resident set out to do, or what became
                    # of it. Read once and cleared, so an outcome line
                    # arrives on exactly one turn.
                    intention=self.intention_line(agent_id),
                    intention_outcome=self.intention_outcome_line(agent_id),
                    # ENG-24. What this resident wants, what is pressing
                    # on it, and what it has learned by being refused.
                    # Without these three a resident arrived at every
                    # tick with no record of its own life and re-derived
                    # the whole thing from its persona, which is most of
                    # what the reports called "thinking instead of
                    # living".
                    goals=self.goal_lines(agent_id),
                    needs=self.need_lines(agent_id),
                    beliefs=self.belief_lines(agent_id),
                    goal_outcomes=self.goal_outcome_lines(agent_id),
                    # LIFE-01. What this resident IS, now - the fact its
                    # goals hang off, and the one the prompt never
                    # carried.
                    life_stage=self.life_stage_line(agent_id),
                    # GOV-17. What the town's institutions are doing, as
                    # facts. `vote` and `declare_candidacy` both require an
                    # `election_id` that nothing in the town would ever
                    # tell a resident, so both were unusable even in a run
                    # where the seats had been filled.
                    civic_notice=self.civic_notice_for(agent_id),
                    # And, when there is none but the room has arrived at the
                    # moment one would help, the line that names the verb.
                    # `start_conversation` shipped reachable to twenty engines
                    # across runs 18 and 19 and was called zero times - the
                    # same result `skillify` got, for the same reason, fixed
                    # the same way.
                    convening_hint=self.world_state.convening_hint(agent_id),
                    # Part D: closes the loop from the observatory's gaps view
                    # back to the agents who hit it. Rate-limited and logged -
                    # see world/gap_feedback.py for why both are mandatory.
                    gap_notice=(
                        self.world_state.gap_feedback.notice_for(self.world_state, agent_id)
                        if self.world_state.gap_feedback is not None
                        else None
                    ),
                )
                if perception is not None
                else ""
            )
            # CORE-32: a dispatch was ATTEMPTED. Recorded before the
            # await, so a timeout or a dead broker still counts as an
            # attempt - the halt gate below must keep firing for those.
            self._tick_had_a_dispatch = True
            try:
                outcome = await asyncio.wait_for(
                    self.gateway.send_tick(
                        agent_id, tick=timestamp.tick_index, is_sleeping=is_sleeping,
                        perception=text,
                        # B3. One resident's tick was measured at fifty
                        # model calls; two residents with the same brief
                        # differed eightfold in what a morning cost.
                        call_budget=self.call_budget_for(agent_id),
                        # ENG-24: the world's own day number, so a night's
                        # reflection happens once a night. The engine can
                        # derive it, but deriving it means a second clock.
                        game_day=timestamp.game_day,
                    ),
                    timeout=self.config.tick_timeout_s,
                )
                # CORE-11: the engine refused because it is still running
                # the previous turn. Nothing was spent and nothing is
                # broken, so this is neither a success nor an engine
                # failure - it is the same "skip this resident" the
                # timeout-based guess arrived at, reached in milliseconds
                # and by being TOLD rather than by assuming.
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
                    # CORE-12: what the turn actually cost, for the pacing.
                    if outcome.steps_used:
                        self.recent_steps_used.append(int(outcome.steps_used))
                if outcome.budget_hit:
                    self.budget_hit_last_turn[agent_id] = outcome.steps_used
                await self.settle_outcome_cost(agent_id, outcome, timestamp.tick_index)
            except asyncio.TimeoutError:
                # Counted, not just logged. A timed-out turn has already paid
                # for every call it made and produced nothing, so a run where
                # this is common is burning money to no effect - and 41 lines
                # in a log nobody greps is not a signal.
                self.timed_out_ticks += 1
                # INFRA-29: and do not immediately ask again.
                #
                # uvicorn does not cancel a handler when its client
                # disconnects, so the engine is STILL RUNNING this turn and
                # the broker is still billing every call it makes. The next
                # tick for this resident blocks on the engine's own
                # `_turn_lock` behind that turn, times out after another
                # full `tick_timeout_s`, and the pattern repeats: one slow
                # resident becomes a column of consecutive timeouts, none
                # of which could ever have succeeded. Ten turns went this
                # way in run 23, after paying.
                #
                # Backing off does not recover the money already spent -
                # nothing in `app/` can, since the cancel would have to
                # reach frozen engine code. What it stops is the pile-up:
                # the resident is left alone until the turn it is actually
                # running has had time to finish.
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
            # INFRA-29: skip the residents whose previous turn is probably
            # still running. Dispatching to them serialises on the engine's
            # turn lock and buys another full timeout for nothing.
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
                # One resident's bad state must not end the town.
                #
                # Without `return_exceptions` a single AttributeError in
                # `one()` propagates out of the gather, out of the tick,
                # and kills the runner - which is exactly what happened
                # eight minutes into the 50-agent run on 2026-09-08. A
                # resident committed to work "until dusk",
                # `ContinuingAction.satisfied` called a `world.is_daytime()`
                # that did not exist, and all fifty engines died while the
                # broker kept billing at $10.77/hour for a town that was
                # no longer being simulated.
                #
                # The same rule the day boundary already follows: "one
                # household must not take the day down for the other
                # sixteen". Counted and logged at WARNING, never swallowed
                # - a turn that vanished quietly would read as a resident
                # choosing to do nothing, which is the measurement this
                # whole experiment is about.
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

        # EVAL-15: the control businesses take their turn too.
        #
        # `ControlBakery.tick()`'s own docstring says it "is called once
        # per world tick by the runner, in the same place
        # `dispatch_due_agents` would have spent a model call" - and no
        # caller existed anywhere outside its own module. So R1-R7, the
        # whole point of having a rule-run business to compare an
        # agent-run one against, have never been computed on any run.
        #
        # Here rather than inside `one(agent_id)`: a control has no engine
        # and no wallet to time out, so it is not a resident being
        # dispatched to - it is the baseline the residents are measured
        # against, and it acts whether or not any resident was due.
        await self.act_the_controls(result.tick.tick_index)

        # EVAL-25: mirror what those turns actually called. Every tick,
        # because the ring holds 5,000 records and drops the oldest - a
        # drain that ran less often than the town fills it would lose the
        # start of every busy run.
        await self.drain_tool_invocations()

        self.count_the_barren_tick(result)


    def count_the_barren_tick(self, result: TickResult) -> None:
        """Whether this tick counts against the CORE-32 halt.

        Split out of `dispatch_due_agents` so it can be driven
        directly: the difference between a sleeping town and a dead
        one is a single boolean, and it stopped a 5-hour run at fifty
        minutes.
        """
        #
        # A tick where every dispatch failed is a tick that changed nothing
        # and cost wall-clock, and a RUN of those is a town of statues that
        # still reports 30/30 engines alive and a rising tick number. It has
        # happened twice: a broker refusing every call after its spend cap
        # tripped (10,465 refusals over 44 minutes), and thirty engines that
        # died on a port collision while the world ticked on above them.
        #
        # Counted here rather than inferred from the trace store, because
        # this is the one place that knows whether a dispatch came back.
        # "Nobody answered" and "nobody was asked" are different towns.
        #
        # This counted a tick as barren whenever anyone was DUE and nothing
        # succeeded - but `one()` returns before dispatching for three
        # legitimate reasons: the resident is dormant, is mid-way through a
        # continuing action, or is ASLEEP. At 1am every resident is asleep
        # (CORE-17), so every night tick was barren and the halt fired
        # twenty ticks into the first night.
        #
        # That is exactly what killed `deni-1c8733e-5h` at tick 152 - hour
        # 01:00 on day 1 - after a run in which all 4,580 HTTP calls
        # returned 200 OK and not one dispatch had failed. CORE-17's own
        # docstring says "no run in 23 has ever reached tick 144", so until
        # night became cheap enough to reach, this could not fire. Two
        # fixes, each right on its own.
        #
        # The halt still fires for every case it was built for - a dead
        # broker, a tripped spend cap, engines that never booted - because
        # all of those ATTEMPT a dispatch and fail. Only a tick where
        # nothing was even asked is now exempt.
        if result.due_agents and self._tick_had_a_dispatch:
            if self._tick_had_a_success:
                self.barren_ticks = 0
                # CORE-32: the wall-clock half of the halt. Cleared with the
                # counter so a recovered run does not carry an old start.
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

        total_hours = timestamp.tick_index * (10.0 / 60.0)  # TICK_MINUTES / 60
        # CORE-06 / ECON-11. This built a FRESH PopulationDials every tick
        # and handed it to every death roll and to upkeep, so
        # `self.population_dials` - the object `PUT /admin/god/population-
        # dials` mutates, and the only thing the operator console can turn -
        # was read by nothing in the live loop. Setting
        # `death_hazard_multiplier=0` from the console changed the field and
        # not the town.
        dials = self.population_dials

        # ── Running out of thinking has to actually happen ───────────────
        #
        # EVERY tick, not hourly: a resident whose wallet empties at 09:10
        # must not keep thinking until 10:00 on money it does not have.
        #
        # `life/death.py::mark_dormant` says in its own first line "Called
        # by the ledger when a debit leaves an agent at $0" and has never
        # had a caller. So `is_dormant` was never set by anything, `revive`
        # would have had nobody to wake, and `check_dormancy_death` - which
        # the hourly block below has been running all along - could not
        # return True however long a run went on. The whole chain from
        # cognitive poverty to death was unreachable, and every report that
        # said "no dormancies" was describing a missing call site.
        await self.mark_the_broke_dormant(timestamp)
        await self.release_served_sentences(timestamp)
        await self.finish_what_the_town_built(timestamp)
        await self.charter_tick(timestamp)

        # ── The land has to come back ────────────────────────────────────
        #
        # GROW-24 / TOOL-20. `regenerate_zones` is complete, tested, and its
        # docstring names the caller it never got: "meant to be called once
        # per tick by world's (not yet built) tick loop". The one caller it
        # does have is `WorldServer.advance_tick`, on the host every
        # launcher declines to boot - so across twenty-four runs a harvested
        # strip and a mined outcrop never recovered.
        #
        # Harmless so far only because DEFAULT_ZONE_YIELD is 10,000 and no
        # run has lasted long enough to exhaust one. It becomes the reason a
        # five-day run starves.
        regenerate_zones(self.world_state)
        await self.close_the_veto_window(timestamp)

        if total_hours - self.counters.last_life_hour_processed >= 1.0:
            elapsed = total_hours - self.counters.last_life_hour_processed
            now = LifeGameClock(total_hours=total_hours)
            for agent_id, agent in list(self.world_state.agents.items()):
                if not agent.alive:
                    continue
                agent, life_events = aging.advance_age(agent, elapsed)
                # A8 (section 26): aging fires agent.aged_to_adult and stops.
                # Until this call existed nothing consumed that event, so a
                # minor crossing eighteen changed nothing at all - the parent
                # kept paying for their thinking and the household never moved.
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

            # ── Living costs money, every day, whether you earned or not ──
            #
            # world/life/upkeep.py implements DESIGN.md section 3 in full -
            # food, shelter and heat billed to the HOUSEHOLD, then mild
            # hunger, then a malnutrition ailment, then dormancy, then death,
            # all reusing the existing health machinery. Its own docstring
            # asks for "the exact one-line hook into world's (not-yet-built)
            # daily tick loop". Nothing ever called it.
            #
            # Without it the town has no stakes. Observed at game-day 3: two
            # residents at zero USD, unable to think at all, still reported
            # alive and well; nobody's sim balance had moved; nobody had any
            # reason to earn, eat, trade or write down a skill. DESIGN.md
            # names this exactly - "without it an agent could minimise cost
            # by doing nothing, and idleness would be the winning strategy.
            # Upkeep makes standing still expensive, so doing nothing is
            # also a way to die."
            await self.run_daily_upkeep(timestamp, dials)

            # ── A7: the other three day jobs, none of which had a caller ─
            #
            # `ledger/head_tax.py::charge_daily_head_tax` is complete, has
            # its own tests, and its docstring says plainly "call this once
            # per household per completed game-day". Nothing did. Nobody in
            # this town has ever paid for existing, so the tax rate was not
            # a political question and the complaints desk had nothing to
            # hear.
            #
            # Wage reconciliation is new with A5. Both run BEFORE the
            # halt check below, so a job that throws is a stopped run
            # rather than a quietly untaxed day.
            await self.charge_the_day_tax(timestamp)
            # GOV-08. Wages before pensions, for no deeper reason than
            # that a town pays the people doing the work first; both are
            # treasury spending and both were absent entirely.
            await self.pay_the_town_payroll(timestamp)
            await self.pay_the_pensions(timestamp)
            await self.reconcile_wages(timestamp)
            # B2: an intention that outlives its day stops being a purpose
            # and becomes furniture in the prompt. What it leaves behind is
            # one line on the resident's next turn saying it lapsed.
            for agent_id, lapsed in self.world_state.expire_intentions():
                logger.debug(
                    "day %d: %s did not do what they set out to: %s",
                    timestamp.game_day, agent_id, lapsed.text,
                )
            # ENG-24, immediately after, for the identical reason and on
            # the identical schedule. `now` and `today` goals do not
            # survive the night; `week` and `life` goals do, which is the
            # only thing separating a horizon from a longer word.
            #
            # Each expiry is published, not merely logged: "how many of
            # yesterday's goals died unfinished" is the measurement this
            # whole substrate exists to make answerable, and a report
            # cannot count what only reached the debug log.
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
        # GOV-09: the eligibility age is the charter's, and the loop has
        # to use the same one the payment enforces or a 65-year-old under
        # a lowered retirement age is skipped here and never reaches it.
        retirement_age = PENSION_MINIMUM_AGE
        try:
            retirement_age = (await world.charter.store.get_policy()).pension_eligibility_age
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not read the retirement age: %s", exc)
        for agent_id, agent in list(world.agents.items()):
            if not agent.alive or agent.age_years < retirement_age:
                continue
            # ECON-09, second defect: this asked the wrong question of
            # the right dict.
            #
            # `wage_promises` is keyed "{business_id}|{employee_id}"
            # (`labour.py:112`), and this looked up a bare agent id. The
            # lookup could NEVER hit, so every retiree fell to the
            # `pensions_without_a_salary` counter below and NO PENSION HAS
            # EVER BEEN PAID on the embedded runner - while ECON-09 read
            # as fixed, because `pay_the_pensions` was wired and running
            # and doing nothing.
            #
            # The highest wage promised to this resident, not the first
            # found: somebody who worked two jobs retires on the better of
            # them, and dict order is not a pension policy.
            last_salary = max(
                (
                    promise.wage_sim
                    for key, promise in world.wage_promises.items()
                    if key.split("|", 1)[-1] == agent_id
                ),
                default=0,
            )
            if last_salary <= 0:
                # Nothing to take a quarter of. Counted, so a town where
                # every retiree draws nothing is visible as that rather
                # than as a pension system nobody noticed was idle.
                self.pensions_without_a_salary += 1
                continue
            # GOV-09. What the town's own policy says, not what a module
            # constant says. `pension_due_sim` and `pension_employment_type`
            # are the reading end of those levers and had no caller, so a
            # retired constable drew a private-sector pension and an
            # amendment to the rate changed nothing anybody was paid.
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
                # A charter that cannot be read is not a reason to stop
                # paying pensions - it is a reason to pay them at the
                # default rate and say so.
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
                # a fact about the treasury, not a reason to end the tick.
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
            # A confined resident earns nothing (`crime/outcomes.py`:
            # "no income"). Skipped rather than paid-and-clawed-back, so
            # the trace shows a sentence costing somebody their wages
            # rather than a wage and a mysterious reversal.
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
                # fact about the town worth seeing, not a reason to end a
                # tick. One unpayable post must not stop the rest.
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
                # The money moved and the book did not close. Loud,
                # because the next day would pay it again.
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
        # `tax_rate_bps` is a declared field on the policy model with a
        # default of 0 and a validator holding it to 0..10000
        # (`charter/models.py:264`). A 3-arg getattr here would swallow a
        # renamed or dropped field as "the town charges no tax", which is
        # a plausible-looking answer and the exact silent default
        # `test_no_silent_defaults` exists to keep out of `world/`.
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
        # Sim that actually moved or became a debt, as distinct from
        # households the job visited. See the gate below.
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
                    # CORE-03: the ledger's own sink, not None.
                    #
                    # With `None` here, `economy.head_tax_debt.accrued`
                    # and `.repaid` were NEVER PUBLISHED - so the report's
                    # Stakes section read head-tax arrears from a stream
                    # nothing wrote to, and printed a zero over a run that
                    # had charged the tax. That zero then read as a
                    # finding about the town.
                    #
                    # `bootstrap.build_world` already gives the ledger a
                    # sink that bridges LedgerEvent onto the world bus, so
                    # this is the same route every other economy event
                    # takes rather than a second one invented here.
                    event_sink=self.ledger.event_sink,
                )
            except Exception as exc:  # noqa: BLE001 - one household must not
                # take the day down for the other sixteen, same as upkeep.
                logger.warning("head tax failed for %s: %s", household_id or payer, exc)
                continue
            charged += 1
            # ECON-01: the SIM, not just the call. `charged` counts
            # households the tax reached, which is true whether it billed
            # 300 sim or nothing at all - and at the genesis rate of 0 bps
            # it billed nothing, in every run ever made, while the gate
            # below reported a fully taxed day.
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

        # ECON-01/GOV-06: counting calls is not counting money.
        #
        # This gate compared households VISITED against households that
        # exist, and passed. At the genesis rate of 0 bps
        # `head_tax_sim_per_head` is 0, `charge_daily_head_tax` skips every
        # move, and the household is still recorded as charged - so S1
        # ("head tax charged at every day boundary, 100%, halts the run if
        # missed") has been satisfied in all 23 runs by a tax that took
        # nothing from anybody.
        #
        # A zero RATE is a policy the town is allowed to have - a
        # government may vote the tax to nothing. A zero rate is therefore
        # not a fault. A non-zero rate that moved no sim IS one, and that
        # is what this checks.
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
            # A need that cannot be read is not worth losing a turn over.
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
                # AblationArm is a str Enum, so `.value` is the wire form
                # the engine reads out of its env. Read directly rather
                # than through a defaulting getattr: if the field is ever
                # renamed this must break loudly, because answering "full"
                # by default would put every resident in the treated group
                # and nothing in the trace would say so.
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
            # ECON-21: below the broker's refusal ceiling is functionally
            # broke. `> 0` left a one-cent resident unable to think and
            # unable to be revived - alive on paper, and stopped.
            #
            # The SHARED constant, not a fourth private copy. ECON-24 put
            # the other three sites on this one precisely so the number
            # the law block quotes to the resident is the number the sweep
            # enforces; a runner holding its own would re-open that drift
            # from the one root that actually runs.
            if self.ledger.usd_balance(agent_id) > DORMANCY_THRESHOLD_USD_CENTS:
                continue
            dormancy, events = mark_dormant(
                agent.dormancy, now, cause=DORMANCY_CAUSES["swept"])
            if not events:
                continue
            self.world_state.agents[agent_id] = agent.model_copy(
                update={"dormancy": dormancy}
            )
            # B1 interruption rule: a worker that has run out of money to
            # think with is not left mid-job. Nothing wakes them - they are
            # dormant - but the commitment must not outlive the tick, or a
            # revived resident resumes a job it decided on before it went
            # dark.
            self.world_state.continuing.pop(agent_id, None)
            household = agent.household_id
            for event in events:
                event.agent_id = agent_id
                event.payload["location_id"] = self.world_state.location_of(agent_id)
                event.payload["household_id"] = household
                event.payload["deadline_hours"] = DORMANCY_DEATH_HOURS
                # Their household reads this on its next turn - that is who
                # can reach them in time.
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
                # seats voted for can still fail validation (window
                # durations that no longer fit the interval). That is the
                # proposal's fault, not the tick's.
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
                # Names, not ids. A resident asked to vote for "n07" is
                # being asked to vote for a string; `vote` still takes the
                # id, and the refusal names it if they get it wrong.
                notice["candidates"] = [
                    f"{self.agent_names.get(who, who)} ({who})"
                    for who in sorted(open_election.candidates)
                ]
                notice["you_have_voted"] = agent_id in (open_election.ballots or {})
                # WHERE, WHEN and WHO - the rules of taking part, as facts.
                #
                # The 50-resident run: all three `vote` calls were refused
                # because voting had not opened yet, and a nine-year-old was
                # refused a candidacy. Both were residents acting on an
                # election they had been told existed and not told the rules
                # of. The phase alone does not say that voting is days away,
                # that it happens in person at the town hall, or that it is
                # for residents of 18 and over. Stating the rule is not
                # advice about whether to use it.
                from world.tools.locations import LOCATION_TOWN_HALL
                from world.tools.place_aliases import display_place

                notice["where"] = display_place(LOCATION_TOWN_HALL)
                notice["candidacy_closes"] = game_time_label(open_election.candidacy_closes_tick)
                notice["voting_opens"] = game_time_label(open_election.voting_opens_tick)
                notice["voting_closes"] = game_time_label(open_election.voting_closes_tick)
                charter = self.charter_cache.get("charter")
                if charter is not None:
                    notice["min_age"] = charter.suffrage_min_age
                    # Whether THIS resident still owes the town a vote.
                    #
                    # The notice already said "You have voted" to people who
                    # had. To everyone else it said nothing, and the absence
                    # of a sentence is not a sentence. The 28 September run:
                    # four of twenty voted, and one of the sixteen read the
                    # open-for-voting notice on six separate turns - with the
                    # candidates, the place and "voting closes game-day 2,
                    # 15:00" all in it - and went on working. Nothing ever
                    # told it the vote was its own to cast.
                    #
                    # Eligibility is read from the resident's own age rather
                    # than assumed: telling a nine-year-old it must vote is
                    # the same class of error the other way round.
                    # `alive`, `age_years` and `compulsory_voting` are all
                    # declared fields, so they are read directly: a getattr
                    # default here could only ever hide a rename, which is
                    # what `test_no_silent_defaults` exists to catch.
                    me = self.world_state.agents.get(agent_id)
                    eligible = (
                        me is not None
                        and me.alive
                        and me.age_years >= charter.suffrage_min_age
                    )
                    if eligible and not notice["you_have_voted"]:
                        notice["you_have_not_voted"] = True
                        notice["voting_is_compulsory"] = charter.compulsory_voting
                        # Where the reader is standing, so the notice can tell
                        # them the one thing it never did: that they are not
                        # at the hall and must walk first.
                        #
                        # The first 50-resident election tallied with ZERO
                        # ballots. Every resident was told "You have NOT
                        # voted... go to the hall and `vote`", all fifty saw
                        # it, and `vote` was called not once in the run. They
                        # made 390 `move` calls during the voting window and
                        # none of them arrived: `vote` carries a town-hall
                        # location gate, so away from the hall the registry
                        # reduces it to an index line with no schema - a verb
                        # a resident cannot form a call for. The notice named
                        # the verb and never the journey.
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

                # GOV-3. "Who governs" is a list of NAMES, in the third
                # person. It never told the winner it had won.
                #
                # The 8-resident election run of 2026-09-23 is the case
                # in point. A government was elected - GOV-1 and GOV-2
                # both held - and then used no government tool at all.
                # Not refused: `registry.visible_to` gates the GOVERNMENT
                # namespace on `is_sitting` alone, so every one of those
                # verbs was already in the winner's schema wherever it
                # stood, and the run's refusal table carries not one
                # government-tool entry. They were served and never
                # reached for.
                #
                # This is the finding `authoring_hint` already paid for
                # once: "an agent told 'write a procedure down', with
                # forty-eight tools in its schema, is not declining to
                # author - it has not connected the sentence to the
                # verb." Naming the verb took skillify from 0% to 4 of 5.
                #
                # So the seat is stated to the holder, with the verbs and
                # the place. Facts and rules, never strategy: what a seat
                # MAY do, never that it should do any of it. Delphine
                # Quintero campaigned on changing the charter, won, and
                # went back to trading flour - what was missing was not
                # the will.
                if state.is_sitting(agent_id):
                    notice["your_seat"] = (
                        "Prime Minister"
                        if agent_id == state.pm_agent_id
                        else "minister"
                    )
                    # A caretaker was appointed rather than elected
                    # (GROW-12), and saying so keeps the record honest in
                    # the one place the holder reads it.
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

        # Refreshed every tick whether or not anything is happening: the
        # notice has to be able to say "the seats are vacant" too.
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
                # take the tick down, same discipline as upkeep and tax.
                logger.warning(
                    "charter tick failed for election %s: %s",
                    election.election_id,
                    exc,
                )
        # Re-read AFTER advancing, so the notice this tick's perceptions are
        # built from says the phase the election is now in. Filled only
        # before, it lagged a tick: at dawn on day 0 every resident was told
        # the election was "scheduled" while candidacy had been open since
        # tick 0 - on the one turn a resident reads it first. A tally or a
        # new schedule above shows up the same tick for the same reason.
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
                    # GROW-06: with a map service a finished building
                    # becomes a real Sector rather than a row. This runner
                    # does not have one yet, so it is None and the building
                    # completes without being walkable - a real, named gap
                    # rather than one left to be discovered.
                    map_service=self.map_service,
                )
            except Exception as exc:  # noqa: BLE001 - one bad building must
                # not stop the others finishing, and must not end a tick.
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

        # ECON-31: a resident with no household is a household of one.
        #
        # The old guard skipped anybody without one entirely, so a
        # household-less resident ate for free for ever while
        # `charge_the_day_tax` still
        # took their head tax - two day-boundary jobs disagreeing about
        # whether the same person exists.
        #
        # Every genesis resident has a household, so this was latent. It
        # stops being latent the moment anybody leaves one: `MOVE_OUT_ALONE`
        # mints a new household, but a household also DISSOLVES when its
        # last other member dies, and GOV-25 is the same gap seen from the
        # suffrage side - that orphan lost the vote, and this one stopped
        # paying for dinner.
        #
        # Keyed on the agent id rather than a minted household id: inventing
        # one would put a household in `world.households` that nobody
        # created and that the family API would then have to explain.
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
                    # The same adapter admin_api uses to charge the ledger
                    # from life/ - built per day rather than held, so it
                    # always reads the current tick.
                    LedgerEconomyPort(self.ledger, self.world_state.tick, self.world_state),
                    dials,
                    # ECON-16: who fed themselves today. Without this a
                    # resident who ate would still walk the hunger ladder.
                    fed_agent_ids=self.fed_today,
                )
            except Exception as exc:  # noqa: BLE001 - one household must not
                # take the day down for the other sixteen.
                logger.warning("upkeep failed for %s: %s", household_id, exc)
                continue
            for member in result.updated_members:
                self.world_state.agents[member.agent_id] = member
            self.hunger_by_agent.update(result.updated_hunger)
            # ECON-15: what the household did not pay is a DEBT.
            #
            # `world.arrears` is documented as {"head_tax": n,
            # "upkeep": n} and only head_tax was ever written, so a
            # shortfall - real money not paid - was recorded nowhere.
            # `check_debts` then told a resident it owed nothing while it
            # went hungry for the bill it had missed.
            #
            # Split across whoever contributed, in proportion to what
            # they managed; equally across the household when nobody paid
            # anything at all. Charging one member the whole bill would
            # invent a debtor the town never had.
            self.record_upkeep_arrears(result)
            await self.publish_life_events(result.events, timestamp.tick_index)

        # ECON-16: the day's meals end with the day.
        #
        # Without this clear, one apple marks a resident fed FOREVER: the
        # set only ever grows, `ate_today` is permanently true, and the
        # hunger ladder can never advance for anybody who has eaten once.
        # That would disable starvation for the whole town - a quieter and
        # worse version of the bug ECON-16 is about, introduced by its own
        # fix. Cleared after the households are billed, not before, so
        # every household in this pass sees the same day.
        self.fed_today.clear()

        # `consecutive_unpaid_days`, which is what HungerState actually
        # carries - the first draft of this line guessed `unpaid_days` and
        # took twelve tick-loop tests down with an AttributeError.
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
        # A9 (The 250 section 31.3): "if he'd owned his own home, that
        # transfers too, by the identical rule." Until this call existed
        # settle_property_estate was written, tested, and invoked by nothing -
        # a dead owner's land simply stayed in their name forever.
        self.settle_estate_land(updated)
        # C1: and what they invented. A tool that dies with its inventor is
        # the one way diffusion can run backwards, and until now that was
        # the only thing that ever happened to one.
        events.extend(self.settle_estate_tools(updated))
        # ECON-07. And their money.
        #
        # `Ledger.settle_inheritance` was written, documented and tested,
        # and had no caller anywhere - `Will`'s own docstring names it as
        # the rule that applies. So the land and the tools were inherited
        # and the MONEY was not: a dead resident's sim left circulation,
        # their USD account stayed open, and `usd_total` kept counting a
        # wallet nobody could spend from. That is the invariant the halt
        # gate reads, so every death quietly moved the number the run
        # stops on.
        #
        # Heirs by the will's money bequests where one names them, else
        # the section 31.3 order (spouse, children, parents) that land
        # already follows. An empty list is not an error: `distribute_
        # estate` escheats the whole estate to treasury, which is the
        # right answer for a resident who leaves nobody.
        await self.settle_money_estate(updated, tick)
        # CORE-10. Take them off the roster.
        #
        # `Scheduler.unregister_agent` had no production caller anywhere, so
        # a dead resident stayed in `entries` forever: still counted in
        # `due_per_tick`, still rotated to by the round robin, still costing
        # a perception build every cadence. The dispatch skip added for
        # INFRA-03 stops the WASTE, but the scheduler's own arithmetic - how
        # many residents are due, which is what CORE-12's pacing divides by
        # - was still counting the dead.
        #
        # Its own docstring says the round-robin design "exists to avoid"
        # exactly this and to "call unregister_agent() first". Nobody did.
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

        # Anything unnamed still has to go somewhere, or it goes nowhere.
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
            # A death must never fail because the land register disagreed.
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
        # Staying is the default because it is the one outcome that needs no
        # resources the agent may not have: no new household id, no partner,
        # no temple fee. Moving out or pairing off are the agent's to choose.
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

    # -- run / stop --------------------------------------------------------

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
            # Before the barren guard, not after it. The barren guard needs
            # twenty ticks to be sure, and twenty ticks is sixty-nine refused
            # calls at this cast size - the whole cost this check exists to
            # avoid. Asked only on a tick that already lost a dispatch, so a
            # healthy run never makes the request.
            if self.barren_ticks:
                terminal = await self.broker_terminal_reason()
                if terminal is not None:
                    logger.error("stopping: %s", terminal)
                    break
            # CORE-32. Ticks, OR wall clock, whichever comes first.
            #
            # "Twenty is roughly a game-hour at the default pacing" assumed
            # 15-second ticks. Measured since: run 23 averaged 63 s/tick, and
            # a tick where every dispatch times out costs the full 120 s - so
            # twenty barren ticks is anywhere between five minutes and forty
            # of an operator watching a town that is not thinking, and paying
            # for the failed calls throughout.
            #
            # Both bounds, because either alone is wrong: ticks alone is
            # unbounded in time, and seconds alone would stop a legitimately
            # slow first tick.
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
                # Nothing has thought for this many consecutive ticks. The
                # cause is upstream - no credentials, a dead broker, a spend
                # cap, engines that never booted - and none of them get
                # better by ticking harder.
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
            # CORE-05: measure what this tick cost, then decide whether the
            # next one would cross the cap. Both before the sleep, so a
            # paced run does not spend its interval on the wrong side of
            # the line.
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
        # CORE-17. A night has nothing to watch: every resident is asleep,
        # nobody is dispatched, and the pacing interval exists to give the
        # broker room to answer calls that are not being made. Bursting it
        # turns 48 ticks of dead wall-clock into a moment, which is the
        # difference between a run that reaches tomorrow morning and 23
        # runs that did not.
        #
        # Read off the world, not off a flag: a resident woken overnight by
        # an interrupt IS dispatched, and this correctly still paces then,
        # because `night_is_quiet` asks whether anybody is actually up.
        if self.night_is_quiet():
            await asyncio.sleep(0)
            return
        if interval <= 0:
            # Unpaced deliberately (mock runs, bursts, tests). Still yield,
            # so a stop request and the HTTP server get their turn.
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
            # Drain while the broker will still answer.
            #
            # Run 22's drain fired 235ms AFTER the cap halted dispatch, so
            # every model call the learning pipeline made was refused and
            # nothing was encoded - a whole morning of signals thrown away
            # by a mechanism built to stop exactly that. A drain that can
            # only ever run after the halt is a drain that can never work.
            #
            # So the last DRAIN_RESERVE cents of the cap are spent on
            # consolidation rather than on more thinking: the moment spend
            # crosses that line, every resident gets its batch, once, while
            # calls are still possible.
            await self.drain_before_halt()
            if self.governor.tripped or self.stop_requested:
                break
        return results

    def request_stop(self) -> None:
        self.stop_requested = True

    # The share of the cap held back so the last thing a run spends money
    # on is remembering what it did. Ten cents at a $2 cap is one drain for
    # twenty residents; the alternative measured in run 22 is a drain after
    # the halt, which encodes nothing at all.
    # CORE-05. The floor, not the reserve.
    #
    # A flat 10 cents was the whole reserve, and one tick of 20 residents
    # spends far more than that. So spend crossed from "below the line" to
    # "over the cap" inside a single tick, the governor tripped, and the
    # drain - which checks `governor.tripped` and returns - could never
    # fire in time. Run 22's drain ran 235 ms after the halt and reported
    # "20 of 20" while every one of those calls was refused.
    #
    # The reserve is now the most expensive tick this run has actually had,
    # with this as a floor. That is the only quantity that guarantees the
    # drain gets a whole tick's worth of headroom, and it needs no tuning
    # because the run measures it.
    DRAIN_RESERVE_CENTS = 10

    def note_tick_spend(self, cents: float) -> None:
        """Remember the most expensive tick, for the drain's reserve."""
        if cents > self.max_tick_spend_cents:
            self.max_tick_spend_cents = float(cents)

    # What one resident's consolidation batch costs, in cents. Measured in
    # run 24: learning_centre calls ran 55-108K input tokens at Kimi's
    # $0.36/1M, so roughly 2-4 cents each. Four is the pessimistic end,
    # because a reserve that is too small buys nothing at all while one
    # that is slightly too large costs a few ticks of thinking.
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
        # One consolidation call per resident. Read off the world rather
        # than off the gateway: every gateway has a different idea of what
        # it is talking to (the mock has no port map at all), and the
        # number that matters is how many residents there are to drain.
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
        # FIRST, while the world still answers. The roster, the refused
        # intentions and the tools being served exist only in its memory,
        # every final report is written after the stop, and the 50-agent
        # run's report printed that silence as fact: "The world reported no
        # roster" about fifty residents. See `capture_final_reading`.
        try:
            await capture_final_reading(
                app=self.world_app,
                base_url=self.config.world_base_url(),
                checkpoint_dir=self.config.checkpoint_dir,
                tick=self.world_state.tick() if self.world_state is not None else None,
            )
        except Exception:  # noqa: BLE001 - describing a run cannot end it
            logger.exception("could not take the final reading")
        # WIRE-02. Ask every engine how its link to the world behaved,
        # BEFORE anything is torn down - a stopped engine cannot answer,
        # and this is the one number the run cannot reconstruct
        # afterwards from anything it wrote down.
        #
        # Kept on `self` so the report and the manifest can read it
        # without asking again, and so a caller that wants it after
        # shutdown still has it.
        try:
            self.wire_health = await self.collect_wire_health()
            self.report_wire_health(self.wire_health)
        except Exception:  # noqa: BLE001 - telemetry must not stop a shutdown
            logger.exception("could not collect the engine link health")
        # Before shutdown_all(): otherwise the watchdog sees units going
        # STOPPED mid-teardown and races to bring them back up.
        if self.watchdog is not None:
            await self.watchdog.stop()
            self.watchdog = None
        if getattr(self, "groundskeeper", None) is not None:
            await self.groundskeeper.stop()
            self.groundskeeper = None
        # BEFORE shutdown_all(), because a killed engine cannot consolidate
        # anything. This is the end-of-run learning drain: every resident
        # gets one last batch over the signals its morning produced, which
        # until now were written, committed and then thrown away with the
        # process. Counted so the run report can say how many residents
        # actually consolidated rather than how many were asked to.
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
        # AFTER shutdown_all(), so no engine still holds its brain.sqlite
        # open and the archive is a whole copy (INFRA-09, run 29).
        archive_engine_homes(self.config.home_root, self.config.checkpoint_dir)
        if self.gateway is not None:
            await self.gateway.aclose()
        if self.embedded_world_server_task is not None:
            # Signal uvicorn's own graceful-exit flag rather than
            # task.cancel()-ing server.serve() outright: a hard cancel lands
            # mid-ASGI-lifespan (Starlette's routing.lifespan is awaiting
            # receive() at that point) and uvicorn logs it as an ERROR
            # traceback on every single stop, real or dry-run - noise that
            # would bury the actual stop reason across a 14-hour genesis
            # run's logs. should_exit=True lets Server.main_loop() unwind
            # the lifespan the way it was designed to.
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
                # Anything else - a sink that raises, a component that
                # snapshots badly - used to escape here and take the whole
                # shutdown with it, on the very last statement of the run.
                # The checkpoint is what a continuation resumes from; losing
                # it must not also lose the clean stop.
                logger.exception("final checkpoint failed and the run stopped anyway")


# ── CLI ──────────────────────────────────────────────────────────────


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kasukabe genesis runner: boot the world, launch 250 engines, run."
    )
    # --mock is kept ONLY so it fails with an explanation instead of
    # argparse's "unrecognized argument". A script that still passes it is a
    # script expecting a free dry run, and silently giving it real Bedrock
    # spend is the one outcome worse than refusing to start.
    parser.add_argument(
        "--mock", action="store_true", default=None,
        help=argparse.SUPPRESS,
    )
    # --live is retained as a NO-OP so existing scripts (start-genesis.sh,
    # the compose commands in docs/HANDOVER.md) keep working unchanged. It
    # describes the only behaviour there is now.
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
        # store_true, so only ever set when asked for - an absent flag must
        # not overwrite a config that already says True.
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
            pass  # signal handlers are not available on every platform (e.g. some CI)

    await runner.boot(resume=not args.no_resume)
    await runner.start_embedded_world_server()
    await runner.start_agents()
    runner.start_watchdog()          # B2 (The 250 section 33)

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
