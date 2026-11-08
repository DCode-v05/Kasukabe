"""A resident that wants something can say so, and the town can tell.

`ENG-24`. The review's charge was not that goals were badly modelled -
it was that there was **no substrate at all**. `Intention` is one
sentence with no horizon, no priority, no progress and no way to be
superseded, so "walk to the mill" and "become a teacher" were the same
object, nothing could outrank anything, a goal was either absent or
present and never half-done, and a resident could not change its mind
on the record. A town measured on "agents that develop their own goals"
had nowhere to keep one.

The substrate is three parts and each is tested here for the property
that would make it worthless if it broke:

* **needs are computed, never declared** - if a resident could assert
  its own hunger, every downstream figure would measure the model's
  willingness to role-play rather than the town;
* **a pressing need produces a goal, and relief closes it** - without
  the second half the goal list is an append-only pile;
* **progress arrives from acts** - a `done_when` tool that succeeds
  moves the goal by itself, so a goal can finish without the resident
  ever claiming it did;
* **a refusal becomes a belief** - the reason the same wall got walked
  into every tick is that there was nowhere to write down that it was a
  wall;
* **goals expire with their horizon** - `now` and `today` do not
  survive the night, `week` and `life` do, which is the only thing that
  makes a horizon more than a longer word.

Each of these fails if its fix is reverted; that was checked by
reverting it.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from world.retrieval_depth import format_goals
from world.tools.bootstrap import build_world
from world.tools.goal_lifecycle import (
    active_goals,
    credit_tool_success,
    expire_goals,
    note_refusal_belief,
    refresh_and_spawn,
    relieve_need_goals,
    spawn_goals_from_needs,
)
from world.tools.models import Goal, GoalHorizon, GoalOrigin, GoalStatus, Need, NeedKind
from world.tools.needs import (
    NEED_PRESSING,
    compute_needs,
    pressing_needs,
    refresh_needs,
    shown_needs,
)
from world.tools.state import WorldState


# `build_world` builds the town, not its population - the runner loads
# genesis separately - so the fixture puts one resident in it. Everything
# under test reads facts off the world about a named agent, and one is
# enough to make each of those readings observable.
RESIDENT = "a_test_resident"


@pytest_asyncio.fixture
async def world() -> WorldState:
    from world.life.models import AgentLifeRecord, Sex

    state, _registry = await build_world(genesis_tick=0, seed=7)
    state.agents[RESIDENT] = AgentLifeRecord(
        agent_id=RESIDENT, age_years=34.0, sex=Sex.FEMALE, household_id="h_test"
    )
    state.ledger.open_agent(RESIDENT, starting_usd_cents=200, starting_sim=500)
    return state


def _somebody(world: WorldState) -> str:
    assert world.agents, "the fixture produced no residents to test against"
    return sorted(world.agents)[0]


# ── the state exists at all ──────────────────────────────────────────


def test_a_world_has_somewhere_to_keep_goals(world):
    """The finding in its plainest form: the three fields exist."""
    assert isinstance(world.goals, dict)
    assert isinstance(world.needs, dict)
    assert isinstance(world.beliefs, dict)


def test_a_goal_is_more_than_a_sentence():
    """Every axis `Intention` lacked, which is why this finding exists."""
    goal = Goal(goal_id="g1", agent_id="a1", text="Open a bakery")
    assert goal.horizon is GoalHorizon.TODAY
    assert goal.priority == 3
    assert goal.progress == 0.0
    assert goal.status is GoalStatus.ACTIVE
    assert goal.origin is GoalOrigin.PERSONA
    # And it can be superseded, which is how changing your mind is
    # distinguished from giving up.
    assert GoalStatus.SUPERSEDED in set(GoalStatus)


def test_a_goal_cannot_be_a_paragraph():
    """160 characters, because it rides in every prompt after this one."""
    with pytest.raises(Exception):
        Goal(goal_id="g", agent_id="a", text="x" * 161)


# ── needs are the world's reading, not the resident's ────────────────


def test_needs_are_computed_from_facts_the_world_holds(world):
    needs = compute_needs(world, _somebody(world))
    assert set(needs) == {k.value for k in NeedKind}
    for need in needs.values():
        assert 0.0 <= need.level <= 1.0


def test_the_same_world_computes_the_same_needs_twice(world):
    """Determinism: a need that wandered would make its goal unattributable."""
    who = _somebody(world)
    first = compute_needs(world, who)
    second = compute_needs(world, who)
    assert {k: v.level for k, v in first.items()} == {k: v.level for k, v in second.items()}


def test_hunger_is_read_off_the_ladder_not_asserted(world):
    """The FOOD need tracks days_since_meal, the town's own starvation clock."""
    from world.life.upkeep import HungerState

    who = _somebody(world)
    world.hunger_by_agent = {}
    assert compute_needs(world, who)["food"].level == 0.0
    world.hunger_by_agent = {who: HungerState(days_since_meal=3)}
    assert compute_needs(world, who)["food"].level == pytest.approx(1.0)


