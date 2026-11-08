"""Tests for world.core.events - WorldEvent validation and the EventBus."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from world.core.events import EventBus, WorldEvent, validate_event_type


@pytest.mark.parametrize(
    "event_type",
    [
        "world.tick.advanced",
        "world.land.expanded",
        "agent.moved",
        "agent.spawned",
        "agent.died",
        "economy.wallet.debited",
        "economy.wallet.credited",
        "economy.ledger.violated",
        "business.registered",
        "business.contract.won",
        "crime.theft.attempted",
        "crime.warrant.issued",
        "politics.vote.cast",
        "politics.charter.amended",
        "politics.regime.changed",
        "religion.prayer.offered",
        "religion.doctrine.emerged",
        "health.ailment.onset",
        "health.agent.died",
        "broker.call.completed",
        "broker.call.rejected",
        "broker.rpm.throttled",
        "research.probe.completed",
        "research.metric.computed",
        "system.checkpoint.written",
        "system.sim.halted",
    ],
)
def test_every_convention_example_type_is_valid(event_type: str) -> None:
    assert validate_event_type(event_type) is None
    WorldEvent(type=event_type, tick=0)


@pytest.mark.parametrize(
    "event_type",
    [
        "world",
        "world.tick.advanced.extra",
        "World.tick.advanced",
        "world.Tick.advanced",
        "world.tick.advanced-thing",
        "unknown_domain.thing.happened",
        "world..advanced",
        "1world.tick.advanced",
    ],
)
def test_invalid_event_types_rejected(event_type: str) -> None:
    assert validate_event_type(event_type) is not None
    with pytest.raises(ValidationError):
        WorldEvent(type=event_type, tick=0)


def test_domain_property_derives_from_type() -> None:
    event = WorldEvent(type="economy.wallet.debited", tick=5, payload={"amount": 100})
    assert event.domain == "economy"


def test_default_payload_is_empty_dict() -> None:
    event = WorldEvent(type="agent.moved", tick=1)
    assert event.payload == {}


@pytest.mark.asyncio
async def test_publish_dispatches_to_domain_subscriber() -> None:
    bus = EventBus()
    received: list[WorldEvent] = []

    async def handler(event: WorldEvent) -> None:
        received.append(event)

    bus.subscribe("agent", handler)
    event = await bus.emit("agent.moved", tick=1, payload={"agent_id": "a1", "location_id": "cafe"})

    assert received == [event]


@pytest.mark.asyncio
async def test_publish_does_not_dispatch_to_other_domain_subscriber() -> None:
    bus = EventBus()
    received: list[WorldEvent] = []

    async def handler(event: WorldEvent) -> None:
        received.append(event)

    bus.subscribe("economy", handler)
    await bus.emit("agent.moved", tick=1, payload={})

    assert received == []


@pytest.mark.asyncio
async def test_wildcard_subscriber_sees_every_domain() -> None:
    bus = EventBus()
    received: list[str] = []

    async def handler(event: WorldEvent) -> None:
        received.append(event.type)

    bus.subscribe("*", handler)
    await bus.emit("agent.moved", tick=1)
    await bus.emit("economy.wallet.debited", tick=1)

    assert received == ["agent.moved", "economy.wallet.debited"]


@pytest.mark.asyncio
async def test_one_failing_subscriber_does_not_block_others() -> None:
    bus = EventBus()
    received: list[str] = []

    async def broken(event: WorldEvent) -> None:
        raise RuntimeError("boom")

    async def healthy(event: WorldEvent) -> None:
        received.append(event.type)

    bus.subscribe("agent", broken)
    bus.subscribe("agent", healthy)

    await bus.emit("agent.moved", tick=1)

    assert received == ["agent.moved"]


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    received: list[str] = []

    async def handler(event: WorldEvent) -> None:
        received.append(event.type)

    bus.subscribe("agent", handler)
    bus.unsubscribe("agent", handler)
    await bus.emit("agent.moved", tick=1)

    assert received == []


def test_unsubscribe_unknown_callback_is_noop() -> None:
    bus = EventBus()

    async def handler(event: WorldEvent) -> None:
        pass

    bus.unsubscribe("agent", handler)


def test_subscribe_rejects_unknown_domain() -> None:
    bus = EventBus()

    async def handler(event: WorldEvent) -> None:
        pass

    with pytest.raises(ValueError):
        bus.subscribe("not_a_real_domain", handler)


@pytest.mark.asyncio
async def test_recent_filters_by_since_tick_and_domain() -> None:
    bus = EventBus()
    await bus.emit("agent.moved", tick=1)
    await bus.emit("economy.wallet.debited", tick=2)
    await bus.emit("agent.moved", tick=3)

    assert [e.tick for e in bus.recent(since_tick=2)] == [2, 3]
    assert [e.type for e in bus.recent(domain="agent")] == ["agent.moved", "agent.moved"]
    assert [e.tick for e in bus.recent(since_tick=2, domain="agent")] == [3]


@pytest.mark.asyncio
async def test_buffer_is_bounded() -> None:
    bus = EventBus(buffer_size=3)
    for i in range(5):
        await bus.emit("agent.moved", tick=i)

    assert len(bus.buffer) == 3
    assert [e.tick for e in bus.recent()] == [2, 3, 4]


@pytest.mark.asyncio
async def test_replay_delivers_buffered_events_in_order() -> None:
    bus = EventBus()
    await bus.emit("agent.moved", tick=1)
    await bus.emit("agent.moved", tick=2)

    received: list[int] = []

    async def handler(event: WorldEvent) -> None:
        received.append(event.tick)

    await bus.replay(handler)
    assert received == [1, 2]


@pytest.mark.asyncio
async def test_replay_isolates_callback_errors() -> None:
    bus = EventBus()
    await bus.emit("agent.moved", tick=1)
    await bus.emit("agent.moved", tick=2)

    async def broken(event: WorldEvent) -> None:
        if event.tick == 1:
            raise RuntimeError("boom")

    await bus.replay(broken)
