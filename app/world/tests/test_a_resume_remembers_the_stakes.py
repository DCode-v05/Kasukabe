"""Hunger, debt and the day's work survive a restart.

`CORE-08`. Three stores the runner owns and no checkpoint carried:

  * `hunger_by_agent` - a resident three days into an unpaid household
    came back FED, so the ladder that ends in dormancy and then death
    could never finish across a resume. The roadmap's multi-day runs on
    spot hardware are precisely where it has to.
  * `head_tax_debts` - `HeadTaxDebtStore` has had `snapshot`/`restore`
    since it was written and was never registered. A resume emptied it
    while `world.arrears` (which IS owned state) kept the amounts, so
    `repay_head_tax_debt` would raise `NoOutstandingDebtError` for a debt
    `check_debts` was still showing.
  * `day_jobs_ran` - which day boundaries have already been charged.

The roadmap claims bit-exact resume. For the economy's stakes it was not
true, and nothing failed loudly when it wasn't.
"""
from __future__ import annotations

import pytest

from world.life.upkeep import HungerState
from world.run_genesis import GenesisRunner, GenesisRunnerConfig


def _runner(tmp_path) -> GenesisRunner:
    return GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    ))


@pytest.mark.asyncio
async def test_the_stakes_round_trip(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()

    victim = sorted(runner.world_state.agents)[0]
    runner.hunger_by_agent[victim] = HungerState(consecutive_unpaid_days=3)
    runner.day_jobs_ran[7] = {"upkeep": 1}

    adapter = runner.tick_engine.checkpoints.components["stakes"]
    blob = adapter.snapshot()

    runner.hunger_by_agent = {}
    runner.day_jobs_ran = {}

    adapter.restore(blob)

    assert runner.hunger_by_agent[victim].consecutive_unpaid_days == 3, (
        "CORE-08: the starvation ladder resets on resume, so a resident "
        "cannot starve across a restart"
    )
    assert runner.day_jobs_ran[7] == {"upkeep": 1}


@pytest.mark.asyncio
async def test_the_day_ledger_keeps_integer_days(tmp_path):
    """JSON turns int keys into strings, and `game_day` is looked up as an
    int - so a naive round trip silently re-runs every day boundary."""
    runner = _runner(tmp_path)
    await runner.boot()
    runner.day_jobs_ran[3] = {"tax": 1}

    adapter = runner.tick_engine.checkpoints.components["stakes"]
    import json

    blob = json.loads(json.dumps(adapter.snapshot()))
    runner.day_jobs_ran = {}
    adapter.restore(blob)

    assert 3 in runner.day_jobs_ran, "day keys came back as strings"


@pytest.mark.asyncio
async def test_it_is_registered_for_checkpointing(tmp_path):
    """Being able to round-trip is not the same as being carried."""
    runner = _runner(tmp_path)
    await runner.boot()
    assert "stakes" in runner.tick_engine.checkpoints.components
