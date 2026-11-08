"""ECON-10 and GOV-09, wiring: the answers must be reachable from the
object the running town actually holds, not just from a store a test built.

This project's commonest defect by a distance is a complete, tested
mechanism that nothing calls - `pay_pension` had zero callers for
twenty-three runs, `charge_daily_head_tax` documented "call this once per
household per completed game-day" and nobody did, and `create_job` stored
a salary that no code path ever read as a debt. A payroll query that only
a unit test can reach would be the same defect wearing a new name.

So this file boots the real `GenesisRunner` in mock mode and works through
`runner.world_state.charter.government` - the exact `GovernmentActions`
instance that `CharterEngine` builds at boot and that the daily job block
in `run_genesis.py` (`charge_the_day_tax`, `pay_the_pensions`,
`reconcile_wages`) has in hand. It proves three things about the live
object graph: a government that hires somebody produces a wage bill on it,
settling a day on it closes that day, and the settlement survives the
checkpoint that `CharterCheckpointAdapter` writes - which matters more
than it sounds, because a resume that dropped the "paid through" tick
would pay every past game-day all over again on the next payroll run.

The payroll job that reads the bill and moves the treasury's money lives
in `run_genesis.py`'s day-job block, and is not part of this change.
"""

from __future__ import annotations

import pytest

from world.charter.models import LOCATION_GOVERNMENT_COMPLEX, TICKS_PER_GAME_DAY, GovernmentState
from world.run_genesis import GenesisRunner, GenesisRunnerConfig


def _runner(tmp_path) -> GenesisRunner:
    return GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000,
    ))


async def _booted_with_a_government(tmp_path) -> GenesisRunner:
    runner = _runner(tmp_path)
    await runner.boot()
    residents = list(runner.world_state.agents)
    await runner.world_state.charter.store.set_government_state(
        GovernmentState(pm_agent_id=residents[0], minister_agent_ids=residents[1:3])
    )
    return runner


@pytest.mark.asyncio
async def test_the_live_charter_engine_reports_the_government_wage_bill(tmp_path) -> None:
    runner = await _booted_with_a_government(tmp_path)
    government = runner.world_state.charter.government
    pm = runner.world_state.charter.store.government_state.pm_agent_id
    officer = [a for a in runner.world_state.agents if a != pm][0]

    await government.hire_police(officer, 300, pm, LOCATION_GOVERNMENT_COMPLEX, now_tick=0)
    lines = await government.payroll_due(TICKS_PER_GAME_DAY)

    assert [(line.agent_id, line.amount_sim) for line in lines] == [(officer, 300)], (
        "the government of this booted town hired a police officer and its own charter "
        "engine reported no wage bill a game-day later. A payroll job hanging off this "
        "object would pay nobody (ECON-10)."
    )
    assert await government.payroll_due_sim(TICKS_PER_GAME_DAY) == 300


@pytest.mark.asyncio
async def test_the_paid_through_tick_survives_a_checkpoint(tmp_path) -> None:
    runner = await _booted_with_a_government(tmp_path)
    government = runner.world_state.charter.government
    store = runner.world_state.charter.store
    pm = store.government_state.pm_agent_id
    officer = [a for a in runner.world_state.agents if a != pm][0]
    day = TICKS_PER_GAME_DAY

    job = await government.hire_police(officer, 300, pm, LOCATION_GOVERNMENT_COMPLEX, now_tick=0)
    (line,) = await government.payroll_due(day)
    await government.record_payroll_paid(job.job_id, line.periods, line.amount_sim, now_tick=day)

    checkpoint = await runner.tick_engine.checkpoints.write(tick_index=day, game_day=1)

    forgetful = await store.get_job(job.job_id)
    forgetful.last_paid_tick = None
    forgetful.total_paid_sim = 0
    await store.put_job(forgetful)
    assert await government.payroll_due(day) != []

    await runner.tick_engine.checkpoints.restore(checkpoint)

    restored = await store.get_job(job.job_id)
    assert restored.last_paid_tick == day
    assert restored.total_paid_sim == 300
    assert await government.payroll_due(day) == [], (
        "a resumed town re-owed a salary it had already paid. The checkpoint has to carry "
        "the settled tick or every resume pays the whole employment history again."
    )


@pytest.mark.asyncio
async def test_the_live_charter_engine_answers_what_a_pension_is_worth(tmp_path) -> None:
    runner = await _booted_with_a_government(tmp_path)
    government = runner.world_state.charter.government
    pm = runner.world_state.charter.store.government_state.pm_agent_id

    assert await government.pension_due_sim(4_000, "private", 72) == 1_000

    await government.set_pension_config(
        pm, LOCATION_GOVERNMENT_COMPLEX, now_tick=0, private_pct=40, eligibility_age=65
    )

    assert await government.pension_due_sim(4_000, "private", 72) == 1_600, (
        "the sitting PM of a booted town changed the pension rate through the real tool "
        "path and the live engine still quoted the old one (GOV-09)."
    )
    assert await government.pension_due_sim(4_000, "private", 66) == 1_600, (
        "retirement was voted down to 65; a 66-year-old is now a retiree"
    )
