"""Tests for ActivityPolicy — consumer wiring for ActivityStateChanged.

Drives the policy with real ActivityStateChanged events through a real
EventEmitter. LCScheduler, Raven, and Coordinator are replaced by
lightweight recorders — NOT mocks of domain logic, just fixtures that
capture calls so we can assert the policy dispatches correctly.
"""

from __future__ import annotations

import asyncio
import pytest

from src.engine_core.events import ActivityStateChanged, EventEmitter
from src.utils.activity_consumers import ActivityPolicy


class FakeLCScheduler:
    """Records calls made by the policy. Not a mock of LC logic."""

    def __init__(self) -> None:
        self.sweep_calls: int = 0
        self.pause_calls: int = 0
        self.resume_calls: int = 0
        self.paused: bool = False

    async def trigger_sweep(self) -> int:
        self.sweep_calls += 1
        return 0

    async def pause(self) -> None:
        self.pause_calls += 1
        self.paused = True

    async def resume(self) -> None:
        self.resume_calls += 1
        self.paused = False


class FakeRaven:
    """Records events sent to raven.track."""

    def __init__(self) -> None:
        self.records: list = []

    async def track(self, event) -> None:
        self.records.append(event)


def evt(old: str, new: str, **kw) -> ActivityStateChanged:
    return ActivityStateChanged(
        old_state=old,
        new_state=new,
        idle_seconds=kw.get("idle_seconds", 0.0),
        tool_calls=kw.get("tool_calls", 0),
        errors=kw.get("errors", 0),
        user_active_seconds=kw.get("user_active_seconds", 0.0),
        engine_active_seconds=kw.get("engine_active_seconds", 0.0),
    )


@pytest.mark.asyncio
async def test_policy_triggers_sweep_on_idle():
    emitter = EventEmitter()
    sched = FakeLCScheduler()
    policy = ActivityPolicy(emitter, lc_scheduler=sched)
    policy.install()

    await emitter.emit(evt("active", "idle"))
    await asyncio.sleep(0.05)

    assert sched.sweep_calls == 1
    assert sched.pause_calls == 0
    assert sched.resume_calls == 0


@pytest.mark.asyncio
async def test_policy_sweeps_and_pauses_on_away():
    emitter = EventEmitter()
    sched = FakeLCScheduler()
    policy = ActivityPolicy(emitter, lc_scheduler=sched)
    policy.install()

    await emitter.emit(evt("idle", "away"))
    await asyncio.sleep(0.05)

    assert sched.sweep_calls == 1
    assert sched.pause_calls == 1
    assert sched.paused is True


@pytest.mark.asyncio
async def test_policy_pauses_on_disconnected():
    emitter = EventEmitter()
    sched = FakeLCScheduler()
    policy = ActivityPolicy(emitter, lc_scheduler=sched)
    policy.install()

    await emitter.emit(evt("away", "disconnected"))
    await asyncio.sleep(0.05)

    assert sched.pause_calls == 1
    assert sched.paused is True


@pytest.mark.asyncio
async def test_policy_resumes_on_reconnect():
    emitter = EventEmitter()
    sched = FakeLCScheduler()
    sched.paused = True
    policy = ActivityPolicy(emitter, lc_scheduler=sched)
    policy.install()

    await emitter.emit(evt("away", "active"))
    await asyncio.sleep(0.05)

    assert sched.resume_calls == 1
    assert sched.paused is False


@pytest.mark.asyncio
async def test_policy_noop_for_active_to_active_spurious():
    emitter = EventEmitter()
    sched = FakeLCScheduler()
    policy = ActivityPolicy(emitter, lc_scheduler=sched)
    policy.install()

    await emitter.emit(evt("active", "active"))
    await asyncio.sleep(0.05)

    assert sched.sweep_calls == 0
    assert sched.pause_calls == 0
    assert sched.resume_calls == 0


@pytest.mark.asyncio
async def test_policy_records_to_raven():
    emitter = EventEmitter()
    raven = FakeRaven()
    policy = ActivityPolicy(emitter, raven_track=raven.track)
    policy.install()

    await emitter.emit(evt("active", "idle", idle_seconds=300.0, tool_calls=3))
    await emitter.emit(evt("idle", "away", idle_seconds=1800.0, tool_calls=5))
    await asyncio.sleep(0.05)

    assert len(raven.records) == 2
    assert raven.records[0].old_state == "active"
    assert raven.records[0].new_state == "idle"
    assert raven.records[0].tool_calls == 3
    assert raven.records[1].new_state == "away"


@pytest.mark.asyncio
async def test_policy_ignores_non_activity_events():
    """Policy subscribes to the shared emitter but only reacts to its own event type."""
    from src.engine_core.events import TextDelta

    emitter = EventEmitter()
    sched = FakeLCScheduler()
    raven = FakeRaven()
    policy = ActivityPolicy(emitter, lc_scheduler=sched, raven_track=raven.track)
    policy.install()

    await emitter.emit(TextDelta(thread_id="t", text="hi"))
    await asyncio.sleep(0.05)

    assert sched.sweep_calls == 0
    assert sched.pause_calls == 0
    assert len(raven.records) == 0


@pytest.mark.asyncio
async def test_install_is_idempotent():
    emitter = EventEmitter()
    sched = FakeLCScheduler()
    policy = ActivityPolicy(emitter, lc_scheduler=sched)
    policy.install()
    policy.install()
    policy.install()

    await emitter.emit(evt("active", "idle"))
    await asyncio.sleep(0.05)
    assert sched.sweep_calls == 1


@pytest.mark.asyncio
async def test_uninstall_stops_dispatch():
    emitter = EventEmitter()
    sched = FakeLCScheduler()
    policy = ActivityPolicy(emitter, lc_scheduler=sched)
    policy.install()
    policy.uninstall()

    await emitter.emit(evt("active", "idle"))
    await asyncio.sleep(0.05)

    assert sched.sweep_calls == 0


@pytest.mark.asyncio
async def test_policy_survives_subsystem_crash():
    """A crash inside trigger_sweep must not tear down the event bus."""
    emitter = EventEmitter()

    class CrashyScheduler(FakeLCScheduler):
        async def trigger_sweep(self) -> int:
            raise RuntimeError("simulated flush failure")

    sched = CrashyScheduler()
    policy = ActivityPolicy(emitter, lc_scheduler=sched)
    policy.install()

    await emitter.emit(evt("active", "idle"))
    await emitter.emit(evt("idle", "away"))
    await asyncio.sleep(0.05)

    assert sched.pause_calls == 1
