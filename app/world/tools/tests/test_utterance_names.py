"""An utterance must remember who said it, by name.

ROADMAP.md records the defect this is a second instance of: agents shown bare
ids produced nine utterances in 933 ticks, because "a model cannot greet
a0031". `format_visible_agents` was fixed then. The utterance RECORD was not,
and nobody noticed because every surface that displays it resolves names from
somewhere else - the viewer from its own roster, `/world/utterances` from the
running genesis.

The one consumer that trusted the stored value is the open-questions section
added for the reply-rate work, and it rendered:

    Still waiting on an answer from you:
      n01 (n01) (32 ticks ago): "..."

Cause: `bootstrap._display_name` read `getattr(agent, "name", None)` off an
`AgentLifeRecord`, which has no `name` field and never has - its own docstring
says so ("no wallet balances, no location, no memory"). The getattr answered
its default every single time, for every utterance in every run this project
has ever made, and a bare id reads exactly like a name that happens to be
short. Build Spec v2 clause 4.4 is about precisely this shape.
"""

from __future__ import annotations

from world.life.models import AgentLifeRecord


def test_agent_life_record_has_no_name_field() -> None:
    """The premise. If a `name` field is ever added, this test fails and
    somebody has to decide which of the two sources is authoritative,
    rather than having two that quietly disagree."""
    assert "name" not in AgentLifeRecord.model_fields
    assert not hasattr(AgentLifeRecord, "name")


def test_display_name_resolves_from_the_canonical_map() -> None:
    from world.tools.bootstrap import display_name

    world = type("W", (), {})()
    world.agent_names = {"n01": "Bram Holloway"}
    world.agents = {"n01": AgentLifeRecord(agent_id="n01", age_years=44.0, sex="male")}
    assert display_name(world, "n01") == "Bram Holloway"


def test_display_name_falls_back_to_the_id_only_when_truly_unknown() -> None:
    """An id is the honest answer for somebody the town has no name for -
    a newborn before genesis names it, say. It must not be the answer for
    somebody the town DOES know."""
    from world.tools.bootstrap import display_name

    world = type("W", (), {})()
    world.agent_names = {}
    world.agents = {}
    assert display_name(world, "n99") == "n99"
    assert display_name(world, None) is None


def test_a_recorded_utterance_carries_the_speakers_real_name() -> None:
    """End to end through the subscriber that actually writes the row."""
    import asyncio

    from world.core.events import WorldEvent
    from world.tools.bootstrap import attach_utterance_log

    world = type("W", (), {})()
    world.agent_names = {"n01": "Bram Holloway", "n02": "Ainsley Holloway"}
    world.agents = {}
    world.utterances = []
    bus = type("B", (), {"handlers": []})()
    bus.subscribe = lambda domain, fn: bus.handlers.append(fn)
    world.event_bus = bus

    attach_utterance_log(world)
    event = WorldEvent(
        type="agent.utterance.spoken",
        tick=5,
        payload={
            "speaker_agent_id": "n01",
            "listener_agent_id": "n02",
            "location_id": "cafe",
            "content": "Who buys copper?",
        },
    )
    asyncio.run(bus.handlers[0](event))
    row = world.utterances[-1]
    assert row["speaker_name"] == "Bram Holloway"
    assert row["listener_name"] == "Ainsley Holloway"
