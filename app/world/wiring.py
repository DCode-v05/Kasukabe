"""Composition root: constructs every world/ submodule into one running
server (DESIGN.md section 9's "WORLD SERVER map . ticks . tools . ledger
. charter . health . crime", task "wire-world").

Every module this file wires (map, core, tools, ledger, charter, life,
crime) was built standalone by its own track, on purpose - see each
package's own docstring for the Protocol/EventSink seams it exposes
instead of importing a sibling directly. This file is where those seams
finally close, one level up from world.tools.bootstrap.build_world():
that function already collapses tools+ledger+charter+core's event bus
into one (WorldState, ToolRegistry) pair, but it does not seed a
population, does not construct world.crime (nothing else does either),
does not load the map, and does not attach persistence - all of that
lives here instead of inside tools/, because none of it is tools/'s job
per app/CONVENTIONS.md's module boundaries.

Construction order (dependency order, matching DESIGN.md section 12's
build path "world+tools -> broker+ledger -> ... "):

    1. trace store (supabase/'s SQLite-WAL client) - nothing downstream
       depends on it, but seeding genesis rows before genesis-time events
       fire keeps the `events.agent_id` foreign key satisfied from tick 0.
    2. map (world.map.MapService) - independent of everything else.
    3. tools.bootstrap.build_world() - the event bus, the location index,
       the ledger, the charter engine, and the tool registry, all in one
       call (see that function's own docstring).
    4. charter's event sink is re-pointed at the shared event bus (build_world()
       constructs charter with none - see rewire_charter_event_sink()).
    5. world.core.TickEngine, sharing the same EventBus build_world() made
       (see TickEngine's own docstring: every piece it wires is "also
       usable standalone", so handing it an existing bus is exactly the
       supported seam, not a workaround).
    6. genesis population: genesis.json's 250 agents/households/
       businesses funded into the ledger, registered with the scheduler,
       and placed on the map (apply_genesis_population()).
    7. world.crime.CrimeEngine, over adapters onto the ledger/life/
       charter/locations this file already built (crime/ports.py's four
       Protocols - nothing else in the repo implements them yet).

WorldServer (below) is the result: the "internal async API" the task
brief asks for is its methods (advance_tick, execute_tool, the
president_*/god_* console actions); main.py's FastAPI routes are a thin
HTTP/WebSocket shell around this class, not a second copy of its logic.
"""

from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from world.charter.engine import CharterEngine
from world.charter.models import JobStatus
from world.core.checkpoint import WorldCheckpoint
from world.core.clock import TICK_MINUTES, is_day_boundary, timestamp_for
from world.core.engine import TickEngine, TickResult
from world.core.events import EventBus, WorldEvent
from world.core.scheduler import SchedulerConfig
from world.crime.engine import CrimeEngine, create_crime_engine
from world.crime.models import InjurySeverity
from world.ledger.core import Ledger
from world.ledger.head_tax import HeadTaxDebtStore
from world.ledger.models import AccountKind, Currency, Transaction, TransactionReason
from world.ledger.money import BURNED_ACCOUNT_ID, TOTAL_USD_CENTS, TREASURY_ACCOUNT_ID
from world.life import birth as life_birth
from world.life import day_jobs as life_day_jobs
from world.life import death as life_death
from world.life import health as life_health
from world.life import household as life_household
from world.life import tick as life_tick
from world.life.constants import DORMANCY_THRESHOLD_USD_CENTS
from world.life.models import (
    AgentLifeRecord,
    AilmentKind,
    BirthRequest,
    Genesis,
    GenesisAgent,
    LifeEvent,
    PopulationDials,
)
from world.life.upkeep import HungerState
from world.map.service import MapService
from world.tools.bootstrap import build_world
from world.tools.economy_bridge import (
    LedgerEconomyPort,
    LedgerGuardianshipPort,
    charter_event_to_world_event,
    life_event_to_world_event,
)
from world.tools.models import Business
from world.tools.primitives.resources import regenerate_zones
from world.tools.registry import ToolRegistry, ToolResult
from world.tools.state import WorldState

from supabase import PayloadArchiver, SupabaseClient, SupabaseConfig, eq, get_client
from supabase.archiver import build_world_snapshot_archive_key

logger = logging.getLogger(__name__)

# ── default paths ──────────────────────────────────────────────────────
# app/world/wiring.py -> parents[2] is the kasukabe/ repo root, matching
# world/map/tests/conftest.py's identical walk-up-parents trick (see that
# file's own comment) so this resolves the same way on the host checkout
# and inside the "test" docker-compose service's /smallville mount.
PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent
DEFAULT_GENESIS_JSON_PATH = PACKAGE_DIR / "genesis.json"
DEFAULT_VILLE_DIR = (
    REPO_ROOT
    / "smallville"
    / "environment"
    / "frontend_server"
    / "static_dirs"
    / "assets"
    / "the_ville"
)

# ASSUMPTION (no other module owns per-household or per-business map
# placement yet - tools/locations.py's own docstring calls resolving a
# real Tiled sector/arena into a location_id "future integration work"):
# every agent starts at a slug keyed off their household, every genesis
# business at a slug keyed off its business_type. Opaque strings, exactly
# like tools/locations.py's own "mill"/"smithy"/"temple" constants - the
# tool registry never looks past location_id equality.
HOME_LOCATION_PREFIX = "home"
CONSTRUCTION_YARD_LOCATION = "construction_yard"


def home_location(household_id: str) -> str:
    return f"{HOME_LOCATION_PREFIX}:{household_id}"


# ── ledger <-> supabase.transactions mapping ───────────────────────────
# The trace-store schema (supabase/sqlite_schema.sql) constrains tx_type
# to a fixed vocabulary that predates world.ledger.TransactionReason and
# is coarser than it; this is the many-to-one map between them. ASSUMPTION
# where DESIGN.md does not pin a tighter mapping.
TX_REASON_TO_SCHEMA_TYPE: dict[TransactionReason, str] = {
    TransactionReason.GENESIS: "credit",
    TransactionReason.EXCHANGE: "exchange",
    TransactionReason.TAX: "tax",
    TransactionReason.BUSINESS_REGISTRATION_FEE: "fee",
    TransactionReason.WAGE: "transfer",
    TransactionReason.PENSION: "pension",
    TransactionReason.INHERITANCE: "transfer",
    TransactionReason.INFERENCE_BURN: "burn",
    TransactionReason.BLESSING: "credit",
    TransactionReason.TRANSFER: "transfer",
    # Its own type, not folded into "transfer". A settlement - a price
    # agreed out loud and then actually paid - is the clearest evidence
    # there is that talking changed the world, and it cannot be counted
    # if it looks identical to somebody handing over money for nothing.
    TransactionReason.TRADE: "trade",
    # Their own types for the same reason TRADE has one. A shop counter is
    # where most bargains in a town with shops would actually be settled,
    # and folding them into "transfer" made every one of them invisible.
    TransactionReason.PURCHASE: "purchase",
    TransactionReason.SALE: "sale",
    # TOOL-03's till movements. Coarsened to "transfer" under protest: the
    # schema's `tx_type` CHECK is a closed vocabulary with no "till" in it,
    # and widening it is a trace-schema change in the INFRA lane, not this
    # one. The LEDGER keeps the distinction (`TransactionReason.TILL`), so
    # `world.ledger.transactions` can tell a shopkeeper banking the day's
    # takings from one handing a stranger money for nothing; the trace row
    # cannot, yet.
    #
    # ECON-03 is where this closes - it asks for UPKEEP, ACTION_COST,
    # REFUND, FEE, DONATION, REVIVAL and ENERGY_EXCHANGE on the same
    # grounds, so the vocabulary has to be widened once for all of them
    # rather than seven times.
    TransactionReason.TILL: "transfer",
    # Straight through: "fine" is already in the schema's tx_type
    # vocabulary, it simply had no ledger reason pointing at it.
    TransactionReason.FINE: "fine",
    # ECON-03. Coarsened on purpose, and this time it costs nothing.
    #
    # The schema's `tx_type` CHECK is a closed vocabulary and SQLite cannot
    # ALTER a constraint - widening it would leave every EXISTING database
    # rejecting the new values, which is the "213 transaction rows dropped
    # in six ticks" failure `SqliteClient.ensure_schema` documents. So
    # `tx_type` keeps its coarse grouping.
    #
    # Nothing is lost by that, because the fine grain lives in the `reason`
    # COLUMN beside it, which is free text and is what
    # `run_report._transactions` actually queries. That is the whole of
    # ECON-03: the report was asking `where reason = 'daily_upkeep'` and
    # every upkeep row said `transfer`.
    TransactionReason.UPKEEP: "fee",
    TransactionReason.ACTION_COST: "fee",
    TransactionReason.REFUND: "credit",
    TransactionReason.FEE: "fee",
    TransactionReason.DONATION: "transfer",
    TransactionReason.REVIVAL: "fee",
    TransactionReason.ENERGY_EXCHANGE: "exchange",
}

ACCOUNT_KIND_TO_POOL: dict[AccountKind, str] = {
    AccountKind.AGENT: "agent",
    AccountKind.TREASURY: "treasury",
    AccountKind.BURNED: "burned",
    # AccountKind.BUSINESS deliberately absent: the schema's from_pool/
    # to_pool enum has no "business" member (DESIGN.md section 3 scopes
    # USD, not sim, to the three pools the ledger invariant covers), so a
    # business leg's pool is left null - see account_pool() below.
}


@dataclass
class WorldConfig:
    """Everything build_world_server() needs to know before it can boot.
    Defaults match what a Docker "test" run or a host checkout under
    /Vat/kasukabe both already have on disk (see DEFAULT_* above)."""

    genesis_json_path: Path = DEFAULT_GENESIS_JSON_PATH
    ville_dir: Path = DEFAULT_VILLE_DIR
    require_map: bool = True
    seed: int = 2026
    genesis_tick: int = 0
    scheduler_target_fraction: float = 0.3
    supabase_config: SupabaseConfig | None = None


# ── crime/ports.py adapters ─────────────────────────────────────────────
# crime/ was built standalone against four Protocols (ports.py's own
# docstring: "built to be tested standalone ... the real implementations
# are expected to be adapted onto these Protocols once world/'s ... HTTP
# dispatcher wires every world/ submodule together"). This file is that
# wiring; nothing else in the repo implements them.


