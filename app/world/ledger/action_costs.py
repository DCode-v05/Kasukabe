"""ActionCostSchedule: DESIGN.md section 3, "Everything costs: living is
not free" - "Acting costs sim-currency per tool call, on a published
schedule: labour consumes materials and wears tools; trade carries a
transaction cost; conversation and socialising cost time that could have
been earning; travel costs by distance; ceremonies, petitions, licences
and registrations carry fees."

Today only thinking costs money (broker's USD-per-token metering,
world.ledger.core.Ledger.burn_for_inference). This module is the other
half: ONE declarative table mapping every registered tool name to a sim-
currency price, so an agent that keeps moving without ever earning still
runs out (DESIGN.md's "survival of the fittest" law) exactly the way an
agent that keeps thinking without earning already does.

Why a table, not code in each handler (the operator's explicit
instruction): world/tools/primitives/*.py's handlers already show what
"scattered" looks like today - register_business_handler (civic.py)
calls Ledger.register_business with a hardcoded fee, marry_handler
(social.py) charges a temple fee buried inside world.life.household.marry,
define_tool_handler (meta.py) reads a dynamic charter fee and moves it
itself. Each of those is a real, working charge, but there is no single
place an agent (or a researcher, or this file's own tests) can read to
answer "what does every tool cost" - the answer is smeared across nine
handler modules in nine different shapes. SCHEDULE_TABLE below is that
single place: one dict, one row shape (ActionCostRule), covering every
tool name world/tools/bootstrap.py's build_world() registers. Publishing
it (ActionCostSchedule.describe()) and querying it (cost_for()) are both
O(1) dict lookups precisely because the table is the only source of
truth - DESIGN.md's "the cost schedule is published in the tool
descriptions they read ... they can query what any action will cost
before taking it" is this table, read twice.

Deliberate non-goal: this module never touches world/tools/registry.py
or any primitives/*.py handler. It owns exactly one new file (this one)
plus its own tests - see the "Integration point" section below for the
one call another agent must add, and "Known double-charge risk" for the
three tools whose handlers already move money outside this schedule.

-- A second, already-built consumer: world/tools/cost_disclosure.py -----

While this module was being written, a concurrent task built
world/tools/cost_disclosure.py (DESIGN.md section 3: "the cost schedule
is published in the tool descriptions they read ... they can query what
any action will cost before taking it"). That file could not import this
one yet (it did not exist), so it coded against an explicit, documented
assumption instead - its own module docstring's "ASSUMED INTERFACE"
section names this exact module and a method called `quote(tool_name,
params=None)` returning an object with `sim_cost: int` and `basis: str`,
or `None` for a tool with no published entry, and says plainly: "if the
real ActionCostSchedule ends up shaped differently ... the fix is a small
adapter". ActionCostSchedule.quote() below is that method, shaped exactly
to their assumption, so no adapter is needed - see
world.ledger.tests.test_ledger_action_costs's
test_quote_interoperates_with_the_real_cost_disclosure_module for a test
that imports cost_disclosure.py's own pure functions and calls them
against a real instance of this module's schedule, not a mock of either
side.

`quote()` is deliberately a second method, not a rename of `cost_for()`
now that both exist: `cost_for()`/`charge()` are how the registry
integration point (above) prices and moves money for a call that is
actually about to happen, and always resolve every rule to a concrete
number, falling back to DEFAULT_RULE so nothing is ever silently free
(see DEFAULT_RULE's own docstring). `quote()` is a read-only disclosure
surface for an agent (or a tool description at registration time) asking
"what would this cost" before or without committing to a call, and
answers None, honestly, for a tool_name this schedule has no row for at
all - a real distinction cost_disclosure.py's own docstring draws
("uncosted, e.g. a pure-read tool") that DEFAULT_RULE's "never silently
free" guarantee would otherwise erase.

-- Pure by construction, not by convention --------------------------------

cost_for() (module-level convenience wrapping ActionCostSchedule.cost_for)
takes exactly (tool_name, params, actor) and returns an int, with no
WorldState, no Ledger, no I/O, no randomness, no wall-clock read. This is
deliberate, not an oversight of "the caller probably has a WorldState
handy": the whole point of a declarative schedule is that its price for
a given call is a pure function of that call's own facts, so it can be
unit-tested with plain dicts (see this package's tests/test_ledger_
action_costs.py) and so a future researcher can recompute "what would
this have cost" from a logged Transaction's params without reconstructing
a live world. ActionActor (below) is the narrow slice of "facts about who
is calling" this module needs; it is NOT AgentLifeRecord or WorldState,
specifically so this file never imports world.tools.state or
world.life.models and creates an import cycle back into the very package
that will call it.

-- Integration point (for whoever wires world/tools/registry.py) ---------

world/tools/registry.py's ToolRegistry.dispatch() (DO NOT EDIT per this
task's file-ownership list - this is a report, not a patch) runs, in
order: actor exists -> visible -> actor requirements -> params validate
-> location gate -> [[ HOOK HERE ]] -> `outcome = await spec.handler(...)`
-> ToolInvocationRecord appended. The hook belongs immediately before the
`spec.handler` call, once `params` is a validated BaseModel and `agent`
has been resolved via `world.require_agent(actor_id)`, i.e. right between:

    agent = world.require_agent(actor_id)
    outcome = await spec.handler(world, agent, params)   # <- charge before this line

Concretely, the added line(s) would look like:

    from world.ledger.action_costs import ActionActor, ActionUnaffordableError, DEFAULT_SCHEDULE

    actor_ctx = ActionActor(
        actor_id=actor_id,
        skill_proficiency=world.skills.get(actor_id, {}).get(SKILL_FOR_TOOL.get(tool_name, ""), 0),
        distance_tiles=resolve_move_distance(world, actor_id, params) if tool_name == "move" else 0,
    )
    await DEFAULT_SCHEDULE.charge(world.ledger, tool_name, params, actor_ctx, world.tick())

`ActionUnaffordableError` (this module) should be allowed to propagate
uncaught, exactly like `ToolValidationError`/`LocationGateError` already
do from earlier gates in the same method - `ToolRegistry.execute()`'s
`except Exception: world.restore(snapshot); raise` already covers it with
no further change, so a refused-for-cost call rolls back cleanly and
never reaches the handler.

Why dispatch(), not execute(): dispatch() is also the method a composite
(define_tool-invented) tool's handler calls once per step (meta.py's
build_composite_handler, under the SAME lock/snapshot the outer execute()
holds - see registry.py's own docstring). Hooking here charges each
primitive step of an invented tool at that primitive's real schedule
rate (harvest, craft, ...) plus the invented tool name's own top-level
entry (SCHEDULE_TABLE has no way to know a not-yet-invented tool's name
in advance, so it falls to DEFAULT_RULE - see that constant's docstring).
This mirrors exactly how ToolInvocationRecord is already logged at this
same dispatch() granularity for the same reason (registry.py: "a
business's secret composite tool's individual primitive steps are each
recorded"), so cost attribution and invocation logging stay in lockstep
at the same call depth - a court subpoena and a cost audit see the same
shape of history.

`SKILL_FOR_TOOL` and `resolve_move_distance` above are sketched, not
provided: this module's `ActionActor.skill_proficiency` and
`.distance_tiles` are caller-resolved by design (see ActionActor's own
docstring) - registry.py's integrator decides which world.skills key
maps to which tool (there is no such mapping anywhere in the codebase
today) and how to turn a `move` call's destination_location_id into a
tile distance (world.core.movement.MovementSystem/estimate_travel_ticks,
already used by move_handler itself in world/tools/primitives/movement.py,
is the natural source). Leaving both at their defaults (0 skill, 0
distance) is always safe: cost_for() still charges every tool's
base_cost, it just skips the mastery discount and undercounts a `move`
call down to its flat minimum instead of the full per-tile charge - never
a crash, never a free ride, just a coarser price until that wiring lands.

-- Known double-charge risk (for whoever does that wiring) ---------------

Three tools already move sim-currency directly inside their own handler,
outside this schedule entirely:

  - `register_business` (civic.py): BUSINESS_REGISTRATION_FEE_SIM (5,000)
    via Ledger.register_business. SCHEDULE_TABLE's entry is 0 on purpose
    - DESIGN.md section 3's "fees: ... business/organization registration"
    is already satisfied by that call; a nonzero schedule entry here would
    charge the SAME registration fee twice.
  - `define_tool` (meta.py): `charter.patent_filing_fee_sim` (a dynamic,
    charter-governed amount - DESIGN.md section 7, amendable by 3/5 vote)
    via a direct Ledger.move() in the handler. SCHEDULE_TABLE's entry is
    0 for the same reason - DESIGN.md's "fees: ... patent office" is
    already this charge, and this schedule cannot second-guess a live
    political rate with a hardcoded one anyway.
  - `marry` (social.py, via world.life.household.marry's EconomyPort): a
    temple ceremony fee. SCHEDULE_TABLE's `marry` entry is deliberately
    KEPT nonzero here, unlike the two above - it prices a different thing
    (DESIGN.md: "conversation and socialising cost time that could have
    been earning"), not the ceremony fee itself. A wedding day costs both
    the venue AND the time off; this schedule only ever adds the second
    one.

`register_org` has NO existing embedded charge (charter.organizations.
register_organization never touches the ledger) despite DESIGN.md listing
"business/organization registration" together under fees - this schedule
is the ONLY charge for it, and is priced accordingly (non-token, see
SCHEDULE_TABLE).

-- Numbers ------------------------------------------------------------

DESIGN.md pins the categories, never the rates ("on a published
schedule" with no numbers given, same as BUSINESS_REGISTRATION_FEE_SIM in
civic.py or TIMBER_PER_TILE in construction.py before it). Every constant
below is an ASSUMPTION in that same sense: internally consistent with the
existing scale this codebase already committed to (business registration
5,000 sim, baby blessing 5,000 sim, patent filing fee charter-governed
starting in the low thousands) rather than picked in isolation.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from world.ledger.core import Ledger
from world.ledger.models import Currency, Transaction, TransactionReason
from world.ledger.money import TREASURY_ACCOUNT_ID

BPS_DENOMINATOR = 10_000

SKILL_DISCOUNT_PROFICIENCY_CAP = 100
SKILL_DISCOUNT_MAX_BPS = 5_000

ACTION_COST_MEMO_PREFIX = "action_cost:"
REFUND_MEMO_PREFIX = "action-refund:"


class ActionCostCategory(str, enum.Enum):
    """DESIGN.md section 3's five cost categories, verbatim."""

    LABOUR = "labour"
    TRADE = "trade"
    SOCIAL = "social"
    TRAVEL = "travel"
    FEE = "fee"


