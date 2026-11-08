"""TOOL-23: `keep_doing` must check the underlying gate at commit.

Tool spec B1's location row promises the gate is "checked once at commit
and again at each repetition". Only the second half was built:
`keep_doing_handler` (living.py) folded the tool name, counted the stop
conditions and checked whether the resident was already working, and
`ToolRegistry.check_location_gate` had exactly one caller - `dispatch`, in
registry.py - so no dry-run happened at commit at all. A resident who
committed to `fell_timber` while standing on the farmland was told "You
are fell_timber now, and will keep at it without stopping to think", was
then skipped by the scheduler for being a working resident, and learned
the truth when the first repetition refused - a full turn cadence later,
which run 23 measured at three ticks. That is the one shape of failure
this verb exists to prevent: the whole point of a commitment is that
nobody is deliberating in between, so a mistake made at commit is a
mistake nobody is awake to catch.

The fix calls the registry's own `check_location_gate` from
`keep_doing_handler`, so the refusal is a `LocationGateError` naming the
inner tool and its required place, arrives while the resident is still
thinking, and lands in `world.tool_refusals` the way any other
out-of-position refusal does.

Both fixed gates (`fell_timber` -> "forest") and resolver gates
(`harvest` -> "farmland:<crop>", computed from the params) are covered,
because only the second kind needs the params model built first and it is
the kind a copy-pasted re-reading of the gate would get wrong.

The second half of TOOL-23 - "repetitions run per cadence, not per tick",
i.e. `advance_continuing` being driven once per due turn from
run_genesis.py - is not fixed here: that scheduler lives outside this
package.
"""

from __future__ import annotations

import pytest

from world.tools.errors import LocationGateError
from world.tools.state import WorldState

from .conftest import move_agent, spawn_agent

FIELD = "Kasukabe Farmland"
FOREST = "Kasukabe Forest"


async def _resident(world: WorldState, where: str, agent_id: str = "n06") -> None:
    await spawn_agent(world, agent_id, where)
    world.agent_names[agent_id] = "Wren Thorne"


@pytest.mark.asyncio
async def test_committing_to_work_you_cannot_do_here_is_refused_now_not_later(
    world: WorldState, registry
) -> None:
    """`fell_timber` is gated to the forest with a plain string."""
    await _resident(world, FIELD)

    with pytest.raises(LocationGateError) as caught:
        await registry.execute(world, "n06", "keep_doing", {
            "tool": "fell_timber", "params": {"quantity": 2}, "until": {"units": 5},
        })

    assert caught.value.tool_name == "fell_timber", (
        "the refusal names the wrapper, not the work; the resident cannot "
        "tell which verb it is standing in the wrong place for"
    )
    assert caught.value.required == "forest"
    assert not world.is_working("n06"), (
        "the resident was left committed to work it cannot do, and the "
        "scheduler will now skip it until the stop condition comes round"
    )


@pytest.mark.asyncio
async def test_a_resolver_gate_is_dry_run_too(world: WorldState, registry) -> None:
    """`harvest`'s required place is computed from `crop`, so the params
    model has to be built before the gate can be read at all."""
    await _resident(world, FOREST)

    with pytest.raises(LocationGateError) as caught:
        await registry.execute(world, "n06", "keep_doing", {
            "tool": "harvest", "params": {"crop": "wheat"}, "until": {"units": 5},
        })

    assert caught.value.required == "farmland:wheat"
    assert "n06" not in world.continuing


@pytest.mark.asyncio
async def test_the_same_commitment_from_the_right_place_is_accepted(
    world: WorldState, registry
) -> None:
    """The other half of the rule. A dry-run that refused a resident
    standing exactly where the work is would end continuing actions
    altogether."""
    await _resident(world, FOREST)

    await registry.execute(world, "n06", "keep_doing", {
        "tool": "fell_timber", "params": {"quantity": 2}, "until": {"units": 5},
    })
    assert world.is_working("n06")

    await move_agent(world, "n06", FIELD)
    world.continuing.pop("n06", None)
    await registry.execute(world, "n06", "keep_doing", {
        "tool": "harvest", "params": {"crop": "wheat"}, "until": {"units": 5},
    })
    assert world.continuing["n06"].tool == "harvest"


@pytest.mark.asyncio
async def test_an_incomplete_call_still_ends_at_the_first_repetition(
    world: WorldState, registry
) -> None:
    """B1's interruption rule 2, unchanged.

    A resolver gate is computed FROM the params, so a commitment whose
    params do not validate has no gate to dry-run. Those still commit and
    still end on the first repetition with the refusal as the reason -
    the dry-run narrows what reaches that path, it does not replace it.
    """
    await _resident(world, FIELD)

    await registry.execute(world, "n06", "keep_doing", {
        "tool": "craft", "params": {"recipe": "definitely-not-a-recipe"},
        "until": {"units": 20},
    })
    assert world.is_working("n06")

    await world.advance_continuing(registry, "n06")
    assert not world.is_working("n06")
    assert "craft" in (world.wake_reason("n06") or "").lower()


@pytest.mark.asyncio
async def test_the_refusal_reaches_the_towns_own_instruments(
    world: WorldState, registry
) -> None:
    """A commit-time refusal must be as visible to the researcher as a
    repetition-time one: `LocationGateError` is a `ToolError`, so
    `unmet_requirement_for` classifies it and it is recorded.
    """
    await _resident(world, FIELD)

    with pytest.raises(LocationGateError):
        await registry.execute(world, "n06", "keep_doing", {
            "tool": "fell_timber", "params": {"quantity": 2}, "until": {"units": 5},
        })

    keys = [r.unmet_requirement for r in world.tool_refusals]
    assert "location:forest" in keys, (
        f"the commit-time refusal was recorded nowhere; tool_refusals holds {keys}"
    )


@pytest.mark.asyncio
async def test_the_dry_run_is_wired_into_the_handler_not_only_available(
    world: WorldState, registry
) -> None:
    """WIRING. Everything above goes through `registry.execute`, which is
    the only way a resident ever reaches `keep_doing` - but each of those
    tests would also pass against a `keep_doing` that refused for some
    other reason. This one pins the wiring itself: the handler the
    registry holds for `keep_doing` must be the thing that performs the
    check, so deleting the call from `keep_doing_handler` and leaving
    `_refuse_if_the_work_is_somewhere_else` sitting in living.py unused -
    this repository's most common defect - goes red here.
    """
    from world.tools.primitives import living

    await _resident(world, FIELD)

    spec = registry.get("keep_doing")
    seen: list[str] = []
    original = living._refuse_if_the_work_is_somewhere_else

    def recording(world_, agent, tool, tool_params):  # noqa: ANN001
        seen.append(tool)
        return original(world_, agent, tool, tool_params)

    living._refuse_if_the_work_is_somewhere_else = recording
    try:
        await spec.handler(
            world,
            world.require_agent("n06"),
            living.KeepDoingParams(
                tool="harvest", params={"crop": "wheat"}, until={"units": 5}
            ),
        )
    finally:
        living._refuse_if_the_work_is_somewhere_else = original

    assert seen == ["harvest"], (
        "the registered keep_doing handler committed the resident without "
        "dry-running the gate; TOOL-23 is back"
    )
