"""Tests for LCScheduler pause/resume.

Uses a real LCScheduler with a stub LCPipeline that records each run_batch.
No mocks of domain logic — just a pipeline fixture that counts invocations.
"""

from __future__ import annotations

import asyncio
import pytest

from src.learning_centre.scheduler import LCScheduler


class StubPipelineResult:
    signals_processed = 0
    errors: list = []


class StubPipeline:
    def __init__(self) -> None:
        self.batch_calls: int = 0

    async def run_batch(self) -> StubPipelineResult:
        self.batch_calls += 1
        return StubPipelineResult()


@pytest.mark.asyncio
async def test_scheduler_starts_and_runs_batch():
    pipe = StubPipeline()
    sched = LCScheduler(pipe, interval_hours=999.0)
    await sched.start()
    await asyncio.sleep(0.1)
    await sched.stop()
    assert pipe.batch_calls >= 1


@pytest.mark.asyncio
async def test_pause_stops_batch_loop():
    pipe = StubPipeline()
    sched = LCScheduler(pipe, interval_hours=999.0)
    sched.interval_seconds = 0.05
    await sched.start()
    await asyncio.sleep(0.05)
    calls_before_pause = pipe.batch_calls
    assert calls_before_pause >= 1

    await sched.pause()
    assert sched.paused is True

    await asyncio.sleep(0.3)
    assert pipe.batch_calls == calls_before_pause, (
        f"expected no batches while paused, got {pipe.batch_calls - calls_before_pause} more"
    )

    await sched.stop()


@pytest.mark.asyncio
async def test_resume_restarts_batch_loop():
    pipe = StubPipeline()
    sched = LCScheduler(pipe, interval_hours=999.0)
    sched.interval_seconds = 0.02
    await sched.start()
    await asyncio.sleep(0.1)
    await sched.pause()
    await asyncio.sleep(0.1)
    before = pipe.batch_calls

    await sched.resume()
    assert sched.paused is False
    await asyncio.sleep(0.3)
    assert pipe.batch_calls > before

    await sched.stop()


@pytest.mark.asyncio
async def test_pause_is_idempotent():
    pipe = StubPipeline()
    sched = LCScheduler(pipe, interval_hours=999.0)
    await sched.start()
    await sched.pause()
    await sched.pause()
    await sched.pause()
    assert sched.paused is True
    await sched.resume()
    assert sched.paused is False
    await sched.stop()


@pytest.mark.asyncio
async def test_resume_when_not_paused_is_noop():
    pipe = StubPipeline()
    sched = LCScheduler(pipe, interval_hours=999.0)
    await sched.start()
    await sched.resume()
    assert sched.paused is False
    await sched.stop()


@pytest.mark.asyncio
async def test_pause_wakes_inflight_sleep():
    """Regression: pause() must interrupt an in-flight asyncio.sleep.

    Without interruptible sleep, pause takes up to interval_seconds to
    take effect (6 hours at default config).  With interruption, pause
    is immediate.
    """
    pipe = StubPipeline()
    sched = LCScheduler(pipe, interval_hours=999.0)
    sched.interval_seconds = 60.0
    await sched.start()
    await asyncio.sleep(0.1)
    batches_before = pipe.batch_calls
    assert batches_before >= 1

    t0 = asyncio.get_event_loop().time()
    await sched.pause()
    await asyncio.sleep(0.1)
    elapsed = asyncio.get_event_loop().time() - t0

    assert elapsed < 1.0, f"pause took {elapsed:.2f}s — sleep was not interrupted"
    assert sched.paused is True
    await sched.stop()


@pytest.mark.asyncio
async def test_trigger_now_works_while_paused():
    """Manual trigger_now bypasses the pause gate — ops override."""
    pipe = StubPipeline()
    sched = LCScheduler(pipe, interval_hours=999.0)
    await sched.start()
    await sched.pause()
    await sched.trigger_now()
    assert pipe.batch_calls >= 1
    await sched.stop()
