"""A conversation needs somebody to have it with.

THE OPERATOR'S RULE, in their words: "If there's no chat, there will be
no open ended chat. If there are people around, only then we will have
chat option available and the engines can chat."

WHAT AN OPEN-ENDED CHAT IS. `say` threads a remark into the conversation
already happening in the room, and mints a fresh one when the room was
quiet. A resident standing alone and speaking therefore opened a
conversation with one participant, no topic, and nobody who could ever
answer into it. It sat open for the whole window and then closed, having
never been a conversation at all. Bram asked an empty market for a copper
buyer five times in one tick; that is five of them.

`start_conversation` had the same hole from the other end: opening one
with an empty roster was legitimate, and returned "You have opened this
on your own. Anyone who speaks here joins it." It was a table laid in an
empty room.

Neither is a conversation, and both fill the registry that the perception,
the Talk book and the township screen all read, so a run looks like it is
full of chatter that never happened.

WHAT IS NOT CHANGED. Speaking alone still WORKS - the words were really
said, the event is emitted, the utterance is recorded, and a resident who
walks in during the window still finds the remark in their perception.
What it does not do is create a thread. A remark with no thread is a
remark; a thread with one person in it is a bug that reads like data.
"""

from __future__ import annotations

import pytest

from .conftest import spawn_agent

MARKET = "Kasukabe Market Square"
MILL = "Kasukabe Mill"


async def cast(world, *ids: str, place: str = MARKET) -> None:
    for agent_id in ids:
        await spawn_agent(world, agent_id, place)
        world.agent_names[agent_id] = f"Resident {agent_id}"


# ── say ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_speaking_alone_opens_no_conversation(world, registry) -> None:
    await cast(world, "n01")
    outcome = await registry.dispatch(
        world, "n01", "say", {"content": "Anyone buying copper?"}
    )
    assert outcome.result.get("conversation_id") is None
    assert world.conversations == {}, (
        "a conversation with one participant is a conversation nobody can "
        "answer into, and it is drawn on the screen as if it were real"
    )


@pytest.mark.asyncio
async def test_speaking_alone_still_happens(world, registry) -> None:
    """The words were really said. Not threading them is a bookkeeping
    decision, not a refusal - a resident who walks in during the window
    still hears it."""
    await cast(world, "n01")
    outcome = await registry.dispatch(
        world, "n01", "say", {"content": "Anyone buying copper?"}
    )
    assert [event.type for event in outcome.events] == ["agent.utterance.spoken"]
    assert outcome.events[0].payload["content"] == "Anyone buying copper?"


@pytest.mark.asyncio
async def test_speaking_with_somebody_present_opens_one(world, registry) -> None:
    await cast(world, "n01", "n02")
    outcome = await registry.dispatch(
        world, "n01", "say", {"content": "Anyone buying copper?"}
    )
    assert outcome.result.get("conversation_id")
    assert len(world.conversations) == 1


@pytest.mark.asyncio
async def test_somebody_in_another_room_does_not_count(world, registry) -> None:
    """Speech reaches the room. A resident at the mill is not somebody you
    are talking to, however many of them there are."""
    await cast(world, "n01", place=MARKET)
    await cast(world, "n02", "n03", place=MILL)
    await registry.dispatch(world, "n01", "say", {"content": "Anyone buying copper?"})
    assert world.conversations == {}


@pytest.mark.asyncio
async def test_a_second_speaker_arriving_joins_the_thread(world, registry) -> None:
    """The one behaviour that must survive: the first remark in a room
    with people present opens the thread, and the answer lands in it."""
    await cast(world, "n01", "n02")
    first = await registry.dispatch(world, "n01", "say", {"content": "Copper?"})
    second = await registry.dispatch(world, "n02", "say", {"content": "How much?"})
    assert second.result["conversation_id"] == first.result["conversation_id"]
    assert len(world.conversations) == 1


# ── start_conversation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_opening_a_conversation_alone_is_refused(world, registry) -> None:
    await cast(world, "n01")
    outcome = await registry.dispatch(
        world, "n01", "start_conversation", {"topic": "the boundary marker"}
    )
    assert outcome.result["opened"] is False
    assert world.conversations == {}
    assert "note" in outcome.result


@pytest.mark.asyncio
async def test_naming_an_absent_person_opens_it_anyway_and_says_who_is_here(
    world, registry
) -> None:
    """People are around, so a chat is available - the roster being short
    is not the same as the room being empty. Told the way `say` reports an
    absent addressee: the correction an agent can act on is who IS here,
    not that its list did not come out."""
    await cast(world, "n01", "n02")
    await cast(world, "n09", place=MILL)
    outcome = await registry.dispatch(
        world, "n01", "start_conversation",
        {"topic": "the boundary", "with_agent_ids": ["n09"]},
    )
    assert outcome.result["opened"] is True
    assert "Resident n02" in outcome.result["note"], outcome.result["note"]


@pytest.mark.asyncio
async def test_naming_only_absent_people_in_an_empty_room_is_refused(
    world, registry
) -> None:
    """The case the refusal exists for: a resident who wanted the
    household round a table, standing where none of them are."""
    await cast(world, "n01")
    await cast(world, "n09", place=MILL)
    outcome = await registry.dispatch(
        world, "n01", "start_conversation",
        {"topic": "the boundary", "with_agent_ids": ["n09"]},
    )
    assert outcome.result["opened"] is False
    assert world.conversations == {}


@pytest.mark.asyncio
async def test_naming_nobody_still_works_when_the_room_is_full(world, registry) -> None:
    """An empty roster is not the same as an empty room. Opening a table
    where three people are standing is exactly what convening is for, and
    whoever speaks joins."""
    await cast(world, "n01", "n02", "n03")
    outcome = await registry.dispatch(
        world, "n01", "start_conversation", {"topic": "the boundary marker"}
    )
    assert outcome.result["opened"] is True
    assert len(world.conversations) == 1


@pytest.mark.asyncio
async def test_a_refused_opening_leaves_no_event(world, registry) -> None:
    """A conversation that did not open must not be counted as one by the
    run report, which reads events."""
    await cast(world, "n01")
    outcome = await registry.dispatch(
        world, "n01", "start_conversation", {"topic": "the boundary marker"}
    )
    assert outcome.events == []
