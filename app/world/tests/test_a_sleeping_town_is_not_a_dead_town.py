"""CORE-32 must not stop a run because it is one in the morning.

The barren-tick halt exists for a real failure: a broker refusing every
call after its spend cap tripped (10,465 refusals over 44 minutes), and
thirty engines that died on a port collision while the world ticked on
above them. It counted a tick as barren whenever anyone was DUE and
nothing succeeded.

But `one()` returns before dispatching for three legitimate reasons - the
resident is dormant, is mid-way through a continuing action, or is ASLEEP
(CORE-17). At 1am every resident is asleep, so every night tick was
barren and the halt fired twenty ticks into the first night.

That is what stopped `deni-1c8733e-5h` at tick 152 (hour 01:00, day 1)
after fifty minutes in which all 4,580 HTTP calls returned 200 OK and not
one dispatch had failed. CORE-17's own docstring says "no run in 23 has
ever reached tick 144 - the budget ran out crossing a night nobody could
observe", so until night became cheap enough to REACH, this could never
fire. Two fixes, each correct alone.

The distinction is "nobody answered" versus "nobody was asked".
"""

from __future__ import annotations

import pytest

from world.core.clock import timestamp_for
from world.core.engine import TickResult
from world.run_genesis import GenesisRunner, GenesisRunnerConfig, TickOutcome

# 1am on day 1 - the tick the real run died on.
NIGHT_TICK = 152
NOON_TICK = 80


async def _town(tmp_path) -> GenesisRunner:
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000,
    ))
    await runner.boot()
    return runner


class DeadGateway:
    """Every dispatch fails - a tripped spend cap, or engines that died."""

    async def send_tick(self, agent_id, **kw) -> TickOutcome:
        return TickOutcome(
            agent_id=agent_id, reply="", learning_ran=False,
            is_sleeping=False, input_tokens=0, output_tokens=0,
            cost_usd_cents=0, succeeded=False, turn_in_flight=False,
        )

    def __getattr__(self, name):
        async def _any(*a, **k):
            return None
        return _any


def test_the_night_tick_really_is_night() -> None:
    """The premise. If the clock changes, this whole test is about nothing."""
    assert timestamp_for(NIGHT_TICK).is_daytime is False
    assert timestamp_for(NIGHT_TICK).hour == 1
    assert timestamp_for(NOON_TICK).is_daytime is True


@pytest.mark.asyncio
async def test_a_town_asleep_does_not_count_as_barren(tmp_path) -> None:
    runner = await _town(tmp_path)
    runner.gateway = DeadGateway()
    due = list(runner.world_state.agents)[:5]

    for _ in range(25):
        # `dispatch_due_agents` counts the tick itself - driving the
        # real path, not the counter in isolation, because the defect was
        # in which ticks reach the counter at all.
        await runner.dispatch_due_agents(
            TickResult(tick=timestamp_for(NIGHT_TICK), due_agents=due)
        )

    assert runner.barren_ticks == 0, (
        f"{runner.barren_ticks} barren ticks at 1am - the halt is about to "
        "stop a run because the town went to bed"
    )


@pytest.mark.asyncio
async def test_a_broker_that_answers_nothing_still_halts_the_run(tmp_path) -> None:
    """The case CORE-32 was built for must keep working. If this stops
    firing, a dead broker bills for hours of a town of statues.
    """
    runner = await _town(tmp_path)
    runner.gateway = DeadGateway()
    due = list(runner.world_state.agents)[:5]

    for _ in range(5):
        # `dispatch_due_agents` counts the tick itself - driving the
        # real path, not the counter in isolation, because the defect was
        # in which ticks reach the counter at all.
        await runner.dispatch_due_agents(
            TickResult(tick=timestamp_for(NOON_TICK), due_agents=due)
        )

    assert runner.barren_ticks == 5, (
        f"{runner.barren_ticks} barren ticks with every dispatch failing at "
        "noon - the halt has stopped noticing a dead broker"
    )
