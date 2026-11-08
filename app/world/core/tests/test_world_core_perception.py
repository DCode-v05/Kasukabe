"""Tests for world.core.perception - LocationIndex and PerceptionSystem."""

from __future__ import annotations

import logging

import pytest

from world.core.events import EventBus
from world.core.perception import LocationIndex, PerceptionSystem, default_interrupt_policy
from world.core.scheduler import AgentScheduler, SchedulerConfig


def test_location_index_place_and_agents_at() -> None:
    index = LocationIndex()
    index.place("a1", "cafe")
    index.place("a2", "cafe")

    assert index.agents_at("cafe") == frozenset({"a1", "a2"})
    assert index.location_of("a1") == "cafe"


def test_location_index_move_updates_both_sides() -> None:
    index = LocationIndex()
    index.place("a1", "cafe")
    index.place("a1", "park")

    assert index.location_of("a1") == "park"
    assert index.agents_at("cafe") == frozenset()
    assert index.agents_at("park") == frozenset({"a1"})


def test_location_index_remove() -> None:
    index = LocationIndex()
    index.place("a1", "cafe")
    index.remove("a1")

    assert index.location_of("a1") is None
    assert index.agents_at("cafe") == frozenset()


def test_location_index_remove_unknown_agent_is_noop() -> None:
    index = LocationIndex()
    index.remove("ghost")


def test_location_index_snapshot_restore_round_trip() -> None:
    index = LocationIndex()
    index.place("a1", "cafe")
    index.place("a2", "park")

    restored = LocationIndex()
    restored.restore(index.snapshot())

    assert restored.location_of("a1") == "cafe"
    assert restored.agents_at("park") == frozenset({"a2"})


@pytest.mark.asyncio
async def test_location_index_attach_tracks_agent_moved_event() -> None:
    bus = EventBus()
    index = LocationIndex()
    index.attach(bus)

    await bus.emit("agent.moved", tick=1, payload={"agent_id": "a1", "location_id": "cafe"})

    assert index.location_of("a1") == "cafe"


@pytest.mark.asyncio
async def test_location_index_attach_tracks_agent_spawned_event() -> None:
    bus = EventBus()
    index = LocationIndex()
    index.attach(bus)

    await bus.emit("agent.spawned", tick=1, payload={"agent_id": "baby", "location_id": "temple"})

    assert index.location_of("baby") == "temple"


@pytest.mark.asyncio
async def test_location_index_attach_removes_on_agent_died_event() -> None:
    bus = EventBus()
    index = LocationIndex()
    index.attach(bus)

    await bus.emit("agent.spawned", tick=1, payload={"agent_id": "a1", "location_id": "cafe"})
    await bus.emit("agent.died", tick=2, payload={"agent_id": "a1"})

    assert index.location_of("a1") is None


@pytest.mark.asyncio
async def test_location_index_ignores_malformed_event(caplog: pytest.LogCaptureFixture) -> None:
    bus = EventBus()
    index = LocationIndex()
    index.attach(bus)

    with caplog.at_level(logging.WARNING):
        await bus.emit("agent.moved", tick=1, payload={"agent_id": "a1"})

    assert index.location_of("a1") is None
    assert "location_id" in caplog.text


@pytest.mark.asyncio
async def test_location_index_untouched_by_other_domains() -> None:
    bus = EventBus()
    index = LocationIndex()
    index.attach(bus)

    await bus.emit("economy.wallet.debited", tick=1, payload={"agent_id": "a1"})

    assert index.location_of("a1") is None


def test_default_interrupt_policy_extracts_target_agent() -> None:
    from world.core.events import WorldEvent

    event = WorldEvent(type="crime.theft.attempted", tick=1, payload={"target_agent_id": "victim"})
    assert default_interrupt_policy(event) == {"victim"}


def test_default_interrupt_policy_extracts_list_keys() -> None:
    from world.core.events import WorldEvent

    event = WorldEvent(
        type="business.contract.won",
        tick=1,
        payload={"involved_agent_ids": ["a1", "a2"], "witness_agent_ids": ["a3"]},
    )
    assert default_interrupt_policy(event) == {"a1", "a2", "a3"}


