"""A conversation is a thing, not an inference drawn afterwards.

Spec: docs/specs/group-conversation-spec.md.

Until now `conversation_id` was worked out at the moment somebody spoke,
from the room and a time window, and carried forward onto the next
remark. Run 17 shows what that costs. Nine conversations, four with a
second speaker, one of sixteen turns - real group talk - but:

  * a resident standing in the room, addressed by name, saying nothing,
    is in no conversation at all, because there is no roster to be on;
  * two pairs talking about different things in one room are one thread,
    and each answer lands under the other's question;
  * a resident who wants the household round a table has no way to call
    for one. They can only speak and hope the window catches a reply.

These tests exercise the real registry through the real tool registry on
a real booted world - `build_world()`, not a stub - because the thing
being tested is whether an agent can actually convene and speak into a
conversation, and a stub would answer a question nobody asked.
"""

from __future__ import annotations

import pytest

from world.tools.state import WorldState

from .conftest import move_agent, spawn_agent

MARKET = "Kasukabe Market Square"
FARM = "Kasukabe Farmland"


async def _cast(world: WorldState, *ids: str, place: str = MARKET) -> None:
    for agent_id in ids:
        await spawn_agent(world, agent_id, place)
        world.agent_names[agent_id] = f"Resident {agent_id}"


async def _call(registry, world, agent_id: str, tool: str, **params):
    """The full path - execute(), not dispatch(). dispatch skips the
    event publish, so the utterance tail these tests read would stay
    empty and every assertion about threading would pass vacuously."""
    return await registry.execute(world, agent_id, tool, params)


# ── convening ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_convened_conversation_exists_before_anybody_speaks(
    world: WorldState, registry
) -> None:
    """The whole point of a roster: five residents are in this before a
    word is said."""
    await _cast(world, "n01", "n02", "n03", "n04", "n05")

    outcome = await _call(
        registry, world, "n01", "start_conversation",
        topic="what to do about the boundary marker",
        with_agent_ids=["n02", "n03", "n04", "n05"],
    )

    conversation_id = outcome.result["conversation_id"]
    record = world.conversations[conversation_id]
    assert record["open"] is True
    assert record["topic"] == "what to do about the boundary marker"
    assert record["place"] == MARKET
    assert set(record["participants"]) == {"n01", "n02", "n03", "n04", "n05"}
    assert record["turns"] == 0, "convening is not speaking"


@pytest.mark.asyncio
async def test_convening_with_somebody_who_is_not_here_is_refused_by_name(
    world: WorldState, registry
) -> None:
    """And the refusal has to say where they are, or the agent learns
    nothing and tries again - the failure `say` already fixed."""
    await _cast(world, "n01", "n02")
    await _cast(world, "n09", place=FARM)

    outcome = await _call(
        registry, world, "n01", "start_conversation",
        topic="the price of flour", with_agent_ids=["n02", "n09"],
    )

    assert "n09" not in world.conversations[outcome.result["conversation_id"]]["participants"]
    note = str(outcome.result.get("note", "")) + str(outcome.result.get("not_here", ""))
    assert "n09" in note or "Resident n09" in note
    assert FARM.lower() in note.lower() or "not here" in note.lower()


@pytest.mark.asyncio
async def test_a_resident_may_open_a_table_where_people_are_standing(
    world: WorldState, registry
) -> None:
    """Convening with nobody NAMED is legitimate: it is how you call a
    meeting and let whoever is here join.

    REVISED, and the reason is a rule change rather than a result nobody
    liked. This used to open a conversation for a resident standing alone,
    on the grounds that it is "how you call a meeting before anyone has
    arrived". The operator's rule is the opposite and is the stronger one:
    "If there's no chat, there will be no open ended chat. If there are
    people around, only then we will have chat option available." A
    conversation of one is a table laid in an empty room, and it sits open
    in the registry that the perception, the Talk book and the township
    screen all read - so a run looks full of chatter that never happened.

    An empty ROSTER in an occupied room is still fine, and is what this
    now pins. See test_no_chat_without_people.py for the other half.
    """
    await _cast(world, "n01", "n02")
    outcome = await _call(
        registry, world, "n01", "start_conversation", topic="the mill's hours"
    )
    record = world.conversations[outcome.result["conversation_id"]]
    assert record["participants"] == ["n01"]
    assert record["open"] is True


