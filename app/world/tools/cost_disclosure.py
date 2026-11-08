"""Cost visibility: DESIGN.md section 3, "Everything costs: living is not
free" and section 4's "law of the town."

"Agents must be able to see all of this. The cost schedule is published
in the tool descriptions they read, their wallet balances and burn rate
are in their context every tick, and they can query what any action will
cost before taking it." This module is that visibility layer, and nothing
more: it prices nothing, taxes nothing, accrues nothing. It only takes
numbers a costing/upkeep system already produced and puts them where an
agent will actually read them, in three shapes:

1. ``annotate_tool_description`` / ``annotate_tool_descriptions``: fold a
   tool's published sim-currency cost straight into the description text
   the agent's tool list already shows it, so the price is not a second
   lookup away.
2. ``quote_action_cost`` (pure function) and ``build_action_cost_quote_spec``
   (the ready-to-register ``ToolSpec`` wrapping it): "what would this
   specific call cost me right now, with these exact params, before I
   commit to it."
3. ``build_economic_context`` / ``render_economic_context_block``: the
   per-tick block DESIGN.md asks for, wallet, sim balance, recent burn
   rate, today's accrued upkeep, household upkeep and dependents, and how
   long the agent can keep thinking at its current rate.

CRITICAL CONSTRAINT, enforced the same way ``world.life.soul_seed``
enforces it (that module's own docstring, "the RULES, never the
STRATEGY"): every string this module renders states a price or a rule,
never a tactic. There is deliberately no line here that says "sell,"
"save," "cooperate," "trade with," "should," or anything else that would
nudge an agent toward one survival strategy over another, because DESIGN.md
section 4 is explicit that which strategies emerge is the experiment, not
a parameter to tune. ``tests/test_cost_disclosure.py`` carries the same
strategy-word tripwire ``tests/test_soul_seed.py`` already does, run
against every string this module can produce.

ASSUMED INTERFACE (read this before wiring anything in): this task's
brief names a sibling module, ``world.ledger.action_costs``, with a class
called ``ActionCostSchedule``, as the single source of truth for what
things cost. That module does not exist yet as of this writing, so this
file does not import it, importing a module that is not there yet would
break every test in this file the moment it is collected. Instead,
``ActionCostScheduleLike`` below is a ``typing.Protocol``: the exact
shape this file needs, structural rather than nominal, so the real
``ActionCostSchedule`` never has to import this module or subclass
anything here to satisfy it, it only has to have a matching ``quote``
method. What is assumed, precisely:

- ``schedule.quote(tool_name, params=None)`` returns an object with a
  ``sim_cost: int`` and a ``basis: str`` (a short human-readable "why this
  number" fragment, e.g. "10 sim per tile travelled", "flat fee", "3% of
  trade value"), or ``None`` if the schedule has no published entry for
  that tool (uncosted, e.g. a pure-read tool).
- Calling ``quote(tool_name)`` with ``params=None`` is how a tool
  description gets annotated at registration time, before any real call
  params exist, so for a variable-cost tool this is expected to return
  the schedule's *reference* number (the per-unit rate DESIGN.md section 3
  describes, e.g. travel's per-tile figure) with a ``basis`` that says so,
  not a guess at the eventual total.
- Calling ``quote(tool_name, params)`` with the actual call params
  resolves a variable-cost tool to the real number for that specific call
  (distance travelled, trade value, whatever the schedule's own pricing
  rule for that tool needs from params).

If the real ``ActionCostSchedule`` ends up shaped differently, for
instance a differently named method, the fix is a small adapter next to
whichever call site constructs it (bootstrap.py), not a change to every
caller in this file: wrap the real object in a one-method shim that
forwards to whatever it actually calls its pricing lookup.

HOUSEHOLD UPKEEP: DESIGN.md section 3 also describes daily upkeep
(food/shelter/heat, charged to the household) and dependents (minors,
the retired) who consume it without earning. As of this writing there is
no accrual system anywhere in this repo that produces those numbers
either (no ``UPKEEP`` transaction reason on the ledger, no daily-upkeep
constant in ``world.life.constants``, see this task's own report for the
grep that confirmed it). This module therefore never computes upkeep
itself; ``HouseholdUpkeepView`` is the shape whatever module ends up
owning upkeep accrual is expected to hand this one, and
``build_economic_context``'s ``accrued_upkeep_today_sim`` /
``household`` parameters are where that data enters. Until that system
exists, a caller can simply omit ``household`` (renders as "no household
upkeep is charged to you right now," which is true today) and leave
``accrued_upkeep_today_sim`` at its default of 0.

What this module CAN already prove against the real thing, and does, in
its own tests: wallet balances and recent inference-burn history come
straight off a real ``world.ledger.core.Ledger`` (``usd_balance``,
``sim_balance``, ``transactions``, ``resolve_usd_payer``), driven with
real ``open_agent``/``burn_for_inference`` calls in tests, not a mocked
ledger. Tool descriptions get annotated against real ``ToolSpec``
objects pulled from a real ``world.tools.bootstrap.build_world()``
registry, not invented stub strings. Only the two genuinely-unbuilt
seams above (action costs, upkeep accrual) are accepted as caller-supplied
data, structurally typed rather than mocked.

Wiring this in (none of the files below are owned by this task, this
section exists so whoever owns each one has a one-line answer, matching
``world.life.soul_seed``'s own "Wiring this in" convention):

- ``world.tools.state.WorldState`` needs one new field,
  ``action_costs: ActionCostScheduleLike | None = None``, so
  ``quote_action_cost_handler`` below has somewhere real to read a
  schedule from at call time (it currently reads
  ``getattr(world, "action_costs", None)`` defensively and raises
  ``ActionCostScheduleUnavailableError`` when it is absent, so nothing
  here breaks before that field exists, it just always reports the
  schedule as unavailable).
- ``world.tools.bootstrap.build_world()`` needs two additions once
  ``world.ledger.action_costs.ActionCostSchedule`` exists: construct it
  and pass it into ``WorldState(..., action_costs=schedule)``, and, right
  after the primitive-module registration loop, rewrite every registered
  ``ToolSpec``'s description through ``annotate_tool_description`` (a
  ``ToolSpec`` is frozen, so this is a ``dataclasses.replace`` per spec,
  not a mutation):
  ``registry.tools[name] = dataclasses.replace(spec, description=cost_disclosure.annotate_tool_description(spec.description, name, schedule))``.
  Also register ``build_action_cost_quote_spec()`` itself, the same way
  ``meta.build_specs(registry)`` is registered today (after
  ``registry.freeze_primitives()``, so it can never become composable
  into an invented tool via the meta-tool, matching every tool ``meta.py``
  itself registers with ``is_primitive=False``): one more
  ``registry.register(cost_disclosure.build_action_cost_quote_spec())``
  line.
- Whoever builds the per-tick prompt assembly layer (``world.core.engine``'s
  own docstring calls this "not yet built" for the same reason
  ``soul_seed.py``'s genesis-boot call site does not exist yet either):
  call ``build_economic_context(world.ledger, agent_id, world.tick(), ...)``
  once per due agent's turn, render it with
  ``render_economic_context_block``, and append the result as its own
  dynamic ``SystemPromptBlock`` (``engine/src/herald/types.py``), the
  per-tick sibling of the stable, genesis-only block
  ``build_soul_seed`` already produces, never merged into that same
  string so the two stay independently editable and cacheable at
  different TTLs (the law of the town is stable across a whole run;
  this block changes every tick).

Known limitation, flagged rather than silently accepted: ``ToolHandler``
(``world.tools.registry.ToolHandler``) only receives ``(world, agent,
params)``, not a reference to the live ``ToolRegistry``, so
``quote_action_cost_handler`` below has no way to run the registry's own
``is_visible`` namespace check against the ``tool_name`` a caller asks it
to price. In practice this only matters for GOVERNMENT/BUSINESS/
ORGANIZATION-namespace tools (DESIGN.md section 2: "non-members cannot
see a confidential tool exists"), a non-member who names a hidden tool
here would get back whatever the schedule publishes for it, which could
leak that the tool exists even though ``ToolNotFoundError`` would have
hidden it from a normal ``execute()`` call. The clean fix needs a seam
this file cannot build alone (the registry threading a visibility
callback into the handler, or the schedule itself only carrying entries
for PUBLIC-namespace primitives); until that lands, whoever wires this
in should have ``ActionCostSchedule`` only publish entries for PUBLIC
tools, keeping this function's visible surface a strict subset of what a
normal tool list already shows every agent.
"""

