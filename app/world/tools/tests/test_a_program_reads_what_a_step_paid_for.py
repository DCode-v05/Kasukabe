"""PROG-02: a condition sees only what a step already paid to find out.

This is the restriction that keeps a program from being a new physics,
and it is worth stating as the property rather than as the code: there is
no way to read world state from inside a `when`. A resident that wants to
branch on whether the shelf is empty puts a real `look_around` in the
program, binds it, and reads the binding - so the read is a gated, priced,
validated, refusable, logged call like every other, and the condition is
arithmetic over a value the town already handed over.

The alternative, and the reason this is a test and not a comment: a
condition with its own window onto world state would be a second way to
read the town that nothing charges for and no location gate covers. The
whole argument that a composite is safe (`meta.py`'s module docstring,
three numbered guarantees) rests on it adding no path the registry does
not already own. A `when` that could see the world would break the third
guarantee quietly, and nothing downstream would notice.
"""

from __future__ import annotations

import pytest

from world.tools import program
from world.tools.tests.conftest import spawn_agent

FIELD = "Kasukabe Farmland"


async def _file(world, registry, agent_id: str, name: str, steps: list, schema=None):
    await registry.execute(
        world,
        agent_id,
        "toolify",
        {
            "name": name,
            "description": f"{name}: a program filed by a test",
            "param_schema": schema or {},
            "steps": steps,
        },
    )


@pytest.mark.asyncio
async def test_a_later_step_reads_an_earlier_step_result(world, registry):
    """The whole point of `bind`: step two knows what step one learned.

    Without it a program is a fixed list that cannot react to anything,
    which is precisely what composites already were.
    """
    await spawn_agent(world, "n01", FIELD)
    await _file(
        world,
        registry,
        "n01",
        "look_then_stay",
        [
            {"tool_name": "look_around", "bind": "here"},
            {"tool_name": "move", "params": {"destination_location_id": "@here.location_id"}},
        ],
    )

    outcome = await registry.execute(world, "n01", "look_then_stay", {})

    assert outcome.result["steps_executed"] == 2
    assert outcome.result["steps_skipped"] == 0
    assert outcome.result["last_step_result"].get("location_id") == FIELD


@pytest.mark.asyncio
async def test_a_true_condition_runs_the_step_and_a_false_one_skips_it(
    world, registry
):
    """Both halves in one program, so neither can pass by accident.

    A guard that only ever checks the true branch measures that
    conditions do not crash; it does not measure that they decide
    anything.
    """
    await spawn_agent(world, "n01", FIELD)
    await _file(
        world,
        registry,
        "n01",
        "rest_if_clear",
        [
            {"tool_name": "check_debts", "bind": "debts"},
            {
                "tool_name": "rest",
                "when": {"left": "@debts.due_today_sim", "op": "==", "right": 0},
            },
            {
                "tool_name": "say",
                "params": {"content": "I owe something today."},
                "when": {"left": "@debts.due_today_sim", "op": ">", "right": 0},
            },
        ],
    )

    outcome = await registry.execute(world, "n01", "rest_if_clear", {})

    assert outcome.result["steps_executed"] == 2, outcome.result
    assert outcome.result["steps_skipped"] == 1, (
        "no step was skipped, so the false condition ran anyway and a "
        "`when` decides nothing"
    )


@pytest.mark.asyncio
async def test_a_skipped_step_never_reaches_the_world(world, registry):
    """Skipping is not a quiet success.

    A condition that evaluated correctly and dispatched anyway would
    look identical in the wrapper's own counters and completely
    different in the town. The invocation log is where the difference
    shows, so that is where this looks.
    """
    await spawn_agent(world, "n01", FIELD)
    await _file(
        world,
        registry,
        "n01",
        "never_say",
        [
            {
                "tool_name": "say",
                "params": {"content": "this must never be spoken"},
                "when": {"left": 1, "op": "==", "right": 2},
            },
        ],
    )
    world.tool_invocations.clear()
    spoken_before = len(world.utterances)

    await registry.execute(world, "n01", "never_say", {})

    assert not [r for r in world.tool_invocations if r.tool_name == "say"]
    assert len(world.utterances) == spoken_before


@pytest.mark.asyncio
async def test_a_condition_cannot_read_the_world(world, registry):
    """A bare word in a condition is a LITERAL, never a lookup.

    `{"left": "location_id", "op": "==", "right": "Kasukabe Farmland"}`
    compares two strings and is false, even though the resident really
    is standing in Kasukabe Farmland. That looks unhelpful and is the
    point: the moment a bare name could mean "ask the world", the
    condition would be reading town state on a path with no gate, no
    price and no record.
    """
    assert (
        program.evaluate_condition(
            _condition("location_id", "==", FIELD), {}, {}
        )
        is False
    )
    assert (
        program.evaluate_condition(
            _condition("@here.location_id", "==", FIELD),
            {},
            {"here": {"location_id": FIELD}},
        )
        is True
    )


def _condition(left, op, right):
    from world.tools.models import StepCondition

    return StepCondition(left=left, op=op, right=right)


@pytest.mark.asyncio
async def test_an_uncomparable_comparison_is_false_not_a_crash(world, registry):
    """Mid-program is the wrong place to raise.

    "Is this string less than that number" has an answer - no - and a
    raise here would unmake every act the program had already completed
    and been charged for. A condition is a question, and a question that
    cannot be answered yes is answered no.
    """
    assert program.evaluate_condition(_condition("apples", "<", 3), {}, {}) is False
    assert program.evaluate_condition(_condition(None, ">=", 1), {}, {}) is False
    assert program.evaluate_condition(_condition(3, "in", 7), {}, {}) is False


@pytest.mark.asyncio
async def test_truthy_and_falsy_need_no_right_hand_side(world, registry):
    """The two one-sided ops, which is how a program asks "did that work".

    Most step results are dicts with a field worth testing; some are
    just present or absent. Requiring a `right` for those would make an
    author invent one.
    """
    assert program.evaluate_condition(_condition("@ok", "truthy", None), {}, {"ok": 1})
    assert program.evaluate_condition(_condition("@no", "falsy", None), {}, {"no": []})


@pytest.mark.asyncio
async def test_a_binding_reads_one_level_and_the_whole_result(world, registry):
    """`@name` is the result; `@name.field` is one field of it.

    Two levels is refused at filing rather than resolved here - see
    `test_a_program_that_cannot_run_is_refused_at_filing`. What this
    holds is that both supported shapes actually resolve, because a
    resolver that only handled the dotted form would silently hand a
    whole dict to a parameter expecting a number.
    """
    bindings = {"shelf": {"quantity": 4, "item": "flour"}}
    assert program.resolve_reference("@shelf", {}, bindings) == bindings["shelf"]
    assert program.resolve_reference("@shelf.quantity", {}, bindings) == 4
    assert program.resolve_reference("@shelf.missing", {}, bindings) is None
    assert program.resolve_reference("plain", {}, bindings) == "plain"
    assert program.resolve_reference(7, {}, bindings) == 7
