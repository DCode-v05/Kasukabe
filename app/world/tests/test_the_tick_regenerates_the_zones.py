"""Farmland and ore have to come back, on the host that runs.

`GROW-24` / `TOOL-20`. `resources.regenerate_zones` is a complete, tested
function whose only caller is `WorldServer.advance_tick` (`wiring.py:1142`)
- the standalone host every launcher declines to use (`start-genesis.sh`,
`runseries.sh` and `run50.sh` all pass `--world-mode embedded`). So in
every run this project has made, a harvested farmland strip and a mined
outcrop never recovered.

It has not bitten yet only because `DEFAULT_ZONE_YIELD` is 10,000 and no
run has lasted long enough to exhaust one. That is the same shape as every
other finding in this review: a mechanism complete, correct, covered by its
own tests, and invoked by nothing. It bites the moment a run reaches the
multi-day length the roadmap is aiming at.

The test drives the runner's own tick path rather than calling
`regenerate_zones` directly, because calling the helper is exactly what the
existing tests already do and is exactly what let this sit unwired for
twenty-four runs.
"""
from __future__ import annotations

import pytest

from world.core.clock import timestamp_for
from world.run_genesis import GenesisRunner, GenesisRunnerConfig
from world.tools.primitives.resources import DEFAULT_ZONE_YIELD


def _runner(tmp_path) -> GenesisRunner:
    return GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    ))


def _a_finite_zone(world):
    for zone_id, zone in sorted(world.resource_zones.items()):
        if zone.remaining_yield is not None and zone.regen_per_tick > 0:
            return zone_id, zone
    pytest.skip("no finite regenerating zone in this fixture")


@pytest.mark.asyncio
async def test_a_depleted_zone_recovers_across_ticks(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    world = runner.world_state

    zone_id, zone = _a_finite_zone(world)
    zone.remaining_yield = max(0, zone.remaining_yield - 100)
    drawn_down = zone.remaining_yield

    for tick in range(1, 40):
        await runner.run_life_ticks(timestamp_for(tick))

    assert world.resource_zones[zone_id].remaining_yield > drawn_down, (
        "GROW-24: the zone never recovered - regenerate_zones has no caller "
        "on the host that runs"
    )


@pytest.mark.asyncio
async def test_regeneration_never_mints_past_the_ceiling(tmp_path):
    """A trickle with no ceiling would make resources out of nothing, which
    is a worse bug than the one being fixed. `regenerate_zones` caps at
    DEFAULT_ZONE_YIELD; this holds the wiring to that."""
    runner = _runner(tmp_path)
    await runner.boot()
    world = runner.world_state
    zone_id, _ = _a_finite_zone(world)

    for tick in range(1, 40):
        await runner.run_life_ticks(timestamp_for(tick))

    assert world.resource_zones[zone_id].remaining_yield <= DEFAULT_ZONE_YIELD