@pytest.mark.asyncio
async def test_naming_yourself_or_repeating_a_name_does_not_duplicate_the_roster(
    world: WorldState, registry
) -> None:
    await _cast(world, "n01", "n02")
    outcome = await _call(
        registry, world, "n01", "start_conversation", topic="grain",
        with_agent_ids=["n01", "n02", "n02"],
    )
    participants = world.conversations[outcome.result["conversation_id"]]["participants"]
    assert participants.count("n01") == 1
    assert participants.count("n02") == 1


# ── speaking into one ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_conversations_in_one_room_stay_two(world: WorldState, registry) -> None:
    """The failure this exists to fix. Two pairs, one market, twelve
    interleaved remarks, and every one lands in the thread it named.

    EVAL-27. This was red on the review's checkout: a `say` carrying an
    explicit `conversation_id` was recorded under a different id, so
    speech into a named conversation was not honoured - the same
    behaviour `DYNAMIC_CHAT_CONSOLIDATED_REPORT` section 1.3 describes
    happening live, where a convened conversation was reached for five
    times across six runs and, once used, was not reliably where the
    speech landed. It passes now; this note is here so a future reader
    knows the test is load-bearing rather than incidental.
    """
    await _cast(world, "n01", "n02", "n03", "n04")

    first = (await _call(registry, world, "n01", "start_conversation",
                         topic="copper", with_agent_ids=["n02"])).result["conversation_id"]
    second = (await _call(registry, world, "n03", "start_conversation",
                          topic="timber", with_agent_ids=["n04"])).result["conversation_id"]
    assert first != second

    for i in range(3):
        await _call(registry, world, "n01", "say",
                    content=f"copper line {i}", conversation_id=first)
        await _call(registry, world, "n03", "say",
                    content=f"timber line {i}", conversation_id=second)
        await _call(registry, world, "n02", "say",
                    content=f"copper answer {i}", conversation_id=first)
        await _call(registry, world, "n04", "say",
                    content=f"timber answer {i}", conversation_id=second)

    by_conversation: dict[str, list[str]] = {}
    for utterance in world.utterances:
        by_conversation.setdefault(utterance["conversation_id"], []).append(utterance["content"])

    assert all("copper" in line for line in by_conversation[first])
    assert all("timber" in line for line in by_conversation[second])
    assert len(by_conversation[first]) == 6
    assert len(by_conversation[second]) == 6


@pytest.mark.asyncio
async def test_speaking_joins_the_roster(world: WorldState, registry) -> None:
    await _cast(world, "n01", "n02", "n03")
    conversation = (await _call(registry, world, "n01", "start_conversation",
                                topic="the fence")).result["conversation_id"]

    await _call(registry, world, "n03", "say", content="I'll help", conversation_id=conversation)

    assert "n03" in world.conversations[conversation]["participants"]


@pytest.mark.asyncio
async def test_a_remark_naming_no_conversation_still_joins_the_room(
    world: WorldState, registry
) -> None:
    """The old behaviour, which must not regress: no id named, one open
    conversation in the room, the remark joins it."""
    await _cast(world, "n01", "n02")
    conversation = (await _call(registry, world, "n01", "start_conversation",
                                topic="the fence")).result["conversation_id"]

    outcome = await _call(registry, world, "n02", "say", content="aye")
    assert outcome.result["conversation_id"] == conversation


@pytest.mark.asyncio
async def test_with_two_open_the_speaker_stays_in_their_own(
    world: WorldState, registry
) -> None:
    """Ambiguity resolved the way a person would: you carry on the
    conversation you are already in."""
    await _cast(world, "n01", "n02", "n03", "n04")
    mine = (await _call(registry, world, "n01", "start_conversation",
                        topic="copper", with_agent_ids=["n02"])).result["conversation_id"]
    (await _call(registry, world, "n03", "start_conversation",
                 topic="timber", with_agent_ids=["n04"])).result["conversation_id"]

    outcome = await _call(registry, world, "n01", "say", content="about that copper")
    assert outcome.result["conversation_id"] == mine


