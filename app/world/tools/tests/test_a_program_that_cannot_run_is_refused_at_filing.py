"""PROG-02: a program that cannot run is refused while it is being written.

Authoring time, not run time, and the ordering is the whole finding. An
invented tool can be taught, licensed, sold and inherited, so a program
with a reference nothing binds does not stay its author's problem: it
becomes a stranger's, three ticks into a turn they paid for, halfway
through a sequence that has already spent their money.

This is TOOL-06's lesson applied one layer up. That finding was Junia's
`mill_flour_run`, accepted carrying a recipe that does not exist and
failing at step 3 on its first call, after the first two steps had run
and been charged. The inventor had paid the fee, the office had approved
it, the registry had wired it in, and the resident learned it was broken
by watching a turn die. Control flow adds three new ways to be broken in
exactly that manner, and all three are checkable on paper.

Every reason is aggregated into ONE refusal, alongside the whitelist and
placeholder complaints `validate_steps` already produced, because an
author told about the whitelist on one turn and about an unbound `@name`
on the next has paid twice for one filing.
"""

from __future__ import annotations

import pytest

from world.tools import program
from world.tools.errors import ToolError
from world.tools.tests.conftest import spawn_agent

FIELD = "Kasukabe Farmland"


async def _refusal(world, registry, steps, schema=None, name="doomed"):
    await spawn_agent(world, "n01", FIELD)
    with pytest.raises(ToolError) as caught:
        await registry.execute(
            world,
            "n01",
            "toolify",
            {
                "name": name,
                "description": "a program the world will not run",
                "param_schema": schema or {},
                "steps": steps,
            },
        )
    return caught.value


@pytest.mark.asyncio
async def test_reading_a_binding_nothing_binds_is_refused(world, registry):
    error = await _refusal(
        world,
        registry,
        [
            {"tool_name": "look_around"},
            {"tool_name": "move", "params": {"destination_location_id": "@nowhere"}},
        ],
    )
    assert "@nowhere" in str(error)
    assert "binds" in str(error)


@pytest.mark.asyncio
async def test_reading_a_binding_from_a_later_step_is_refused(world, registry):
    """A step may only read results from steps ABOVE it.

    Forward references are the ones that look correct in a filing and
    are empty at run time, because at the moment the guarded step is
    evaluated the step that would have bound the name has not run yet.
    """
    error = await _refusal(
        world,
        registry,
        [
            {
                "tool_name": "rest",
                "when": {"left": "@later.location_id", "op": "truthy"},
            },
            {"tool_name": "look_around", "bind": "later"},
        ],
    )
    assert "@later" in str(error)
    assert "ABOVE" in str(error)


@pytest.mark.asyncio
async def test_reading_two_levels_into_a_result_is_refused(world, registry):
    """One level, and a refusal rather than a silent None.

    A reference that quietly evaluated to nothing is how a condition
    comes to be false for a reason nobody can see - which in a program
    means a step that never runs and never explains itself.
    """
    error = await _refusal(
        world,
        registry,
        [
            {"tool_name": "look_around", "bind": "here"},
            {
                "tool_name": "rest",
                "when": {"left": "@here.tile.x", "op": ">", "right": 0},
            },
        ],
    )
    assert "two levels" in str(error)


@pytest.mark.asyncio
async def test_two_steps_binding_one_name_is_refused(world, registry):
    """A later reader cannot tell which result it got.

    Refused rather than last-wins, because last-wins is a rule nobody
    reading the filing can see, and a program is a document other
    residents are meant to be able to read.
    """
    error = await _refusal(
        world,
        registry,
        [
            {"tool_name": "look_around", "bind": "x"},
            {"tool_name": "check_debts", "bind": "x"},
        ],
    )
    assert "already" in str(error)


@pytest.mark.asyncio
async def test_a_program_that_could_never_finish_is_refused(world, registry):
    """If even the floor is over the ceiling, it stops every time it runs.

    Not the exact cost - a guarded step may be skipped and a `$repeat`
    is unknown until the call - but the number of dispatches this
    program makes with NOTHING skipped. A filing whose best case is
    already over the limit is a tool that is broken by construction, and
    letting it through means finding out mid-turn, after it has spent.
    """
    over = program.PROGRAM_MAX_CALLS + 1
    steps = [
        {"tool_name": "rest", "repeat": program.PROGRAM_MAX_REPEAT}
        for _ in range((over // program.PROGRAM_MAX_REPEAT) + 1)
    ]
    error = await _refusal(world, registry, steps, name="never_finishes")
    assert str(program.PROGRAM_MAX_CALLS) in str(error)


@pytest.mark.asyncio
async def test_a_program_longer_than_the_step_cap_is_refused(world, registry):
    steps = [{"tool_name": "rest"} for _ in range(program.PROGRAM_MAX_STEPS + 1)]
    error = await _refusal(world, registry, steps, name="too_long")
    assert str(program.PROGRAM_MAX_STEPS) in str(error)


@pytest.mark.asyncio
async def test_a_comparison_the_world_does_not_have_is_refused(world, registry):
    """The closed set is closed.

    `op` is validated on the model itself, so a program cannot be filed
    with `"op": "matches"` and discover at run time that the runtime
    falls through to False - which would be a condition that is always
    false and never says why.
    """
    error = await _refusal(
        world,
        registry,
        [{"tool_name": "rest", "when": {"left": 1, "op": "≈", "right": 1}}],
        name="fuzzy",
    )
    assert "op" in str(error)


@pytest.mark.asyncio
async def test_every_problem_in_one_filing_is_reported_at_once(world, registry):
    """One refusal, all the reasons - the aggregate-errors idiom.

    Two problems reported one per turn is two turns spent on one
    filing, and the second one is spent blind.
    """
    error = await _refusal(
        world,
        registry,
        [
            {"tool_name": "look_around", "bind": "x"},
            {"tool_name": "check_debts", "bind": "x"},
            {"tool_name": "rest", "params": {"stop_walking": "@missing"}},
        ],
        name="two_problems",
    )
    text = str(error)
    assert "already" in text and "@missing" in text, (
        f"only one of the two problems was reported: {text}"
    )


@pytest.mark.asyncio
async def test_a_control_flow_refusal_teaches_the_vocabulary(world, registry):
    """`ToolError.context`, for the same reason `skillify`'s refusals have it.

    An author whose retry is as blind as its first attempt has paid two
    turns for one lesson. The caps and the condition vocabulary are what
    the second attempt needs and cannot derive.
    """
    error = await _refusal(
        world,
        registry,
        [{"tool_name": "rest", "when": {"left": "@ghost", "op": "truthy"}}],
        name="needs_help",
    )
    assert error.context, "a control-flow refusal carried no context at all"
    assert "conditions_available" in error.context
    assert "max_calls_per_program" in error.context
    assert "how_to_read_a_result" in error.context


@pytest.mark.asyncio
async def test_an_ordinary_composition_refusal_is_not_buried_in_caps(world, registry):
    """A misspelled primitive does not need to be told the loop ceiling.

    A refusal that answers questions nobody asked is how the useful half
    stops being read.
    """
    error = await _refusal(
        world,
        registry,
        [{"tool_name": "not_a_real_primitive"}],
        name="typo",
    )
    assert not (error.context or {}).get("max_calls_per_program")
