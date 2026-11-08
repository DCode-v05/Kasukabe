"""A resident must be able to see the conversation they are standing in.

Runs 18 and 19 both ended with the same finding: `start_conversation` was
registered, priced, served to all twenty engines, and never once called.
Reading a real `look_around` result from those runs explains it without
any need to speculate:

    {"location_id": "carpentry", "purpose": "...", "people_here": [],
     "places_you_could_go": [...]}

The world knew who was in which conversation, how many turns it had run
and whether it was still open. None of it reached the agent. A resident
standing in a room where nine people were mid-conversation was told the
names of the people and nothing about the conversation - no id to answer
into, and no sign that convening one was a thing that could be done.

A tool nothing in your view refers to is not a tool you can choose. So
this is perception, reported the way `people_here` is reported: what is
happening here, as fact. Nothing tells the resident to talk.
"""

from __future__ import annotations

import pytest

from world.tools.state import WorldState

from .conftest import spawn_agent

MARKET = "Kasukabe Market Square"


async def _cast(world: WorldState, *ids: str) -> None:
    for agent_id in ids:
        await spawn_agent(world, agent_id, MARKET)
        world.agent_names[agent_id] = f"Resident {agent_id}"


@pytest.mark.asyncio
async def test_looking_around_shows_the_conversation_you_are_standing_in(
    world: WorldState, registry
) -> None:
    await _cast(world, "n01", "n02", "n03")
    started = await registry.execute(world, "n01", "start_conversation", {
        "topic": "the boundary marker", "with_agent_ids": ["n02", "n03"],
    })
    conversation_id = started.result["conversation_id"]
    await registry.execute(world, "n01", "say", {
        "content": "It stood by the elm.", "conversation_id": conversation_id,
    })

    seen = (await registry.execute(world, "n02", "look_around", {})).result
    conversations = seen.get("conversations_here")
    assert conversations, "the conversation is invisible to somebody standing in it"

    first = conversations[0]
    assert first["conversation_id"] == conversation_id, (
        "no id, so there is nothing to answer into"
    )
    assert first["topic"] == "the boundary marker"
    assert first["turns"] == 1
    assert set(first["participants"]) >= {"Resident n01", "Resident n02", "Resident n03"}


@pytest.mark.asyncio
async def test_a_conversation_in_another_room_is_not_shown(
    world: WorldState, registry
) -> None:
    """Perception is what is here. A conversation across town is not."""
    await _cast(world, "n01", "n02")
    await spawn_agent(world, "n09", "Kasukabe Farmland")
    await registry.execute(world, "n01", "start_conversation", {"topic": "grain"})

    seen = (await registry.execute(world, "n09", "look_around", {})).result
    assert not seen.get("conversations_here")


@pytest.mark.asyncio
async def test_a_closed_conversation_is_not_offered_as_one_to_join(
    world: WorldState, registry
) -> None:
    from world.tools.state import CONVERSATION_WINDOW_TICKS

    await _cast(world, "n01", "n02")
    await registry.execute(world, "n01", "start_conversation", {"topic": "grain"})
    world.close_stale_conversations(world.tick() + CONVERSATION_WINDOW_TICKS + 1)

    seen = (await registry.execute(world, "n02", "look_around", {})).result
    assert not seen.get("conversations_here"), (
        "a closed conversation shown as joinable sends the agent at a dead id"
    )


@pytest.mark.asyncio
async def test_a_room_with_people_and_no_conversation_says_so(
    world: WorldState, registry
) -> None:
    """The other half of the affordance. Standing with three people and no
    conversation open is exactly the moment convening one is possible, and
    the agent cannot know that from `people_here` alone."""
    await _cast(world, "n01", "n02", "n03", "n04")

    seen = (await registry.execute(world, "n01", "look_around", {})).result
    assert seen.get("conversations_here") == []
    note = str(seen.get("note", ""))
    assert "start_conversation" in note, (
        f"nothing in the resident's view names the tool that fits: {seen}"
    )


@pytest.mark.asyncio
async def test_an_empty_room_does_not_suggest_convening_anything(
    world: WorldState, registry
) -> None:
    """A note that appears when nobody is there teaches the wrong thing:
    convening alone is legal but pointless, and an agent that learns to do
    it in empty rooms has learned noise."""
    await _cast(world, "n01")
    seen = (await registry.execute(world, "n01", "look_around", {})).result
    assert "start_conversation" not in str(seen.get("note", ""))