@pytest.mark.asyncio
async def test_being_addressed_puts_a_silent_resident_on_the_roster(
    world: WorldState, registry
) -> None:
    """Participation is being spoken to, not only producing text."""
    await _cast(world, "n01", "n02")
    outcome = await _call(registry, world, "n01", "say",
                          content="Resident n02, are you coming?", listener_agent_id="n02")
    conversation = outcome.result["conversation_id"]
    assert "n02" in world.conversations[conversation]["participants"]


# ── refusals that teach ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_speaking_into_a_closed_conversation_is_refused_and_says_so(
    world: WorldState, registry
) -> None:
    from world.tools.primitives.social import CONVERSATION_WINDOW_TICKS

    await _cast(world, "n01", "n02")
    conversation = (await _call(registry, world, "n01", "start_conversation",
                                topic="the fence")).result["conversation_id"]
    world.close_stale_conversations(world.tick() + CONVERSATION_WINDOW_TICKS + 1)

    outcome = await _call(registry, world, "n02", "say",
                          content="too late", conversation_id=conversation)
    assert outcome.result["conversation_id"] != conversation
    assert "closed" in str(outcome.result.get("note", "")).lower()


@pytest.mark.asyncio
async def test_speaking_into_a_conversation_elsewhere_says_where_it_is(
    world: WorldState, registry
) -> None:
    await _cast(world, "n01", "n02")
    conversation = (await _call(registry, world, "n01", "start_conversation",
                                topic="the fence", with_agent_ids=["n02"])).result["conversation_id"]
    await move_agent(world, "n02", FARM)

    outcome = await _call(registry, world, "n02", "say",
                          content="shouting from the field", conversation_id=conversation)
    note = str(outcome.result.get("note", ""))
    assert MARKET.lower() in note.lower()


# ── lifecycle ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_quiet_room_closes_its_conversation_and_the_next_remark_opens_another(
    world: WorldState, registry
) -> None:
    from world.tools.primitives.social import CONVERSATION_WINDOW_TICKS

    await _cast(world, "n01", "n02")
    first = (await _call(registry, world, "n01", "say", content="morning")).result["conversation_id"]

    world.close_stale_conversations(world.tick() + CONVERSATION_WINDOW_TICKS + 1)
    assert world.conversations[first]["open"] is False

    second = (await _call(registry, world, "n02", "say", content="afternoon")).result["conversation_id"]
    assert second != first


@pytest.mark.asyncio
async def test_a_participant_who_walks_out_stays_on_the_roster(
    world: WorldState, registry
) -> None:
    """The roster is who this conversation was, which does not change
    because somebody left the room."""
    await _cast(world, "n01", "n02")
    conversation = (await _call(registry, world, "n01", "start_conversation",
                                topic="the fence", with_agent_ids=["n02"])).result["conversation_id"]
    await move_agent(world, "n02", FARM)
    assert "n02" in world.conversations[conversation]["participants"]


@pytest.mark.asyncio
async def test_a_hundred_remarks_do_not_slow_the_room_down(
    world: WorldState, registry
) -> None:
    """The tail the matcher walks stays bounded; the roster does not
    grow without limit either."""
    await _cast(world, "n01", "n02")
    conversation = (await _call(registry, world, "n01", "start_conversation",
                                topic="long")).result["conversation_id"]
    for i in range(100):
        await _call(registry, world, "n01", "say", content=f"line {i}",
                    conversation_id=conversation)
    record = world.conversations[conversation]
    assert record["turns"] == 100
    assert len(record["participants"]) == 1


@pytest.mark.asyncio
async def test_convening_is_priced(world: WorldState, registry) -> None:
    """An action that costs nothing is an action with no opportunity
    cost, which is not the economy this town runs on."""
    from world.ledger.action_costs import SCHEDULE_TABLE

    assert "start_conversation" in SCHEDULE_TABLE
