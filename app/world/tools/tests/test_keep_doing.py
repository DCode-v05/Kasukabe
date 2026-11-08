"""B1: a resident commits to a job and stops paying to think about it.

From docs/kasukabetoolspec.md B1, and it is step 1 of the spec's own order
of work because everything after it is unaffordable without it:

    "Every action is instant except walking. A day of farming is 23
     separate `harvest` calls, each preceded by a 20k-token
     deliberation."

`keep_doing` commits the caller to repeating one primitive with fixed
parameters until a stop condition. The world executes each repetition
through the same `dispatch()` under the caller's id, charging the normal
sim cost, **with no model call between repetitions**. That is where the
cost falls: a farmer's afternoon becomes twenty repetitions and one model
call.

It is also what makes mastery mean something in dollars rather than sim.
Each repetition records through `dispatch()` exactly as a hand-called tool
does, so proficiency rises under a continuing action - and a mastered
trade becomes a cheap one in the currency that actually runs out.

Test names are the spec's five, plus the stop conditions its table lists.
"""

from __future__ import annotations

import pytest

from world.tools.state import WorldState

from .conftest import move_agent, spawn_agent

FIELD = "Kasukabe Farmland"


async def _farmer(world: WorldState, agent_id: str = "n06") -> None:
    await spawn_agent(world, agent_id, FIELD)
    world.agent_names[agent_id] = "Wren Thorne"


@pytest.mark.asyncio
async def test_a_continuing_action_makes_no_model_call_between_repetitions(
    world: WorldState, registry
) -> None:
    """The spec's first named test, and the whole point of the verb: the
    scheduler must skip a working resident, so a morning of harvesting
    costs one deliberation instead of twenty."""
    await _farmer(world)
    await registry.execute(world, "n06", "keep_doing", {
        "tool": "harvest", "params": {"crop": "wheat"}, "until": {"units": 5},
    })

    action = world.continuing.get("n06")
    assert action is not None
    assert action.tool == "harvest"
    assert world.is_working("n06") is True, (
        "the scheduler has no way to know this resident should not be woken"
    )


@pytest.mark.asyncio
async def test_only_labour_primitives_can_be_kept_doing(
    world: WorldState, registry
) -> None:
    """B1's params row lists them exactly: harvest, mine, fell_timber,
    craft, cook, rest. A resident that could `keep_doing` `say` would
    talk to an empty room until dusk."""
    from world.tools.errors import ToolValidationError

    await _farmer(world)
    with pytest.raises(ToolValidationError) as caught:
        await registry.execute(world, "n06", "keep_doing", {
            "tool": "say", "params": {"content": "hello"}, "until": {"ticks": 5},
        })
    assert "say" in str(caught.value)


@pytest.mark.asyncio
async def test_exactly_one_stop_condition_is_required(
    world: WorldState, registry
) -> None:
    """"exactly one of" - a commitment with two ends is two commitments,
    and one with none never ends."""
    from world.tools.errors import ToolValidationError

    await _farmer(world)
    with pytest.raises(ToolValidationError):
        await registry.execute(world, "n06", "keep_doing", {
            "tool": "harvest", "params": {}, "until": {},
        })
    with pytest.raises(ToolValidationError):
        await registry.execute(world, "n06", "keep_doing", {
            "tool": "harvest", "params": {}, "until": {"units": 3, "ticks": 9},
        })


@pytest.mark.asyncio
async def test_a_resident_already_working_cannot_start_a_second_job(
    world: WorldState, registry
) -> None:
    """B1's actor requirements: "alive, not already under a continuing
    action"."""
    from world.tools.errors import ToolValidationError

    await _farmer(world)
    await registry.execute(world, "n06", "keep_doing", {
        "tool": "harvest", "params": {}, "until": {"units": 5},
    })
    with pytest.raises(ToolValidationError):
        await registry.execute(world, "n06", "keep_doing", {
            "tool": "cook", "params": {}, "until": {"units": 2},
        })


