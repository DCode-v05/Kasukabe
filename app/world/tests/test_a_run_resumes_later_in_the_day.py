"""A resumed run can pick the day up later than the checkpoint left it.

Run 28 stopped at 11:30 on day 0, its $10 spent on one morning. The
operator's next question was the evening: what twenty residents do when
the light goes, and what the first midnight - upkeep, head tax, hunger -
does to a town that never reached one.

`--start-at-dawn` answers "where does a FRESH town begin". Nothing
answered "where does a RESUMED town pick up": a resume restored the
checkpoint's tick and that was the only choice. `--resume-at "0 18:00"`
restores the checkpoint and then moves the clock forward to that moment.

What moving the clock does and does not do, and why each is safe:

  * The clock is one integer (`world/core/clock.py`: "A checkpoint only
    needs to persist tick_index"), so the jump is one assignment.
  * Hourly life jobs catch up in one step by elapsed hours (aging, the
    dormancy check) - proportional, never repeated.
  * Daily jobs fire on a change of game day, and 11:30 to 18:00 is the same
    day, so no upkeep or head tax is charged for the skipped hours.
  * Every resident whose next decision is in the past is due on the next
    tick - they all wake into the evening together.

Two refusals, both loud. Time does not run backwards - a resume-at earlier
than the checkpoint would replay ticks the ledger has already charged. And
a resume-at with no checkpoint to resume is an operator who believes they
are continuing a run and would be starting a fresh town.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from world.core.clock import timestamp_for
from world.ledger.core import BURNED_ACCOUNT_ID, TREASURY_ACCOUNT_ID, TOTAL_USD_CENTS
from world.run_genesis import GenesisRunner, GenesisRunnerConfig, build_arg_parser, config_from_args

EVENING = 108  # day 0, 18:00


def _runner(tmp_path: Path, **overrides: object) -> GenesisRunner:
    kwargs: dict[str, object] = dict(
        mock_mode=True, checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded", world_port=0, budget_cap_cents=40_000,
    )
    kwargs.update(overrides)
    return GenesisRunner(GenesisRunnerConfig(**kwargs))


async def _a_morning_on_disk(tmp_path: Path) -> int:
    """Twenty ticks of a real (mock-mode) run, stopped - a checkpoint on disk."""
    first = _runner(tmp_path)
    await first.boot(resume=False)
    await first.start_agents()
    await first.run_burst(20)
    day_counter = first.counters.last_life_day_processed
    await first.shutdown()
    return day_counter


@pytest.mark.asyncio
async def test_the_clock_picks_up_in_the_evening(tmp_path):
    await _a_morning_on_disk(tmp_path)

    second = _runner(tmp_path, resume_at_tick=EVENING)
    await second.boot(resume=True)

    assert second.world_state.current_tick == EVENING
    assert second.tick_engine.clock.tick_index == EVENING
    now = second.tick_engine.clock.now()
    assert (now.game_day, now.hour, now.minute) == (0, 18, 0)
    await second.shutdown()


@pytest.mark.asyncio
async def test_the_skipped_hours_charge_no_day(tmp_path):
    """Same game day, so the daily jobs have nothing to catch up, and the
    $1,000 ledger is exactly what the checkpoint left."""
    day_counter = await _a_morning_on_disk(tmp_path)

    second = _runner(tmp_path, resume_at_tick=EVENING)
    await second.boot(resume=True)

    assert second.counters.last_life_day_processed == day_counter
    ledger = second.ledger
    wallets = sum(ledger.usd_balance(a) for a in second.world_state.agents)
    wallets += sum(ledger.usd_balance(b) for b in second.world_state.businesses)
    assert wallets + ledger.usd_balance(TREASURY_ACCOUNT_ID) + ledger.usd_balance(BURNED_ACCOUNT_ID) == TOTAL_USD_CENTS
    await second.shutdown()


@pytest.mark.asyncio
async def test_time_does_not_run_backwards(tmp_path):
    await _a_morning_on_disk(tmp_path)

    second = _runner(tmp_path, resume_at_tick=10)
    with pytest.raises(ValueError) as refused:
        await second.boot(resume=True)

    assert "20" in str(refused.value) and "10" in str(refused.value), str(refused.value)


@pytest.mark.asyncio
async def test_there_must_be_something_to_resume(tmp_path):
    """Asked to continue a run, with no run on disk: a fresh town would be
    the wrong answer delivered silently."""
    runner = _runner(tmp_path, resume_at_tick=EVENING)

    with pytest.raises(ValueError) as refused:
        await runner.boot(resume=True)

    assert "checkpoint" in str(refused.value)


def test_the_command_line_reads_a_day_and_a_time():
    parser = build_arg_parser()

    evening = config_from_args(parser.parse_args(["--resume-at", "0 18:00"]))
    next_dawn = config_from_args(parser.parse_args(["--resume-at", "1 06:00"]))

    assert evening.resume_at_tick == EVENING
    assert next_dawn.resume_at_tick == 180
    assert timestamp_for(evening.resume_at_tick).hour == 18


@pytest.mark.parametrize("bad", ["18:00", "0 25:00", "0 18:05", "-1 06:00", "evening"])
def test_a_time_the_clock_cannot_land_on_is_refused(bad, capsys):
    """Ticks are ten minutes long, so 18:05 is not a moment the town has.

    The message is asserted, not just the exit: argparse also exits 2 for a
    flag it has never heard of, which is how these first "passed" before
    `--resume-at` existed."""
    parser = build_arg_parser()

    with pytest.raises(SystemExit):
        config_from_args(parser.parse_args(["--resume-at", bad]))

    assert "ten-minute" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_the_manifest_says_the_run_was_resumed_later(tmp_path, monkeypatch):
    from world.tests.map_assets import needs_the_map  # noqa: F401 - same host needs as the boot

    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    await _a_morning_on_disk(tmp_path)

    second = _runner(tmp_path, resume_at_tick=EVENING)
    await second.boot(resume=True)
    path = second.write_the_run_manifest()

    assert path is not None
    assert json.loads(path.read_text())["resume_at_tick"] == EVENING
    await second.shutdown()