def test_a_resident_nobody_speaks_to_notices(world):
    """SOCIAL reads the utterance log by the keys bootstrap actually writes.

    Guessing at `heard_by` or `to_agent_id` - names nothing writes -
    would have pinned this need at its ceiling for everyone forever,
    which is the EVAL-01 failure repeated.
    """
    who = _somebody(world)
    world.current_tick = 100
    world.utterances = []
    assert compute_needs(world, who)["social"].level == pytest.approx(1.0)
    world.utterances = [
        {"tick": 100, "speaker_agent_id": "someone_else", "listener_agent_id": who,
         "addressed_agent_ids": [who]}
    ]
    assert compute_needs(world, who)["social"].level == pytest.approx(0.0)


def test_a_need_remembers_how_long_it_has_pressed(world):
    from world.life.upkeep import HungerState

    who = _somebody(world)
    world.current_tick = 10
    world.hunger_by_agent = {who: HungerState(days_since_meal=3)}
    refresh_needs(world, who)
    assert world.needs[who]["food"].since_tick == 10
    world.current_tick = 25
    refresh_needs(world, who)
    assert world.needs[who]["food"].since_tick == 10, "a standing need restarted its clock"


# ── a need produces a goal, and relief closes it ─────────────────────


def test_a_pressing_need_produces_a_goal(world):
    from world.life.upkeep import HungerState

    who = _somebody(world)
    world.hunger_by_agent = {who: HungerState(days_since_meal=3)}
    refresh_needs(world, who)
    events = spawn_goals_from_needs(world, who)
    hunger_goals = [g for g in active_goals(world, who) if g.done_when.get("need") == "food"]
    assert hunger_goals, "a starving resident was given nothing to want"
    assert hunger_goals[0].origin is GoalOrigin.NEED
    assert hunger_goals[0].priority == 5
    assert any(e.type == "agent.goal.set" for e in events), "a goal appeared with no trace row"


def test_a_standing_need_does_not_produce_a_second_goal(world):
    from world.life.upkeep import HungerState

    who = _somebody(world)
    world.hunger_by_agent = {who: HungerState(days_since_meal=3)}
    for _ in range(5):
        refresh_and_spawn(world, who)
    food = [g for g in active_goals(world, who) if g.done_when.get("need") == "food"]
    assert len(food) == 1, f"five hungry ticks produced {len(food)} identical goals"


def test_eating_closes_the_goal_without_the_resident_saying_so(world):
    from world.life.upkeep import HungerState

    who = _somebody(world)
    world.hunger_by_agent = {who: HungerState(days_since_meal=3)}
    refresh_and_spawn(world, who)
    goal = [g for g in active_goals(world, who) if g.done_when.get("need") == "food"][0]
    world.hunger_by_agent = {who: HungerState(days_since_meal=0)}
    refresh_needs(world, who)
    events = relieve_need_goals(world, who)
    assert goal.status is GoalStatus.DONE
    assert goal.progress == 1.0
    assert any(e.type == "agent.goal.progressed" for e in events)


# ── progress arrives from acts ───────────────────────────────────────


def test_a_tool_that_succeeds_moves_the_goal_by_itself(world):
    who = _somebody(world)
    goal = Goal(
        goal_id="g_sell", agent_id=who, text="Sell three loaves",
        done_when={"tool": "world_sell", "times": 3},
    )
    world.goals[goal.goal_id] = goal
    credit_tool_success(world, who, "world_sell")
    assert goal.progress == pytest.approx(1 / 3)
    assert goal.status is GoalStatus.ACTIVE
    credit_tool_success(world, who, "world_sell")
    credit_tool_success(world, who, "world_sell")
    assert goal.progress == pytest.approx(1.0)
    assert goal.status is GoalStatus.DONE, "three sales did not finish a three-sale goal"


def test_the_wrong_tool_moves_nothing(world):
    who = _somebody(world)
    goal = Goal(goal_id="g", agent_id=who, text="Sell", done_when={"tool": "world_sell"})
    world.goals[goal.goal_id] = goal
    credit_tool_success(world, who, "world_cook")
    assert goal.progress == 0.0