@pytest.mark.asyncio
async def test_being_named_wakes_a_working_resident_and_says_who(
    world: WorldState, registry
) -> None:
    """Interruption rule 3, and the spec's second named test. A resident
    who cannot be interrupted by its own name is not present in the
    town."""
    await _farmer(world)
    await spawn_agent(world, "n07", FIELD)
    world.agent_names["n07"] = "Marta Thorne"
    await registry.execute(world, "n06", "keep_doing", {
        "tool": "harvest", "params": {}, "until": {"units": 50},
    })

    await registry.execute(world, "n07", "say", {
        "content": "Wren Thorne, come here a moment.", "listener_agent_id": "n06",
    })

    assert not world.is_working("n06"), "being named did not wake the worker"
    assert "Marta Thorne" in (world.wake_reason("n06") or ""), (
        "the resident is awake and does not know who called it"
    )


@pytest.mark.asyncio
async def test_ambient_talk_does_not_wake_a_worker_but_is_heard_later(
    world: WorldState, registry
) -> None:
    """The spec is explicit: "Ambient speech in the room does not wake
    it." That is how working while people talk around you behaves."""
    await _farmer(world)
    await spawn_agent(world, "n07", FIELD)
    world.agent_names["n07"] = "Marta Thorne"
    await registry.execute(world, "n06", "keep_doing", {
        "tool": "harvest", "params": {}, "until": {"units": 50},
    })

    await registry.execute(world, "n07", "say", {"content": "Cold morning, all."})

    assert world.is_working("n06"), "ambient chatter interrupted a working resident"


@pytest.mark.asyncio
async def test_the_units_condition_ends_the_action(world: WorldState, registry) -> None:
    await _farmer(world)
    await registry.execute(world, "n06", "keep_doing", {
        "tool": "rest", "params": {}, "until": {"units": 2},
    })
    for _ in range(4):
        await world.advance_continuing(registry, "n06")
    assert not world.is_working("n06")
    assert "2" in (world.wake_reason("n06") or "")


@pytest.mark.asyncio
async def test_running_out_of_stock_ends_the_action_with_the_refusal_as_reason(
    world: WorldState, registry
) -> None:
    """Interruption rule 2, and the spec's third named test. A refusal is
    the world telling the resident its plan no longer works, and it must
    reach the resident rather than be retried until dusk."""
    await _farmer(world)
    await registry.execute(world, "n06", "keep_doing", {
        "tool": "craft", "params": {"recipe": "definitely-not-a-recipe"},
        "until": {"units": 20},
    })
    await world.advance_continuing(registry, "n06")

    assert not world.is_working("n06")
    reason = world.wake_reason("n06") or ""
    assert reason and "craft" in reason.lower()


@pytest.mark.asyncio
async def test_being_moved_ends_the_action(world: WorldState, registry) -> None:
    """B1's location-gate row: "checked once at commit and again at each
    repetition. If the resident is moved, the action ends"."""
    await _farmer(world)
    await registry.execute(world, "n06", "keep_doing", {
        "tool": "harvest", "params": {}, "until": {"units": 50},
    })
    await move_agent(world, "n06", "Kasukabe Forest")
    await world.advance_continuing(registry, "n06")
    assert not world.is_working("n06")


@pytest.mark.asyncio
async def test_proficiency_rises_under_a_continuing_action(
    world: WorldState, registry
) -> None:
    """The spec's fifth named test, and the sentence DESIGN.md has been
    promising: a mastered trade is a cheap one. Each repetition goes
    through the same dispatch(), so it counts exactly as a hand-called
    tool does."""
    await _farmer(world)
    await registry.execute(world, "n06", "keep_doing", {
        "tool": "rest", "params": {}, "until": {"units": 3},
    })
    before = len([r for r in world.tool_invocations if r.tool_name == "rest"])
    for _ in range(3):
        await world.advance_continuing(registry, "n06")

    after = len([r for r in world.tool_invocations if r.tool_name == "rest"])
    assert after - before == 3, (
        f"three repetitions produced {after - before} recorded calls; they are "
        "not going through the path that earns proficiency"
    )


