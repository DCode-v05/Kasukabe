"""INFRA-03: a resident with no money must not be asked to think.

Dormancy means the wallet hit zero. The broker then refuses every call, so
the turn ends in a refusal and the resident is asked again next tick.
Build Spec 4.5 allows one refusal per broke resident. What ran instead was
one every third tick for the rest of the run - each paying for a perception
build and a dispatch to learn a fact the world already knew.

These test the predicate and the counter. Both fail if `is_dormant` stops
being consulted before the perception is built.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


def _runner(tmp_path) -> GenesisRunner:
    return GenesisRunner(
        GenesisRunnerConfig(
            mock_mode=True,
            checkpoint_dir=tmp_path / "checkpoints",
            world_mode="embedded",
            world_port=0,
            budget_cap_cents=40_000,
        )
    )


@pytest.mark.asyncio
async def test_a_dormant_resident_reads_as_dormant(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    world = runner.world_state

    ids = list(world.agents)[:2]
    assert len(ids) == 2, "need two seeded residents for this test to mean anything"
    broke, solvent = ids

    world.agents[broke].dormancy.is_dormant = True

    assert runner.is_dormant(broke) is True
    assert runner.is_dormant(solvent) is False, (
        "a solvent resident must still be dispatched, or the town stops thinking"
    )


@pytest.mark.asyncio
async def test_a_dead_resident_is_never_dispatched_either(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    world = runner.world_state
    someone = next(iter(world.agents))
    world.agents[someone].alive = False
    assert runner.is_dormant(someone) is True


@pytest.mark.asyncio
async def test_an_unknown_id_is_not_treated_as_dormant(tmp_path):
    """Fail open, not closed: an id the world does not know must not
    silently stop being dispatched - that would be a town that goes quiet
    for a reason nobody can see."""
    runner = _runner(tmp_path)
    await runner.boot()
    assert runner.is_dormant("no-such-resident") is False


@pytest.mark.asyncio
async def test_the_skip_is_counted(tmp_path):
    runner = _runner(tmp_path)
    assert runner.dormant_skips == 0, (
        "the counter must exist before boot, so a run that skips everybody "
        "can say so rather than looking like a quiet town"
    )


@pytest.mark.asyncio
async def test_dispatch_skips_the_dormant_before_building_a_perception(tmp_path):
    """The one that matters: does the DISPATCH PATH consult the predicate?

    A predicate nothing calls is the exact shape of the bugs this review
    found. This drives `dispatch_due_agents` for real, with one dormant
    resident and one solvent one, and asserts the dormant one never reached
    the gateway.
    """
    from world.core.clock import timestamp_for
    from world.core.engine import TickResult

    runner = _runner(tmp_path)
    await runner.boot()
    world = runner.world_state

    ids = list(world.agents)[:2]
    broke, solvent = ids
    world.agents[broke].dormancy.is_dormant = True

    asked: list[str] = []

    class Outcome:
        succeeded = True
        error = None
        cost_cents = 0
        def __getattr__(self, name):
            return None

    class SpyGateway:
        def __getattr__(self, name):
            async def _any(*a, **k):
                if a and isinstance(a[0], str):
                    asked.append(a[0])
                return Outcome()
            return _any

    runner.gateway = SpyGateway()

    result = TickResult(tick=timestamp_for(60), due_agents=[broke, solvent])
    await runner.dispatch_due_agents(result)

    assert broke not in asked, (
        f"the dormant resident {broke} was dispatched anyway - it will be "
        "refused by the broker and asked again next tick, forever (INFRA-03)"
    )
    assert runner.dormant_skips >= 1, "the skip happened but was not counted"