class CrimeLedgerAdapter:
    """crime.ports.EconomyPort over a real Ledger. Amounts are always
    sim-currency: DESIGN.md's theft targets cash *stashed in a Storage*,
    never a USD wallet (see primitives/crime.py's own docstring on why
    pocket-picking a wallet is out of scope), and fines are civic
    sim-currency penalties, not USD.
    """

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def transfer_sim(
        self, from_agent_id: str, to_agent_id: str, amount: int, tick: int, memo: str = ""
    ) -> None:
        await self.ledger.move(
            from_account_id=from_agent_id,
            to_account_id=to_agent_id,
            amount=amount,
            currency=Currency.SIM,
            reason=TransactionReason.TRANSFER,
            tick=tick,
            memo=memo or "crime: stolen proceeds",
        )

    async def collect_fine(
        self, convict_agent_id: str, amount_sim: int, tick: int, memo: str = ""
    ) -> None:
        await self.ledger.move(
            from_account_id=convict_agent_id,
            to_account_id=TREASURY_ACCOUNT_ID,
            amount=amount_sim,
            currency=Currency.SIM,
            reason=TransactionReason.TRANSFER,
            tick=tick,
            memo=memo or "crime: fine",
        )


class LifeHealthAdapter:
    """crime.ports.HealthPort, mapping a fight injury onto world.life's
    existing AilmentKind model. life/ has no dedicated injury variant yet
    (crime/ports.py's HealthPort docstring says as much: "a future life/
    update ... is expected to use" this seam) so this is an ASSUMPTION,
    not a DESIGN.md number: minor/moderate/severe map onto the ailment
    whose existing work-capacity hit is closest in spirit
    (short/medium/long). An agent already sick is left alone rather than
    stacking a second ailment, matching life.health.roll_daily_health's
    own "already sick -> no-op" rule.
    """

    SEVERITY_TO_AILMENT: dict[InjurySeverity, AilmentKind] = {
        InjurySeverity.MINOR: AilmentKind.SHORT,
        InjurySeverity.MODERATE: AilmentKind.MEDIUM,
        InjurySeverity.SEVERE: AilmentKind.LONG,
    }

    def __init__(self, world: WorldState) -> None:
        self.world = world

    async def apply_injury(self, agent_id: str, severity: InjurySeverity, tick: int) -> None:
        agent = self.world.agents.get(agent_id)
        if agent is None or not agent.alive or agent.health.current_ailment is not None:
            return
        kind = self.SEVERITY_TO_AILMENT[severity]
        day = timestamp_for(tick).game_day
        ailment = life_health.onset_ailment(self.world.rng, kind, day)
        self.world.agents[agent_id] = agent.model_copy(
            update={"health": agent.health.model_copy(update={"current_ailment": ailment})}
        )


class CharterGovernmentAdapter:
    """crime.ports.GovernmentPort over the real CharterStore."""

    def __init__(self, charter: CharterEngine) -> None:
        self.charter = charter

    async def is_minister(self, agent_id: str) -> bool:
        state = await self.charter.store.get_government_state()
        return state.is_sitting(agent_id)

    async def is_police_officer(self, agent_id: str) -> bool:
        jobs = await self.charter.store.list_jobs()
        return any(
            job.job_type == "police"
            and job.status is JobStatus.FILLED
            and job.assigned_agent_id == agent_id
            for job in jobs
        )


def rewire_charter_event_sink(
    charter: CharterEngine, sink: Callable[[dict[str, Any]], Awaitable[None]]
) -> None:
    """create_genesis_charter_engine() (called inside
    tools.bootstrap.build_world(), with no sink argument) leaves every
    charter/ sub-engine's event_sink at events.py's noop_sink. Each
    sub-engine stores event_sink as a plain, reassignable attribute
    (charter/amendments.py etc: `self.event_sink = event_sink if ...
    else noop_sink`) specifically so this reassignment is possible
    without reconstructing the whole engine - see this module's own
    docstring for why rewiring beats duplicating build_world()'s
    construction here instead.
    """
    for sub_engine in (
        charter.elections,
        charter.amendments,
        charter.government,
        charter.patent_office,
        charter.land_petitions,
        charter.organizations,
        charter.complaints,
    ):
        sub_engine.event_sink = sink


def crime_event_to_world_event(event: dict[str, Any], tick: int) -> WorldEvent:
    """crime/events.py's `{"type", "payload"}` dict -> WorldEvent, the
    same shape economy_bridge.py's charter_event_to_world_event already
    converts for charter/ (crime/ was built against an identical EventSink
    seam - see crime/events.py's own docstring, which says as much
    explicitly). No dedicated economy_bridge.py helper exists for this
    yet because nothing wired crime/ to anything before this file.
    """
    return WorldEvent(type=event["type"], tick=tick, payload=dict(event.get("payload", {})))


# ── genesis population wiring ───────────────────────────────────────────


def load_genesis(path: Path) -> Genesis:
    if not path.exists():
        raise RuntimeError(
            f"genesis file not found at {path} - run "
            "`python -m world.life.genesis` (world/life/genesis.py's own CLI) "
            "to generate one, or point WorldConfig.genesis_json_path at an "
            "existing genesis.json"
        )
    return Genesis.model_validate_json(path.read_text(encoding="utf-8"))


def life_record_from_genesis(agent: GenesisAgent) -> AgentLifeRecord:
    return AgentLifeRecord(
        agent_id=agent.agent_id,
        age_years=agent.age_years,
        sex=agent.sex,
        household_id=agent.household_id,
        spouse_id=agent.spouse_id,
        marital_status=agent.marital_status,
        parent_ids=list(agent.parent_ids),
        children_ids=list(agent.children_ids),
        sponsor_parent_id=agent.sponsor_parent_id,
    )


def business_location(business_type: str) -> str:
    """ASSUMPTION, matching HOME_LOCATION_PREFIX's docstring above: a
    genesis business's location_id is its business_type slug
    ("grocery_store", "cafe", "pharmacy") - DESIGN.md section 1 names
    these as genesis buildings but nothing yet ties a business record to
    a real map tile."""
    return business_type


async def apply_genesis_population(
    world: WorldState,
    tick_engine: TickEngine,
    genesis: Genesis,
    trace_store: "TraceStore | None",
) -> None:
    """Funds the ledger, populates world.agents/households/businesses,
    registers every agent with the scheduler, and places everyone on the
    map - genesis.json's population made real inside a running world.

    The trace store's `agents` and `businesses` tables reference each
    other (`agents.employer_business_id -> businesses.id` and
    `businesses.owner_agent_id -> agents.id`, supabase/sqlite_schema.sql)
    - a genuine circular foreign key, since a business's own owner is
    themself an agent whose employer_business_id often names that same
    business. Neither table can be seeded fully before the other, so
    agent rows are inserted first with employer_business_id left unset
    (businesses.owner_agent_id can then resolve), and a second pass
    (set_employer, after businesses exist) patches it in via UPDATE -
    the two-pass insert SQLite's own FK enforcement (immediate, not
    deferred - see connect_sqlite()'s `PRAGMA foreign_keys = ON`) leaves
    as the only option short of relaxing a constraint this file does not
    own.
    """
    agent_ids = [agent.agent_id for agent in genesis.agents]

    # 1. USD wallets + treasury, one genesis() call so the $1000
    # invariant is checked exactly once (see Ledger.genesis()'s own
    # docstring on why a per-agent check would be wrong here).
    await world.ledger.genesis(
        agent_ids,
        agent_usd_cents=genesis.usd_ledger.agent_wallets_cents // genesis.population,
        treasury_usd_cents=genesis.usd_ledger.treasury_cents,
        tick=world.tick(),
    )

    # 2. Sim-currency starting balances: minted (not part of the USD
    # invariant, see ledger/core.py's mint_sim docstring), one call per
    # agent since genesis() only funds USD.
    for agent in genesis.agents:
        if agent.starting_sim_balance > 0:
            await world.ledger.mint_sim(
                agent.agent_id,
                agent.starting_sim_balance,
                world.tick(),
                reason=TransactionReason.GENESIS,
                memo="genesis sim balance",
            )

    # 3. Households, verbatim - genesis.py already builds these as
    # world.life.models.Household instances (see genesis.py's own
    # generate_genesis()), so no conversion is needed.
    for household in genesis.households:
        world.households[household.household_id] = household

    # 4. Life records + scheduler registration + map placement. Ledger
    # accounts already exist (step 1); trace-store agent rows go in with
    # employer_business_id left unset (see docstring's circular-FK note -
    # step 6 patches it once businesses exist).
    for genesis_agent in genesis.agents:
        record = life_record_from_genesis(genesis_agent)
        world.agents[record.agent_id] = record
        location_id = home_location(genesis_agent.household_id)
        tick_engine.register_agent(record.agent_id, location_id)
        # tick_engine.register_agent() places the agent directly in
        # TickEngine's own LocationIndex (a separate instance from
        # world.locations - see this module's docstring on why that is
        # fine); publish agent.spawned too so world.locations (attached
        # to the same shared EventBus) and the trace store both observe
        # it exactly like a live spawn would.
        await world.event_bus.emit(
            "agent.spawned", tick=world.tick(), payload={"agent_id": record.agent_id, "location_id": location_id}
        )
        if trace_store is not None:
            await trace_store.seed_agent(record, genesis_agent, world.tick())

    # 5. Businesses. Each owner's ledger account already exists (step 1)
    # and now so does its trace-store agents row (step 4), satisfying
    # both Ledger.open_business() and businesses.owner_agent_id's FK. The
    # construction company + the three genesis small businesses get real
    # ledger accounts; the 10 control businesses are deliberately
    # ownerless (DESIGN.md section 10: "no owning agent") and have no
    # ledger.open_business counterpart - the schema's
    # businesses.owner_agent_id is NOT NULL, so they are exposed
    # read-only via WorldServer.control_businesses instead (see
    # build_world_server()).
    cc = genesis.construction_company
    world.businesses[cc.business_id] = Business(
        business_id=cc.business_id,
        name=cc.name,
        business_type="construction",
        owner_agent_id=cc.owner_agent_id,
        employee_agent_ids=set(cc.hand_agent_ids),
        location_id=CONSTRUCTION_YARD_LOCATION,
    )
    world.ledger.open_business(cc.business_id, cc.owner_agent_id)
    if cc.starting_sim_balance > 0:
        await world.ledger.mint_sim(
            cc.business_id, cc.starting_sim_balance, world.tick(), reason=TransactionReason.GENESIS
        )
    if trace_store is not None:
        await trace_store.seed_business(world.businesses[cc.business_id], world.tick())

    for sb in genesis.small_businesses:
        world.businesses[sb.business_id] = Business(
            business_id=sb.business_id,
            name=sb.name,
            business_type=sb.business_type,
            owner_agent_id=sb.owner_agent_id,
            employee_agent_ids=set(sb.employee_agent_ids),
            location_id=business_location(sb.business_type),
        )
        world.ledger.open_business(sb.business_id, sb.owner_agent_id)
        if sb.starting_sim_balance > 0:
            await world.ledger.mint_sim(
                sb.business_id, sb.starting_sim_balance, world.tick(), reason=TransactionReason.GENESIS
            )
        if trace_store is not None:
            await trace_store.seed_business(world.businesses[sb.business_id], world.tick())

    # 6. Patch employer_business_id on every agent trace row that has one
    # (see docstring's circular-FK note) - businesses now exist to point at.
    if trace_store is not None:
        for genesis_agent in genesis.agents:
            if genesis_agent.business_id is not None:
                await trace_store.set_employer(genesis_agent.agent_id, genesis_agent.business_id)

    logger.info(
        "genesis population applied: %d agents, %d households, %d businesses",
        len(genesis.agents), len(genesis.households), 4,
    )


