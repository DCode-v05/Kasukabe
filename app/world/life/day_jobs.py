"""The day-boundary economy, in one module both composition roots call.

ECON-08. Kasukabe has two roots that advance world time:
``world/run_genesis.py``'s ``GenesisRunner`` (the embedded runner every
launcher actually boots) and ``world/main.py`` + ``world/wiring.py``'s
``WorldServer`` (the standalone server ROADMAP T3 says becomes the only
one, and the target of the game/console viewer contract). Only the first
had an economy. Grepping ``wiring.py`` for ``upkeep``, ``head_tax``,
``accrue_wages``, ``pension`` or ``payroll`` returned nothing: its
``advance_tick`` ran hourly aging plus a daily health roll and stopped.
So the root the roadmap is migrating to ran materially different physics
from the root the runs were made on - nobody there paid for food,
shelter or heat, nobody paid for existing, no wage promise ever came
due, no retiree drew anything, and a resident whose dollar wallet hit
zero was never marked dormant unless a burn happened to notice. Every
stake DESIGN.md section 3 describes was absent from the server the
viewer talks to.

The fix the review asks for is not "copy the jobs into the second root"
- that is how ECON-22's two life-tick implementations came to differ in
the first place. It is one module, taking ``(world, ledger, debts,
hunger, dials, rng, tick, day)`` explicitly and reaching into no runner,
so both roots call the same code and a change to the economy cannot
reach one town and miss the other.

WHAT LIVES HERE, and what deliberately does not. This module owns the
five jobs that are pure functions of world state plus the ledger:

  * ``sweep_zero_wallets`` - every tick, not just at the day boundary: a
    resident whose wallet empties at 09:10 must not keep thinking until
    midnight on money it does not have.
  * ``run_daily_upkeep``   - ``upkeep.run_daily_upkeep_tick`` once per
    household per day, hunger ladder included.
  * ``charge_head_tax``    - ``ledger.head_tax.charge_daily_head_tax``
    once per household per day, arrears recorded.
  * ``pay_pensions``       - ``Ledger.pay_pension`` for every retiree.
  * ``reconcile_wages``    - ``WorldState.accrue_wages`` plus one
    overdue notice per unpaid promise.

It does NOT own coming-of-age, estate settlement or scheduler
unregistration. Those are run_genesis's inline additions (ECON-22) and
each of them needs a runner-owned collaborator - a guardianship port, a
will store, the tick engine's scheduler - that ``WorldState`` alone
cannot supply. Folding them in here would mean this module could only
ever be called by the runner, which is the exact coupling it exists to
undo. ``life/tick.py`` carries the per-agent half of that consolidation.

EVENTS. ``life/`` has never imported ``world.core`` (see soul_seed.py's
module docstring on why that seam is load-bearing), and this module does
not become the first. Every job returns or forwards
``world.life.models.LifeEvent`` through a ``publish`` callable whose
signature - ``(events, tick)`` - is exactly
``GenesisRunner.publish_life_events``'s, so the runner's half of the
consolidation is a one-line change at its call site rather than a
rewrite of its event handling.

MONEY. Every amount below is an integer sim unit. Nothing here computes
a rate, a share or a fee: it reads what ``life/upkeep.py``,
``ledger/head_tax.py`` and ``Ledger.pay_pension`` already decided.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic import BaseModel

from world.ledger.core import Ledger
from world.ledger.errors import LedgerError
from world.ledger.head_tax import HeadTaxDebtStore, charge_daily_head_tax
from world.ledger.money import PENSION_MINIMUM_AGE
from world.life.clock import GameClock
from world.life.constants import DORMANCY_THRESHOLD_USD_CENTS
from world.life.death import DORMANCY_CAUSES, mark_dormant
from world.life.models import LifeEvent, PopulationDials
from world.life.ports import EconomyPort
from world.life.upkeep import HungerState, run_daily_upkeep_tick

logger = logging.getLogger(__name__)

# ``GenesisRunner.publish_life_events``'s exact signature. Named so a
# caller that already has that method can pass it unchanged.
PublishLifeEvents = Callable[[Sequence[LifeEvent], int], Awaitable[None]]

# A resident with no household is a household of one (ECON-31). Keyed on
# the agent id rather than a minted household id, because inventing one
# would put a household in ``world.households`` that nobody created and
# that the family API would then have to explain.
SOLO_HOUSEHOLD_PREFIX = "solo:"


class DayJobsReport(BaseModel):
    """What the day boundary actually did, in money and in counts.

    Counts alone are not evidence: ECON-01 is the finding that
    ``assert_the_day_was_charged`` compared households VISITED against
    households that exist and passed over a tax that moved nothing.
    Every job here reports the sim it settled alongside the households
    it reached, so a caller that wants to gate on the money has the
    money.
    """

    day: int
    households: int = 0
    upkeep_households: int = 0
    upkeep_failed: int = 0
    upkeep_paid_sim: int = 0
    upkeep_shortfall_sim: int = 0
    head_tax_households: int = 0
    head_tax_sim: int = 0
    head_tax_rate_bps: int = 0
    pensions_paid: int = 0
    pensions_without_a_salary: int = 0
    wages_accrued: int = 0
    wages_overdue: int = 0


def households_of(world: Any) -> dict[str, list[Any]]:
    """Every living resident grouped by the household that pays for them.

    ECON-31: a resident with no household is not exempt, it is a
    household of one. The old upkeep grouping skipped ``household_id is
    None`` entirely while the head tax still billed that same person, so
    two day-boundary jobs disagreed about whether they existed. Every
    genesis resident has a household, so this is latent until somebody
    leaves one - ``MOVE_OUT_ALONE`` mints a new household, but a
    household also dissolves when its last other member dies.
    """
    by_household: dict[str, list[Any]] = {}
    for agent in world.agents.values():
        if not agent.alive:
            continue
        key = agent.household_id or f"{SOLO_HOUSEHOLD_PREFIX}{agent.agent_id}"
        by_household.setdefault(key, []).append(agent)
    return by_household


def who_pays_for(ledger: Ledger, heads: Sequence[str]) -> str:
    """Which member of a household is billed for its head tax.

    ECON-19. "First in dict order" was an accident of seeding, not a
    rule: it meant the bill fell on whoever ``agents`` iterated first,
    which in practice was often a child or a pensioner holding 200 sim,
    so the household went into recorded debt while an earning adult
    beside them held thousands. The richest holder pays, with the id
    breaking a tie so two runs of the same seed bill the same person.

    An account the ledger has never heard of sorts as zero rather than
    raising: a household whose members are all unknown to the ledger
    still needs a payer for ``charge_daily_head_tax`` to record the debt
    against.
    """

    def holding(agent_id: str) -> tuple[int, str]:
        try:
            return (ledger.sim_balance(agent_id), agent_id)
        except LedgerError:
            return (0, agent_id)

    return max(heads, key=holding)


async def sweep_zero_wallets(
    world: Any,
    ledger: Ledger,
    *,
    now: GameClock,
    tick: int,
    publish: PublishLifeEvents,
) -> list[str]:
    """Any living resident whose dollar wallet has reached zero stops
    thinking, and the town is told where they fell.

    Runs EVERY tick, not at the day boundary: a wallet that empties at
    09:10 must not buy thinking until midnight. ``burn_inference``
    catches the case where the burn itself is what emptied the wallet,
    but nothing catches a wallet emptied by anything else - a minor
    whose paying parent died, a resumed checkpoint, an account opened
    with nothing - so without this the dormancy chain (dormant ->
    24 game-hours -> death, and the ``revive`` tool that interrupts it)
    is unreachable on this root by any route but one.

    The event carries the place and the deadline because the only thing
    anybody can do about it is walk there and pay, and a household that
    is not told where cannot.
    """
    fell: list[str] = []
    for agent_id, agent in list(world.agents.items()):
        if not agent.alive or agent.dormancy.is_dormant:
            continue
        try:
            # A minor's calls are billed to the parent (DESIGN.md
            # section 4), so it is the PAYER's wallet that decides
            # whether this resident can still think - the same resolution
            # burn_inference already makes before it burns.
            payer_id = ledger.resolve_usd_payer(agent_id)
            balance = ledger.usd_balance(payer_id)
        except LedgerError:
            # No account, or a minor with no parent on file. Not a
            # resident who has run out of money; a resident the ledger
            # cannot answer for at all, and inventing "$0" for them would
            # mark the whole cohort dormant on the first tick.
            continue
        # The named threshold, not a bare zero: the law block quotes this
        # same constant back to the resident (ECON-24), and a law that
        # reads its number from a different copy than the code does is
        # how a rule stops being true.
        if balance > DORMANCY_THRESHOLD_USD_CENTS:
            continue
        dormancy, events = mark_dormant(
            agent.dormancy, now, cause=DORMANCY_CAUSES["at_work"])
        if not events:
            continue
        world.agents[agent_id] = agent.model_copy(update={"dormancy": dormancy})
        # B1's interruption rule: a worker that has run out of money to
        # think with is not left mid-job. Nothing wakes them - they are
        # dormant - but the commitment must not outlive the tick, or a
        # revived resident resumes a job it decided on before it went dark.
        world.continuing.pop(agent_id, None)
        household_id = agent.household_id
        for event in events:
            event.agent_id = agent_id
            event.payload["location_id"] = world.location_of(agent_id)
            event.payload["household_id"] = household_id
            # Their household reads this on its next turn - that is who
            # can reach them in time.
            event.payload["involved_agent_ids"] = [
                other_id
                for other_id, other in world.agents.items()
                if household_id and other.household_id == household_id
            ] or [agent_id]
        await publish(events, tick)
        fell.append(agent_id)
    return fell


async def run_daily_upkeep(
    rng: Any,
    world: Any,
    economy: EconomyPort,
    hunger_by_agent: dict[str, HungerState],
    dials: PopulationDials,
    *,
    tick: int,
    day: int,
    publish: PublishLifeEvents,
    report: DayJobsReport,
    # ECON-16: who has eaten since the last day boundary. Defaults to
    # nobody so an untaught caller keeps the old behaviour rather than
    # silently feeding the whole town.
    fed_agent_ids: set[str] | None = None,
) -> None:
    """Charge every household its day's living costs, and escalate.

    One call per household per game-day, the shape
    ``upkeep.run_daily_upkeep_tick`` was written for. Hunger state lives
    in the caller's dict rather than on ``AgentLifeRecord`` because that
    record deliberately owns no such field (see its own docstring);
    upkeep.py's integration report names exactly that dict as the
    intended home.

    One household that raises must not take the day down for the rest -
    the same rule the head tax below follows. A failure is counted, so a
    day where every household failed is visible as that rather than as a
    day where nobody owed anything.
    """
    for household_id, members in households_of(world).items():
        try:
            result = await run_daily_upkeep_tick(
                rng,
                household_id,
                day,
                members,
                hunger_by_agent,
                economy,
                dials,
                # ECON-16: eating relieves hunger without paying the bill.
                fed_agent_ids=fed_agent_ids,
            )
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning("upkeep failed for %s: %s", household_id, exc)
            report.upkeep_failed += 1
            continue
        for member in result.updated_members:
            world.agents[member.agent_id] = member
        hunger_by_agent.update(result.updated_hunger)
        report.upkeep_households += 1
        report.upkeep_paid_sim += int(result.outcome.amount_paid_sim)
        report.upkeep_shortfall_sim += int(result.outcome.shortfall_sim)
        await publish(result.events, tick)


async def charge_head_tax(
    world: Any,
    ledger: Ledger,
    debts: HeadTaxDebtStore,
    *,
    tick: int,
    report: DayJobsReport,
) -> None:
    """One head tax bill per household, per completed game-day.

    A household that cannot cover every head pays for as many as it can,
    in order, and the rest becomes recorded debt in ``world.arrears`` -
    never a negative balance, never a silent write-off, because
    ``check_debts`` reads those arrears straight back to the resident.

    The rate is read from the charter's own policy store on every call,
    so a government that votes the tax up or down is obeyed on the very
    next day boundary with no propagation step. Read as a real attribute
    rather than through a defaulting ``getattr``: a policy that lost the
    field should stop the town, not quietly tax it at zero for ever.
    """
    policy = await world.charter.store.get_policy()
    rate_bps = int(policy.tax_rate_bps)
    report.head_tax_rate_bps = rate_bps

    for household_id, members in households_of(world).items():
        heads = [member.agent_id for member in members]
        payer = who_pays_for(ledger, heads)
        try:
            result = await charge_daily_head_tax(
                ledger,
                debts,
                payer_agent_id=payer,
                head_agent_ids=heads,
                tax_rate_bps=rate_bps,
                tick=tick,
                household_id=(
                    None if household_id.startswith(SOLO_HOUSEHOLD_PREFIX) else household_id
                ),
                # The ledger's own sink, not None: with None the
                # `economy.head_tax_debt.accrued`/`.repaid` events are
                # never published at all, so a report reading arrears off
                # the bus prints a zero over a run that did charge the tax
                # (CORE-03).
                event_sink=ledger.event_sink,
            )
        except Exception as exc:  # noqa: BLE001 - one household must not
            # take the day down for the others, same as upkeep above.
            logger.warning("head tax failed for %s: %s", household_id, exc)
            continue
        report.head_tax_households += 1
        # ECON-01: the SIM, not just the call. At a rate of 0 bps this
        # correctly stays zero while the household count still rises,
        # which is the distinction a caller gating on "was the day
        # charged" needs and did not have.
        report.head_tax_sim += int(result.total_paid_sim)
        for debt in result.new_debts:
            bucket = world.arrears.setdefault(debt.debtor_agent_id, {})
            bucket["head_tax"] = bucket.get("head_tax", 0) + debt.amount_sim
            report.head_tax_sim += int(debt.amount_sim)


def last_salary_sim(world: Any, agent_id: str) -> int:
    """The best wage this resident was ever promised, or 0.

    ``WorldState.wage_promises`` is keyed ``"{business_id}|{employee_id}"``
    (see its own field comment), so a lookup by bare agent id never hits
    and every retiree in the town answers "no salary". Filtering on
    ``employee_id`` is the only correct read, and it is the reason a
    pension job can look fully wired and still pay nobody.
    """
    wages = [
        promise.wage_sim
        for promise in world.wage_promises.values()
        if promise.employee_id == agent_id and promise.wage_sim > 0
    ]
    return max(wages) if wages else 0


async def pay_pensions(
    world: Any, ledger: Ledger, *, tick: int, report: DayJobsReport
) -> None:
    """Pay every retiree, once a game-day (ECON-09).

    ``Ledger.pay_pension`` implements DESIGN.md section 4 in full -
    treasury pays at 70+, a quarter of last salary for a private-sector
    retiree. The caller has to supply age and last salary because the
    ledger deliberately does not own demographics.

    A retiree who never held a wage promise draws nothing, which is
    correct: there is no salary to take a quarter of. That case is
    counted rather than skipped silently, so a town where every retiree
    draws nothing is visible as that rather than as a pension system
    nobody noticed was idle. An unpayable pension (an empty treasury) is
    a fact about the town worth seeing, not an exception worth ending a
    tick for.
    """
    for agent_id, agent in list(world.agents.items()):
        if not agent.alive or agent.age_years < PENSION_MINIMUM_AGE:
            continue
        salary = last_salary_sim(world, agent_id)
        if salary <= 0:
            report.pensions_without_a_salary += 1
            continue
        try:
            await ledger.pay_pension(
                agent_id, int(agent.age_years), salary, "private", tick, memo="daily pension"
            )
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning("could not pay %s's pension: %s", agent_id, exc)
            continue
        report.pensions_paid += 1


async def reconcile_wages(
    world: Any, *, tick: int, day: int, publish: PublishLifeEvents, report: DayJobsReport
) -> None:
    """Compare what was promised to what was paid, and tell both sides.

    The world never pays on its own - this is the counting half, and it
    is the only thing that makes a promise mean anything: an employer
    who never pays now has a line in its own perception every morning
    saying so, and so does the employee.

    The overdue notice is a ``LifeEvent`` rather than a ``WorldEvent``
    purely so this module keeps ``life/``'s no-``world.core`` boundary
    (see the module docstring). ``life_event_to_world_event`` renders it
    onto the bus identically: ``agent_id=None`` passes straight through
    and the list-valued ``involved_agent_ids`` key that perception reads
    survives intact.
    """
    moved = world.accrue_wages(day, tick)
    report.wages_accrued = len(moved)

    for promise in world.wage_promises.values():
        if promise.owed_sim <= 0:
            continue
        report.wages_overdue += 1
        business = world.businesses.get(promise.business_id)
        employer_id = business.owner_agent_id if business else None
        await publish(
            [
                LifeEvent(
                    type="business.wage.overdue",
                    agent_id=None,
                    payload={
                        "business_id": promise.business_id,
                        "business_name": business.name if business else promise.business_id,
                        "employer_id": employer_id,
                        "employee_id": promise.employee_id,
                        "agent_id": promise.employee_id,
                        "owed_sim": promise.owed_sim,
                        "days_overdue": promise.days_overdue(tick),
                        "involved_agent_ids": [
                            who for who in (promise.employee_id, employer_id) if who
                        ],
                    },
                )
            ],
            tick,
        )


async def run_day_boundary_jobs(
    rng: Any,
    world: Any,
    ledger: Ledger,
    debts: HeadTaxDebtStore,
    economy: EconomyPort,
    hunger_by_agent: dict[str, HungerState],
    dials: PopulationDials,
    *,
    tick: int,
    day: int,
    publish: PublishLifeEvents,
) -> DayJobsReport:
    """Everything a completed game-day costs, in the order it costs it.

    ECON-08's single entry point: one call at the day boundary gives a
    composition root the whole economy - upkeep, head tax, pensions,
    wage accrual - instead of four call sites it can each forget one of.
    ``wiring.py``'s ``WorldServer.advance_tick`` had forgotten all four.

    Order matters and is the runner's, kept deliberately: upkeep first,
    so a household spends its money on food before the government takes
    its cut and a shortfall shows up as hunger rather than as tax debt;
    then the tax; then the treasury's outflows (pensions), so the day's
    revenue is in before anything is paid out of it; then wage
    reconciliation, which moves no money at all and only counts.
    """
    report = DayJobsReport(day=day, households=len(households_of(world)))
    await run_daily_upkeep(
        rng, world, economy, hunger_by_agent, dials,
        tick=tick, day=day, publish=publish, report=report,
    )
    await charge_head_tax(world, ledger, debts, tick=tick, report=report)
    await pay_pensions(world, ledger, tick=tick, report=report)
    await reconcile_wages(world, tick=tick, day=day, publish=publish, report=report)
    logger.info(
        "day %d: upkeep %d/%d household(s) paying %d sim, head tax %d sim at %d bps, "
        "%d pension(s), %d wage promise(s) overdue",
        day, report.upkeep_households, report.households, report.upkeep_paid_sim,
        report.head_tax_sim, report.head_tax_rate_bps, report.pensions_paid,
        report.wages_overdue,
    )
    return report