from __future__ import annotations

import bisect
import weakref
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from world.core.clock import TICKS_PER_DAY
from world.ledger.core import Ledger
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import SIM_PER_USD_CENT
from world.life.models import AgentLifeRecord
from world.life.soul_seed import format_sim, format_usd_cents
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState

# One game-day of inference-burn history (DESIGN.md section 1: 144
# ticks/game-day) is the default window for "recent burn rate" - long
# enough to smooth over a single idle or unusually busy tick, short
# enough that a burn rate from a week ago is not still being quoted as
# "recent."
DEFAULT_BURN_WINDOW_TICKS = TICKS_PER_DAY


# ---------------------------------------------------------------------------
# Assumed interface onto world.ledger.action_costs.ActionCostSchedule (see
# module docstring's "ASSUMED INTERFACE" section for exactly why this is a
# Protocol, not an import).
# ---------------------------------------------------------------------------


@runtime_checkable
class ActionCostQuoteLike(Protocol):
    """The shape this module needs back from a schedule's ``quote()``."""

    sim_cost: int
    basis: str


@runtime_checkable
class ActionCostScheduleLike(Protocol):
    """The shape this module needs from ``world.ledger.action_costs.
    ActionCostSchedule``. Structural: any object with a matching
    ``quote`` method satisfies this, no inheritance required either
    direction. See module docstring for what ``params=None`` versus a
    real params mapping is expected to mean. ``@runtime_checkable`` so
    tests (and any future caller) can confirm a candidate object
    satisfies this shape with a plain ``isinstance`` check rather than
    trusting duck typing blind.
    """

    def quote(
        self, tool_name: str, params: Mapping[str, Any] | None = None
    ) -> ActionCostQuoteLike | None: ...


