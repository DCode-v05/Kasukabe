"""What the operator turns has to be what the town reads.

`CORE-06` / `ECON-11`: `run_life_ticks` constructed a fresh
`PopulationDials(target_band_min=..., target_band_max=...)` on every tick
and handed THAT to every death roll and to upkeep. `self.population_dials`
- the object `PUT /admin/god/population-dials` mutates, and the only
population control the console has - was read by nothing in the live loop.
Setting `death_hazard_multiplier=0` changed a field and not the town.

`CORE-07`: `apply_tick_pacing()` was called from inside `seed_genesis()`,
so a run resumed from a checkpoint came back unpaced - the resume branch
restores and returns, and CORE-01's new `pace_the_tick` then reads an
interval nobody computed. Harmless while `--tick-interval-s` was dead;
a real regression the moment it started working.
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
async def test_the_life_tick_reads_the_runners_own_dials(tmp_path):
    """The wiring, asserted where it broke: the object the admin route
    mutates must be the object the tick hands to the death rolls."""
    runner = _runner(tmp_path)
    await runner.boot()

    runner.set_population_dials(death_hazard_multiplier=0.0)
    seen: list[object] = []
    original = runner.run_daily_upkeep

    async def capture(timestamp, dials, *a, **kw):
        seen.append(dials)
        return await original(timestamp, dials, *a, **kw)

    runner.run_daily_upkeep = capture  # type: ignore[method-assign]
    await runner.run_life_ticks(timestamp_for(144))

    assert seen, "run_daily_upkeep was never reached; the test proves nothing"
    assert seen[0] is runner.population_dials, (
        "CORE-06: the life tick built its own PopulationDials, so the "
        "console's dials reached nothing"
    )
    assert seen[0].death_hazard_multiplier == 0.0


@pytest.mark.asyncio
async def test_a_resumed_run_is_paced(tmp_path):
    """CORE-07. Boot once to write a checkpoint, boot again onto it, and
    assert the resume arm computed a pace.

    Asserts the CALL, not the resulting number. The constructor already
    leaves `continuous_interval_s` at 0.0 on purpose - the population does
    not exist yet - so "is not None", or any comparison against 0, passes
    whether the fix is present or not. Only the call site distinguishes
    them, which is the whole finding.
    """
    first = _runner(tmp_path)
    await first.boot()
    await first.tick_engine.checkpoints.write(tick_index=10, game_day=0)

    second = _runner(tmp_path)
    calls: list[int] = []
    original = second.apply_tick_pacing

    async def spy():
        calls.append(1)
        return await original()

    second.apply_tick_pacing = spy  # type: ignore[method-assign]
    await second.boot(resume=True)

    assert calls, (
        "CORE-07: a resumed run never called apply_tick_pacing, so it runs "
        "at whatever interval the constructor guessed with zero agents"
    )
