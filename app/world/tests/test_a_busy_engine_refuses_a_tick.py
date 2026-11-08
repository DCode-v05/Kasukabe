"""A tick that arrives mid-turn is refused, not queued behind it.

`CORE-11`. `asyncio.wait_for` cancels the coroutine that is AWAITING the
response; it does not reach into the engine process and stop the turn.
uvicorn does not cancel a handler when its client disconnects either. So
after a dispatch timed out, the engine kept running that turn and kept
being billed for every model call it made, while the runner marked the
resident decided and moved on.

Three ticks later the runner POSTed again. That request blocked on the
engine's own `_turn_lock` until the first turn finished, and then ran a
turn against a perception several ticks stale. Money spent, actions taken
late, `timed_out_ticks` incremented with no way to attribute the late
tool calls to anything. Run 23: ten turns went this way.

The review's fix, and the reason this was actionable after I had filed it
as blocked twice: "the engine answers 409 if a tick arrives while one is
running (seam change in `agent_adapter.py`, allowed under Law 1)".
`agent_adapter.py` and `pinned_boot.py` are `engine/src/sim/` - the seam
Law 1 leaves writable. `may_dispatch_to`'s docstring asserted the
opposite, and that sentence is why this sat closed.

The runner's INFRA-29 backoff is not replaced by this. It stays as the
cheap pre-filter that skips a resident without making the call at all;
the 409 is the authority for the cases the timer gets wrong.

The test the review asks for is the last one here: "a gateway that sleeps
2x the timeout must not receive a second /tick while the first is
pending".
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from world.run_genesis import (
    GenesisRunner,
    GenesisRunnerConfig,
    TickOutcome,
)


def _runner(tmp_path, timeout_s: float = 0.2) -> GenesisRunner:
    return GenesisRunner(
        GenesisRunnerConfig(
            mock_mode=True,
            checkpoint_dir=tmp_path / "checkpoints",
            world_mode="embedded",
            world_port=0,
            budget_cap_cents=40_000,
            tick_timeout_s=timeout_s,
        )
    )


SEAM = Path(__file__).resolve().parents[3] / "engine" / "src" / "sim"


def test_the_engine_can_say_whether_it_is_busy() -> None:
    """`turn_in_flight` reads the lock rather than a flag we maintain: a
    flag has a window between the lock being taken and the flag being
    written, and a request arriving in a window is the entire finding."""
    source = (SEAM / "pinned_boot.py").read_text(encoding="utf-8")
    assert "def turn_in_flight" in source, (
        "CORE-11: the engine cannot say whether a turn is running, so the "
        "runner is back to guessing from a timer"
    )
    assert "_turn_lock.locked()" in source


def test_the_tick_route_refuses_with_409() -> None:
    """A 409, not a 429: this is not a rate limit to retry on a timer, it
    is a statement that the resource is in a conflicting state now."""
    source = (SEAM / "agent_adapter.py").read_text(encoding="utf-8")
    assert "engine.turn_in_flight" in source, (
        "CORE-11: /tick still queues on _turn_lock, so a second tick runs "
        "against a perception several ticks stale"
    )
    assert "status_code=409" in source


def test_the_outcome_distinguishes_refused_from_failed() -> None:
    """A refusal costs nothing and means nothing is broken. Counting it as
    an engine failure would make a healthy busy town look like a failing
    one - and counting it as a success would book a turn that never ran."""
    refused = TickOutcome(
        agent_id="a1", reply="", learning_ran=False, is_sleeping=False,
        input_tokens=0, output_tokens=0, cost_usd_cents=0,
        succeeded=False, turn_in_flight=True,
    )
    assert refused.turn_in_flight is True
    assert refused.succeeded is False

    ordinary_failure = TickOutcome(
        agent_id="a1", reply="", learning_ran=False, is_sleeping=False,
        input_tokens=0, output_tokens=0, cost_usd_cents=0, succeeded=False,
    )
    assert ordinary_failure.turn_in_flight is False, (
        "CORE-11: the default must be False, or every engine failure reads "
        "as a busy engine and no failure is ever reported again"
    )


@pytest.mark.asyncio
async def test_a_refusal_is_counted_as_a_skip_not_a_failure(tmp_path) -> None:
    runner = _runner(tmp_path)
    await runner.boot()

    from world.core.clock import timestamp_for
    from world.core.engine import TickResult

    someone = next(iter(runner.world_state.agents))

    class RefusingGateway:
        calls = 0

        async def send_tick(self, agent_id, **kw) -> TickOutcome:
            RefusingGateway.calls += 1
            return TickOutcome(
                agent_id=agent_id, reply="", learning_ran=False,
                is_sleeping=False, input_tokens=0, output_tokens=0,
                cost_usd_cents=0, succeeded=False, turn_in_flight=True,
            )

        def __getattr__(self, name):
            async def _any(*a, **k):
                return None
            return _any

    runner.gateway = RefusingGateway()
    before_skips = runner.dispatch_skipped_ticks
    before_timeouts = runner.timed_out_ticks

    await runner.dispatch_due_agents(
        TickResult(tick=timestamp_for(60), due_agents=[someone])
    )

    assert RefusingGateway.calls == 1
    assert runner.dispatch_skipped_ticks == before_skips + 1, (
        "CORE-11: a refused tick is not counted anywhere, so a town whose "
        "engines are all busy looks like a town that is thinking"
    )
    assert runner.timed_out_ticks == before_timeouts, (
        "CORE-11: a refusal was counted as a timeout - it is the opposite, "
        "an answer that arrived in milliseconds"
    )


@pytest.mark.asyncio
async def test_a_slow_gateway_gets_no_second_tick_while_the_first_pends(
    tmp_path,
) -> None:
    """The review's acceptance, verbatim: "a gateway that sleeps 2x the
    timeout must not receive a second /tick while the first is pending".

    This drives the real `dispatch_due_agents` twice, on consecutive
    ticks, with a gateway whose first call outlives the timeout. Before
    the fix the second dispatch reached the gateway and blocked; the
    engine then ran it against a stale perception.
    """
    from world.core.clock import timestamp_for
    from world.core.engine import TickResult

    runner = _runner(tmp_path, timeout_s=0.2)
    await runner.boot()
    someone = next(iter(runner.world_state.agents))

    started: list[int] = []
    release = asyncio.Event()

    class SlowGateway:
        async def send_tick(self, agent_id, *, tick, **kw) -> TickOutcome:
            started.append(tick)
            if len(started) == 1:
                await asyncio.wait_for(release.wait(), timeout=5.0)
            return TickOutcome(
                agent_id=agent_id, reply="ok", learning_ran=False,
                is_sleeping=False, input_tokens=0, output_tokens=0,
                cost_usd_cents=0, succeeded=True,
            )

        def __getattr__(self, name):
            async def _any(*a, **k):
                return None
            return _any

    runner.gateway = SlowGateway()

    await runner.dispatch_due_agents(
        TickResult(tick=timestamp_for(60), due_agents=[someone])
    )
    assert started == [60], "the first tick must have been sent"
    assert runner.timed_out_ticks >= 1

    await runner.dispatch_due_agents(
        TickResult(tick=timestamp_for(63), due_agents=[someone])
    )

    release.set()
    assert started == [60], (
        "CORE-11: a second /tick was sent while the first was still "
        f"pending (sent {started}). It queues on the engine's _turn_lock "
        "and, when it finally runs, runs against a perception three ticks "
        "stale - having paid for both."
    )
