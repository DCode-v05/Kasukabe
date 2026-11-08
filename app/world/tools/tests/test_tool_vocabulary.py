"""Part B: agents can see what they can do, and the town's vocabulary grows.

`describe_primitives` is what makes composing informed rather than guesswork,
and `ToolRegistry.version` is what lets an engine notice that a tool invented
mid-run exists at all.
"""

import pytest

from world.tools.bootstrap import build_world
from world.tools.locations import farmland_location
from world.tools.tests.conftest import spawn_agent

FIELD = farmland_location("wheat")


async def _world():
    world, registry = await build_world(seed=51)
    await spawn_agent(world, "ada", FIELD, starting_sim=100_000)
    return world, registry


@pytest.mark.asyncio
async def test_describe_primitives_returns_the_callable_vocabulary_with_prices() -> None:
    world, registry = await _world()

    result = await registry.execute(world, "ada", "describe_primitives", {})
    rows = {row["name"]: row for row in result.result["tools"]}

    assert result.result["count"] > 10
    assert "harvest" in rows
    assert rows["harvest"]["sim_cost_minimum"] > 0, "a price the agent can compare before committing"
    assert "crop" in rows["harvest"]["params"]
    assert rows["harvest"]["at_location"] is not None, "harvest is location-gated, and says so"


@pytest.mark.asyncio
async def test_the_price_shown_is_this_agent_s_price_not_a_list_price() -> None:
    """An agent choosing between two ways to do a job should not have to
    apply its own discount in its head."""
    world, registry = await _world()

    before = await registry.execute(world, "ada", "describe_primitives", {"contains": "harvest"})
    quoted_before = before.result["tools"][0]["sim_cost_minimum"]

    for _ in range(40):
        await registry.execute(world, "ada", "harvest", {"crop": "wheat", "quantity": 1})

    after = await registry.execute(world, "ada", "describe_primitives", {"contains": "harvest"})
    row = after.result["tools"][0]
    assert row["your_proficiency"] > 0
    assert row["sim_cost_minimum"] < quoted_before, "practice shows up in the quoted price"


@pytest.mark.asyncio
async def test_the_filter_narrows_the_vocabulary() -> None:
    world, registry = await _world()
    everything = await registry.execute(world, "ada", "describe_primitives", {})
    narrowed = await registry.execute(world, "ada", "describe_primitives", {"contains": "harvest"})
    assert 0 < narrowed.result["count"] < everything.result["count"]


@pytest.mark.asyncio
async def test_the_vocabulary_is_what_YOU_can_call_not_what_exists() -> None:
    """An ordinary resident is not shown the subpoena tools at all - they are
    invisible to it, and describe_primitives reuses the registry's own
    visibility check rather than inventing a second, looser one. Listing
    powers an agent cannot use would invite it to write workflows that fail at
    their first step."""
    world, registry = await _world()

    result = await registry.execute(world, "ada", "describe_primitives", {})
    names = {row["name"] for row in result.result["tools"]}
    assert "file_subpoena" not in names
    assert "harvest" in names


@pytest.mark.asyncio
async def test_a_seat_holder_sees_the_powers_of_the_seat_and_they_are_marked() -> None:
    """So an agent composing a workflow can tell, before filing it, that what
    it is writing is a procedure of state."""
    from world.charter.models import GovernmentState

    world, registry = await _world()
    await world.charter.store.set_government_state(GovernmentState(pm_agent_id="ada"))

    result = await registry.execute(world, "ada", "describe_primitives", {"contains": "subpoena"})
    rows = [r for r in result.result["tools"] if r["name"] == "file_subpoena"]
    assert rows, "a sitting PM can see the powers of the chair"
    assert rows[0]["requires_seat"] is True


@pytest.mark.asyncio
async def test_registering_a_tool_bumps_the_registry_version() -> None:
    """The staleness signal an engine polls. Bumped in register(), the single
    choke point every invented tool passes through."""
    world, registry = await _world()
    before = registry.version

    from pydantic import BaseModel

    from world.tools.registry import ActorRequirements, ToolNamespace, ToolSpec

    class Empty(BaseModel):
        pass

    async def handler(world, agent, params):
        from world.tools.registry import ToolOutcome
        return ToolOutcome(result={}, events=[])

    registry.register(
        ToolSpec(
            name="rope_making", description="", params_model=Empty, handler=handler,
            namespace=ToolNamespace.PUBLIC, location_gate=None,
            actor_requirements=ActorRequirements(), is_primitive=False,
        )
    )
    assert registry.version == before + 1


@pytest.mark.asyncio
async def test_a_skill_the_agent_holds_appears_in_its_own_perception() -> None:
    """B.3 end to end: the workflow reaches the prompt, with the agent's place
    in it marked."""
    from world.retrieval_depth import format_skill_playbook

    world, registry = await _world()
    await registry.execute(
        world, "ada", "skillify",
        {
            "name": "a proper harvest",
            "preconditions": "you are at a ripe field",
            "steps": [
                {"order": 1, "kind": "tool", "ref": "look_around", "note": ""},
                {"order": 2, "kind": "tool", "ref": "harvest", "note": ""},
            ],
        },
    )
    await registry.execute(world, "ada", "look_around", {})

    text = format_skill_playbook(world.skill_playbook("ada"))
    assert "YOU KNOW: a proper harvest" in text
    assert "you are at step 2" in text
    assert "✓ done" in text, "the step already taken is marked, not dropped"
    assert "> " in text and "harvest" in text


@pytest.mark.asyncio
async def test_a_built_in_trade_has_no_playbook_because_it_has_no_steps() -> None:
    """farming is a level, not a procedure. Showing it as an empty workflow
    would be showing the agent nothing at all."""
    world, registry = await _world()
    await registry.execute(world, "ada", "harvest", {"crop": "wheat", "quantity": 1})

    assert world.skills["ada"]["farming"] > 0
    assert world.skill_playbook("ada") == []
