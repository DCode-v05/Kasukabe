"""End-to-end proof for world/ledger/head_tax.py, across several real
game-days, against the REAL world.charter (government policy, the actual
tax-rate-setting action) and the REAL world.crime (complaints desk)
modules, not fakes standing in for them.

This project's standing lesson is that every serious bug came from two
modules built against an assumed contract, each mocked on the other's
side, both green. The only way to not repeat that here is to drive the
real CharterStore/GovernmentActions and the real CrimeStore/ComplaintDesk
exactly as their own test suites do (see world/charter/tests/
test_charter_government.py and world/crime/tests/test_crime_desk.py,
whose construction patterns this file mirrors) and assert on what those
real modules actually recorded, not on a stand-in's behavior.

Scenario, one household (amara the adult, beni and chiazo her children),
across four game-days:

  day 1 (tick 144): genesis tax rate (0) charges nothing.
  between day 1 and day 2: the sitting government sets tax_rate_bps=1000
    (10%) through the real GovernmentActions.set_tax_rate.
  day 2 (tick 288): amara can afford all three heads' tax; all three are
    paid, on_behalf_of naming each head.
  day 3 (tick 432): amara's sim has run low; she pays her own head's tax
    and cannot cover beni's or chiazo's, which accrue as recorded debt.
    That debt is filed at the real crime complaints desk.
  day 4 (tick 576): amara is paid a wage, day 4's tax is paid in full,
    and she repays her outstanding head-tax debt.

Throughout: ledger.check_invariant() is asserted after every operation
that touches money, no agent or treasury balance is ever negative, and
the treasury's final balance is independently reconciled against the
ledger's own transaction log rather than against this test's running
totals.
"""

from __future__ import annotations

import pytest

from world.charter.genesis import seed_genesis
from world.charter.government import GovernmentActions
from world.charter.models import LOCATION_GOVERNMENT_COMPLEX, GovernmentState
from world.charter.store import CharterStore
from world.core.clock import is_day_boundary, timestamp_for
from world.crime.desk import ComplaintDesk
from world.crime.models import CaseStatus, CrimeKind
from world.crime.store import CrimeStore
from world.ledger.head_tax import (
    HeadTaxDebtStore,
    charge_daily_head_tax,
    file_head_tax_delinquency,
    head_tax_view,
    read_tax_rate_bps,
    repay_head_tax_debt,
)
from world.ledger.core import Ledger
from world.ledger.models import Currency
from world.ledger.money import TOTAL_USD_CENTS, TREASURY_ACCOUNT_ID
from world.ledger.tests.ledger_factories import build_genesis_ledger

HOUSEHOLD = "house-amara"
HEADS = ["amara", "beni", "chiazo"]

DAY_1_TICK = 144
DAY_2_TICK = 288
DAY_3_TICK = 432
DAY_4_TICK = 576


async def make_government() -> tuple[CharterStore, GovernmentActions]:
    store = CharterStore()
    await seed_genesis(store, genesis_tick=0)
    await store.set_government_state(
        GovernmentState(pm_agent_id="pm", minister_agent_ids=["m1", "m2", "m3", "m4"])
    )
    return store, GovernmentActions(store)


def assert_no_negative_balances(ledger: Ledger, agent_ids: list[str]) -> None:
    for agent_id in agent_ids:
        assert ledger.sim_balance(agent_id) >= 0
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) >= 0


