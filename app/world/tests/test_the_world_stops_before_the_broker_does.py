"""The run halts itself while the broker will still answer.

`CORE-25`. The runner's spend governor and the broker's cap were the same
number. Two counters race to it - the broker counts what it dispatched,
the ledger counts what was burned - and the broker's arrives first. The
ending was always the same shape: the broker halts, every engine turn
500s, one whole barren tick passes, and only then does the probe stop the
run. Every one of those refusals is a round trip and a trace row, and the
learning drain (`CORE-05`) that is supposed to fire "while the broker
will still answer" fires into a broker that will not.

Second half: `/metrics` reported `spend_usd_cents` with no scale beside
it. An operator had to know the cap from somewhere else to read it, and
on every run so far that somewhere else was a different number from the
one actually in force.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from world.run_genesis import (
    HALT_RESERVE_FLOOR_CENTS,
    GenesisRunnerConfig,
    GenesisRunner,
    build_embedded_world_app,
    halt_reserve_cents,
)
from world.admin_api import AdminExtras


def test_the_runner_stops_below_the_brokers_cap() -> None:
    """A $2.50 run - run 24's cap - must not aim at exactly $2.50."""
    config = GenesisRunnerConfig.from_env({"SPEND_CAP_USD": "2.50"})
    assert config.budget_cap_cents < 250, (
        "CORE-25: the runner's cap is the broker's cap, so the governor can "
        "only trip after the broker has already started refusing calls"
    )
    assert config.budget_cap_cents == 250 - halt_reserve_cents(250)


def test_the_reserve_is_a_proportion_with_a_floor() -> None:
    """Big runs need a proportional margin; tiny ones still need some."""
    assert halt_reserve_cents(40_000) == 2_000
    assert halt_reserve_cents(50) == HALT_RESERVE_FLOOR_CENTS
    assert halt_reserve_cents(1) == HALT_RESERVE_FLOOR_CENTS


def test_the_reserve_never_eats_the_whole_cap() -> None:
    """A $0.03 run is a mistake, but it must not become a $0 run that
    halts before its first call."""
    config = GenesisRunnerConfig.from_env({"SPEND_CAP_USD": "0.03"})
    assert config.budget_cap_cents >= 1


def test_an_explicit_cap_is_left_exactly_alone() -> None:
    """A run that deliberately wants the two equal says so, and this must
    not quietly shave it."""
    config = GenesisRunnerConfig.from_env(
        {"SPEND_CAP_USD": "2.50", "KASUKABE_BUDGET_CAP_CENTS": "250"}
    )
    assert config.budget_cap_cents == 250


def test_the_reserve_covers_more_than_one_tick() -> None:
    """The reason the reserve exists: spend is only observed between
    ticks, so a margin smaller than one tick's spend cannot stop the run
    in time. Run 24's priciest ticks ran a few cents."""
    assert halt_reserve_cents(250) >= 10


@pytest.mark.asyncio
async def test_metrics_reports_the_cap_it_will_stop_at(tmp_path):
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=238,
    ))
    await runner.boot()
    app = build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics,
        tick_engine=runner.tick_engine, genesis=runner.genesis,
        map_service=runner.map_service,
        admin_extras=AdminExtras(operator_token="t"),
    )
    with TestClient(app) as client:
        body = client.get("/metrics").json()

    assert body["budget_cap_usd_cents"] == 238, (
        "CORE-25: /metrics reports spend with no scale beside it, so the "
        f"operator cannot tell how close the run is to stopping. Got: {body}"
    )
    assert body["spend_fraction_of_cap"] is not None


@pytest.mark.asyncio
async def test_an_unknown_cap_is_not_an_infinite_one() -> None:
    """`None`, not 0.0. A metrics object built without a cap must not
    render as "0% of budget used" - which is what a reader takes from a
    zero, and it is the opposite of the truth."""
    from world.run_genesis import GenesisMetrics, RunnerCounters
    from world.tools.bootstrap import build_world

    world, _registry = await build_world(genesis_tick=0, seed=1)
    metrics = GenesisMetrics(world.ledger, world, RunnerCounters())
    snapshot = metrics.snapshot(game_day=0)
    assert snapshot["budget_cap_usd_cents"] is None
    assert snapshot["spend_fraction_of_cap"] is None
