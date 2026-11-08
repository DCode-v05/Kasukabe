"""PROG-01: the invocation log can say "these calls were one decision".

`docs/runs/WIRING-AND-TOOL-CALLING-AUDIT.md` section 7 puts this first of
the three pieces, and the reason it gives is the reason this file exists:
*"it is cheap, it changes no behaviour, and without it no run can measure
whether programmatic calling helped."*

Every step of a composite has always landed in `tool_invocations`. What
no query could do was tell six calls that were six decisions from six
calls that were one, so the only figure that justifies programmatic tool
calling at all - acts bought per model call - was not derivable from the
world's own record. A run could have shipped the whole feature and still
have had nothing to report.

The failure this guards against is the shape the project keeps hitting:
an instrument with no consumer, or a consumer keyed to a field nothing
writes. So these tests do not assert that a column exists. They compute
the figure a report would compute, from the rows the world actually
appended, and check the answer.
"""

from __future__ import annotations

import pytest

from world.tools.tests.conftest import spawn_agent

FIELD = "Kasukabe Farmland"


async def _inventor_with_a_composite(
    world, registry, agent_id: str = "n01", name: str = "look_twice"
) -> None:
    await spawn_agent(world, agent_id, FIELD)
    await registry.execute(
        world,
        agent_id,
        "toolify",
        {
            "name": name,
            "description": "look around, then count what is owed",
            # Two DIFFERENT reads on purpose. TRADE-04 folds a
            # repeated identical read inside one tick, and a folded
            # repeat is answered without a dispatch record - correctly,
            # because it is not an act. A guard about counting acts must
            # not accidentally be a guard about that fold; there is a
            # separate one for it below.
            "steps": [
                {"tool_name": "look_around"},
                {"tool_name": "check_debts"},
            ],
        },
    )


def _rows(world, tool_name: str) -> list:
    return [r for r in world.tool_invocations if r.tool_name == tool_name]


@pytest.mark.asyncio
async def test_every_step_of_a_composite_names_the_same_program(world, registry):
    await _inventor_with_a_composite(world, registry)
    world.tool_invocations.clear()

    await registry.execute(world, "n01", "look_twice", {})

    programs = {
        row.program_id for row in world.tool_invocations if row.program_id is not None
    }
    assert len(programs) == 1, (
        "the steps of one composite filed themselves under "
        f"{len(programs)} different programs: {programs}. A report joining "
        "on program_id would count one decision as several."
    )


@pytest.mark.asyncio
async def test_the_wrapper_is_step_zero_and_its_steps_are_numbered(world, registry):
    """The wrapper carries the program too, at step 0.

    Without this the acts-per-decision figure needs a join against
    something else to find the decision the steps belonged to. With it,
    the whole program is one `group by` and the answer is a subtraction:
    rows in the program, minus the one wrapper.
    """
    await _inventor_with_a_composite(world, registry)
    world.tool_invocations.clear()

    await registry.execute(world, "n01", "look_twice", {})

    wrapper = _rows(world, "look_twice")
    assert len(wrapper) == 1
    assert wrapper[0].program_step == 0, (
        "the wrapper is not step 0, so nothing in the log says which call "
        "was the decision and which were the acts it bought"
    )

    steps = [
        row for row in world.tool_invocations if row.tool_name != "look_twice"
    ]
    assert [row.program_step for row in steps] == [1, 2]
    assert all(row.program_id == wrapper[0].program_id for row in steps)


@pytest.mark.asyncio
async def test_acts_per_model_call_is_computable_from_the_log(world, registry):
    """The figure itself, computed the way a report would compute it.

    Asserting the column exists is not the same as asserting the question
    can be answered - `test_the_price_table_reads_a_real_price` is in this
    same directory because those two came apart once already.
    """
    await _inventor_with_a_composite(world, registry)
    world.tool_invocations.clear()

    await registry.execute(world, "n01", "look_twice", {})

    by_program: dict[str, int] = {}
    for row in world.tool_invocations:
        if row.program_id:
            by_program[row.program_id] = by_program.get(row.program_id, 0) + 1

    assert len(by_program) == 1
    acts_bought = next(iter(by_program.values())) - 1  # minus the wrapper
    assert acts_bought == 2, (
        "one model call bought two acts and the log cannot say so; "
        f"rows per program: {by_program}"
    )


@pytest.mark.asyncio
async def test_an_ordinary_call_belongs_to_no_program(world, registry):
    """A resident that walks by hand made a decision per call.

    Stamping every call with a program id would make the figure above
    meaningless in the other direction: everything would be a program and
    the ratio would be one, always. NULL here is what carries the
    distinction the feature exists to measure.
    """
    await spawn_agent(world, "n02", FIELD)
    world.tool_invocations.clear()

    await registry.execute(world, "n02", "look_around", {})

    rows = _rows(world, "look_around")
    assert rows, "look_around left no invocation record at all"
    assert all(row.program_id is None for row in rows)
    assert all(row.program_step is None for row in rows)


@pytest.mark.asyncio
async def test_a_program_frame_does_not_outlive_its_call(world, registry):
    """The next call must not file itself under a finished program.

    `world.program_stack` is a cursor into a call that is still running,
    not a record, so a frame left behind by one call would silently
    adopt the next one - and the run that noticed would be reading a
    report, weeks later, wondering why a resident's `rest` was step 4 of
    somebody's milling program.
    """
    await _inventor_with_a_composite(world, registry)
    await registry.execute(world, "n01", "look_twice", {})
    assert world.program_stack == []

    world.tool_invocations.clear()
    await registry.execute(world, "n01", "look_around", {})
    assert all(row.program_id is None for row in _rows(world, "look_around"))