@dataclass(frozen=True)
class ActionActor:
    """The narrow slice of "who is calling" cost_for() needs, deliberately
    NOT AgentLifeRecord or WorldState - see the module docstring's "Pure
    by construction" section for why. A caller with nothing more specific
    than an agent id can pass `ActionActor(actor_id)` and every rule still
    prices correctly, just without the skill discount or a distance-aware
    travel charge - both fields are opt-in refinements, never required.
    """

    actor_id: str
    skill_proficiency: int = 0
    distance_tiles: int = 0
    holds_a_tool: bool = False
    trade_value_sim: int | None = None


@dataclass(frozen=True)
class ActionCostRule:
    """One SCHEDULE_TABLE row: how to price one tool.

    cost = max(min_cost, discount(base_cost + per_unit_cost * quantity
                                   + value * value_bps // BPS_DENOMINATOR))

    quantity and value are both extracted from the call's own params (or,
    for `move`/`build_structure`, from params/actor - see extract_
    quantity()) - a rule never carries logic of its own, only the rates
    those two pure extractors get multiplied by. Most rules use exactly
    one of per_unit_cost/value_bps and leave the other at 0; both existing
    at once is legal (nothing forbids a tool priced on quantity AND
    value) but no current row needs it.
    """

    category: ActionCostCategory
    base_cost: int = 0
    per_unit_cost: int = 0
    value_bps: int = 0
    min_cost: int = 0
    skill_discount: bool = False
    description: str = ""


