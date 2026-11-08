"""CORE-24: a run must record what it is, before it does anything.

DESIGN's claim is that every result traces to a row and a run is
reproducible. It was not. A run's config, seed, genesis hash, git sha,
model id and knob settings existed only in the shell history of whoever
launched it - so two runs with different casts, different prices and
different code produced reports that looked alike and could not be told
apart afterwards.

This matters more now than it did last week: `SIM_PER_USD` changed from
10,000 to 5,000 and the context window from 12,500 to 131,072. Every
report written before those changes measures a different town, and without
a manifest there is nothing in the artifact that says so.
"""
from __future__ import annotations

import json

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
async def test_booting_writes_a_manifest(tmp_path):
    runner = _runner(tmp_path)
    assert runner.run_manifest_path is None
    await runner.boot()

    path = runner.run_manifest_path
    assert path is not None and path.exists(), (
        "the run recorded nothing about itself, so its config, seed and code "
        "version live only in somebody's shell history (CORE-24)"
    )
    manifest = json.loads(path.read_text())
    for key in ("run_id", "started_at", "population_seed", "sim_per_usd",
                "budget_cap_cents", "agents"):
        assert key in manifest, f"the manifest does not record {key}"


@pytest.mark.asyncio
async def test_it_records_the_knobs_that_change_what_a_number_means(tmp_path):
    """Two reports can only be compared if the prices behind them match."""
    runner = _runner(tmp_path)
    await runner.boot()
    manifest = json.loads(runner.run_manifest_path.read_text())

    from world.ledger.money import SIM_PER_USD

    assert manifest["sim_per_usd"] == SIM_PER_USD, (
        "the exchange rate is not in the manifest, so a report from before "
        "the 2026-09-04 re-rating is indistinguishable from one after it"
    )
    for key in ("context_window", "full_schema_budget", "model_id"):
        assert key in manifest


@pytest.mark.asyncio
async def test_it_is_written_at_boot_not_at_shutdown(tmp_path):
    """A run that crashes at tick 3 is exactly the run somebody will want to
    identify later, and a manifest written at the end is the one that never
    exists when it is needed."""
    runner = _runner(tmp_path)
    await runner.boot()
    assert runner.run_manifest_path.exists()


@pytest.mark.asyncio
async def test_what_could_not_be_captured_is_null_not_absent(tmp_path):
    """A reader must be able to tell "not captured" from "captured as empty"."""
    runner = _runner(tmp_path)
    await runner.boot()
    manifest = json.loads(runner.run_manifest_path.read_text())
    assert "genesis_sha256" in manifest
    assert "git_sha" in manifest


@pytest.mark.asyncio
async def test_the_manifest_shares_the_run_id_with_the_trace(tmp_path):
    """Minting a second id would give one run two names."""
    from world.agents_api import RUN_ID

    runner = _runner(tmp_path)
    await runner.boot()
    manifest = json.loads(runner.run_manifest_path.read_text())
    assert manifest["run_id"] == RUN_ID
