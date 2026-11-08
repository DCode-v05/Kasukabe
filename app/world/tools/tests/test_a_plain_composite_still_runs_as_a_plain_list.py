"""PROG-02: the shape that already existed still behaves exactly as it did.

Every composite in this town now goes through `world/tools/program.py`,
including one filed before programs existed. That was a choice, and the
alternative - a second execution path for the old shape - is the one that
looks safer and is not:

  * PROG-01 exists to measure how many acts one model call bought. The
    commonest composite in any run is a plain list, and routing it around
    the runtime would have meant the commonest shape was the one nothing
    measured.
  * Two paths that must agree is the seam this project keeps breaking.
    `docs/runs/WIRING-AND-TOOL-CALLING-AUDIT.md` section 1: *"what breaks
    is never the piece; it is the seam between two pieces that each still
    pass their own tests."*

So there is one path, and this file is the price of that: proof that a
definition with no `when`, no `repeat` and no `bind` runs each step once,
in order, skipping nothing, charging what it always charged, and still
returning the two result keys any existing caller reads.
"""

from __future__ import annotations

import pytest

from world.tools import program
from world.tools.tests.conftest import action_cost, spawn_agent

FIELD = "Kasukabe Farmland"
PLAIN_STEPS = [
    {"tool_name": "look_around"},
    {"tool_name": "check_debts"},
    {"tool_name": "rest"},
]


async def _file_plain(world, registry, agent_id: str = "n01", name: str = "morning"):
    await spawn_agent(world, agent_id, FIELD)
    await registry.execute(
        world,
        agent_id,
        "toolify",
        {
            "name": name,
            "description": "look, count, breathe",
            "steps": PLAIN_STEPS,
        },
    )


def test_a_definition_with_no_control_flow_says_so():
    """The description number the cost-model decision turns on.

    `has_control_flow` is not a switch between execution paths - there
    is only one. It answers "how many of the town's invented tools took
    up control flow", which is the adoption figure
    `docs/decisions/PROG-COST-MODEL.md` needs and nothing else can
    supply.
    """
    from world.tools.models import StepCondition, ToolStep

    plain = [ToolStep(tool_name="rest"), ToolStep(tool_name="look_around")]
    assert program.has_control_flow(plain) is False

    guarded = plain + [
        ToolStep(tool_name="rest", when=StepCondition(left=1, op="==", right=1))
    ]
    assert program.has_control_flow(guarded) is True
    assert program.has_control_flow([ToolStep(tool_name="rest", repeat=2)]) is True
    assert program.has_control_flow([ToolStep(tool_name="rest", bind="x")]) is True


@pytest.mark.asyncio
async def test_every_step_runs_once_in_order_and_nothing_is_skipped(world, registry):
    await _file_plain(world, registry)
    world.tool_invocations.clear()

    outcome = await registry.execute(world, "n01", "morning", {})

    ran = [r.tool_name for r in world.tool_invocations if r.program_step]
    assert ran == ["look_around", "check_debts", "rest"]
    assert outcome.result["steps_skipped"] == 0


@pytest.mark.asyncio
async def test_the_result_still_carries_the_keys_it_always_carried(world, registry):
    """`steps_executed` and `last_step_result` are what callers read.

    New keys beside them are additive and harmless; either of these two
    disappearing would break every existing reader silently, because a
    missing key in a dict reads as absent rather than as wrong.
    """
    await _file_plain(world, registry, name="morning2")

    outcome = await registry.execute(world, "n01", "morning2", {})

    assert outcome.result["steps_executed"] == len(PLAIN_STEPS)
    assert isinstance(outcome.result["last_step_result"], dict)
    assert outcome.result["last_step_result"], "the last step's result was empty"


@pytest.mark.asyncio
async def test_a_plain_composite_costs_exactly_its_steps(world, registry):
    """TOOL-06's property, restated after the runtime moved.

    `test_an_invented_tool_costs_what_the_blurb_says` holds this against
    the blurb. This holds it against the new execution path, so a
    runtime that quietly charged for its own bookkeeping would be caught
    here rather than in a run report.
    """
    await _file_plain(world, registry, name="morning3")
    before = world.ledger.sim_balance("n01")

    await registry.execute(world, "n01", "morning3", {})

    spent = before - world.ledger.sim_balance("n01")
    expected = sum(action_cost(step["tool_name"]) for step in PLAIN_STEPS)
    assert spent == expected, (
        f"a plain composite cost {spent} where its steps cost {expected}; "
        "the wrapper is charging for something"
    )


@pytest.mark.asyncio
async def test_a_refusing_step_still_rolls_the_whole_call_back(world, registry):
    """A REFUSAL is not a ceiling, and must still behave as it always did.

    Stopping at a ceiling keeps what ran. A step the world refuses is
    the opposite case: the world is saying that act may not happen, the
    call raises, `execute` restores, and TOOL-06's refund gives back what
    the earlier steps paid. Programs changed neither half, and the
    distinction is easy to lose - so it is pinned here.
    """
    await spawn_agent(world, "n02", FIELD)
    await registry.execute(
        world,
        "n02",
        "toolify",
        {
            "name": "look_then_fail",
            "description": "one good step and one the world will refuse",
            "steps": [
                {"tool_name": "look_around"},
                {"tool_name": "eat", "params": {"item": "bread"}},
            ],
        },
    )
    before = world.ledger.sim_balance("n02")
    world.tool_invocations.clear()

    with pytest.raises(Exception):
        await registry.execute(world, "n02", "look_then_fail", {})

    assert world.ledger.sim_balance("n02") == before, (
        "a composite that was refused partway left the actor poorer for "
        "steps the world says never happened - TOOL-06's exact finding"
    )
    assert not world.tool_invocations, (
        "a refused call left invocation records behind; the log is meant "
        "to hold only calls that genuinely committed"
    )


@pytest.mark.asyncio
async def test_a_program_cannot_step_around_a_location_gate(world, registry):
    """The "no new physics" claim, at run time.

    `meta.py`'s module docstring rests the whole safety argument on one
    sentence: the composite handler "replays the filed steps one at a
    time through `ToolRegistry.dispatch()`, the exact same gate-checked
    path every ordinary tool call takes". PROG-02 moved that replay into
    a runtime with conditions and loops in it, and the sentence has to
    survive the move or the argument does not.

    Filed successfully, because where the caller will be standing is not
    knowable at filing time - and refused at the step, from the street,
    exactly as the same call by hand would be.
    """
    await spawn_agent(world, "n03", "street")
    await registry.execute(
        world,
        "n03",
        "toolify",
        {
            "name": "mine_from_anywhere",
            "description": "a program that would like to skip the walk",
            "steps": [
                {"tool_name": "look_around"},
                {"tool_name": "mine", "params": {"metal": "copper"}},
            ],
        },
    )
    world.tool_invocations.clear()

    with pytest.raises(Exception) as refused:
        await registry.execute(world, "n03", "mine_from_anywhere", {})

    assert "location" in str(refused.value).lower(), (
        "a gated step inside a program failed for some reason other than "
        f"the gate: {refused.value}"
    )
    assert not world.tool_invocations, (
        "the refused program left records behind, so the rollback that "
        "makes a gate meaningful did not run"
    )
