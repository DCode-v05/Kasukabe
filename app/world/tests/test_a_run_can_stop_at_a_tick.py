"""A run can stop at a tick, not only at a midnight.

`--max-game-days` stops a run when the day number reaches the bound - so
only ever at 00:00. The first election is counted AT 00:00 (tick 432), so a
run bounded to three days ended on the very tick of the count and the world
went away before anybody saw the result. Found in the UI rehearsal of
2026-09-22: the last frame of the recording still read "Voting · 13 ballots".

`--max-ticks` stops after a given tick. The election run uses 590: past the count
at 432 and past the new government's first day boundary at 576, where a
changed tax is first charged and its wages first paid.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig, build_arg_parser


async def _launched(tmp_path: Path, **overrides) -> GenesisRunner:
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp", home_root=str(tmp_path / "homes"),
        world_mode="embedded", world_port=0, start_at_dawn=True, **overrides,
    ))
    await runner.boot(resume=False)
    await runner.start_agents()
    return runner


@pytest.mark.asyncio
async def test_the_run_stops_on_the_tick_it_was_given(tmp_path) -> None:
    runner = await _launched(tmp_path, max_ticks=40, max_game_days=1000)

    await runner.run_continuous()

    assert runner.world_state.tick() == 40
    await runner.shutdown()


@pytest.mark.asyncio
async def test_without_it_nothing_changes(tmp_path) -> None:
    runner = await _launched(tmp_path, max_game_days=1)

    await runner.run_continuous()

    assert runner.world_state.tick() == 144, "max_game_days alone still stops at midnight"
    await runner.shutdown()


def test_the_command_line_takes_it() -> None:
    args = build_arg_parser().parse_args(["--max-ticks", "480"])
    assert args.max_ticks == 480


def test_the_election_run_outlives_the_count_and_the_next_day_boundary() -> None:
    """The count is at tick 432; the new government's first day boundary -
    where a changed tax is first charged and its wages paid - is at 576."""
    import re as _re

    launcher = (Path(__file__).resolve().parents[2] / "run-election.sh").read_text(encoding="utf-8")
    assert '--max-ticks "$STOP_TICK"' in launcher
    default = int(_re.search(r'STOP_TICK="\$\{STOP_TICK:-(\d+)\}"', launcher).group(1))
    assert default > 576, f"the run stops at {default}, before the day-3 boundary at 576"