QUANTITY_FIELDS: dict[str, str] = {
    "harvest": "quantity",
    "mine": "quantity",
    "fell_timber": "quantity",
    "craft": "batches",
    "cook": "batches",
    "eat": "quantity",
    "give": "quantity",
    "store_item": "quantity",
    "withdraw_item": "quantity",
    "stock_shelf": "quantity",
    "take_from_shelf": "quantity",
    "steal": "quantity",
}


def normalize_params(params: Mapping[str, Any] | BaseModel) -> Mapping[str, Any]:
    """Accepts either a tool's already-validated pydantic params model
    (what world/tools/registry.py has in hand at the integration point -
    see module docstring) or a plain dict (what this module's own tests,
    and any caller without a live params model, pass instead). Normalizing
    once here is what lets cost_for() stay agnostic about which shape it
    was handed.
    """
    if isinstance(params, BaseModel):
        return params.model_dump()
    return params


def extract_quantity(tool_name: str, params: Mapping[str, Any], actor: ActionActor) -> int:
    """"How many physical units" a call did, for a per_unit_cost rule.
    Never raises: a malformed or missing field degrades to a safe default
    (1 for a fixed field, 0 for a resolver) rather than blocking a charge
    on a params-shape problem this module does not own.
    """
    if tool_name == "move":
        return max(0, actor.distance_tiles)
    if tool_name == "build_structure":
        footprint = params.get("footprint") or []
        return max(1, len(footprint))
    field_name = QUANTITY_FIELDS.get(tool_name)
    if field_name is None:
        return 1
    raw = params.get(field_name, 1)
    try:
        quantity = int(raw)
    except (TypeError, ValueError):
        quantity = 1
    return max(quantity, 0)


def extract_trade_value(tool_name: str, params: Mapping[str, Any]) -> int:
    """The sim-currency value a TRADE-category call moves, for a
    value_bps rule. `buy`/`sell` carry unit_price_sim and quantity
    separately (their total is never a single params field); `transfer`
    carries the moved amount directly. Every other tool has no notion of
    "value" and gets 0, which is correct: their rules use per_unit_cost,
    not value_bps, so this return is never multiplied against anything.
    """
    try:
        if tool_name in ("buy", "sell"):
            total = params.get("total_price_sim")
            if total is not None:
                return max(0, int(total))
            unit = params.get("unit_price_sim")
            if unit is not None:
                return max(0, int(unit) * int(params.get("quantity", 0)))
            return 0
        if tool_name == "transfer":
            return max(0, int(params.get("amount_sim", 0)))
    except (TypeError, ValueError):
        return 0
    return 0


def posted_trade_value(world: Any, tool_name: str, params: Any) -> int | None:
    """What a `buy`/`sell` call will actually move, read off the price board.

    The registry resolves this before charging, exactly as it resolves
    `proficiency_for` - this module stays pure and the caller supplies the
    one fact only the world holds. Returns None for anything that is not a
    posted trade, so `ActionActor` records "not resolved" rather than a
    misleading zero.
    """
    if tool_name not in ("buy", "sell"):
        return None
    fields = normalize_params(params)
    holder_id = fields.get("seller_id") if tool_name == "buy" else fields.get("buyer_id")
    item = fields.get("item")
    if not holder_id or not item:
        return None
    post = (world.price_board.get(holder_id) or {}).get(item)
    if post is None:
        return None
    price = post.ask_sim if tool_name == "buy" else post.bid_sim
    if price is None:
        return None
    return max(0, int(price) * int(fields.get("quantity", 0)))


SKILL_FOR_TOOL: dict[str, str] = {
    "harvest": "farming",
    "fell_timber": "forestry",
    "mine": "mining",
    "craft": "artisanry",
    "cook": "cooking",
    "build_structure": "masonry",
    "work_site": "masonry",
}