class ActionCostScheduleUnavailableError(RuntimeError):
    """Raised by ``quote_action_cost_handler`` when ``WorldState`` has no
    ``action_costs`` schedule wired in yet. Not a ``world.tools.errors.
    ToolError`` subclass, on purpose, matching how sibling-system
    failures (a ``world.ledger.errors.InsufficientFundsError``, say)
    already propagate through ``ToolRegistry.dispatch`` unwrapped
    (registry.py's own module docstring): this is a sibling-system
    (action-cost schedule) not being configured yet, not a bad call.
    """

    def __init__(self) -> None:
        super().__init__(
            "no action-cost schedule is wired into this world yet "
            "(WorldState.action_costs is not set); see cost_disclosure.py's "
            "module docstring, 'Wiring this in'"
        )


# ---------------------------------------------------------------------------
# 1. Publishing costs into tool descriptions.
# ---------------------------------------------------------------------------


def describe_cost(tool_name: str, schedule: ActionCostScheduleLike) -> str:
    """The one-sentence cost disclosure fragment for ``tool_name``, price
    and rule only, per this module's CRITICAL CONSTRAINT. Used standalone
    by anything that wants just the cost sentence, and by
    ``annotate_tool_description`` to fold it into a tool's description.
    """
    quote = schedule.quote(tool_name)
    if quote is None:
        return "Cost: no published sim-currency charge for this action."
    return f"Cost: {format_sim(quote.sim_cost)} ({quote.basis})."


def annotate_tool_description(
    base_description: str, tool_name: str, schedule: ActionCostScheduleLike
) -> str:
    """``base_description`` with ``describe_cost(tool_name, schedule)``
    appended as its own sentence. The base text is preserved verbatim,
    this only ever appends, so a caller diffing tool descriptions before
    and after wiring this in sees exactly one new sentence, nothing
    reworded.
    """
    stripped = base_description.rstrip()
    if not stripped.endswith((".", "!", "?")):
        stripped += "."
    return f"{stripped} {describe_cost(tool_name, schedule)}"


def annotate_tool_descriptions(
    base_descriptions: Mapping[str, str], schedule: ActionCostScheduleLike
) -> dict[str, str]:
    """Bulk form of ``annotate_tool_description``, one call per name in
    ``base_descriptions`` (e.g. ``{name: spec.description for name, spec
    in registry.tools.items()}``). Returns a fresh dict, name -> new
    description; it does not mutate anything, ``ToolSpec`` is frozen and
    this module does not own the registry that would apply the result
    (see module docstring's "Wiring this in").
    """
    return {
        name: annotate_tool_description(description, name, schedule)
        for name, description in base_descriptions.items()
    }


# ---------------------------------------------------------------------------
# 2. Quotation: what would THIS call cost, before committing.
# ---------------------------------------------------------------------------


# The engine publishes every world tool under a namespace prefix
# (engine/src/sim/world_tools.py's WORLD_TOOL_NAME_PREFIX is "world:", and
# the tool-name sanitiser the SDK applies downstream turns the colon into
# an underscore), so the name an agent reads in its own tool list is
# "world:mine" or "world_mine" while the registry, the schedule and the
# dispatch charge all use the bare "mine". A resident quoting the name it
# was shown is not making a mistake, and must not be answered as if the
# tool did not exist. ECON-17.
ENGINE_TOOL_NAME_PREFIXES: tuple[str, ...] = ("world:", "world_")


