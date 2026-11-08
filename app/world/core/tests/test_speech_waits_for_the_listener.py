"""Something said TO you waits until you next think.

The hearing window is measured in ticks; an agent's turns are not. At the
default cadence a resident acts on roughly one tick in three - but
measured on a live ten-resident run, the gap between one agent's turns
ran to a median of 33 ticks and a maximum of 207. Every single gap was
longer than the four-tick window, so a remark expired before the person
it was aimed at ever thought again. Only 7 of 615 perceptions in that run
carried a remark at all, and not one conversation was ever answered.

From inside: Ainsley told Perrin "Perrin. Walk home. Tell Bram." while
the two of them stood alone together in the pharmacy. Perrin's next
perception read "With you, and nobody else: Ainsley Holloway" and
directly under it "Nothing notable happened recently."
"""

from __future__ import annotations

import pytest

from world.core.events import EventBus, WorldEvent
from world.core.perception import UTTERANCE_EVENT, LocationIndex, PerceptionSystem


def _said(tick: int, speaker: str, listener: str | None, where: str) -> WorldEvent:
    return WorldEvent(
        type=UTTERANCE_EVENT,
        tick=tick,
        payload={
            "speaker_agent_id": speaker,
            "listener_agent_id": listener,
            "target_agent_id": listener,
            "involved_agent_ids": [speaker] + ([listener] if listener else []),
            "location_id": where,
            "content": "Perrin. Walk home. Tell Bram.",
            "is_public": listener is None,
        },
    )


def _system(window: int = 4) -> tuple[PerceptionSystem, EventBus, LocationIndex]:
    bus = EventBus()
    locations = LocationIndex()
    locations.place("ainsley", "pharmacy")
    locations.place("perrin", "pharmacy")
    return PerceptionSystem(locations, bus, hearing_window_ticks=window), bus, locations


@pytest.mark.asyncio
async def test_a_remark_survives_a_gap_far_longer_than_the_window() -> None:
    """The measured case: spoken at tick 233, heard at tick 266 - a
    33-tick gap against a 4-tick window."""
    system, bus, _ = _system(window=4)
    # Perrin has thought before, so the system knows where to resume from.
    system.build_perception("perrin", tick=232)
    await bus.publish(_said(233, "ainsley", "perrin", "pharmacy"))

    perception = system.build_perception("perrin", tick=266)

    assert [e.type for e in perception.heard_events] == [UTTERANCE_EVENT]
    assert perception.heard_events[0].payload["speaker_agent_id"] == "ainsley"


@pytest.mark.asyncio
async def test_a_question_keeps_being_asked_until_it_is_answered() -> None:
    """One delivery is exactly one chance to notice. A question does not
    stop being open because the person asked happened to be busy on the
    turn it arrived - measured, agents took that single chance about a
    third of the time, and Nell asked Tobias eight times in one thread
    without ever being answered."""
    system, bus, _ = _system()
    system.build_perception("perrin", tick=1)
    await bus.publish(_said(2, "ainsley", "perrin", "pharmacy"))

    assert len(system.build_perception("perrin", tick=10).heard_events) == 1
    assert len(system.build_perception("perrin", tick=20).heard_events) == 1


@pytest.mark.asyncio
async def test_answering_closes_it() -> None:
    """Saying something back to them is the answer. It must stop being
    put in front of the agent the moment it is no longer true that it is
    unanswered."""
    system, bus, _ = _system()
    system.build_perception("perrin", tick=1)
    await bus.publish(_said(2, "ainsley", "perrin", "pharmacy"))
    assert len(system.build_perception("perrin", tick=10).heard_events) == 1

    await bus.publish(_said(11, "perrin", "ainsley", "pharmacy"))

    assert system.build_perception("perrin", tick=20).heard_events == []