def test_another_residents_success_moves_nothing(world):
    who = _somebody(world)
    goal = Goal(goal_id="g", agent_id=who, text="Sell", done_when={"tool": "world_sell"})
    world.goals[goal.goal_id] = goal
    credit_tool_success(world, "somebody_else", "world_sell")
    assert goal.progress == 0.0


# ── a refusal is evidence ────────────────────────────────────────────


def test_being_refused_leaves_something_behind(world):
    who = _somebody(world)
    events = note_refusal_belief(world, who, "world_cook", "not a kitchen", "sector:farmland")
    mine = [b for b in world.beliefs.values() if b.agent_id == who]
    assert mine, "a refused resident learned nothing"
    assert "world_cook" in mine[0].statement
    assert any(e.type == "agent.belief.updated" for e in events)


def test_being_refused_twice_raises_confidence_rather_than_the_count(world):
    who = _somebody(world)
    note_refusal_belief(world, who, "world_cook", "not a kitchen", "sector:farmland")
    first = [b for b in world.beliefs.values() if b.agent_id == who][0].confidence
    note_refusal_belief(world, who, "world_cook", "not a kitchen", "sector:farmland")
    mine = [b for b in world.beliefs.values() if b.agent_id == who]
    assert len(mine) == 1, "the same wall was written down twice"
    assert mine[0].confidence > first


def test_a_belief_never_reaches_certainty(world):
    """A rule can change; a belief pinned at 1.0 could never be revised."""
    who = _somebody(world)
    for _ in range(20):
        note_refusal_belief(world, who, "world_cook", "not a kitchen", "sector:farmland")
    assert [b for b in world.beliefs.values() if b.agent_id == who][0].confidence < 1.0


# ── horizons ─────────────────────────────────────────────────────────


def test_todays_goals_do_not_survive_the_night(world):
    who = _somebody(world)
    for horizon in GoalHorizon:
        world.goals[f"g_{horizon.value}"] = Goal(
            goal_id=f"g_{horizon.value}", agent_id=who, text=f"a {horizon.value} goal",
            horizon=horizon,
        )
    events = expire_goals(world)
    assert world.goals["g_now"].status is GoalStatus.EXPIRED
    assert world.goals["g_today"].status is GoalStatus.EXPIRED
    assert world.goals["g_week"].status is GoalStatus.ACTIVE, "a week goal died in a night"
    assert world.goals["g_life"].status is GoalStatus.ACTIVE, "a life goal died in a night"
    assert len(events) == 2


# ── the resident is told ─────────────────────────────────────────────


def test_the_perception_says_what_you_want_and_what_is_pressing():
    block = format_goals(
        goals=[{"text": "Get something to eat.", "horizon": "now", "progress": 0.0}],
        needs=[{"kind": "food", "level": 0.9}],
        beliefs=[{"statement": "world_cook does not work here"}],
    )
    assert block is not None
    assert "starving" in block
    assert "Get something to eat." in block
    assert "world_cook does not work here" in block
    # No arithmetic put in front of the resident: the float is the
    # world's bookkeeping, not something a person feels.
    assert "0.9" not in block


def test_a_resident_under_no_pressure_is_told_nothing():
    """An empty section every tick trains a resident to skip the place
    where its goals appear."""
    assert format_goals(goals=[], needs=[], beliefs=[]) is None
    assert format_goals() is None


def test_the_block_stays_small():
    """It rides in every prompt; it cannot be allowed to grow."""
    block = format_goals(
        goals=[{"text": f"goal {i}", "horizon": "today", "progress": 0.5} for i in range(9)],
        needs=[{"kind": k.value, "level": 0.9} for k in NeedKind],
        beliefs=[{"statement": f"belief {i}"} for i in range(9)],
    )
    assert block is not None
    assert block.count("goal ") <= 3
    assert block.count("belief ") <= 3
    assert len(block.split("\n")) == 3


def test_progress_is_shown_only_once_there_is_some():
    fresh = format_goals(goals=[{"text": "Sell bread", "horizon": "today", "progress": 0.0}])
    assert "%" not in fresh
    moved = format_goals(goals=[{"text": "Sell bread", "horizon": "today", "progress": 0.5}])
    assert "50%" in moved


# ── and the wiring, without which none of the above ships ────────────


