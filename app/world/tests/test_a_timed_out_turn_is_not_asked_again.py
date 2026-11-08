"""One slow resident does not become a column of timeouts.

`INFRA-29`. When the runner's 120-second httpx timeout fires, the
engine's `/tick` handler KEEPS EXECUTING that turn - uvicorn does not
cancel a handler when its client disconnects - and the broker keeps
billing every call it makes. The next tick for that resident then blocks
on the engine's own `_turn_lock` behind the turn still running, times out
after another full 120 seconds, and the pattern repeats. Ten turns went
this way in run 23, after paying for every call.

WHAT THIS FIXES AND WHAT IT DOES NOT. It does not recover the money
already committed: cancelling the in-flight turn would have to reach
frozen engine code, and `engine/src/sim/` has no per-step hook to check a
deadline against. What it stops is the pile-up - the resident is left
alone until the turn it is actually running has had time to finish,
instead of being handed ticks that cannot run.
"""
from __future__ import annotations

import time

import pytest
import pytest_asyncio

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


@pytest_asyncio.fixture
async def runner(tmp_path):
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        tick_timeout_s=30.0,
    ))
    await runner.boot()
    return runner


def test_an_ordinary_resident_is_dispatched_to(runner) -> None:
    assert runner.may_dispatch_to("a0001")


def test_a_resident_whose_turn_timed_out_is_left_alone(runner) -> None:
    """The finding: this is the tick that would have blocked on the
    engine's turn lock and bought another whole timeout."""
    runner.dispatch_blocked_until["a0001"] = time.monotonic() + 60
    assert not runner.may_dispatch_to("a0001"), (
        "INFRA-29: a resident whose previous turn is still running is sent "
        "another tick, which serialises behind it and times out too"
    )


def test_the_block_expires(runner) -> None:
    """A resident must not be dropped from the run permanently because one
    turn ran long."""
    runner.dispatch_blocked_until["a0001"] = time.monotonic() - 1
    assert runner.may_dispatch_to("a0001")
    assert "a0001" not in runner.dispatch_blocked_until, (
        "the expired entry is left behind, so the dict grows for the life "
        "of the run"
    )


def test_only_the_slow_resident_is_held_back(runner) -> None:
    """The whole town must not stop because one engine is slow."""
    runner.dispatch_blocked_until["a0001"] = time.monotonic() + 60
    assert runner.may_dispatch_to("a0002")


def test_the_timeout_handler_actually_sets_the_block() -> None:
    """The tests above set `dispatch_blocked_until` themselves, so none of
    them notices if nothing ever populates it. This reads the one place
    that must: the `asyncio.TimeoutError` handler.

    An earlier version of this file searched the whole class for
    `tick_timeout_s`, found it somewhere unrelated, and stayed green while
    the assignment was removed - a test that cannot fail, which is the
    thing this project's own bar forbids.
    """
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1] / "run_genesis.py"
    ).read_text(encoding="utf-8")
    # Anchored on the handler's own log line, not on the first
    # `except asyncio.TimeoutError:` in the file - there are several, and
    # the first belongs to something else entirely. Getting that wrong is
    # how a test ends up reading a region that cannot contain what it is
    # looking for and reporting it as a defect.
    marker = 'self.timed_out_ticks += 1'
    handler = source[source.index(marker):]
    handler = handler[: handler.index(chr(10) + "            finally:")]

    assert "dispatch_blocked_until[agent_id]" in handler, (
        "INFRA-29: a turn times out and nothing records that the engine is "
        "still working on it, so the next tick blocks behind it and times "
        "out too"
    )
    assert "tick_timeout_s" in handler, (
        "the back-off is not derived from the timeout it responds to, so "
        "the two can drift apart"
    )


def test_the_skipped_turns_are_counted(runner) -> None:
    """`timed_out_ticks` was already counted "because 41 lines in a log
    nobody greps is not a signal". The same argument applies to a resident
    quietly sitting out."""
    assert runner.dispatch_skipped_ticks == 0
    assert hasattr(runner, "dispatch_skipped_ticks")


def test_the_count_reaches_the_metrics(runner) -> None:
    """An operator watching a run needs to see the engines falling behind
    the cadence - it is a pacing problem, and it looks like a broker one."""
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1] / "run_genesis.py"
    ).read_text(encoding="utf-8")
    assert '"dispatch_skipped_ticks": self.dispatch_skipped_ticks,' in source


def test_the_dispatch_loop_consults_it() -> None:
    """The mechanism existing is not the mechanism running - this review's
    most common defect, seven times over."""
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1] / "run_genesis.py"
    ).read_text(encoding="utf-8")
    assert "if self.may_dispatch_to(a)" in source, (
        "INFRA-29: the back-off is computed and the dispatch loop ignores "
        "it, so every timed-out resident is asked again immediately"
    )