REPS_TO_MASTERY = 60


def proficiency_from_reps(reps: int) -> int:
    """Repetitions -> proficiency, on a square root rather than a line.

    Linear gives no feedback for days and then arrives all at once. Square root
    pays the first visible discount after four uses (26 proficiency, 13% off)
    while still taking a game-week to reach the cap:

        4 reps -> 26     15 -> 50     34 -> 75     60 -> 100
    """
    if reps <= 0:
        return 0
    ratio = min(1.0, reps / REPS_TO_MASTERY)
    return min(SKILL_DISCOUNT_PROFICIENCY_CAP,
               round(SKILL_DISCOUNT_PROFICIENCY_CAP * (ratio ** 0.5)))


def skills_for_tool(world: Any, tool_name: str) -> list[str]:
    """Every skill that discounts `tool_name`: the built-in trade, plus any
    agent-authored skill whose steps name it (Part B populates
    `world.skill_tools`; until then this is just the seeded map)."""
    found: list[str] = []
    seeded = SKILL_FOR_TOOL.get(tool_name)
    if seeded:
        found.append(seeded)
    for skill, tools in getattr(world, "skill_tools", {}).items():
        if tool_name in tools and skill not in found:
            found.append(skill)
    return found


def proficiency_for(world: Any, actor_id: str, tool_name: str) -> int:
    """The discount this actor gets on this tool.

    HIGHEST WINS when several skills name the same tool. Not only because
    stacking would blow the 50% cap and make SKILL_DISCOUNT_MAX_BPS
    meaningless - the better reason is behavioural: a second overlapping skill
    is then worth exactly nothing, so an agent who wants more advantage has to
    broaden rather than file redundant variants of what it already holds.
    """
    held = world.skills.get(actor_id, {})
    return max((held.get(s, 0) for s in skills_for_tool(world, tool_name)), default=0)


def apply_skill_discount(cost: int, skill_proficiency: int) -> int:
    """Linear discount, 0% at proficiency 0 up to SKILL_DISCOUNT_MAX_BPS
    at SKILL_DISCOUNT_PROFICIENCY_CAP and beyond (clamped, see that
    constant's docstring). Pure integer arithmetic, floor-divided like
    every other money computation in this package.
    """
    capped = min(max(skill_proficiency, 0), SKILL_DISCOUNT_PROFICIENCY_CAP)
    discount_bps = (capped * SKILL_DISCOUNT_MAX_BPS) // SKILL_DISCOUNT_PROFICIENCY_CAP
    return cost - (cost * discount_bps) // BPS_DENOMINATOR


def compute_cost(rule: ActionCostRule, tool_name: str, params: Mapping[str, Any], actor: ActionActor) -> int:
    """The one arithmetic path every rule goes through - see
    ActionCostRule's docstring for the formula this implements."""
    quantity = extract_quantity(tool_name, params, actor)
    value = (
        actor.trade_value_sim
        if actor.trade_value_sim is not None
        else extract_trade_value(tool_name, params)
    )
    raw = rule.base_cost + rule.per_unit_cost * quantity + (value * rule.value_bps) // BPS_DENOMINATOR
    if rule.skill_discount and actor.skill_proficiency > 0:
        raw = apply_skill_discount(raw, actor.skill_proficiency)
    if rule.skill_discount and actor.holds_a_tool:
        raw = raw * TOOL_HELD_FEE_BPS // 10_000
    return max(raw, rule.min_cost)


TOOL_HELD_FEE_BPS = 6_000