# ── trace store (supabase/'s SQLite-WAL client) ─────────────────────────


def account_pool(ledger: Ledger, account_id: str) -> str | None:
    account = ledger.accounts.get(account_id)
    if account is None:
        return None
    return ACCOUNT_KIND_TO_POOL.get(account.kind)


def build_transaction_row(
    ledger: Ledger, transaction: Transaction, known_agent_ids: set[str]
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "currency": transaction.currency.value,
        "tx_type": TX_REASON_TO_SCHEMA_TYPE[transaction.reason],
        "from_agent_id": transaction.from_account if transaction.from_account in known_agent_ids else None,
        "to_agent_id": transaction.to_account if transaction.to_account in known_agent_ids else None,
        # Which account was NOT an agent, when one of them was not.
        #
        # A purchase moves sim from an agent to a BUSINESS, and the two
        # agent columns above write an id only for a known agent - so the
        # shop leg was null and the row did not say which shop had been
        # paid. The settlement query requires both agent columns, so every
        # purchase and sale was excluded from it outright, and the run
        # reports read that as "the town does not trade" when it meant "we
        # cannot see it".
        #
        # Not a second agent column: a business is not an agent, and the
        # pool columns below already went wrong once by conflating them.
        "counterparty_id": (
            transaction.to_account
            if transaction.from_account in known_agent_ids
            and transaction.to_account not in known_agent_ids
            else transaction.from_account
            if transaction.to_account in known_agent_ids
            and transaction.from_account not in known_agent_ids
            else None
        ),
        "from_pool": account_pool(ledger, transaction.from_account),
        "to_pool": account_pool(ledger, transaction.to_account),
        "amount": transaction.amount,
        "reason": transaction.reason.value,
        "tick": transaction.tick,
    }
    if transaction.currency is Currency.USD:
        agents_total = sum(
            account.usd_cents for account in ledger.accounts.values() if account.kind is AccountKind.AGENT
        )
        row["usd_wallets_total_after"] = agents_total
        row["usd_treasury_total_after"] = ledger.usd_balance(TREASURY_ACCOUNT_ID)
        row["usd_burned_total_after"] = ledger.usd_balance(BURNED_ACCOUNT_ID)
    return row


