"""Daily household upkeep (DESIGN.md section 3, "Everything costs: living
is not free"): food, shelter, and heat accrue once per game-day, per
agent, and are charged to the HOUSEHOLD, not blindly to whichever
individual happens to hold the money.

This is the load-bearing rule DESIGN.md names explicitly: "without it an
agent could minimise cost by doing nothing, and idleness would be the
winning strategy. Upkeep makes standing still expensive, so *doing
nothing is also a way to die*." Every other cost in the system (thinking,
acting, travel, fees) is something an agent chooses to spend on. Upkeep
is the one cost nobody can opt out of by staying home.

DEPENDENTS (DESIGN.md section 4: "minors and the retired") consume
upkeep while earning nothing. This module never invents a second age
threshold for "retired" - it reuses PENSION_MINIMUM_AGE from
world/ledger/money.py, the same 70-year cutoff the pension system
already uses (DESIGN.md section 4: "elderly (70+, pensioned)"), so the
two systems can never disagree about who counts as retired.

The escalating consequence for a household that cannot cover its bill
deliberately reuses the EXISTING health and dormancy machinery instead
of inventing a parallel one (this task's explicit instruction):
  1. Days 1 to SEVERE-1 unpaid: mild hunger, a capacity penalty this
     module applies on top of health.work_capacity() (see
     effective_work_capacity below) - no new state in health.py, just a
     multiplier layered outside it.
  2. From HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD: a real malnutrition
     ailment, built with health.onset_ailment(AilmentKind.SHORT, ...)
     and stored in the agent's own HealthState.current_ailment exactly
     like a naturally-rolled illness would be, so health.work_capacity(),
     health.advance_recovery(), and the chronic-death-hazard path all
     apply to it automatically with zero code duplicated here.
  3. From HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD: death.mark_dormant() is
     called directly, the SAME dormancy state DESIGN.md's cognitive-
     poverty rule uses for a $0 USD wallet. From there the EXISTING
     24-game-hour dormancy timeout (death.check_dormancy_death, driven
     each hour by tick.run_hourly_life_tick, both untouched by this
     module) takes the agent to death with no new death path, cause, or
     timer defined anywhere in this file.

This module owns no AgentLifeRecord field of its own (it cannot: the
task that owns this file may not edit life/models.py). HungerState is
therefore tracked the same way AgentLifeRecord itself is today - as a
plain value the caller (the not-yet-built tick engine, see the report
at the bottom of this module) keeps in a dict keyed by agent_id,
mirroring how it will already be keeping a dict of AgentLifeRecord keyed
by agent_id. Everything here is a pure or EconomyPort-mediated function
of (state in) -> (state out, events), exactly the shape health.py,
death.py, and household.py already use.

Every sim-currency amount below is an ASSUMPTION in the same sense
constants.py's own module docstring defines the word: DESIGN.md
describes the mechanic ("food, shelter, and heat") but pins no number.
These live here rather than in world/life/constants.py only because this
task owns exclusively this new file; whoever wires the daily tick should
consider hoisting them into constants.py alongside PHARMACY_SHORT_
TREATMENT_COST_SIM and TEMPLE_WEDDING_FEE_SIM, which were picked under
the identical ASSUMPTION label.
"""

from __future__ import annotations

import random

from pydantic import BaseModel, Field

from world.ledger.money import PENSION_MINIMUM_AGE
from world.life.clock import GameClock
from world.life.death import mark_dormant
from world.life.health import onset_ailment, work_capacity
from world.life.models import AgentLifeRecord, AilmentKind, HealthState, LifeEvent, PopulationDials
from world.life.ports import EconomyPort, InsufficientFundsError

UPKEEP_FOOD_SIM_PER_DAY = 40
UPKEEP_SHELTER_SIM_PER_DAY = 30
UPKEEP_HEAT_SIM_PER_DAY = 20
UPKEEP_TOTAL_SIM_PER_DAY = (
    UPKEEP_FOOD_SIM_PER_DAY + UPKEEP_SHELTER_SIM_PER_DAY + UPKEEP_HEAT_SIM_PER_DAY
)

UPKEEP_TRANSACTION_REASON = "daily_upkeep"

HUNGER_MILD_WORK_CAPACITY_MULTIPLIER = 0.75
HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD = 2
HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD = 3
assert HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD < HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD, (
    "the hunger ladder must strictly escalate: severe has to bite before dormancy does"
)


def is_dependent(agent: AgentLifeRecord) -> bool:
    """Minors and the retired (DESIGN.md section 3): consume upkeep, earn
    nothing. Reuses AgentLifeRecord.is_minor and money.py's
    PENSION_MINIMUM_AGE rather than a second, possibly-drifting cutoff.
    """
    return agent.is_minor or agent.age_years >= PENSION_MINIMUM_AGE


