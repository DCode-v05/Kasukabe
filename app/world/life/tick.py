"""Where health.py, death.py, aging.py, household.py and population.py
meet one agent, once - the ONE per-agent life tick both composition
roots run.

ECON-22. There were two. This module was called by `wiring.py`; a second
copy lived inline in `run_genesis.py`'s `run_life_ticks`, and they had
already diverged. The inline copy reacted to `agent.aged_to_adult` by
running the coming-of-age transition (ending parent billing, moving the
household) and settled the estate and unregistered the dead from the
scheduler when somebody died. This module did neither. So on the
`world.main` root a minor who crossed eighteen changed nothing at all -
the parent kept funding their thinking for ever - and a death left the
corpse on the scheduler's roster with its money still in its own name.
Two implementations of one rule is one implementation and one bug that
nobody has found yet; the ONLY fix is that there is one.

The reason there were two is that the second root needed effects this
module had no way to express: coming of age has to await a guardianship
port, and a full death has to await an estate settlement. So both tick
functions are async and take those two effects as optional hooks. With
no hooks passed they behave exactly as the sync originals did, which is
what keeps every pure unit test in life/tests honest; with hooks passed
they are the runner's version. Neither root keeps a copy.

Ordering within a day (see run_daily_health_tick): recovery is checked
before death, so an ailment that expires exactly on its own hazard-check
day recovers instead of still being eligible to kill; death paths are
checked terminal -> chronic -> elderly, first hit wins, because an
agent can only die once and terminal is the most specific/certain cause
in play; a fresh illness is only rolled if the agent is still alive and
healthy after all of the above.

Ordering within an hour (see run_hourly_life_tick): age first, then the
coming-of-age transition the aging may have triggered, then the dormancy
death check - a resident who turns eighteen on the same hour their
dormancy deadline expires becomes an adult and then dies as one, because
the billing relationship they were in when the clock ran out is a fact
about the estate that follows.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable

from world.life import aging, death, health
from world.life.clock import GameClock
from world.life.models import AgentLifeRecord, DeathCause, LifeEvent, PopulationDials

# `world.life.household.bring_of_age`'s exact signature: the agent that
# just turned eighteen, and the game-hour it happened at. Returns the
# agent as it stands afterwards plus whatever the transition emitted.
ComingOfAgeHook = Callable[
    [AgentLifeRecord, float], Awaitable[tuple[AgentLifeRecord, list[LifeEvent]]]
]

# A death that has consequences beyond the record: estate settlement,
# scheduler unregistration, anything else a root owns. Same return shape
# as `apply_death` below, which is the default when no hook is given.
DeathHook = Callable[
    [AgentLifeRecord, DeathCause], Awaitable[tuple[AgentLifeRecord, list[LifeEvent]]]
]


def apply_death(
    agent: AgentLifeRecord, cause: DeathCause
) -> tuple[AgentLifeRecord, list[LifeEvent]]:
    """Mark an agent dead and emit both events app/CONVENTIONS.md's table
    lists for a health-caused death: the health-domain event (cause
    detail, for health dashboards) and the agent-domain event (existence
    change, for population counts and the game view's roster).

    The record half only. A root that also has to settle an estate, close
    a ledger account or take the dead off the scheduler passes a
    `DeathHook` to the tick functions below instead of calling this - see
    the module docstring for why that seam exists.
    """
    updated = agent.model_copy(update={"alive": False, "death_cause": cause})
    events = [
        LifeEvent(
            type="health.agent.died", agent_id=agent.agent_id, payload={"cause": cause.value}
        ),
        LifeEvent(type="agent.died", agent_id=agent.agent_id, payload={"cause": cause.value}),
    ]
    return updated, events


async def _die(
    agent: AgentLifeRecord, cause: DeathCause, on_death: DeathHook | None
) -> tuple[AgentLifeRecord, list[LifeEvent]]:
    """One death, through the caller's hook if it has one."""
    if on_death is None:
        return apply_death(agent, cause)
    return await on_death(agent, cause)


async def run_daily_health_tick(
    rng: random.Random,
    agent: AgentLifeRecord,
    day: int,
    dials: PopulationDials,
    *,
    on_death: DeathHook | None = None,
) -> tuple[AgentLifeRecord, list[LifeEvent]]:
    if not agent.alive:
        return agent, []

    events: list[LifeEvent] = []
    current_health, recovery_events = health.advance_recovery(agent.agent_id, agent.health, day)
    events.extend(recovery_events)
    agent = agent.model_copy(update={"health": current_health})

    if death.check_terminal_death(rng, agent.health, day, dials):
        agent, death_events = await _die(agent, DeathCause.TERMINAL, on_death)
        return agent, [*events, *death_events]
    if death.check_chronic_death(rng, agent.health, dials):
        agent, death_events = await _die(agent, DeathCause.UNTREATED_CHRONIC, on_death)
        return agent, [*events, *death_events]
    if death.check_elderly_death(rng, agent.age_years, dials):
        agent, death_events = await _die(agent, DeathCause.ELDERLY_HAZARD, on_death)
        return agent, [*events, *death_events]

    new_health, onset_events = health.roll_daily_health(rng, agent.agent_id, agent.health, day)
    events.extend(onset_events)
    agent = agent.model_copy(update={"health": new_health})
    return agent, events


async def run_hourly_life_tick(
    rng: random.Random,
    agent: AgentLifeRecord,
    now: GameClock,
    elapsed_hours: float,
    dials: PopulationDials,
    *,
    on_coming_of_age: ComingOfAgeHook | None = None,
    on_death: DeathHook | None = None,
) -> tuple[AgentLifeRecord, list[LifeEvent]]:
    if not agent.alive:
        return agent, []

    events: list[LifeEvent] = []
    agent, aging_events = aging.advance_age(agent, elapsed_hours)
    events.extend(aging_events)

    # Aging fires `agent.aged_to_adult` and stops there. Until a caller
    # consumed that event, a minor crossing eighteen changed nothing at
    # all: the parent kept paying for their thinking and the household
    # never moved. A root with no hook keeps that behaviour, which is why
    # this is the one line ECON-22 was really about.
    if on_coming_of_age is not None and any(
        event.type == "agent.aged_to_adult" for event in aging_events
    ):
        agent, coming_of_age_events = await on_coming_of_age(agent, now.total_hours)
        events.extend(coming_of_age_events)

    if death.check_dormancy_death(rng, agent.dormancy, now, dials):
        agent, death_events = await _die(agent, DeathCause.USD_ZERO_UNREVIVED, on_death)
        events.extend(death_events)

    return agent, events
