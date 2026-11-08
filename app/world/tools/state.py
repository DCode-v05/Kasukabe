"""WorldState: everything a tool handler reads or mutates, gathered in one
place.

Two kinds of state live on this object, and the split matters for
atomicity (see ToolRegistry.execute() in registry.py):

1. References to sibling systems that already guarantee their own
   operations are all-or-nothing by construction: `ledger` (every public
   Ledger method validates fully before mutating anything, per its own
   module docstring), `charter` (CharterStore is lock-guarded and each
   charter/ sub-engine method is a single coherent state transition),
   `event_bus` and `locations` (world.core's canonical event bus and
   agent<->location index), and `movement` (world.core.movement.
   MovementSystem - task "walking": a plain, synchronous dict/list state
   machine that cannot itself fail partway through a call, the same
   all-or-nothing property the other three sibling systems have by
   construction; None when bootstrap.build_world() could not load a real
   WorldMap to path against - see that module's docstring - in which case
   `move` falls back to its pre-walking opaque location jump). This
   module never snapshots or rolls these back; it relies on each of them
   to never leave a call half-applied.
2. State this package owns outright because nothing else does yet:
   agent life records and households (life/ only ships pure functions,
   no store - see life/models.py's own docstring), physical inventory,
   resource zone yields, businesses, storages, plots/buildings, skills,
   prayer requests, crime cases, invented-tool definitions (models.py),
   and `agent_tiles` - the last real (x, y) tile a genuine walked step
   reported for an agent (task "walking"), recorded from `agent.moved`
   events that carry `tile_x`/`tile_y` (bootstrap.build_world()'s
   `attach_tile_tracking` subscriber does the recording; `movement`
   primitive's `move_handler` is the reader, using it to resolve "where
   is this agent standing right now, in tile terms" before starting a
   new route - see that module's docstring). Stored as a plain
   `tuple[int, int]`, not a `world.map.model.TileCoord`, deliberately:
   run_genesis.py's WorldStateCheckpointAdapter classifies every owned
   field it does not recognize as a pydantic dict/list model as "plain"
   and round-trips it through a bare JSON list, and a tuple survives that
   round trip as a list a caller can still unpack with `TileCoord(*value)`
   - a TileCoord instance would not (it has no `__iter__`), which would
   have silently corrupted every agent's remembered position on the very
   first checkpoint resume. `snapshot()`/`restore()` cover exactly this
   whole half, so a
   handler that fails partway through only needs ToolRegistry to undo
   the mutations *this* module made, never ledger's or charter's - see
   registry.py's module docstring for the ordering discipline that makes
   that safe (validate and call fallible external systems before mutating
   anything here that depends on their success).

A handler always reaches world state through this object, never by
importing ledger/charter/life module-level singletons - there are none;
every sibling system is constructed once by bootstrap.py and threaded
through here, exactly the "pass in whatever facts a module needs instead
of reaching for an import that doesn't exist yet" idiom every sibling
module in this repo already follows (see e.g. life/ports.py's docstring).
"""

from __future__ import annotations

import copy
import os
import random
import uuid
from dataclasses import dataclass, field
from typing import Any

from world.call_budget import MAX_CALLS_PER_TICK
from world.charter.engine import CharterEngine
from world.core.events import EventBus
from world.core.movement import MovementSystem
from world.core.perception import LocationIndex
from world.tools.place_aliases import satisfies_gate
from world.core.clock import TICK_MINUTES, TICKS_PER_DAY
from world.ledger.action_costs import SCHEDULE_TABLE, ActionCostCategory
from world.ledger.core import Ledger
from world.life.clock import GameClock
from world.life.models import AgentLifeRecord, BirthRequest, Household
from world.tools.inventory import Inventory
from world.tools.models import (
    Belief,
    Goal,
    Intention,
    LoanRecord,
    Need,
    ToolLicence,
    WagePromise,
    SkillDefinition,
    SkillProgress,
    ToolRefusalRecord,
    Building,
    Business,
    CrimeCase,
    CustomToolDefinition,
    Plot,
    PrayerRequest,
    PricePost,
    ResourceZone,
    Storage,
    Subpoena,
    TileOwnership,
    ToolInvocationRecord,
    Will,
)

# Field names snapshot()/restore() cover: every piece of mutable state
# this module owns outright (see module docstring, point 2). Kept as an
# explicit tuple, not "every dataclass field", so adding a field that is
# a *reference* to something external (another Ledger, say) does not
# silently start getting deep-copied - a future field must be added here
# deliberately.
# One game-hour at TICKS_PER_DAY = 144. A workflow the agent has not touched
# for this long is treated as abandoned rather than paused: procedures that do
# not survive contact with the world are a finding, not a bug to paper over.
SKILL_ABANDONED_AFTER_TICKS = 12

# Ring caps for the two records that grow with every action rather than with
# the town. A 30-agent run makes tens of thousands of tool calls in a few
# hours, and an uncapped list of them is the thing that ends a long run: the
# process grows until the machine swaps, which on 8 GB is fatal.
#
# The full history is not lost - `tool_invocations` is mirrored into the trace
# store, and refusals are grouped by the observatory as they arrive. These
# lists are the live tail the running world needs, not the archive.
#
# EVAL-25: that first sentence was FALSE when it was written. There was no
# `tool_invocations` table and no recorder - the mirror it describes did
# not exist, so this ring was the only copy and it died with the process.
# Everything downstream (task labels, refusal recovery, cost-per-action
# against proficiency, verb reach, "41 of 76 never called") was recovered
# by scraping `session_log.jsonl` instead: a log, for facts the world
# already had.
#
# It is true now. `GenesisRunner.drain_tool_invocations` runs every tick
# and `TraceStore.record_tool_invocations` writes the rows - which is also
# why the drain runs per tick rather than per day: at 5,000 records a busy
# run fills this ring faster than a daily drain would empty it.
MAX_TOOL_INVOCATIONS_KEPT = 5000
MAX_TOOL_REFUSALS_KEPT = 2000

# How many of a resident's turn-start ticks to keep. Only the tail is
# ever read, and an uncapped list per resident is a leak the length of
# the run - the same reason the two caps above exist.
MAX_TURN_TICKS_KEPT = 12

# How long a question addressed to somebody stays their business.
#
# The hearing window is at most 18 ticks and 4 at the default depth, while
# the scheduler wakes an agent every 3. So a directed question was visible
# for roughly ONE turn, and a resident who spent that turn walking never saw
# it again: 56 of 81 exchanges in one run ended after a single remark, and
# only 2 of 11 directed questions were answered by the person asked.
#
# 36 ticks is six game-hours - long enough that a resident gets several
# turns to notice and answer, short enough that nobody is still being
# asked about yesterday's barrel. Bounded on purpose (Build Spec v2
# section 6): a question that never expires becomes a permanent tax on
# every prompt.
OPEN_QUESTION_TTL_TICKS = 36

# At most this many open questions in one prompt. A resident shown a
# backlog reads a list rather than a question, and Bram asked the room for
# a copper buyer five times in a single tick.
MAX_OPEN_QUESTIONS_SHOWN = 3

# How many people have to be standing here before convening is worth
# suggesting. Three: two people talking are already having a conversation,
# and the third is what turns it into a discussion somebody might want to
# frame. Below that the hint would fire on every ordinary exchange.
# How many turns in a row a resident must reach nobody before the world
# says so.
#
# Two, so a single quiet turn is never mentioned. Standing and thinking is
# a legitimate thing for a person to do, and a report that fired on the
# first one would fire on nearly everybody - measured on run short1, 45 of
# 71 turns produced no tool call - and a resident would rightly learn to
# skip it.
#
# What it is aimed at is the RUN. n06 wrote three consecutive scenes with
# actions [], including a direct question to Marta while she was standing
# next to him, and nothing in any of the three prompts told him the last
# one had reached nobody.
SILENT_TURN_REPORT_MIN = 2

CONVENE_HINT_MIN_PRESENT = 3

# How far back the hint looks for evidence that talk is actually happening
# here. One game-hour: a room that was loud an hour ago is a quiet room now,
# and proximity without talk is not a discussion waiting to happen.
CONVENE_HINT_WINDOW_TICKS = 6

# How many recent invocations the authoring hint looks back over.
AUTHORING_HINT_SCAN = 400

# WHICH tools count as a job worth writing down. Work and dealing; not
# walking, not looking, not talking.
#
# This is the correction that matters, and it came from reading the runs
# rather than from taste. The threshold used to be eight repeats of ANY
# tool, and eight WAS reached - eight agent-and-tool pairs crossed it in
# data/loop5.db and eight in loop4. Every single one of them was `move` or
# `look_around`. So the hint did fire, and what it said was "you have
# walked eight times, write a procedure down", which is noise, and a
# resident was right to ignore it. Run 19 then reported "nothing was
# authored" and put it down to nobody crossing the line.
#
# A skill is a work routine. LABOUR is harvest, mine, fell_timber, craft,
# cook, build_structure; TRADE is buy, sell, trade, transfer, store_item,
# withdraw_item. Those are the sequences that repeat with a shape.
AUTHORING_HINT_CATEGORIES = frozenset(
    {ActionCostCategory.LABOUR, ActionCostCategory.TRADE}
)

# Three, and it is a bar the town can actually reach. The old eight was
# set against "a game-day's worth of one job", but run 19 ended at tick 59
# with the most any resident had COMPLETED of one skill being two - so for
# real work, as opposed to walking, eight was not a high bar but an
# unreachable one. Three is a routine: it is the point at which a resident
# has done the same job enough times to know its shape and not so many
# that the run is over first.
#
# Still env-overridable, and still named rather than quietly defaulted,
# because moving it changes what is being measured.
AUTHORING_HINT_REPEATS = int(os.environ.get("KASUKABE_AUTHORING_HINT_REPEATS", "3"))
# How many times a PAIR of consecutive work actions must repeat before the
# same hint fires on the sequence instead of the single tool. Two, because a
# pair is already two actions' worth of evidence - see authoring_hint.
AUTHORING_HINT_PAIR_REPEATS = int(
    os.environ.get("KASUKABE_AUTHORING_HINT_PAIR_REPEATS", "2")
)


def authoring_hint_text(
    what: str,
    times: int,
    *,
    decisions: int | None = None,
    call_ceiling: int | None = None,
    already_written: tuple[str, str] | None = None,
) -> str:
    """The nudge, in one place.

    Written once and read twice: by `WorldState.authoring_hint` on every
    live turn, and by `world/evals/verb_reach.py`, which has to show a
    resident exactly what a live resident reads. The L1 run of 2026-09-12
    measured reach for these verbs against their DESCRIPTIONS alone - the
    harness rendered no hint - and got 0% on four of the five repetition
    scenarios. A harness paraphrasing this text would have been measuring
    a prompt nobody is given, which is the defect EVAL-06 already cost
    this project once.

    Names the verbs and what each is for, never which one to use or what
    to put in it (the standing rule: prices and rules, never strategy).

    `decisions` and `call_ceiling` are run 29's finding, and they are what
    the first two sentences were missing. Naming the verbs moved
    `skillify` from 0% to 4 of 5 and moved `toolify` and `do_in_order` not
    at all, with the hint and without it, in three consecutive runs. The
    asymmetry is not in the naming, it is in the PAYOFF: `skillify` pays
    in proficiency, which a resident already reads in its own perception
    every turn, and the other two pay in calls not spent - a quantity
    nothing has ever put in front of a resident. "Better is cheaper" is a
    claim about a number the reader cannot see. So the number goes in:
    how many separate calls this repetition has actually taken, against
    the ceiling a turn can buy (`world/call_budget.py`). Run 29 measured
    0.85 acts per model call and 7.15 acts per turn against a ceiling of
    12, so the figure is real and it is the resident's own.

    Still a FACT and not an instruction - it reports what the resident
    spent, the way `silent_turns_report` reports that nothing happened,
    and says nothing about what to do with it.

    `already_written` is (skill name, holder's name) when somebody ELSE
    in the town has filed a procedure over these same tools. Run 29:
    Corwin Thorne filed `Timber Felling` at tick 72 and Tobias Holloway
    filed `timber felling` at tick 76 - the same job, the same hour, two
    authors, neither with any way to know. Naming it here is the cheapest
    place the fact can land, because this is the one line a resident
    reads at the exact moment it is about to write the duplicate; and the
    same sentence is the only thing in the town that has ever told a
    resident somebody holds a procedure it does not, which is the
    precondition `teach_skill` has been waiting on through five runs of
    never being called.
    """
    # MEASURED, 2026-09-12, through the L1 harness at five samples each
    # (`world/evals/scenarios/*/positive_*_with_the_hint.json`):
    #
    #   * with this hint shown, `skillify` reached 4 of 5 - its first
    #     non-zero reach since run 22 measured it at 0%;
    #   * `toolify` and `do_in_order` reached 0 of 5 with the hint and 0 of
    #     5 without it;
    #   * naming the three in the order of commitment instead (run now,
    #     keep a tool, write a procedure) moved skillify to 3 of 5 and
    #     moved the other two not at all.
    #
    # Three of five against four of five is noise at this sample size, so
    # the wording is the one the higher number was measured against and
    # the question is open rather than answered: the eval spec fixes 20
    # samples, and 20 is what would decide it. What the pair DOES say is
    # that the hint is what moves skillify and that nothing yet moves the
    # other two, which is a fact about the nudge, not about the order.
    #
    # Still no word on WHICH to use - that is the resident's, and the
    # standing rule's.
    parts = [
        f"You have done {what} {times} times lately and have written nothing "
        "down about it."
    ]
    if decisions is not None and call_ceiling:
        # The measured half. One call with no `program_id` was one model
        # call that bought one act - see `ToolInvocationRecord.program_id`,
        # which exists so this subtraction is possible at all.
        parts.append(
            f"Those came to {decisions} separate calls, one decision each, "
            f"against the {call_ceiling} a turn can buy."
        )
    parts.append(
        "Writing a procedure makes you better at it, and better is cheaper. "
        "`skillify` writes one down; `toolify` makes a new tool out of steps "
        "you already have, and a step may be a tool you already wrote; "
        "`do_in_order` just runs the steps now, in one call, without keeping "
        "anything. Either filing can be changed afterwards with `revise_skill` "
        "or `revise_tool`, so a first draft costs you nothing but the filing."
    )
    if already_written is not None:
        skill_name, holder = already_written
        # Who holds it, not who authored it: `teach_skill` refuses a
        # teacher whose proficiency is 0, so naming an author who never
        # walked their own procedure would name somebody the verb would
        # then turn away.
        parts.append(
            f"{holder} already holds a procedure over these same actions, "
            f"called {skill_name!r}. A procedure somebody else holds reaches "
            "you through `teach_skill`, which they call, standing where you "
            "are, with your consent; what it costs is theirs to name."
        )
    return " ".join(parts)


