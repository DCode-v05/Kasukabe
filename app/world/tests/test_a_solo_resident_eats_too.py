"""A resident with no household still has to buy dinner.

`ECON-31`. `run_daily_upkeep` grouped residents by `household_id` behind
`if household_id:`, so anybody without one was skipped entirely - they
ate for free, for ever. `charge_the_day_tax` meanwhile handles a
household-less resident as a solo payer, so the two day-boundary jobs
disagreed about whether the same person existed: taxed, but never fed.

Latent at genesis, because DESIGN section 4 gives every genesis resident
a household. It stops being latent the moment anybody leaves one.
`MOVE_OUT_ALONE` mints a new household, but a household also DISSOLVES
when its last other member dies - and GOV-25 is the identical gap seen
from the suffrage side: that orphan silently lost the vote, this one
silently stopped paying for food.

The key is `solo:<agent_id>` rather than a minted household id. Inventing
a real one would put a household in `world.households` that nobody
created, that no member joined, and that the family API would then have
to explain to a reader.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


@pytest_asyncio.fixture
async def runner(tmp_path):
    built = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await built.boot()
    return built


def _make_solo(runner) -> str:
    """Take somebody out of their household, the way a death does."""
    agent_id = next(
        a.agent_id for a in runner.world_state.agents.values()
        if a.alive and a.household_id
    )
    runner.world_state.agents[agent_id].household_id = None
    return agent_id


@pytest.mark.asyncio
async def test_a_solo_resident_is_charged_upkeep(runner) -> None:
    """The finding. Before this they were skipped outright."""
    from world.core.clock import timestamp_for

    agent_id = _make_solo(runner)
    runner.ledger.get_account(agent_id).sim_units = 5_000
    before = runner.ledger.get_account(agent_id).sim_units

    await runner.run_daily_upkeep(timestamp_for(144), runner.population_dials)

    assert runner.ledger.get_account(agent_id).sim_units < before, (
        "ECON-31: a resident with no household was skipped by upkeep, so "
        "they eat for free while still paying head tax"
    )


@pytest.mark.asyncio
async def test_they_are_billed_as_one_person_not_as_a_family(runner) -> None:
    """A solo resident is a household of ONE. Billing them for a family
    would be the opposite error and just as silent."""
    from world.core.clock import timestamp_for

    solo = _make_solo(runner)
    runner.ledger.get_account(solo).sim_units = 5_000
    solo_before = runner.ledger.get_account(solo).sim_units

    await runner.run_daily_upkeep(timestamp_for(144), runner.population_dials)
    charged = solo_before - runner.ledger.get_account(solo).sim_units

    assert charged > 0
    from world.life.upkeep import UPKEEP_TOTAL_SIM_PER_DAY

    assert charged <= UPKEEP_TOTAL_SIM_PER_DAY * 2, (
        f"ECON-31: a solo resident was billed {charged}, which is more "
        "than one person's upkeep - they are being charged as a household"
    )


@pytest.mark.asyncio
async def test_households_are_still_billed_together(runner) -> None:
    """The control. Keying everybody solo would dissolve every household
    in the town and change what the economy measures."""
    from world.core.clock import timestamp_for

    households = {
        a.household_id for a in runner.world_state.agents.values() if a.household_id
    }
    assert households, "this genesis seeds no households"

    await runner.run_daily_upkeep(timestamp_for(144), runner.population_dials)
    import inspect

    source = inspect.getsource(runner.run_daily_upkeep)
    assert "agent.household_id or" in source, (
        "ECON-31: the household is no longer the primary key, so members "
        "of one family are now billed separately"
    )


@pytest.mark.asyncio
async def test_the_two_day_jobs_agree_about_who_exists(runner) -> None:
    """The real defect underneath: `charge_the_day_tax` already treated a
    household-less resident as solo, and upkeep skipped them. Two jobs at
    the same day boundary disagreeing about the population is how a
    resident ends up taxed and never fed."""
    import inspect

    tax = inspect.getsource(runner.charge_the_day_tax)
    upkeep = inspect.getsource(runner.run_daily_upkeep)

    assert "if household_id:" not in upkeep, (
        "ECON-31: upkeep still skips household-less residents while the "
        "tax job bills them"
    )
    assert "household" in tax, "premise check: the tax job groups by household"