SCHEDULE_TABLE: dict[str, ActionCostRule] = {
    "harvest": ActionCostRule(
        category=ActionCostCategory.LABOUR, base_cost=2, per_unit_cost=3, min_cost=2,
        skill_discount=True, description="tool wear per unit of crop harvested",
    ),
    "mine": ActionCostRule(
        category=ActionCostCategory.LABOUR, base_cost=3, per_unit_cost=5, min_cost=3,
        skill_discount=True, description="tool wear per unit of ore mined (harder than farmland)",
    ),
    "fell_timber": ActionCostRule(
        category=ActionCostCategory.LABOUR, base_cost=2, per_unit_cost=4, min_cost=2,
        skill_discount=True, description="tool wear per unit of timber felled",
    ),
    "craft": ActionCostRule(
        category=ActionCostCategory.LABOUR, base_cost=6, per_unit_cost=8, min_cost=6,
        skill_discount=True, description="tool wear per batch converted at a workshop",
    ),
    "cook": ActionCostRule(
        category=ActionCostCategory.LABOUR, base_cost=5, per_unit_cost=6, min_cost=5,
        skill_discount=True, description="tool wear per batch cooked",
    ),
    "build_structure": ActionCostRule(
        category=ActionCostCategory.LABOUR, base_cost=25, per_unit_cost=15, min_cost=25,
        skill_discount=True,
        description="tool wear per footprint tile, on top of the timber/metal the handler already debits",
    ),
    "admit_to_org": ActionCostRule(
        category=ActionCostCategory.LABOUR, base_cost=2, min_cost=2,
        description="the paperwork of admitting a member",
    ),
    "leave_org": ActionCostRule(
        category=ActionCostCategory.LABOUR, base_cost=0, min_cost=0,
        description="leaving costs nothing; a body you cannot afford to "
                    "leave is not an association",
    ),
    "work_site": ActionCostRule(
        category=ActionCostCategory.LABOUR, base_cost=5, min_cost=5,
        skill_discount=True,
        description="tool wear for one tick of work on a building site",
    ),
    "steal": ActionCostRule(
        category=ActionCostCategory.LABOUR, base_cost=5, min_cost=5, skill_discount=False,
        description="physical effort of an attempt, independent of outcome; legal punishment is crime/'s job, not this schedule's",
    ),
    "buy": ActionCostRule(
        category=ActionCostCategory.TRADE, value_bps=100, min_cost=2,
        description="1% transaction cost on the total price paid",
    ),
    "sell": ActionCostRule(
        category=ActionCostCategory.TRADE, value_bps=100, min_cost=2,
        description="1% transaction cost on the total price received",
    ),
    "transfer": ActionCostRule(
        category=ActionCostCategory.TRADE, value_bps=50, min_cost=1,
        description="0.5% transaction cost on a direct peer-to-peer payment",
    ),
    "store_item": ActionCostRule(
        category=ActionCostCategory.TRADE, base_cost=1, per_unit_cost=1, min_cost=1,
        description="handling fee per item stashed",
    ),
    "withdraw_item": ActionCostRule(
        category=ActionCostCategory.TRADE, base_cost=1, per_unit_cost=1, min_cost=1,
        description="handling fee per item retrieved",
    ),
    "stock_shelf": ActionCostRule(
        category=ActionCostCategory.TRADE, base_cost=1, per_unit_cost=1, min_cost=1,
        description="handling fee per item shelved",
    ),
    "take_from_shelf": ActionCostRule(
        category=ActionCostCategory.TRADE, base_cost=1, per_unit_cost=1, min_cost=1,
        description="handling fee per item taken back off the shelf",
    ),
    "deposit_to_till": ActionCostRule(
        category=ActionCostCategory.TRADE, base_cost=1, min_cost=1,
        description="a coin to count the money in",
    ),
    "draw_from_till": ActionCostRule(
        category=ActionCostCategory.TRADE, base_cost=1, min_cost=1,
        description="a coin to count the money out",
    ),
    "find_people": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=1, min_cost=1,
        description="asking who does what costs a moment, not a morning",
    ),
    "describe_person": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=1, min_cost=1,
        description="looking one neighbour up costs a moment",
    ),
    "trade": ActionCostRule(
        category=ActionCostCategory.TRADE, base_cost=2, min_cost=2,
        description="striking a bargain costs the time it takes to strike",
    ),
    "eat": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="eating what you already hold costs nothing but the food",
    ),
    "give": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="handing something to somebody standing with you; the gift is the cost",
    ),
    "buy_treatment": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="the cure is the cost; the pharmacy charges its own fee",
    ),
    "rest": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=1, min_cost=1,
        description="a spell of standing still is a spell not spent earning",
    ),
    "say": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=1, min_cost=1,
        description="a moment's talk is a moment not spent earning",
    ),
    "start_conversation": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=2, min_cost=2,
        description=(
            "calling people together spends everyone's attention, not only yours"
        ),
    ),
    "keep_doing": ActionCostRule(
        category=ActionCostCategory.LABOUR, base_cost=0, min_cost=0,
        description="committing to a job costs nothing; each repetition pays its own way",
    ),
    "set_tax_rate": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - governing is what the seat is for",
    ),
    "set_price_control": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - governing is what the seat is for",
    ),
    "set_business_licensing": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - governing is what the seat is for",
    ),
    "issue_business_permit": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - the applicant's permit fee is the price, not the clerk's time",
    ),
    "issue_builder_license": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - the applicant's fee is the price, not the clerk's time",
    ),
    "grant_plot": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - the ground is the cost, and the ground is finite",
    ),
    "table_amendment": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - proposing a rule is what the seat is for",
    ),
    "vote_amendment": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - a vote that costs money is not a vote",
    ),
    "declare_candidacy": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - a poll tax on standing is a poll tax on standing",
    ),
    "convene": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=2, min_cost=2,
        description="calling people together spends everyone's attention, not only yours",
    ),
    "appoint_police": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - the salary is the cost",
    ),
    "arrest": ActionCostRule(
        category=ActionCostCategory.LABOUR, base_cost=2, min_cost=2,
        description="walking somebody to the cells is an hour nobody spent earning",
    ),
    "rule": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - the fine is the fine, and a verdict is not for sale",
    ),
    "release": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - letting somebody go costs nothing",
    ),
    "license_tool": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - the licence fee is the price",
    ),
    "revoke_license": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - ending something you granted costs nothing",
    ),
    "sell_tool": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - the sale price is the price",
    ),
    "teach_tool": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - the tuition is the price",
    ),
    "set_intention": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="free - a note to yourself costs nothing",
    ),
    "drop_intention": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="free - changing your mind costs nothing",
    ),
    "set_goal": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="free - wanting something costs nothing",
    ),
    "update_goal": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="free - saying how far you have got costs nothing",
    ),
    "retire_goal": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="free - giving up costs nothing but the goal",
    ),
    "recall_goals": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="free - remembering what you already know costs nothing",
    ),
    "note_belief": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="free - a resident charged to learn would stop learning",
    ),
    "set_wage": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - a promise costs nothing to make, which is the point",
    ),
    "pay_wage": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - the wage is the cost",
    ),
    "check_debts": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="free - asking what you owe costs nothing",
    ),
    "post_price": ActionCostRule(
        category=ActionCostCategory.TRADE, base_cost=1, min_cost=1,
        description="a sign costs a coin",
    ),
    "withdraw_price": ActionCostRule(
        category=ActionCostCategory.TRADE, base_cost=1, min_cost=1,
        description="taking the sign down costs the same coin as putting it up",
    ),
    "revive": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="the revival fee is the price; standing over them costs nothing more",
    ),
    "buy_energy": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="the exchange is the price; the counter charges nothing",
    ),
    "marry": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=30, min_cost=30,
        description="a wedding day is a day not spent earning, separate from the temple's own ceremony fee",
    ),
    "come_of_age": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=20, min_cost=20,
        description="settling where you live now you are eighteen; the temple fee for pairing off is separate",
    ),
    "ask_for_a_child": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=5, min_cost=5,
        description="putting your household's name down for a child; the blessing itself is not yours to grant",
    ),
    "pray": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=3, min_cost=3,
        description="time at the temple, separate from any voluntary donation",
    ),
    "teach_skill": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=15, min_cost=15,
        description="the teacher's time, separate from any voluntary tuition price_sim",
    ),
    "move": ActionCostRule(
        category=ActionCostCategory.TRAVEL, base_cost=1, per_unit_cost=2, min_cost=1,
        description="flat minimum plus a per-tile charge for the distance actually walked",
    ),
    "list_places": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="free: recalling which places exist costs energy to think, not money",
    ),
    "describe_place": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="free: learning what a place is for costs energy to think, not money",
    ),
    "look_around": ActionCostRule(
        category=ActionCostCategory.SOCIAL, base_cost=0, min_cost=0,
        description="free: observing where you already stand costs energy to think, not money",
    ),
    "vote": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=2, min_cost=2,
        description="casting a ballot at town hall",
    ),
    "petition": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=100, min_cost=100,
        description="filing a land-expansion petition to the President",
    ),
    "register_business": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="no separate fee - registering a business costs 5,000 sim on its own",
    ),
    "register_org": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=3_000, min_cost=3_000,
        description="the registration fee for founding an organization",
    ),
    "pay_tax": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free: paying the treasury what you owe it costs you the debt, not a fee",
    ),
    "file_complaint": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=10, min_cost=10,
        description="filing paperwork at the complaints desk",
    ),
    "hire_employee": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=10, min_cost=10,
        description="payroll/roster paperwork",
    ),
    "fire_employee": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=10, min_cost=10,
        description="payroll/roster paperwork",
    ),
    "transfer_tile": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=20, min_cost=20,
        description="deed transfer for one 1x1 land unit (sale or gift)",
    ),
    "auction_land": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=20, min_cost=20,
        description="government land auction paperwork; the sale proceeds go to the treasury",
    ),
    "set_will": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=15, min_cost=15,
        description="registering an owner's wishes for their estate (section 31.3)",
    ),
    "read_will": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=2, min_cost=2,
        description="looking a will up in the registry",
    ),
    "transfer_business": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=20, min_cost=20,
        description="ownership-transfer paperwork (sale or inheritance handoff)",
    ),
    "file_subpoena": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=25, min_cost=25,
        description="filing a court request for another agent's invocation records",
    ),
    "grant_subpoena": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=25, min_cost=25,
        description="a sitting official's paperwork to grant a subpoena",
    ),
    "deny_subpoena": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=10, min_cost=10,
        description="a sitting official's paperwork to deny a subpoena",
    ),
    "define_tool": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="no separate fee - the patent office charges its own filing fee",
    ),
    "approve_patent": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=15, min_cost=15,
        description="a minister's paperwork to approve a pending patent",
    ),
    "reject_patent": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=10, min_cost=10,
        description="a minister's paperwork to reject a pending patent",
    ),
    "revoke_patent": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=20, min_cost=20,
        description="a minister's paperwork to revoke a granted patent",
    ),
    "define_business_tool": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - a trade secret needs no filing",
    ),
    "define_organization_tool": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="0 by design, same reasoning as define_business_tool",
    ),
    "describe_primitives": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=1, min_cost=1,
        description="a token fee for listing what you can do",
    ),
    "quote_action_cost": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - asking a price costs nothing",
    ),
    "revise_skill": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - changing your mind about how a job is done",
    ),
    "revise_tool": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - each step still costs what it always did",
    ),
    "toolify": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description=(
            "free to compose; calling the tool you made costs the sum of "
            "its steps and nothing for the wrapper"
        ),
    ),
    "skillify": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free - what a workflow costs you is the thinking",
    ),
    "do_in_order": ActionCostRule(
        category=ActionCostCategory.FEE, base_cost=0, min_cost=0,
        description="free; each step costs what it costs on its own",
    ),
}

