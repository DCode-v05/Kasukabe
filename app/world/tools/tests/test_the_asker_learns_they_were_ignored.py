"""Run 19, issues 6 and 7: the two halves of a distorted conversation.

**Issue 6.** Residents repeated themselves verbatim.

    L208  Marta Thorne says the same sentence twice, four ticks apart:
          "Corwin. Sit down and tell me what you mean by 'learning how to
          feed myself the hard way.'"
    L142, L146  Tova Okonjo demands the same yes-or-no answer of Father
          Anselm at t40 and again at t43. He never answers either.
    L110, L196  Silas Vance delivers the same speech about his unsold
          plank at t40 and t41.

`open_questions` already carries an unanswered question forward to the
person who was ASKED. Nothing carries anything back to the person who
asked. So a resident who spoke into silence cannot tell the difference
between "they have not answered" and "I never said it", and asking again
is the rational move. It is also the expensive one: every repeat is a
billed turn that changes nothing.

**Issue 7.** One remark, addressed to three people, recorded against one.

    L132  Ezra Okonjo's utterance has `listener_agent_id` = Father Anselm
          and contains three speeches: one to Anselm ("You want temple
          bread, walk to temple"), one to Tova ("Girl. Two sim for
          walking, you said?"), and one to the room.

Tova was standing there and had asked the question he was answering, and
the record does not show her as addressed. So `open_questions` still owed
her an answer that had in fact been given, and the conversation roster
credited it to the wrong person.

The fix for both is the same fact, recorded once: WHO a remark was
actually addressed to, as a list.
"""

from __future__ import annotations

import pytest

from world.tools.state import WorldState

from .conftest import spawn_agent

ROOM = "Kasukabe Market Square"


async def _cast(world: WorldState, *ids: str) -> None:
    names = {
        "n15": "Ezra Okonjo", "n18": "Tova Okonjo", "n19": "Father Anselm",
        "n07": "Marta Thorne", "n09": "Corwin Thorne",
    }
    for agent_id in ids:
        await spawn_agent(world, agent_id, ROOM)
        world.agent_names[agent_id] = names.get(agent_id, f"Resident {agent_id}")


@pytest.mark.asyncio
async def test_a_remark_naming_two_people_is_recorded_against_both(
    world: WorldState, registry
) -> None:
    """Ezra's exact utterance shape: addressed to Anselm by parameter,
    answering Tova by name inside the words."""
    await _cast(world, "n15", "n18", "n19")

    outcome = await registry.execute(world, "n15", "say", {
        "listener_agent_id": "n19",
        "content": (
            "Father Anselm. You want temple bread, walk to temple. "
            "Tova, two sim for walking, you said? That's the flat of it."
        ),
    })

    addressed = set(outcome.result.get("addressed") or [])
    assert "Father Anselm" in addressed
    assert "Tova Okonjo" in addressed, (
        "the person actually answered is not recorded as addressed, so the "
        "world still thinks she is owed an answer"
    )


@pytest.mark.asyncio
async def test_being_named_in_the_words_discharges_the_question_you_asked(
    world: WorldState, registry
) -> None:
    """The consequence that matters: Tova asked, Ezra answered her inside a
    remark addressed to somebody else, and the world must not go on telling
    her she is unanswered."""
    await _cast(world, "n15", "n18", "n19")

    await registry.execute(world, "n18", "say", {
        "listener_agent_id": "n15",
        "content": "Ezra, are you holding anything? Yes or no.",
    })
    await registry.execute(world, "n15", "say", {
        "listener_agent_id": "n19",
        "content": "Father Anselm, records will keep. Tova, two sim for walking is the flat of it.",
    })

    still_owed = world.open_questions("n15", world.tick())
    assert not any(q.get("speaker_agent_id") == "n18" for q in still_owed), (
        "Ezra answered Tova by name and is still shown as owing her an answer"
    )


@pytest.mark.asyncio
async def test_a_remark_naming_nobody_addresses_nobody_in_particular(
    world: WorldState, registry
) -> None:
    """Speaking to the room must not silently address everyone in it."""
    await _cast(world, "n15", "n18")
    outcome = await registry.execute(world, "n15", "say", {"content": "Morning, all."})
    assert not (outcome.result.get("addressed") or [])


@pytest.mark.asyncio
async def test_a_resident_is_told_which_of_its_questions_went_unanswered(
    world: WorldState, registry
) -> None:
    await _cast(world, "n18", "n19")

    await registry.execute(world, "n18", "say", {
        "listener_agent_id": "n19",
        "content": "Father Anselm. Are you holding anything? Yes or no.",
    })

    waiting = world.awaiting_reply("n18", world.tick())
    assert waiting, "the asker is told nothing, so asking again is the rational move"
    assert waiting[0]["listener_agent_id"] == "n19"
    assert "Father Anselm" in str(waiting[0].get("listener_name"))


@pytest.mark.asyncio
async def test_once_they_answer_the_asker_stops_being_told(
    world: WorldState, registry
) -> None:
    """A notice that never clears is furniture, and gets read as furniture."""
    await _cast(world, "n18", "n19")

    await registry.execute(world, "n18", "say", {
        "listener_agent_id": "n19", "content": "Are you holding anything?",
    })
    await registry.execute(world, "n19", "say", {
        "listener_agent_id": "n18", "content": "Tova. I hold nothing.",
    })

    assert not world.awaiting_reply("n18", world.tick())


@pytest.mark.asyncio
async def test_speaking_to_the_room_is_not_something_you_are_owed_an_answer_to(
    world: WorldState, registry
) -> None:
    """Only a directed remark creates an expectation. "Morning, all"
    leaves nobody owing anybody anything."""
    await _cast(world, "n18", "n19")
    await registry.execute(world, "n18", "say", {"content": "Morning, all."})
    assert not world.awaiting_reply("n18", world.tick())


@pytest.mark.asyncio
async def test_an_old_unanswered_question_stops_being_raised(
    world: WorldState, registry
) -> None:
    """A question from an hour ago is not a live obligation, and showing it
    forever would turn the notice into a backlog nobody reads."""
    await _cast(world, "n18", "n19")
    await registry.execute(world, "n18", "say", {
        "listener_agent_id": "n19", "content": "Are you holding anything?",
    })
    assert not world.awaiting_reply("n18", world.tick() + 500)