def normalise_tool_name(
    raw_tool_name: str, known_tool_names: Collection[str] | None = None
) -> str | None:
    """Resolve the name an agent typed to the name the registry and the
    cost schedule actually use, or ``None`` when no such tool exists.

    Three outcomes, deliberately distinct (ECON-17): the name is already
    a real tool (returned unchanged); the name is a real tool wearing one
    of ``ENGINE_TOOL_NAME_PREFIXES`` (returned bare); or the name matches
    nothing the caller knows about (``None``, "no such tool"). The bare
    name is only preferred over the literal one when the literal one is
    not itself registered, so a tool genuinely called ``world_x`` would
    still win over a tool called ``x``.

    ``known_tool_names`` empty or omitted means the caller cannot verify
    names at all (the pure function called with no world behind it). In
    that case this strips a known prefix and returns the result rather
    than claiming a tool does not exist on no evidence - an unverifiable
    name is not a refuted one.
    """
    candidate = raw_tool_name.strip()
    known = frozenset(known_tool_names) if known_tool_names else None

    if known is None:
        for prefix in ENGINE_TOOL_NAME_PREFIXES:
            if candidate.startswith(prefix) and len(candidate) > len(prefix):
                return candidate[len(prefix) :]
        return candidate

    if candidate in known:
        return candidate
    for prefix in ENGINE_TOOL_NAME_PREFIXES:
        if candidate.startswith(prefix):
            stripped = candidate[len(prefix) :]
            if stripped in known:
                return stripped
    return None


class ActionCostQuote(BaseModel):
    """What ``quote_action_cost`` hands back: a priced answer plus, when
    the caller supplied a current sim balance, whether the call is
    affordable right now.

    ``usd_equivalent_cents`` is informational only, floor-divided at the
    real exchange rate (``world.ledger.money.SIM_PER_USD_CENT``), and is
    NOT the same computation as ``world.ledger.money.sim_to_usd_cents``:
    that function deliberately raises on a sim amount that is not an
    exact multiple of the rate (a real government-store exchange must be
    exact), which most small action costs are not. This field exists so
    an agent can see roughly what a price means in the currency that
    actually funds its thinking, never to be fed back into a real
    exchange call.

    ``tool_name`` is the RESOLVED name (the one the registry and the
    schedule use); ``requested_tool_name`` is what the caller actually
    asked about, which differs whenever the agent quoted the prefixed
    name its own tool list shows it. ``known_tool`` and ``priced``
    separate the two answers ECON-17 found were being reported
    identically: a tool that exists and carries no charge (``known_tool``
    true, ``priced`` false, ``sim_cost`` 0 and that zero is real), and a
    name that resolves to no tool at all (``known_tool`` false, in which
    case ``sim_cost`` is 0 only because there is nothing to price, and
    ``affordable`` stays ``None`` rather than claiming the call is free
    and therefore affordable).
    """

    tool_name: str
    requested_tool_name: str
    sim_cost: int
    usd_equivalent_cents: int
    basis: str
    known_tool: bool
    priced: bool
    sim_balance_before: int | None = None
    sim_balance_after: int | None = None
    affordable: bool | None = None
    summary: str


def quote_action_cost(
    schedule: ActionCostScheduleLike,
    tool_name: str,
    params: Mapping[str, Any] | None = None,
    *,
    current_sim_balance: int | None = None,
    known_tool_names: Collection[str] | None = None,
) -> ActionCostQuote:
    """The pure quotation logic: resolves ``tool_name``'s cost for
    ``params`` (the real call params, for a variable-cost tool) through
    ``schedule``, and, when ``current_sim_balance`` is given, whether
    that balance covers it.

    ``tool_name`` is first put through ``normalise_tool_name`` against
    ``known_tool_names``, so the engine-prefixed name an agent reads in
    its own tool list prices as the tool it names rather than falling off
    the end of the schedule (ECON-17). A name that resolves to no known
    tool is answered as no such tool, NOT as free: quoting zero for a
    misspelt or invented name is the exact failure the finding caught,
    because the dispatch charge does not agree with it.

    A tool that does exist but carries no published charge still quotes
    as free (``sim_cost=0``, ``priced=False``), not as an error, matching
    ``describe_cost``'s same reading of a ``None`` from the schedule.
    """
    requested = tool_name
    resolved_name = normalise_tool_name(tool_name, known_tool_names)

    if resolved_name is None:
        summary = (
            f"There is no tool named {requested!r} in this world, so no price "
            f"can be quoted for it. Ask again with the name exactly as it "
            f"appears in your tool list, without any prefix."
        )
        return ActionCostQuote(
            tool_name=requested,
            requested_tool_name=requested,
            sim_cost=0,
            usd_equivalent_cents=0,
            basis="no tool by that name is registered in this world",
            known_tool=False,
            priced=False,
            sim_balance_before=current_sim_balance,
            sim_balance_after=None,
            affordable=None,
            summary=summary,
        )

    resolved = schedule.quote(resolved_name, params)
    priced = resolved is not None
    sim_cost = resolved.sim_cost if resolved is not None else 0
    basis = resolved.basis if resolved is not None else "no published sim-currency charge for this action"
    usd_equivalent_cents = sim_cost // SIM_PER_USD_CENT

    sim_balance_after = None
    affordable = None
    if current_sim_balance is not None:
        sim_balance_after = current_sim_balance - sim_cost
        affordable = sim_balance_after >= 0

    summary = f"{resolved_name} costs {format_sim(sim_cost)} ({basis})."
    if resolved_name != requested:
        summary += f" (You asked about {requested!r}; that is this town's {resolved_name}.)"
    if sim_balance_after is not None:
        summary += f" That would leave a sim-currency balance of {format_sim(sim_balance_after)}."

    return ActionCostQuote(
        tool_name=resolved_name,
        requested_tool_name=requested,
        sim_cost=sim_cost,
        usd_equivalent_cents=usd_equivalent_cents,
        basis=basis,
        known_tool=True,
        priced=priced,
        sim_balance_before=current_sim_balance,
        sim_balance_after=sim_balance_after,
        affordable=affordable,
        summary=summary,
    )


