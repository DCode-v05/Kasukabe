"""A resident's turn is not one model call, and the pacing has to know.

`CORE-12` / `INFRA-12`. `resolve_tick_interval` computed
`due_per_tick * 60 / capacity` - one request per due resident. A turn makes
up to `MAX_CALLS_PER_TICK` model calls plus a post-turn summariser; run 23
measured 2 to 12.

So at 250 residents and a 0.3 cadence the real demand is about 83 turns x
~5 calls = 415 requests a tick against 600 RPM, and every interval this
runner computed was out by that factor. The town was paced to outrun its
own broker, and the resulting queueing read as Bedrock being slow.

Measured rather than assumed: the engines report `steps_used` on every
`/tick` response and nothing was reading it, so a town of quiet residents
is not paced as though everybody thinks twelve times.
"""
from __future__ import annotations

import pytest

from world.run_genesis import (
    DEFAULT_CALLS_PER_TURN,
    GenesisRunner,
    GenesisRunnerConfig,
)


def _runner(tmp_path) -> GenesisRunner:
    return GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    ))


def test_before_any_turn_it_assumes_a_whole_turn(tmp_path):
    """Never 1.0 - that is the bug. The fallback is conservative because
    pacing too fast costs a queue that looks like the broker failing."""
    runner = _runner(tmp_path)
    assert runner.calls_per_turn_estimate() == DEFAULT_CALLS_PER_TURN
    assert runner.calls_per_turn_estimate() > 1.0


def test_it_averages_what_the_engines_reported(tmp_path):
    runner = _runner(tmp_path)
    runner.recent_steps_used.extend([2, 4, 6])
    assert runner.calls_per_turn_estimate() == pytest.approx(4.0 + 1.0)


def test_a_quiet_town_is_not_paced_like_a_busy_one(tmp_path):
    """The reason to measure rather than use a constant."""
    quiet = _runner(tmp_path)
    quiet.recent_steps_used.extend([1, 1, 1])
    busy = _runner(tmp_path)
    busy.recent_steps_used.extend([11, 12, 12])
    assert quiet.calls_per_turn_estimate() < busy.calls_per_turn_estimate()


def test_the_sample_stays_bounded(tmp_path):
    """Pacing wants a recent average, not a history of the run."""
    runner = _runner(tmp_path)
    runner.recent_steps_used.extend(range(10_000))
    assert len(runner.recent_steps_used) <= 200
