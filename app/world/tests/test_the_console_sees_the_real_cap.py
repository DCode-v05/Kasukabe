"""The console must draw its spend bar against the run's own cap.

`UI-16`. `console.js` held `const RUN_BUDGET_CAP_USD_CENTS = 40000` - $400
- and every run this project has done was capped between $1 and $10. So the
bar read about 1% full on a run that had spent its entire budget, and the
note under it said "of $400 cap" on all of them.

The console could not fix this alone: the world's `/admin/spend/status`
reported `budget_total_usd_cents`, which is the $1000 LEDGER POOL - a
different quantity that happens to also be money. Nothing on the wire
carried what the run was actually allowed to spend.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from world.admin_api import AdminExtras
from world.run_genesis import (
    GenesisRunner,
    GenesisRunnerConfig,
    build_embedded_world_app,
)


async def _app_and_client(tmp_path, cap_cents: int):
    """A booted runner and its embedded app, with a known admin token."""
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=cap_cents,
    ))
    await runner.boot()
    app = build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics,
        tick_engine=runner.tick_engine, genesis=runner.genesis,
        map_service=runner.map_service,
        admin_extras=AdminExtras(operator_token="t"),
        run_cap_usd_cents_getter=lambda: runner.config.budget_cap_cents,
    )
    return app


@pytest.mark.asyncio
async def test_the_status_reports_this_runs_cap(tmp_path):
    app = await _app_and_client(tmp_path, cap_cents=500)   # a $5 run, like run 24
    with TestClient(app) as client:
        body = client.get(
            "/admin/spend/status", headers={"X-Operator-Token": "t"}
        ).json()

    assert body.get("run_cap_usd_cents") == 500, (
        "UI-16: the world does not report the run's cap, so the console has "
        f"nothing to draw against but its own hard-coded $400. Got: {body}"
    )


@pytest.mark.asyncio
async def test_the_cap_is_not_the_ledger_pool(tmp_path):
    """The two numbers are different quantities and were being conflated:
    `budget_total_usd_cents` is the fixed $1000 the ledger must always sum
    to, and it is identical on every run."""
    app = await _app_and_client(tmp_path, cap_cents=500)
    with TestClient(app) as client:
        body = client.get(
            "/admin/spend/status", headers={"X-Operator-Token": "t"}
        ).json()

    assert body["run_cap_usd_cents"] != body["budget_total_usd_cents"]