class QuoteActionCostParams(BaseModel):
    tool_name: str
    call_params: dict[str, Any] = Field(default_factory=dict)


async def quote_action_cost_handler(
    world: WorldState, agent: AgentLifeRecord, params: QuoteActionCostParams
) -> ToolOutcome:
    """``ToolHandler`` for the registered ``quote_action_cost`` tool (see
    ``build_action_cost_quote_spec``). Reads ``world.action_costs``
    defensively via ``getattr`` because ``WorldState`` does not declare
    that field yet (module docstring's "Wiring this in"); raises
    ``ActionCostScheduleUnavailableError`` rather than a bare
    ``AttributeError`` so the failure is a named, documented one like
    every other sibling-system failure this registry already lets
    through unwrapped.

    A pure read: never mutates ``world``, so it publishes no events, the
    empty ``events`` list on the returned ``ToolOutcome`` is not an
    oversight.

    ECON-17: the name is resolved against the world's live tool
    vocabulary before it reaches the schedule, so the prefixed name the
    engine publishes prices as the tool it names and an unknown name is
    answered as unknown instead of free.
    """
    schedule = getattr(world, "action_costs", None)
    if schedule is None:
        raise ActionCostScheduleUnavailableError()

    current_sim_balance = world.ledger.sim_balance(agent.agent_id)
    quote = quote_action_cost(
        schedule,
        params.tool_name,
        params.call_params,
        current_sim_balance=current_sim_balance,
        known_tool_names=world_tool_vocabulary(world),
    )
    return ToolOutcome(result=quote.model_dump())


def world_tool_vocabulary(world: WorldState) -> frozenset[str]:
    """Every tool name this world will actually dispatch, for
    ``normalise_tool_name`` to check a quoted name against.

    Reads the live registry (``WorldState.tool_registry``, set by
    ``bootstrap.build_world``) rather than only the frozen
    ``registry_tool_names`` snapshot, because that snapshot is taken
    before ``quote_action_cost`` itself is registered and before any tool
    a resident invents at runtime exists - quoting those as "no such
    tool" would be a new lie in place of the old one. The snapshot is
    unioned in rather than replaced so a world assembled by hand with
    only the names, and no registry, still verifies.
    """
    registry = world.tool_registry
    live: frozenset[str] = frozenset(registry.tools) if registry is not None else frozenset()
    return live | world.registry_tool_names


def build_action_cost_quote_spec() -> ToolSpec:
    """The ready-to-register ``ToolSpec`` for ``quote_action_cost``. Not a
    world-physics primitive (it mutates nothing), so ``is_primitive`` is
    left ``False``, matching every tool ``world.tools.meta`` itself
    registers for the identical reason: it must never become composable
    into an invented tool via the meta-tool's "existing primitives only"
    whitelist (``registry.py``'s ``ToolSpec.is_primitive`` docstring).
    Callable from anywhere, by anyone alive and not dormant, no location
    gate, no license: asking what something costs is not itself an
    action DESIGN.md's cost schedule prices.
    """
    return ToolSpec(
        name="quote_action_cost",
        description=(
            "Ask what a specific tool call would cost in sim-currency before "
            "committing to it. Give the tool's name and, for a variable-cost "
            "tool (one whose price depends on distance, value, or size), the "
            "same params you would call it with, otherwise you get back the "
            "tool's published base rate rather than a total for a specific call."
        ),
        params_model=QuoteActionCostParams,
        handler=quote_action_cost_handler,
        namespace=ToolNamespace.PUBLIC,
        location_gate=None,
        actor_requirements=ActorRequirements(),
        is_primitive=False,
    )


# ---------------------------------------------------------------------------
# 3. The per-tick economic context block.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WalletAndBurn:
    """What ``gather_wallet_and_burn`` reads off a real ``Ledger``:
    internal-only data (app/CONVENTIONS.md: "internal-only data can be a
    plain dataclass"), consumed by ``build_economic_context`` in this
    same module.
    """

    wallet_owner_agent_id: str
    usd_wallet_cents: int
    sim_balance: int
    recent_burn_usd_cents: int
    burn_window_ticks: int