def test_the_goal_verbs_are_registered_and_free():
    """A tool nobody can call is a tool that does not exist.

    ENG-16's lesson, stamped-fixed-with-no-commit: the substrate can be
    perfect and still reach zero residents. And priced at zero on
    purpose - the measurement is goals-set against goals-reached, and a
    resident charged per goal would set fewer and the ratio would
    improve without the town improving at all.
    """
    from world.ledger.action_costs import SCHEDULE_TABLE
    from world.tools.bootstrap import PRIMITIVE_MODULES
    from world.tools.primitives import goals as goals_module

    assert goals_module in PRIMITIVE_MODULES, "the goal tools are not in the registry"
    named = {spec.name for spec in goals_module.build_specs()}
    assert named == {
        "set_goal", "update_goal", "retire_goal", "note_belief",
        # The read the sleep reflection goes through, rather than being
        # handed a private window onto world state.
        "recall_goals",
    }
    for name in named:
        assert name in SCHEDULE_TABLE, f"{name} has no price row"
        assert SCHEDULE_TABLE[name].base_cost == 0, f"{name} costs a resident to want things"


def test_set_goal_is_shown_in_full_every_turn():
    """ENG-03 trimmed the schema budget; a verb reduced to an index line
    costs a turn to find. `set_goal` is the entry point to the whole
    substrate, so it stays in full."""
    from world.tools.registry import ALWAYS_IN_FULL

    assert "set_goal" in ALWAYS_IN_FULL


def test_goal_events_reach_the_events_table():
    """CORE-24's split, which is the one this substrate could quietly die of.

    `agent.*` is not a recorded domain - `agent.moved` alone was the
    21 GB - so without an explicit allow every goal and belief would
    publish and never record, and a report could prove nothing.
    """
    from world.run_genesis import is_worth_recording

    for kind in (
        "agent.goal.set",
        "agent.goal.progressed",
        "agent.goal.retired",
        "agent.goal.superseded",
        "agent.belief.updated",
    ):
        assert is_worth_recording(kind), f"{kind} would publish and never record"
    # And the thing the allow-list must not undo.
    assert not is_worth_recording("agent.moved")
    assert not is_worth_recording("agent.perceived")


@pytest.mark.asyncio
async def test_a_hungry_resident_is_actually_told_so(tmp_path, monkeypatch):
    """The whole substrate, through the runner that every run uses.

    Written behaviourally on purpose. The first version of this test
    asserted `"goals=self.goal_lines(agent_id)" in inspect.getsource(...)`
    - which is the ECON-26 / CORE-23 pattern: a comment naming the call
    satisfies it, and it would have passed on a tree where the wiring
    was present and broken. It boots a town instead, starves somebody,
    and reads the block the resident would be sent.
    """
    from world.life.upkeep import HungerState
    from world.run_genesis import GenesisRunner, GenesisRunnerConfig

    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp",
        world_mode="embedded", world_port=0, start_at_dawn=True,
    ))
    # boot() + start_agents(), the sequence a real run does before it
    # ticks: `run_tick` asserts a gateway, and a booted-but-unlaunched
    # runner has none.
    await runner.boot(resume=False)
    await runner.start_agents()
    town = runner.world_state

    # Starve everybody, so whoever the scheduler picks is hungry. The
    # test must not choose the resident it then inspects - `dispatch_due_
    # agents` chooses, and the point is that the DISPATCH refreshes needs.
    for agent_id in town.agents:
        runner.hunger_by_agent[agent_id] = HungerState(
            days_since_meal=3, consecutive_unpaid_days=3
        )
    town.hunger_by_agent = runner.hunger_by_agent

    # Capture the perception the gateway is actually handed. Asserting on
    # `format_goals` alone would pass on a tree where the block is built
    # and never passed to `render_perception_text` - which is the exact
    # shape of the ENG-16 failure (a fix that reached zero residents),
    # and it is what this test did until removing the `goals=` argument
    # from the runner changed nothing here.
    sent: dict[str, str] = {}
    real_send = runner.gateway.send_tick

    async def remember(agent_id, tick, is_sleeping, perception, call_budget=None,
                       game_day=None):
        sent[agent_id] = perception
        return await real_send(
            agent_id, tick, is_sleeping, perception,
            call_budget=call_budget, game_day=game_day,
        )

    runner.gateway.send_tick = remember

    # One real tick through `run_tick`, which is what drives a run -
    # `tick_engine.tick()` alone only advances the clock and returns who
    # is due; `dispatch_due_agents` is the half that builds a perception,
    # and that is the half the wiring lives in. Nothing below calls
    # `refresh_goals_for`; unwiring it from the dispatch must break this.
    await runner.run_tick()

    woken = sorted(town.needs)
    assert woken, (
        "a whole tick of dispatch and not one resident's needs were read - "
        "the goal substrate is not wired into the turn"
    )
    who = woken[0]

    assert town.needs[who]["food"].level == pytest.approx(1.0)
    spawned = [g for g in town.goals.values() if g.agent_id == who]
    assert spawned, "a starving resident was handed nothing to want"

    # And it is in the prompt that left the building.
    prompt = sent.get(who)
    assert prompt, f"{who} took a turn and no perception was sent"
    assert "You are starving" in prompt, (
        "the resident's needs never reached its own prompt:\n" + prompt[:400]
    )
    assert "Get something to eat." in prompt, (
        "the goal the world spawned is not in the prompt:\n" + prompt[:400]
    )

    await runner.shutdown()


