"""An author must be able to see the evidence it is authoring from.

`authoring_context` already hands a would-be author the environment (every
tool it can see, priced for it, with its gates) and most of itself (trade,
place, wallet, holdings, what it has already written). Three things it
does not hand over, and each one costs the author a turn or a bad guess:

  * **the repetition counts.** Authoring in this town is evidence-driven:
    `authoring_hint` only speaks once an agent has actually repeated a job,
    and the operator's report says plainly that "an agent is prompted to
    write a procedure down only after repeating one job several times, and
    nobody crossed that line". The hint says "you keep doing this"; the
    context beside it does not say how many times, for which tools, or how
    close anything else is. The evidence and the invitation arrive in the
    same payload and only one of them is legible.

  * **the steps of what it already wrote.** `already_written` gives names.
    Improving a procedure you cannot read is guesswork, and improvement is
    the whole point of writing one down - a skill that cannot be revised on
    evidence is a snapshot of the first time an agent got something to work.

  * **`revise_tool` returned no context at all**, while `revise_skill`
    did. Revising the tool IS the improvement path, and it was the one
    arriving blind.
"""

from __future__ import annotations

import pytest

from world.tools.state import WorldState

from .conftest import spawn_agent

FARM = "Kasukabe Farmland"


async def _worker(world: WorldState, agent_id: str = "n01") -> None:
    await spawn_agent(world, agent_id, FARM)
    world.agent_names[agent_id] = "Wren Thorne"


@pytest.mark.asyncio
async def test_the_context_carries_the_repetitions_it_is_authored_from(
    world: WorldState, registry
) -> None:
    """The counts behind the hint, in the payload the hint arrives with."""
    await _worker(world)
    world.skill_reps.setdefault("n01", {})["harvest"] = 6
    world.skill_reps["n01"]["cook"] = 2

    context = await world.authoring_context("n01")
    reps = context["you"]["repetitions"]
    assert reps["harvest"] == 6
    assert reps["cook"] == 2


@pytest.mark.asyncio
async def test_repetitions_are_absent_not_faked_for_a_resident_who_has_done_nothing(
    world: WorldState, registry
) -> None:
    """An empty map, not zeroes for all fifty-five tools: a wall of zeroes
    is noise an author has to read past to find the one number that
    matters."""
    await _worker(world)
    context = await world.authoring_context("n01")
    assert context["you"]["repetitions"] == {}


@pytest.mark.asyncio
async def test_an_author_can_read_the_procedure_it_is_about_to_improve(
    world: WorldState, registry
) -> None:
    """`already_written` naming a skill is not enough to revise it."""
    await _worker(world)
    await registry.execute(world, "n01", "skillify", {
        "name": "Morning Harvest",
        "steps": [
            {"order": 1, "kind": "tool", "ref": "move", "note": "to the field"},
            {"order": 2, "kind": "tool", "ref": "harvest", "note": "cut what is ripe"},
        ],
    })

    context = await world.authoring_context("n01")
    written = context["already_written"]["skills"]
    assert "Morning Harvest" in written, "the name is still listed"

    detail = context["already_written"]["skill_steps"]["Morning Harvest"]
    assert [s["ref"] for s in detail] == ["move", "harvest"], (
        "an author cannot improve a procedure it cannot read"
    )


@pytest.mark.asyncio
async def test_revising_a_tool_comes_back_with_the_same_context_as_revising_a_skill(
    world: WorldState, registry
) -> None:
    """The improvement path was the one arriving blind."""
    await _worker(world)
    # toolify's steps are {order, tool_name}; skillify's are
    # {order, kind, ref}. Two authoring tools, two step shapes - noted
    # rather than papered over, because a test that quietly used one
    # shape for both would hide it.
    await registry.execute(world, "n01", "toolify", {
        "name": "walk_and_cut",
        "description": "go to the field and harvest",
        # TOOL-06: filing now dry-runs each step's literal params, so
        # both of these carry the arguments they would need at call time.
        "steps": [
            {"order": 1, "tool_name": "move",
             "params": {"destination_location_id": "sector:900007"}},
            {"order": 2, "tool_name": "harvest", "params": {"crop": "wheat"}},
        ],
    })

    revised = await registry.execute(world, "n01", "revise_tool", {
        "name": "walk_and_cut",
        "description": "go to the field, harvest, and carry it home",
        "steps": [
            {"order": 1, "tool_name": "move",
             "params": {"destination_location_id": "sector:900007"}},
            {"order": 2, "tool_name": "harvest", "params": {"crop": "wheat"}},
            {"order": 3, "tool_name": "look_around"},
        ],
    })

    assert "you" in revised.result, "revise_tool answered without any context"
    assert "tools_you_can_use" in revised.result
    assert revised.result["you"]["agent_id"] == "n01"


@pytest.mark.asyncio
async def test_the_context_never_raises_for_a_resident_the_world_has_lost(
    world: WorldState, registry
) -> None:
    """It is what a REFUSAL carries; a stack trace here replaces a useful
    message with a crash."""
    context = await world.authoring_context("nobody")
    assert context["you"]["repetitions"] == {}
    assert context["already_written"]["skill_steps"] == {}
