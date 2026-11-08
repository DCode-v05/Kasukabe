"""A $30 run that ends inside game-day 2 should be continued from there.

Most of that already works, and this file says so: `resume_at_tick` restores
the cast, the clock, the RNG and the invocation cursor and refuses to travel
backwards, and `shutdown()` writes a final checkpoint at the tick the run
stopped on - not merely at the last midnight it passed. The first two tests
here are guards on that, because a continuation is worthless if either
regresses.

Two things did not work.

FIRST: that final checkpoint is the last statement of the run, and it caught
only `CheckpointError`. Anything else a sink or a component raised escaped,
taking the clean shutdown with it - and the checkpoint it failed to write is
exactly what a continuation would have resumed from. It now survives any
failure and says so in the log.

SECOND: the residents' own memories. `archive_engine_homes` has packed every
`brain.sqlite` and session log into the checkpoint directory since INFRA-09,
and nothing has ever unpacked it. The default home root is under `/tmp`,
which macOS clears, so continuing a town produced residents who remembered
the whole run in the world state and nothing at all in their own heads -
while the archive sat beside the checkpoint being resumed from.

What no test here can fix: a run that is killed rather than stopped (Ctrl-C
at the wrong moment, an OOM, a closed laptop) never reaches `shutdown()`, and
falls back to the last day boundary.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest
import pytest_asyncio

from world.run_genesis import (
    GenesisRunner,
    GenesisRunnerConfig,
    archive_engine_homes,
    restore_engine_homes,
)


@pytest_asyncio.fixture
async def booted(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        home_root=str(tmp_path / "homes"),
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()
    await runner.attach_persistence()
    return runner, tmp_path


# ── the tick a run stopped on ─────────────────────────────────────────


def _stand_at(runner, tick: int) -> None:
    runner.tick_engine.clock.tick_index = tick
    runner.world_state.current_tick = tick


@pytest.mark.asyncio
async def test_stopping_writes_a_checkpoint_at_the_tick_it_stopped(booted) -> None:
    runner, _ = booted
    _stand_at(runner, 250)

    await runner.shutdown()

    latest = runner.checkpoint_store.latest()
    assert latest is not None, (
        "the run stopped inside game-day 1 and left nothing to continue from"
    )
    assert latest.tick_index == 250


@pytest.mark.asyncio
async def test_it_beats_the_last_midnight(booted) -> None:
    """The whole point: a resume must find the stop, not the day boundary
    before it. 106 ticks of a paid-for morning sit between them."""
    runner, _ = booted
    await runner.tick_engine.checkpoints.write(144, 1)     # the day-boundary one
    _stand_at(runner, 250)

    await runner.shutdown()

    assert runner.checkpoint_store.latest().tick_index == 250


@pytest.mark.asyncio
async def test_a_failed_final_checkpoint_does_not_stop_the_shutdown(booted) -> None:
    runner, _ = booted

    async def boom(*a, **k):
        raise RuntimeError("no")

    runner.tick_engine.checkpoints.write = boom
    await runner.shutdown()          # must not raise


# ── the residents' memories ───────────────────────────────────────────


def _a_town_with_memories(tmp_path) -> Path:
    homes = tmp_path / "homes"
    (homes / "n01").mkdir(parents=True)
    (homes / "n01" / "brain.sqlite").write_text("what Ivo remembers", encoding="utf-8")
    (homes / "n02").mkdir(parents=True)
    (homes / "n02" / "session_log.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    return homes


def test_the_memories_come_back_when_the_home_root_is_gone(tmp_path) -> None:
    homes = _a_town_with_memories(tmp_path)
    checkpoints = tmp_path / "checkpoints"
    archive_engine_homes(homes, checkpoints)
    import shutil

    shutil.rmtree(homes)                       # /tmp, cleared between runs

    restored = restore_engine_homes(homes, checkpoints)

    assert restored is not None
    assert (homes / "n01" / "brain.sqlite").read_text(encoding="utf-8") == "what Ivo remembers"
    assert (homes / "n02" / "session_log.jsonl").exists()


def test_a_home_root_under_a_new_name_still_gets_them(tmp_path) -> None:
    """Runs are booted with their own home roots - `kasukabe-agents-run29`,
    `kasukabe-agents-teambox50`. The archive must not depend on the next
    run choosing the same one."""
    homes = _a_town_with_memories(tmp_path)
    checkpoints = tmp_path / "checkpoints"
    archive_engine_homes(homes, checkpoints)
    elsewhere = tmp_path / "homes-run-31"

    restore_engine_homes(elsewhere, checkpoints)

    assert (elsewhere / "n01" / "brain.sqlite").exists()


def test_memories_that_are_still_there_are_never_overwritten(tmp_path) -> None:
    """The live homes are the newer ones. Restoring over them would hand a
    resident an older self."""
    homes = _a_town_with_memories(tmp_path)
    checkpoints = tmp_path / "checkpoints"
    archive_engine_homes(homes, checkpoints)
    (homes / "n01" / "brain.sqlite").write_text("a later morning", encoding="utf-8")

    assert restore_engine_homes(homes, checkpoints) is None
    assert (homes / "n01" / "brain.sqlite").read_text(encoding="utf-8") == "a later morning"


def test_no_archive_is_not_an_error(tmp_path) -> None:
    """Every run before this existed has none, and a first run never does."""
    assert restore_engine_homes(tmp_path / "homes", tmp_path / "checkpoints") is None


def test_it_refuses_to_write_outside_the_home_root(tmp_path) -> None:
    """A tar is an untrusted file format. One member named `../escape` must
    not land beside the checkpoints."""
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    victim = tmp_path / "escape"
    payload = tmp_path / "payload"
    payload.write_text("no", encoding="utf-8")
    with tarfile.open(checkpoints / "engine-homes.tar.gz", "w:gz") as tar:
        tar.add(payload, arcname="homes/../../escape")

    restore_engine_homes(tmp_path / "homes", checkpoints)

    assert not victim.exists()


@pytest.mark.asyncio
async def test_a_booting_run_picks_the_memories_up(tmp_path, monkeypatch) -> None:
    """The mechanism needs a caller: the second run of a continued town has
    to find them without anybody unpacking a tarball by hand."""
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    homes = _a_town_with_memories(tmp_path)
    checkpoints = tmp_path / "checkpoints"
    archive_engine_homes(homes, checkpoints)
    import shutil

    shutil.rmtree(homes)

    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=checkpoints, home_root=str(homes),
        world_mode="embedded", world_port=0,
    ))
    await runner.boot()

    assert (homes / "n01" / "brain.sqlite").exists(), (
        "the run booted beside an archive of its own town's memories and "
        "left them in the tarball"
    )
