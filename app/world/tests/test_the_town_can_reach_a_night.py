"""CORE-17: a night must be cheap, and it must end in a morning.

No run in 23 has ever reached tick 144. A large part of why: night was
dispatched exactly like day. A perception was built for every resident on
every one of the 48 night ticks, handed to the gateway, and thrown away
there because `is_sleeping` short-circuits the turn. The whole night was
paid for in wall clock and in perception building, and nothing came of it.

The reviewer's acceptance: 48 night ticks complete fast with no dispatch
beyond the onset edges, and an interrupt raised at night is still honoured.
"""
from __future__ import annotations

import time

import pytest

from world.core.clock import timestamp_for
from world.run_genesis import (
    DAWN_TICK,
    NIGHT_ONSET_TICK,
    GenesisRunner,
    GenesisRunnerConfig,
)


def _runner(tmp_path, **kw) -> GenesisRunner:
    cfg = dict(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    )
    cfg.update(kw)
    return GenesisRunner(GenesisRunnerConfig(**cfg))


@pytest.mark.asyncio
async def test_a_sleeping_resident_is_not_dispatched(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    someone = next(iter(runner.world_state.agents))

    deep_night = timestamp_for(NIGHT_ONSET_TICK + 10)
    assert not deep_night.is_daytime, "the fixture is not actually at night"
    assert runner.sleeps_through(someone, deep_night) is True, (
        "a perception is still built for every resident on every one of the "
        "48 night ticks and thrown away inside the gateway (CORE-17)"
    )


@pytest.mark.asyncio
async def test_the_sleep_onset_edge_still_gets_one_turn(tmp_path):
    """One turn to finish up and go to bed, rather than freezing mid-act."""
    runner = _runner(tmp_path)
    await runner.boot()
    someone = next(iter(runner.world_state.agents))
    onset = timestamp_for(NIGHT_ONSET_TICK)
    assert not onset.is_daytime
    assert runner.sleeps_through(someone, onset) is False


@pytest.mark.asyncio
async def test_an_overnight_interrupt_still_wakes_them(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    someone = next(iter(runner.world_state.agents))
    runner.world_state.wake_reasons[someone] = "Wren knocked on your door"
    assert runner.sleeps_through(someone, timestamp_for(NIGHT_ONSET_TICK + 20)) is False, (
        "an interrupt raised overnight was silently dropped"
    )


@pytest.mark.asyncio
async def test_daytime_is_never_slept_through(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    someone = next(iter(runner.world_state.agents))
    assert timestamp_for(DAWN_TICK).is_daytime
    assert runner.sleeps_through(someone, timestamp_for(DAWN_TICK)) is False


@pytest.mark.asyncio
async def test_a_quiet_night_is_not_paced(tmp_path):
    """The wall-clock half. 48 paced ticks at 3s is 2.4 minutes of watching
    an empty town; bursting them is the difference between a run that
    reaches tomorrow morning and one that does not."""
    runner = _runner(tmp_path, tick_interval_s=0.2)
    await runner.boot()
    runner.world_state.current_tick = NIGHT_ONSET_TICK + 5

    started = time.monotonic()
    for _ in range(10):
        await runner.wait_out_the_tick(time.monotonic())
    elapsed = time.monotonic() - started
    assert elapsed < 0.5, (
        f"10 quiet night ticks took {elapsed:.2f}s at a 0.2s interval - the "
        "night is still being paced as though somebody were awake"
    )


@pytest.mark.asyncio
async def test_a_night_with_somebody_awake_is_still_paced(tmp_path):
    """The predicate reads the world, not a flag: if an interrupt woke
    somebody, that resident is dispatched and the broker needs its room."""
    runner = _runner(tmp_path, tick_interval_s=0.2)
    await runner.boot()
    runner.world_state.current_tick = NIGHT_ONSET_TICK + 5
    someone = next(iter(runner.world_state.agents))
    runner.world_state.wake_reasons[someone] = "a shout outside"

    started = time.monotonic()
    await runner.wait_out_the_tick(time.monotonic())
    assert time.monotonic() - started >= 0.15, (
        "somebody is awake and being dispatched, but the tick did not pace"
    )


@pytest.mark.asyncio
async def test_daytime_is_still_paced(tmp_path):
    runner = _runner(tmp_path, tick_interval_s=0.2)
    await runner.boot()
    runner.world_state.current_tick = DAWN_TICK + 10
    started = time.monotonic()
    await runner.wait_out_the_tick(time.monotonic())
    assert time.monotonic() - started >= 0.15


def test_the_dispatch_path_actually_consults_the_sleep_rule():
    """Third time this pattern has bitten in one session, so it is checked
    every time now: a predicate nothing calls is not a fix."""
    import ast
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(GenesisRunner.dispatch_due_agents))
    called = {
        node.func.attr
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "sleeps_through" in called, (
        "dispatch_due_agents never asks whether the resident is asleep, so "
        "a perception is still built for all 48 night ticks (CORE-17)"
    )
    assert "is_dormant" in called, "the INFRA-03 skip went missing too"
