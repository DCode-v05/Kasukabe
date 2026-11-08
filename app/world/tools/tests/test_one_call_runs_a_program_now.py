"""One call runs a program now - no naming it, no filing it, no waiting a turn.

Runs 26, 27 and 28: **0 programs** in 662 resident turns. PROG-01/02/03
built the mechanism and PROG-03 measured it, and every report said the same
thing - "No programs ran."

The residents were not failing to think in programs. They said so:

    "walk, harvest what I can carry, walk back, stock the shelf. Rinse and
    repeat"                                         - Farid, run 26
    "This is how a forester survives - fell, craft, sell, repeat."
                                                    - Idris, run 28

What stood between that sentence and a program was a detour. A program
existed only as a NAMED TOOL filed with `toolify` - a name, a description,
a parameter schema - and then called on a later decision. And "later" was
literal: the engine rebuilds its tool list at the start of a tick, so the
tool `toolify` answers "callable now" for is not in the list until the
resident's next turn. One resident in three runs took the detour (Jovana's
`harvest_maintain`, run 26) and never came back to call it.

Meanwhile run 28 spent the thing programs exist to save: 46 of 264 turns
ran to the 12-call limit, and those turns used 45% of every call made in a
turn - one model call per act, for acts the resident had already decided.

`do_in_order` is the same runtime with the detour removed. The steps are
the same `{tool_name, params, when, repeat, bind}` `toolify` files, checked
by the same `validate_steps`, run by the same `program.run_program` under
the same four ceilings, every step still gated, priced, refusable and
traced, and the wrapper filed as step 0 so PROG-03 counts it. Nothing is
registered and nothing persists: a routine worth keeping is still
`toolify`'s job.
"""

from __future__ import annotations

import pytest

from world.tools import program
from world.tools.errors import ToolError
from world.tools.registry import tool_registry_rows
from world.tools.tests.conftest import action_cost, spawn_agent

FIELD = "Kasukabe Farmland"


async def _run(world, registry, agent_id: str, steps: list):
    return await registry.execute(world, agent_id, "do_in_order", {"steps": steps})


@pytest.mark.asyncio
async def test_three_acts_are_one_call_and_one_program(world, registry):
    await spawn_agent(world, "n01", FIELD)
    world.tool_invocations.clear()

    outcome = await _run(world, registry, "n01", [
        {"tool_name": "rest"},
        {"tool_name": "rest"},
        {"tool_name": "look_around"},
    ])

    assert outcome.result["steps_executed"] == 3
    rows = list(world.tool_invocations)
    assert len({r.program_id for r in rows}) == 1 and rows[0].program_id, (
        "the acts were not filed under one program, so PROG-03 cannot see "
        "that one decision bought them"
    )
    wrapper = [r for r in rows if r.program_step == 0]
    assert [r.tool_name for r in wrapper] == ["do_in_order"]
    assert sorted(r.program_step for r in rows if r.program_step) == [1, 2, 3]


@pytest.mark.asyncio
async def test_the_call_costs_exactly_its_steps(world, registry):
    """The wrapper is free, as a filed composite's is (TOOL-06): what a
    program saves is deciding, never the price of an act."""
    await spawn_agent(world, "n02", FIELD)
    opening = world.ledger.sim_balance("n02")

    outcome = await _run(world, registry, "n02", [{"tool_name": "rest"}, {"tool_name": "rest"}])

    spent = opening - world.ledger.sim_balance("n02")
    assert spent == 2 * action_cost("rest")
    assert outcome.result["sim_spent"] == spent


@pytest.mark.asyncio
async def test_a_condition_reads_what_an_earlier_step_found(world, registry):
    await spawn_agent(world, "n03", FIELD)

    outcome = await _run(world, registry, "n03", [
        {"tool_name": "look_around", "bind": "here"},
        {"tool_name": "rest", "when": {"left": "@here.location_id", "op": "truthy"}},
        {"tool_name": "rest", "when": {"left": "@here.location_id", "op": "falsy"}},
    ])

    assert outcome.result["steps_executed"] == 2
    assert outcome.result["steps_skipped"] == 1