def test_default_interrupt_policy_ignores_unrelated_payload() -> None:
    from world.core.events import WorldEvent

    event = WorldEvent(type="world.tick.advanced", tick=1, payload={"game_day": 2})
    assert default_interrupt_policy(event) == set()


def test_perception_visible_agents_excludes_self() -> None:
    bus = EventBus()
    locations = LocationIndex()
    locations.place("a1", "cafe")
    locations.place("a2", "cafe")
    locations.place("a3", "park")
    system = PerceptionSystem(locations, bus)

    perception = system.build_perception("a1", tick=1)

    assert perception.location_id == "cafe"
    assert perception.visible_agents == ["a2"]


def test_perception_with_no_location_sees_nobody() -> None:
    bus = EventBus()
    locations = LocationIndex()
    system = PerceptionSystem(locations, bus)

    perception = system.build_perception("ghost", tick=1)

    assert perception.location_id is None
    assert perception.visible_agents == []
    assert perception.heard_events == []


@pytest.mark.asyncio
async def test_perception_heard_events_filtered_by_location() -> None:
    bus = EventBus()
    locations = LocationIndex()
    locations.place("a1", "cafe")
    locations.place("a2", "park")
    system = PerceptionSystem(locations, bus)

    await bus.emit("business.contract.won", tick=5, payload={"location_id": "cafe"})

    cafe_perception = system.build_perception("a1", tick=5)
    park_perception = system.build_perception("a2", tick=5)

    assert len(cafe_perception.heard_events) == 1
    assert cafe_perception.heard_events[0].involves_self is False
    assert park_perception.heard_events == []


@pytest.mark.asyncio
async def test_perception_includes_events_that_involve_self_regardless_of_location() -> None:
    bus = EventBus()
    locations = LocationIndex()
    locations.place("victim", "home")
    system = PerceptionSystem(locations, bus)

    await bus.emit(
        "crime.theft.attempted",
        tick=5,
        payload={"location_id": "market", "target_agent_id": "victim"},
    )

    perception = system.build_perception("victim", tick=5)

    assert len(perception.heard_events) == 1
    assert perception.heard_events[0].involves_self is True


@pytest.mark.asyncio
async def test_perception_respects_hearing_window() -> None:
    bus = EventBus()
    locations = LocationIndex()
    locations.place("a1", "cafe")
    system = PerceptionSystem(locations, bus, hearing_window_ticks=1)

    await bus.emit("business.contract.won", tick=3, payload={"location_id": "cafe"})

    assert len(system.build_perception("a1", tick=3).heard_events) == 1
    assert system.build_perception("a1", tick=4).heard_events == []


def test_perception_system_rejects_zero_hearing_window() -> None:
    bus = EventBus()
    locations = LocationIndex()
    with pytest.raises(ValueError):
        PerceptionSystem(locations, bus, hearing_window_ticks=0)


def test_perception_ambient_matches_clock_calendar() -> None:
    bus = EventBus()
    locations = LocationIndex()
    system = PerceptionSystem(locations, bus)

    perception = system.build_perception("a1", tick=144)

    assert perception.ambient.game_day == 1
    assert perception.ambient.hour == 0
    assert perception.ambient.is_daytime is False


@pytest.mark.asyncio
async def test_perception_attach_interrupts_scheduler_on_targeted_event() -> None:
    bus = EventBus()
    locations = LocationIndex()
    system = PerceptionSystem(locations, bus)
    scheduler = AgentScheduler(SchedulerConfig(target_fraction=1 / 10))
    scheduler.register_agent("victim", current_tick=0)
    system.attach(scheduler)

    assert scheduler.due_agents(0) == []

    await bus.emit("crime.theft.attempted", tick=0, payload={"target_agent_id": "victim"})

    assert scheduler.due_agents(0) == ["victim"]


@pytest.mark.asyncio
async def test_perception_attach_ignores_events_naming_nobody() -> None:
    bus = EventBus()
    locations = LocationIndex()
    system = PerceptionSystem(locations, bus)
    scheduler = AgentScheduler(SchedulerConfig(target_fraction=1 / 10))
    scheduler.register_agent("bystander", current_tick=0)
    system.attach(scheduler)

    await bus.emit("world.tick.advanced", tick=0, payload={"game_day": 0})

    assert scheduler.due_agents(0) == []