DEFAULT_RULE = ActionCostRule(
    category=ActionCostCategory.FEE, base_cost=5, min_cost=5,
    description="fallback for a tool name not yet in SCHEDULE_TABLE - never 0",
)


class ActionUnaffordableError(Exception):
    """The affordability refusal: an actor's sim balance cannot cover an
    action's cost. Raised BEFORE any money moves (ActionCostSchedule.
    charge() checks the balance itself before ever calling Ledger.move,
    see that method's docstring) - the action is refused, never partially
    charged, and the actor's balance is left exactly where it was. This is
    what DESIGN.md's "an agent that cannot fund its own cognition falls
    dormant" becomes for ACTING instead of thinking: refusal, not debt.
    """

    def __init__(self, tool_name: str, actor_id: str, cost: int, balance: int) -> None:
        self.tool_name = tool_name
        self.actor_id = actor_id
        self.cost = cost
        self.balance = balance
        self.shortfall = cost - balance
        super().__init__(
            f"{actor_id!r} cannot afford {tool_name!r}: costs {cost} sim, has {balance} sim "
            f"available (short {self.shortfall} sim). Action refused, balance unchanged."
        )


@dataclass(frozen=True)
class ActionCostQuote:
    """A read-only priced answer to "what would tool_name cost". Carries
    exactly the two fields world/tools/cost_disclosure.py's
    ActionCostQuoteLike Protocol needs (sim_cost, basis) - see module
    docstring's "A second, already-built consumer" section. This module
    never imports cost_disclosure.py (world/ledger/ is a lower-level
    package than world/tools/ - see app/CONVENTIONS.md's module
    boundaries), it only shapes this class to match structurally, exactly
    as that module's own docstring asks callers of a differently-shaped
    real schedule to do.
    """

    sim_cost: int
    basis: str