@pytest.mark.asyncio
async def test_multi_day_head_tax_scenario() -> None:
    """The full PROVE checklist in one run: a working adult taxed for
    itself plus two children, a government rate change taking effect,
    unpaid tax becoming a recorded debt filed with the real crime desk,
    balances never negative, and the ledger invariant holding throughout.
    """
    ledger = await build_genesis_ledger(HEADS, agent_usd_cents=200, sim_per_agent=0)
    # Enough for day 2's full bill (300) plus day 3's own head (100), not
    # enough left over for day 3's two dependents (200 more): the exact
    # shortfall this scenario needs to prove debt accrual honestly.
    ledger.accounts["amara"].sim_units = 400
    debts = HeadTaxDebtStore()
    charter_store, government = await make_government()
    crime_store = CrimeStore()
    desk = ComplaintDesk(crime_store)

    assert is_day_boundary(DAY_1_TICK)
    assert timestamp_for(DAY_1_TICK).game_day == 1
    assert timestamp_for(DAY_2_TICK).game_day == 2
    assert timestamp_for(DAY_3_TICK).game_day == 3
    assert timestamp_for(DAY_4_TICK).game_day == 4

    # -- day 1: this town has not legislated a tax, so nobody is taxed ----
    #
    # Set here rather than leaned on. This used to read the genesis default
    # and assert it was 0, which made the scenario depend on a constant it
    # is not about - and ECON-01 changed that constant, because a town that
    # starts at 0 bps and has no seated government to raise it can never
    # tax anybody. The story below is "a rate of nothing charges nothing,
    # then a government sets one", and it should say which rate it means.
    await government.set_tax_rate(
        0, "m2", LOCATION_GOVERNMENT_COMPLEX, now_tick=DAY_1_TICK - 1
    )
    rate = await read_tax_rate_bps(charter_store)
    assert rate == 0

    result_1 = await charge_daily_head_tax(
        ledger,
        debts,
        payer_agent_id="amara",
        head_agent_ids=HEADS,
        tax_rate_bps=rate,
        tick=DAY_1_TICK,
        household_id=HOUSEHOLD,
    )
    ledger.check_invariant()
    assert_no_negative_balances(ledger, HEADS)
    assert result_1.total_paid_sim == 0
    assert result_1.total_new_debt_sim == 0
    assert ledger.sim_balance("amara") == 400

    # -- between day 1 and day 2: the government legislates a tax rate ---
    # A real seat holder, at the real government complex, through the
    # real GovernmentActions API, not a direct store poke.
    policy = await government.set_tax_rate(
        1_000, "m2", LOCATION_GOVERNMENT_COMPLEX, now_tick=DAY_1_TICK + 1
    )
    assert policy.tax_rate_bps == 1_000
    assert policy.version == 2

    # -- day 2: the new rate is live; amara covers all three heads -------
    rate = await read_tax_rate_bps(charter_store)
    assert rate == 1_000  # the rate change took effect

    view_before_day_2 = await head_tax_view(
        debts, debtor_agent_id="amara", head_agent_ids=HEADS, tax_rate_bps=rate
    )
    assert view_before_day_2.sim_per_head == 100
    assert view_before_day_2.amount_owed_today_sim == 300
    assert view_before_day_2.outstanding_debt_sim == 0

    result_2 = await charge_daily_head_tax(
        ledger,
        debts,
        payer_agent_id="amara",
        head_agent_ids=HEADS,
        tax_rate_bps=rate,
        tick=DAY_2_TICK,
        household_id=HOUSEHOLD,
    )
    ledger.check_invariant()
    assert_no_negative_balances(ledger, HEADS)

    assert result_2.total_paid_sim == 300
    assert result_2.total_new_debt_sim == 0
    assert len(result_2.paid) == 3
    on_behalf_of = {t.on_behalf_of for t in result_2.paid}
    assert on_behalf_of == {None, "beni", "chiazo"}  # per-head attribution
    for transaction in result_2.paid:
        assert transaction.amount == 100
        assert transaction.from_account == "amara"
        assert transaction.to_account == TREASURY_ACCOUNT_ID

    assert ledger.sim_balance("amara") == 100  # 400 - 300
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) == 300
    assert await debts.outstanding("amara") == 0

    # -- day 3: only enough left for one head, two dependents go to debt --
    result_3 = await charge_daily_head_tax(
        ledger,
        debts,
        payer_agent_id="amara",
        head_agent_ids=HEADS,
        tax_rate_bps=rate,
        tick=DAY_3_TICK,
        household_id=HOUSEHOLD,
    )
    ledger.check_invariant()
    assert_no_negative_balances(ledger, HEADS)

    assert result_3.total_paid_sim == 100  # amara's own head, paid first
    assert result_3.paid[0].on_behalf_of is None
    assert result_3.total_new_debt_sim == 200
    assert {d.head_agent_id for d in result_3.new_debts} == {"beni", "chiazo"}
    assert ledger.sim_balance("amara") == 0  # spent everything, never negative
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) == 400  # 300 + 100

    assert await debts.outstanding("amara") == 200

    view_after_day_3 = await head_tax_view(
        debts, debtor_agent_id="amara", head_agent_ids=HEADS, tax_rate_bps=rate
    )
    assert view_after_day_3.outstanding_debt_sim == 200
    assert view_after_day_3.tax_rate_bps == 1_000
    assert view_after_day_3.sim_per_head == 100

    # -- the debt is not silent: it is filed at the real complaints desk --
    beni_debt = next(d for d in result_3.new_debts if d.head_agent_id == "beni")
    complaint, case = await file_head_tax_delinquency(
        desk,
        debtor_agent_id="amara",
        head_agent_id="beni",
        amount_sim=beni_debt.amount_sim,
        tick=DAY_3_TICK,
    )
    assert case.kind is CrimeKind.OTHER
    assert case.status is CaseStatus.OPEN
    assert case.accused_agent_id == "amara"
    assert complaint.case_id == case.case_id
    # It is really in the store, not just in the return value.
    stored_case = await desk.get_case(case.case_id)
    assert stored_case.accused_agent_id == "amara"
    all_cases = await desk.list_cases()
    assert case.case_id in {c.case_id for c in all_cases}

    # A second head's shortfall from the same day's debt attaches to the
    # existing case instead of opening a duplicate.
    chiazo_debt = next(d for d in result_3.new_debts if d.head_agent_id == "chiazo")
    _, same_case = await file_head_tax_delinquency(
        desk,
        debtor_agent_id="amara",
        head_agent_id="chiazo",
        amount_sim=chiazo_debt.amount_sim,
        tick=DAY_3_TICK,
        case_id=case.case_id,
    )
    assert same_case.case_id == case.case_id
    assert len(await desk.list_complaints(case.case_id)) == 2

    # -- day 4: amara earns a wage, day 4's tax is paid, debt is repaid --
    # The treasury pays wages out of the same sim balance it collects tax
    # into (world.ledger.core.pay_wage's own docstring: TREASURY_ACCOUNT_ID
    # is "a government job"); top it up first, exactly like
    # test_ledger_wage_tax_pension.py's own
    # test_pay_wage_from_the_government does, to represent the sim a real
    # treasury would also hold from business fees and the rest of the
    # economy this scenario does not otherwise model.
    treasury_topup_sim = 1_000
    ledger.accounts[TREASURY_ACCOUNT_ID].sim_units += treasury_topup_sim
    await ledger.pay_wage(TREASURY_ACCOUNT_ID, "amara", 500, tick=DAY_4_TICK - 1)
    ledger.check_invariant()
    assert_no_negative_balances(ledger, HEADS)
    assert ledger.sim_balance("amara") == 500

    result_4 = await charge_daily_head_tax(
        ledger,
        debts,
        payer_agent_id="amara",
        head_agent_ids=HEADS,
        tax_rate_bps=rate,
        tick=DAY_4_TICK,
        household_id=HOUSEHOLD,
    )
    ledger.check_invariant()
    assert_no_negative_balances(ledger, HEADS)
    assert result_4.total_paid_sim == 300
    assert result_4.total_new_debt_sim == 0
    assert ledger.sim_balance("amara") == 200  # 500 - 300

    repayment = await repay_head_tax_debt(
        ledger, debts, debtor_agent_id="amara", amount_sim=200, tick=DAY_4_TICK
    )
    ledger.check_invariant()
    assert_no_negative_balances(ledger, HEADS)
    assert repayment.amount_sim == 200
    assert ledger.sim_balance("amara") == 0
    assert await debts.outstanding("amara") == 0

    # -- the $1000 USD invariant held throughout: this whole scenario is
    # sim-currency only, so USD never moved, and it still reconciles.
    assert ledger.usd_total() == TOTAL_USD_CENTS
    assert ledger.halted is False

    # -- the treasury's final sim balance is independently reconciled
    # from the ledger's own transaction log (every sim leg that ever
    # touched it, in either direction) plus the one manual top-up above,
    # rather than trusted from this test's running totals.
    sim_inflows = sum(
        t.amount
        for t in ledger.transactions
        if t.to_account == TREASURY_ACCOUNT_ID and t.currency is Currency.SIM
    )
    sim_outflows = sum(
        t.amount
        for t in ledger.transactions
        if t.from_account == TREASURY_ACCOUNT_ID and t.currency is Currency.SIM
    )
    assert (
        ledger.sim_balance(TREASURY_ACCOUNT_ID) == treasury_topup_sim + sim_inflows - sim_outflows
    )

    # -- and specifically, every sim unit amara herself paid in tax or
    # debt repayment is exactly her three days' bills plus her repayment,
    # nothing more and nothing less.
    amara_to_treasury_sim = sum(
        t.amount
        for t in ledger.transactions
        if t.from_account == "amara"
        and t.to_account == TREASURY_ACCOUNT_ID
        and t.currency is Currency.SIM
    )
    assert amara_to_treasury_sim == 300 + 100 + 300 + 200  # day2 + day3 + day4 + repayment
