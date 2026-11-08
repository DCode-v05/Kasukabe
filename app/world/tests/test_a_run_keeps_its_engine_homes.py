"""Run 29's engine homes were deleted by the operating system.

Every engine keeps its own home under `home_root` - the session log that
records each turn, the persona, and `brain.sqlite`, the resident's
conversation store and memory. The default home root is
`/tmp/kasukabe-agents`, and macOS clears `/tmp`. Run 29's 20 homes, 135 MB,
were gone within a day: the raw material for every re-analysis and every
replay (`engine/tests/eval_records_in_world_voice.py` rebuilds histories
from exactly these files) survives only as whatever the report happened to
quote before it went.

INFRA-09 named this and left it open: "the engines' own brain.sqlite files
are still not archived (that is the second half of INFRA-09, and it belongs
to the runner's stop path)". This is that half. The runner packs the homes
into the run's checkpoint directory - which is under `data/`, beside the
trace - once the engines have stopped, so their databases are closed and
the copy is whole.
"""

from __future__ import annotations

import tarfile

import pytest


def _homes(tmp_path):
    root = tmp_path / "homes"
    for agent in ("n01", "n02"):
        home = root / agent
        home.mkdir(parents=True)
        (home / "session_log.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
        (home / "brain.sqlite").write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)
    return root


def test_the_homes_are_packed_beside_the_checkpoints(tmp_path) -> None:
    from world.run_genesis import archive_engine_homes

    archive = archive_engine_homes(_homes(tmp_path), tmp_path / "checkpoints")

    assert archive is not None and archive.exists()
    assert archive.parent == tmp_path / "checkpoints"
    names = tarfile.open(archive).getnames()
    assert any(n.endswith("n01/brain.sqlite") for n in names), names
    assert any(n.endswith("n02/session_log.jsonl") for n in names), names


def test_no_homes_is_nothing_to_keep_and_not_an_error(tmp_path) -> None:
    from world.run_genesis import archive_engine_homes

    assert archive_engine_homes(tmp_path / "never-made", tmp_path / "checkpoints") is None


def test_a_failed_archive_never_stops_a_shutdown(tmp_path, monkeypatch) -> None:
    """Keeping evidence must not cost the rest of the stop path."""
    from world import run_genesis

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(run_genesis.tarfile, "open", broken)

    assert run_genesis.archive_engine_homes(_homes(tmp_path), tmp_path / "checkpoints") is None


@pytest.mark.asyncio
async def test_a_stopping_runner_archives_them(tmp_path, monkeypatch) -> None:
    """The wiring: a real runner's shutdown calls it. A packing function
    nothing calls is the defect this project keeps finding."""
    from world.run_genesis import GenesisRunner, GenesisRunnerConfig

    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    homes = _homes(tmp_path)
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        home_root=str(homes),
    ))
    await runner.boot()
    await runner.shutdown()

    assert (tmp_path / "checkpoints" / "engine-homes.tar.gz").exists()
