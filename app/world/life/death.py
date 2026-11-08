"""The four death paths (DESIGN.md section 5): terminal, untreated
chronic, 85+ hazard, USD-zero unrevived.

Every path here returns a plain bool ("did this agent die from this
cause today") rather than mutating an AgentLifeRecord directly - the
actual "mark the agent dead, pick a death_cause, emit both the
health.agent.died and agent.died events" step lives in tick.py, which
also decides priority when more than one path could fire the same day
(see run_daily_health_tick's docstring). Keeping that decision out of
this module means each check here is independently unit-testable
against a bare HealthState/DormancyState instead of a full agent
record.

All four paths are gated through population.roll_death, so the god-tab
death-hazard multiplier (DESIGN.md: "0 disables death") applies
uniformly without each function reimplementing the gate.
"""

from __future__ import annotations

import random

from world.life.clock import GameClock
from world.life.constants import (
    CHRONIC_UNTREATED_DAILY_HAZARD,
    DORMANCY_DEATH_HOURS,
    ELDERLY_DAILY_HAZARD,
    ELDERLY_HAZARD_AGE_YEARS,
    EXCHANGE_SIM_PER_USD_CENT,
    REVIVAL_COST_SIM,
    REVIVAL_USD_CREDIT_CENTS,
)
from world.life.models import AilmentKind, DormancyState, HealthState, LifeEvent, PopulationDials
from world.life.population import roll_death
from world.life.ports import EconomyPort


def check_terminal_death(
    rng: random.Random, health: HealthState, day: int, dials: PopulationDials
) -> bool:
    """Has a terminal ailment reached (or passed) its scheduled death day?

    Once past the scheduled day the hazard keeps re-rolling every
    subsequent day at full base probability - with the multiplier below
    1.0, a terminal agent can survive past their originally scheduled
    day, which is the intended effect of a dial that exists to slow
    death down, not just switch it off.
    """
    ailment = health.current_ailment
    if ailment is None or ailment.kind is not AilmentKind.TERMINAL:
        return False
    if ailment.scheduled_death_day is None or day < ailment.scheduled_death_day:
        return False
    return roll_death(rng, base_probability=1.0, dials=dials)


def check_chronic_death(rng: random.Random, health: HealthState, dials: PopulationDials) -> bool:
    """Untreated-chronic death path: daily hazard for an untreated medium/long ailment.

    Pharmacy cannot treat medium/long at genesis, so ``treated`` is
    always False for them today - the guard is here so a future
    hospital tool that can treat them doesn't need this module changed,
    just ``ailment.treated`` flipped to True by the treatment code path.
    """
    ailment = health.current_ailment
    if ailment is None or ailment.treated:
        return False
    hazard = CHRONIC_UNTREATED_DAILY_HAZARD.get(ailment.kind)
    if hazard is None:
        return False
    return roll_death(rng, base_probability=hazard, dials=dials)


def check_elderly_death(rng: random.Random, age_years: float, dials: PopulationDials) -> bool:
    """85+ hazard: independent of sickness, checked once per game-day."""
    if age_years < ELDERLY_HAZARD_AGE_YEARS:
        return False
    return roll_death(rng, base_probability=ELDERLY_DAILY_HAZARD, dials=dials)


DORMANCY_CAUSES = {
    "spent": "spent its last cent thinking - a debit left the wallet at $0",
    "swept": "found at $0 by the tick sweep, with no energy bought since",
    "at_work": "ran out of thinking money mid-shift, and the day job stopped",
    "unstated": "not stated by the caller",
}


def mark_dormant(
    dormancy: DormancyState,
    now: GameClock,
    cause: str = DORMANCY_CAUSES["unstated"],
) -> tuple[DormancyState, list[LifeEvent]]:
    """Enter dormancy. Called by the ledger when a debit leaves an agent at $0.

    A no-op if already dormant, so a second $0-triggering debit while
    already dormant doesn't reset the 24-hour countdown.

    `cause` is written into the event because the report has a column for
    it and had nothing to put there: every dormancy in the 50-agent run
    reads `None`. A caller that says nothing still records "not stated",
    which is a fact about the code rather than a blank about the town.
    """
    if dormancy.is_dormant:
        return dormancy, []
    updated = dormancy.model_copy(
        update={"is_dormant": True, "dormant_since_hours": now.total_hours}
    )
    return updated, [LifeEvent(
        type="health.agent.dormant",
        payload={"at_hours": now.total_hours, "reason": cause},
    )]


def check_dormancy_death(
    rng: random.Random, dormancy: DormancyState, now: GameClock, dials: PopulationDials
) -> bool:
    """USD-zero unrevived death path: 24 game-hours dormant with no revival."""
    if not dormancy.is_dormant or dormancy.dormant_since_hours is None:
        return False
    elapsed = now.total_hours - dormancy.dormant_since_hours
    if elapsed < DORMANCY_DEATH_HOURS:
        return False
    return roll_death(rng, base_probability=1.0, dials=dials)


async def revive(
    payer_id: str, target_id: str, dormancy: DormancyState, economy: EconomyPort, now: GameClock
) -> tuple[DormancyState, list[LifeEvent]]:
    """Any agent pays REVIVAL_COST_SIM to revive a dormant agent.

    DESIGN.md section 3: "any agent can revive them by paying 10,000
    sim" (re-rated 2026-08-01 from the original 100,000 sim figure).
    The fee converts to USD credit for the target at the same rate the
    government store uses (10,000 sim -> $1), so revival is literally
    buying the dormant agent $1 of API runway. Raises InsufficientFundsError
    (from economy.debit_sim) if the payer can't cover it; the target stays
    dormant in that case.
    """
    if not dormancy.is_dormant:
        raise ValueError(f"{target_id} is not dormant, nothing to revive")
    await economy.debit_sim(payer_id, REVIVAL_COST_SIM, reason="revival_payment")
    await economy.credit_usd_cents(target_id, REVIVAL_USD_CREDIT_CENTS, reason="revival_credit")
    updated = dormancy.model_copy(update={"is_dormant": False, "dormant_since_hours": None})
    event = LifeEvent(
        type="health.agent.revived",
        agent_id=target_id,
        payload={
            "paid_by": payer_id,
            "cost_sim": REVIVAL_COST_SIM,
            "credited_usd_cents": REVIVAL_USD_CREDIT_CENTS,
            "at_hours": now.total_hours,
        },
    )
    return updated, [event]


assert REVIVAL_COST_SIM // EXCHANGE_SIM_PER_USD_CENT == 100, (
    "revival fee no longer converts to exactly $1 of USD credit at the "
    "10,000-sim-per-dollar exchange rate DESIGN.md section 3 specifies"
)