@pytest.mark.asyncio
async def test_re_asking_lapses_but_the_first_delivery_never_does() -> None:
    """Two different guarantees, and only one of them has a clock on it.

    Being told at all has NO age limit - the measured gap between an
    agent's turns reached 207 ticks, so any cutoff would silently
    reinstate the original bug. Being told AGAIN does lapse: a question
    from half a day ago is not a live obligation, and a perception that
    accumulated every one of them forever would crowd out the present.
    """
    from world.core.perception import UNANSWERED_LOOKBACK_TICKS

    system, bus, _ = _system()
    system.build_perception("perrin", tick=1)
    await bus.publish(_said(2, "ainsley", "perrin", "pharmacy"))

    very_late = 2 + UNANSWERED_LOOKBACK_TICKS * 3
    # First sight, long after the window and the lookback both closed.
    assert len(system.build_perception("perrin", tick=very_late).heard_events) == 1
    # Seen and ignored; now it lapses rather than repeating forever.
    assert system.build_perception("perrin", tick=very_late + 5).heard_events == []


@pytest.mark.asyncio
async def test_answering_somebody_else_does_not_close_it() -> None:
    """Replying to Marta is not replying to Ainsley."""
    system, bus, locations = _system()
    locations.place("marta", "pharmacy")
    system.build_perception("perrin", tick=1)
    await bus.publish(_said(2, "ainsley", "perrin", "pharmacy"))
    await bus.publish(_said(5, "perrin", "marta", "pharmacy"))

    heard = system.build_perception("perrin", tick=12).heard_events

    assert [e.payload["speaker_agent_id"] for e in heard] == ["ainsley"]


@pytest.mark.asyncio
async def test_a_speaker_is_not_handed_back_its_own_words() -> None:
    """involved_agent_ids carries the speaker too."""
    system, bus, _ = _system()
    system.build_perception("ainsley", tick=1)
    await bus.publish(_said(2, "ainsley", "perrin", "pharmacy"))

    assert system.build_perception("ainsley", tick=40).heard_events == []


@pytest.mark.asyncio
async def test_overheard_chatter_still_expires_on_the_window() -> None:
    """You do not walk into a room and hear yesterday's gossip. Only being
    addressed by name waits."""
    system, bus, locations = _system(window=4)
    locations.place("bystander", "pharmacy")
    system.build_perception("bystander", tick=1)
    await bus.publish(_said(2, "ainsley", "perrin", "pharmacy"))

    late = system.build_perception("bystander", tick=40)

    assert late.heard_events == []


@pytest.mark.asyncio
async def test_a_bystander_present_at_the_time_still_hears_it() -> None:
    """Within the window, the room hears it as before - this must not
    narrow ordinary earshot."""
    system, bus, locations = _system(window=4)
    locations.place("bystander", "pharmacy")
    await bus.publish(_said(2, "ainsley", "perrin", "pharmacy"))

    assert len(system.build_perception("bystander", tick=3).heard_events) == 1


@pytest.mark.asyncio
async def test_a_first_ever_perception_does_not_replay_all_history() -> None:
    """An agent that has never thought must not be handed the whole
    backlog of the town on its first turn."""
    system, bus, _ = _system(window=4)
    await bus.publish(_said(2, "ainsley", "perrin", "pharmacy"))

    assert system.build_perception("perrin", tick=500).heard_events == []


@pytest.mark.asyncio
async def test_several_unanswered_remarks_all_arrive() -> None:
    """Being addressed three times while away is three things to answer."""
    system, bus, _ = _system()
    system.build_perception("perrin", tick=1)
    for tick in (5, 20, 60):
        await bus.publish(_said(tick, "ainsley", "perrin", "pharmacy"))

    perception = system.build_perception("perrin", tick=100)

    assert len(perception.heard_events) == 3


@pytest.mark.asyncio
async def test_a_remark_reaches_you_even_after_you_walk_away() -> None:
    """It was said to you. Where you are standing when you finally think
    about it does not unsay it."""
    system, bus, locations = _system()
    system.build_perception("perrin", tick=1)
    await bus.publish(_said(2, "ainsley", "perrin", "pharmacy"))
    locations.place("perrin", "college")

    perception = system.build_perception("perrin", tick=50)

    assert len(perception.heard_events) == 1
    assert perception.location_id == "college"



@pytest.mark.asyncio
async def test_a_remark_to_the_room_does_not_follow_you_around() -> None:
    """Only a question addressed to you by name carries the obligation.
    Speech to everyone present expires on the ordinary window."""
    system, bus, _ = _system(window=4)
    system.build_perception("perrin", tick=1)
    await bus.publish(_said(2, "ainsley", None, "pharmacy"))

    assert system.build_perception("perrin", tick=20).heard_events == []
