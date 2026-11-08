"""Part B: an agent writes down a procedure, and the procedure is real.

Real meaning three things, each with its own test below: the workflow is
validated against the live registry when it is filed, following it end to end
is what earns proficiency, and a procedure of state cannot be filed as an
ordinary trade to avoid the council.
"""

import pytest

from world.tools.bootstrap import build_world
from world.tools.errors import ToolValidationError
from world.tools.locations import farmland_location
from world.tools.state import SKILL_ABANDONED_AFTER_TICKS
from world.tools.tests.conftest import spawn_agent

WHEAT_FIELD = farmland_location("wheat")


def step(order, kind, ref=None, note=""):
    return {"order": order, "kind": kind, "ref": ref, "note": note}


async def _world():
    world, registry = await build_world(seed=31)
    await spawn_agent(world, "yara", WHEAT_FIELD, starting_sim=100_000)
    return world, registry


@pytest.mark.asyncio
async def test_filing_a_workflow_derives_what_it_discounts() -> None:
    world, registry = await _world()

    result = await registry.execute(
        world, "yara", "skillify",
        {
            "name": "working a field",
            "preconditions": "standing at a field you may work",
            "steps": [
                step(1, "check", note="is the field actually ripe?"),
                step(2, "tool", "look_around"),
                step(3, "tool", "harvest"),
            ],
        },
    )

    assert result.result["discounts_tools"] == ["look_around", "harvest"]
    assert result.result["governmental"] is False
    assert result.result["status"] == "ACTIVE"
    # The author holds it, but only at 1: writing a procedure is not knowing it.
    assert world.skills["yara"]["working a field"] == 1


@pytest.mark.asyncio
async def test_a_workflow_cannot_name_a_tool_that_does_not_exist() -> None:
    """The reason `discounts_tools` is safe to derive: every reference is
    checked against the live registry at filing time, so a skill cannot point
    at a vocabulary the town does not have."""
    world, registry = await _world()

    with pytest.raises(ToolValidationError) as caught:
        await registry.execute(
            world, "yara", "skillify",
            {"name": "wishful thinking", "steps": [step(1, "tool", "summon_rain")]},
        )
    assert "summon_rain" in str(caught.value)
    assert "wishful thinking" not in world.skill_definitions


@pytest.mark.asyncio
async def test_every_bad_reference_is_reported_at_once() -> None:
    world, registry = await _world()

    with pytest.raises(ToolValidationError) as caught:
        await registry.execute(
            world, "yara", "skillify",
            {
                "name": "three mistakes",
                "steps": [
                    step(1, "tool", "summon_rain"),
                    step(2, "tool", "read_minds"),
                    step(3, "skill", "a skill nobody wrote"),
                ],
            },
        )
    message = str(caught.value)
    assert "summon_rain" in message and "read_minds" in message and "nobody wrote" in message


@pytest.mark.asyncio
async def test_a_skill_cannot_call_itself() -> None:
    world, registry = await _world()
    with pytest.raises(ToolValidationError):
        await registry.execute(
            world, "yara", "skillify",
            {"name": "ouroboros", "steps": [step(1, "skill", "ouroboros")]},
        )


@pytest.mark.asyncio
async def test_nesting_is_capped_at_two() -> None:
    """Two workflows may name each other's work; a third layer may not. Same
    guard build_composite_handler already applies to invented tools."""
    world, registry = await _world()
    await registry.execute(
        world, "yara", "skillify", {"name": "inner", "steps": [step(1, "tool", "harvest")]}
    )
    await registry.execute(
        world, "yara", "skillify",
        {"name": "middle", "steps": [step(1, "skill", "inner"), step(2, "tool", "look_around")]},
    )

    with pytest.raises(ToolValidationError) as caught:
        await registry.execute(
            world, "yara", "skillify", {"name": "outer", "steps": [step(1, "skill", "middle")]}
        )
    assert "nests" in str(caught.value)


@pytest.mark.asyncio
async def test_walking_the_workflow_end_to_end_is_what_earns_proficiency() -> None:
    """The junk-skill guard, stated positively. Individual tool calls along the
    way earn the BUILT-IN trade its repetition; the authored skill earns one
    only when the last step lands."""
    world, registry = await _world()
    await registry.execute(
        world, "yara", "skillify",
        {
            "name": "a proper harvest",
            "steps": [
                step(1, "tool", "look_around"),
                step(2, "tool", "harvest"),
            ],
        },
    )
    assert world.skills["yara"]["a proper harvest"] == 1

    await registry.execute(world, "yara", "look_around", {})
    # Halfway: progress is open, but nothing has been earned yet.
    assert world.skill_progress["yara::a proper harvest"].next_step == 1
    assert world.skill_reps.get("yara", {}).get("a proper harvest", 0) == 0

    await registry.execute(world, "yara", "harvest", {"crop": "wheat", "quantity": 1})
    assert world.skill_reps["yara"]["a proper harvest"] == 1
    assert "yara::a proper harvest" not in world.skill_progress, "completed, so no longer open"
    # And the built-in trade advanced independently, from the harvest itself.
    assert world.skill_reps["yara"]["farming"] == 1