def reference_cost(rule: ActionCostRule) -> int:
    """The "published reference number" ActionCostSchedule.quote() returns
    for a tool_name with no real call params yet (registration-time
    annotation, before an agent has committed to a specific call) -
    cost_disclosure.py's own docstring: "the schedule's reference number
    (the per-unit rate ... e.g. travel's per-tile figure) ... not a guess
    at the eventual total." Precedence mirrors which rate actually varies
    for this rule: a value-proportional rule (buy/sell/transfer) has no
    single flat reference number, so its floor is the honest lower bound;
    a per-unit rule (harvest, move, ...) reports the per-unit rate itself,
    the same number DESIGN.md's own "10 sim per tile travelled" example
    describes; a purely flat rule reports its one real number outright,
    exact rather than a reference.
    """
    if rule.value_bps > 0:
        return rule.min_cost
    if rule.per_unit_cost > 0:
        return rule.per_unit_cost
    return rule.base_cost


def memo_for(tool_name: str) -> str:
    """The memo every action-cost Transaction carries - see module
    docstring's ACTION_COST_MEMO_PREFIX for why a memo prefix, not a
    dedicated TransactionReason.
    """
    return f"{ACTION_COST_MEMO_PREFIX}{tool_name}"


def tool_name_from_memo(transaction: Transaction) -> str | None:
    """The inverse of memo_for(): recovers the tool name an action-cost
    Transaction was attributed to, or None for a Transaction this module
    did not create (any other memo, including the empty default).
    """
    if not transaction.memo.startswith(ACTION_COST_MEMO_PREFIX):
        return None
    return transaction.memo[len(ACTION_COST_MEMO_PREFIX):]


