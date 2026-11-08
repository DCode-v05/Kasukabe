"""Integration tests for world.core.engine.TickEngine - the full wiring
(clock + event bus + scheduler + perception + checkpoint + pacing)."""

from __future__ import annotations

import pytest

from world.core.checkpoint import InMemoryCheckpointSink
from world.core.clock import TICKS_PER_DAY
from world.core.engine import TickEngine
from world.core.events import WorldEvent
from world.core.pacing import PacingConfig, PacingMode
from world.core.scheduler import SchedulerConfig


@pytest.mark.asyncio
async def test_tick_advances_clock_and_publishes_world_tick_advanced() -> None:
    engine = TickEngine()
    received: list[WorldEvent] = []

    async def handler(event: WorldEvent) -> None:
        received.append(event)

    engine.event_bus.subscribe("world", handler)

    result = await engine.tick()

    assert engine.clock.tick_index == 1
    assert result.tick.tick_index == 1
    assert [e.type for e in received] == ["world.tick.advanced"]


@pytest.mark.asyncio
async def test_register_agent_places_on_map_and_enrolls_in_scheduler() -> None:
    engine = TickEngine()
    engine.register_agent("a1", location_id="cafe")

    assert engine.locations.location_of("a1") == "cafe"
    assert engine.scheduler.agent_count == 1


@pytest.mark.asyncio
async def test_register_agent_without_location_is_not_placed() -> None:
    engine = TickEngine()
    engine.register_agent("a1")

    assert engine.locations.location_of("a1") is None
    assert engine.scheduler.agent_count == 1


@pytest.mark.asyncio
async def test_due_agents_appear_in_tick_result_with_perceptions() -> None:
    engine = TickEngine(scheduler_config=SchedulerConfig(target_fraction=1.0))
    engine.register_agent("a1", location_id="cafe")
    engine.register_agent("a2", location_id="cafe")

    result = await engine.tick()

    assert set(result.due_agents) == {"a1", "a2"}
    assert set(result.perceptions) == {"a1", "a2"}
    assert result.perceptions["a1"].visible_agents == ["a2"]
    assert result.perceptions["a1"].location_id == "cafe"


@pytest.mark.asyncio
async def test_mark_agent_decided_reschedules_agent() -> None:
    # cadence 2: a1 decides every other tick once marked decided each time.
    engine = TickEngine(scheduler_config=SchedulerConfig(target_fraction=0.5))
    engine.register_agent("a1")

    result1 = await engine.tick()  # tick 1: next_decision_tick was 1, due
    assert "a1" in result1.due_agents
    engine.mark_agent_decided("a1")  # reschedules to 1 + cadence(2) = 3

    result2 = await engine.tick()  # tick 2: not due yet
    assert "a1" not in result2.due_agents

    result3 = await engine.tick()  # tick 3: due again, on schedule
    assert "a1" in result3.due_agents


@pytest.mark.asyncio
async def test_no_checkpoint_before_a_full_day_elapses() -> None:
    engine = TickEngine()
    for _ in range(TICKS_PER_DAY - 1):
        result = await engine.tick()
        assert result.checkpoint is None


@pytest.mark.asyncio
async def test_checkpoint_written_exactly_on_day_boundary() -> None:
    sink = InMemoryCheckpointSink()
    engine = TickEngine(checkpoint_sink=sink)
    engine.register_agent("a1", location_id="cafe")

    checkpoints_seen = []
    for _ in range(TICKS_PER_DAY):
        result = await engine.tick()
        if result.checkpoint is not None:
            checkpoints_seen.append(result.checkpoint)

    assert len(checkpoints_seen) == 1
    checkpoint = checkpoints_seen[0]
    assert checkpoint.tick_index == TICKS_PER_DAY
    assert checkpoint.game_day == 1
    assert set(checkpoint.components) == {"clock", "scheduler", "locations"}
    assert sink.checkpoints == [checkpoint]