# ---------------------------------------------------------------------------
# ECON-28: the recent-burn window, without re-reading the whole ledger.
#
# `gather_wallet_and_burn` runs once per agent per tick (run_genesis's
# `wallet_line`, on the volatile line every resident reads every turn).
# Summing it by filtering `ledger.transactions` made that O(agents x
# transactions) per tick against a log that only ever grows: at 250
# residents and 144 ticks a game-day the scan gets more expensive with
# every hour the town has already lived, which is exactly backwards for a
# long run.
#
# The finding's own suggestion was a per-agent ring buffer fed from
# `economy.wallet.debited` events in the runner. This is the same idea
# without needing the runner: the index below is built here, from the
# ledger this module is already handed, and advanced incrementally - each
# call walks only the transactions appended since the last one. A ring
# buffer would also have needed a length chosen in advance (too short and
# a long burn window reads short, silently); a per-agent prefix sum has no
# such horizon and answers any window in a binary search.
#
# Correctness against a log that is NOT purely append-only: `run_genesis`
# replaces `ledger.transactions` wholesale when it restores a saved run,
# and a rolled-back tool call can shorten it. `_LedgerBurnIndex.sync`
# therefore re-derives from scratch whenever the list object changed, or
# is shorter than what was consumed, or no longer holds the same
# Transaction object at the last consumed position. A stale index is a
# wrong number on a resident's own runway, so the cheap check runs every
# call rather than being trusted to a caller.
# ---------------------------------------------------------------------------


class _AgentBurnHistory:
    """One agent's INFERENCE_BURN history: tick-stamped amounts plus a
    running total, so any inclusive tick window is two binary searches
    and a subtraction.

    ``monotonic`` records whether ticks arrived in non-decreasing order.
    They do in a running simulation (the ledger is appended to as the
    clock advances), but a test or an imported trace can append out of
    order, and a binary search over an unsorted list would silently
    answer a wrong number. When that happens this walks the agent's own
    rows instead - still bounded by one agent's burns, never by the whole
    town's transaction log.
    """

    __slots__ = ("ticks", "running_total", "monotonic")

    def __init__(self) -> None:
        self.ticks: list[int] = []
        self.running_total: list[int] = []
        self.monotonic = True

    def append(self, tick: int, amount: int) -> None:
        if self.ticks and tick < self.ticks[-1]:
            self.monotonic = False
        previous = self.running_total[-1] if self.running_total else 0
        self.ticks.append(tick)
        self.running_total.append(previous + amount)

    def total_between(self, since_tick: int, tick: int) -> int:
        if not self.ticks:
            return 0
        if not self.monotonic:
            total = 0
            previous = 0
            for index, stamped_tick in enumerate(self.ticks):
                amount = self.running_total[index] - previous
                previous = self.running_total[index]
                if since_tick <= stamped_tick <= tick:
                    total += amount
            return total
        low = bisect.bisect_left(self.ticks, since_tick)
        high = bisect.bisect_right(self.ticks, tick)
        if high <= low:
            return 0
        return self.running_total[high - 1] - (self.running_total[low - 1] if low else 0)


class _LedgerBurnIndex:
    """The per-ledger index: agent id -> ``_AgentBurnHistory``, advanced
    incrementally from ``ledger.transactions`` and re-derived whenever
    that log is not the same log, in the same state, it was left in.
    """

    __slots__ = ("_log", "_consumed", "_last_row", "_by_agent")

    def __init__(self) -> None:
        self._log: list[Any] | None = None
        self._consumed = 0
        self._last_row: Any = None
        self._by_agent: dict[str, _AgentBurnHistory] = {}

    def _is_stale(self, log: list[Any]) -> bool:
        if log is not self._log:
            return True
        if self._consumed > len(log):
            return True
        return self._consumed > 0 and log[self._consumed - 1] is not self._last_row

    def sync(self, ledger: Ledger) -> None:
        log = ledger.transactions
        if self._is_stale(log):
            self._log = log
            self._consumed = 0
            self._last_row = None
            self._by_agent = {}
        # Indexed, not sliced: a slice would copy the tail, and the cost of
        # this loop is the point of the whole mechanism.
        for index in range(self._consumed, len(log)):
            transaction = log[index]
            if (
                transaction.currency is Currency.USD
                and transaction.reason is TransactionReason.INFERENCE_BURN
            ):
                attributed_to = transaction.on_behalf_of or transaction.from_account
                history = self._by_agent.get(attributed_to)
                if history is None:
                    history = _AgentBurnHistory()
                    self._by_agent[attributed_to] = history
                history.append(transaction.tick, transaction.amount)
        self._consumed = len(log)
        self._last_row = log[len(log) - 1] if log else None

    def burn_between(self, agent_id: str, since_tick: int, tick: int) -> int:
        history = self._by_agent.get(agent_id)
        if history is None:
            return 0
        return history.total_between(since_tick, tick)