# Which checkpointed fields hold pydantic models, and in what shape.
# `snapshot()` deepcopies (so in-process resume keeps the objects) but a
# checkpoint written to disk is JSON, and comes back as plain dicts.
def _typed_state_fields() -> "dict[str, tuple[str, Any]]":
    """field name -> (shape, model). Built lazily to avoid import cycles."""
    from world.tools.models import (
        Belief,
        Building,
        CrimeCase,
        CustomToolDefinition,
        Goal,
        Need,
        Plot,
        Storage,
    )

    return {
        "storages": ("map", Storage),
        "plots": ("map", Plot),
        "buildings": ("map", Building),
        "custom_tools": ("map", CustomToolDefinition),
        "crime_cases": ("list", CrimeCase),
        "price_board": ("map_of_map", PricePost),
        "wage_promises": ("map", WagePromise),
        "intentions": ("map", Intention),
        # ENG-24
        "goals": ("map", Goal),
        "needs": ("map_of_map", Need),
        "beliefs": ("map", Belief),
        # Run 28b: in OWNED_STATE_FIELDS and written to every checkpoint, and
        # missing here - so Wren's harvest-until-twelve came back from disk
        # as a dict and every one of her turns after the resume died on
        # `action.satisfied`. Defined in this module, below.
        "continuing": ("map", ContinuingAction),
    }


def _rehydrated(name: str, value: Any) -> Any:
    """Rebuild the models a JSON checkpoint flattened into dicts."""
    spec = _typed_state_fields().get(name)
    if spec is None:
        return value
    shape, model = spec

    def one(raw: Any) -> Any:
        if isinstance(raw, dict):
            try:
                return model(**raw)
            except Exception:  # noqa: BLE001 - a field this build does not
                # know is a schema change, not a reason to lose the run.
                # Left as the dict it was, which is what happened before.
                return raw
        return raw

    if shape == "map" and isinstance(value, dict):
        return {k: one(v) for k, v in value.items()}
    if shape == "list" and isinstance(value, list):
        return [one(v) for v in value]
    if shape == "map_of_map" and isinstance(value, dict):
        return {
            holder: {item: one(post) for item, post in (board or {}).items()}
            for holder, board in value.items()
        }
    return value


OWNED_STATE_FIELDS: tuple[str, ...] = (
    "agents",
    "households",
    "businesses",
    "resource_zones",
    "storages",
    "plots",
    "buildings",
    "skills",
    "prayer_requests",
    "crime_cases",
    "custom_tools",
    "subpoenas",
    "tool_invocations",
    "agent_tiles",
    "tile_owners",
    "tool_wear",
    "expanded_rects",
    "raised_alerts",
    "birth_requests",
    "wills",
    "skill_sources",
    "skill_reps",
    "skill_definitions",
    "skill_progress",
    "turn_ticks",
    # Tool spec B1 / addendum section 6, whose state row says "Owned-state
    # list, checkpoint" in as many words. See the fields' own comment for
    # why the `utterances` analogy that kept them out does not hold.
    "continuing",
    "wake_reasons",
    # A3. Nothing is for sale until somebody posts a price, so this dict
    # IS the market. Genesis leaves it empty on purpose: the tick of the
    # town's first posted price is evaluation figure E1, and seeding it
    # would answer the question the run is asking.
    "price_board",
    # A5 and C2: what one person owes another. Owned state and
    # checkpointed, because a resumed run that forgot every wage promise
    # would clear the town's debts by restarting it.
    "wage_promises",
    "loans",
    "arrears",
    # B2. A resident that loses what it set out to do on a checkpoint
    # resume goes back to re-deciding the same thing every tick, which is
    # the churn the verb exists to end.
    "intentions",
    # ENG-24: a resumed town that forgot what everyone was trying to do
    # would go back to deciding it again every morning.
    "goals",
    "needs",
    "beliefs",
    # LIFE-01: a resumed town that forgot everyone's stage would
    # announce every change again on the first tick and supersede the
    # whole town's goals over nothing.
    "life_stages",
    # C1. Who holds what. Diffusion is measured off exactly these two
    # (C4: "a tool held by three residents"), so a resume that lost them
    # would put every taught copy back in the inventor's hands.
    "tool_licences",
    "tool_owners",
    "tool_copies",
    # Part D. A resume that forgot who was in the cells would release the
    # whole jail without anybody deciding to.
    "police",
    "confined",
)


def tile_key(x: int, y: int) -> str:
    """The canonical key for one 1x1 land unit. See `WorldState.tile_owners`
    for why land is keyed by a string and not an (x, y) tuple."""
    return f"{x},{y}"


# Tool spec B1's LABOUR set, verbatim from its params row. `say` is
# deliberately absent: a resident that could keep_doing `say` would talk to
# an empty room until dusk.
# GROW-18: "work_site" joins them. DESIGN section 2 asks construction to
# occupy the crew for time proportional to size, and a builder who has to
# re-decide to keep building every single tick is paying a model call per
# tick to do one job. It is the same case harvest and craft are.
CONTINUABLE_TOOLS = frozenset(
    {"harvest", "mine", "fell_timber", "craft", "cook", "rest", "work_site"}
)


@dataclass
class ContinuingAction:
    """One resident's commitment to repeat a job until something stops it."""

    tool: str
    params: dict[str, Any]
    until: dict[str, Any]
    started_tick: int
    location_id: str | None
    repetitions: int = 0

    def satisfied(self, world: "WorldState") -> str | None:
        """The stop condition's own words when it is met, else None."""
        if self.until.get("units") and self.repetitions >= int(self.until["units"]):
            return f"finished {self.repetitions} of them"
        if self.until.get("ticks"):
            elapsed = world.tick() - self.started_tick
            if elapsed >= int(self.until["ticks"]):
                return f"worked for {elapsed} ticks"
        if self.until.get("dusk") and not world.is_daytime():
            return "the light went"
        wanted = self.until.get("item_count")
        if isinstance(wanted, dict) and wanted.get("item"):
            held = world.inventory.balances.get(self.owner or "", {}).get(wanted["item"], 0)
            if held >= int(wanted.get("at_least", 1)):
                return f"held {held} {wanted['item']}"
        return None

    owner: str = ""


# How long a room's conversation stays open with nothing said in it. Lives
# here rather than in social.py because the registry below is what enforces
# it; social.py re-exports the name so every existing importer is unchanged.
CONVERSATION_WINDOW_TICKS = 12



def conversation_outcome(record: dict) -> dict:
    """What a conversation settled, from the deals filed under it.

    `deals` 0 is an answer too: a conversation that closed with nothing
    agreed is exactly the case N6 ("does what is agreed in speech become an
    act?") needs to count, and it must be told apart from one never read.
    """
    deals = list(record.get("deals") or [])
    return {
        "deals": len(deals),
        "sim_moved": sum(int(d.get("total_sim") or 0) for d in deals),
        "items": sorted({str(d.get("item")) for d in deals if d.get("item")}),
    }

