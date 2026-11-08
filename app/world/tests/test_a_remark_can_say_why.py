"""A2A: a remark can say why it was made - and nobody in the town hears why.

docs/A2A_CONFORMANCE.md §2, "Intent on a message": Atlas attaches motivation,
purpose and scope to every message; a Kasukabe remark carried only its words.
`say` now takes an optional `intent`. It goes onto the utterance row, the
event and so the trace - the research record - and never into what anybody
else perceives. A listener hears what was said, not why.

That second half is the whole design. An intent that reached the room would
be a new channel: a resident could tell its neighbours what to think while
appearing only to explain itself, and every "does the town cooperate"
measurement would start measuring that channel instead. So the guard is
written against the text an engine actually receives.
"""

from __future__ import annotations

import pytest

from world.core.perception import AmbientInfo, PerceivedEvent, Perception
from world.retrieval_depth import RetrievalDepthConfig, format_heard_event, render_perception_text
from world.tools.tests.conftest import (  # noqa: F401 - fixtures
    registry,
    spawn_agent,
    world,
    world_and_registry,
)

MARKET = "market_square"
WHY = "to get the price down before the others arrive"


async def _speak(world, registry, **extra):
    await spawn_agent(world, "farid", MARKET)
    await spawn_agent(world, "mireille", MARKET)
    world.agent_names.update({"farid": "Farid Ravensworth", "mireille": "Mireille Jarrah"})
    return await registry.execute(world, "mireille", "say", {
        "content": "Farid, what for four wheat?", "listener_agent_id": "farid", **extra,
    })


def _the_event(outcome):
    return next(e for e in outcome.events if e.type == "agent.utterance.spoken")


@pytest.mark.asyncio
async def test_the_intent_is_on_the_record(world, registry):
    outcome = await _speak(world, registry, intent=WHY)

    assert _the_event(outcome).payload["intent"] == WHY
    assert world.utterances[-1]["intent"] == WHY


@pytest.mark.asyncio
async def test_the_listener_hears_the_words_and_not_the_reason(world, registry):
    outcome = await _speak(world, registry, intent=WHY)
    event = _the_event(outcome)
    names = dict(world.agent_names)

    heard = format_heard_event(
        PerceivedEvent(type=event.type, tick=event.tick, payload=event.payload,
                       involves_self=True, for_agent_id="farid"),
        names, here=MARKET,
    )
    perception = Perception(
        agent_id="farid", tick=world.tick(), location_id=MARKET,
        ambient=AmbientInfo(game_day=0, hour=9, minute=0, is_daytime=True),
        visible_agents=["mireille"],
        heard_events=[PerceivedEvent(type=event.type, tick=event.tick, payload=event.payload,
                                     involves_self=True, for_agent_id="farid")],
    )
    whole = render_perception_text(
        perception, depth=RetrievalDepthConfig(fraction=0.0),
        open_questions=world.open_questions("farid", world.tick()),
        awaiting_reply=world.awaiting_reply("farid", world.tick()),
        conversation=world.conversation_here("farid", world.tick()),
    )

    assert "four wheat" in heard and "four wheat" in whole, "the words must still arrive"
    for text in (heard, whole):
        assert WHY not in text and "price down" not in text, "the reason reached the room"


@pytest.mark.asyncio
async def test_a_remark_without_one_is_exactly_as_before(world, registry):
    outcome = await _speak(world, registry)

    assert "intent" not in _the_event(outcome).payload
    assert world.utterances[-1]["intent"] is None


@pytest.mark.asyncio
async def test_a_blank_intent_is_no_intent(world, registry):
    outcome = await _speak(world, registry, intent="   ")

    assert "intent" not in _the_event(outcome).payload


@pytest.mark.asyncio
async def test_an_intent_is_short(world, registry):
    with pytest.raises(Exception):
        await _speak(world, registry, intent="x" * 201)


def test_the_field_says_nobody_hears_it_and_gives_no_advice() -> None:
    from world.tools.primitives.social import SayParams

    text = SayParams.model_fields["intent"].description.lower()
    assert "nobody" in text and "hears" in text
    for word in ("should", "must", "try to", "consider", "persuade"):
        assert word not in text, f"the field coaches: {word!r}"