def test_a_refusal_writes_a_belief_on_the_path_that_actually_runs():
    """It has to happen AFTER the restore, or the rollback eats it.

    `execute()` snapshots, and a failing call restores before it
    re-raises. A belief written inside the transaction would be rolled
    back precisely when it had something to say - the mistake the action
    charge and the refusal log each made once.
    """
    import inspect

    from world.tools import registry as registry_module

    source = inspect.getsource(registry_module.ToolRegistry.execute)
    restore_at = source.index("world.restore(snapshot)")
    belief_at = source.index("beliefs_from_refusal")
    assert restore_at < belief_at, "the belief is written inside the transaction that rolls back"


@pytest.mark.asyncio
async def test_setting_a_goal_end_to_end(world):
    """Through the registry, as a resident would."""
    from world.tools.primitives.goals import SetGoalParams, set_goal_handler

    who = _somebody(world)
    outcome = await set_goal_handler(
        world,
        world.agents[who],
        SetGoalParams(text="Open a bakery", horizon=GoalHorizon.WEEK, priority=4),
    )
    goal_id = outcome.result["goal_id"]
    assert world.goals[goal_id].text == "Open a bakery"
    assert world.goals[goal_id].horizon is GoalHorizon.WEEK
    assert any(e.type == "agent.goal.set" for e in outcome.events)


@pytest.mark.asyncio
async def test_changing_your_mind_is_recorded_not_erased(world):
    """`supersedes` is what `Intention` had no way to express."""
    from world.tools.primitives.goals import SetGoalParams, set_goal_handler

    who = _somebody(world)
    first = await set_goal_handler(
        world, world.agents[who], SetGoalParams(text="Open a bakery")
    )
    old_id = first.result["goal_id"]
    second = await set_goal_handler(
        world,
        world.agents[who],
        SetGoalParams(text="Open a forge instead", supersedes=old_id),
    )
    assert world.goals[old_id].status is GoalStatus.SUPERSEDED
    assert world.goals[old_id].text == "Open a bakery", "the old goal was erased, not superseded"
    assert any(e.type == "agent.goal.superseded" for e in second.events)


@pytest.mark.asyncio
async def test_a_resident_cannot_want_everything_at_once(world):
    """And the refusal names the way out, rather than just saying no."""
    from world.tools.errors import ToolValidationError
    from world.tools.goal_lifecycle import MAX_ACTIVE_GOALS
    from world.tools.primitives.goals import SetGoalParams, set_goal_handler

    who = _somebody(world)
    for i in range(MAX_ACTIVE_GOALS):
        await set_goal_handler(world, world.agents[who], SetGoalParams(text=f"goal {i}"))
    with pytest.raises(ToolValidationError) as caught:
        await set_goal_handler(world, world.agents[who], SetGoalParams(text="one more"))
    said = str(caught.value)
    assert "retire_goal" in said and "supersedes" in said


@pytest.mark.asyncio
async def test_naming_a_tool_that_does_not_exist_is_refused(world):
    """TOOL-13's lesson: a goal that nothing could ever clear is a trap."""
    from world.tools.errors import ToolValidationError
    from world.tools.primitives.goals import SetGoalParams, set_goal_handler

    who = _somebody(world)
    with pytest.raises(ToolValidationError):
        await set_goal_handler(
            world,
            world.agents[who],
            SetGoalParams(text="Do the thing", done_when_tool="world_teleport"),
        )


def test_a_town_that_resumes_remembers_what_everyone_wanted(world):
    """Goals are checkpointed, or every morning starts the deciding again."""
    from world.tools.state import OWNED_STATE_FIELDS

    for field in ("goals", "needs", "beliefs"):
        assert field in OWNED_STATE_FIELDS, f"{field} is not carried across a resume"


