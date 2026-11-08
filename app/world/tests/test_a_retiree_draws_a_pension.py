"""ECON-09: `pay_pension` had zero callers.

`Ledger.pay_pension` implements DESIGN section 4 in full - the treasury
pays at 70+, a quarter of last salary for a private-sector retiree and half
for a government one - and **nothing in the tree called it**. It was found
by sweeping the reviewers' "X has no caller" claims symbol by symbol, not
by anybody re-reading the code.

So nobody in this town has ever drawn a pension. Growing old was pure loss:
a resident aged past working and then simply ran out of money, and the
"pension" line in DESIGN was decorative.
"""
from __future__ import annotations

import pytest

from world.core.clock import TICKS_PER_DAY, timestamp_for
from world.ledger.money import PENSION_MINIMUM_AGE, PENSION_PRIVATE_PCT
from world.run_genesis import GenesisRunner, GenesisRunnerConfig
from world.tools.state import WagePromise


def _runner(tmp_path) -> GenesisRunner:
    return GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000,
    ))


@pytest.mark.asyncio
async def test_a_retiree_with_a_salary_is_paid(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    world = runner.world_state

    # Fund the treasury. It holds ZERO sim at genesis while the residents
    # hold 1.57 million between them, so on a real run a pension is owed and
    # unpayable until tax revenue arrives - see the test below, which pins
    # that as a fact rather than letting it look like this one failing.
    from world.ledger.money import TREASURY_ACCOUNT_ID

    await world.ledger.mint_sim(TREASURY_ACCOUNT_ID, 10_000, tick=0, reason="blessing")

    old = next(iter(world.agents))
    world.agents[old] = world.agents[old].model_copy(update={"age_years": 72.0})
    # ECON-09, second defect. This used to key the promise on the bare
    # agent id, which is NOT what `pay_wage` writes - `labour.py:112`
    # keys it "{business_id}|{employee_id}". So this test agreed with the
    # broken lookup in `pay_the_pensions` and passed for years while no
    # pension was ever paid on a real run. Seeded the way production
    # seeds it, so the test can fail when the lookup is wrong.
    world.wage_promises[f"b_mill|{old}"] = WagePromise(
        business_id="b_mill", employee_id=old, wage_sim=400, every_days=1,
        promised_tick=0,
    )
    before = world.ledger.sim_balance(old)

    await runner.pay_the_pensions(timestamp_for(TICKS_PER_DAY))

    after = world.ledger.sim_balance(old)
    expected = 400 * PENSION_PRIVATE_PCT // 100
    assert after == before + expected, (
        f"a {int(world.agents[old].age_years)}-year-old who last earned 400 "
        f"sim drew {after - before} instead of {expected}. Nobody in this "
        "town has ever drawn a pension (ECON-09)."
    )
    assert runner.pensions_paid == 1


@pytest.mark.asyncio
async def test_somebody_of_working_age_is_not_paid(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    world = runner.world_state
    young = next(iter(world.agents))
    world.agents[young] = world.agents[young].model_copy(update={"age_years": 30.0})
    world.wage_promises[f"b_mill|{young}"] = WagePromise(
        business_id="b", employee_id=young, wage_sim=400, every_days=1, promised_tick=0,
    )
    before = world.ledger.sim_balance(young)
    await runner.pay_the_pensions(timestamp_for(TICKS_PER_DAY))
    assert world.ledger.sim_balance(young) == before
    assert runner.pensions_paid == 0


@pytest.mark.asyncio
async def test_a_retiree_who_never_earned_draws_nothing_and_is_counted(tmp_path):
    """Correct - there is no salary to take a quarter of - but visible, so a
    town where every retiree draws nothing reads as that rather than as a
    pension system nobody noticed was idle."""
    runner = _runner(tmp_path)
    await runner.boot()
    world = runner.world_state
    old = next(iter(world.agents))
    world.agents[old] = world.agents[old].model_copy(update={"age_years": 80.0})
    before = world.ledger.sim_balance(old)

    await runner.pay_the_pensions(timestamp_for(TICKS_PER_DAY))

    assert world.ledger.sim_balance(old) == before
    assert runner.pensions_paid == 0
    assert runner.pensions_without_a_salary >= 1


@pytest.mark.asyncio
async def test_the_minimum_age_is_the_designed_one(tmp_path):
    assert PENSION_MINIMUM_AGE == 70
    assert PENSION_PRIVATE_PCT == 25


def test_the_day_boundary_actually_calls_it():
    """A sweep nothing calls is the bug it was written to fix."""
    import ast
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(GenesisRunner.run_life_ticks))
    attrs = {
        node.func.attr
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "pay_the_pensions" in attrs, (
        "nothing on the day boundary pays a pension, so `pay_pension` still "
        "has zero callers (ECON-09)"
    )
    assert "charge_the_day_tax" in attrs, "the tax sweep went missing too"


@pytest.mark.asyncio
async def test_the_treasury_starts_with_nothing_to_pay_pensions_from(tmp_path):
    """The other half of ECON-04, found by wiring ECON-09.

    The sweep above is correct and wired, and on a real run it still pays
    nobody on day 1: the treasury holds **zero sim** at genesis while the
    residents hold about 1.57 million between them. So the government can
    fund nothing - no pension, no payroll, no relief - until head tax
    revenue arrives.

    Whether the treasury should be seeded, or should fill only from tax, is
    a genesis policy question (`06`.1: fixed sim supply, or minted against
    production). It is the operator's, not this test's. What this test does
    is stop the fact being invisible.
    """
    from world.ledger.money import TREASURY_ACCOUNT_ID

    runner = _runner(tmp_path)
    await runner.boot()
    treasury = runner.world_state.ledger.sim_balance(TREASURY_ACCOUNT_ID)
    residents = sum(
        runner.world_state.ledger.sim_balance(a) for a in runner.world_state.agents
    )
    assert treasury == 0 and residents > 0, (
        f"treasury={treasury}, residents={residents}. If the treasury is now "
        "seeded, a genesis policy decision was taken - record it in "
        "OPEN-QUESTIONS-ANSWERED.md `06`.1 and update this test."
    )
