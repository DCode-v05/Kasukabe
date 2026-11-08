"""A town booted at dawn thought it was midnight until its first tick.

`start_at_dawn` builds the tick engine's clock at DAWN_TICK (36, 06:00), and
the fresh-genesis path never told the world state: `world_state.current_tick`
stayed 0, so `world.tick()` answered 0 while the clock answered 36. The resume
path sets both; only this one forgot.

Everything done between the end of boot and the first tick was stamped with
the wrong time - an operator's action, a transaction, an event, the reading a
run takes if it is stopped straight away. Found by a soak that checks the
clock advances by exactly one each tick: the first tick of a dawn start went
0 -> 37.
"""

from __future__ import annotations

import pytest

from world.run_genesis import DAWN_TICK, GenesisRunner, GenesisRunnerConfig, TickOutcome


class _Quiet:
    """A gateway that answers every tick and does nothing, so `run_tick`
    can be driven without engines."""

    async def send_tick(self, agent_id, *, tick, **kw) -> TickOutcome:
        return TickOutcome(agent_id=agent_id, reply="", learning_ran=False, is_sleeping=False,
                           input_tokens=0, output_tokens=0, cost_usd_cents=0, succeeded=True)

    def __getattr__(self, name):
        async def _any(*a, **k):
            return None
        return _any


def _runner(tmp_path, **over) -> GenesisRunner:
    return GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp", home_root=str(tmp_path / "h"),
        world_mode="embedded", world_port=0, **over,
    ))


@pytest.mark.asyncio
async def test_a_town_booted_at_dawn_knows_it_is_dawn(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "t.db"))
    runner = _runner(tmp_path, start_at_dawn=True)

    await runner.boot(resume=False)

    assert runner.tick_engine.clock.tick_index == DAWN_TICK
    assert runner.world_state.current_tick == DAWN_TICK
    assert runner.world_state.tick() == DAWN_TICK


@pytest.mark.asyncio
async def test_its_first_tick_is_the_next_one(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "t.db"))
    runner = _runner(tmp_path, start_at_dawn=True)
    await runner.boot(resume=False)
    runner.gateway = _Quiet()
    before = runner.world_state.current_tick

    await runner.run_tick()

    assert runner.world_state.current_tick == before + 1


@pytest.mark.asyncio
async def test_a_town_booted_at_midnight_is_unchanged(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "t.db"))
    runner = _runner(tmp_path)

    await runner.boot(resume=False)

    assert runner.tick_engine.clock.tick_index == 0 == runner.world_state.current_tick