@pytest.mark.asyncio
async def test_a_junk_skill_naming_many_tools_earns_nothing_from_ordinary_work() -> None:
    """File a workflow over every tool you already use, then do your normal
    day's work. The skill must still be worth 1 - what it was when written."""
    world, registry = await _world()
    await registry.execute(
        world, "yara", "skillify",
        {
            "name": "everything at once",
            "steps": [
                step(1, "tool", "harvest"),
                step(2, "tool", "harvest"),
                step(3, "tool", "harvest"),
                step(4, "tool", "look_around"),
            ],
        },
    )

    # Two harvests: real work, and it does advance the workflow - but the
    # workflow is not finished, so the skill has earned nothing.
    await registry.execute(world, "yara", "harvest", {"crop": "wheat", "quantity": 1})
    await registry.execute(world, "yara", "harvest", {"crop": "wheat", "quantity": 1})

    assert world.skills["yara"]["everything at once"] == 1
    assert world.skill_reps["yara"]["farming"] == 2, "the real trade did advance"


@pytest.mark.asyncio
async def test_a_workflow_left_alone_is_abandoned() -> None:
    """Which procedures get dropped, and where, is a finding - so progress
    expires rather than waiting forever."""
    world, registry = await _world()
    await registry.execute(
        world, "yara", "skillify",
        {"name": "slow work", "steps": [step(1, "tool", "look_around"), step(2, "tool", "harvest")]},
    )
    await registry.execute(world, "yara", "look_around", {})
    assert "yara::slow work" in world.skill_progress

    world.current_tick += SKILL_ABANDONED_AFTER_TICKS + 1
    await registry.execute(world, "yara", "look_around", {})

    progress = world.skill_progress["yara::slow work"]
    assert progress.next_step == 1, "the stale attempt was dropped and a fresh one opened"


@pytest.mark.asyncio
async def test_check_steps_do_not_stall_a_workflow() -> None:
    """A `check` dispatches nothing, so a workflow that waited for one to
    arrive through dispatch() would never finish."""
    world, registry = await _world()
    await registry.execute(
        world, "yara", "skillify",
        {
            "name": "careful work",
            "steps": [
                step(1, "check", note="is the field ripe?"),
                step(2, "tool", "harvest"),
                step(3, "check", note="was that worth doing?"),
            ],
        },
    )

    await registry.execute(world, "yara", "harvest", {"crop": "wheat", "quantity": 1})
    assert world.skill_reps["yara"]["careful work"] == 1, "leading and trailing checks skipped"


@pytest.mark.asyncio
async def test_a_completed_inner_skill_advances_the_outer_one() -> None:
    world, registry = await _world()
    await registry.execute(
        world, "yara", "skillify", {"name": "gathering", "steps": [step(1, "tool", "harvest")]}
    )
    await registry.execute(
        world, "yara", "skillify",
        {"name": "a day's work", "steps": [step(1, "skill", "gathering"), step(2, "tool", "look_around")]},
    )

    await registry.execute(world, "yara", "harvest", {"crop": "wheat", "quantity": 1})
    assert world.skill_reps["yara"]["gathering"] == 1
    assert world.skill_progress["yara::a day's work"].next_step == 1, "inner done, outer advanced"

    await registry.execute(world, "yara", "look_around", {})
    assert world.skill_reps["yara"]["a day's work"] == 1


@pytest.mark.asyncio
async def test_an_authored_skill_discounts_its_own_tools_once_practised() -> None:
    """The payoff: the workflow is not just a document, it changes the price."""
    world, registry = await _world()
    await registry.execute(
        world, "yara", "skillify", {"name": "quick gathering", "steps": [step(1, "tool", "harvest")]}
    )

    from world.ledger.action_costs import proficiency_for

    for _ in range(20):
        await registry.execute(world, "yara", "harvest", {"crop": "wheat", "quantity": 1})

    assert world.skill_reps["yara"]["quick gathering"] == 20
    # Highest wins across the built-in trade and the authored skill.
    assert proficiency_for(world, "yara", "harvest") == max(
        world.skills["yara"]["farming"], world.skills["yara"]["quick gathering"]
    )


@pytest.mark.asyncio
async def test_authored_skills_survive_a_real_checkpoint_round_trip() -> None:
    """Through WorldStateCheckpointAdapter - the path run_genesis.py actually
    writes to disk, not WorldState.snapshot() (which keeps live model
    instances and is deliberately not json.dumps-safe on its own).

    Worth pinning because the adapter derives its field coverage from
    OWNED_STATE_FIELDS by resolved type hint, so `skill_definitions` and
    `skill_progress` are handled without anyone hand-listing them. This test
    is what proves that derivation actually caught them: if it had not, a town
    would forget every procedure it had written the moment it was saved, and
    silently - a missing skill looks exactly like an agent that never wrote
    one.
    """
    import json

    from world.run_genesis import WorldStateCheckpointAdapter

    world, registry = await _world()
    await registry.execute(
        world, "yara", "skillify",
        {
            "name": "a proper harvest",
            "preconditions": "at a ripe field",
            "steps": [step(1, "tool", "look_around"), step(2, "tool", "harvest")],
        },
    )
    await registry.execute(world, "yara", "look_around", {})
    assert world.skill_progress["yara::a proper harvest"].next_step == 1

    adapter = WorldStateCheckpointAdapter(world)
    on_disk = json.loads(json.dumps(adapter.snapshot()))  # really JSON, really back
    world.skill_definitions.clear()
    world.skill_progress.clear()
    world.skill_reps.clear()
    adapter.restore(on_disk)

    definition = world.skill_definitions["a proper harvest"]
    assert [s.ref for s in definition.steps] == ["look_around", "harvest"]
    assert definition.author_agent_id == "yara"
    assert definition.discounts_tools == ["look_around", "harvest"]
    assert world.skill_progress["yara::a proper harvest"].next_step == 1

    # And the workflow carries on from where it was, across the save.
    await registry.execute(world, "yara", "harvest", {"crop": "wheat", "quantity": 1})
    assert world.skill_reps["yara"]["a proper harvest"] == 1
