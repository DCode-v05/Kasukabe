"""Decision 1: the runner absorbs the server's wiring.

There were two composition roots. `main.py` built `WorldServer`, which
wires the charter sink, the crime engine, the map service and the trace
bus. Every one of the 23 runs used `GenesisRunner`, which wired none of
them. Four of the review's twenty worst findings trace to that one fact.

The operator's decision of 2026-09-04 was that the runner absorbs the
server's wiring - it is the host that has actually run - so these assert
the absorbed pieces are present on the host a real run boots.
"""
from __future__ import annotations

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


def _runner(tmp_path) -> GenesisRunner:
    return GenesisRunner(
        GenesisRunnerConfig(
            mock_mode=True,
            checkpoint_dir=tmp_path / "checkpoints",
            world_mode="embedded",
            world_port=0,
            budget_cap_cents=40_000,
        )
    )


@pytest.mark.asyncio
async def test_the_crime_engine_exists_on_the_host_that_runs(tmp_path):
    """GOV-04. The crime package was a complete, tested library that had
    never been instantiated on a running world."""
    runner = _runner(tmp_path)
    assert runner.crime is None, "nothing should exist before boot"
    await runner.boot()
    assert runner.crime is not None, (
        "GenesisRunner booted without a crime engine, so every case, "
        "warrant, confinement and metric in world/crime/ is unreachable on "
        "the only host that runs (GOV-04)"
    )
    assert runner.crime_engine_wired is True


@pytest.mark.asyncio
async def test_the_crime_engine_can_answer_for_a_quiet_town(tmp_path):
    """Built AND callable - the distinction this whole review is about."""
    runner = _runner(tmp_path)
    await runner.boot()
    metrics = await runner.crime.compute_metrics()
    assert metrics is not None, "compute_metrics had one caller and it was on the other host"


@pytest.mark.asyncio
async def test_crime_events_reach_the_bus_not_a_void(tmp_path):
    """The engine's sink must be the world's bus, or its events go nowhere
    exactly the way the charter's did (CORE-02)."""
    runner = _runner(tmp_path)
    await runner.boot()
    for desk_name in ("theft", "fights", "desk", "investigation", "warrants",
                      "arrests", "outcomes"):
        desk = getattr(runner.crime, desk_name)
        sink = getattr(desk, "event_sink", None) or getattr(desk, "_event_sink", None)
        assert sink is not None, (
            f"crime.{desk_name} has no event sink, so everything it emits is "
            "discarded exactly the way the charter's events were (CORE-02)"
        )
        assert getattr(sink, "__name__", "") != "noop_sink", (
            f"crime.{desk_name} publishes into noop_sink"
        )


@pytest.mark.asyncio
async def test_the_charter_is_absorbed_too(tmp_path):
    """CORE-02, kept green alongside its sibling."""
    runner = _runner(tmp_path)
    await runner.boot()
    assert runner.charter_sink_wired is True
