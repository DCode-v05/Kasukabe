"""Confusing the two authoring shapes must teach, not just refuse.

`skillify` and `toolify` take different steps, and for a real reason:

    SkillStep  {order, kind, ref, note}   a plan entry. `kind` may be
                                          "check", which dispatches
                                          nothing - a note to your future
                                          self ("is the saw sharp?").
    ToolStep   {tool_name, params}        an actual call. A composite tool
                                          runs its steps, so every one of
                                          them has to be executable.

They are not arbitrarily different and unifying them would break one of
the two: a tool made of unexecutable checks cannot run, and a procedure
that cannot contain a check cannot say the thing procedures exist to say.

But nothing told the author. An agent that had just written a skill and
went on to write a tool - the exact sequence the town is built to
encourage - got back:

    toolify: invalid params: steps.0.tool_name: Field required;
    steps.1.tool_name: Field required

which names the missing field and never says the shape differs, what the
right one is, or which tool wants which. Worse, a params-level refusal is
raised before the handler runs, so it is the one authoring refusal that
carries no `authoring_context` at all: the retry is as blind as the first
try, at another turn's cost.
"""

from __future__ import annotations

import pytest

from world.tools.state import WorldState

from .conftest import spawn_agent

FARM = "Kasukabe Farmland"


async def _author(world: WorldState) -> None:
    await spawn_agent(world, "n01", FARM)
    world.agent_names["n01"] = "Wren Thorne"


@pytest.mark.asyncio
async def test_skill_shaped_steps_given_to_toolify_name_both_shapes(
    world: WorldState, registry
) -> None:
    from world.tools.errors import ToolValidationError

    await _author(world)
    with pytest.raises(ToolValidationError) as caught:
        await registry.execute(world, "n01", "toolify", {
            "name": "walk_and_cut", "description": "go and cut",
            "steps": [{"order": 1, "kind": "tool", "ref": "move"}],
        })

    message = str(caught.value).lower()
    assert "tool_name" in message, "the author still needs the field name"
    assert "skillify" in message, "nothing tells the author which tool it used the shape of"
    assert "check" in message, (
        "the reason the shapes differ is never stated, so the author learns a "
        "rule without its reason and cannot generalise it"
    )


@pytest.mark.asyncio
async def test_tool_shaped_steps_given_to_skillify_name_both_shapes(
    world: WorldState, registry
) -> None:
    """The same confusion in the other direction, which is likelier: a
    resident writes a tool first, then a procedure."""
    from world.tools.errors import ToolValidationError

    await _author(world)
    with pytest.raises(ToolValidationError) as caught:
        await registry.execute(world, "n01", "skillify", {
            "name": "Morning Harvest",
            "steps": [{"tool_name": "harvest"}],
        })

    message = str(caught.value).lower()
    assert "toolify" in message
    assert "kind" in message and "ref" in message


@pytest.mark.asyncio
async def test_a_correct_filing_is_not_disturbed_by_the_new_check(
    world: WorldState, registry
) -> None:
    """The guard must not cost a well-formed author anything."""
    await _author(world)
    ok = await registry.execute(world, "n01", "skillify", {
        "name": "Morning Harvest",
        "steps": [
            {"order": 1, "kind": "tool", "ref": "move", "note": "to the field"},
            {"order": 2, "kind": "check", "note": "is anything ripe?"},
            {"order": 3, "kind": "tool", "ref": "harvest"},
        ],
    })
    assert "Morning Harvest" in world.skill_definitions, (
        f"a well-formed filing was refused or lost: {ok.result}"
    )


@pytest.mark.asyncio
async def test_the_shape_difference_is_stated_in_both_tool_descriptions(
    world: WorldState, registry
) -> None:
    """An author reads the description before it reads an error. The
    difference belongs there too, or the error is the only place a rule
    of the system is written down."""
    skillify = registry.tools["skillify"].description.lower()
    toolify = registry.tools["toolify"].description.lower()
    assert "kind" in skillify and "ref" in skillify
    assert "tool_name" in toolify