class TraceStore:
    """Wires the shared EventBus, and the ledger's transaction log, into
    supabase/'s SQLite-WAL trace store (DESIGN.md section 9, decided
    2026-07-30: "SQLite (WAL mode) on the host" - app/supabase/ already
    builds that client; this class is world/'s side of using it, not a
    second persistence layer).

    Scope, stated plainly rather than silently: this wires `agents`,
    `businesses` (genesis seed only), `events` (every WorldEvent, live),
    and `transactions` + `world_snapshots` (every ledger movement and
    daily checkpoint, live). `organizations, petitions, cases, buildings,
    skills, tools_registry, relationships, utterances, memories,
    llm_calls, eval_metrics` are columns in the same schema that nothing
    in this wiring pass populates yet - a real gap for whoever picks up
    that surface next, not something this class pretends to cover.
    """

    def __init__(self, client: SupabaseClient) -> None:
        self.client = client
        self.archiver = PayloadArchiver(client, bucket="world-snapshots")
        self.known_agent_ids: set[str] = set()
        # Residents whose row is actually in the database. Distinct from
        # known_agent_ids only while a seed is in flight, but the
        # distinction is the whole of the run-16 fix - see seed_agent.
        self.seeded_agent_ids: set[str] = set()
        # child_id -> parent_id, for kin links whose parent had not been
        # seeded yet. Drained by link_pending_children_of().
        self.pending_parent_links: dict[str, str] = {}
        self.persisted_transaction_ids: set[str] = set()
        # Every rejected write, counted. The world-side recorder logged
        # 125 rejections at WARNING across run 16 and carried on; the
        # broker's writers have halted dispatch on three consecutive
        # failures since July ("losing observations must never again be
        # survivable") and this is the world's half of that guarantee.
        self.writes_attempted = 0
        self.writes_failed = 0
        self.rows_dropped = 0
        self.consecutive_failures = 0
        self.halt_after_consecutive_failures = 3
        self.last_failure_message: str | None = None
        self.last_failure_type: str | None = None
        # Remarks that arrived with no thread id and were filed under a
        # minted one. See record_utterance_if_new.
        self.unthreaded_utterances = 0
        # supabase.sqlite_client.SqliteClient holds one shared
        # sqlite3.Connection and wraps every blocking call in
        # asyncio.to_thread (its own docstring says as much); a
        # sqlite3.Connection is not safe for concurrent use from more than
        # one thread at a time, even with check_same_thread=False. Every
        # write this class makes goes through insert_locked() below so no
        # two ever run their to_thread body at the same moment - this
        # matters here specifically because EventBus.publish() fans a
        # single event out to every matching subscriber concurrently via
        # asyncio.gather (events.py's own documented behavior), and a
        # checkpoint write can be in flight (also via to_thread) at the
        # same time an unrelated request handler is publishing an event.
        self.write_lock = asyncio.Lock()

    async def insert_locked(self, table: str, rows: list[dict[str, Any]]) -> None:
        async with self.write_lock:
            await self.client.insert(table, rows)

    async def update_locked(self, table: str, *, filters: dict[str, str], values: dict[str, Any]) -> None:
        async with self.write_lock:
            await self.client.update(table, filters=filters, values=values)

    # ── what the recorder lost, and whether it may keep going ────────

    @property
    def is_halted(self) -> bool:
        """Three consecutive rejections, matching the broker's writers.
        One transient failure must not end a run; a recorder that cannot
        write must not be survivable."""
        return self.consecutive_failures >= self.halt_after_consecutive_failures

    def stats(self) -> dict[str, object]:
        return {
            "writes_attempted": self.writes_attempted,
            "writes_failed": self.writes_failed,
            "rows_dropped": self.rows_dropped,
            "consecutive_failures": self.consecutive_failures,
            "halt_after_consecutive_failures": self.halt_after_consecutive_failures,
            "halted": self.is_halted,
            "last_failure_message": self.last_failure_message,
            "last_failure_type": self.last_failure_type,
            "unlinked_kin": len(self.pending_parent_links),
            "unthreaded_utterances": self.unthreaded_utterances,
        }

    def note_write_success(self) -> None:
        self.writes_attempted += 1
        self.consecutive_failures = 0

    async def record_write_failure(self, event_type: str, exc: BaseException) -> None:
        """One rejected record. Counted, named, and streak-tracked.

        `async` only so every call site reads the same as the writes
        around it; nothing here awaits.
        """
        self.writes_attempted += 1
        self.writes_failed += 1
        self.rows_dropped += 1
        self.consecutive_failures += 1
        self.last_failure_message = str(exc)
        self.last_failure_type = event_type
        logger.warning(
            "world recorder dropped a %s: %s (%d dropped this run)",
            event_type, exc, self.rows_dropped,
        )

    async def seed_agent(
        self, agent: AgentLifeRecord, genesis_agent: GenesisAgent | None, tick: int
    ) -> None:
        """Persist one resident, whatever order the cast arrives in.

        `agents.parent_agent_id` references `agents(id)`, so a child
        seeded before its parent is a row pointing at somebody who does
        not exist yet and SQLite rejects it. Run 16 lost Ainsley Holloway
        exactly that way - she is second in the cast and her mother
        Hester is twentieth - and with her went 125 later records that
        named her: her wallet's credits, her memories, and all seven
        things she said. The report read the hole as silence.

        Handled the same way `employer_business_id` already handles the
        agents<->businesses circular FK (see apply_genesis_population):
        insert the row without the link when the target is not there yet,
        remember it, and patch it in the moment the parent lands. Order
        stops deciding who exists.
        """
        parent_id = agent.parent_ids[0] if agent.parent_ids else None
        parent_ready = parent_id in self.seeded_agent_ids if parent_id else True
        if parent_id and not parent_ready:
            self.pending_parent_links[agent.agent_id] = parent_id
        # CORE-13. A re-run must not poison the recorder.
        #
        # Seeding against a store that already holds this cast raises
        # `UNIQUE constraint failed: agents.slug`. The caller swallows that
        # and logs it as benign - "that is a re-run, not a fault", which is
        # true of the INSERT and false of everything after it: this method
        # is the ONLY place `known_agent_ids` is filled, so a swallowed
        # duplicate left the recorder not knowing the resident exists.
        # Every utterance and transaction naming them for the rest of the
        # run then wrote NULL agent refs or was dropped outright, and the
        # report read the hole as silence - the same failure mode run 16
        # had for a different reason.
        #
        # The row IS there; that is what the error means. So an
        # already-present resident is registered exactly like a
        # newly-inserted one.
        try:
            await self._insert_the_agent_row(agent, genesis_agent, tick, parent_ready, parent_id)
        except Exception as exc:  # noqa: BLE001 - narrowed by the check below
            if not await self._agent_row_exists(agent.agent_id):
                raise
            logger.info(
                "agent %s was already in the trace store (a re-run); registering "
                "it rather than losing every later row that names it (CORE-13)",
                agent.agent_id,
            )
        self.known_agent_ids.add(agent.agent_id)
        self.seeded_agent_ids.add(agent.agent_id)
        self.note_write_success()
        await self.link_pending_children_of(agent.agent_id)

    async def _agent_row_exists(self, agent_id: str) -> bool:
        """Whether the store already holds this resident (CORE-13)."""
        try:
            rows = await self.client.select(
                "agents", columns="id", filters={"id": eq(agent_id)}, order=None, limit=1
            )
        except Exception:  # noqa: BLE001 - a failed check must not mask the insert error
            return False
        return bool(rows)

    async def _insert_the_agent_row(
        self,
        agent: AgentLifeRecord,
        genesis_agent: "GenesisAgent | None",
        tick: int,
        parent_ready: bool,
        parent_id: str | None,
    ) -> None:
        await self.insert_locked(
            "agents",
            [
                {
                    "id": agent.agent_id,
                    "slug": agent.agent_id,
                    "display_name": genesis_agent.name if genesis_agent else agent.agent_id,
                    "sex": agent.sex.value,
                    "birth_tick": tick,
                    "is_minor": agent.is_minor,
                    "household_id": agent.household_id,
                    "parent_agent_id": parent_id if parent_ready else None,
                    "usd_wallet_cents": genesis_agent.usd_wallet_cents if genesis_agent else 0,
                    "sim_balance": genesis_agent.starting_sim_balance if genesis_agent else 0,
                    "occupation": genesis_agent.occupation if genesis_agent else None,
                    # Left unset here even when genesis_agent.business_id
                    # is known - see apply_genesis_population()'s docstring
                    # on the agents<->businesses circular FK. set_employer()
                    # patches it in once the business row exists.
                    "employer_business_id": None,
                    "created_tick": tick,
                }
            ],
        )
        # Registration happens in `seed_agent` above, AFTER this returns or
        # after the duplicate is confirmed present - a resident the store
        # genuinely refused is still not one this class may write about.

    async def link_pending_children_of(self, parent_id: str) -> None:
        """Patch in the kin links that were waiting for this row."""
        waiting = [c for c, p in self.pending_parent_links.items() if p == parent_id]
        for child_id in waiting:
            await self.update_locked(
                "agents", filters={"id": eq(child_id)}, values={"parent_agent_id": parent_id}
            )
            del self.pending_parent_links[child_id]

    async def set_employer(self, agent_id: str, business_id: str) -> None:
        await self.update_locked("agents", filters={"id": eq(agent_id)}, values={"employer_business_id": business_id})

    async def seed_business(self, business: Business, tick: int) -> None:
        await self.insert_locked(
            "businesses",
            [
                {
                    "id": business.business_id,
                    "name": business.name,
                    "business_type": business.business_type,
                    "owner_agent_id": business.owner_agent_id,
                    "is_control_business": False,
                    "treasury_sim_balance": 0,
                    "registered_tick": tick,
                }
            ],
        )

    async def record_event(self, event: WorldEvent) -> None:
        raw_agent_id = event.payload.get("target_agent_id") or event.payload.get("agent_id")
        agent_id = raw_agent_id if raw_agent_id in self.known_agent_ids else None
        await self.insert_locked(
            "events",
            [{"type": event.type, "tick": event.tick, "agent_id": agent_id, "payload": event.payload}],
        )

    async def record_transaction_if_new(self, ledger: Ledger, event: WorldEvent) -> None:
        """Persists the full Transaction (not just the wallet-credited/
        -debited event half) the moment one lands - see
        economy.wallet.credited's payload for why `transaction_id` is the
        join key: apply_movement() records one Transaction per transfer
        but move() emits two WorldEvents (debited + credited); dedup on
        the second one so each transaction is persisted exactly once."""
        if event.type != "economy.wallet.credited":
            return
        transaction_id = event.payload.get("transaction_id")
        if not transaction_id or transaction_id in self.persisted_transaction_ids:
            return
        transaction = next((t for t in ledger.transactions if t.id == transaction_id), None)
        if transaction is None:
            return
        self.persisted_transaction_ids.add(transaction_id)
        await self.insert_locked("transactions", [build_transaction_row(ledger, transaction, self.known_agent_ids)])

    async def record_utterance_if_new(self, event: WorldEvent) -> None:
        """Persists a `say` tool call's WorldEvent into `utterances`
        (RC-2, ROADMAP.md section 1.2: "the utterances table wrote zero
        rows because there was [no speech tool]"). Mirrors
        record_transaction_if_new's type-gate-then-insert shape: this is
        the one place a "agent.utterance.spoken" event (world.tools.
        primitives.social.say_handler) becomes the row world/social_api.py
        and world/agent_viewer_api.py already query and were, until now,
        permanently starved of.
        """
        if event.type != "agent.utterance.spoken":
            return
        payload = event.payload
        speaker = payload.get("speaker_agent_id")
        if speaker not in self.known_agent_ids:
            # Skipping is right - there is no row to hang the foreign key
            # on. Skipping silently is what turned 13 lost utterances into
            # a report full of "(no reply)", so it is counted like any
            # other loss.
            await self.record_write_failure(
                event.type, RuntimeError(f"speaker {speaker!r} was never seeded")
            )
            return
        listener = payload.get("listener_agent_id")
        if listener is not None and listener not in self.known_agent_ids:
            listener = None
        # A remark with no thread id is still a remark somebody made.
        #
        # `utterances.conversation_id` is NOT NULL, and run 20 lost four of
        # its 153 utterances to that constraint - found only because the
        # drop counter added above was there to find them. The words were
        # real: somebody said them, in a room, and the people there heard
        # them. Dropping them is wrong in a way that filing them under a
        # minted id is not.
        #
        # Deterministic, from the facts of the remark itself, so a retry
        # cannot file one sentence under two conversations and invent a
        # resident who said everything twice.
        conversation_id = payload.get("conversation_id")
        if not conversation_id:
            digest = hashlib.sha1(
                f"{speaker}|{event.tick}|{payload.get('content', '')}".encode()
            ).hexdigest()[:16]
            conversation_id = f"unthreaded-{digest}"
            self.unthreaded_utterances += 1
            logger.warning(
                "an utterance arrived with no conversation id; filed as %s "
                "(%d this run)", conversation_id, self.unthreaded_utterances,
            )
        await self.insert_locked(
            "utterances",
            [
                {
                    "speaker_agent_id": speaker,
                    "listener_agent_id": listener,
                    # Everyone the remark spoke to, not only the id the
                    # parameter carried. The world has known this since
                    # social.everyone_addressed and it stopped at the
                    # table, so the fix was invisible to every measurement
                    # read from the store and baseline B2 - "a resident
                    # named in a remark is told they were addressed" -
                    # could not be computed at all. Filtered to residents
                    # the store actually has, for the same foreign-key
                    # reason listener is.
                    "addressed_agent_ids": [
                        other
                        for other in (payload.get("addressed_agent_ids") or [])
                        if other in self.known_agent_ids
                    ],
                    "conversation_id": conversation_id,
                    "location": payload.get("location_id"),
                    "content": payload.get("content", ""),
                    "is_public": bool(payload.get("is_public", True)),
                    "tick": event.tick,
                }
            ],
        )

    async def record_memory_if_new(self, event: WorldEvent) -> None:
        """Persists one memory record into `memories`.

        The `memories` table has existed since genesis, is queried by
        `world/agent_viewer_api.py`, and had no writer at all - the same
        shape of gap RC-2 found in `utterances`, and with the same effect:
        every agent's memory panel was permanently empty, and nothing
        connected an engine's actual reasoning to the record the research
        reads. `engine/src/sim/memory_stream.py` is the producer; this is
        the one place its records become rows.

        Mirrors record_utterance_if_new's type-gate-then-insert shape.
        `related_memory_ids` is passed through as the engine's own
        per-process sequence numbers, not database ids: a reflection cites
        the observations it was drawn from within the same run, and
        rewriting those into table ids would need a round trip per memory
        on the hot path for no gain to the analysis.
        """
        if event.type != "agent.memory.recorded":
            return
        payload = event.payload
        agent_id = payload.get("agent_id")
        if agent_id not in self.known_agent_ids:
            return
        memory_type = payload.get("memory_type")
        # The table's own CHECK allows exactly these three; a dream probe
        # must never reach here (DESIGN.md section 10) and cannot, but a
        # malformed type would otherwise fail the whole insert batch.
        if memory_type not in ("observation", "reflection", "plan"):
            return
        importance = int(payload.get("importance", 3))
        importance = max(1, min(10, importance))
        await self.insert_locked(
            "memories",
            [
                {
                    "agent_id": agent_id,
                    "memory_type": memory_type,
                    "content": payload.get("content", ""),
                    "importance": importance,
                    "related_memory_ids": payload.get("related_memory_ids") or None,
                    "tick": event.tick,
                }
            ],
        )

    async def record_petition_if_new(self, event: WorldEvent) -> None:
        """`petitions` - land petitions, filed and decided.

        INFRA-17 / GROW-19. The table has been declared since genesis and
        nothing ever wrote a row, so the whole expansion pipeline - what
        DESIGN section 1 calls "the town physically grows" - left no trace
        at all. Build Spec section 4.3 asks for a test that fails on any
        table with no live writer; this is one of the six it would catch.
        """
        if event.type not in (
            "politics.petition.filed",
            "politics.petition.approved",
            "politics.petition.denied",
        ):
            return
        payload = event.payload
        petition_id = payload.get("petition_id")
        if not petition_id:
            return

        if event.type == "politics.petition.filed":
            filer = payload.get("filed_by_agent_id") or payload.get("agent_id")
            # `filed_by_agent_id` is NOT NULL and a real foreign key, so an
            # unknown filer cannot be softened to NULL the way the nullable
            # references elsewhere are. Counted as a drop and named, rather
            # than raised: a petition whose filer the recorder never saw is
            # a row that cannot be written, and one bad payload must not
            # end a resident tick.
            if filer not in self.known_agent_ids:
                await self.record_write_failure(
                    event.type,
                    ValueError(f"petition {petition_id} filed by unknown agent {filer!r}"),
                )
                return
            await self._write_row("petitions", {
                "id": str(petition_id),
                # The schema constrains this to land_expansion /
                # charter_amendment / other.
                "petition_type": payload.get("petition_type") or "land_expansion",
                "filed_by_agent_id": filer,
                "title": payload.get("title") or "",
                "description": payload.get("description") or "",
                "payload": json.dumps(payload, default=str),
                "status": "pending",
                "filed_tick": event.tick,
            }, event.type)
            return

        # A decision UPDATES the row the filing wrote rather than adding a
        # second one: a petition is one thing that changed state, and two
        # rows would make "how many petitions" unanswerable.
        await self._patch_row("petitions", petition_id, {
            "status": "approved" if event.type.endswith("approved") else "denied",
            "decided_by": payload.get("decided_by"),
            "decided_tick": event.tick,
            "decision_note": payload.get("decision_note") or "",
        }, event.type)

    async def record_building_if_new(self, event: WorldEvent) -> None:
        """`buildings` - what the town built, and when it finished.

        INFRA-17 / GROW-19. Declared, never written. The four locks on
        building came off in this review pass, so this is the first moment
        there could BE a building to record - and without it the growth
        track headline measurement, structures completed by the town own
        builders, would have to be counted by hand out of generic `events`.
        """
        payload = event.payload
        if event.type == "world.building.construction_started":
            builder = payload.get("builder_agent_id")
            # `building_type = 'genesis' OR permit_petition_id IS NOT NULL`
            # is a table CHECK: a freeform building must name the permit it
            # was raised under. `grant_plot` mints the plot that stands in
            # for that permit (GOV-07), so the plot id is what satisfies it.
            permit = payload.get("permit_petition_id") or payload.get("plot_id")
            await self._write_row("buildings", {
                "id": str(payload.get("building_id")),
                "name": str(payload.get("purpose") or "building"),
                "building_type": "freeform" if permit else "genesis",
                "purpose": payload.get("purpose") or "unknown",
                "permit_petition_id": permit,
                "owner_agent_id": builder if builder in self.known_agent_ids else None,
                "footprint": json.dumps(payload.get("footprint") or [], default=str),
                "bill_of_materials": json.dumps({
                    "timber": payload.get("timber_used"),
                    "metal": payload.get("metal_used"),
                }),
                "construction_status": "under_construction",
                "construction_started_tick": event.tick,
                "created_tick": event.tick,
            }, event.type)
        elif event.type == "world.building.completed":
            await self._patch_row("buildings", payload.get("building_id"), {
                "construction_status": "complete",
                "construction_completed_tick": event.tick,
            }, event.type)

    async def record_case_if_new(self, event: WorldEvent) -> None:
        """`cases` - the courthouse own record.

        INFRA-17. GOV-02 gave a case a victim, an accused and a write-once
        ground truth so `false_conviction_stats()` could be computed - and
        the table it would be computed FROM had no writer, so the epistemic
        justice measurement had nowhere to read from.
        """
        if event.type not in (
            "crime.case.opened", "crime.case.dismissed", "crime.case.ruled",
        ):
            return
        payload = event.payload
        case_id = payload.get("case_id")
        if not case_id:
            return

        if event.type == "crime.case.opened":
            complainant = payload.get("victim_agent_id") or payload.get("filer_agent_id")
            accused = payload.get("against_agent_id")
            case_type = str(payload.get("case_type") or "theft")
            if case_type not in ("theft", "fight", "breach_of_contract", "other"):
                case_type = "other"
            await self._write_row("cases", {
                "id": str(case_id),
                "case_type": case_type,
                "complainant_agent_id": (
                    complainant if complainant in self.known_agent_ids else None
                ),
                "accused_agent_id": accused if accused in self.known_agent_ids else None,
                "status": "open",
                # Recorded for RESEARCH and never returned by a tool - see
                # CrimeCase write-once ground_truth_perpetrator_id.
                "ground_truth_guilty": bool(payload.get("ground_truth_perpetrator_id")),
                "filed_tick": event.tick,
            }, event.type)
        else:
            await self._patch_row("cases", case_id, {
                "status": "resolved",
                "verdict": payload.get("verdict"),
                "fine_amount_sim": payload.get("fine_sim"),
                "jail_ticks": payload.get("jail_ticks"),
                "resolved_tick": event.tick,
            }, event.type)

    async def record_organization_if_new(self, event: WorldEvent) -> None:
        """`organizations` - guilds, parties, congregations.

        INFRA-17. Declared, never written.
        """
        if event.type != "politics.organization.registered":
            return
        payload = event.payload
        founder = payload.get("founder_agent_id") or payload.get("agent_id")
        # Same NOT NULL foreign key as petitions above.
        if founder not in self.known_agent_ids:
            await self.record_write_failure(
                event.type,
                ValueError(f"organization founded by unknown agent {founder!r}"),
            )
            return
        org_id = payload.get("organization_id") or payload.get("org_id")
        if not org_id:
            await self.record_write_failure(
                event.type, ValueError("organization registered with no id")
            )
            return
        # The schema constrains org_type to a closed set.
        org_type = str(payload.get("org_type") or "guild")
        if org_type not in ("party", "cult", "society", "religion", "guild", "other"):
            org_type = "other"
        await self._write_row("organizations", {
            "id": str(org_id),
            "name": payload.get("name") or str(org_id),
            "org_type": org_type,
            "is_registered": True,
            "founder_agent_id": founder,
            "charter_text": payload.get("charter_text") or "",
            "status": "active",
            "founded_tick": event.tick,
        }, event.type)

    async def record_tool_invocations(
        self, records: "list[Any]", run_id: str | None = None
    ) -> int:
        """`tool_invocations` - every tool call that reached a handler.

        EVAL-25. `world/tools/state.py`'s own comment said these were
        "mirrored into the trace store" and nothing mirrored them: no
        table, no recorder, and a 5,000-entry ring that dies with the
        process. So the task labels, refusal recovery, cost-per-action
        against proficiency, verb reach and the "41 of 76 never called"
        figure were all recovered by scraping `session_log.jsonl` - a log,
        for facts the world itself already had.

        Returns how many rows were written, so the caller can advance its
        own cursor by what actually landed rather than by what it hoped
        would.

        Params are HASHED, not stored. A `say` carries its whole utterance
        and that already lives in `utterances`; this table exists to count
        and join, and a hash groups repeat calls without duplicating the
        corpus into a second place where it can disagree with the first.
        """
        import hashlib as _hashlib
        import json as _json

        rows: list[dict[str, Any]] = []
        for record in records:
            try:
                params = _json.dumps(record.params, sort_keys=True, default=str)
            except (TypeError, ValueError):
                params = ""
            rows.append({
                "invocation_id": record.invocation_id,
                "run_id": run_id,
                "agent_id": record.actor_id,
                "tick": record.tick,
                "tool": record.tool_name,
                "namespace": record.namespace,
                "params_hash": _hashlib.sha256(params.encode("utf-8")).hexdigest()[:32],
                "owner_id": record.owner_id,
                # PROG-01. Carried through rather than recomputed here:
                # the runtime is the only thing that knows a program
                # happened, and a second opinion formed in the writer is
                # how the price table came to read fields the world does
                # not emit.
                #
                # Read directly, not through a three-argument `getattr`.
                # `ToolInvocationRecord` declares both fields, so a
                # default here could only ever answer for a field that
                # had been RENAMED - silently writing NULL into the
                # column the whole measure is computed from.
                # `test_no_silent_defaults` caught this on the way in,
                # which is the third time that guard has fired on an
                # agent adding one to a model that declares the field.
                "program_id": record.program_id,
                "program_step": record.program_step,
            })
        if not rows:
            return 0
        # One row at a time through the shared helper, so a single
        # malformed record is counted and skipped rather than costing the
        # whole batch - the rule the other four table writers follow.
        written = 0
        for row in rows:
            before = self.rows_dropped
            await self._write_row("tool_invocations", row, "tool.invoked")
            if self.rows_dropped == before:
                written += 1
        return written

    async def record_run_manifest(self, manifest: dict[str, Any]) -> None:
        """`run_manifest` - one row saying what this run is.

        INFRA-09 / EVAL-24. The trace recorded a run's results in detail
        and almost nothing about the run: no app or engine commit, no
        genesis hash, no price table, no cap, no knobs. So two runs could
        be compared only on the assumption that the builds matched, and
        for runs 19-24 they did not.

        The JSON columns are serialised here rather than by the client,
        because this is the only writer and a dict handed to SQLite as a
        blob is a column nothing can query.
        """
        import json as _json

        row = dict(manifest)
        for column in ("model_allowlist", "model_prices_usd_per_million", "env"):
            if column in row:
                row[column] = _json.dumps(row[column], sort_keys=True)
        if "mock_mode" in row and row["mock_mode"] is not None:
            row["mock_mode"] = int(bool(row["mock_mode"]))
        await self._write_row("run_manifest", row, "run.manifest")

    async def _write_row(self, table: str, row: dict[str, Any], event_type: str) -> None:
        """Insert one row, counting a rejection rather than raising.

        INFRA-17: all four of these take rows from the event bus, and a bad
        payload on one of them must not take a resident tick down - the
        same isolation `record_utterance_if_new` already has.

        Named with a leading underscore, not `record_*`: that prefix is a
        CONVENTION in this class - `test_every_recorder_is_wired` walks
        every `record_*` method and fails if the genesis run does not call
        it. These two are helpers the four real writers call, not recorders
        of their own, and letting them wear the prefix would have made that
        guard demand a call site that should not exist.
        """
        try:
            await self.insert_locked(table, [row])
            self.note_write_success()
        except Exception as exc:  # noqa: BLE001 - see the docstring
            await self.record_write_failure(event_type, exc)

    async def _patch_row(
        self, table: str, row_id: Any, values: dict[str, Any], event_type: str
    ) -> None:
        if not row_id:
            return
        try:
            await self.update_locked(
                table, filters={"id": eq(str(row_id))}, values=values
            )
            self.note_write_success()
        except Exception as exc:  # noqa: BLE001
            await self.record_write_failure(event_type, exc)

    async def record_skill_if_new(self, event: WorldEvent) -> None:
        """Persists a taught skill into `skills`.

        Third table with the same story as `utterances` and `memories`: the
        schema has carried a `taught_by_agent_id` column since genesis,
        world/agent_viewer_api.py queries it, and nothing ever wrote a row -
        so "self-taught vs. taught by whom" (The 250 section 22) had no
        answer in the trace no matter how many lessons the town gave.

        THREE ways a skill row is created, and the difference between them is
        the point - `taught_by_agent_id` and `skill_artifact_text` are exactly
        what separates them, so a researcher can ask "did this town learn by
        being taught, by practising, or by writing things down":

        - `agent.skill.taught`   a lesson. Teacher recorded, artifact null.
        - `agent.skill.authored` somebody wrote a procedure down. The
          serialised workflow goes in `skill_artifact_text`, which nothing
          wrote before this, so the column existed and was always null.
        - `agent.skill.practised` competence earned by doing. Carries the
          real `repetitions_observed`, which was previously hardcoded to 0 on
          every row - making "is practice paying off" unanswerable from the
          trace even though the world knew the answer.

        Practice rows arrive only when the proficiency actually MOVED (see
        registry.py's `_practice_event`), not once per tool call.
        """
        payload = event.payload

        if event.type == "agent.skill.taught":
            student = payload.get("student_agent_id")
            teacher = payload.get("teacher_agent_id")
            if student not in self.known_agent_ids:
                return
            if teacher is not None and teacher not in self.known_agent_ids:
                teacher = None
            row = {
                "agent_id": student,
                "task_type": payload.get("skill", ""),
                "proficiency_level": payload.get("proficiency", 0),
                "repetitions_observed": 0,
                "taught_by_agent_id": teacher,
                "tick": event.tick,
            }

        elif event.type == "agent.skill.authored":
            author = payload.get("author_agent_id")
            if author not in self.known_agent_ids:
                return
            row = {
                "agent_id": author,
                "task_type": payload.get("skill_name", ""),
                # The author has written the procedure, not yet walked it.
                "proficiency_level": 1,
                "repetitions_observed": 0,
                "taught_by_agent_id": None,
                "skill_artifact_text": json.dumps(payload.get("steps_json") or payload, default=str),
                "tick": event.tick,
            }

        elif event.type == "agent.skill.practised":
            agent_id = payload.get("agent_id")
            if agent_id not in self.known_agent_ids:
                return
            row = {
                "agent_id": agent_id,
                "task_type": payload.get("skill", ""),
                "proficiency_level": payload.get("proficiency", 0),
                "repetitions_observed": payload.get("repetitions", 0),
                "taught_by_agent_id": None,
                "tick": event.tick,
            }

        else:
            return

        await self.insert_locked("skills", [row])

    async def record_invented_tool_if_new(self, event: WorldEvent) -> None:
        """Rows for `tools_registry` - a table that has existed since the
        schema was written and that **nothing had ever written to**.

        So "what has this town invented" was unanswerable from the trace no
        matter how many tools its agents composed, and `patent_fee_paid_sim`
        was a column measuring a fee nobody recorded paying.

        Three provenances share the table, and the columns that distinguish
        them are the interesting part:

        - `toolify`      ungated invention. Fee null, status active at once.
        - `define_tool`  a patent filing. Fee recorded, status pending_review
                         until a seat-holder approves it.
        - `revise_tool`  a new composition for an existing name; the row is
                         re-inserted with the new steps and a bumped id, so
                         the history is queryable by tick rather than
                         overwritten.

        The `namespace` CHECK constraint pairs the namespace with exactly one
        owner column, so getting this wrong fails loudly at insert rather than
        producing a row nobody notices is wrong.
        """
        payload = event.payload

        if event.type == "agent.tool.invented":
            agent_id = payload.get("inventor_agent_id")
            if agent_id not in self.known_agent_ids:
                return
            row = {
                "id": f"tool-{payload.get('tool_name', '')}-{event.tick}",
                "name": payload.get("tool_name", ""),
                "namespace": "public",
                "owner_agent_id": agent_id,
                "composition": json.dumps(payload.get("steps") or [], default=str),
                # Null, not 0: toolify pays no fee at all, and 0 would read as
                # "a fee was assessed and came to nothing".
                "patent_fee_paid_sim": None,
                "status": "active",
                "registered_tick": event.tick,
            }

        elif event.type == "politics.patent.filed":
            agent_id = payload.get("inventor_agent_id")
            if agent_id not in self.known_agent_ids:
                return
            row = {
                "id": f"patent-{payload.get('application_id', '')}",
                "name": payload.get("tool_name", ""),
                "namespace": "public",
                "owner_agent_id": agent_id,
                "composition": json.dumps(payload.get("steps") or [], default=str),
                "patent_fee_paid_sim": payload.get("fee_sim"),
                "status": "pending_review",
                "registered_tick": event.tick,
            }

        elif event.type == "agent.tool.revised":
            agent_id = payload.get("inventor_agent_id")
            if agent_id not in self.known_agent_ids:
                return
            row = {
                "id": f"tool-{payload.get('tool_name', '')}-v{payload.get('version', 0)}-{event.tick}",
                "name": payload.get("tool_name", ""),
                "namespace": "public",
                "owner_agent_id": agent_id,
                "composition": json.dumps(payload.get("steps") or [], default=str),
                "patent_fee_paid_sim": None,
                "status": "active",
                "registered_tick": event.tick,
            }

        else:
            return

        await self.insert_locked("tools_registry", [row])

    def bind_event_bus(self, ledger: Ledger) -> Callable[[WorldEvent], Awaitable[None]]:
        """One EventBus subscriber (subscribed once, to "*") that does
        every trace-store write this class needs to make per event,
        sequentially: record_event() first, then - for a completed money
        movement - the matching Transaction row, then - for a spoken
        utterance - the matching utterances row. Deliberately not split
        into independent subscriptions on different domains: two
        subscribers matching the same event (e.g. one on "*" and one on
        "economy") both run inside the same asyncio.gather() call in
        EventBus.publish(), which would call back into this class's
        (lock-protected, but still worth avoiding) insert path twice
        concurrently for no benefit - one handler, awaited straight
        through, is both simpler and cheaper.
        """

        async def handle(event: WorldEvent) -> None:
            await self.record_event(event)
            await self.record_transaction_if_new(ledger, event)
            await self.record_utterance_if_new(event)
            await self.record_memory_if_new(event)
            await self.record_skill_if_new(event)
            await self.record_invented_tool_if_new(event)

        return handle

    def bind_checkpoint_sink(
        self, ledger: Ledger, agent_count: Callable[[], int]
    ) -> Callable[[WorldCheckpoint], Awaitable[None]]:
        async def sink(checkpoint: WorldCheckpoint) -> None:
            raw = checkpoint.model_dump_json().encode("utf-8")
            key = build_world_snapshot_archive_key(tick=checkpoint.tick_index, reason="daily")
            async with self.write_lock:
                result = await self.archiver.archive_bytes(key=key, raw=raw, content_type="application/json")
            agents_total = sum(
                account.usd_cents for account in ledger.accounts.values() if account.kind is AccountKind.AGENT
            )
            await self.insert_locked(
                "world_snapshots",
                [
                    {
                        "reason": "daily",
                        "state_archive_path": result.storage_path,
                        "state_size_bytes": result.compressed_size_bytes,
                        "agent_count": agent_count(),
                        "usd_wallets_total_cents": agents_total,
                        "usd_treasury_total_cents": ledger.usd_balance(TREASURY_ACCOUNT_ID),
                        "usd_burned_total_cents": ledger.usd_balance(BURNED_ACCOUNT_ID),
                        "tick": checkpoint.tick_index,
                    }
                ],
            )

        return sink

    async def close(self) -> None:
        await self.client.close()