# Weakly keyed so a finished run's ledger, and its index, are collected
# together: nothing here keeps a Ledger alive, and a second Ledger in the
# same process (every test builds its own) gets its own index rather than
# reading another run's burns.
_BURN_INDEXES: "weakref.WeakKeyDictionary[Ledger, _LedgerBurnIndex]" = weakref.WeakKeyDictionary()


def burn_index_for(ledger: Ledger) -> _LedgerBurnIndex:
    """``ledger``'s INFERENCE_BURN index, brought up to date with every
    transaction appended since the last call and created on first use.
    Callers only read from it; it owns its own freshness.
    """
    index = _BURN_INDEXES.get(ledger)
    if index is None:
        index = _LedgerBurnIndex()
        _BURN_INDEXES[ledger] = index
    index.sync(ledger)
    return index


def gather_wallet_and_burn(
    ledger: Ledger,
    agent_id: str,
    tick: int,
    burn_window_ticks: int = DEFAULT_BURN_WINDOW_TICKS,
) -> WalletAndBurn:
    """Reads ``agent_id``'s current wallet/sim balances and its recent
    inference-burn history straight off a real ``Ledger``, no mock, no
    second copy of the numbers. ``wallet_owner_agent_id`` is whichever
    account actually pays for this agent's thinking, resolved through
    ``Ledger.resolve_usd_payer`` (DESIGN.md section 4: a minor's calls
    are billed to its parent), so the USD figure returned is always the
    one whose depletion would actually stop this agent's cognition, self
    for an adult, parent for a minor.

    ``recent_burn_usd_cents`` sums every ``INFERENCE_BURN`` USD
    transaction attributed to ``agent_id`` (``on_behalf_of`` when set,
    which is exactly the minor case, otherwise ``from_account``) within
    ``[tick - burn_window_ticks + 1, tick]`` inclusive, matching
    ``world.core.perception.PerceptionSystem``'s own inclusive
    since-tick window convention.

    ECON-28: that sum is read out of ``burn_index_for`` rather than by
    re-scanning the whole transaction log, so the per-agent per-tick cost
    is the number of NEW transactions since the last call plus a binary
    search, not the length of the log.
    """
    if burn_window_ticks <= 0:
        raise ValueError(f"burn_window_ticks must be positive, got {burn_window_ticks}")

    payer_id = ledger.resolve_usd_payer(agent_id)
    usd_wallet_cents = ledger.usd_balance(payer_id)
    sim_balance = ledger.sim_balance(agent_id)

    since_tick = max(0, tick - burn_window_ticks + 1)
    recent_burn_usd_cents = burn_index_for(ledger).burn_between(agent_id, since_tick, tick)

    return WalletAndBurn(
        wallet_owner_agent_id=payer_id,
        usd_wallet_cents=usd_wallet_cents,
        sim_balance=sim_balance,
        recent_burn_usd_cents=recent_burn_usd_cents,
        burn_window_ticks=burn_window_ticks,
    )


def estimate_ticks_until_dormancy(
    usd_wallet_cents: int, recent_burn_usd_cents: int, burn_window_ticks: int
) -> float | None:
    """Pure runway math: at the burn rate ``recent_burn_usd_cents``
    implies over ``burn_window_ticks``, how many more ticks does
    ``usd_wallet_cents`` fund. Returns ``None`` when there is no recent
    burn to project from (rate is 0, DESIGN.md gives no basis to predict
    a hypothetical future burn from a currently-idle agent, "unknown" is
    the honest answer, not "forever"). Returns ``0.0`` for an
    already-empty wallet with a nonzero rate, DESIGN.md section 3's
    dormancy trigger is exactly this state.
    """
    if recent_burn_usd_cents <= 0:
        return None
    if usd_wallet_cents <= 0:
        return 0.0
    burn_rate_cents_per_tick = recent_burn_usd_cents / burn_window_ticks
    return usd_wallet_cents / burn_rate_cents_per_tick


class HouseholdUpkeepView(BaseModel):
    """What ``render_economic_context_block`` needs about one agent's
    household economics: DESIGN.md section 3's "household's daily upkeep
    and who depends on it." Not backed by a real upkeep-accrual system as
    of this writing, see module docstring's "HOUSEHOLD UPKEEP" section:
    the caller supplies these numbers once that system exists, this
    module only renders them.
    """

    household_id: str
    daily_upkeep_sim: int
    dependent_agent_ids: tuple[str, ...] = ()
    # Run 28: how many people the bill above is for. Without it a
    # household's bill reached a resident's perception as a personal one.
    member_count: int = 1

    @property
    def dependent_count(self) -> int:
        return len(self.dependent_agent_ids)


