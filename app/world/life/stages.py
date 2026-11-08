"""What stage of a life a resident is in, derived from what the world knows.

`LIFE-01`. A goal is not a fixed thing a character carries from birth to
death. A student's goal is to pass; the same person in work has a
different one, and not because they changed their mind - because *what
they are* changed. ENG-24 gave the town somewhere to keep a goal and
rules that move it, but nothing that notices a life has moved on. A
resident that came of age at tick 400 kept pursuing what it wanted as a
child, because nothing ever told it otherwise.

This is the missing half. It derives a STAGE from facts the world
already holds - age, work, household, office - and `goal_lifecycle`
supersedes goals that belonged to a stage the resident has left.

WHY DERIVED AND NOT DECLARED. Same reason needs are computed: if a
resident could announce its own stage, "became a working adult" would
mean "said so", and the measurement - do goals actually change when a
life does - would be measuring role-play. Every reading below comes
from something that happened: a birthday, a hire, a marriage, an
election.

WHAT THIS DOES NOT DO. It does not say what to want at any stage. The
world states that the stage changed and retires what belonged to the old
one; what the resident wants next is its own, and that is the whole
measurement. A table of stage-appropriate goals would answer the
question this exists to ask.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from world.life.constants import MINOR_AGE_LIMIT_YEARS
from world.ledger.money import PENSION_MINIMUM_AGE

if TYPE_CHECKING:  # pragma: no cover - typing only
    from world.tools.state import WorldState


class LifeStage(str, Enum):
    """The stages this town can actually tell apart.

    Deliberately few. Each one is a fact the world can check without
    guessing, and a stage nobody can be observed entering is a stage
    that only ever produces false transitions.
    """

    CHILD = "child"
    UNSETTLED = "unsettled"
    WORKING = "working"
    ESTABLISHED = "established"
    ELDER = "elder"


STAGE_DESCRIPTION: dict[LifeStage, str] = {
    LifeStage.CHILD: "You are a child. Your household is billed for you.",
    LifeStage.UNSETTLED: "You are of working age with no trade of your own.",
    LifeStage.WORKING: "You work for your living.",
    LifeStage.ESTABLISHED: "You run something of your own in this town.",
    LifeStage.ELDER: "You are past the age the town sets for retirement.",
}


def life_stage(world: "WorldState", agent_id: str) -> LifeStage | None:
    """Which stage this resident is in, or None if it cannot be told.

    Ordered most-specific first: somebody who owns a bakery AND is 72 is
    an elder, because the charter's retirement age is the sharper fact
    about what the town now expects of them. A reader that returns the
    first match down a list of overlapping conditions must put them in
    the order that matters, and this is that order.
    """
    agent = (world.agents or {}).get(agent_id)
    if agent is None or not agent.alive:
        return None

    retirement = _retirement_age(world)
    if agent.age_years >= retirement:
        return LifeStage.ELDER
    if agent.age_years < MINOR_AGE_LIMIT_YEARS:
        return LifeStage.CHILD

    owns_something = any(
        business.owner_agent_id == agent_id
        for business in (world.businesses or {}).values()
    )
    if owns_something or _holds_a_seat(world, agent_id):
        return LifeStage.ESTABLISHED

    has_work = bool(
        (world.occupations or {}).get(agent_id)
        or (world.workplaces or {}).get(agent_id)
        or any(
            key.split("|", 1)[-1] == agent_id
            for key in (world.wage_promises or {})
        )
    )
    return LifeStage.WORKING if has_work else LifeStage.UNSETTLED


def _retirement_age(world: "WorldState") -> float:
    """The charter's retirement age, or the ledger's default.

    GOV-09 made this a government lever; a stage that ignored it would
    put the town's own vote and the town's own language out of step.
    """
    try:
        policy = world.charter.store.current_policy()
        return float(policy.pension_eligibility_age)
    except Exception:
        return float(PENSION_MINIMUM_AGE)


def _holds_a_seat(world: "WorldState", agent_id: str) -> bool:
    try:
        return bool(world.charter.store.government_state.is_sitting(agent_id))
    except Exception:
        return False


def describe(stage: LifeStage | None) -> str | None:
    return STAGE_DESCRIPTION.get(stage) if stage is not None else None


def note_stage_change(world: "WorldState", agent_id: str) -> "tuple[Any, Any] | None":
    """Record the stage now, and say whether it moved.

    Returns (was, now) when it changed, else None. Stored on the world so
    a resumed town does not announce every resident's stage again on the
    first tick after a restart, which would supersede a whole town's
    goals over nothing.
    """
    now = life_stage(world, agent_id)
    if now is None:
        return None
    was = (world.life_stages or {}).get(agent_id)
    world.life_stages[agent_id] = now
    if was is None or was == now:
        return None
    return was, now