@pytest.mark.asyncio
async def test_the_wallet_ceiling_still_holds(world, registry):
    await spawn_agent(world, "n04", FIELD, starting_sim=20)

    outcome = await _run(world, registry, "n04", [{"tool_name": "rest", "repeat": 20}])

    assert outcome.result["stopped_early"] == "wallet", outcome.result
    assert world.ledger.sim_balance("n04") > 0


@pytest.mark.asyncio
async def test_a_program_that_could_never_finish_is_refused_before_any_act(world, registry):
    """Three steps of twenty is sixty acts against a ceiling of forty. The
    floor check refuses it on paper, so nothing runs and nothing is paid."""
    await spawn_agent(world, "n05", FIELD)
    opening = world.ledger.sim_balance("n05")
    world.tool_invocations.clear()
    repeat = program.PROGRAM_MAX_REPEAT

    with pytest.raises(ToolError) as refused:
        await _run(world, registry, "n05", [
            {"tool_name": "rest", "repeat": repeat},
            {"tool_name": "rest", "repeat": repeat},
            {"tool_name": "rest", "repeat": repeat},
        ])

    assert f"at most {program.PROGRAM_MAX_CALLS}" in str(refused.value), str(refused.value)
    assert not [r for r in world.tool_invocations if r.tool_name == "rest"]
    assert world.ledger.sim_balance("n05") == opening


@pytest.mark.asyncio
@pytest.mark.parametrize("inner", ["do_in_order", "toolify"])
async def test_a_step_may_only_be_a_primitive(world, registry, inner):
    """A step may be an act or an invented tool, never a meta-tool.

    This used to read "no program inside a program", on the grounds that
    the forty-call ceiling bounds ONE run. TOOL-31 made a tool citable as
    a step - the ceiling is now one budget for the whole nest
    (`program.ProgramBudget`), so nesting no longer multiplies it - but
    the meta-tools themselves are still not citable, and that is the part
    this guards: a program that could call `toolify` would be a program
    that writes its own physics, one indirection removed.
    """
    await spawn_agent(world, "n06", FIELD)

    with pytest.raises(ToolError) as refused:
        await _run(world, registry, "n06", [{"tool_name": inner, "params": {}}])

    assert "neither a primitive" in str(refused.value), str(refused.value)


@pytest.mark.asyncio
async def test_it_takes_no_parameters_of_its_own(world, registry):
    """A `$name` reads a filed tool's call parameters. An unfiled program
    has none, so the reference is refused rather than silently empty."""
    await spawn_agent(world, "n07", FIELD)

    with pytest.raises(ToolError) as refused:
        await _run(world, registry, "n07", [{"tool_name": "rest", "repeat": "$times"}])

    assert "$times" in str(refused.value) and "no parameter" in str(refused.value), (
        str(refused.value)
    )


@pytest.mark.asyncio
async def test_every_resident_is_served_it_in_full_from_anywhere(world, registry):
    """Wired, not merely registered - and served in FULL.

    The first version of this test checked the name was in the payload, and
    passed while the engine received this:

        "Run a short program now, in one call: ... Needs: steps."
        input_schema: 85 bytes, no step shape, no when/repeat/bind

    An index row. ENG-03 serves 24 full schemas and ranks the rest by name,
    so a verb that is not gated here and not in ALWAYS_IN_FULL is a line of
    text: the resident can see a program exists and cannot see how one is
    written. The tool would have shipped exactly as unreachable as the
    finding it fixes.
    """
    await spawn_agent(world, "n08", FIELD)

    rows = {row["name"]: row for row in tool_registry_rows(registry, world, "n08")}

    assert "do_in_order" in rows
    served = rows["do_in_order"]
    step = (served["input_schema"].get("$defs") or {}).get("ToolStep") or {}
    assert {"tool_name", "when", "repeat", "bind"} <= set(step.get("properties") or {}), (
        "the engine is served do_in_order without the step shape, so the model "
        f"cannot write a program: {served}"
    )
    assert "`repeat` runs one step" in served["description"], (
        "the description was cut to its first sentence - an index row"
    )
