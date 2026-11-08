"""A run that is killed captures nothing, and killed is how runs end.

`capture_final_reading` asks the world for the three things no table holds -
the roster, the refused intentions, the tools being served - and writes them
beside the checkpoints. It runs in `shutdown()`, which is exactly the path a
run does not take when it is killed: Ctrl-C at the wrong moment, an
out-of-memory kill, a closed laptop, a machine that goes to sleep. The
50-resident run is the case in point: its report says "The world reported no
roster" about fifty residents and "Refusals ... are not recorded here",
because by the time anybody asked, nobody was there to answer.

So the reading is taken WHILE the run is going as well - by default once a
game-day, which is 144 ticks and, at run 29's pace, about 2¼ hours of
wall-clock. The cost is nine local route reads against the world's own ASGI
app; the benefit is that a run which dies at tick 250 still has a roster
from tick 144 instead of nothing at all.

The newest reading wins, because a later one describes a town that has
lived longer. `KASUKABE_READING_EVERY_TICKS=0` switches it off, and the
manifest records what it was set to, because a run that captured itself
hourly and one that never did are different experiments.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


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
    runner.build_world_app()
    return runner, tmp_path


def _reading(tmp_path) -> dict:
    return json.loads((tmp_path / "checkpoints" / "final-reading.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_a_reading_is_taken_while_the_run_is_going(booted) -> None:
    runner, tmp_path = booted

    await runner.capture_reading_if_due(144)

    assert (tmp_path / "checkpoints" / "final-reading.json").is_file(), (
        "the run passed a whole game-day and still nothing would survive a kill"
    )
    assert _reading(tmp_path)["tick"] == 144


@pytest.mark.asyncio
async def test_it_is_not_taken_on_every_tick(booted) -> None:
    """Nine route reads per tick would be a tax on every tick of every run,
    to re-record a town that has barely moved."""
    runner, tmp_path = booted

    for tick in range(1, 144):
        await runner.capture_reading_if_due(tick)

    assert not (tmp_path / "checkpoints" / "final-reading.json").exists()


@pytest.mark.asyncio
async def test_the_newer_reading_wins(booted) -> None:
    """A later reading describes a town that has lived longer."""
    runner, tmp_path = booted

    await runner.capture_reading_if_due(144)
    await runner.capture_reading_if_due(288)

    assert _reading(tmp_path)["tick"] == 288


@pytest.mark.asyncio
async def test_it_can_be_switched_off(booted, monkeypatch) -> None:
    import world.run_genesis as rg

    monkeypatch.setattr(rg, "READING_EVERY_TICKS", 0)
    runner, tmp_path = booted

    await runner.capture_reading_if_due(144)

    assert not (tmp_path / "checkpoints" / "final-reading.json").exists()


@pytest.mark.asyncio
async def test_a_failed_reading_does_not_stop_the_tick(booted, monkeypatch) -> None:
    """Describing a run must never be able to end it - the same rule the
    stop path follows."""
    import world.run_genesis as rg

    async def boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(rg, "capture_final_reading", boom)
    runner, _ = booted

    await runner.capture_reading_if_due(144)      # must not raise


@pytest.mark.asyncio
async def test_the_tick_loop_is_the_caller(booted) -> None:
    """The mechanism needs a caller in the loop, or it only ever runs in a
    test. Run 26's report described run 25's cast for want of exactly this
    kind of wiring."""
    import inspect

    runner, _ = booted
    assert "capture_reading_if_due" in inspect.getsource(runner.run_tick)


def test_how_often_a_run_read_itself_is_recorded() -> None:
    from world.run_manifest import RECORDED_ENV

    assert "KASUKABE_READING_EVERY_TICKS" in RECORDED_ENV