class UpkeepBreakdown(BaseModel):
    """One agent's slice of a household's daily bill.

    The formula is uniform (DESIGN.md: "Per-agent DAILY UPKEEP") - a
    dependent costs exactly as much to feed, house, and heat as an
    earner does. ``is_dependent`` is carried here purely for reporting
    (compute_household_bill uses it to split totals), it never changes
    the amount itself.
    """

    agent_id: str
    food_sim: int
    shelter_sim: int
    heat_sim: int
    total_sim: int
    is_dependent: bool


def per_agent_upkeep(agent: AgentLifeRecord) -> UpkeepBreakdown:
    return UpkeepBreakdown(
        agent_id=agent.agent_id,
        food_sim=UPKEEP_FOOD_SIM_PER_DAY,
        shelter_sim=UPKEEP_SHELTER_SIM_PER_DAY,
        heat_sim=UPKEEP_HEAT_SIM_PER_DAY,
        total_sim=UPKEEP_TOTAL_SIM_PER_DAY,
        is_dependent=is_dependent(agent),
    )


class HouseholdUpkeepBill(BaseModel):
    """What one household owes for one game-day, and who in it is a
    dependent versus an earner. This is the "queryable" half of the
    task's requirement: pure and synchronous, so any caller (a future
    read-only viewer endpoint included, app/CONVENTIONS.md's "research/
    is read-only") can ask what a household costs without touching the
    ledger at all.
    """

    household_id: str
    day: int
    breakdowns: list[UpkeepBreakdown]
    total_sim: int
    dependents_total_sim: int
    earners_total_sim: int
    earner_ids: list[str]
    dependent_ids: list[str]


def compute_household_bill(
    household_id: str, day: int, members: list[AgentLifeRecord]
) -> HouseholdUpkeepBill:
    """Pure: no I/O, no randomness. Dead members owe nothing - a corpse
    consumes no food, shelter, or heat - so the bill only ever reflects
    ``member.alive`` agents, silently dropping the rest rather than
    requiring the caller to pre-filter.
    """
    alive_members = [member for member in members if member.alive]
    breakdowns = [per_agent_upkeep(member) for member in alive_members]
    earner_ids = [b.agent_id for b in breakdowns if not b.is_dependent]
    dependent_ids = [b.agent_id for b in breakdowns if b.is_dependent]
    total_sim = sum(b.total_sim for b in breakdowns)
    dependents_total_sim = sum(b.total_sim for b in breakdowns if b.is_dependent)
    return HouseholdUpkeepBill(
        household_id=household_id,
        day=day,
        breakdowns=breakdowns,
        total_sim=total_sim,
        dependents_total_sim=dependents_total_sim,
        earners_total_sim=total_sim - dependents_total_sim,
        earner_ids=earner_ids,
        dependent_ids=dependent_ids,
    )


def earner_burden(bill: HouseholdUpkeepBill) -> dict[str, int]:
    """Who is carrying the bill, projected as an even split (the second
    half of "what does it cost per day and who is carrying it").

    Splits the WHOLE bill (earners' own upkeep plus every dependent's)
    across the earners, since DESIGN.md section 3 is explicit that "a
    household's earners must cover them" - dependents are not expected
    to contribute while an earner is available. Falls back to splitting
    across the dependents themselves only when the household has no
    earner at all, matching collect_household_upkeep's own payer-order
    fallback so this projection never promises a split the real
    settlement wouldn't attempt. Remainder cents go to the first payers
    in list order, the same rule settle_inheritance uses in ledger/core.py.
    """
    payer_ids = bill.earner_ids or bill.dependent_ids
    if not payer_ids:
        return {}
    share, remainder = divmod(bill.total_sim, len(payer_ids))
    burden = {agent_id: share for agent_id in payer_ids}
    for agent_id in payer_ids[:remainder]:
        burden[agent_id] += 1
    return burden


class HouseholdUpkeepSummary(BaseModel):
    """One call, both halves of the queryability requirement: the bill
    and the projected burden."""

    bill: HouseholdUpkeepBill
    burden_sim: dict[str, int]


def summarize_household_upkeep(
    household_id: str, day: int, members: list[AgentLifeRecord]
) -> HouseholdUpkeepSummary:
    bill = compute_household_bill(household_id, day, members)
    return HouseholdUpkeepSummary(bill=bill, burden_sim=earner_burden(bill))


class UpkeepPaymentOutcome(BaseModel):
    household_id: str
    day: int
    bill: HouseholdUpkeepBill
    paid_in_full: bool
    amount_paid_sim: int
    shortfall_sim: int
    contributions: dict[str, int]
    events: list[LifeEvent]


