"""CORE-01: `--tick-interval-s` must actually pace the town.

The loop ended on `asyncio.sleep(0)` - a yield, not a wait. So the knob was
dead, the town ran at the speed of its slowest turn, and the game clock
advanced at whatever rate Bedrock happened to answer. Two runs of "one game
day" were not comparable, because a game hour was four seconds in one and
ninety in another.

The reviewer's acceptance test, written as they specified it: a run with
`tick_interval_s=0.2` and 10 ticks must take at least 2 seconds.
"""
from __future__ import annotations

import time

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


def _runner(tmp_path, **kw) -> GenesisRunner:
    cfg = dict(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    )
    cfg.update(kw)
    return GenesisRunner(GenesisRunnerConfig(**cfg))


@pytest.mark.asyncio
async def test_ten_ticks_at_two_tenths_take_at_least_two_seconds(tmp_path):
    runner = _runner(tmp_path, tick_interval_s=0.2)
    started = time.monotonic()
    for _ in range(10):
        await runner.wait_out_the_tick(time.monotonic())
    elapsed = time.monotonic() - started
    assert elapsed >= 2.0, (
        f"10 ticks at 0.2s took {elapsed:.3f}s - the interval is not being "
        "waited out, so --tick-interval-s is a dead knob and the game clock "
        "advances at whatever rate the broker happens to answer (CORE-01)"
    )


@pytest.mark.asyncio
async def test_an_overrunning_tick_does_not_compound_the_delay(tmp_path):
    """The REMAINDER, not the whole interval.

    A tick that already spent its whole budget must start the next one now.
    Sleeping a full interval on top would make every slow tick push the
    clock further behind, permanently.
    """
    runner = _runner(tmp_path, tick_interval_s=0.5)
    already_spent = time.monotonic() - 0.6  # this tick overran its budget
    started = time.monotonic()
    await runner.wait_out_the_tick(already_spent)
    elapsed = time.monotonic() - started
    assert elapsed < 0.1, f"waited {elapsed:.3f}s after an overrun; should not wait at all"
    assert runner.ticks_that_overran == 1, "an overrun must be counted, not swallowed"


@pytest.mark.asyncio
async def test_zero_means_unpaced_and_still_yields(tmp_path):
    runner = _runner(tmp_path, tick_interval_s=0.0)
    started = time.monotonic()
    await runner.wait_out_the_tick(time.monotonic())
    assert time.monotonic() - started < 0.05