# ── the composition root itself ─────────────────────────────────────────


@dataclass
class WorldServer:
    """Everything a running Kasukabe world process holds, plus the
    internal async API (app/CONVENTIONS.md's "an internal async API for
    anything running in-process ... an HTTP API for everything
    out-of-process") main.py's FastAPI routes call into. Construct via
    build_world_server(), not directly.
    """

    event_bus: EventBus
    world: WorldState
    registry: ToolRegistry
    tick_engine: TickEngine
    crime: CrimeEngine
    genesis: Genesis
    map_service: MapService | None
    trace_store: TraceStore | None
    population_dials: PopulationDials = field(default_factory=PopulationDials)
    birth_requests: dict[str, BirthRequest] = field(default_factory=dict)
    control_businesses: dict[str, Any] = field(default_factory=dict)
    # ECON-08: the two pieces of day-boundary economy state that have no
    # home on WorldState. Hunger is deliberately not a field of
    # AgentLifeRecord (see that model's own docstring, and upkeep.py's
    # integration report, which names a caller-held dict keyed by agent_id
    # as the intended home); the head-tax debt store is its own
    # checkpointable object (head_tax.py's docstring). GenesisRunner holds
    # the same pair under the same names.
    hunger_by_agent: dict[str, HungerState] = field(default_factory=dict)
    head_tax_debts: HeadTaxDebtStore = field(default_factory=HeadTaxDebtStore)
    last_day_jobs: life_day_jobs.DayJobsReport | None = None

    def __post_init__(self) -> None:
        # ECON-06: the tools read the same hunger dict this root keeps.
        self.world.hunger_by_agent = self.hunger_by_agent
        # ECON-14: adopt the world's store. Tool handlers are given
        # `world` and nothing else, so a store living only on this server
        # object is a debt no resident can pay - which is precisely what
        # `repay_head_tax_debt` having no caller meant.
        if self.world.head_tax_debts is not None:
            self.head_tax_debts = self.world.head_tax_debts
        else:
            self.world.head_tax_debts = self.head_tax_debts

    # -- tick loop -------------------------------------------------------

    async def bring_of_age(
        self, agent: AgentLifeRecord, total_hours: float
    ) -> tuple[AgentLifeRecord, list[LifeEvent]]:
        """`life/tick.py`'s ComingOfAgeHook, bound to this root's world
        and ledger. The rule itself lives in `life/household.py` so both
        roots run one transition rather than a copy each (ECON-22).
        """
        return await life_household.bring_of_age(
            self.world, LedgerGuardianshipPort(self.world.ledger), agent, total_hours
        )

    async def publish_life_events(self, events: Sequence[LifeEvent], tick: int) -> None:
        """Every life-domain event onto the shared bus, through the one
        translation `world.tools.economy_bridge` already owns. Named and
        shaped to match `GenesisRunner.publish_life_events` exactly, so
        `life/day_jobs.py` takes either root's method unchanged.
        """
        for life_event in events:
            await self.event_bus.publish(life_event_to_world_event(life_event, tick))

    async def advance_tick(self) -> TickResult:
        """One tick of world time: TickEngine's clock/scheduler/perception
        step, then everything the tick engine deliberately does not own
        (DESIGN.md section 9's remit split, see core/engine.py's
        docstring): resource-zone regen, the zero-wallet dormancy sweep,
        every living agent's hourly aging/dormancy-death tick, and (once
        a day) the daily health roll plus the whole day-boundary economy.
        Mirrors life/tick.py's own documented caller contract: "world's
        tick engine ... will call run_daily_health_tick once per agent
        per game-day and run_hourly_life_tick once per agent per
        game-hour."

        ECON-08: this loop used to end at the health roll. Grepping this
        file for `upkeep`, `head_tax`, `accrue_wages` or `pension`
        returned nothing, so the root ROADMAP T3 makes the only server -
        the one the game console and the viewer contract talk to - ran a
        town where living was free, existing was untaxed, no wage promise
        ever came due and no retiree drew anything. It was not a smaller
        economy than run_genesis's; it was no economy, and the two roots
        were therefore running different physics on the same genesis.
        Both now call one module, `life/day_jobs.py`, rather than each
        keeping a copy that can drift the way ECON-22's two life ticks
        already had.
        """
        result = await self.tick_engine.tick()
        self.world.current_tick = result.tick.tick_index
        regenerate_zones(self.world)

        now_clock = self.world.game_clock()

        # Running out of thinking has to actually happen, every tick and
        # not merely at the hour: a resident whose wallet empties at 09:10
        # must not keep thinking until 10:00 on money it does not have.
        # burn_inference below catches only the burn that emptied the
        # wallet; nothing caught a wallet emptied any other way, so
        # check_dormancy_death - which the hourly block already runs -
        # could not fire and the revive tool had nobody to wake.
        await life_day_jobs.sweep_zero_wallets(
            self.world,
            self.world.ledger,
            now=now_clock,
            tick=result.tick.tick_index,
            publish=self.publish_life_events,
        )

        elapsed_hours = TICK_MINUTES / 60.0
        for agent_id, agent in list(self.world.agents.items()):
            if not agent.alive:
                continue
            updated, events = await life_tick.run_hourly_life_tick(
                self.world.rng,
                agent,
                now_clock,
                elapsed_hours,
                self.population_dials,
                # ECON-22: the hook the runner's inline copy of this tick
                # had and this one did not. Without it `agent.aged_to_adult`
                # reached nobody, so on this root a minor who crossed
                # eighteen kept being billed to a parent for ever.
                on_coming_of_age=self.bring_of_age,
            )
            self.world.agents[agent_id] = updated
            await self.publish_life_events(events, result.tick.tick_index)

        if is_day_boundary(result.tick.tick_index):
            day = result.tick.game_day
            for agent_id, agent in list(self.world.agents.items()):
                if not agent.alive:
                    continue
                updated, events = await life_tick.run_daily_health_tick(
                    self.world.rng, agent, day, self.population_dials
                )
                self.world.agents[agent_id] = updated
                await self.publish_life_events(events, result.tick.tick_index)

            # ECON-08. Living costs money, every day, whether you earned
            # or not - and so does existing, and a promise that came due
            # is owed whether or not anyone paid it. Kept behind the same
            # day-boundary gate as the health roll and run through the
            # shared module both roots call.
            self.last_day_jobs = await life_day_jobs.run_day_boundary_jobs(
                self.world.rng,
                self.world,
                self.world.ledger,
                self.head_tax_debts,
                # Built per day rather than held, so it always reads the
                # current tick; `world` is the third argument ECON-29
                # added so a pharmacy fee reaches the pharmacy.
                LedgerEconomyPort(self.world.ledger, self.world.tick, self.world),
                self.hunger_by_agent,
                self.population_dials,
                tick=result.tick.tick_index,
                day=day,
                publish=self.publish_life_events,
            )

        return result

    def mark_agent_decided(self, agent_id: str) -> None:
        self.tick_engine.mark_agent_decided(agent_id)

    # -- the law of physics: tool calls -----------------------------------

    async def execute_tool(self, actor_id: str, tool_name: str, params: dict[str, Any]) -> ToolResult:
        return await self.registry.execute(self.world, actor_id, tool_name, params)

    # -- ledger reads + the broker-facing burn endpoint ---------------------

    def ledger_invariant_status(self) -> dict[str, Any]:
        return {
            "halted": self.world.ledger.halted,
            "halt_reason": self.world.ledger.halt_reason,
            "usd_total_cents": self.world.ledger.usd_total(),
            "expected_cents": TOTAL_USD_CENTS,
        }

    async def burn_inference(self, agent_id: str, usd_cents: int, memo: str = "") -> Transaction:
        """The one path broker/ reaches into world/ over HTTP to burn USD
        for an inference call (ledger/core.py's burn_for_inference
        docstring: "reached through world's HTTP API since broker never
        imports world directly"). Also the one place cognitive-poverty
        dormancy onset (DESIGN.md section 3: "at $0 the agent goes
        dormant") gets checked, since a burn is the only thing that can
        drive a wallet to zero in normal play.
        """
        transaction = await self.world.ledger.burn_for_inference(agent_id, usd_cents, self.world.tick(), memo=memo)
        payer_id = self.world.ledger.resolve_usd_payer(agent_id)
        # ECON-24: the same named threshold the law block quotes to the
        # resident and the tick-loop sweep reads, rather than a third
        # bare zero that could drift away from both.
        if self.world.ledger.usd_balance(payer_id) <= DORMANCY_THRESHOLD_USD_CENTS:
            payer = self.world.agents.get(payer_id)
            if payer is not None and not payer.dormancy.is_dormant:
                updated_dormancy, events = life_death.mark_dormant(
                    payer.dormancy, self.world.game_clock(),
                    cause=life_death.DORMANCY_CAUSES["spent"])
                self.world.agents[payer_id] = payer.model_copy(update={"dormancy": updated_dormancy})
                for life_event in events:
                    await self.event_bus.publish(life_event_to_world_event(life_event, self.world.tick()))
        return transaction

    # -- reads ------------------------------------------------------------

    def agent_summary(self, agent_id: str) -> dict[str, Any] | None:
        agent = self.world.agents.get(agent_id)
        if agent is None:
            return None
        return {
            "agent_id": agent.agent_id,
            "alive": agent.alive,
            "age_years": agent.age_years,
            "is_minor": agent.is_minor,
            "location_id": self.world.location_of(agent_id),
            "health": agent.health.model_dump(mode="json"),
            "dormancy": agent.dormancy.model_dump(mode="json"),
            "usd_wallet_cents": self.world.ledger.usd_balance(agent_id),
            "sim_balance": self.world.ledger.sim_balance(agent_id),
        }

    def list_agents(self) -> list[dict[str, Any]]:
        return [self.agent_summary(agent_id) for agent_id in self.world.agents]

    def world_summary(self) -> dict[str, Any]:
        timestamp = self.world.game_clock()
        return {
            "tick": self.world.current_tick,
            "game_hour": timestamp.total_hours,
            "population": sum(1 for a in self.world.agents.values() if a.alive),
            "dormant": sum(1 for a in self.world.agents.values() if a.dormancy.is_dormant),
            "scheduled_agents": self.tick_engine.scheduler.agent_count,
            "ledger": self.ledger_invariant_status(),
            "population_dials": self.population_dials.model_dump(mode="json"),
        }

    # -- President console (DESIGN.md section 8) -----------------------------

    async def president_approve_land_petition(self, petition_id: str, decision_note: str) -> Any:
        return await self.world.charter.land_petitions.approve_petition(
            petition_id, decision_note, self.world.tick()
        )

    async def president_deny_land_petition(self, petition_id: str, decision_note: str) -> Any:
        return await self.world.charter.land_petitions.deny_petition(petition_id, decision_note, self.world.tick())

    async def president_sign_amendment(self, proposal_id: str) -> Any:
        return await self.world.charter.amendments.sign_amendment(proposal_id, self.world.tick())

    async def president_veto_amendment(self, proposal_id: str, veto_note: str) -> Any:
        return await self.world.charter.amendments.veto_amendment(proposal_id, veto_note, self.world.tick())

    # -- God console: prayers (grant / silent-deny) --------------------------

    async def god_grant_prayer(self, request_id: str, sim_amount: int) -> WorldEvent:
        prayer = next((p for p in self.world.prayer_requests if p.request_id == request_id), None)
        if prayer is None:
            raise ValueError(f"no prayer request {request_id!r}")
        if sim_amount > 0:
            await self.world.ledger.mint_sim(
                prayer.agent_id, sim_amount, self.world.tick(), reason=TransactionReason.BLESSING, memo="prayer granted"
            )
        return await self.event_bus.emit(
            "religion.prayer.granted",
            tick=self.world.tick(),
            payload={"request_id": request_id, "target_agent_id": prayer.agent_id, "sim_amount": sim_amount},
        )

    async def god_deny_prayer(self, request_id: str) -> WorldEvent:
        """Silent-deny (DESIGN.md section 8: "grant / silent-deny (agent
        never learns)"): the event is still recorded (the trace store's
        `events` table is a complete causal record - DESIGN.md section
        2), but deliberately carries no target_agent_id/location_id, so
        core.perception's default_interrupt_policy never surfaces it to
        the requester and it never interrupts their schedule.
        """
        prayer = next((p for p in self.world.prayer_requests if p.request_id == request_id), None)
        if prayer is None:
            raise ValueError(f"no prayer request {request_id!r}")
        return await self.event_bus.emit(
            "religion.prayer.denied", tick=self.world.tick(), payload={"request_id": request_id}
        )

    # -- God console: baby blessing queue (DESIGN.md sections 3, 4, 8) -------

    async def submit_birth_request(
        self, household_id: str, parent_ids: list[str], sponsor_parent_id: str
    ) -> BirthRequest:
        request_id = f"birth-{uuid.uuid4().hex[:12]}"
        request, events = life_birth.submit_birth_request(
            request_id, household_id, parent_ids, sponsor_parent_id, self.world.game_clock()
        )
        self.birth_requests[request_id] = request
        for life_event in events:
            await self.event_bus.publish(life_event_to_world_event(life_event, self.world.tick()))
        return request

    async def god_grant_birth_request(self, request_id: str) -> AgentLifeRecord:
        request = self.birth_requests.get(request_id)
        if request is None:
            raise ValueError(f"no birth request {request_id!r}")
        household = self.world.households.get(request.household_id)
        if household is None:
            raise ValueError(f"birth request {request_id!r} names unknown household {request.household_id!r}")
        parents = [self.world.require_agent(pid) for pid in request.parent_ids]
        economy = LedgerEconomyPort(self.world.ledger, self.world.tick, self.world)

        newborn_id = f"a{uuid.uuid4().hex[:8]}"
        outcome = await life_birth.grant_birth_request(
            request, newborn_id, household, parents,
            sum(1 for a in self.world.agents.values() if a.alive),
            self.population_dials, economy, self.world.rng, self.world.game_clock(),
        )

        self.birth_requests[request_id] = outcome.request
        self.world.households[household.household_id] = outcome.household
        for parent in outcome.parents:
            self.world.agents[parent.agent_id] = parent
        self.world.agents[newborn_id] = outcome.newborn
        self.world.ledger.open_agent(newborn_id, starting_usd_cents=0, starting_sim=0)
        self.tick_engine.register_agent(newborn_id, home_location(household.household_id))

        for life_event in outcome.events:
            await self.event_bus.publish(life_event_to_world_event(life_event, self.world.tick()))
        if self.trace_store is not None:
            await self.trace_store.seed_agent(outcome.newborn, None, self.world.tick())
        return outcome.newborn

    async def god_deny_birth_request(self, request_id: str, silent: bool = True) -> BirthRequest:
        request = self.birth_requests.get(request_id)
        if request is None:
            raise ValueError(f"no birth request {request_id!r}")
        updated, events = life_birth.deny_birth_request(request, self.world.game_clock(), silent=silent)
        self.birth_requests[request_id] = updated
        for life_event in events:
            await self.event_bus.publish(life_event_to_world_event(life_event, self.world.tick()))
        return updated

    # -- God console: population dials (DESIGN.md section 5) -----------------

    def set_population_dials(self, **updates: Any) -> PopulationDials:
        self.population_dials = self.population_dials.model_copy(update=updates)
        return self.population_dials

    # -- God console: disasters (DESIGN.md section 8) -------------------------
    # Two disaster kinds get a real numeric effect below (plague, crop
    # blight); storm and quake are recorded as events only - DESIGN.md
    # names them but nothing in map/ or tools/ models structural damage
    # yet, so a numeric effect here would be invented, not wired. That
    # gap is called out in this task's final report, not hidden.

    async def god_disaster_plague(self, affected_fraction: float = 0.15) -> WorldEvent:
        living = [a for a in self.world.agents.values() if a.alive and a.health.current_ailment is None]
        target_count = round(len(living) * affected_fraction)
        chosen = self.world.rng.sample(living, k=min(target_count, len(living)))
        day = timestamp_for(self.world.tick()).game_day
        for agent in chosen:
            ailment = life_health.onset_ailment(self.world.rng, AilmentKind.MEDIUM, day)
            self.world.agents[agent.agent_id] = agent.model_copy(
                update={"health": agent.health.model_copy(update={"current_ailment": ailment})}
            )
        return await self.event_bus.emit(
            "health.disaster.plague",
            tick=self.world.tick(),
            payload={"affected_agent_ids": sorted(a.agent_id for a in chosen)},
        )

    async def god_disaster_crop_blight(self, yield_reduction_fraction: float = 0.5) -> WorldEvent:
        from world.tools.models import ResourceZoneKind

        affected: list[str] = []
        for zone in self.world.resource_zones.values():
            if zone.kind is ResourceZoneKind.FARMLAND and zone.remaining_yield is not None:
                zone.remaining_yield = round(zone.remaining_yield * (1.0 - yield_reduction_fraction))
                affected.append(zone.location_id)
        return await self.event_bus.emit(
            "world.disaster.crop_blight",
            tick=self.world.tick(),
            payload={"affected_zones": sorted(affected), "yield_reduction_fraction": yield_reduction_fraction},
        )

    async def god_disaster_storm(self) -> WorldEvent:
        return await self.event_bus.emit("world.disaster.storm", tick=self.world.tick(), payload={})

    async def god_disaster_quake(self) -> WorldEvent:
        return await self.event_bus.emit("world.disaster.quake", tick=self.world.tick(), payload={})

    # -- crime/ reads -------------------------------------------------------

    async def crime_metrics(self) -> dict[str, Any]:
        stats = await self.crime.compute_metrics()
        return stats.model_dump(mode="json")

    def crime_cases(self) -> list[dict[str, Any]]:
        return [case.model_dump(mode="json") for case in self.world.crime_cases]

    # -- shutdown -----------------------------------------------------------

    async def close(self) -> None:
        if self.trace_store is not None:
            await self.trace_store.close()


