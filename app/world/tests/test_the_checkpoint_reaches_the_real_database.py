"""INFRA-01: `world_snapshots` must land in the run's own database.

`mirror_checkpoint_to_shared_store` called `get_client()` with no argument.
That falls through to `SupabaseConfig.from_env()`, whose `sqlite_path`
defaults to ":memory:". So on every real run the snapshot row and the
checkpoint archive went into a throwaway database discarded at process
exit - and the very next line logged "checkpoint written: tick=... -> path"
because the LOCAL write had succeeded.

Nothing failed, nothing warned, and the shared store was empty. The
reviewer's acceptance test: boot a mock run with TRACE_SQLITE_PATH set and
`select count(*) from world_snapshots` on that file must not be zero.
"""
from __future__ import annotations

import sqlite3

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


@pytest.mark.asyncio
async def test_a_checkpoint_row_lands_in_the_configured_file(tmp_path, monkeypatch):
    db = tmp_path / "kasukabe.db"
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(db))

    runner = GenesisRunner(
        GenesisRunnerConfig(
            mock_mode=True,
            checkpoint_dir=tmp_path / "checkpoints",
            world_mode="embedded",
            world_port=0,
            budget_cap_cents=40_000,
        )
    )
    await runner.boot()
    assert runner.trace_store is not None

    sink = runner.build_checkpoint_sink()
    checkpoint = await runner.tick_engine.checkpoints.write(tick_index=144, game_day=1)
    await sink(checkpoint)

    conn = sqlite3.connect(str(db))
    (count,) = conn.execute("select count(*) from world_snapshots").fetchone()
    conn.close()

    assert count >= 1, (
        f"world_snapshots in {db.name} holds {count} rows after a checkpoint. "
        "The mirror opened its own ':memory:' client, wrote there, and the "
        "runner logged 'checkpoint written' anyway (INFRA-01)."
    )


def test_the_mirror_takes_a_client_so_it_cannot_open_its_own(tmp_path):
    """The signature is the fix: a caller that has a store must pass it."""
    import inspect

    from world.run_genesis import mirror_checkpoint_to_shared_store

    params = inspect.signature(mirror_checkpoint_to_shared_store).parameters
    assert "client" in params, (
        "the mirror cannot be given the run's own database, so it will "
        "always resolve one from the environment"
    )