# ── §5.3's acceptance line: the outcome renders once ─────────────────


def test_eating_tells_the_resident_its_goal_closed_exactly_once(world):
    """"a buy+eat marks it done and the outcome line renders once".

    The loop this substrate has that does not route through the model's
    own claim: the resident acts, the world notices the pressure fell,
    and says so - on one turn, not every turn after.
    """
    from world.life.upkeep import HungerState
    from world.retrieval_depth import format_goal_outcomes

    who = _somebody(world)
    world.hunger_by_agent = {who: HungerState(days_since_meal=3)}
    refresh_and_spawn(world, who)
    assert [g for g in active_goals(world, who) if g.done_when.get("need") == "food"]

    world.hunger_by_agent = {who: HungerState(days_since_meal=0)}
    refresh_and_spawn(world, who)

    queued = world.last_goal_outcome.get(who) or []
    assert queued, "the resident was never told its goal closed"
    rendered = format_goal_outcomes([{"text": g.text, "reason": r} for g, r in queued])
    assert rendered is not None and "no longer need to" in rendered

    # Popped: the second turn carries no news, because there is none.
    world.last_goal_outcome.pop(who)
    refresh_and_spawn(world, who)
    assert not (world.last_goal_outcome.get(who) or []), "the outcome line repeated"


def test_a_resident_that_retires_its_own_goal_is_not_told_it_did(world):
    """Being told what you just did spends a line restating the last one."""
    import asyncio

    from world.tools.primitives.goals import (
        RetireGoalParams,
        SetGoalParams,
        retire_goal_handler,
        set_goal_handler,
    )

    who = _somebody(world)
    made = asyncio.run(
        set_goal_handler(world, world.agents[who], SetGoalParams(text="Open a bakery"))
    )
    asyncio.run(
        retire_goal_handler(
            world,
            world.agents[who],
            RetireGoalParams(goal_id=made.result["goal_id"], done=True),
        )
    )
    assert not (world.last_goal_outcome.get(who) or [])


def test_a_lapsed_goal_says_so(world):
    from world.retrieval_depth import format_goal_outcomes

    who = _somebody(world)
    world.goals["g"] = Goal(
        goal_id="g", agent_id=who, text="Sell the wheat", horizon=GoalHorizon.TODAY
    )
    expire_goals(world)
    queued = world.last_goal_outcome.get(who) or []
    rendered = format_goal_outcomes([{"text": g.text, "reason": r} for g, r in queued])
    assert rendered is not None and "did not manage to" in rendered
    assert "Sell the wheat" in rendered


# ── ENG-05: the world as corrector, in its own acceptance terms ──────


def test_two_refusals_of_the_same_thing_put_it_in_the_next_prompt(world):
    """ENG-05's acceptance line.

    "after two refusals of `craft` at the bakery, the next prompt carries
    the belief and the third attempt does not occur in >=80% of L1
    scenarios." The second half is a measurement over live models and
    belongs to a run; the first half is this file's business, and it was
    the half that had nothing behind it - `step11_skill_evolution` reads
    `source='user'` and the seam writes `source='engine'`, so the loop
    the finding traced could never fire at all.

    The belief goes in the world rather than through the learning
    pipeline for a reason the finding itself allows: the refusal happens
    in the world, the world already knows why, and routing it through a
    consolidation pass would put a model call between a resident and a
    fact it was just told to its face.
    """
    from world.retrieval_depth import format_goals

    who = _somebody(world)
    for _ in range(2):
        note_refusal_belief(world, who, "craft", "there is no forge here", "business:b_bakery")

    held = [b for b in world.beliefs.values() if b.agent_id == who]
    assert len(held) == 1, "two refusals of one thing made two beliefs"
    assert held[0].confidence > 0.6, "being refused twice taught nothing extra"

    block = format_goals(
        beliefs=[{"statement": b.statement, "confidence": b.confidence} for b in held]
    )
    assert block is not None, "the belief never reached the prompt"
    assert "craft" in block and "no forge" in block


def test_a_refusal_somewhere_else_is_a_different_belief(world):
    """Keyed on tool AND place: "craft does not work here" is about here."""
    who = _somebody(world)
    note_refusal_belief(world, who, "craft", "no forge", "business:b_bakery")
    note_refusal_belief(world, who, "craft", "no forge", "sector:farmland")
    assert len([b for b in world.beliefs.values() if b.agent_id == who]) == 2
