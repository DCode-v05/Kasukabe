"""The research surface gets written to by an actual run.

`INFRA-10`. `research/parquet_export.export_game_day` was written to slice
the trace store into per-game-day Parquet files, and had NO CALLER
anywhere - no scheduler, no cron, no systemd timer, nothing in `world/`.
So the analysis surface every roadmap plan is built on has never had a
single file written to it on any run this project has made.

Two smaller things went with it:

  * `INSTALL sqlite` ran on every export. That reaches the network on a
    host without the extension already cached, and failed here with HTTP
    403. The Dockerfile pre-fetches it for the image, so inside a
    container the install was pure latency - and outside one, an export
    that needs the network is an export that fails exactly when a day
    boundary fires.
  * `kasukabe-litestream.service` carried a standing notice that
    `litestream.yml` "is deliberately NOT shipped" and that the unit
    "will fail to start with 'config file does not exist'". The file had
    landed; the notice had not been removed. A "this does not work yet"
    beside a thing that works is how a service nobody enables stays
    unenabled.

This is the sixth complete-and-unwired mechanism in this review. The
pattern - finished, tested, called by nothing - is this project's most
common defect by a wide margin, which is why the test that matters below
is the one asserting a CALLER exists rather than one asserting the
exporter works.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import pytest_asyncio

from world.run_genesis import GenesisRunner, GenesisRunnerConfig

APP = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()
    return runner


def test_the_day_boundary_calls_the_exporter() -> None:
    """The finding itself. A test of `export_game_day` alone would have
    passed before this fix and after it."""
    source = (APP / "world" / "run_genesis.py").read_text(encoding="utf-8")
    sink = source[source.index("def build_checkpoint_sink"):]
    sink = sink[: sink.index("\n    def ", 10)]
    assert "export_the_day" in sink, (
        "INFRA-10: a game day ends, a checkpoint is written, and nothing "
        "exports the day - the Parquet research surface has never had a "
        "file written to it on any run"
    )


@pytest.mark.asyncio
async def test_it_asks_for_the_day_that_just_ended(runner, monkeypatch, tmp_path) -> None:
    asked: list[dict] = []

    async def fake_export(*, sqlite_path, game_day, output_dir):
        asked.append({"day": game_day, "out": output_dir})
        return []

    import research.parquet_export as pe

    monkeypatch.setattr(pe, "export_game_day", fake_export)
    (tmp_path / "trace.db").write_bytes(b"")

    await runner.export_the_day(3)

    assert asked and asked[0]["day"] == 3
    assert "day-003" in str(asked[0]["out"])


@pytest.mark.asyncio
async def test_a_failed_export_does_not_end_the_run(runner, monkeypatch, tmp_path) -> None:
    """A research inconvenience must not cost a night's simulation - and
    the original failure mode was a NETWORK call at a day boundary."""

    async def boom(**kwargs):
        raise RuntimeError("duckdb could not reach the internet")

    import research.parquet_export as pe

    monkeypatch.setattr(pe, "export_game_day", boom)
    (tmp_path / "trace.db").write_bytes(b"")

    assert await runner.export_the_day(1) is None       # must not raise


@pytest.mark.asyncio
async def test_no_trace_store_is_not_an_error(runner, monkeypatch, tmp_path) -> None:
    """A run with no trace file exports nothing and says nothing. Silence
    is right here: a mock run has no trace by design, and a warning every
    game day would train the operator to ignore the log.

    (An earlier draft assumed the fixture's trace path did not exist. It
    does - `boot()` creates it - so the export ran and succeeded, which is
    correct behaviour and made the assertion wrong rather than the code.)
    """
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "nothing-here.db"))
    assert await runner.export_the_day(1) is None


def test_the_extension_is_loaded_before_it_is_installed() -> None:
    """`INSTALL` first meant a network call on every export."""
    source = (APP / "research" / "parquet_export.py").read_text(encoding="utf-8")
    body = source[source.index("duckdb.connect"):]
    load_at = body.index('con.execute("LOAD sqlite")')
    install_at = body.index('con.execute("INSTALL sqlite")')
    assert load_at < install_at, (
        "INFRA-10: INSTALL runs before LOAD, so every export reaches the "
        "network - and it failed here with HTTP 403"
    )


def test_the_litestream_unit_no_longer_says_it_cannot_start() -> None:
    unit = (APP / "infra" / "systemd" / "kasukabe-litestream.service").read_text(encoding="utf-8")
    config = re.search(r"litestream\.yml", unit)
    assert config, "the unit no longer references its config at all"
    assert "config file does not exist" not in unit, (
        "INFRA-10: the unit still warns that it cannot start until "
        "litestream.yml lands, and litestream.yml is in the repo"
    )
    assert (APP / "infra" / "litestream.yml").exists()
