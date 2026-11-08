"""A resident carries the conversation it is in, and `say` goes there.

THE DEFECT, from `final4` (docs/runs/series/DYNAMIC_CHAT_CONSOLIDATED_REPORT.md
section 5.2). Perrin Holloway convened a five-person conversation about
"the household accounts and the hidden 400 sim", roster [Perrin, Ainsley,
Tobias, Nell, Marta], id `fd8dda6a`.

    That conversation received exactly ONE remark.

The family then held the conversation Perrin wanted - sixteen remarks,
five speakers, the 400 sim named and morally argued over - under a
DIFFERENT, implicit id `774ba02d`. The report's words: "The town held the
conversation Perrin wanted - but not in the object he created."

**Why.** `say`'s `conversation_id` is optional and the residents omit it,
so every remark falls to room-default routing: whatever thread the room
was already carrying. A convened conversation is therefore a table set in
a room where everybody carries on talking at the old one. The
consolidated report's recommendation 2, verbatim: "When a resident is on
an open conversation's roster, default their `say` to that
`conversation_id`."

**What the pointer is.** Per-resident, derived rather than stored: the
conversation this resident is currently in, resolved fresh on every read
so it cannot go stale against a conversation that closed or a resident
who walked out. `say` uses it when the caller names nothing.

**What it deliberately does not do.** It does not stop a resident naming a
different conversation - objective 3 of the spec is that two conversations
in one room stay two, and overriding an explicit id would abolish exactly
that. The pointer is the DEFAULT, never the rule.
"""

from __future__ import annotations

import pytest

from .conftest import spawn_agent

TABLE = "home:h_quarry"
ELSEWHERE = "Kasukabe Mill"


async def household(world, *ids: str, place: str = TABLE):
    for agent_id in ids:
        await spawn_agent(world, agent_id, place)
        world.agent_names[agent_id] = f"Resident {agent_id}"


# -- The pointer itself ----------------------------------------------


@pytest.mark.asyncio
async def test_a_resident_in_no_conversation_has_no_pointer(world, registry) -> None:
    await household(world, "n01")
    assert world.conversation_pointer("n01") is None


@pytest.mark.asyncio
async def test_convening_puts_the_convener_on_their_own_pointer(world, registry) -> None:
    await household(world, "n01", "n02")
    outcome = await registry.dispatch(world, "n01", "start_conversation", {
        "topic": "the household accounts", "with_agent_ids": ["n02"],
    })
    assert world.conversation_pointer("n01") == outcome.result["conversation_id"]


@pytest.mark.asyncio
async def test_an_invitee_carries_it_before_saying_a_word(world, registry) -> None:
    """Spec objective 2: the invited are on the roster before they have
    spoken. If the pointer only followed speech, Perrin's four invitees
    would each still default to the room's old thread - which is exactly
    what happened."""
    await household(world, "n01", "n02", "n03")
    outcome = await registry.dispatch(world, "n01", "start_conversation", {
        "topic": "the hidden 400 sim", "with_agent_ids": ["n02", "n03"],
    })
    for invitee in ("n02", "n03"):
        assert world.conversation_pointer(invitee) == outcome.result["conversation_id"]


@pytest.mark.asyncio
async def test_walking_out_drops_the_pointer(world, registry) -> None:
    """A conversation happens in a room. Somebody who has left is not in
    it, and must not keep speaking into it from across town."""
    await household(world, "n01", "n02")
    await registry.dispatch(world, "n01", "start_conversation", {
        "topic": "grain", "with_agent_ids": ["n02"],
    })
    # Placed directly rather than via `move`, which starts a ROUTE the
    # resident walks over several ticks - the first draft of this test
    # called `move` and then asserted the pointer had dropped, while n02
    # was still standing at the table with a journey pending. Arriving is
    # what leaves a conversation, not setting off.
    world.locations.place("n02", ELSEWHERE)
    assert world.location_of("n02") == ELSEWHERE, "premise: n02 has arrived"
    assert world.conversation_pointer("n02") is None


@pytest.mark.asyncio
async def test_a_closed_conversation_is_not_pointed_at(world, registry) -> None:
    """Derived, not stored, so it cannot outlive what it points to."""
    from world.tools.state import CONVERSATION_WINDOW_TICKS

    await household(world, "n01", "n02")
    await registry.dispatch(world, "n01", "start_conversation", {
        "topic": "grain", "with_agent_ids": ["n02"],
    })
    world.current_tick += CONVERSATION_WINDOW_TICKS + 1
    assert world.conversation_pointer("n01") is None


# -- `say` follows it ------------------------------------------------


