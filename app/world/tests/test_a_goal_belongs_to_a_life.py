"""A student's goal is to pass. The same person in work wants something else.

`LIFE-01`. ENG-24 gave the town somewhere to keep a goal and rules that
move it — a need spawns one, a tool credits one, the day boundary
expires the short ones. What none of that noticed is that **the person
changes**. A resident that came of age at tick 400 kept pursuing what it
had wanted as a child, because nothing ever told it otherwise.

That is not a resident changing its mind. It is a goal that stopped
belonging to anyone, and the difference matters to every measurement
downstream: `abandoned` and `superseded` are different facts about a
town, and reading one as the other makes "do residents stick at things"
unanswerable.

The stage is DERIVED, never declared — same rule as needs. If a resident
could announce "I am a working adult", the transition would mean "said
so", and this would measure role-play instead of a life.

**What is deliberately absent: any statement of what to want at any
stage.** The world says the stage changed and closes what belonged to
the old one. What comes next is the resident's, and a table of
age-appropriate ambitions would answer the exact question the goal
substrate exists to ask.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from world.life.models import AgentLifeRecord, Sex
from world.life.stages import LifeStage, life_stage
from world.tools.bootstrap import build_world
from world.tools.goal_lifecycle import (
    active_goals,
    refresh_and_spawn,
    supersede_goals_of_a_life_left_behind,
)
from world.tools.models import Business, Goal, GoalHorizon, GoalStatus

RESIDENT = "a_life"


@pytest_asyncio.fixture
async def town():
    world, registry = await build_world(genesis_tick=0, seed=13)
    world.agents[RESIDENT] = AgentLifeRecord(
        agent_id=RESIDENT, age_years=15.0, sex=Sex.FEMALE, household_id="h1"
    )
    world.ledger.open_agent(RESIDENT, starting_usd_cents=200, starting_sim=800)
    return world, registry


def _goal(world, text: str, horizon=GoalHorizon.WEEK) -> Goal:
    from world.tools.goal_lifecycle import _stage_now

    goal = Goal(
        goal_id=f"g_{len(world.goals)}", agent_id=RESIDENT, text=text,
        horizon=horizon, set_in_stage=_stage_now(world, RESIDENT),
    )
    world.goals[goal.goal_id] = goal
    return goal


def test_a_child_is_a_child(town):
    world, _ = town
    assert life_stage(world, RESIDENT) is LifeStage.CHILD


def test_coming_of_age_with_no_trade_is_unsettled(town):
    world, _ = town
    world.agents[RESIDENT] = world.agents[RESIDENT].model_copy(update={"age_years": 19.0})
    assert life_stage(world, RESIDENT) is LifeStage.UNSETTLED


def test_taking_work_makes_you_a_worker(town):
    world, _ = town
    world.agents[RESIDENT] = world.agents[RESIDENT].model_copy(update={"age_years": 19.0})
    world.occupations[RESIDENT] = "miller"
    assert life_stage(world, RESIDENT) is LifeStage.WORKING


def test_running_something_of_your_own_outranks_working(town):
    world, _ = town
    world.agents[RESIDENT] = world.agents[RESIDENT].model_copy(update={"age_years": 30.0})
    world.occupations[RESIDENT] = "miller"
    world.businesses["b_mine"] = Business(
        business_id="b_mine", name="Hers", business_type="mill",
        owner_agent_id=RESIDENT, location_id="Kasukabe Farmland",
    )
    assert life_stage(world, RESIDENT) is LifeStage.ESTABLISHED


def test_the_retirement_age_is_the_charters_not_a_constant(town):
    """GOV-09 made it a government lever. A stage that ignored the vote
    would put the town's own language out of step with its own law."""
    world, _ = town
    world.agents[RESIDENT] = world.agents[RESIDENT].model_copy(update={"age_years": 66.0})
    assert life_stage(world, RESIDENT) is not LifeStage.ELDER

    import asyncio

    async def lower_it():
        policy = await world.charter.store.get_policy()
        await world.charter.store.push_policy(
            policy.model_copy(update={"pension_eligibility_age": 65})
        )

    asyncio.run(lower_it())
    assert life_stage(world, RESIDENT) is LifeStage.ELDER


def test_a_childs_goal_is_superseded_when_it_comes_of_age(town):
    world, _ = town
    supersede_goals_of_a_life_left_behind(world, RESIDENT)
    school = _goal(world, "Finish at the schoolhouse")
    assert school.set_in_stage == "child"

    world.agents[RESIDENT] = world.agents[RESIDENT].model_copy(update={"age_years": 19.0})
    events = supersede_goals_of_a_life_left_behind(world, RESIDENT)

    assert school.status is GoalStatus.SUPERSEDED, (
        "a resident that came of age kept pursuing what it wanted as a child"
    )
    assert any(e.type == "agent.life.stage_changed" for e in events)
    assert any(e.type == "agent.goal.superseded" for e in events)


def test_superseded_is_not_abandoned(town):
    """It did not give the goal up. The goal stopped being its, and
    `abandoned` vs `superseded` are different facts about a town."""
    world, _ = town
    supersede_goals_of_a_life_left_behind(world, RESIDENT)
    school = _goal(world, "Finish at the schoolhouse")

    world.agents[RESIDENT] = world.agents[RESIDENT].model_copy(update={"age_years": 19.0})
    supersede_goals_of_a_life_left_behind(world, RESIDENT)

    assert school.status is not GoalStatus.ABANDONED
    assert "now unsettled" in " ".join(school.evidence)


