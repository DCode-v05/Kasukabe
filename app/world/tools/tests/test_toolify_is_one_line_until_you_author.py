"""X8, operator decision 2026-09-21: `toolify` is one line until you author.

Measured against the 50-resident run, `toolify` was about 911 input tokens on
every resident call - a third of what the never-called verbs cost to carry -
and it had 0 calls in every run since 25. It stays (T3 has no other route to
a tool that outlives the turn), shrunk to a line that keeps the two things a
resident needs to decide: what it makes, and that it is free. Its steps are
`do_in_order`'s, which is served in full. A resident who has already authored
a skill or a tool gets it in full again.
"""

from __future__ import annotations

import json

import pytest

from world.tools.models import CustomToolDefinition, SkillDefinition, ToolStep
from world.tools.registry import FULL_ONCE_AUTHORING, INDEX_NOTES, tool_registry_rows
from world.tools.tests.conftest import spawn_agent

FIELD = "Kasukabe Farmland"


def _row(registry, world, agent_id: str, name: str) -> dict:
    return {row["name"]: row for row in tool_registry_rows(registry, world, agent_id)}[name]


@pytest.mark.asyncio
async def test_a_resident_who_has_authored_nothing_gets_one_line(world, registry):
    await spawn_agent(world, "n01", FIELD)

    row = _row(registry, world, "n01", "toolify")

    assert row.get("elsewhere") is True
    assert len(json.dumps(row)) < 800, "the point of the decision is the tokens"


@pytest.mark.asyncio
async def test_the_line_keeps_what_a_resident_needs_to_decide(world, registry):
    await spawn_agent(world, "n01", FIELD)

    text = _row(registry, world, "n01", "toolify")["description"]

    assert "new tool" in text                      # what it makes
    assert "the wrapper itself is free" in text     # what it costs
    assert "do_in_order" in text                    # where the step shape is
    assert "describe_primitives" in text            # where the rest is
    assert "Needs: " in text and "steps" in text    # it can still be called


@pytest.mark.asyncio
async def test_the_step_shape_it_points_to_is_served_in_full(world, registry):
    await spawn_agent(world, "n01", FIELD)

    step = _row(registry, world, "n01", "do_in_order")["input_schema"]["$defs"]["ToolStep"]

    assert {"tool_name", "params", "when", "repeat", "bind"} <= set(step["properties"])


@pytest.mark.asyncio
async def test_an_author_of_a_tool_gets_it_in_full(world, registry):
    await spawn_agent(world, "n01", FIELD)
    world.custom_tools["n01_harvest_twice"] = CustomToolDefinition(
        tool_name="n01_harvest_twice", description="harvest twice", creator_agent_id="n01",
        steps=[ToolStep(tool_name="harvest", params={})],
    )

    row = _row(registry, world, "n01", "toolify")

    assert not row.get("elsewhere")
    assert "$defs" in row["input_schema"]


@pytest.mark.asyncio
async def test_an_author_of_a_skill_gets_it_in_full(world, registry):
    await spawn_agent(world, "n01", FIELD)
    await spawn_agent(world, "n02", FIELD)
    world.skill_definitions["bake"] = SkillDefinition.model_construct(
        name="bake", author_agent_id="n01"
    )

    assert not _row(registry, world, "n01", "toolify").get("elsewhere")
    assert _row(registry, world, "n02", "toolify").get("elsewhere"), (
        "somebody else's skill is not your authorship"
    )


@pytest.mark.asyncio
async def test_a_one_line_toolify_can_still_be_called(world, registry):
    """One line is not a hidden verb: the world still takes the full call."""
    await spawn_agent(world, "n01", FIELD)
    assert _row(registry, world, "n01", "toolify").get("elsewhere")

    outcome = await registry.execute(world, "n01", "toolify", {
        "name": "look_twice", "description": "look around twice",
        "steps": [{"tool_name": "look_around", "params": {}},
                  {"tool_name": "look_around", "params": {}}],
    })

    assert outcome is not None
    assert not _row(registry, world, "n01", "toolify").get("elsewhere"), (
        "having authored one, the resident is served it in full"
    )


def test_only_toolify_is_held_back() -> None:
    assert FULL_ONCE_AUTHORING == frozenset({"toolify"})
    assert set(INDEX_NOTES) <= FULL_ONCE_AUTHORING