@pytest.mark.asyncio
async def test_say_routes_into_the_convened_conversation(world, registry) -> None:
    """final4, fixed. Perrin convenes; the next remark lands in the table
    he set rather than in the room's old thread."""
    await household(world, "n01", "n02", "n03")
    convened = await registry.dispatch(world, "n01", "start_conversation", {
        "topic": "the household accounts and the hidden 400 sim",
        "with_agent_ids": ["n02", "n03"],
    })
    thread = convened.result["conversation_id"]

    said = await registry.dispatch(world, "n02", "say", {"content": "400 sim. That's the number."})
    assert said.result["conversation_id"] == thread, (
        "an invitee spoke and it landed outside the conversation they were "
        "invited to - the final4 split"
    )


@pytest.mark.asyncio
async def test_the_convened_one_wins_over_the_room_s_older_thread(world, registry) -> None:
    """THE ACTUAL final4 CASE, and the first version of this file did not
    reproduce it.

    That version convened into an empty room, where the convened
    conversation was the only one there - and `_thread_for` already
    returned it, because it returns the open conversation the speaker is a
    participant of. It passed without the fix and proved nothing.

    final4's room was already talking. Marta had opened an implicit thread
    ("Ainsley. Is Bram at the mines today?") BEFORE Perrin convened, so
    when an invitee then spoke, `_thread_for` found TWO conversations they
    were a participant of and returned the first - the older, implicit
    one. Perrin's table kept its single remark.

    Reproduced before the fix:

        implicit : 885c1a21
        convened : 8b453660
        landed in: 885c1a21   <- the old implicit one

    The newer, deliberately convened conversation wins. Convening is an
    act; drifting into the room's ambient thread is not.
    """
    await household(world, "n01", "n02", "n03")
    first = await registry.dispatch(world, "n02", "say", {
        "content": "Ainsley. Is Bram at the mines today?",
    })
    implicit = first.result["conversation_id"]

    convened = (await registry.dispatch(world, "n01", "start_conversation", {
        "topic": "the household accounts and the hidden 400 sim",
        "with_agent_ids": ["n02", "n03"],
    })).result["conversation_id"]
    assert convened != implicit, "premise: the room now holds two conversations"

    landed = (await registry.dispatch(world, "n02", "say", {
        "content": "400 sim. That's the number.",
    })).result["conversation_id"]
    assert landed == convened, (
        "an invitee spoke and it landed in the room's older implicit "
        "thread - Perrin's table, set and abandoned"
    )


@pytest.mark.asyncio
async def test_naming_a_different_conversation_still_wins(world, registry) -> None:
    """Spec objective 3. The pointer is a default; a resident that names a
    thread means it, and two conversations in one room stay two."""
    await household(world, "n01", "n02", "n03")
    first = await registry.dispatch(world, "n01", "start_conversation", {
        "topic": "grain", "with_agent_ids": ["n02"],
    })
    second = await registry.dispatch(world, "n03", "start_conversation", {
        "topic": "timber", "with_agent_ids": ["n02"],
    })
    said = await registry.dispatch(world, "n02", "say", {
        "content": "About the timber.",
        "conversation_id": second.result["conversation_id"],
    })
    assert said.result["conversation_id"] == second.result["conversation_id"]
    assert said.result["conversation_id"] != first.result["conversation_id"]


@pytest.mark.asyncio
async def test_a_resident_with_no_pointer_behaves_exactly_as_before(world, registry) -> None:
    """Spec objective 4: joining stays automatic, convening is an addition
    and never a precondition. Somebody who convened nothing still opens or
    joins the room's conversation the way they always did."""
    await household(world, "n01", "n02")
    first = await registry.dispatch(world, "n01", "say", {"content": "Morning."})
    second = await registry.dispatch(world, "n02", "say", {"content": "Morning to you."})
    assert first.result["conversation_id"]
    assert second.result["conversation_id"] == first.result["conversation_id"]


# -- The resident can see which one they are in ----------------------


@pytest.mark.asyncio
async def test_the_perception_names_the_conversation_you_are_in(world, registry) -> None:
    """A pointer the resident cannot read is a pointer it cannot act on -
    the same finding that put conversations into the perception in the
    first place."""
    await household(world, "n01", "n02")
    convened = await registry.dispatch(world, "n01", "start_conversation", {
        "topic": "the boundary marker", "with_agent_ids": ["n02"],
    })
    seen = world.conversation_here("n02", world.tick())
    assert seen is not None
    assert seen["conversation_id"] == convened.result["conversation_id"]
    assert seen["topic"] == "the boundary marker"
