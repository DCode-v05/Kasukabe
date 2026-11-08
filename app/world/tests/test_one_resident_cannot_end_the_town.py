"""One resident's bad state must not end the run.

On 2026-09-08 the 50-agent series died eight minutes in. A resident had
committed to work "until dusk"; `ContinuingAction.satisfied` called a
`world.is_daytime()` that did not exist; the AttributeError came out of
`asyncio.gather` in `dispatch_due_agents`, out of the tick, and out of the
runner. All fifty engines stopped. The broker went on billing at
$10.77/hour for a town nobody was simulating any more.

The missing method is fixed separately
(`world/tools/tests/test_working_until_dusk_does_not_end_the_world.py`).
This is the other half: the blast radius. A per-resident failure is now
one lost turn, counted and logged, which is the same rule the day
boundary already follows - "one household must not take the day down for
the other sixteen".

Counted, NOT swallowed: a turn that vanished silently would read as a
resident choosing to do nothing, and "did anything happen last turn" is
the measurement this whole experiment exists to make.
"""

from __future__ import annotations

import pytest

from world.core.clock import timestamp_for
from world.core.engine import TickResult
from world.run_genesis import GenesisRunner, GenesisRunnerConfig, TickOutcome
from world.tools.state import ContinuingAction


class QuietGateway:
    """A turn that succeeds and costs nothing, so this measures the
    dispatch loop rather than the broker."""

    async def send_tick(self, agent_id, **kw) -> TickOutcome:
        return TickOutcome(
            agent_id=agent_id, reply="", learning_ran=False,
            is_sleeping=False, input_tokens=0, output_tokens=0,
            cost_usd_cents=0, succeeded=True, turn_in_flight=False,
        )

    def __getattr__(self, name):
        async def _any(*a, **k):
            return None
        return _any


async def _town(tmp_path) -> GenesisRunner:
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000,
    ))
    await runner.boot()
    return runner


@pytest.mark.asyncio
async def test_a_broken_resident_costs_one_turn_not_the_run(tmp_path) -> None:
    runner = await _town(tmp_path)
    runner.gateway = QuietGateway()
    world = runner.world_state
    victim = next(iter(world.agents))

    world.continuing[victim] = ContinuingAction(
        tool="harvest", params={"item": "wheat"}, until={"dusk": True},
        started_tick=0, location_id="sector:farm",
    )
    original = type(world).is_daytime

    def explode(self):
        raise AttributeError("'WorldState' object has no attribute 'is_daytime'")

    type(world).is_daytime = explode
    try:
        await runner.dispatch_due_agents(
            TickResult(tick=timestamp_for(60), due_agents=[victim])
        )
    finally:
        type(world).is_daytime = original

    assert runner.turns_lost_to_errors == 1, (
        "the failure was swallowed - a lost turn that nothing counts reads "
        "as a resident who chose to do nothing"
    )


@pytest.mark.asyncio
async def test_the_other_residents_still_take_their_turn(tmp_path) -> None:
    """The point of the fix. One broken resident, everybody else fine."""
    runner = await _town(tmp_path)
    runner.gateway = QuietGateway()
    world = runner.world_state
    ids = list(world.agents)[:4]
    victim = ids[0]

    world.continuing[victim] = ContinuingAction(
        tool="harvest", params={"item": "wheat"}, until={"dusk": True},
        started_tick=0, location_id="sector:farm",
    )
    original = type(world).is_daytime

    def explode(self):
        raise AttributeError("boom")

    type(world).is_daytime = explode
    try:
        await runner.dispatch_due_agents(
            TickResult(tick=timestamp_for(60), due_agents=ids)
        )
    finally:
        type(world).is_daytime = original

    assert runner.turns_lost_to_errors == 1, (
        f"{runner.turns_lost_to_errors} turns lost - one resident's bad "
        "state is still spreading to the others"
    )


@pytest.mark.asyncio
async def test_a_healthy_town_loses_no_turns(tmp_path) -> None:
    """The counter must mean something. If it ticks up on a clean run it
    is noise, and the next reader will learn to ignore it.
    """
    runner = await _town(tmp_path)
    runner.gateway = QuietGateway()
    due = list(runner.world_state.agents)[:5]

    await runner.dispatch_due_agents(
        TickResult(tick=timestamp_for(60), due_agents=due)
    )

    assert runner.turns_lost_to_errors == 0