async def collect_household_upkeep(
    bill: HouseholdUpkeepBill, economy: EconomyPort
) -> UpkeepPaymentOutcome:
    """Settle one household's bill against real wallets.

    "Charged to the household, not blindly to the individual" (this
    task's instruction) means the bill is one shared number, not N
    personal debts - so this pools payment across every earner in turn,
    each contributing as much of the running total as their own balance
    allows, instead of each earner separately owing an even 1/N share
    and individually failing if their own slice doesn't fit their
    wallet. A richer earner covering a poorer co-earner's shortfall is
    the intended effect, not a bug.

    If the household has no earner at all (every alive member is a
    dependent), the dependents pay from their own reserves as a last
    resort - there is no one else to charge, and DESIGN.md section 3 is
    explicit that a dependents-only household "starves together" rather
    than being magically exempted from the bill.

    Payment is best-effort, not all-or-nothing: a household that can
    only partly afford its bill still pays what it has (nobody gets to
    keep grocery money while skipping the bill), and the shortfall
    drives the hunger consequence in advance_hunger. Balances are
    re-queried immediately before each debit (not computed once up
    front) so a concurrent debit elsewhere in a real ledger can only
    ever reduce what this call collects, never cause it to attempt a
    debit larger than what is actually there; debit_sim failing anyway
    (a genuine race) is treated as "this payer had nothing left" rather
    than aborting the whole collection.
    """
    if bill.total_sim <= 0:
        return UpkeepPaymentOutcome(
            household_id=bill.household_id,
            day=bill.day,
            bill=bill,
            paid_in_full=True,
            amount_paid_sim=0,
            shortfall_sim=0,
            contributions={},
            events=[],
        )

    payer_ids = bill.earner_ids or bill.dependent_ids
    remaining = bill.total_sim
    contributions: dict[str, int] = {}
    for payer_id in payer_ids:
        if remaining <= 0:
            break
        balance = await economy.get_sim_balance(payer_id)
        take = min(balance, remaining)
        if take <= 0:
            continue
        try:
            await economy.debit_sim(payer_id, take, reason=UPKEEP_TRANSACTION_REASON)
        except InsufficientFundsError:
            continue
        contributions[payer_id] = take
        remaining -= take

    paid_in_full = remaining <= 0
    amount_paid = bill.total_sim - max(remaining, 0)
    event_type = "economy.upkeep.charged" if paid_in_full else "economy.upkeep.defaulted"
    event = LifeEvent(
        type=event_type,
        agent_id=None,
        payload={
            "household_id": bill.household_id,
            "day": bill.day,
            "total_sim": bill.total_sim,
            "amount_paid_sim": amount_paid,
            "shortfall_sim": max(remaining, 0),
            "contributions": contributions,
        },
    )
    return UpkeepPaymentOutcome(
        household_id=bill.household_id,
        day=bill.day,
        bill=bill,
        paid_in_full=paid_in_full,
        amount_paid_sim=amount_paid,
        shortfall_sim=max(remaining, 0),
        contributions=contributions,
        events=[event],
    )


class HungerState(BaseModel):
    """Per-agent hunger state driven by unpaid household upkeep.

    Owned entirely by this module's caller, never folded into
    AgentLifeRecord - see the module docstring for why. Keyed by
    agent_id in a dict the same way the tick engine will already be
    keeping AgentLifeRecord itself.
    """

    consecutive_unpaid_days: int = Field(default=0, ge=0)
    days_since_meal: int = Field(default=0, ge=0)


def effective_work_capacity(health: HealthState, hunger: HungerState) -> float:
    """The "hunger affecting work capacity" half of the escalating
    consequence, layered on top of health.work_capacity() rather than
    duplicating it. Only applies while hunger is mild (below the severe
    threshold, see the module docstring's ladder) - once hunger is
    severe enough to have onset a real malnutrition ailment,
    health.work_capacity() alone already reflects that (WORK_CAPACITY_
    BY_AILMENT[SHORT]), and stacking this multiplier on top of it too
    would double-penalise the same day of hunger.
    """
    base = work_capacity(health)
    if 0 < hunger.days_since_meal < HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD:
        return base * HUNGER_MILD_WORK_CAPACITY_MULTIPLIER
    return base