@dataclass(frozen=True)
class ActionCostSchedule:
    """The published, queryable schedule DESIGN.md section 3 calls for.
    DEFAULT_SCHEDULE (below) is the one real instance every caller should
    use; the class itself takes rules as constructor arguments purely so
    this module's own tests can build a small, hand-picked schedule
    instead of asserting against the full 33-row production table for
    every single case.
    """

    rules: Mapping[str, ActionCostRule] = field(default_factory=lambda: dict(SCHEDULE_TABLE))
    default_rule: ActionCostRule = DEFAULT_RULE

    def rule_for(self, tool_name: str) -> ActionCostRule:
        return self.rules.get(tool_name, self.default_rule)

    def quote(
        self,
        tool_name: str,
        params: Mapping[str, Any] | BaseModel | None = None,
        actor: ActionActor | None = None,
    ) -> ActionCostQuote | None:
        """world/tools/cost_disclosure.py's assumed `quote(tool_name,
        params=None)` interface, satisfied for real - see module
        docstring's "A second, already-built consumer" section for the
        full contract this implements. `actor` is an addition beyond what
        that module's Protocol declares (it never passes one, always
        calling with one or two positional args - see its own
        quote_action_cost()); accepting it as an optional keyword-only-in-
        spirit third parameter costs that caller nothing while letting a
        future, actor-aware caller get a skill-discounted or
        distance-resolved quote through the exact same method, rather
        than a second one.

        Returns None only when tool_name has NO row in self.rules at all
        (cost_disclosure.py's "uncosted, e.g. a pure-read tool") - unlike
        cost_for()/charge(), this never falls back to default_rule, so a
        genuinely unpriced tool is disclosed as unpriced rather than
        quoted at a number that would only ever apply if someone actually
        called it (see DEFAULT_RULE's own docstring for why cost_for()
        still must fall back; disclosure and enforcement are allowed to
        answer this differently).
        """
        rule = self.rules.get(tool_name)
        if rule is None:
            return None
        effective_actor = actor if actor is not None else ActionActor(actor_id="")
        if params is None:
            sim_cost = reference_cost(rule)
        else:
            params_map = normalize_params(params)
            sim_cost = compute_cost(rule, tool_name, params_map, effective_actor)
        return ActionCostQuote(sim_cost=sim_cost, basis=rule.description or rule.category.value)

    def cost_for(self, tool_name: str, params: Mapping[str, Any] | BaseModel, actor: ActionActor) -> int:
        """Pure: see module docstring's "Pure by construction" section.
        Never raises - an unknown tool_name prices at default_rule rather
        than erroring, and a malformed params value degrades through
        extract_quantity()/extract_trade_value()'s own try/except rather
        than propagating.
        """
        rule = self.rule_for(tool_name)
        params_map = normalize_params(params)
        return compute_cost(rule, tool_name, params_map, actor)

    async def charge(
        self,
        ledger: Ledger,
        tool_name: str,
        params: Mapping[str, Any] | BaseModel,
        actor: ActionActor,
        tick: int,
    ) -> Transaction | None:
        """The one call the registry integration point makes per
        dispatch() (see module docstring). Computes the cost, refuses
        with ActionUnaffordableError if the actor's sim balance cannot
        cover it (checked here, BEFORE Ledger.move is ever called, so a
        refusal is guaranteed to be a no-op against the ledger - not just
        "probably", since Ledger.apply_movement's own InsufficientFundsError
        guard is defense-in-depth, not the primary check), and otherwise
        moves the sim to the government treasury through Ledger.move -
        the same double-entry primitive every other sim-currency operation
        in this package uses, so the books stay balanced and the tool that
        caused the charge is recorded in the Transaction's memo (memo_for).

        Returns None for a zero-cost action (SCHEDULE_TABLE's three
        deliberately-0 rows, or any rule that nets to 0) rather than
        moving 0 sim - Ledger.apply_movement's amount<=0 guard would raise
        InvalidAmountError on that, and "this action happens to be free"
        is not the same failure as "this actor cannot afford this action".
        """
        cost = self.cost_for(tool_name, params, actor)
        if cost <= 0:
            return None
        balance = ledger.sim_balance(actor.actor_id)
        if balance < cost:
            raise ActionUnaffordableError(tool_name, actor.actor_id, cost, balance)
        return await ledger.move(
            from_account_id=actor.actor_id,
            to_account_id=TREASURY_ACCOUNT_ID,
            amount=cost,
            currency=Currency.SIM,
            reason=TransactionReason.ACTION_COST,
            tick=tick,
            memo=memo_for(tool_name),
        )

    def describe(self) -> list[dict[str, Any]]:
        """JSON-able rows for publishing to agents (DESIGN.md: "The cost
        schedule is published in the tool descriptions they read") and for
        researchers/tests to query programmatically. Sorted by tool_name
        for a stable, diffable rendering.
        """
        return [
            {
                "tool_name": tool_name,
                "category": rule.category.value,
                "base_cost": rule.base_cost,
                "per_unit_cost": rule.per_unit_cost,
                "value_bps": rule.value_bps,
                "min_cost": rule.min_cost,
                "skill_discount": rule.skill_discount,
                "description": rule.description,
            }
            for tool_name, rule in sorted(self.rules.items())
        ]


DEFAULT_SCHEDULE = ActionCostSchedule(rules=dict(SCHEDULE_TABLE), default_rule=DEFAULT_RULE)


def cost_for(tool_name: str, params: Mapping[str, Any] | BaseModel, actor: ActionActor) -> int:
    """Module-level convenience matching this task's literal
    `cost_for(tool_name, params, actor)` signature - delegates to
    DEFAULT_SCHEDULE, the production table."""
    return DEFAULT_SCHEDULE.cost_for(tool_name, params, actor)


async def refund_action(
    ledger: Ledger,
    tool_name: str,
    actor_id: str,
    amount: int,
    tick: int,
) -> Transaction | None:
    """Give back an action charge whose action then failed.

    Needed because the charge and the world are protected by different
    mechanisms. `ToolRegistry.execute()` snapshots OWNED_STATE_FIELDS and
    restores them if a handler raises - but the ledger is deliberately NOT in
    that snapshot (it is a sibling system, see WorldState's docstring), so
    money that moved during a failed call would simply stay moved. Charging
    before the handler - which is required, so an unaffordable action is
    refused while the world is still untouched - would therefore leave a
    failed action paid for and rolled back at the same time.

    A reversing move rather than deleting the original transaction: the books
    stay double-entry, and the audit trail keeps BOTH legs, so a researcher
    reading the ledger can still see that the attempt was made and reversed.
    An erased charge would look like the attempt never happened.
    """
    if amount <= 0:
        return None
    return await ledger.move(
        from_account_id=TREASURY_ACCOUNT_ID,
        to_account_id=actor_id,
        amount=amount,
        currency=Currency.SIM,
        reason=TransactionReason.REFUND,
        tick=tick,
        memo=f"{REFUND_MEMO_PREFIX}{tool_name}",
    )


async def charge_for_action(
    ledger: Ledger,
    tool_name: str,
    params: Mapping[str, Any] | BaseModel,
    actor: ActionActor,
    tick: int,
) -> Transaction | None:
    """Module-level convenience wrapping DEFAULT_SCHEDULE.charge() - the
    exact call the registry integration point makes (see module
    docstring's "Integration point" section)."""
    return await DEFAULT_SCHEDULE.charge(ledger, tool_name, params, actor, tick)
