"""Per-agent, per-game-day health incidence (DESIGN.md section 5).

One roll per healthy agent per game-day, partitioned so the four
incidence rates are mutually exclusive (an agent can't roll into two
ailments on the same day): draw one uniform random number and walk it
through ordered bands, rarest first. An already-sick agent does not
roll - DESIGN.md never describes stacking illnesses, and there is no
defined interaction between two simultaneous ailments' work-capacity or
treatment rules, so "already sick" is treated as immune to new onset
until the current ailment resolves (recovery or death).
"""

from __future__ import annotations

import random

from world.life.constants import (
    AILMENT_DAILY_INCIDENCE,
    AILMENT_DURATION_DAYS_LONG,
    AILMENT_DURATION_DAYS_SHORT,
    AILMENT_DURATION_MEDIUM_MAX_DAYS,
    AILMENT_DURATION_MEDIUM_MIN_DAYS,
    PHARMACY_SHORT_TREATMENT_COST_SIM,
    PHARMACY_TREATABLE_AILMENTS,
    TERMINAL_DEATH_WINDOW_DAYS,
    WORK_CAPACITY_BY_AILMENT,
    WORK_CAPACITY_HEALTHY,
)
from world.life.models import Ailment, AgentLifeRecord, AilmentKind, HealthState, LifeEvent
from world.life.ports import EconomyPort

INCIDENCE_ROLL_ORDER: tuple[AilmentKind, ...] = (
    AilmentKind.TERMINAL,
    AilmentKind.LONG,
    AilmentKind.MEDIUM,
    AilmentKind.SHORT,
)


class TreatmentUnavailableError(RuntimeError):
    """Pharmacy visit attempted for an ailment genesis has no cure for.

    DESIGN.md section 5: "pharmacy for short; anything worse needs
    infrastructure the town doesn't have yet." Section 1 lists a
    hospital as deliberately absent from genesis buildings, so medium,
    long, and terminal ailments have no treatment path until the town
    builds one.
    """


def onset_ailment(rng: random.Random, kind: AilmentKind, onset_day: int) -> Ailment:
    """Build a freshly-onset ailment of the given kind."""
    if kind is AilmentKind.SHORT:
        return Ailment(kind=kind, onset_day=onset_day, duration_days=AILMENT_DURATION_DAYS_SHORT)
    if kind is AilmentKind.MEDIUM:
        duration = rng.randint(AILMENT_DURATION_MEDIUM_MIN_DAYS, AILMENT_DURATION_MEDIUM_MAX_DAYS)
        return Ailment(kind=kind, onset_day=onset_day, duration_days=duration)
    if kind is AilmentKind.LONG:
        return Ailment(kind=kind, onset_day=onset_day, duration_days=AILMENT_DURATION_DAYS_LONG)
    death_day = onset_day + rng.randint(*TERMINAL_DEATH_WINDOW_DAYS)
    return Ailment(kind=AilmentKind.TERMINAL, onset_day=onset_day, scheduled_death_day=death_day)


def roll_daily_health(
    rng: random.Random, agent_id: str, health: HealthState, day: int
) -> tuple[HealthState, list[LifeEvent]]:
    """Roll one agent's health incidence for a single game-day.

    Returns the (possibly unchanged) health state and any events fired.
    No-ops if the agent is already sick.
    """
    if health.current_ailment is not None:
        return health, []

    draw = rng.random()
    cumulative = 0.0
    for kind in INCIDENCE_ROLL_ORDER:
        cumulative += AILMENT_DAILY_INCIDENCE[kind]
        if draw < cumulative:
            ailment = onset_ailment(rng, kind, day)
            event = LifeEvent(
                type="health.ailment.onset",
                agent_id=agent_id,
                payload={"kind": kind.value, "onset_day": day},
            )
            return health.model_copy(update={"current_ailment": ailment}), [event]

    return health, []


def is_recovered(health: HealthState, day: int) -> bool:
    """True if the agent's current ailment has run its natural course.

    Terminal ailments never "recover" through this path - they either
    get to their scheduled_death_day (death.py) or the operator zeroes
    the death-hazard multiplier and they simply stay terminal forever.
    """
    ailment = health.current_ailment
    if ailment is None or ailment.kind is AilmentKind.TERMINAL:
        return False
    if ailment.duration_days is None:
        return False
    return day >= ailment.onset_day + ailment.duration_days