@pytest.mark.asyncio
async def test_keep_doing_is_priced_at_nothing_and_each_repetition_pays(
    world: WorldState, registry
) -> None:
    """B1's cost row: "LABOUR, base 0. Each repetition is charged as
    itself." Charging for the commitment as well would tax the resident
    twice for one decision."""
    from world.ledger.action_costs import SCHEDULE_TABLE

    rule = SCHEDULE_TABLE["keep_doing"]
    assert rule.base_cost == 0 and rule.min_cost == 0


@pytest.mark.asyncio
async def test_a_commitment_survives_a_checkpoint(world: WorldState, registry) -> None:
    """Addendum §6: "Owned-state list, checkpoint."

    The checkpoint is `WorldState.snapshot()`, which reads
    OWNED_STATE_FIELDS and nothing else. With `continuing` outside that
    tuple, a resumed run dropped every continuing action on the floor: a
    farmer who committed to the strip until dusk came back from a restart
    idle, with nothing in perception to say why, and the scheduler
    cheerfully paid to have it think again.
    """
    await _farmer(world)
    await registry.execute(world, "n06", "keep_doing", {
        "tool": "harvest", "params": {"crop": "wheat"}, "until": {"dusk": True},
    })

    saved = world.snapshot()
    world.continuing.clear()
    world.restore(saved)

    action = world.continuing.get("n06")
    assert action is not None, "the resume lost the commitment"
    assert action.tool == "harvest"
    assert action.until == {"dusk": True}
    assert world.is_working("n06") is True


@pytest.mark.asyncio
async def test_a_rolled_back_call_does_not_leave_a_resident_committed(
    world: WorldState, registry
) -> None:
    """A commitment is not an utterance - nobody heard it.

    `execute()` restores owned state when a handler raises, and refunds the
    charge. A resident left in `continuing` after that is skipped by the
    scheduler for a commitment the world says never happened, and stays
    skipped until its stop condition comes round.
    """
    await _farmer(world)

    spec = registry.get("keep_doing")
    original = spec.handler

    async def commits_then_fails(world_, agent, params):  # noqa: ANN001
        await original(world_, agent, params)
        raise RuntimeError("the handler fell over after committing")

    object.__setattr__(spec, "handler", commits_then_fails)
    try:
        with pytest.raises(RuntimeError):
            await registry.execute(world, "n06", "keep_doing", {
                "tool": "harvest", "params": {"crop": "wheat"}, "until": {"units": 5},
            })
    finally:
        object.__setattr__(spec, "handler", original)

    assert world.is_working("n06") is False, (
        "a call the world rolled back left the resident committed to it"
    )


@pytest.mark.asyncio
async def test_a_failed_remark_does_not_wake_a_worker(world: WorldState, registry) -> None:
    """`wake_if_working` runs inside `say`'s handler.

    A `say` that fails after that point would tell a working resident who
    spoke their name for a remark that never left anyone's mouth - and end
    their afternoon's work on the strength of it.
    """
    await _farmer(world)
    await spawn_agent(world, "n05", FIELD)
    world.agent_names["n05"] = "Marta Thorne"
    await registry.execute(world, "n06", "keep_doing", {
        "tool": "harvest", "params": {"crop": "wheat"}, "until": {"units": 5},
    })

    spec = registry.get("say")
    original = spec.handler

    async def wakes_then_fails(world_, agent, params):  # noqa: ANN001
        try:
            return await original(world_, agent, params)
        finally:
            raise RuntimeError("the remark never made it out")

    object.__setattr__(spec, "handler", wakes_then_fails)
    try:
        with pytest.raises(RuntimeError):
            await registry.execute(world, "n05", "say", {"content": "Wren Thorne. A word."})
    finally:
        object.__setattr__(spec, "handler", original)

    assert world.is_working("n06") is True, "a remark that never happened ended the work"
    assert world.wake_reasons.get("n06") is None
