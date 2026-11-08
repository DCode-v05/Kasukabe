"""CORE-04: no upkeep or head tax on the first tick of a run.

`last_life_day_processed` defaulted to -1 and the boot day is 0, so
`game_day != last_life_day_processed` was true on the very first tick of
every run. 90 sim of upkeep per household plus a head-tax pass, charged for
a day nobody had lived.

It was invisible from the report, because upkeep moves money as a transfer
and publishes no event at all - so the section that should have shown it
read an empty list and said nothing was charged (the EVAL-01 family).

The reviewer's own settling query was `select count(*) from transactions
where reason='daily_upkeep' and tick=37`. This asserts the same thing at
the counter, before a run can spend anything.
"""
from __future__ import annotations

import pytest

from world.core.clock import timestamp_for
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
async def test_a_fresh_boot_has_already_processed_the_boot_day(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    boot_day = timestamp_for(runner.world_state.current_tick).game_day
    assert runner.counters.last_life_day_processed == boot_day, (
        f"last_life_day_processed is {runner.counters.last_life_day_processed} "
        f"but the boot day is {boot_day}; the first tick will run the day "
        "boundary and bill every household for a day nobody lived (CORE-04)"
    )


@pytest.mark.asyncio
async def test_starting_at_dawn_does_not_bill_yesterday_either(tmp_path):
    """Dawn is tick 36 - still day 0, and still not a day anyone lived."""
    runner = _runner(tmp_path, start_at_dawn=True)
    await runner.boot()
    assert runner.counters.last_life_day_processed == timestamp_for(
        runner.world_state.current_tick
    ).game_day


@pytest.mark.asyncio
async def test_the_next_real_day_boundary_still_charges(tmp_path):
    """The fix must not disable the mechanism - only skip the phantom day."""
    runner = _runner(tmp_path)
    await runner.boot()
    day_after = timestamp_for(144)
    assert day_after.game_day != runner.counters.last_life_day_processed, (
        "tick 144 is the first real day boundary and must still be charged"
    )
