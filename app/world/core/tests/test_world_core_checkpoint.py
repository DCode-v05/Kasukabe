"""Tests for world.core.checkpoint - CheckpointManager snapshot/restore."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from world.core.checkpoint import (
    CheckpointError,
    CheckpointManager,
    InMemoryCheckpointSink,
    WorldCheckpoint,
)


class FakeComponent:
    """A minimal Checkpointable for testing, independent of real world-core pieces."""

    def __init__(
        self, value: int = 0, fail_on_snapshot: bool = False, fail_on_restore: bool = False
    ) -> None:
        self.value = value
        self.fail_on_snapshot = fail_on_snapshot
        self.fail_on_restore = fail_on_restore

    def snapshot(self) -> dict[str, Any]:
        if self.fail_on_snapshot:
            raise RuntimeError("snapshot exploded")
        return {"value": self.value}

    def restore(self, data: dict[str, Any]) -> None:
        if self.fail_on_restore:
            raise RuntimeError("restore exploded")
        self.value = data["value"]


def test_register_duplicate_name_raises() -> None:
    manager = CheckpointManager()
    manager.register("thing", FakeComponent())
    with pytest.raises(ValueError):
        manager.register("thing", FakeComponent())


def test_unregister_removes_component() -> None:
    manager = CheckpointManager()
    manager.register("thing", FakeComponent())
    manager.unregister("thing")
    manager.register("thing", FakeComponent())  # must not raise now


def test_unregister_unknown_name_is_noop() -> None:
    manager = CheckpointManager()
    manager.unregister("nothing")  # must not raise


@pytest.mark.asyncio
async def test_write_collects_snapshot_from_each_component() -> None:
    manager = CheckpointManager()
    manager.register("clock", FakeComponent(value=42))
    manager.register("scheduler", FakeComponent(value=7))

    checkpoint = await manager.write(tick_index=144, game_day=1)

    assert checkpoint.tick_index == 144
    assert checkpoint.game_day == 1
    assert checkpoint.components == {"clock": {"value": 42}, "scheduler": {"value": 7}}


@pytest.mark.asyncio
async def test_write_calls_sink_with_the_checkpoint() -> None:
    sink = InMemoryCheckpointSink()
    manager = CheckpointManager(sink=sink)
    manager.register("clock", FakeComponent(value=1))

    checkpoint = await manager.write(tick_index=144, game_day=1)

    assert sink.latest == checkpoint
    assert sink.checkpoints == [checkpoint]


@pytest.mark.asyncio
async def test_write_without_sink_still_returns_checkpoint() -> None:
    manager = CheckpointManager()
    manager.register("clock", FakeComponent(value=1))

    checkpoint = await manager.write(tick_index=144, game_day=1)
    assert checkpoint.components == {"clock": {"value": 1}}


@pytest.mark.asyncio
async def test_write_aggregates_errors_from_every_failing_component() -> None:
    manager = CheckpointManager()
    manager.register("bad_one", FakeComponent(fail_on_snapshot=True))
    manager.register("bad_two", FakeComponent(fail_on_snapshot=True))
    manager.register("good", FakeComponent(value=1))

    with pytest.raises(CheckpointError) as exc_info:
        await manager.write(tick_index=144, game_day=1)

    assert len(exc_info.value.errors) == 2
    assert any("bad_one" in e for e in exc_info.value.errors)
    assert any("bad_two" in e for e in exc_info.value.errors)


@pytest.mark.asyncio
async def test_write_failure_does_not_call_sink() -> None:
    sink = InMemoryCheckpointSink()
    manager = CheckpointManager(sink=sink)
    manager.register("bad", FakeComponent(fail_on_snapshot=True))

    with pytest.raises(CheckpointError):
        await manager.write(tick_index=144, game_day=1)

    assert sink.checkpoints == []


@pytest.mark.asyncio
async def test_restore_calls_restore_on_each_registered_component() -> None:
    manager = CheckpointManager()
    clock = FakeComponent(value=0)
    manager.register("clock", clock)

    checkpoint = WorldCheckpoint(tick_index=144, game_day=1, components={"clock": {"value": 99}})
    await manager.restore(checkpoint)

    assert clock.value == 99


@pytest.mark.asyncio
async def test_restore_raises_when_registered_component_missing_from_checkpoint() -> None:
    manager = CheckpointManager()
    manager.register("clock", FakeComponent())
    manager.register("scheduler", FakeComponent())

    checkpoint = WorldCheckpoint(tick_index=144, game_day=1, components={"clock": {"value": 1}})

    with pytest.raises(CheckpointError) as exc_info:
        await manager.restore(checkpoint)

    assert any("scheduler" in e for e in exc_info.value.errors)


@pytest.mark.asyncio
async def test_restore_aggregates_component_restore_failures() -> None:
    manager = CheckpointManager()
    manager.register("bad", FakeComponent(fail_on_restore=True))

    checkpoint = WorldCheckpoint(tick_index=144, game_day=1, components={"bad": {"value": 1}})

    with pytest.raises(CheckpointError):
        await manager.restore(checkpoint)


@pytest.mark.asyncio
async def test_restore_warns_and_skips_unregistered_components(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = CheckpointManager()
    manager.register("clock", FakeComponent())

    checkpoint = WorldCheckpoint(
        tick_index=144,
        game_day=1,
        components={"clock": {"value": 1}, "unknown_thing": {"value": 2}},
    )

    with caplog.at_level(logging.WARNING):
        await manager.restore(checkpoint)  # must not raise

    assert "unknown_thing" in caplog.text


def test_in_memory_sink_latest_is_none_when_empty() -> None:
    sink = InMemoryCheckpointSink()
    assert sink.latest is None


def test_checkpoint_round_trips_through_json() -> None:
    checkpoint = WorldCheckpoint(
        tick_index=144, game_day=1, components={"clock": {"tick_index": 144}}
    )
    restored = WorldCheckpoint.model_validate_json(checkpoint.model_dump_json())
    assert restored == checkpoint