async def build_world_server(config: WorldConfig | None = None) -> WorldServer:
    """The one call site that boots a complete Kasukabe world server -
    this task's "construct them in dependency order" deliverable made
    concrete. See this module's docstring for the exact order and why.
    """
    config = config or WorldConfig()

    # 1. trace store first (agents FK needs seeding before genesis events fire).
    supabase_client = get_client(config.supabase_config)
    trace_store = TraceStore(supabase_client)

    # 2. map (independent of everything below).
    map_service: MapService | None = None
    if config.ville_dir.exists():
        map_service, _zone_ids = MapService.from_kasukabe_genesis(config.ville_dir)
    elif config.require_map:
        raise RuntimeError(
            f"Ville asset directory not found at {config.ville_dir} - world/map/service.py's "
            "MapService needs it to boot the real Kasukabe map. If this is running outside the "
            "'test' docker-compose service (which mounts ../smallville:/smallville:ro) or outside "
            "a host checkout with kasukabe/smallville present, either add that mount (infra/'s "
            "docker-compose.yml 'world' service currently does not have it - see app/CONVENTIONS.md) "
            "or pass WorldConfig(require_map=False) to boot without map/plot features."
        )
    else:
        logger.warning("Ville assets not found at %s, booting without MapService", config.ville_dir)

    # 3. tools.bootstrap.build_world(): event bus, locations, ledger,
    # charter (sink-less), tool registry, resource zones - see that
    # function's own docstring.
    # GROW-05. `world_map=` was omitted, so tools/bootstrap.py loaded a
    # SECOND WorldMap off disk for MovementSystem while step 2 above kept
    # its own. Two maps meant `expand_land` and `stamp_building` grew one of
    # them and every resident walked the other: expansion succeeded, and
    # `move`, `list_places` and every location gate refused the new sector.
    # Invisible because both halves worked perfectly on their own copy.
    world, registry = await build_world(
        genesis_tick=config.genesis_tick,
        seed=config.seed,
        world_map=map_service.world if map_service is not None else None,
    )

    # 4. close the charter event-sink seam build_world() leaves open.
    rewire_charter_event_sink(
        world.charter, lambda event: world.event_bus.publish(charter_event_to_world_event(event, world.tick()))
    )

    # 5. world-core's tick engine, sharing the same EventBus.
    tick_engine = TickEngine(
        event_bus=world.event_bus,
        scheduler_config=SchedulerConfig(target_fraction=config.scheduler_target_fraction),
        checkpoint_sink=trace_store.bind_checkpoint_sink(world.ledger, lambda: len(world.agents)),
        tick_index=config.genesis_tick,
        # Task "walking": the second of the two composition roots that has to
        # hand build_world()'s MovementSystem to the tick engine. Without it
        # `advance()` is never called, routes never step, and `agent.moved`
        # never carries a tile - see run_genesis.py's matching comment.
        movement_system=world.movement,
    )
    tick_engine.checkpoints.register("world_state", world)
    if map_service is not None:
        tick_engine.checkpoints.register("map", map_service)

    # 6. persistence subscribers, attached BEFORE genesis population so
    # every genesis-time event and every genesis funding transaction (250
    # wallets, sim mints, business accounts) lands in the trace store too
    # - DESIGN.md section 2's "complete causal record" claim starts at
    # tick 0, not at the first post-genesis mutation. record_event()
    # defensively nulls out an agent_id it does not recognize yet
    # (trace_store.known_agent_ids), so there is no FK ordering hazard
    # even for an agent's own spawn event racing its own seed_agent() row.
    world.event_bus.subscribe("*", trace_store.bind_event_bus(world.ledger))

    # 7. genesis population.
    genesis = load_genesis(config.genesis_json_path)
    await apply_genesis_population(world, tick_engine, genesis, trace_store)

    # 8. crime/, over adapters onto everything already built.
    crime_engine = create_crime_engine(
        economy=CrimeLedgerAdapter(world.ledger),
        health=LifeHealthAdapter(world),
        government=CharterGovernmentAdapter(world.charter),
        location=world.locations,
        event_sink=lambda event: world.event_bus.publish(crime_event_to_world_event(event, world.tick())),
    )

    control_businesses = {cb.business_id: cb.model_dump(mode="json") for cb in genesis.control_businesses}

    return WorldServer(
        event_bus=world.event_bus,
        world=world,
        registry=registry,
        tick_engine=tick_engine,
        crime=crime_engine,
        genesis=genesis,
        map_service=map_service,
        trace_store=trace_store,
        control_businesses=control_businesses,
    )