@dataclass
class WorldState:
    """Everything world/tools/ needs. Constructed once by bootstrap.py;
    every field is a plain, deep-copyable container except the sibling
    system references and `rng`, both deliberately excluded from
    snapshot/restore (see module docstring).
    """

    ledger: Ledger
    charter: CharterEngine
    event_bus: EventBus
    locations: LocationIndex
    current_tick: int = 0
    rng: random.Random = field(default_factory=random.Random)
    # Task "walking": None until bootstrap.build_world() can load a real
    # WorldMap to path against (see that module's docstring) - a sibling
    # system, like ledger/charter/event_bus/locations above, not owned
    # state (see point 1 above for why).
    movement: MovementSystem | None = None
    # The price list, so `quote_action_cost` has somewhere real to read from
    # at call time (cost_disclosure.py's "Wiring this in"). A sibling system
    # like ledger/charter above, not owned state: it is a constant table, and
    # snapshotting it per tool call would deep-copy 44 frozen rules for
    # nothing.
    action_costs: Any = None
    # ECON-14: the head-tax debt store, so a resident can PAY what the
    # town says it owes.
    #
    # Both composition roots (`run_genesis.py:3758`, `wiring.py`) built a
    # `HeadTaxDebtStore` on the runner, where no tool handler could reach
    # it - handlers get `world`. So `repay_head_tax_debt` sat complete and
    # tested with no caller, `transfer` refused the treasury (it requires
    # `to_agent_id in world.agents` and the treasury is not an agent), and
    # `check_debts` told residents to pay with a tool that would refuse.
    # The debt was real, permanent and unpayable.
    #
    # A sibling reference like `movement`/`ledger` above, not owned state:
    # the runner still owns snapshot/restore of the same object.
    head_tax_debts: Any = None
    # ECON-06/ECON-16: hunger, where a tool handler can read it.
    #
    # `hunger_by_agent` lived only on the runner, so nothing inside
    # `world/tools/` could see whether a resident had eaten - which is why
    # `effective_work_capacity` and `WORK_CAPACITY_BY_AILMENT` sat inert
    # and a terminal patient harvested at full rate until the day it died.
    # Keyed by agent_id, exactly as `upkeep.py`'s integration note asks.
    hunger_by_agent: Any = None
    # Part D: the one component that writes INTO the experiment - it tells
    # agents what the town keeps failing to do. A sibling system rather than
    # owned state, and not snapshotted: its audit log must survive a rolled
    # back tool call, because a notice that was delivered was delivered.
    gap_feedback: Any = None
    # Set by the last bump_skill_repetition call: did that repetition actually
    # move the proficiency, or was it one of the many that do not? Read by
    # dispatch() to decide whether the practice is worth a trace row. A plain
    # flag rather than a richer return, because the method's return value
    # (the new level) is already load-bearing for callers that do not care.
    last_practice_moved_the_level: bool = False

    # -- owned state (see OWNED_STATE_FIELDS) -----------------------------
    agents: dict[str, AgentLifeRecord] = field(default_factory=dict)
    # EVAL-15. The owners of the scripted control businesses, who may act
    # through the registry and are NOT residents.
    #
    # `build_controls` used to hand a control bakery its own business id as
    # the owner, so the scripted baker acted as `cb01` - which is not an
    # agent - and `dispatch` refused every call. Run 26: 151 refused
    # `post_price`, and the control the whole R1-R7 comparison stands on
    # never did anything. Its tests passed because they spawned a real
    # agent to own it, which production never did.
    #
    # Kept out of `agents` on purpose. Everything that counts, bills, houses,
    # elects or wakes RESIDENTS iterates `agents`; a scripted owner in there
    # would be a citizen who votes, owes upkeep and goes dormant with no
    # engine behind it. Only the registry's actor lookup reads this, via
    # `require_agent`. Not an owned-state field: nothing changes it after
    # boot, and `build_controls` rebuilds it on resume.
    control_actors: dict[str, AgentLifeRecord] = field(default_factory=dict)
    households: dict[str, Household] = field(default_factory=dict)
    inventory: Inventory = field(default_factory=Inventory)
    # The registry of everything callable, set by bootstrap.build_world
    # after the registry is complete. A REFERENCE, like `ledger` and
    # `charter` above, so it is not snapshotted: a rolled-back tool call
    # must not un-register a tool that another agent invented.
    #
    # Optional because a test may build a WorldState directly with no
    # registry at all; `authoring_context` answers an empty vocabulary in
    # that case rather than raising, since it is what a REFUSAL carries and
    # a bookkeeping failure inside an error path replaces a useful message
    # with a stack trace.
    tool_registry: Any = None
    # When each resident last took a turn. Owned state, and snapshotted:
    # a rolled-back tool call must not make a turn un-happen, because
    # the turn did happen - it is the ACTION that was undone, which is
    # precisely what this records the absence of.
    turn_ticks: dict[str, list[int]] = field(default_factory=dict)
    businesses: dict[str, Business] = field(default_factory=dict)
    resource_zones: dict[str, ResourceZone] = field(default_factory=dict)
    storages: dict[str, Storage] = field(default_factory=dict)
    plots: dict[str, Plot] = field(default_factory=dict)
    buildings: dict[str, Building] = field(default_factory=dict)
    skills: dict[str, dict[str, int]] = field(default_factory=dict)
    # B6 (The 250 section 22): who a skill came FROM. `skills` records the
    # level and nothing else, so "self-taught vs. taught by whom" - half of
    # what section 22 asks a character sheet to show - was unanswerable from
    # world state. Keyed agent_id -> skill -> teacher_agent_id; an absent
    # entry means self-taught, which is the common case and therefore the
    # one that should cost no storage.
    skill_sources: dict[str, dict[str, str]] = field(default_factory=dict)
    # Part A: how many times each agent has SUCCESSFULLY completed a tool that
    # some skill discounts. `skills` holds the resulting 0-100 proficiency;
    # this holds the count it was derived from, because proficiency alone is
    # lossy - two agents at 100 are indistinguishable, and a rounded level
    # cannot be incremented without knowing the reps behind it. Keyed
    # agent_id -> skill -> reps. Snapshotted with the rest of owned state, so
    # a handler that raises partway through never banks a repetition.
    skill_reps: dict[str, dict[str, int]] = field(default_factory=dict)
    # Part B: every workflow the town has authored, keyed by skill name.
    # Town-wide rather than per-agent because a skill is a document that can be
    # taught: `skills[agent][name]` says who holds it and how well, this says
    # what it actually IS. One definition, many holders.
    # Refused intentions. NOT in OWNED_STATE_FIELDS, deliberately: a refusal
    # only exists because the call failed, and execute() restores owned state
    # on exactly that path - so a snapshotted refusal log would erase itself
    # the instant it had something to say. The same trap the action charge
    # fell into (see registry.dispatch), and the same fix: write it after the
    # rollback, not inside the transaction it is a record of.
    tool_refusals: list[ToolRefusalRecord] = field(default_factory=list)
    # TOOL-06. Every action charge made inside the current `execute()`,
    # as (tool_name, actor_id, amount).
    #
    # A composite tool runs its steps through `dispatch`, which charges
    # each one, inside a single `execute`, which snapshots and restores
    # tools-owned state. Only the step that RAISED was refunded, so a
    # five-step tool failing at step 3 had paid for steps 1 and 2 while
    # the world said they never happened - the exact case registry.py's
    # own comment says must not occur.
    #
    # Kept out of OWNED_STATE_FIELDS for the same reason `tool_refusals`
    # is: the refund happens after the restore, and a list that the
    # restore rolled back would be empty precisely when it had something
    # to say.
    charges_this_call: list[tuple[str, str, int]] = field(default_factory=list)
    # PROG-01. Which program the call now being dispatched is a step of,
    # as (program_id, step_index), innermost last.
    #
    # Pushed by `world/tools/program.py` around each step it runs and
    # popped after, so `registry.dispatch` can stamp the invocation record
    # without knowing anything about programs. Read at the moment the
    # record is appended and nowhere else.
    #
    # Kept OUT of OWNED_STATE_FIELDS deliberately, and for the opposite
    # reason to `tool_refusals`: this is not a record, it is a cursor into
    # a call that is still running. A snapshot/restore in the middle of a
    # program would put back a stack entry for a step the restore has just
    # unmade, and the next step would file itself under a program that no
    # longer has that history. `execute()` clears it, exactly as it clears
    # `charges_this_call`, so a call that raised somewhere unexpected
    # cannot leave a frame behind for the next one to inherit.
    program_stack: list[tuple[str, int]] = field(default_factory=list)
    # TOOL-31. ONE ceiling for a whole nest of programs, not one per
    # level. An invented tool may now be a step of another invented tool,
    # and each `run_program` used to read its own forty-dispatch and
    # half-the-wallet ceiling from scratch - so a tool that called a tool
    # that called a tool had eight times the budget of the call the
    # resident actually made, which is the one thing those ceilings exist
    # to prevent. The outermost run creates this; every nested one
    # inherits it (see program.ProgramBudget).
    #
    # Transient, like `program_stack` above and for the same reason: it
    # is a cursor into a call that is still running, not a record. It is
    # kept out of OWNED_STATE_FIELDS and cleared by `execute()` going in,
    # so a call that raised somewhere unexpected leaves no ceiling behind
    # for the next resident to inherit.
    program_budget: Any | None = None
    # What the town has said out loud, most recent last. NOT owned state and
    # NOT snapshotted, for the same reason as tool_refusals above: this is a
    # record of what happened, and rolling it back with a failed tool call
    # would erase speech that really did occur. Capped, because a long run
    # produces tens of thousands of utterances and the viewer only ever shows
    # the tail.
    utterances: list[dict] = field(default_factory=list)
    # The town's conversations, by id. A conversation is a first-class
    # record here rather than something inferred afterwards from the
    # utterance tail, because three things were impossible while it was
    # an inference (docs/specs/group-conversation-spec.md):
    #
    #   * a resident standing in the room and addressed by name was in no
    #     conversation, having no roster to be on;
    #   * two pairs talking in one room were one thread, and each answer
    #     landed under the other's question;
    #   * nobody could convene anything - a resident who wanted the
    #     household round a table could only speak and hope.
    #
    # Same ownership as `utterances` above and for the same reason: a
    # rolled-back tool call must not erase a conversation that really
    # happened.
    conversations: dict[str, dict] = field(default_factory=dict)
    # Tool spec B1: who is under a continuing action, and why they last
    # woke. A resident in here is skipped by the scheduler, which is where
    # the cost of a day actually falls - a farmer's afternoon becomes
    # twenty repetitions and one model call instead of twenty of each.
    #
    # OWNED STATE, both of them, per the tool-text addendum's rule 6 and
    # keep_doing's own state row ("Owned-state list, checkpoint"). They
    # shipped outside it, reasoning by analogy to `utterances` - a
    # rolled-back call must not un-say something that was heard - and the
    # analogy does not hold in either direction:
    #
    #   * A commitment is not an utterance. Nobody heard it. If the call
    #     that made it is rolled back and refunded, a resident left in this
    #     dict is skipped by the scheduler for a commitment the world says
    #     never happened - silently, until dusk.
    #   * The checkpoint reads exactly this tuple (see snapshot()), so
    #     leaving `continuing` out meant a resumed run dropped every
    #     continuing action on the floor and every committed resident
    #     quietly stopped working.
    #   * `wake_if_working` runs inside `say`'s handler (social.py), so a
    #     `say` that fails after it would wake a worker, and name who spoke
    #     to them, for a remark that never left anyone's mouth.
    #
    # `advance_continuing` calls `stop_continuing` OUTSIDE the inner
    # `registry.execute`, so a refused repetition still ends the action and
    # keeps its reason - the one case the original comment was protecting
    # is not affected by this at all.
    continuing: dict[str, "ContinuingAction"] = field(default_factory=dict)
    wake_reasons: dict[str, str] = field(default_factory=dict)
    # A3: holder_id -> item -> PricePost. The holder is a business id or an
    # agent id, the same key space `Inventory` already uses, because a
    # farmer standing in the market with three cabbages is doing what a
    # shop does at a smaller scale.
    price_board: dict[str, dict[str, PricePost]] = field(default_factory=dict)
    # A5: "{business_id}|{employee_id}" -> WagePromise. A flat string key
    # rather than a tuple because the checkpoint round-trips through JSON,
    # and a tuple key comes back as a string anyway - better to say so.
    wage_promises: dict[str, WagePromise] = field(default_factory=dict)
    # C2: "{lender_id}|{borrower_id}" -> LoanRecord, for transfers the
    # lender tagged `reason="loan"`.
    loans: dict[str, LoanRecord] = field(default_factory=dict)
    # A7: agent_id -> {"head_tax": n, "upkeep": n}, written by the runner's
    # day-boundary jobs. The tax engine owns the arithmetic; this object
    # only carries the answer so `check_debts` and perception can read it.
    arrears: dict[str, dict[str, int]] = field(default_factory=dict)
    # B2: agent_id -> Intention. At most one at a time, deliberately - a
    # list of intentions is a plan, and a resident that keeps a plan in
    # perception reads a document rather than a purpose.
    intentions: dict[str, Intention] = field(default_factory=dict)
    # ENG-24. Goals, needs and beliefs - the substrate the review found
    # missing. `Intention` is one sentence with no horizon, priority,
    # progress or way to be superseded, so "agents that develop their own
    # goals" had nothing to develop.
    goals: dict[str, Goal] = field(default_factory=dict)
    needs: dict[str, dict[str, Need]] = field(default_factory=dict)
    beliefs: dict[str, Belief] = field(default_factory=dict)
    # LIFE-01. What stage of life each resident was last seen in.
    #
    # Kept so a CHANGE can be noticed. Without it the world can say what
    # somebody is but never that they have become it, and a goal that
    # belonged to a life they have left survives forever.
    life_stages: dict[str, Any] = field(default_factory=dict)
    # ENG-24, §5.3's acceptance: "a buy+eat marks it done and the outcome
    # line renders once". Popped, never read, for the same reason
    # `last_intention_outcome` is: it is a message about something that
    # happened, and a message that keeps arriving stops being read.
    last_goal_outcome: dict[str, list[tuple[Goal, str]]] = field(default_factory=dict)
    # Set by bootstrap so `set_intention` can refuse a `done_when_tool`
    # that names no tool. The registry is not world state and this object
    # must not hold a reference to it (see the module docstring), so it
    # holds the NAMES, which are just strings.
    registry_tool_names: frozenset[str] = field(default_factory=frozenset)
    # agent_id -> (the intention, why it ended). Read once by the next
    # perception and cleared, so a resident is told what became of what it
    # set out to do. NOT owned state, for the same reason as
    # `wake_reasons` is not a record of a decision but a message about one
    # that already happened - see that field.
    last_intention_outcome: dict[str, tuple[Intention, str]] = field(default_factory=dict)
    # C1: "{tool_name}|{licensee_id}" -> ToolLicence. Flat string keys for
    # the same reason `wage_promises` uses them: the checkpoint round-trips
    # through JSON and a tuple key comes back a string anyway.
    tool_licences: dict[str, ToolLicence] = field(default_factory=dict)
    # tool_name -> current owner. Absent means "still the creator", so a
    # tool nobody has sold needs no row.
    tool_owners: dict[str, str] = field(default_factory=dict)
    # tool_name -> the agents taught their own copy. Unrevocable by design:
    # that is the whole difference between teaching and licensing.
    tool_copies: dict[str, set[str]] = field(default_factory=dict)
    # Part D. Who may arrest, and who is being held.
    #
    # Empty at genesis, both of them: DESIGN.md gives the town a
    # government but no police force, and a run in which somebody is
    # appointed is a run in which somebody decided to. `confined` maps
    # agent_id -> {case_id, since_tick, until_tick, by}; `until_tick` is
    # None until a seat rules on it, which is the difference between being
    # held and being sentenced.
    police: set[str] = field(default_factory=set)
    confined: dict[str, dict[str, Any]] = field(default_factory=dict)
    # agent_id -> display name, seeded once at boot from the genesis cast.
    #
    # AgentLifeRecord deliberately carries no name (its own docstring: "no
    # wallet balances, no location, no memory"), so nothing inside the world
    # could turn "Greta" into a0087. `say` needs exactly that: agents address
    # each other by name in the content and leave listener_agent_id unset -
    # 8 of 15 remarks in a live run named somebody and 1 set the parameter -
    # and without this map the world had no way to tell who was meant.
    #
    # NOT owned state: it is set at boot and never changes, so a rolled-back
    # tool call has nothing to restore, and a checkpoint that omitted it
    # would leave the town anonymous after a resume.
    agent_names: dict[str, str] = field(default_factory=dict)
    # agent_id -> trade, and agent_id -> where that trade is worked. Seeded
    # once at boot from genesis, alongside agent_names and for the same
    # reason: AgentLifeRecord carries no occupation (its own docstring: "no
    # wallet balances, no location, no memory"), so nothing inside the world
    # could answer "who is the trader" without them.
    #
    # This is the data half of world/tools/cards.py. Bram Holloway asked the
    # room five times in one tick who bought copper, while a trader stood two
    # buildings away: the town knew and had no way to say so.
    #
    # NOT owned state, exactly as agent_names is not: set at boot, never
    # mutated by a tool call, so a rolled-back call has nothing to restore
    # and a checkpoint that omitted them would leave the town unable to find
    # anybody after a resume.
    occupations: dict[str, str] = field(default_factory=dict)
    workplaces: dict[str, str] = field(default_factory=dict)
    skill_definitions: dict[str, SkillDefinition] = field(default_factory=dict)
    # Who is partway through which workflow, keyed "agent_id::skill_name" - an
    # agent can have more than one open, because a skill step may itself be a
    # skill (see skillify's nesting cap).
    skill_progress: dict[str, SkillProgress] = field(default_factory=dict)
    prayer_requests: list[PrayerRequest] = field(default_factory=list)
    crime_cases: list[CrimeCase] = field(default_factory=list)
    custom_tools: dict[str, CustomToolDefinition] = field(default_factory=dict)
    subpoenas: dict[str, Subpoena] = field(default_factory=dict)
    # DESIGN.md section 2's "complete causal record": every tool call that
    # reached and completed a handler, appended by ToolRegistry.execute()
    # regardless of the tool's namespace - see ToolInvocationRecord's
    # docstring for why this is the log a court subpoena reveals.
    tool_invocations: list[ToolInvocationRecord] = field(default_factory=list)
    # Task "walking": see module docstring point 2 above for the full
    # rationale (why a plain tuple, not a TileCoord, and who reads/writes
    # it). Keyed by agent_id, empty until the first real walked step.
    agent_tiles: dict[str, tuple[int, int]] = field(default_factory=dict)
    # The 250 section 31: one owner record per 1x1 unit of land, keyed
    # "x,y". A string key rather than a (x, y) tuple deliberately - the
    # same checkpoint round-trip that makes `agent_tiles` a plain tuple
    # turns dict keys into JSON object keys, which must be strings; a
    # tuple key would come back as the string "(142, 87)" and never match
    # a lookup again. `tile_key()` is the one place that formatting lives.
    tile_owners: dict[str, TileOwnership] = field(default_factory=dict)
    # GROW-23: acts of manual work since this resident last wore out a
    # tool. See world/tools/wear.py for why a counter and not a durability
    # field on the item.
    tool_wear: dict[str, int] = field(default_factory=dict)
    # GROW-20. The strips the town has won, as (x0, y0, x1, y1) half-open
    # rects in map coordinates.
    #
    # `TileOwnership.from_expansion` has existed since section 31.2 asked
    # `auction_land` to scope itself to "newly petitioned/expanded land",
    # and nothing ever set it - because the fact lives on the MAP (which
    # tiles a petition added) and the flag lives on a TILE record that is
    # materialised lazily, one at a time, long after the expansion. There
    # was nowhere for the answer to be written down.
    #
    # This is that place: `approve_land_petition` records the rect
    # `expand_land` already returns (GROW-26), and `require_tile`'s
    # government default consults it. Owned state, so it is checkpointed
    # and a resumed town still knows which of its land is new.
    expanded_rects: list[tuple[int, int, int, int]] = field(default_factory=list)

    def came_from_expansion(self, x: int, y: int) -> bool:
        """Whether this tile arrived through a land petition."""
        return any(
            x0 <= x < x1 and y0 <= y < y1
            for (x0, y0, x1, y1) in self.expanded_rects
        )
    # UI-32: alerts the console has already been shown, so they outlive the
    # event they were derived from.
    #
    # `/admin/alerts` recomputed the whole feed from `event_bus.recent()`
    # on every 8-second poll, and that ring is 2,000 entries - now
    # `agents * 200` since CORE-09. A regime change at tick 5 simply
    # vanished from the operator's console once 2,000 later events pushed
    # it out, with nothing anywhere saying it had ever been raised. An
    # alert that disappears is worse than one never raised: the operator
    # has no way to tell "nothing happened" from "it scrolled off".
    #
    # Owned state, so it is checkpointed and a resumed town still knows
    # what it warned about - same reasoning as `expanded_rects` above.
    # Bounded by MAX_RAISED_ALERTS so a long run cannot grow it without
    # limit; the console asks for 50 and the newest are kept.
    raised_alerts: list[dict] = field(default_factory=list)

    # UI-05: the operator's baby-blessing queue, owned by the world.
    #
    # It lived on `AdminExtras` (the console's own object) and on the
    # retired standalone `WorldServer`, and on the embedded host - the one
    # every run uses - nothing could write to it: `submit_birth_request`
    # had no HTTP route and no tool. The queue, the console panel, the
    # grant/deny routes and the population governor were all built and
    # tested against a queue that was empty in every run ever made.
    #
    # Here because a request for a child is world state, not console
    # state: it survives a checkpoint, and the `ask_for_a_child` tool can
    # reach it. `run_genesis` hands this same dict to `AdminExtras`, so
    # the console reads exactly what the town wrote.
    birth_requests: dict[str, "BirthRequest"] = field(default_factory=dict)

    # Section 31.3: an owner's own wishes, keyed by agent_id. Absent an
    # entry here the automatic default applies unchanged.
    wills: dict[str, Will] = field(default_factory=dict)

    def tick(self) -> int:
        """Current tick, for anything that needs to stamp a WorldEvent or
        call a sibling system's `tick`/`now_tick` argument."""
        return self.current_tick

    def is_daytime(self) -> bool:
        """Whether the sun is up right now.

        `ContinuingAction.satisfied` has called this since it was written
        (`state.py:377`, the `until={"dusk": ...}` stop condition) and it
        did not exist. Any resident that committed to work "until dusk"
        took the whole tick loop down with an AttributeError the moment
        the runner advanced its action - which is exactly what happened
        eight minutes into the 50-agent run on 2026-09-08, killing every
        engine.

        No test reached it because no test built a continuing action with
        a `dusk` stop; the run built one within the first game-hour.

        Derived from `timestamp_for`, the single clock `world/core/` and
        the engine's own `is_daytime` payload key already read, so "day"
        means the same thing to the stop condition, to the perception
        text, and to the map's lighting.
        """
        from world.core.clock import timestamp_for

        return timestamp_for(self.current_tick).is_daytime

    def game_clock(self) -> GameClock:
        """Current tick expressed as a life.clock.GameClock, for handlers
        (marry) that call into world.life functions taking `at: GameClock`
        instead of a bare tick. `TICK_MINUTES` is core/'s own canonical
        "1 tick = 10 game-minutes" constant (DESIGN.md section 1) - hours
        are derived from it rather than re-declaring the ratio here, so a
        future change to tick granularity only has one place to edit.
        """
        return GameClock(total_hours=self.current_tick * TICK_MINUTES / 60.0)

    def owner_of_tile(self, x: int, y: int) -> TileOwnership | None:
        """Who holds this 1x1 unit, or None if no record has ever been
        written for it. A caller that needs section 31.2's "unclaimed land
        is government land" reading should treat None as government - see
        primitives/property.py's `require_tile`, which does exactly that
        rather than pre-seeding an ownership row for every tile on the map.
        """
        return self.tile_owners.get(tile_key(x, y))

    # ── conversations ─────────────────────────────────────────────────

    def open_conversation(
        self, *, place: str | None, topic: str, participants: list[str], now: int
    ) -> str:
        """Create one and return its id. Participants are de-duplicated in
        the order given, so a roster reads as the order people joined."""
        conversation_id = str(uuid.uuid4())
        roster: list[str] = []
        for agent_id in participants:
            if agent_id not in roster:
                roster.append(agent_id)
        self.conversations[conversation_id] = {
            "conversation_id": conversation_id,
            "place": place,
            "topic": topic,
            "participants": roster,
            "opened_tick": now,
            "last_tick": now,
            "turns": 0,
            "open": True,
            # A2A: what the session resolved into. Every deal agreed inside
            # it, appended as it settles (commerce._settled_in_conversation),
            # and a summary written when it closes.
            "deals": [],
        }
        return conversation_id

    def record_conversation_deal(self, conversation_id: str, deal: dict) -> None:
        """A completed buy, sell or trade, filed under the conversation it
        was agreed in. Unknown ids are ignored: a deal must never fail
        because its conversation's record is missing."""
        record = self.conversations.get(conversation_id)
        if record is not None:
            record.setdefault("deals", []).append(dict(deal))

    def join_conversation(self, conversation_id: str, agent_id: str) -> None:
        record = self.conversations.get(conversation_id)
        if record is not None and agent_id not in record["participants"]:
            record["participants"].append(agent_id)

    def note_conversation_turn(self, conversation_id: str, now: int) -> None:
        record = self.conversations.get(conversation_id)
        if record is not None:
            record["turns"] += 1
            record["last_tick"] = now

    def close_stale_conversations(self, now: int) -> list[str]:
        """Close every conversation whose room has been quiet longer than
        the window, and return what was closed.

        A conversation that closes is not deleted: the transcript and the
        roster are the record of a thing that happened, and the UI shows
        both. Closing only decides what a new remark joins.
        """
        closed = []
        for conversation_id, record in self.conversations.items():
            if record["open"] and now - record["last_tick"] > CONVERSATION_WINDOW_TICKS:
                record["open"] = False
                record["closed_tick"] = now
                record["outcome"] = conversation_outcome(record)
                closed.append(conversation_id)
        return closed

    def open_conversations_at(self, place: str | None, now: int) -> list[dict]:
        """Open conversations in this room, most recently spoken in first.

        Closes the stale ones on the way past, so a caller never has to
        remember to - the window is the only thing that ends a
        conversation and it must not depend on somebody checking.
        """
        self.close_stale_conversations(now)
        here = [
            record for record in self.conversations.values()
            if record["open"] and satisfies_gate(record["place"] or "", place or "")
        ]
        return sorted(here, key=lambda record: record["last_tick"], reverse=True)

    def conversation_pointer(self, agent_id: str) -> "str | None":
        """The conversation this resident is currently in, or None.

        THE DEFECT IT FIXES, from `final4`. Perrin Holloway convened a
        five-person conversation about the household's hidden 400 sim and
        it received exactly ONE remark; the family then held that same
        conversation, sixteen remarks and five speakers, under a different
        implicit id. The consolidated report: "The town held the
        conversation Perrin wanted - but not in the object he created."

        `say`'s `conversation_id` is optional, residents omit it, and every
        remark therefore fell to room-default routing - whatever thread the
        room already carried. A convened conversation was a table set in a
        room where everybody went on talking at the old one.

        DERIVED, NEVER STORED, and that is the whole of the design. A
        stored pointer goes stale against a conversation that closed, a
        resident who walked out, or a roster that changed, and a stale
        pointer routes speech into a thread nobody is reading - which is
        the defect again wearing a different hat. Resolved fresh on every
        read: you are in it if it is open, it is here, and you are on it.

        Prefers the conversation you were most recently ON THE ROSTER of
        over the room's ambient one, because being invited is a stronger
        claim than being in earshot.
        """
        here = self.locations.location_of(agent_id)
        if here is None:
            return None
        mine = [
            record
            for record in self.open_conversations_at(here, self.current_tick)
            if agent_id in record["participants"]
        ]
        if not mine:
            return None
        # A CONVENED conversation beats an ambient one, and the newest
        # convened conversation beats an older one.
        #
        # The discriminator is the topic, not the clock. `start_conversation`
        # requires one; the implicit thread `_thread_for` opens when a room
        # starts talking has `topic=""`. So "somebody deliberately called
        # this and put you on it" is recorded in the data already, and a
        # convened table is a stronger claim on your next remark than the
        # conversation the room happened to be carrying when you walked in.
        #
        # An earlier draft ranked on `opened_tick` alone. Both
        # conversations in the reproduction opened on tick 0 - convening
        # and speaking inside one tick is ordinary - so the tie fell back
        # to `open_conversations_at`'s last-spoken order and picked the
        # implicit thread, which is the final4 defect exactly.
        def claim(record: dict[str, Any]) -> tuple[int, int]:
            return (1 if (record.get("topic") or "").strip() else 0, record["opened_tick"])

        return str(max(mine, key=claim)["conversation_id"])

    def conversation_partners(self, agent_id: str) -> set[str]:
        """Everybody this resident is in an open conversation with, here.

        One-to-one or a group, equally: every other member of an open
        conversation at this resident's place, on whose roster the resident
        is, who is standing here too. Empty for somebody talking to nobody -
        including somebody who spoke only to the room, which opens a thread
        of one. What `buy` is offered on (`registry.tool_registry_rows`).
        """
        here = self.locations.location_of(agent_id)
        if here is None:
            return set()
        partners: set[str] = set()
        for record in self.open_conversations_at(here, self.current_tick):
            if agent_id not in record["participants"]:
                continue
            for member in record["participants"]:
                if member != agent_id and satisfies_gate(
                    self.locations.location_of(member) or "", here
                ):
                    partners.add(member)
        return partners

    def conversation_here(self, agent_id: str, now_tick: int) -> "dict[str, Any] | None":
        """The conversation this resident is standing in, ready to render.

        The read side of `open_conversation`. `open_conversations_at`
        answers in ids because the tools that call it want ids; a
        perception wants names, because a resident cannot greet n07.

        Membership is NOT re-checked here and the room is: a conversation
        belongs to a place, so standing in the room is what puts you in
        earshot of it, whether or not you were on the roster when it
        opened. That is also what makes it joinable - somebody who walks
        in can see what is being discussed and answer into it.

        Returns the liveliest one when a room holds several, because a
        perception naming two conversations invites a remark that lands in
        neither.
        """
        here = self.locations.location_of(agent_id)
        if here is None:
            return None
        records = self.open_conversations_at(here, now_tick)
        if not records:
            return None
        record = records[0]
        present = [
            member
            for member in record["participants"]
            if self.locations.location_of(member) == here
        ]
        return {
            "conversation_id": record["conversation_id"],
            "topic": record["topic"],
            "participants": [
                self.agent_names.get(member, member) for member in present
            ],
            "turns": record["turns"],
            "opened_tick": record["opened_tick"],
        }

    # ── who holds an invented tool (tool spec C1) ─────────────────────

    def tool_owner(self, tool_name: str) -> str:
        """Whoever holds it now: the creator until somebody sells it."""
        sold_to = self.tool_owners.get(tool_name)
        if sold_to:
            return sold_to
        definition = self.custom_tools.get(tool_name)
        return definition.creator_agent_id if definition is not None else ""

    def licence_for(
        self, tool_name: str, licensee_id: str, now_tick: int
    ) -> "ToolLicence | None":
        licence = self.tool_licences.get(f"{tool_name}|{licensee_id}")
        if licence is None or not licence.is_current(now_tick):
            return None
        return licence

    def grant_licence(self, licence: "ToolLicence") -> None:
        self.tool_licences[f"{licence.tool_name}|{licence.licensee_id}"] = licence

    def end_licence(self, tool_name: str, licensee_id: str) -> None:
        self.tool_licences.pop(f"{tool_name}|{licensee_id}", None)

    def transfer_tool_ownership(self, tool_name: str, new_owner_id: str) -> None:
        self.tool_owners[tool_name] = new_owner_id

    def grant_tool_copy(self, tool_name: str, holder_id: str) -> None:
        self.tool_copies.setdefault(tool_name, set()).add(holder_id)

    def may_call_tool(self, tool_name: str, agent_id: str, now_tick: int) -> bool:
        """Is this invented tool this resident's to call?

        The owner, anyone taught their own copy, and anyone holding a
        current licence. Before C1 the answer was "everybody", which is
        why diffusion was zero by construction rather than as a finding:
        there was nothing for a licence to grant and nothing for C4 to
        count.
        """
        if tool_name not in self.custom_tools:
            return True  # not an invented tool; this rule is not about it
        if agent_id == self.tool_owner(tool_name):
            return True
        if agent_id in (self.tool_copies.get(tool_name) or set()):
            return True
        return self.licence_for(tool_name, agent_id, now_tick) is not None

    def holders_of(self, tool_name: str, now_tick: int) -> set[str]:
        """Everyone who can call it right now - evaluation C4's numerator."""
        holders = {self.tool_owner(tool_name)}
        holders |= set(self.tool_copies.get(tool_name) or set())
        holders |= {
            licence.licensee_id
            for licence in self.tool_licences.values()
            if licence.tool_name == tool_name and licence.is_current(now_tick)
        }
        return {holder for holder in holders if holder}

    def inherit_tools(self, deceased_id: str, heir_id: str) -> list[str]:
        """Move a dead inventor's tools to the heir.

        Without this a tool dies with the person who thought of it, which
        is the one way diffusion can go backwards.
        """
        moved: list[str] = []
        for tool_name in list(self.custom_tools):
            if self.tool_owner(tool_name) == deceased_id:
                self.transfer_tool_ownership(tool_name, heir_id)
                moved.append(tool_name)
        return moved

    # ── intentions (tool spec B2) ─────────────────────────────────────

    def game_clock_label(self) -> str:
        """"08:30", for the intention line. The clock a resident reads."""
        minutes = (self.tick() * TICK_MINUTES) % (24 * 60)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    def intention_of(self, agent_id: str) -> "Intention | None":
        return self.intentions.get(agent_id)

    def drop_intention(self, agent_id: str, reason: str) -> "Intention | None":
        """Clear it and say why. Returns what was cleared, or None."""
        dropped = self.intentions.pop(agent_id, None)
        if dropped is not None:
            self.last_intention_outcome[agent_id] = (dropped, reason)
        return dropped

    def fulfil_intention(self, agent_id: str, tool_name: str) -> "Intention | None":
        """Clear an intention whose named tool just succeeded.

        Called from the registry after a completed call, so "I set out to
        sell the wheat" clears itself the moment the wheat is sold rather
        than sitting in perception telling a resident to do what it has
        already done.
        """
        current = self.intentions.get(agent_id)
        if current is None or current.done_when_tool != tool_name:
            return None
        return self.drop_intention(agent_id, "fulfilled")

    def expire_intentions(self, reason: str = "the day ended") -> list[tuple[str, "Intention"]]:
        """Clear everything at the day boundary and report what lapsed.

        An intention that outlives its day stops being a purpose and
        becomes furniture. What it leaves behind is the line the resident
        reads next: "The day ended and you had not done what you set out
        to at 08:30."
        """
        lapsed = [(agent_id, intention) for agent_id, intention in self.intentions.items()]
        for agent_id, _ in lapsed:
            self.drop_intention(agent_id, reason)
        return lapsed

    # ── wages and debts (tool spec A5, C2) ────────────────────────────

    def wage_promises_for(self, agent_id: str) -> "list[WagePromise]":
        """Every promise this resident is on either side of."""
        owned = {
            business_id
            for business_id, business in self.businesses.items()
            if business.owner_agent_id == agent_id
        }
        return [
            promise
            for promise in self.wage_promises.values()
            if promise.employee_id == agent_id or promise.business_id in owned
        ]

    def accrue_wages(self, game_day: int, now_tick: int) -> "list[WagePromise]":
        """Bring every promise up to date at a day boundary.

        The world never pays. It counts: a promise whose period has come
        round accrues what it said it would, and only `pay_wage` ever moves
        the other number. Idempotent by `accrued_through_day`, so a
        reconciliation that runs twice does not bill twice.
        """
        moved: list[WagePromise] = []
        for promise in self.wage_promises.values():
            if promise.accrued_through_day is not None and (
                game_day <= promise.accrued_through_day
            ):
                continue
            start = (
                promise.accrued_through_day
                if promise.accrued_through_day is not None
                else promise.promised_tick // TICKS_PER_DAY
            )
            periods = (game_day - start) // promise.every_days
            if periods > 0:
                promise.accrued_sim += periods * promise.wage_sim
                promise.accrued_through_day = start + periods * promise.every_days
                moved.append(promise)
            elif promise.accrued_through_day is None:
                promise.accrued_through_day = start
        return moved

    def record_loan(self, lender_id: str, borrower_id: str, amount: int, tick: int) -> None:
        key = f"{lender_id}|{borrower_id}"
        existing = self.loans.get(key)
        if existing is None:
            self.loans[key] = LoanRecord(
                lender_id=lender_id, borrower_id=borrower_id,
                principal_sim=amount, since_tick=tick,
            )
        else:
            existing.principal_sim += amount

    def record_repayment(self, borrower_id: str, lender_id: str, amount: int) -> int:
        """Apply a payment against what the borrower owes this lender.

        Returns what was actually applied - anything beyond the outstanding
        principal is just a payment, and saying so keeps a resident from
        being recorded as having overpaid a debt it had already cleared.
        """
        loan = self.loans.get(f"{lender_id}|{borrower_id}")
        if loan is None:
            return 0
        applied = min(amount, loan.outstanding_sim)
        loan.repaid_sim += applied
        return applied

    def debts_of(self, agent_id: str) -> dict[str, Any]:
        """C2's whole answer: what you owe, what you are owed, due today."""
        owed_by_you: list[dict[str, Any]] = []
        owed_to_you: list[dict[str, Any]] = []
        now = self.tick()

        for promise in self.wage_promises_for(agent_id):
            if promise.owed_sim <= 0:
                continue
            row = {
                "kind": "wage",
                "sim": promise.owed_sim,
                "days": promise.days_overdue(now),
                "business": promise.business_id,
            }
            if promise.employee_id == agent_id:
                owed_to_you.append({**row, "from": promise.business_id})
            else:
                owed_by_you.append({**row, "to": promise.employee_id})

        for loan in self.loans.values():
            if loan.outstanding_sim <= 0:
                continue
            if loan.borrower_id == agent_id:
                owed_by_you.append({
                    "kind": "loan", "to": loan.lender_id,
                    "sim": loan.outstanding_sim, "since_tick": loan.since_tick,
                })
            elif loan.lender_id == agent_id:
                owed_to_you.append({
                    "kind": "loan", "from": loan.borrower_id,
                    "sim": loan.outstanding_sim, "since_tick": loan.since_tick,
                })

        # Head tax and upkeep arrears live in the ledger's own tax store,
        # which this object holds a reference to rather than owning.
        for row in self.tax_arrears_for(agent_id):
            owed_by_you.append(row)

        return {
            "owed_by_you": owed_by_you,
            "owed_to_you": owed_to_you,
            "due_today_sim": sum(row["sim"] for row in owed_by_you),
        }

    def tax_arrears_for(self, agent_id: str) -> list[dict[str, Any]]:
        """Head tax and upkeep the town is still waiting on.

        Reads whatever the runner recorded on `self.arrears`; empty until a
        run has crossed a day boundary, which no run had until the day jobs
        were wired. Deliberately not computed here: the tax engine owns the
        arithmetic and this object owns none of it.
        """
        rows: list[dict[str, Any]] = []
        for kind, amount in (self.arrears.get(agent_id) or {}).items():
            if amount > 0:
                rows.append({
                    "kind": kind, "to": "government:treasury", "sim": int(amount),
                    "days": 0,
                })
        return rows

    # ── the price board (tool spec A3) ────────────────────────────────

    def posted(self, holder_id: str, item: str) -> "PricePost | None":
        return (self.price_board.get(holder_id) or {}).get(item)

    def posts_of(self, holder_id: str) -> "dict[str, PricePost]":
        return dict(self.price_board.get(holder_id) or {})

    def put_price(self, holder_id: str, post: "PricePost") -> "PricePost | None":
        """Post or replace one offer. Returns whatever it replaced."""
        board = self.price_board.setdefault(holder_id, {})
        previous = board.get(post.item)
        board[post.item] = post
        return previous

    def drop_price(self, holder_id: str, item: str) -> "PricePost | None":
        board = self.price_board.get(holder_id)
        if board is None:
            return None
        removed = board.pop(item, None)
        if not board:
            self.price_board.pop(holder_id, None)
        return removed

    def prices_visible_at(self, location_id: str | None) -> "dict[str, dict[str, PricePost]]":
        """Every posted offer a resident standing here can see.

        A business's board hangs at its premises whether the owner is
        present or not; a person's travels with them, so it is visible
        wherever they are standing. Both answer to "what is for sale where
        I am", which is the only question `look_around` needs to answer.
        """
        if location_id is None:
            return {}
        visible: dict[str, dict[str, PricePost]] = {}
        for holder_id, board in self.price_board.items():
            if not board:
                continue
            business = self.businesses.get(holder_id)
            if business is not None:
                if satisfies_gate(business.location_id, location_id):
                    visible[holder_id] = dict(board)
                continue
            if holder_id in self.agents and satisfies_gate(
                self.location_of(holder_id), location_id
            ):
                visible[holder_id] = dict(board)
        return visible

    def earliest_standing_post_tick(self) -> int | None:
        """When the oldest offer still standing was put up.

        NOT evaluation E1. E1 is "the tick of the first posted price in the
        town" and comes from `economy.price.posted` in the completed trace,
        because a post that was later re-priced overwrites its own tick
        here and a withdrawn one leaves nothing at all. This is the live
        view for the observatory; the report must not read it.
        """
        ticks = [
            post.posted_tick
            for board in self.price_board.values()
            for post in board.values()
        ]
        return min(ticks) if ticks else None

    # ── continuing actions (tool spec B1) ─────────────────────────────

    def is_working(self, agent_id: str) -> bool:
        """Is this resident under a continuing action right now?

        `dispatch_due_agents` reads this to decide whether to spend a
        model call on them, which is the whole economics of the verb.
        """
        return agent_id in self.continuing

    def wake_reason(self, agent_id: str) -> str | None:
        return self.wake_reasons.get(agent_id)

    def continuing_repetitions(self, agent_id: str) -> int:
        action = self.continuing.get(agent_id)
        return action.repetitions if action else 0

    def start_continuing(
        self, agent_id: str, tool: str, params: dict[str, Any], until: dict[str, Any]
    ) -> "ContinuingAction":
        action = ContinuingAction(
            tool=tool, params=dict(params), until=dict(until),
            started_tick=self.tick(), location_id=self.location_of(agent_id),
            owner=agent_id,
        )
        self.continuing[agent_id] = action
        self.wake_reasons.pop(agent_id, None)
        return action

    def stop_continuing(self, agent_id: str, reason: str) -> None:
        """End the action and leave the resident the reason.

        The reason is the first thing its next perception says, because a
        resident that stops working and does not know why has been given a
        gap in its own morning.
        """
        self.continuing.pop(agent_id, None)
        self.wake_reasons[agent_id] = reason

    def wake_if_working(self, agent_id: str, reason: str) -> bool:
        """Interruption rules 3 to 6: something happened that a working
        resident has to be told about now rather than at dusk."""
        if agent_id not in self.continuing:
            return False
        action = self.continuing[agent_id]
        self.stop_continuing(
            agent_id,
            f"You stopped {action.tool} after {action.repetitions}. {reason}",
        )
        return True

    async def advance_continuing(self, registry: Any, agent_id: str) -> str | None:
        """Run one repetition. Returns the ending reason, or None to go on.

        Through the same `dispatch()` the resident's own hand would use,
        under its own id, charged the normal cost - so proficiency rises
        exactly as it would otherwise, and a mastered trade becomes a
        cheap one in the currency that runs out.
        """
        action = self.continuing.get(agent_id)
        if action is None:
            return None

        # Rule 1, before working: a condition already met must not buy one
        # more repetition the resident did not ask for.
        done = action.satisfied(self)
        if done:
            self.stop_continuing(agent_id, f"You stopped {action.tool}: {done}.")
            return done

        # B1's location row: the gate is checked again at each repetition,
        # and being moved ends the action.
        if self.location_of(agent_id) != action.location_id:
            self.stop_continuing(
                agent_id,
                f"You stopped {action.tool}: you are no longer where you began.",
            )
            return "moved"

        try:
            await registry.execute(self, agent_id, action.tool, dict(action.params))
        except Exception as exc:  # noqa: BLE001
            # Rule 2. A refusal is the world saying the plan no longer
            # works; retrying it until dusk would burn the resident's sim
            # on the same mistake many times over.
            self.stop_continuing(
                agent_id,
                f"You stopped {action.tool} after {action.repetitions}: {exc}",
            )
            return str(exc)

        action.repetitions += 1
        done = action.satisfied(self)
        if done:
            self.stop_continuing(agent_id, f"You stopped {action.tool}: {done}.")
            return done
        return None

    def require_agent(self, agent_id: str) -> AgentLifeRecord:
        from world.tools.errors import ActorNotFoundError

        agent = self.agents.get(agent_id) or self.control_actors.get(agent_id)
        if agent is None:
            raise ActorNotFoundError(agent_id)
        return agent

    def location_of(self, agent_id: str) -> str | None:
        return self.locations.location_of(agent_id)

    # `inventory` is a dataclass containing a single dict (`balances`), so
    # it is included in the deep-copied snapshot by value like every other
    # owned field below; it does not need its own entry in
    # OWNED_STATE_FIELDS. Left out of that tuple deliberately: see
    # snapshot()'s explicit handling of it.

    @property
    def skill_tools(self) -> dict[str, list[str]]:
        """skill name -> the tools its steps name. Read by
        `action_costs.skills_for_tool()` on every dispatch to answer "which
        skills make this tool cheaper".

        Derived on read rather than maintained as a second dict, because a
        cached copy is a chance to disagree with `skill_definitions`, and the
        town has tens of skills, not millions. A PENDING_COUNCIL standing order
        is excluded: it is not followable yet, so it discounts nothing.
        """
        followable = {
            name: d for name, d in self.skill_definitions.items() if self.is_followable(d)
        }

        # A `skill` step contributes the tools the skill it names performs.
        # Run 29: Silas Vance's `saw_through_timber_stack` ran
        # `saw_planks_from_timber` and discounted nothing, so a resident
        # taught the composite paid full price for every plank it cut by
        # walking it. Only FOLLOWABLE sub-skills count - a standing order
        # the council has not enacted discounts nothing on its own, and must
        # not start to by being named inside an ordinary skill - and a
        # skill already on the path is not re-entered, so no loop can form.
        def leaves(definition: SkillDefinition, path: frozenset[str]) -> list[str]:
            out: list[str] = []
            for step in definition.steps:
                if step.kind == "tool" and step.ref:
                    out.append(step.ref)
                elif step.kind == "skill" and step.ref in followable and step.ref not in path:
                    out.extend(leaves(followable[step.ref], path | {step.ref}))
            return out

        return {name: leaves(d, frozenset({name})) for name, d in followable.items()}

    def is_followable(self, definition: SkillDefinition) -> bool:
        """May this workflow be walked right now?

        An ordinary trade skill: always. It orders tools its holder could
        already call one at a time, so there is nothing to permit.

        A governmental one: only once the council has enacted it, which is
        read live from the charter rather than from a stored status. The
        charter is the book of rules and an amendment is the only thing that
        writes to it, so asking the book directly means a standing order
        becomes followable the instant it is signed and stops the instant it
        is repealed - with no enactment callback to forget to fire.
        """
        if not definition.governmental:
            return definition.status == "ACTIVE"
        try:
            charter = self.charter.store.current_charter()
        except (AttributeError, IndexError):
            # A world built without genesis (unit tests): no book, no orders.
            return False
        return definition.name in tuple(charter.standing_orders)

    def _next_actionable(self, definition: SkillDefinition, index: int) -> int:
        """The first step at or after `index` that actually dispatches
        something. `check` steps are the agent's own notes to itself - they
        call nothing, so waiting for one to arrive through dispatch() would
        stall every workflow that contains one, forever."""
        while index < len(definition.steps) and definition.steps[index].kind == "check":
            index += 1
        return index

    def advance_skill_workflows(self, agent_id: str, kind: str, ref: str, tick: int) -> list[str]:
        """Move this agent along any workflow whose next step is `ref`.

        Returns the skills COMPLETED by this act. Called from dispatch() after
        a successful tool call, and then again for each skill it completes, so
        a nested skill finishing advances the outer workflow that was waiting
        on it.

        Progress opens on the first actionable step and advances only in
        order. Out-of-order calls are ignored rather than punished: an agent
        doing something else in the middle of a procedure has simply not
        advanced, which is true and requires no special case.
        """
        completed: list[str] = []
        for key, progress in list(self.skill_progress.items()):
            if progress.agent_id == agent_id and tick - progress.last_advance_tick > SKILL_ABANDONED_AFTER_TICKS:
                # Abandoned. Deleted rather than marked, because the fact worth
                # keeping - which skills get dropped, and at which step - is
                # recorded in the trace, not carried in live state.
                del self.skill_progress[key]

        for name in list(self.skills.get(agent_id, {})):
            definition = self.skill_definitions.get(name)
            if definition is None or not self.is_followable(definition):
                continue
            key = f"{agent_id}::{name}"
            progress = self.skill_progress.get(key)
            index = self._next_actionable(definition, progress.next_step if progress else 0)
            if index >= len(definition.steps):
                continue
            step = definition.steps[index]
            if step.kind != kind or step.ref != ref:
                continue

            following = self._next_actionable(definition, index + 1)
            if following >= len(definition.steps):
                # Walked end to end. THIS is what earns an authored skill its
                # repetition - not the individual tool calls along the way, so
                # a skill nobody actually follows is worth nothing no matter
                # how many tools it names.
                self.skill_progress.pop(key, None)
                self.bump_skill_repetition(agent_id, name)
                completed.append(name)
            else:
                self.skill_progress[key] = SkillProgress(
                    agent_id=agent_id,
                    skill_name=name,
                    next_step=following,
                    started_tick=progress.started_tick if progress else tick,
                    last_advance_tick=tick,
                )

        for finished in list(completed):
            completed.extend(self.advance_skill_workflows(agent_id, "skill", finished, tick))
        return completed

    def skill_playbook(self, agent_id: str, limit: int = 3) -> list[dict[str, Any]]:
        """What this agent knows how to do, ready for its prompt.

        Ordered by how far along the agent already is, then by proficiency: a
        workflow it is halfway through is the most relevant thing it could be
        shown, and a procedure it is good at is worth more prompt than one it
        has barely started. Capped, because an agent with twenty skills would
        otherwise spend its whole context reading its own filing cabinet.

        Skills whose definitions are not followable (an un-enacted standing
        order) are omitted - showing an agent a procedure it may not carry out
        would be inviting it to waste a turn.
        """
        rows: list[dict[str, Any]] = []
        for name, proficiency in self.skills.get(agent_id, {}).items():
            definition = self.skill_definitions.get(name)
            if definition is None or not self.is_followable(definition):
                # A built-in trade (farming, masonry) has a level but no
                # written procedure - there is nothing to show as steps.
                continue
            progress = self.skill_progress.get(f"{agent_id}::{name}")
            rows.append(
                {
                    "name": name,
                    "proficiency": proficiency,
                    "preconditions": definition.preconditions,
                    "next_step": progress.next_step if progress else None,
                    "steps": [s.model_dump() for s in definition.steps],
                }
            )
        rows.sort(key=lambda r: (r["next_step"] is None, -r["proficiency"]))
        return rows[:limit]

    async def authoring_context(self, agent_id: str) -> dict[str, Any]:
        """Everything an author needs about the world and about itself.

        The operator's ask, in their words: "when skillify and toolify tool
        is used, it should have complete knowledge about the environment
        and about itself."

        WHY IT IS NOT ENOUGH THAT `describe_primitives` EXISTS. It does the
        environment half well, and it is a separate call: an agent has to
        know it is there, spend a turn on it, and still be holding the
        answer a turn later when it writes the skill. Measured across three
        runs, `describe_primitives` was called 12 times and `skillify` 3.
        Worse, a REFUSED filing came back naming the problem and twelve
        tool names and nothing about the author at all, so the second
        attempt was as blind as the first and cost another turn.

        Returned on every skillify and toolify result, accepted or refused.
        Authoring is rare enough that the tokens are cheap and the turn
        they save is not.

        Never raises. An unknown agent gets a context with empty holdings
        rather than an error: this is what a REFUSAL carries, and a
        bookkeeping failure inside an error path would replace a useful
        message with a stack trace.
        """
        # Imported here: the registry imports this module.
        from world.tools.registry import location_label

        from world.ledger.action_costs import DEFAULT_SCHEDULE, ActionActor, proficiency_for

        vocabulary: list[dict[str, Any]] = []
        registry = getattr(self, "tool_registry", None)
        for name, spec in sorted(getattr(registry, "tools", {}).items()):
            # DESIGN.md section 2: "Non-members cannot see a confidential
            # tool exists." A helpful listing attached to a REFUSAL would
            # be a worse leak than the one the visibility rule prevents,
            # because it arrives unasked and nobody had to probe for it.
            # This is why the whole method is async: the government and
            # organization checks read the charter store.
            if not await registry.is_visible(spec, self, agent_id):
                continue
            proficiency = proficiency_for(self, agent_id, name)
            vocabulary.append(
                {
                    "name": name,
                    "description": spec.description,
                    "params": sorted(spec.params_model.model_fields.keys()),
                    # Priced for THIS resident, discount included. An author
                    # choosing between two ways to do one job has to see
                    # which is cheaper for it, not a list price it then has
                    # to adjust in its head. The minimum, because a tool
                    # that charges per unit costs more for a bigger job -
                    # `quote_action_cost` prices one exact call.
                    "sim_cost_minimum": DEFAULT_SCHEDULE.cost_for(
                        name, {}, ActionActor(agent_id, skill_proficiency=proficiency)
                    ),
                    "your_proficiency": proficiency,
                    "requires_seat": spec.actor_requirements.require_government_seat,
                    # Plain data. This list rides on every refusal from
                    # do_in_order, skillify and toolify, and a raw gate
                    # FUNCTION in it turned each of those refusals into
                    # "Internal Server Error" (50-resident run).
                    "at_location": location_label(spec.location_gate),
                }
            )

        holding = dict(self.inventory.balances.get(agent_id, {}))
        return {
            "you": {
                "agent_id": agent_id,
                "name": self.agent_names.get(agent_id, agent_id),
                "occupation": self.occupations.get(agent_id),
                "workplace": self.workplaces.get(agent_id),
                "location_id": self.locations.location_of(agent_id),
                "sim": self.ledger.sim_balance(agent_id) if agent_id in self.agents else 0,
                "usd_cents": self.ledger.usd_balance(agent_id) if agent_id in self.agents else 0,
                "skills": dict(self.skills.get(agent_id, {})),
                # The counts the proficiency was derived from, and the same
                # evidence `authoring_hint` speaks from. The hint says "you
                # keep doing this"; without these the author cannot see how
                # many times, for what else, or how close anything is - so
                # the invitation and the evidence for it arrived in one
                # payload with only the invitation legible. Absent rather
                # than zero-filled: fifty-five zeroes is noise to read past.
                "repetitions": {
                    tool: count
                    for tool, count in sorted(self.skill_reps.get(agent_id, {}).items())
                    if count
                },
                "holding": holding,
            },
            "already_written": {
                # So an author does not file a second copy of something it
                # already has, and can see what it would be revising.
                "skills": sorted(
                    name
                    for name, definition in self.skill_definitions.items()
                    if getattr(definition, "author_agent_id", None) == agent_id
                    or name in self.skills.get(agent_id, {})
                ),
                "tools": sorted(
                    name
                    for name, definition in self.custom_tools.items()
                    if getattr(definition, "creator_agent_id", None) == agent_id
                ),
                # What those procedures actually SAY. A name is enough to
                # avoid filing a duplicate and not enough to improve one,
                # and improving is the point of writing it down: a skill
                # that can only be revised blind is a snapshot of the first
                # time an agent got something to work.
                "skill_steps": {
                    # Direct attribute access, not getattr-with-a-default:
                    # SkillStep is a typed model whose fields are known, and
                    # a three-argument getattr here would quietly answer
                    # None if one were ever renamed - which is the exact
                    # failure test_no_silent_defaults exists to prevent, and
                    # which it caught in this very block.
                    name: [
                        {
                            "order": step.order,
                            "kind": step.kind,
                            "ref": step.ref,
                            "note": step.note,
                        }
                        for step in definition.steps
                    ]
                    for name, definition in self.skill_definitions.items()
                    if getattr(definition, "author_agent_id", None) == agent_id
                    or name in self.skills.get(agent_id, {})
                },
            },
            "tools_you_can_use": vocabulary,
        }

    def authoring_hint(self, agent_id: str) -> str | None:
        """One line telling this agent it could write down what it keeps doing
        - or None, which is the common case.

        Evidence-driven, not a standing instruction. Putting "you may invent
        tools" in every prompt forever costs 314 agents a paragraph a tick and
        teaches nothing: the agent has no reason to act on it at any particular
        moment, so it reads as furniture and gets ignored. A line that appears
        only once an agent has actually repeated something is a line with a
        reason attached, and it disappears the moment the agent acts on it.

        Names the TOOL, never the content. The line used to name neither, on
        the grounds that handing agents the answer would measure our
        vocabulary rather than theirs - and that is still true of what to
        write, which is why the hint says nothing about it.

        It is not true of which verb to reach for. Measured across a run:
        the hint fired in 61 of 351 sessions, `skillify` was called 0 times
        and `describe_primitives` once. An agent told "write a procedure
        down", with forty-eight tools in its schema, is not declining to
        author - it has not connected the sentence to the verb. Naming the
        verb costs nine words and leaves every interesting decision (whether
        to write anything, what to write, what to call it, which steps) with
        the agent.
        """
        held = {
            name
            for name in self.skills.get(agent_id, {})
            if name in self.skill_definitions
        }
        covered = {
            tool
            for name in held
            for tool in self.skill_tools.get(name, [])
        }
        # Only the tail: this runs per agent per tick, and what matters is what
        # the agent has been doing lately, not what it did on day one.
        counts: dict[str, int] = {}
        for record in self.tool_invocations[-AUTHORING_HINT_SCAN:]:
            if record.actor_id != agent_id or record.tool_name in covered:
                continue
            rule = SCHEDULE_TABLE.get(record.tool_name)
            if rule is None or rule.category not in AUTHORING_HINT_CATEGORIES:
                # Walking and looking are not a procedure. See
                # AUTHORING_HINT_CATEGORIES: every pair that crossed the old
                # threshold in two consecutive runs was `move` or
                # `look_around`.
                continue
            counts[record.tool_name] = counts.get(record.tool_name, 0) + 1
        if not counts:
            return None
        tool, repeats = max(counts.items(), key=lambda kv: kv[1])
        if repeats < AUTHORING_HINT_REPEATS:
            # Nothing done three times. But a procedure is a SEQUENCE, and
            # this rule only ever looked for one tool used again and again.
            #
            # Run 19: the town's highest repetition count for any single
            # tool was two, so the hint could not fire at all in a 59-tick
            # run, and "nothing was authored" described the length of the
            # run rather than the residents. Corwin Thorne walked to the
            # forest, felled timber and walked back, twice over - a job
            # done end to end and repeated, which is precisely what
            # `skillify` exists to write down, and invisible to a counter
            # that only knew how to count one name at a time.
            #
            # A PAIR of consecutive work actions, done twice. Two rather
            # than three because a pair is already two actions' worth of
            # evidence, and the same exclusions apply - a pair of `move`
            # and `look_around` is still walking about, not a trade.
            pair = self._repeated_work_pair(agent_id, covered)
            if pair is None:
                return None
            first, second, times = pair
            return authoring_hint_text(
                f"'{first}' then '{second}'",
                times,
                decisions=self._decisions_spent_on(agent_id, {first, second}),
                call_ceiling=MAX_CALLS_PER_TICK,
                already_written=self._procedure_already_held(agent_id, {first, second}),
            )
        return authoring_hint_text(
            f"'{tool}'",
            repeats,
            decisions=self._decisions_spent_on(agent_id, {tool}),
            call_ceiling=MAX_CALLS_PER_TICK,
            already_written=self._procedure_already_held(agent_id, {tool}),
        )

    def _decisions_spent_on(self, agent_id: str, tools: set[str]) -> int:
        """How many separate calls this agent's repetition has actually cost.

        A call with no `program_id` was its own decision: one model call
        that bought one act. A call that carries one was a step inside a
        program somebody had already decided, and charging it to the
        resident's decision count would make `do_in_order` look no
        cheaper than doing the same work by hand - which is the precise
        claim the number exists to settle.

        Same window as the counts above, so the figure and the "you have
        done this N times" it sits beside are read off the same scan and
        cannot disagree.
        """
        return sum(
            1
            for record in self.tool_invocations[-AUTHORING_HINT_SCAN:]
            if record.actor_id == agent_id
            and record.tool_name in tools
            and record.program_id is None
        )

    def _procedure_already_held(
        self, agent_id: str, tools: set[str]
    ) -> "tuple[str, str] | None":
        """(skill name, holder's name) if somebody else can already do this.

        Run 29 filed `Timber Felling` and `timber felling` four ticks
        apart, by two residents, over the same single action. Nothing in
        the town could notice, and nothing has ever told a resident that
        a neighbour holds a procedure it lacks - which is why
        `teach_skill`, verified working on 2026-09-14, has never been
        called in any run.

        Only names what the verb would actually allow. A teacher needs
        proficiency above 0 (`teach_skill_handler` refuses otherwise), so
        an author who filed a procedure and never walked it is not
        offered; and a business's trade secret is only named to somebody
        who works there, because `teach_skill` would refuse the lesson
        anyway and naming it would advertise a secret the world is meant
        to keep.
        """
        best: tuple[int, str, str] | None = None
        for name, definition in self.skill_definitions.items():
            if definition.status != "ACTIVE":
                continue
            if not tools.issubset(set(self.skill_tools.get(name, []))):
                continue
            if self.skills.get(agent_id, {}).get(name, 0) > 0:
                continue  # already yours; there is nothing to be taught
            if definition.namespace == "business":
                business = self.businesses.get(definition.owner_id or "")
                staff = (
                    {business.owner_agent_id, *business.employee_agent_ids}
                    if business is not None
                    else set()
                )
                if agent_id not in staff:
                    continue
            for holder, held_skills in self.skills.items():
                proficiency = held_skills.get(name, 0)
                if holder == agent_id or proficiency <= 0:
                    continue
                if best is None or proficiency > best[0]:
                    best = (proficiency, name, self.agent_names.get(holder, holder))
        if best is None:
            return None
        _, skill_name, holder_name = best
        return skill_name, holder_name

    def note_turn(self, agent_id: str, tick: int) -> None:
        """Record that this resident is taking a turn now.

        Called where the perception is built, which is the one place that
        knows a turn is beginning. `tool_invocations` already records
        every call with its actor and tick; what was missing was the other
        half - when a turn STARTED - without which "did anything happen
        last turn" cannot be asked.

        Bounded to the last few, because only the tail is ever read and
        250 residents times a whole run is a list that grows forever.
        """
        ticks = self.turn_ticks.setdefault(agent_id, [])
        if ticks and ticks[-1] == tick:
            return  # the same turn, seen twice
        ticks.append(tick)
        if len(ticks) > MAX_TURN_TICKS_KEPT:
            del ticks[: len(ticks) - MAX_TURN_TICKS_KEPT]

    def silent_turns_report(self, agent_id: str) -> "str | None":
        """One line saying the last few turns reached nobody - or None,
        which is what a resident who is acting gets.

        A FACT, not an instruction. It names no action, no subject and no
        addressee, the same way `open_questions` reports that somebody
        asked you something and stops there. A line reading "you must call
        a tool" would produce a town that calls tools when told to, which
        measures our prompt rather than the residents.

        Never raises. It runs on every agent's every turn, and a bad row
        in tool_invocations must not take a resident's turn down with it.
        """
        ticks = self.turn_ticks.get(agent_id) or []
        if len(ticks) < 2:
            return None  # no previous turn to have been silent

        silent = 0
        for start, end in zip(reversed(ticks[:-1]), reversed(ticks[1:])):
            acted = False
            for record in self.tool_invocations:
                actor = getattr(record, "actor_id", None)
                tick = getattr(record, "tick", None)
                if actor == agent_id and isinstance(tick, int) and start <= tick < end:
                    acted = True
                    break
            if acted:
                break
            silent += 1

        if silent < SILENT_TURN_REPORT_MIN:
            return None
        turns = "turn" if silent == 1 else "turns"
        return (
            f"Your last {silent} {turns} ended without you touching the world, so "
            f"whatever you thought or wrote in them reached nobody and changed "
            f"nothing. Nobody heard it and the town has no record of it."
        )

    def convening_hint(self, agent_id: str) -> "str | None":
        """One line telling this agent it could frame the talk going on around
        it - or None, which is the common case.

        `start_conversation` shipped served, priced and reachable to twenty
        engines across runs 18 and 19, and was never once called. That is
        the same result `skillify` got, for the reason
        SELF_AUTHORING_AGENTS.md section 8 records: an LLM agent does not
        spontaneously reach for a verb it was merely handed a schema for.
        The fix that worked there was an evidence-driven line naming the
        verb, and this is that fix applied to the same problem.

        Evidence-driven, not a standing instruction. A permanent "you may
        convene" costs every agent a paragraph every tick, carries no reason
        at any particular moment, and reads as furniture. This fires only when
        the room has actually arrived at the situation the verb exists for:
        several people present, and talk already happening between them.

        Names the VERB, never the SUBJECT. What a town chooses to coordinate
        on when nobody suggested it is the experiment; supplying the topic
        would measure our vocabulary instead of theirs.
        """
        here = self.locations.location_of(agent_id)
        if here is None:
            return None
        present = [
            other
            for other in self.agents
            if other != agent_id and self.locations.location_of(other) == here
        ]
        if len(present) + 1 < CONVENE_HINT_MIN_PRESENT:
            return None
        if self.open_conversations_at(here, self.current_tick):
            # A conversation is already framed here. Nagging somebody to
            # open one is how a hint stops being information.
            return None

        recent_speakers = {
            row.get("speaker_agent_id")
            for row in (getattr(self, "utterances", None) or ())
            if isinstance(row, dict)
            and isinstance(row.get("tick"), int)
            and self.current_tick - row["tick"] <= CONVENE_HINT_WINDOW_TICKS
            and row.get("location_id") == here
        }
        if not recent_speakers - {agent_id, None}:
            # People standing together in silence are not a discussion
            # waiting to happen.
            return None

        return (
            f"{len(present) + 1} of you are here and talking. `start_conversation` "
            "opens one on a named subject with the people present, so everyone "
            "in it can see what is being discussed and answer into it by id."
        )

    def open_questions(self, agent_id: str, now_tick: int) -> list[dict[str, Any]]:
        """Remarks addressed to this agent that it has not answered yet.

        Answered means this agent later spoke TO THAT SPEAKER. Speaking
        aloud does not discharge a directed question, and answering
        somebody else does not either - otherwise an agent could clear
        every obligation it had by muttering about the weather.

        Oldest first, because the person waiting longest is the one with
        the strongest claim, and capped because a resident shown a backlog
        reads a list rather than a question. Only the most recent question
        from any one speaker survives: Bram asked the room for a copper
        buyer five times in a single tick, and five copies of one want is
        not five wants.

        Reads `utterances`, which is already kept, already capped, and
        already carries speaker, listener and tick. Nothing new is
        recorded to answer this - the world simply had no way to be asked.
        """
        rows = getattr(self, "utterances", None) or []
        answered_speakers: set[str] = set()
        asked: dict[str, dict[str, Any]] = {}

        for row in rows:
            if not isinstance(row, dict):
                continue
            tick = row.get("tick")
            if not isinstance(tick, int):
                continue
            speaker = row.get("speaker_agent_id")
            listener = row.get("listener_agent_id")
            # Everyone this remark spoke to, not only the id in the
            # parameter. Ezra Okonjo answered Father Anselm and Tova
            # Okonjo in one utterance addressed to Anselm; before
            # addressed_agent_ids existed, Tova stayed on the books as
            # unanswered and asked again three ticks later.
            spoke_to = [
                other
                for other in [listener, *(row.get("addressed_agent_ids") or [])]
                if isinstance(other, str)
            ]
            if speaker == agent_id and spoke_to:
                # This agent answering somebody closes anything that
                # person had asked before now.
                answered_speakers.update(spoke_to)
                continue
            if agent_id not in spoke_to or not isinstance(speaker, str):
                continue
            if speaker == agent_id:
                continue
            if now_tick - tick > OPEN_QUESTION_TTL_TICKS:
                continue
            previous = asked.get(speaker)
            if previous is None or tick >= previous["tick"]:
                asked[speaker] = row

        still_open = [
            row
            for speaker, row in asked.items()
            if speaker not in answered_speakers
            # An answer only counts if it came AFTER the question.
            or not any(
                isinstance(r, dict)
                and r.get("speaker_agent_id") == agent_id
                and speaker
                in [
                    other
                    for other in [
                        r.get("listener_agent_id"),
                        *(r.get("addressed_agent_ids") or []),
                    ]
                    if other
                ]
                and isinstance(r.get("tick"), int)
                and r["tick"] >= row["tick"]
                for r in rows
            )
        ]
        # TOOL-17. A question whose asker has walked away stops being a
        # question you can answer.
        #
        # `say` reaches only somebody in the same place - the perception
        # says so in as many words - so once the asker has left, the
        # prompt was carrying an obligation the resident had no way to
        # discharge, for the full 36-tick TTL. The reports call this
        # "unanswered questions persist 30+ ticks after the asker left".
        # Worse than clutter: it is the one line in the prompt that
        # another resident is supposedly waiting on, and it outranks the
        # town roster - so a stale one displaces a live one.
        #
        # Dropped from the PROMPT only. The utterance stays in
        # `self.utterances` and in the trace, so "asked and never
        # answered" remains countable; what changes is that a resident
        # is not asked to answer into an empty room.
        here = self._where_or_none(agent_id)
        if here is not None:
            still_open = [
                row
                for row in still_open
                if self._asker_is_still_reachable(row.get("speaker_agent_id"), here)
            ]
        still_open.sort(key=lambda r: r["tick"])
        return still_open[:MAX_OPEN_QUESTIONS_SHOWN]

    def _asker_is_still_reachable(self, speaker_id: Any, here: "str | None") -> bool:
        """Whether an answer could actually reach the person who asked.

        Permissive on anything it cannot establish. A speaker who is not
        a resident, or a reader with no location, is not evidence that
        the question is dead - and dropping a live question is a worse
        error than keeping a stale one, because the resident never learns
        it was asked at all.
        """
        if not isinstance(speaker_id, str):
            return True
        # No `speaker_id in self.agents` check: `_where_or_none` already
        # answers None for anybody this world cannot place, and reading
        # `self.agents` would raise on the partially-built WorldState
        # several tests construct to exercise this bookkeeping alone.
        there = self._where_or_none(speaker_id)
        if there is None:
            return True
        return satisfies_gate(there, here)

    def _where_or_none(self, agent_id: str) -> "str | None":
        """Where somebody is, or None if this world cannot say.

        `location_of` reads the location index, and a `WorldState` built
        without one raises. That is a legitimate shape - several tests
        construct exactly it to exercise the utterance bookkeeping alone -
        and a world that cannot answer "where is everybody" must not
        start silently deleting questions from prompts.
        """
        try:
            return self.location_of(agent_id)
        except Exception:
            return None

    def awaiting_reply(self, agent_id: str, now_tick: int) -> list[dict[str, Any]]:
        """Questions THIS agent asked that nobody has answered yet.

        `open_questions` is the other side of the same fact: it tells the
        person who was asked that they owe an answer. Nothing told the
        asker, so a resident who spoke into silence could not tell "they
        have not answered" from "I never said it" - and asking again is
        the rational move when you cannot tell.

        Run 19 has three residents doing exactly that. Marta Thorne
        repeats one sentence verbatim four ticks apart; Tova Okonjo
        demands the same yes-or-no answer at t40 and again at t43; Silas
        Vance gives the same speech about his unsold plank twice. Every
        repeat is a billed turn that changed nothing.

        Only DIRECTED remarks count. "Morning, all" is not a debt anyone
        owes, and treating it as one would put a standing complaint in
        every resident's prompt.

        Answered means the person asked later spoke to this agent - by
        parameter or by naming them, which is why `addressed_agent_ids`
        is read here as well as `listener_agent_id`. Ezra Okonjo answered
        Tova inside a remark addressed to Father Anselm, and before that
        field existed the world went on telling her she was unanswered.
        """
        rows = self.utterances

        def answered_by(listener: str, since_tick: int) -> bool:
            for row in rows:
                if not isinstance(row, dict) or row.get("speaker_agent_id") != listener:
                    continue
                tick = row.get("tick")
                if not isinstance(tick, int) or tick < since_tick:
                    continue
                heard = [row.get("listener_agent_id"), *(row.get("addressed_agent_ids") or [])]
                if agent_id in [h for h in heard if h]:
                    return True
            return False

        # Most recent question per person asked: five copies of one want is
        # not five wants, the same reasoning as open_questions.
        asked: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("speaker_agent_id") != agent_id:
                continue
            tick = row.get("tick")
            if not isinstance(tick, int) or now_tick - tick > OPEN_QUESTION_TTL_TICKS:
                continue
            for listener in {
                listener
                for listener in [
                    row.get("listener_agent_id"),
                    *(row.get("addressed_agent_ids") or []),
                ]
                if isinstance(listener, str) and listener != agent_id
            }:
                previous = asked.get(listener)
                if previous is None or tick >= previous["tick"]:
                    asked[listener] = {**row, "listener_agent_id": listener}

        waiting = [
            {
                **row,
                "listener_name": self.agent_names.get(
                    row["listener_agent_id"], row["listener_agent_id"]
                ),
                "ticks_waiting": now_tick - row["tick"],
            }
            for listener, row in asked.items()
            if not answered_by(listener, row["tick"])
        ]
        waiting.sort(key=lambda row: row["tick"])
        return waiting[:MAX_OPEN_QUESTIONS_SHOWN]

    def _repeated_work_pair(
        self, agent_id: str, covered: set[str]
    ) -> tuple[str, str, int] | None:
        """The consecutive pair of work actions this agent has repeated
        most, if it has repeated one at least twice.

        Consecutive in THIS agent's own actions, so another resident
        acting in between does not break the pair - what matters is what
        the agent did next, not what the town did next.
        """
        from world.ledger.action_costs import DEFAULT_SCHEDULE

        mine: list[str] = []
        for record in self.tool_invocations[-AUTHORING_HINT_SCAN:]:
            if record.actor_id != agent_id or record.tool_name in covered:
                continue
            rule = DEFAULT_SCHEDULE.rule_for(record.tool_name)
            if rule is None or rule.category not in AUTHORING_HINT_CATEGORIES:
                continue
            mine.append(record.tool_name)

        pairs: dict[tuple[str, str], int] = {}
        for first, second in zip(mine, mine[1:]):
            if first == second:
                # Already the single-tool rule's business.
                continue
            pairs[(first, second)] = pairs.get((first, second), 0) + 1
        if not pairs:
            return None
        (first, second), times = max(pairs.items(), key=lambda kv: kv[1])
        if times < AUTHORING_HINT_PAIR_REPEATS:
            return None
        return first, second, times

    def bump_skill_repetition(self, agent_id: str, skill: str) -> int:
        """One successful use of a tool this skill discounts. Returns the new
        proficiency.

        Learning by doing, and ONLY by doing: this is called from
        `ToolRegistry.dispatch()` after the handler returns, so an action that
        was refused, gated, or raised teaches nothing. Practice is the act, not
        the intention.

        Never lowers a level. An agent taught a skill at 40 by someone else
        (`skill_sources`) keeps 40 while its own reps are still worth less;
        the two paths to competence do not fight, the better one just wins.
        """
        from world.ledger.action_costs import proficiency_from_reps

        reps = self.skill_reps.setdefault(agent_id, {})
        reps[skill] = reps.get(skill, 0) + 1
        held = self.skills.setdefault(agent_id, {})
        before = held.get(skill, 0)
        level = max(before, proficiency_from_reps(reps[skill]))
        held[skill] = level
        self.last_practice_moved_the_level = level != before
        return level

    def snapshot(self) -> dict[str, Any]:
        """Deep copy of every owned-state field, for
        ToolRegistry.execute()'s roll-back-on-failure. Plain
        `copy.deepcopy` is safe here specifically because every owned
        field is either a dict/list of pydantic models or, for
        `inventory`, a dataclass of nested dicts of ints - none of it
        holds a lock, a socket, or anything else deepcopy chokes on
        (contrast `ledger`/`charter`/`event_bus`/`locations`, deliberately
        excluded, which do hold locks).

        ONE FIELD IS COPIED SHALLOWLY, and it is the difference between a
        town that answers and one that times out. `tool_invocations` is a
        ring of up to MAX_TOOL_INVOCATIONS_KEPT (5,000) records, and this
        method runs inside `ToolRegistry.execute()`'s single global lock on
        every call every resident makes. Deep-copying a full ring measures
        at ~27ms, against 0.1ms for all forty other fields put together -
        so past the first five thousand calls, every tool call in the town
        paid ~27ms of copying that nothing ever read, serialised behind one
        lock. Thirty engines posting at once queue on that, and the queue
        is what the 10s client read timeout in `engine/src/sim/world_tools.py`
        was hitting: the election30 run lost about one action in seven to
        `ReadTimeout` - roughly 1,800 of 12,600 - and a resident cannot tell
        a lost call from a refusal, so it reasons all turn from an act it
        never performed.

        A shallow `list()` is exactly as correct here, and only here.
        Rollback of this field has to undo list MEMBERSHIP - the phantom
        record a failed composite would otherwise leave behind, which is
        why the field is in OWNED_STATE_FIELDS at all (see
        `ToolInvocationRecord`) - and membership is all a shallow copy of a
        list restores. It would be wrong if a record could change after it
        was appended, because the snapshot would then share the mutation;
        nothing mutates one. The only writes to `tool_invocations` anywhere
        in `app/world` are the append and the ring trim in
        `registry.dispatch()`, and every read is an iteration.
        """
        data = {
            name: (
                list(getattr(self, name))
                if name == "tool_invocations"
                else copy.deepcopy(getattr(self, name))
            )
            for name in OWNED_STATE_FIELDS
        }
        data["inventory"] = copy.deepcopy(self.inventory)
        return data

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Put a snapshot back, rebuilding the typed values it held.

        `snapshot()` deepcopies, so an in-process round trip keeps real
        `PricePost`, `WagePromise`, `Intention` objects and this used to be
        a blind `setattr` that worked fine. A checkpoint on DISK is JSON,
        and JSON has no idea what a PricePost is - so a resumed run got
        plain dicts in every one of these fields, and the first line of code
        to touch one died:

            AttributeError: 'dict' object has no attribute 'ask_sim'

        Observed killing a real run at its first dispatch. It is worse than
        one crash: `wage_promises`, `intentions`, `loans` and `crime_cases`
        have the same shape, so a resumed town owed nobody, intended
        nothing and had no open cases, whichever one happened to be read
        first.
        """
        for name, value in snapshot.items():
            setattr(self, name, _rehydrated(name, value))
