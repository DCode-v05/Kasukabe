"""A skill that runs another skill discounted nothing.

Run 29: Silas Vance wrote `saw_planks_from_timber` (check, then craft) and,
two ticks later, `saw_through_timber_stack` - check, then run
`saw_planks_from_timber`. The first time in this project a resident built
on its own authored work, and the report printed the second one as
"discounts nothing".

It was not a display quirk. `WorldState.skill_tools` is what
`action_costs.skills_for_tool` reads on every dispatch to decide which held
skills make a tool cheaper, and it read only a skill's DIRECT `tool` steps.
So a resident taught `saw_through_timber_stack` alone - the composite, the
thing worth teaching - paid full price for every plank it cut by walking it.
Composition was taxed, which is the shape TOOL-31 removed from invented
tools ("a tower costs exactly its leaves") and which had survived here.

Safe to flatten for the reason `discounts_tools` is derived at all: a
discount needs proficiency, and proficiency is earned only by walking the
workflow end to end, which means doing the sub-skill's work. Citing a
skill with forty tool steps buys nothing until those forty acts are done.
"""

from __future__ import annotations

import pytest

from world.tools.tests.conftest import spawn_agent

WHERE = "Kasukabe Farmland"


async def _two_skills_one_on_the_other(world, registry, author: str = "n01") -> None:
    await spawn_agent(world, author, WHERE)
    await registry.dispatch(world, author, "skillify", {
        "name": "saw_planks_from_timber", "preconditions": "at the sawmill",
        "steps": [
            {"order": 1, "kind": "check", "ref": "timber_count", "note": "timber in hand"},
            {"order": 2, "kind": "tool", "ref": "craft", "note": "saw until none remains"},
        ],
    })
    await registry.dispatch(world, author, "skillify", {
        "name": "saw_through_timber_stack", "preconditions": "a stack of timber",
        "steps": [
            {"order": 1, "kind": "check", "ref": "timber_count", "note": "count the stack"},
            {"order": 2, "kind": "skill", "ref": "saw_planks_from_timber", "note": "run the saw"},
        ],
    })


@pytest.mark.asyncio
async def test_the_composite_discounts_the_tools_its_sub_skill_performs(world, registry):
    """What dispatch reads, so this is the price and not the label."""
    await _two_skills_one_on_the_other(world, registry)

    tools = world.skill_tools

    assert "craft" in tools["saw_through_timber_stack"], tools
    assert tools["saw_planks_from_timber"] == ["craft"]


@pytest.mark.asyncio
async def test_what_is_filed_says_so_too(world, registry):
    """`discounts_tools` is what the report and the workshop show a person.
    It printed "discounts nothing" for a skill that cuts planks."""
    await _two_skills_one_on_the_other(world, registry)

    filed = world.skill_definitions["saw_through_timber_stack"].discounts_tools

    assert "craft" in filed, filed


@pytest.mark.asyncio
async def test_a_skill_only_discounts_what_it_can_actually_walk(world, registry):
    """A PENDING standing order discounts nothing on its own, and it must
    not start discounting by being named as a step of an ordinary skill."""
    await _two_skills_one_on_the_other(world, registry)
    world.skill_definitions["saw_planks_from_timber"].status = "PENDING_COUNCIL"

    tools = world.skill_tools

    assert "saw_planks_from_timber" not in tools
    assert "craft" not in tools["saw_through_timber_stack"], tools