def advance_hunger(
    rng: random.Random,
    agent: AgentLifeRecord,
    hunger: HungerState,
    day: int,
    paid_in_full: bool,
    ate_today: bool = False,
) -> tuple[AgentLifeRecord, HungerState, list[LifeEvent]]:
    """One agent's step through the hunger ladder for one game-day.

    A no-op for a dead agent (matches tick.run_daily_health_tick's and
    run_hourly_life_tick's own "if not agent.alive: return agent, []"
    guard). Being fed today resets the streak to zero and, if the agent
    was hungry yesterday, fires a relief event - it does NOT retroactively
    cure a malnutrition ailment already onset (that heals on its own
    schedule through the existing health.advance_recovery, exactly like
    any other short ailment) and it does NOT undo dormancy (the existing
    revive() flow in death.py is the only way out of that, matching how
    a $0-triggered dormancy is not un-done just because sim currency,
    which dormancy has nothing to do with, is now flush again).
    """
    if not agent.alive:
        return agent, hunger, []

    unpaid_days = 0 if paid_in_full else hunger.consecutive_unpaid_days + 1

    fed_today = paid_in_full or ate_today
    streak = 0 if fed_today else hunger.days_since_meal + 1
    updated_hunger = HungerState(
        consecutive_unpaid_days=unpaid_days, days_since_meal=streak
    )

    if fed_today:
        if hunger.days_since_meal == 0 and hunger.consecutive_unpaid_days == unpaid_days:
            return agent, hunger, []
        events = []
        if hunger.days_since_meal > 0:
            events.append(
                LifeEvent(
                    type="health.hunger.relieved",
                    agent_id=agent.agent_id,
                    payload={"day": day},
                )
            )
        return agent, updated_hunger, events

    events: list[LifeEvent] = [
        LifeEvent(
            type="health.hunger.worsened",
            agent_id=agent.agent_id,
            payload={
                "day": day,
                "days_since_meal": streak,
                "consecutive_unpaid_days": unpaid_days,
            },
        )
    ]

    if streak >= HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD:
        now = GameClock.at_day(day)
        new_dormancy, dormancy_events = mark_dormant(agent.dormancy, now)
        agent = agent.model_copy(update={"dormancy": new_dormancy})
        events.extend(dormancy_events)
        return agent, updated_hunger, events

    if streak >= HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD and agent.health.current_ailment is None:
        ailment = onset_ailment(rng, AilmentKind.SHORT, day)
        new_health = agent.health.model_copy(update={"current_ailment": ailment})
        agent = agent.model_copy(update={"health": new_health})
        events.append(
            LifeEvent(
                type="health.ailment.onset",
                agent_id=agent.agent_id,
                payload={
                    "kind": AilmentKind.SHORT.value,
                    "onset_day": day,
                    "cause": "malnutrition",
                },
            )
        )

    return agent, updated_hunger, events


class UpkeepTickResult(BaseModel):
    household_id: str
    day: int
    outcome: UpkeepPaymentOutcome
    updated_members: list[AgentLifeRecord]
    updated_hunger: dict[str, HungerState]
    events: list[LifeEvent]


async def run_daily_upkeep_tick(
    rng: random.Random,
    household_id: str,
    day: int,
    members: list[AgentLifeRecord],
    hunger_by_agent: dict[str, HungerState],
    economy: EconomyPort,
    dials: PopulationDials,
    fed_agent_ids: set[str] | None = None,
) -> UpkeepTickResult:
    """One household's complete daily upkeep cycle: bill, collect,
    escalate. This is the single entry point a day-boundary tick calls
    once per household per game-day - see this module's report for the
    exact one-line hook into world's (not-yet-built) daily tick loop.

    ``dials`` is accepted for the same reason every other life/ tick
    function takes it (death.check_dormancy_death, called later by
    tick.run_hourly_life_tick against the dormancy this function may
    set, is gated by dials.death_hazard_multiplier) - mark_dormant()
    itself is not gated (entering dormancy is a state transition, not a
    hazard roll; only the eventual death from staying dormant is), so
    this parameter is forwarded for signature symmetry with the rest of
    life/tick.py rather than consumed directly here.
    """
    alive_members = [member for member in members if member.alive]
    bill = compute_household_bill(household_id, day, alive_members)
    outcome = await collect_household_upkeep(bill, economy)

    events = list(outcome.events)
    updated_members: list[AgentLifeRecord] = []
    updated_hunger: dict[str, HungerState] = dict(hunger_by_agent)
    for member in members:
        if not member.alive:
            updated_members.append(member)
            continue
        hunger = hunger_by_agent.get(member.agent_id, HungerState())
        updated_agent, new_hunger, hunger_events = advance_hunger(
            rng,
            member,
            hunger,
            day,
            outcome.paid_in_full,
            ate_today=member.agent_id in (fed_agent_ids or ()),
        )
        updated_members.append(updated_agent)
        updated_hunger[member.agent_id] = new_hunger
        events.extend(hunger_events)

    return UpkeepTickResult(
        household_id=household_id,
        day=day,
        outcome=outcome,
        updated_members=updated_members,
        updated_hunger=updated_hunger,
        events=events,
    )

