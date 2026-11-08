"""TOOL-11.4: a resident can put its own invented tool on a routine.

`CONTINUABLE_TOOLS` was a fixed list of six primitives, so a tool a resident
authored with `toolify` could never be handed to `keep_doing`. The only way
to run an invented tool twenty times was twenty model calls - strictly more
expensive than the primitive it was built to replace.

That makes DESIGN's "mastery lowers the cost of living in dollars"
unclaimable by construction. A resident who automates its own job should pay
LESS to do it, and until this it paid the same or more.
"""
from __future__ import annotations

import pytest

from world.tools.models import CustomToolDefinition
from world.tools.tests.conftest import spawn_agent
from world.tools.state import CONTINUABLE_TOOLS


def _invented(world, name: str, inventor: str = "n01") -> None:
    world.custom_tools[name] = CustomToolDefinition(
        tool_name=name,
        description="Turn flax into rope, the way I worked it out.",
        creator_agent_id=inventor,
        param_schema={},
        steps=[],
        created_tick=0,
    )


def test_the_six_primitives_are_still_continuable():
    assert {"harvest", "mine", "fell_timber", "craft", "cook", "rest"} <= set(CONTINUABLE_TOOLS)


@pytest.mark.asyncio
async def test_keep_doing_accepts_a_tool_this_town_wrote(world_and_registry):
    world, registry = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Farmland")
    _invented(world, "rope_making")

    outcome = await registry.execute(
        world, "n01", "keep_doing",
        {"tool": "rope_making", "params": {}, "until": {"units": 3}},
    )
    assert outcome is not None
    assert world.continuing.get("n01") is not None, (
        "an authored tool cannot be put on a routine, so automating your own "
        "job costs the same twenty model calls it always did (TOOL-11.4)"
    )
    assert world.continuing["n01"].tool == "rope_making"


@pytest.mark.asyncio
async def test_a_tool_nobody_invented_is_still_refused(world_and_registry):
    """The gate still means something: this is not 'anything goes'."""
    world, registry = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Farmland")
    from world.tools.errors import ToolValidationError

    with pytest.raises(ToolValidationError):
        await registry.execute(
            world, "n01", "keep_doing",
            {"tool": "say", "params": {}, "until": {"units": 3}},
        )


@pytest.mark.asyncio
async def test_the_refusal_names_invented_tools_as_an_option(world_and_registry):
    """A refusal that does not say what IS allowed teaches nothing."""
    world, registry = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Farmland")
    from world.tools.errors import ToolValidationError

    with pytest.raises(ToolValidationError) as caught:
        await registry.execute(
            world, "n01", "keep_doing",
            {"tool": "say", "params": {}, "until": {"units": 3}},
        )
    assert "invented" in str(caught.value).lower()
