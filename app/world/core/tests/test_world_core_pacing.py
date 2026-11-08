"""Tests for world.core.pacing - PacingController drives a bare tick_fn."""

from __future__ import annotations

import asyncio

import pytest

from world.core.pacing import PacingConfig, PacingController, PacingMode


def test_config_rejects_negative_interval() -> None:
    with pytest.raises(ValueError):
        PacingConfig(continuous_interval_s=-1)


def test_config_defaults_to_continuous_uncapped() -> None:
    config = PacingConfig()
    assert config.mode is PacingMode.CONTINUOUS
    assert config.continuous_interval_s == 0.0


@pytest.mark.asyncio
async def test_run_burst_calls_tick_fn_exactly_n_times() -> None:
    calls: list[int] = []

    async def tick_fn() -> int:
        calls.append(len(calls))
        return calls[-1]

    controller = PacingController(tick_fn)
    results = await controller.run_burst(5)

    assert len(calls) == 5
    assert results == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_run_burst_zero_ticks_calls_nothing() -> None:
    calls: list[int] = []

    async def tick_fn() -> None:
        calls.append(1)

    controller = PacingController(tick_fn)
    results = await controller.run_burst(0)

    assert calls == []
    assert results == []


@pytest.mark.asyncio
async def test_run_burst_rejects_negative_num_ticks() -> None:
    async def tick_fn() -> None:
        pass

    controller = PacingController(tick_fn)
    with pytest.raises(ValueError):
        await controller.run_burst(-1)


@pytest.mark.asyncio
async def test_run_burst_stops_early_on_request_stop() -> None:
    calls: list[int] = []

    async def tick_fn() -> None:
        calls.append(1)
        if len(calls) == 3:
            controller.request_stop()

    controller = PacingController(tick_fn)
    results = await controller.run_burst(10)

    assert len(calls) == 3
    assert len(results) == 3


@pytest.mark.asyncio
async def test_run_continuous_stops_on_request() -> None:
    calls: list[int] = []

    async def tick_fn() -> None:
        calls.append(1)

    controller = PacingController(tick_fn, PacingConfig(continuous_interval_s=0.0))
    task = asyncio.create_task(controller.run_continuous())

    # Let a handful of ticks run before asking it to stop.
    for _ in range(50):
        await asyncio.sleep(0)
        if len(calls) >= 3:
            break
    controller.request_stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert len(calls) >= 3


@pytest.mark.asyncio
async def test_run_dispatches_burst_mode() -> None:
    calls: list[int] = []

    async def tick_fn() -> None:
        calls.append(1)

    controller = PacingController(tick_fn, PacingConfig(mode=PacingMode.BURST))
    await controller.run(num_ticks=4)

    assert len(calls) == 4


@pytest.mark.asyncio
async def test_run_burst_mode_requires_num_ticks() -> None:
    async def tick_fn() -> None:
        pass

    controller = PacingController(tick_fn, PacingConfig(mode=PacingMode.BURST))
    with pytest.raises(ValueError):
        await controller.run()


@pytest.mark.asyncio
async def test_run_dispatches_continuous_mode_and_returns_none() -> None:
    calls: list[int] = []

    async def tick_fn() -> None:
        calls.append(1)
        if len(calls) == 2:
            controller.request_stop()

    controller = PacingController(tick_fn, PacingConfig(mode=PacingMode.CONTINUOUS))
    result = await asyncio.wait_for(controller.run(), timeout=2.0)

    assert result is None
    assert len(calls) == 2