def test_the_resident_is_told_once(town):
    """A goal vanishing without a word is how a town starts feeling
    arbitrary."""
    world, _ = town
    supersede_goals_of_a_life_left_behind(world, RESIDENT)
    _goal(world, "Finish at the schoolhouse")

    world.agents[RESIDENT] = world.agents[RESIDENT].model_copy(update={"age_years": 19.0})
    supersede_goals_of_a_life_left_behind(world, RESIDENT)

    queued = world.last_goal_outcome.get(RESIDENT) or []
    assert queued, "the goal was retired silently"
    assert "no longer a child" in queued[0][1]


def test_a_goal_set_in_the_new_life_survives(town):
    """Only goals of the life left behind close. Otherwise a change of
    stage would wipe everything, including what was just decided."""
    world, _ = town
    world.agents[RESIDENT] = world.agents[RESIDENT].model_copy(update={"age_years": 19.0})
    supersede_goals_of_a_life_left_behind(world, RESIDENT)
    fresh = _goal(world, "Find work at the mill")

    world.occupations[RESIDENT] = "miller"
    supersede_goals_of_a_life_left_behind(world, RESIDENT)

    assert fresh.status is GoalStatus.SUPERSEDED, "set as unsettled, now working"

    after = _goal(world, "Learn the trade properly")
    supersede_goals_of_a_life_left_behind(world, RESIDENT)
    assert after.status is GoalStatus.ACTIVE


def test_a_goal_the_world_cannot_place_is_left_alone(town):
    """Goals from before the world stamped stages have `set_in_stage`
    None. Superseding those would retire things at random."""
    world, _ = town
    supersede_goals_of_a_life_left_behind(world, RESIDENT)
    old = Goal(goal_id="g_old", agent_id=RESIDENT, text="From before", set_in_stage=None)
    world.goals["g_old"] = old

    world.agents[RESIDENT] = world.agents[RESIDENT].model_copy(update={"age_years": 19.0})
    supersede_goals_of_a_life_left_behind(world, RESIDENT)

    assert old.status is GoalStatus.ACTIVE


def test_a_life_horizon_goal_goes_too(town):
    """A horizon is about how long a thing takes, not about whether it
    still belongs to you. Somebody who wanted to finish school for life
    does not still want it as a retiree."""
    world, _ = town
    supersede_goals_of_a_life_left_behind(world, RESIDENT)
    forever = _goal(world, "Be the best in the school", horizon=GoalHorizon.LIFE)

    world.agents[RESIDENT] = world.agents[RESIDENT].model_copy(update={"age_years": 19.0})
    supersede_goals_of_a_life_left_behind(world, RESIDENT)

    assert forever.status is GoalStatus.SUPERSEDED


def test_nothing_happens_when_the_life_has_not_moved(town):
    world, _ = town
    supersede_goals_of_a_life_left_behind(world, RESIDENT)
    goal = _goal(world, "Finish at the schoolhouse")

    for _ in range(4):
        assert supersede_goals_of_a_life_left_behind(world, RESIDENT) == []
    assert goal.status is GoalStatus.ACTIVE


def test_the_turn_itself_supersedes_a_life_left_behind(town):
    """The wiring, not the function.

    `refresh_and_spawn` is what a turn calls. If the supersession is not
    inside it, every test above still passes and no resident is ever
    told anything - which is the ENG-16 failure, and the first draft of
    this test had exactly that hole: it asserted the STAGE of newly
    spawned goals, which is stamped at creation either way.
    """
    world, _ = town
    supersede_goals_of_a_life_left_behind(world, RESIDENT)
    school = _goal(world, "Finish at the schoolhouse")

    world.agents[RESIDENT] = world.agents[RESIDENT].model_copy(update={"age_years": 19.0})
    refresh_and_spawn(world, RESIDENT)

    assert school.status is GoalStatus.SUPERSEDED, (
        "a turn does not notice a life changing; the supersession is not "
        "wired into refresh_and_spawn"
    )


def test_the_supersession_runs_before_new_goals_are_spawned(town):
    """Order matters: a resident whose life just changed must not have
    this tick's needs spawn goals into the life it has left."""
    import inspect

    from world.tools import goal_lifecycle

    source = inspect.getsource(goal_lifecycle.refresh_and_spawn)
    superseded = source.index("supersede_goals_of_a_life_left_behind")
    spawned = source.index("spawn_goals_from_needs")
    assert superseded < spawned


def test_the_world_never_says_what_to_want_at_any_stage(town):
    """DESIGN.md section 4: told the prices and the rules, never the
    strategy. A table of age-appropriate ambitions would answer the
    question the goal substrate exists to ask."""
    from world.life.soul_seed import law_of_the_town_rules
    from world.life.stages import STAGE_DESCRIPTION

    text = (" ".join(STAGE_DESCRIPTION.values()) + " " +
            " ".join(law_of_the_town_rules())).lower()
    for coaching in (
        "you should", "you must find", "it is time to", "now you need to",
        "a good goal", "consider becoming", "try to become", "aim to",
        "your goal should",
    ):
        assert coaching not in text, f"the town now coaches: {coaching!r}"
