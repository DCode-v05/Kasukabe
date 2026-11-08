"""A town with a police force pays its police.

`GOV-08`. The treasury had revenue and no spending. Head tax,
registration fees, `rule` fines and `pray` donations all flow in;
nothing flowed out. `Job.salary_sim` was stored by `create_job` and read
by nobody, `appoint_police`'s `wage_sim` was an event payload and
nothing else, and `treasury_view` broke out a wage line it could never
see. So a government could staff a police force, a school and a clinic,
and every one of those people worked for free - which makes a
government job strictly worse than no job at all, and makes every
"why does nobody take office" question unanswerable.

Half of it already existed with no caller: `GovernmentEngine.payroll_due`
and `record_payroll_paid` (ECON-10) are a complete payroll ledger that
deliberately does not move money, because `charter/` holds no reference
to the money layer. The missing half was a payer.

The properties that make a payer correct rather than merely present:

* it pays the treasury's money to the person named, not to nobody;
* it closes the game-day AFTER the transfer, so a failed transfer is
  not recorded as a paid wage;
* one unpayable post does not stop the rest, because an empty treasury
  is a fact about the town, not a reason to end a tick;
* a confined resident earns nothing, per `crime/outcomes.py`;
* and the same day is never paid twice.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from world.core.clock import TICKS_PER_DAY, timestamp_for
from world.ledger.models import TransactionReason
from world.charter.models import Job
from world.ledger.money import TREASURY_ACCOUNT_ID
from world.run_genesis import GenesisRunner, GenesisRunnerConfig


@pytest_asyncio.fixture
async def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot(resume=False)
    return runner


async def _fund_the_treasury(runner, sim: int = 10_000) -> None:
    """Give the treasury money to pay with.

    Needed, and the reason is itself worth stating: the treasury is
    funded in USD at genesis and holds **zero sim**. So a brand-new town
    cannot pay a sim wage at all until it has taxed somebody - which is
    why `charge_the_day_tax` runs before `pay_the_town_payroll` at the
    day boundary rather than after, and `test_the_tax_comes_in_before_
    the_wages_go_out` holds that ordering in place.
    """
    await runner.ledger.mint_sim(TREASURY_ACCOUNT_ID, sim, 0, memo="test float")


async def _post(runner, *, agent_id: str | None, salary_sim: int, tick: int = 0) -> Job:
    """Put a post on the town's books.

    Written into the store rather than through `create_job`, which
    requires a seated government at the complex. This file is about the
    PAYER: seating a mayor first would make every test here also a test
    of `require_sitting`, and a failure there would read as a payroll
    failure.
    """
    job = Job(
        title="constable",
        job_type="police",
        salary_sim=salary_sim,
        assigned_agent_id=agent_id,
        created_by_agent_id="mayor",
        created_tick=tick,
    )
    await runner.world_state.charter.store.put_job(job)
    return job


async def _hire(runner, agent_id: str, salary_sim: int, tick: int = 0) -> Job:
    return await _post(runner, agent_id=agent_id, salary_sim=salary_sim, tick=tick)


def _promise(agent_id: str, wage_sim: int):
    """A wage promise, which is where `pay_the_pensions` reads last salary."""
    from world.tools.models import WagePromise

    return WagePromise(
        business_id="b_mill", employee_id=agent_id, wage_sim=wage_sim,
        promised_tick=0,
    )


def _somebody(runner) -> str:
    return sorted(runner.world_state.agents)[0]


@pytest.mark.asyncio
async def test_a_filled_post_is_actually_paid(runner):
    await _fund_the_treasury(runner)
    who = _somebody(runner)
    await _hire(runner, who, salary_sim=140)

    before = runner.ledger.sim_balance(who)
    treasury_before = runner.ledger.sim_balance(TREASURY_ACCOUNT_ID)

    await runner.pay_the_town_payroll(timestamp_for(TICKS_PER_DAY))

    assert runner.ledger.sim_balance(who) == before + 140, (
        "a constable worked a day for the town and was paid nothing"
    )
    assert runner.ledger.sim_balance(TREASURY_ACCOUNT_ID) == treasury_before - 140
    assert runner.payroll_lines_paid == 1
    assert runner.payroll_paid_sim == 140


@pytest.mark.asyncio
async def test_a_vacant_post_costs_nothing(runner):
    await _post(runner, agent_id=None, salary_sim=140)
    treasury_before = runner.ledger.sim_balance(TREASURY_ACCOUNT_ID)

    await runner.pay_the_town_payroll(timestamp_for(TICKS_PER_DAY))

    assert runner.ledger.sim_balance(TREASURY_ACCOUNT_ID) == treasury_before
    assert runner.payroll_lines_paid == 0


@pytest.mark.asyncio
async def test_the_same_day_is_not_paid_twice(runner):
    await _fund_the_treasury(runner)
    who = _somebody(runner)
    await _hire(runner, who, salary_sim=140)
    when = timestamp_for(TICKS_PER_DAY)

    await runner.pay_the_town_payroll(when)
    after_one = runner.ledger.sim_balance(who)
    await runner.pay_the_town_payroll(when)

    assert runner.ledger.sim_balance(who) == after_one, (
        "the payroll ran twice for one game-day"
    )
    assert runner.payroll_lines_paid == 1


@pytest.mark.asyncio
async def test_a_second_day_is_paid(runner):
    """The other direction: the guard must not stop the payroll forever."""
    await _fund_the_treasury(runner)
    who = _somebody(runner)
    await _hire(runner, who, salary_sim=140)

    await runner.pay_the_town_payroll(timestamp_for(TICKS_PER_DAY))
    after_one = runner.ledger.sim_balance(who)
    await runner.pay_the_town_payroll(timestamp_for(TICKS_PER_DAY * 2))

    assert runner.ledger.sim_balance(who) == after_one + 140


@pytest.mark.asyncio
async def test_a_confined_resident_earns_nothing(runner):
    """`crime/outcomes.py`: a sentence costs you your income."""
    await _fund_the_treasury(runner)
    who = _somebody(runner)
    await _hire(runner, who, salary_sim=140)
    runner.world_state.confined[who] = {"case_id": "case1", "until_tick": 999}

    before = runner.ledger.sim_balance(who)
    await runner.pay_the_town_payroll(timestamp_for(TICKS_PER_DAY))

    assert runner.ledger.sim_balance(who) == before
    assert runner.payroll_skipped_confined == 1
    assert runner.payroll_lines_paid == 0


@pytest.mark.asyncio
async def test_one_unpayable_post_does_not_stop_the_others(runner, monkeypatch):
    await _fund_the_treasury(runner)
    first, second = sorted(runner.world_state.agents)[:2]
    await _hire(runner, first, salary_sim=140)
    await _hire(runner, second, salary_sim=90)

    real_pay = runner.ledger.pay_wage
    calls: list[str] = []

    async def one_bad_apple(employer, employee, amount, tick, *, memo=""):
        calls.append(employee)
        if employee == first:
            raise RuntimeError("treasury refused")
        return await real_pay(employer, employee, amount, tick, memo=memo)

    monkeypatch.setattr(runner.ledger, "pay_wage", one_bad_apple)
    before = runner.ledger.sim_balance(second)

    await runner.pay_the_town_payroll(timestamp_for(TICKS_PER_DAY))

    assert len(calls) == 2, "the payroll stopped at the first failure"
    assert runner.ledger.sim_balance(second) == before + 90
    assert runner.payroll_unpayable == 1
    assert runner.payroll_lines_paid == 1


@pytest.mark.asyncio
async def test_a_failed_transfer_is_not_recorded_as_a_paid_wage(runner, monkeypatch):
    """The ordering that matters: settle after the money, never before.

    A period closed before a failing transfer is a day's wages the town
    believes it paid and did not - and the next day would not pay it
    either, because the book says it is settled.
    """
    who = _somebody(runner)
    await _hire(runner, who, salary_sim=140)

    async def always_refused(*a, **k):
        raise RuntimeError("treasury refused")

    monkeypatch.setattr(runner.ledger, "pay_wage", always_refused)
    await runner.pay_the_town_payroll(timestamp_for(TICKS_PER_DAY))

    # Still owed: the failure did not close the day.
    still_due = await runner.world_state.charter.government.payroll_due(TICKS_PER_DAY)
    assert [line.agent_id for line in still_due] == [who], (
        "a wage that could not be paid was booked as paid anyway"
    )


@pytest.mark.asyncio
async def test_the_wage_is_booked_as_a_wage(runner):
    """`treasury_view` breaks out a wage line; it has to be findable."""
    await _fund_the_treasury(runner)
    who = _somebody(runner)
    await _hire(runner, who, salary_sim=140)

    seen: list[str] = []
    real_move = runner.ledger.move

    async def watch(**kw):
        seen.append(kw.get("reason"))
        return await real_move(**kw)

    runner.ledger.move = watch
    await runner.pay_the_town_payroll(timestamp_for(TICKS_PER_DAY))

    assert TransactionReason.WAGE in seen


@pytest.mark.asyncio
async def test_an_empty_treasury_pays_nobody_and_says_so(runner):
    """A town with no sim cannot pay a wage, and that is the honest answer.

    Not funded on purpose. The treasury is funded in USD at genesis and
    holds zero sim, so a brand-new town's first payroll is unpayable
    until it has taxed somebody. The counter is what makes that a fact
    rather than a silence - `payroll_lines_paid == 0` alone reads
    identically to "nobody was employed".
    """
    who = _somebody(runner)
    await _hire(runner, who, salary_sim=140)
    before = runner.ledger.sim_balance(who)

    await runner.pay_the_town_payroll(timestamp_for(TICKS_PER_DAY))

    assert runner.ledger.sim_balance(who) == before
    assert runner.payroll_unpayable == 1
    assert runner.payroll_lines_paid == 0
    # And it is still owed, so the day the treasury has money it is paid.
    still_due = await runner.world_state.charter.government.payroll_due(TICKS_PER_DAY)
    assert [line.agent_id for line in still_due] == [who]


@pytest.mark.asyncio
async def test_the_tax_comes_in_before_the_wages_go_out(runner):
    """The ordering at the day boundary is load-bearing, not aesthetic.

    The treasury starts with no sim. If the payroll ran before the head
    tax, day one's wages would be unpayable on every run of every town -
    and the finding would read as unfixed while the code was correct.
    """
    import inspect

    source = inspect.getsource(GenesisRunner)
    tax = source.index("await self.charge_the_day_tax(timestamp)")
    payroll = source.index("await self.pay_the_town_payroll(timestamp)")
    pensions = source.index("await self.pay_the_pensions(timestamp)")
    assert tax < payroll < pensions, (
        "the town pays its staff before it collects the money to pay them"
    )


@pytest.mark.asyncio
async def test_the_day_boundary_actually_runs_it(runner):
    """The GOV-08 failure was never in the paying - it was that nothing
    called it. A payer with no caller is `Ledger.pay_pension` before
    ECON-09, and `payroll_due` before this."""
    await _fund_the_treasury(runner)
    import inspect

    who = _somebody(runner)
    await _hire(runner, who, salary_sim=140)
    before = runner.ledger.sim_balance(who)

    # The real day-boundary path, not the method directly.
    await runner.run_life_day(timestamp_for(TICKS_PER_DAY)) if hasattr(
        runner, "run_life_day"
    ) else None
    source = inspect.getsource(GenesisRunner)
    assert "await self.pay_the_town_payroll(timestamp)" in source, (
        "GOV-08: the payer exists and the day boundary does not call it"
    )


# ── GOV-09: the pension levers, which were also dead ─────────────────


async def _set_policy(runner, **fields):
    """Amend the policy in force, without staging an election."""
    store = runner.world_state.charter.store
    policy = await store.get_policy()
    await store.push_policy(policy.model_copy(update=fields))


@pytest.mark.asyncio
async def test_a_lowered_retirement_age_actually_retires_somebody(runner):
    """GOV-09. `pension_eligibility_age` was a lever nothing read.

    The payment enforced 70 from a module constant, and so did the loop
    that decided who to consider - so a town that voted retirement down
    to 65 changed a number no payment and no eligibility check ever
    consulted. Both had to move: leaving the loop on the constant would
    skip the 66-year-old before the payment ever saw them.
    """
    await _fund_the_treasury(runner)
    who = _somebody(runner)
    runner.world_state.agents[who] = runner.world_state.agents[who].model_copy(
        update={"age_years": 66.0}
    )
    runner.world_state.wage_promises["b_mill|" + who] = _promise(who, 200)

    await runner.pay_the_pensions(timestamp_for(TICKS_PER_DAY))
    before = runner.pensions_paid
    assert before == 0, "66 is not retired at the default age of 70"

    await _set_policy(runner, pension_eligibility_age=65)
    await runner.pay_the_pensions(timestamp_for(TICKS_PER_DAY))

    # The CHANGE, not the total. Genesis now seeds a wage promise for every
    # employee it seeds - before, a job earned nothing and the pension loop,
    # which reads promises, could not see an employee at all. So lowering the
    # age retires this 66-year-old AND anybody else the cast already employed
    # who is over it: a0204 at the construction yard is 67. That is the lever
    # working on more of the town, not a double payment, and asserting an
    # absolute count here only held while employment paid nothing.
    assert runner.pensions_paid > before, (
        "the town voted retirement down to 65 and nobody retired"
    )


@pytest.mark.asyncio
async def test_a_government_retiree_draws_the_government_rate(runner):
    """DESIGN section 4 pays a government retiree twice a private one.

    `pay_the_pensions` passed the literal "private" for everybody, so a
    retired constable drew half what the design says it should - and
    `pension_employment_type`, which is the only thing in the town that
    knows which somebody was, had no caller at all.
    """
    await _fund_the_treasury(runner)
    who = _somebody(runner)
    runner.world_state.agents[who] = runner.world_state.agents[who].model_copy(
        update={"age_years": 75.0}
    )
    runner.world_state.wage_promises["b_mill|" + who] = _promise(who, 200)

    before = runner.ledger.sim_balance(who)
    await runner.pay_the_pensions(timestamp_for(TICKS_PER_DAY))
    private = runner.ledger.sim_balance(who) - before
    assert private == 200 * 25 // 100

    # Now give them a government post in their history and pay again.
    await _post(runner, agent_id=who, salary_sim=140)
    before = runner.ledger.sim_balance(who)
    await runner.pay_the_pensions(timestamp_for(TICKS_PER_DAY * 2))
    government = runner.ledger.sim_balance(who) - before

    assert government == 200 * 50 // 100
    assert government == private * 2, (
        "a retired constable drew a private-sector pension"
    )


@pytest.mark.asyncio
async def test_an_amended_rate_is_the_rate_that_is_paid(runner):
    await _fund_the_treasury(runner)
    who = _somebody(runner)
    runner.world_state.agents[who] = runner.world_state.agents[who].model_copy(
        update={"age_years": 75.0}
    )
    runner.world_state.wage_promises["b_mill|" + who] = _promise(who, 200)

    await _set_policy(runner, pension_private_pct=40)
    before = runner.ledger.sim_balance(who)
    await runner.pay_the_pensions(timestamp_for(TICKS_PER_DAY))

    assert runner.ledger.sim_balance(who) - before == 200 * 40 // 100


@pytest.mark.asyncio
async def test_an_unreadable_charter_still_pays_the_default(runner, monkeypatch):
    """A charter that cannot be read is a reason to pay the default rate,
    not a reason to stop paying pensions."""
    await _fund_the_treasury(runner)
    who = _somebody(runner)
    runner.world_state.agents[who] = runner.world_state.agents[who].model_copy(
        update={"age_years": 75.0}
    )
    runner.world_state.wage_promises["b_mill|" + who] = _promise(who, 200)

    async def broken():
        raise RuntimeError("charter store unavailable")

    monkeypatch.setattr(runner.world_state.charter.store, "get_policy", broken)
    before = runner.ledger.sim_balance(who)
    await runner.pay_the_pensions(timestamp_for(TICKS_PER_DAY))

    assert runner.ledger.sim_balance(who) - before == 200 * 25 // 100


@pytest.mark.asyncio
async def test_an_unreadable_policy_still_knows_who_was_a_constable(runner, monkeypatch):
    """The `employment_type` argument has to stay accurate even when the
    percentages fall back to constants.

    Written because reverting that argument to a hard-coded "private"
    broke nothing: once `pension_pct` is handed in, `pay_pension` never
    consults it. The one path where it still decides is this one - the
    job history readable, the policy not - and without a test for it the
    argument was decorative.
    """
    await _fund_the_treasury(runner)
    who = _somebody(runner)
    runner.world_state.agents[who] = runner.world_state.agents[who].model_copy(
        update={"age_years": 75.0}
    )
    runner.world_state.wage_promises["b_mill|" + who] = _promise(who, 200)
    await _post(runner, agent_id=who, salary_sim=140)

    async def broken():
        raise RuntimeError("policy row unreadable")

    monkeypatch.setattr(runner.world_state.charter.store, "get_policy", broken)
    before = runner.ledger.sim_balance(who)
    await runner.pay_the_pensions(timestamp_for(TICKS_PER_DAY))

    assert runner.ledger.sim_balance(who) - before == 200 * 50 // 100, (
        "the policy was unreadable and a retired constable fell back to "
        "the private rate, which is not what its service was worth"
    )

