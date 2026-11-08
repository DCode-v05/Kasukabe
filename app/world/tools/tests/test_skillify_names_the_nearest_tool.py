"""A refused step names the tool the author meant, not the first twelve.

Run 26: Farid Ravensworth filed a seven-step `skillify` of the whole bread
chain - farmland, harvest, mill, craft, kitchen, cook, grocery - the most
ambitious authoring of the run. It was refused on naming:

    step 1: no skill named 'move_to_farmland'
    step 4: no tool named 'craft:wheat->flour' - tools are named like:
            admit_to_org, appoint_police, approve_patent, arrest,
            ask_for_a_child, auction_land, build_structure, buy, buy_energy,
            check_debts, come_of_age, convene

The hint was `sorted(registry.tools)[:12]`: the same twelve alphabetical
names for every mistake anybody made. The two tools he needed were already
inside his own refs.
"""

from __future__ import annotations

import pytest

from .conftest import spawn_agent


async def _farids_attempt(world, registry) -> str:
    await spawn_agent(world, "a0090", "street")
    with pytest.raises(Exception) as raised:
        await registry.execute(world, "a0090", "skillify", {
            "name": "bread_chain",
            "preconditions": "standing at the farmland with the day ahead",
            "steps": [
                {"order": 1, "kind": "skill", "ref": "move_to_farmland", "note": "walk out"},
                {"order": 2, "kind": "tool", "ref": "craft:wheat->flour", "note": "mill it"},
                {"order": 3, "kind": "tool", "ref": "cook:flour->bread", "note": "bake it"},
            ],
        })
    return str(raised.value)


@pytest.mark.asyncio
async def test_the_alphabetical_twelve_are_gone(world, registry):
    message = await _farids_attempt(world, registry)
    assert "admit_to_org" not in message, message
    assert "appoint_police" not in message, message


@pytest.mark.asyncio
async def test_a_tool_ref_is_pointed_at_the_verb_inside_it(world, registry):
    message = await _farids_attempt(world, registry)
    assert "craft" in message.split("craft:wheat->flour", 1)[1], message
    assert "cook" in message.split("cook:flour->bread", 1)[1], message


@pytest.mark.asyncio
async def test_a_skill_ref_that_names_a_tool_says_so(world, registry):
    message = await _farids_attempt(world, registry)
    assert "'move' is a tool" in message, message