def advance_recovery(
    agent_id: str, health: HealthState, day: int
) -> tuple[HealthState, list[LifeEvent]]:
    """Clear a current ailment if it has run its course by ``day``."""
    if not is_recovered(health, day):
        return health, []
    kind = health.current_ailment.kind  # type: ignore[union-attr]
    event = LifeEvent(
        type="health.ailment.recovered", agent_id=agent_id, payload={"kind": kind.value}
    )
    return health.model_copy(update={"current_ailment": None}), [event]


def work_capacity(health: HealthState) -> float:
    """Fraction of normal work capacity the agent currently has."""
    if health.current_ailment is None:
        return WORK_CAPACITY_HEALTHY
    return WORK_CAPACITY_BY_AILMENT[health.current_ailment.kind]


def pharmacy_refusal(agent: AgentLifeRecord, day: int) -> str | None:
    """Why this resident cannot buy treatment here today, in the words a
    refusal should use - or None if they can.

    ECON-12. The pharmacy is the one genesis building whose advertised
    purpose ("Buy treatment for short illnesses", tools/primitives/
    places.py) has never had a tool behind it, and the handler that
    finally provides one must not re-derive the rule: the day it words
    "the pharmacy only covers short ailments" from its own copy of
    PHARMACY_TREATABLE_AILMENTS is the day the counter and the sign start
    disagreeing, which is how `buy_energy`'s description came to quote an
    exchange rate that had changed months earlier. Both the guard inside
    `treat_at_pharmacy` and any caller that wants to refuse BEFORE
    charging read this one function.

    The price is named in every refusal that is about money, and derived
    from the constant rather than typed, for the same reason.
    """
    if not agent.alive:
        return f"{agent.agent_id} is dead; there is nothing left to treat"
    ailment = agent.health.current_ailment
    if ailment is None:
        return f"{agent.agent_id} is not ill; there is nothing to treat"
    if ailment.kind not in PHARMACY_TREATABLE_AILMENTS:
        return (
            f"{ailment.kind.value} ailments have no genesis treatment path "
            "(DESIGN.md section 5): the pharmacy only covers short illnesses, "
            "and anything worse needs infrastructure the town has not built"
        )
    if is_recovered(agent.health, day):
        return (
            f"{agent.agent_id}'s illness has already run its course and clears "
            f"on its own; {PHARMACY_SHORT_TREATMENT_COST_SIM} sim-currency "
            "would buy nothing"
        )
    return None


async def treat_at_pharmacy(
    agent: AgentLifeRecord, day: int, economy: EconomyPort
) -> tuple[AgentLifeRecord, list[LifeEvent]]:
    """Cure a short ailment at the pharmacy. Raises for anything else.

    DESIGN.md section 5: only short ailments are pharmacy-treatable at
    genesis. Debits PHARMACY_SHORT_TREATMENT_COST_SIM before curing so a
    failed debit (insufficient funds) leaves the ailment in place.

    ECON-12. This took a bare `HealthState` and returned one, which is
    two defects in one signature and the reason it was never reachable
    from anything holding real world state. It could not tell whether the
    resident was ALIVE, so a corpse could be charged five hundred sim for
    a cure; and it could not tell WHAT DAY it was, so a resident whose
    short illness had already run its full course - `advance_recovery`
    clears those for free at the very next day boundary - could be sold a
    cure for an illness they no longer had. Both are now refused before
    any money moves, through `pharmacy_refusal` above, and the whole
    updated record comes back so a caller writes one object into
    `world.agents` rather than reassembling the agent around a health
    field it has to know to replace.
    """
    refusal = pharmacy_refusal(agent, day)
    if refusal is not None:
        ailment = agent.health.current_ailment
        if ailment is not None and ailment.kind not in PHARMACY_TREATABLE_AILMENTS:
            raise TreatmentUnavailableError(refusal)
        raise ValueError(refusal)

    ailment = agent.health.current_ailment
    assert ailment is not None
    await economy.debit_sim(
        agent.agent_id, PHARMACY_SHORT_TREATMENT_COST_SIM, reason="pharmacy_treatment"
    )
    event = LifeEvent(
        type="health.ailment.treated",
        agent_id=agent.agent_id,
        payload={
            "kind": ailment.kind.value,
            "cost_sim": PHARMACY_SHORT_TREATMENT_COST_SIM,
            "day": day,
        },
    )
    cured = agent.health.model_copy(update={"current_ailment": None})
    return agent.model_copy(update={"health": cured}), [event]
