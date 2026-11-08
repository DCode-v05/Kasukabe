"""ECON-01 / GOV-06: a tax of nothing, and a gate that could not tell.

`GovernmentPolicy.tax_rate_bps` defaults to 0. `set_tax_rate` is
government-namespace and the seats have been vacant in all 23 runs because
no election was ever tallied, so the only rate this town has ever had is
zero. `head_tax_sim_per_head(0)` is 0 and `charge_daily_head_tax` skips
every ledger move.

Meanwhile `assert_the_day_was_charged` - the S1 gate, the one that is
supposed to halt a run whose economy skipped a day - compared households
VISITED against households that EXIST. Both numbers were right. No money
moved. The evaluation spec's "head tax charged at every day boundary, for
every household, 100%, halts the run if missed" has been satisfied, every
run, by a tax that took nothing from anybody.

Two changes, and they only work together: a real genesis rate read from
the rulebook that already carried one, and a gate that asserts sim.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.charter.genesis import genesis_tax_rate_bps
from world.core.clock import timestamp_for
from world.ledger.head_tax import head_tax_sim_per_head

from world.tests.test_run_genesis import make_runner


def test_the_town_starts_with_a_tax_it_can_actually_charge():
    rate = genesis_tax_rate_bps()
    assert rate > 0, "a town that starts at 0 bps can never charge anybody"
    assert head_tax_sim_per_head(rate) > 0


def test_the_rate_is_the_rulebooks_and_not_a_second_number():
    """GOV-18: the world's charter said 0 while the viewer's economy said 3%.

    One of them has to be the source. Read here rather than retyped, so a
    change to the rulebook cannot leave the two disagreeing again.
    """
    from world.rules.loader import load_rules

    assert genesis_tax_rate_bps() == int(load_rules().get("economy.tax_rate_bps"))


@pytest.mark.asyncio
async def test_a_booted_town_has_a_non_zero_policy(tmp_path: Path) -> None:
    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    assert runner.world_state is not None

    policy = await runner.world_state.charter.store.get_policy()
    assert policy.tax_rate_bps > 0


@pytest.mark.asyncio
async def test_the_day_boundary_moves_real_money(tmp_path: Path) -> None:
    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    assert runner.world_state is not None and runner.ledger is not None

    treasury_before = runner.ledger.sim_balance("government:treasury")
    stamp = timestamp_for(144)
    await runner.charge_the_day_tax(stamp)

    record = runner.day_jobs_ran[stamp.game_day]
    assert record["households"] > 0
    assert record["head_tax"] == record["households"]
    assert record["head_tax_sim"] > 0
    assert runner.ledger.sim_balance("government:treasury") > treasury_before


@pytest.mark.asyncio
async def test_the_gate_halts_a_non_zero_rate_that_moved_nothing(
    tmp_path: Path,
) -> None:
    """The failure the old gate was blind to, made to happen on purpose."""
    runner = make_runner(tmp_path)
    await runner.boot(resume=False)

    stamp = timestamp_for(144)
    runner.day_jobs_ran[stamp.game_day] = {
        "households": 4,
        "head_tax": 4,
        "head_tax_sim": 0,
        "head_tax_rate_bps": 300,
    }

    runner.assert_the_day_was_charged(stamp)

    assert runner.stop_requested
    assert "moved 0 sim" in (runner.halt_reason or "")


@pytest.mark.asyncio
async def test_a_town_that_voted_its_tax_away_is_not_a_fault(tmp_path: Path) -> None:
    """A zero RATE is a policy. A zero YIELD at a non-zero rate is a bug.

    The gate must not punish a government for choosing not to tax, or the
    first amendment that lowers the rate halts the run.
    """
    runner = make_runner(tmp_path)
    await runner.boot(resume=False)

    stamp = timestamp_for(144)
    runner.day_jobs_ran[stamp.game_day] = {
        "households": 4,
        "head_tax": 4,
        "head_tax_sim": 0,
        "head_tax_rate_bps": 0,
    }

    runner.assert_the_day_was_charged(stamp)

    assert not runner.stop_requested


@pytest.mark.asyncio
async def test_a_skipped_household_still_halts_the_run(tmp_path: Path) -> None:
    """The check that already existed has to keep working."""
    runner = make_runner(tmp_path)
    await runner.boot(resume=False)

    stamp = timestamp_for(144)
    runner.day_jobs_ran[stamp.game_day] = {
        "households": 4,
        "head_tax": 3,
        "head_tax_sim": 90,
        "head_tax_rate_bps": 300,
    }

    runner.assert_the_day_was_charged(stamp)

    assert runner.stop_requested
    assert "3 of 4" in (runner.halt_reason or "")
