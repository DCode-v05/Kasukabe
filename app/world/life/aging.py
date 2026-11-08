"""Aging: minors age 1 year per game-hour until 18, adults static.

DESIGN.md section 4/5, verbatim. At 1 year per game-hour a newborn
reaches 18 in 18 game-hours (0.75 game-days) - deliberately fast, so
that birth -> childhood -> working age is something the ~6-game-day
budget can actually show happening, instead of a mechanic nobody ever
sees fire. Once an agent crosses 18 this module stops touching their
age permanently ("adults static") - there is no further aging clock for
adults, so the only way an agent is ever subject to the 85+ elderly
hazard (death.py) is if genesis seeded them there already.
"""

from __future__ import annotations

from world.life.constants import MINOR_AGE_LIMIT_YEARS, YEARS_AGED_PER_GAME_HOUR
from world.life.models import AgentLifeRecord, LifeEvent


def advance_age(
    agent: AgentLifeRecord, elapsed_hours: float
) -> tuple[AgentLifeRecord, list[LifeEvent]]:
    """Age a minor by ``elapsed_hours`` of game time. No-op for adults.

    Caps exactly at MINOR_AGE_LIMIT_YEARS rather than overshooting past
    18, and fires agent.aged_to_adult the tick that happens on, so a
    caller polling elapsed_hours in whatever-sized steps the tick loop
    uses still gets exactly one adulthood transition per agent.
    """
    if elapsed_hours < 0:
        raise ValueError(f"cannot advance age by a negative amount ({elapsed_hours})")
    if not agent.is_minor:
        return agent, []

    new_age = agent.age_years + elapsed_hours * YEARS_AGED_PER_GAME_HOUR
    crossed_into_adulthood = new_age >= MINOR_AGE_LIMIT_YEARS
    if crossed_into_adulthood:
        new_age = MINOR_AGE_LIMIT_YEARS

    updated = agent.model_copy(update={"age_years": new_age})
    if not crossed_into_adulthood:
        return updated, []

    updated = updated.model_copy(update={"sponsor_parent_id": None})
    event = LifeEvent(
        type="agent.aged_to_adult", agent_id=agent.agent_id, payload={"age_years": new_age}
    )
    return updated, [event]
