"""A dead resident comes off the schedule.

`CORE-10`. `AgentScheduler.unregister_agent` had **no production caller**.
Its own docstring says the round-robin design "exists to avoid" repeatedly
serving the same agents and tells the caller to "call unregister_agent()
first" - and nothing ever did, so a dead resident stayed in `entries` for
the rest of the run.

The dispatch skip added for INFRA-03 stops the wasted turn, so this is not
about spend. It is about arithmetic: the scheduler's `entries` is what
`due_per_tick` counts, and `due_per_tick` is what CORE-12's pacing divides
the tick interval by. A town of twenty that has buried five was still being
paced, and reported, as a town of twenty.
"""
from __future__ import annotations

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


@pytest.mark.asyncio
async def test_death_removes_the_agent_from_the_scheduler(tmp_path):
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    ))
    await runner.boot()

    scheduler = runner.tick_engine.scheduler
    victim = sorted(runner.world_state.agents)[0]
    assert victim in scheduler.entries, "the fixture never scheduled anybody"

    agent = runner.world_state.agents[victim]
    await runner.apply_death(agent, "terminal")

    assert victim not in scheduler.entries, (
        "CORE-10: the dead stay on the roster, so due_per_tick counts them "
        "and the pacing arithmetic divides by a population that includes "
        "the buried"
    )


@pytest.mark.asyncio
async def test_the_living_stay_on_it(tmp_path):
    """A guard that empties the roster would be worse than the bug."""
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    ))
    await runner.boot()

    scheduler = runner.tick_engine.scheduler
    everyone = sorted(runner.world_state.agents)
    victim, *survivors = everyone
    await runner.apply_death(runner.world_state.agents[victim], "terminal")

    for survivor in survivors:
        assert survivor in scheduler.entries