@pytest.mark.asyncio
async def test_system_checkpoint_written_event_fires_on_day_boundary() -> None:
    engine = TickEngine()
    received: list[WorldEvent] = []

    async def handler(event: WorldEvent) -> None:
        received.append(event)

    engine.event_bus.subscribe("system", handler)

    for _ in range(TICKS_PER_DAY):
        await engine.tick()

    assert len(received) == 1
    assert received[0].type == "system.checkpoint.written"
    assert received[0].payload["game_day"] == 1


@pytest.mark.asyncio
async def test_perception_interrupt_flows_through_the_full_stack() -> None:
    """An event naming an agent (per perception.py's payload convention)
    pulls them into the due set on the very next tick even though their
    own schedule says they're not due yet."""
    engine = TickEngine(scheduler_config=SchedulerConfig(target_fraction=1 / 20))
    engine.register_agent("victim", location_id="market")
    # cadence 20: victim's baseline next_decision_tick is far away.
    assert "victim" not in engine.scheduler.due_agents(engine.clock.tick_index)

    await engine.event_bus.emit(
        "crime.theft.attempted",
        tick=engine.clock.tick_index,
        payload={"location_id": "market", "target_agent_id": "victim"},
    )

    result = await engine.tick()
    assert "victim" in result.due_agents


@pytest.mark.asyncio
async def test_restore_from_checkpoint_resets_state_on_a_fresh_engine() -> None:
    sink = InMemoryCheckpointSink()
    source = TickEngine(
        checkpoint_sink=sink, scheduler_config=SchedulerConfig(target_fraction=1 / 3)
    )
    source.register_agent("a1", location_id="cafe")
    source.register_agent("a2", location_id="park")

    for _ in range(TICKS_PER_DAY):
        await source.tick()
    checkpoint = sink.latest
    assert checkpoint is not None

    fresh = TickEngine()
    await fresh.restore_from_checkpoint(checkpoint)

    assert fresh.clock.tick_index == source.clock.tick_index == TICKS_PER_DAY
    assert fresh.locations.location_of("a1") == "cafe"
    assert fresh.locations.location_of("a2") == "park"
    assert fresh.scheduler.agent_count == source.scheduler.agent_count == 2
    # Scheduling behavior from here matches exactly, tick for tick.
    for tick in range(TICKS_PER_DAY + 1, TICKS_PER_DAY + 5):
        assert fresh.scheduler.due_agents(tick) == source.scheduler.due_agents(tick)


@pytest.mark.asyncio
async def test_run_burst_advances_exactly_n_ticks() -> None:
    engine = TickEngine(pacing_config=PacingConfig(mode=PacingMode.BURST))
    results = await engine.run_burst(10)

    assert len(results) == 10
    assert engine.clock.tick_index == 10
    assert [r.tick.tick_index for r in results] == list(range(1, 11))


@pytest.mark.asyncio
async def test_stop_halts_a_running_continuous_engine() -> None:
    import asyncio

    pacing = PacingConfig(mode=PacingMode.CONTINUOUS, continuous_interval_s=0.0)
    engine = TickEngine(pacing_config=pacing)
    task = asyncio.create_task(engine.run_continuous())

    for _ in range(50):
        await asyncio.sleep(0)
        if engine.clock.tick_index >= 3:
            break
    engine.stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert engine.clock.tick_index >= 3


@pytest.mark.asyncio
async def test_tool_call_style_event_updates_location_and_next_perception() -> None:
    """Simulates a world-tools 'move' primitive publishing agent.moved -
    the perception system should reflect the new location on the very
    next build_perception call, with no direct API coupling required."""
    engine = TickEngine()
    engine.register_agent("a1", location_id="cafe")
    engine.register_agent("a2", location_id="park")

    await engine.event_bus.emit(
        "agent.moved",
        tick=engine.clock.tick_index,
        payload={"agent_id": "a1", "location_id": "park"},
    )

    perception = engine.perception.build_perception("a2", tick=engine.clock.tick_index)
    assert perception.visible_agents == ["a1"]
