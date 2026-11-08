"""ECON-16, second order: the day's meals end with the day.

This is a bug ECON-16's own fix introduced, caught by reading the diff
rather than by any test.

`GenesisRunner.fed_today` is the set the day boundary consults to decide
whether a resident fed itself. It only ever grew. A resident that ate one
apple stayed in it for the rest of the run, so `ate_today` was
permanently true, `advance_hunger` saw `fed_today=True` every single day,
and the hunger ladder could never advance for it again - no worsening, no
malnutrition, no dormancy. Starvation switched off town-wide, by the fix
meant to switch it back on, and every unit test stayed green.

Behavioural, not a source grep: the first version of this test asserted
`"fed_today.clear()" in inspect.getsource(...)`, which is the same shape
as the EVAL-15 guard that passed over code raising NameError on every
tick. This one runs two real day boundaries.
"""

from __future__ import annotations

import pytest

from world.core.clock import TICKS_PER_DAY, timestamp_for
from world.life.upkeep import HungerState
from world.life.models import PopulationDials
from world.run_genesis import GenesisRunner, GenesisRunnerConfig


async def _town(tmp_path) -> GenesisRunner:
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000,
    ))
    await runner.boot()
    return runner


@pytest.mark.asyncio
async def test_a_meal_on_monday_does_not_feed_you_on_tuesday(tmp_path) -> None:
    runner = await _town(tmp_path)
    agent_id = next(iter(runner.world_state.agents))
    dials = PopulationDials()

    for account in list(runner.world_state.agents):
        runner.ledger.get_account(account).sim_units = 0

    runner.fed_today.add(agent_id)
    await runner.run_daily_upkeep(timestamp_for(TICKS_PER_DAY), dials)
    after_monday = runner.hunger_by_agent.get(agent_id, HungerState())
    assert after_monday.days_since_meal == 0, "the Monday meal did not register"

    await runner.run_daily_upkeep(timestamp_for(TICKS_PER_DAY * 2), dials)
    after_tuesday = runner.hunger_by_agent.get(agent_id, HungerState())

    assert after_tuesday.days_since_meal == 1, (
        "ECON-16: Monday's apple was still feeding this resident on "
        "Tuesday - `fed_today` is never cleared, so nobody in this town "
        "can ever go hungry again"
    )


@pytest.mark.asyncio
async def test_the_arrears_still_mount_while_it_eats(tmp_path) -> None:
    """The other half, over two days: eating never pays the bill."""
    runner = await _town(tmp_path)
    agent_id = next(iter(runner.world_state.agents))
    dials = PopulationDials()
    for account in list(runner.world_state.agents):
        runner.ledger.get_account(account).sim_units = 0

    for day in (1, 2):
        runner.fed_today.add(agent_id)
        await runner.run_daily_upkeep(timestamp_for(TICKS_PER_DAY * day), dials)

    hunger = runner.hunger_by_agent.get(agent_id, HungerState())
    assert hunger.days_since_meal == 0, "it ate both days"
    assert hunger.consecutive_unpaid_days == 2, (
        f"ECON-16: two unpaid days recorded as {hunger.consecutive_unpaid_days}"
    )