class EconomicContext(BaseModel):
    """Everything one call to ``render_economic_context_block`` needs for
    one agent at one tick. Built by ``build_economic_context``, but a
    caller with its own data sources (a future test, a different
    upkeep/household system than the one assumed here) can always
    construct one directly, this class carries no dependency on
    ``Ledger`` or any other live object.
    """

    agent_id: str
    tick: int
    wallet_owner_agent_id: str
    usd_wallet_cents: int
    sim_balance: int
    recent_burn_usd_cents: int
    burn_window_ticks: int
    accrued_upkeep_today_sim: int = 0
    household: HouseholdUpkeepView | None = None


def build_economic_context(
    ledger: Ledger,
    agent_id: str,
    tick: int,
    *,
    accrued_upkeep_today_sim: int = 0,
    household: HouseholdUpkeepView | None = None,
    burn_window_ticks: int = DEFAULT_BURN_WINDOW_TICKS,
) -> EconomicContext:
    """Assembles one agent's ``EconomicContext`` for one tick: wallet and
    burn history read for real off ``ledger`` (``gather_wallet_and_burn``),
    upkeep/household figures taken as given (see module docstring, no
    real accrual system exists yet to read those from).
    """
    wallet_and_burn = gather_wallet_and_burn(ledger, agent_id, tick, burn_window_ticks)
    return EconomicContext(
        agent_id=agent_id,
        tick=tick,
        wallet_owner_agent_id=wallet_and_burn.wallet_owner_agent_id,
        usd_wallet_cents=wallet_and_burn.usd_wallet_cents,
        sim_balance=wallet_and_burn.sim_balance,
        recent_burn_usd_cents=wallet_and_burn.recent_burn_usd_cents,
        burn_window_ticks=wallet_and_burn.burn_window_ticks,
        accrued_upkeep_today_sim=accrued_upkeep_today_sim,
        household=household,
    )


def render_economic_context_block(context: EconomicContext) -> str:
    """The literal per-tick text an agent sees: DESIGN.md section 3's
    "USD wallet, sim balance, its own recent burn rate, today's accrued
    upkeep, its household's daily upkeep and who depends on it, and how
    long it can survive at the current rate," in that order, prices and
    facts only. Mirrors ``world.life.soul_seed.build_soul_seed``'s plain
    declarative style so the two blocks (stable genesis law, dynamic
    per-tick numbers) read as one voice; unlike that block this one is
    never cached long, it changes every tick by construction (module
    docstring's "Wiring this in" on the ``SystemPromptBlock`` TTL).
    """
    # Energy, not dollars (world/ledger/money.py). Not wired into a
    # perception today; whoever wires it inherits the right unit.
    from world.ledger.money import format_energy

    lines = [f"YOUR ECONOMY RIGHT NOW (tick {context.tick})", ""]

    if context.wallet_owner_agent_id == context.agent_id:
        lines.append(
            f"Energy (what your thinking runs on): {format_energy(context.usd_wallet_cents)}."
        )
    else:
        lines.append(
            f"Your thinking draws on {context.wallet_owner_agent_id}'s energy "
            f"(a minor's thinking is paid for by a parent): "
            f"{format_energy(context.usd_wallet_cents)}."
        )
    lines.append(f"Sim-currency balance: {format_sim(context.sim_balance)}.")

    game_days_window = context.burn_window_ticks / TICKS_PER_DAY
    if context.recent_burn_usd_cents <= 0:
        lines.append(
            f"No inference spending recorded in the last {game_days_window:.2f} game-days; "
            "current burn rate is 0."
        )
    else:
        rate_per_day_cents = int(round(context.recent_burn_usd_cents / game_days_window))
        lines.append(
            f"Recent burn rate: {format_energy(rate_per_day_cents)} per game-day "
            f"(from {format_energy(context.recent_burn_usd_cents)} over the last "
            f"{game_days_window:.2f} game-days)."
        )
        ticks_left = estimate_ticks_until_dormancy(
            context.usd_wallet_cents, context.recent_burn_usd_cents, context.burn_window_ticks
        )
        if ticks_left is not None:
            days_left = ticks_left / TICKS_PER_DAY
            lines.append(
                f"At that rate, your energy runs out in about {ticks_left:.0f} ticks "
                f"(~{days_left:.1f} game-days) unless that changes."
            )

    lines.append(f"Upkeep accrued so far today: {format_sim(context.accrued_upkeep_today_sim)}.")

    if context.household is None:
        lines.append("No household upkeep is charged to you right now.")
    else:
        household = context.household
        lines.append(
            f"Household {household.household_id} daily upkeep: {format_sim(household.daily_upkeep_sim)}."
        )
        if household.dependent_agent_ids:
            lines.append(
                f"{household.dependent_count} dependent(s) rely on this household's upkeep: "
                f"{', '.join(household.dependent_agent_ids)}."
            )
        else:
            lines.append("No dependents currently rely on this household.")

    return "\n".join(lines)
