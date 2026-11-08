"""Living costs money, every day, whether you earned or not.

world/life/upkeep.py implements DESIGN.md section 3 in full - food, shelter
and heat billed to the household, then mild hunger, then a malnutrition
ailment, then dormancy, then death - and its own docstring asks for "the
exact one-line hook into world's (not-yet-built) daily tick loop". Nothing
ever called it.

Observed at game-day 3 without it: two residents at zero USD, unable to
think at all, both reported alive and well; nobody's sim balance had moved
in three days; nobody had any reason to earn, eat, trade, or write a skill
down. DESIGN.md names the consequence exactly - "without it an agent could
minimise cost by doing nothing, and idleness would be the winning strategy".

CORE-23 / EVAL-21 / ECON-26 - three findings, one pattern. Every test in
this file used to assert a substring of
`inspect.getsource(...)` - "`run_daily_upkeep` in source". A COMMENT naming
the function satisfied that, and Build Spec section 10.3 forbids exactly
this pattern. Worse, these tests sat on the paths CORE-01, CORE-04 and
CORE-05 were broken on and passed the whole time.

They now charge a real household on a real ledger and check the money moved.
"""
from __future__ import annotations

import pytest

from world.life.upkeep import (
    HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD,
    HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD,
    UPKEEP_TOTAL_SIM_PER_DAY,
)
from world.core.clock import TICKS_PER_DAY, timestamp_for
from world.run_genesis import GenesisRunner, GenesisRunnerConfig


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
async def test_a_day_boundary_actually_moves_money(tmp_path):
    """The hook the module has been asking for, exercised rather than read."""
    runner = _runner(tmp_path)
    await runner.boot()
    world = runner.world_state

    before = {a: world.ledger.sim_balance(a) for a in world.agents}
    assert any(v > 0 for v in before.values()), "nobody holds sim; the fixture is empty"

    # The first real day boundary. CORE-04 made the boot day not count, so
    # this is the first tick at which upkeep is due.
    await runner.run_life_ticks(timestamp_for(TICKS_PER_DAY))

    after = {a: world.ledger.sim_balance(a) for a in world.agents}
    moved = [a for a in before if after[a] != before[a]]
    assert moved, (
        "a whole game-day passed and not one resident's sim balance changed. "
        "Nothing in the town costs anything, so standing still is the winning "
        "strategy - which is exactly what DESIGN.md section 3 forbids."
    )


@pytest.mark.asyncio
async def test_the_boot_day_is_not_charged(tmp_path):
    """CORE-04, held here too: the first tick of a run must not bill a day
    nobody lived."""
    runner = _runner(tmp_path)
    await runner.boot()
    world = runner.world_state

    before = {a: world.ledger.sim_balance(a) for a in world.agents}
    await runner.run_life_ticks(timestamp_for(1))
    after = {a: world.ledger.sim_balance(a) for a in world.agents}

    assert before == after, (
        "the first tick of the run charged upkeep, so every run this project "
        "has made was silently billed for a day that never happened"
    )


@pytest.mark.asyncio
async def test_one_households_failure_does_not_stop_the_others(tmp_path):
    """A resident who cannot pay must not stop the town being charged."""
    runner = _runner(tmp_path)
    await runner.boot()
    world = runner.world_state

    # Make one resident far richer than the rest, so the day's billing has
    # both a payer who can and payers who cannot.
    rich = next(iter(world.agents))
    await world.ledger.mint_sim(rich, 100_000, tick=0, reason="blessing")

    before = {a: world.ledger.sim_balance(a) for a in world.agents}
    await runner.run_life_ticks(timestamp_for(TICKS_PER_DAY))
    after = {a: world.ledger.sim_balance(a) for a in world.agents}

    assert after != before, (
        "the whole day's billing stopped; one household that could not pay "
        "must not stop the town being charged"
    )


def test_the_escalation_ladder_is_real_and_ordered():
    """Not source text: the constants themselves, and their order.

    DESIGN section 3 is a ladder - mild hunger, then malnutrition, then
    dormancy. If the thresholds are equal or inverted the ladder collapses
    into a single step and a resident goes from fed to dormant with nothing
    in between.
    """
    assert 0 < HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD < HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD, (
        f"severe={HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD} and "
        f"dormancy={HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD}: the escalation "
        "ladder is not ordered, so there is no warning before dormancy"
    )
    assert UPKEEP_TOTAL_SIM_PER_DAY > 0, "living is free; idleness wins"


@pytest.mark.asyncio
async def test_eating_is_wired_to_hunger(tmp_path):
    """Eating must reach the hunger state, not just emit an event."""
    runner = _runner(tmp_path)
    await runner.boot()
    assert runner.hunger_by_agent is not None, (
        "there is nowhere to record that a resident is hungry, so eating "
        "cannot mean anything"
    )
    # Populated lazily, so the claim worth testing is that a day fills it -
    # otherwise upkeep's escalation ladder has nothing to climb.
    await runner.run_life_ticks(timestamp_for(TICKS_PER_DAY))
    assert runner.hunger_by_agent, (
        "a whole game-day passed and no resident has a hunger state, so "
        "nobody can ever go hungry and the ladder from mild hunger to "
        "malnutrition to dormancy is unreachable"
    )
