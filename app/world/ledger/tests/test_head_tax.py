"""Unit tests for world/ledger/head_tax.py's fiscal mechanics: the
bps-to-sim conversion, per-head charging with per-head attribution,
debt accrual and repayment, and the read-only view.

Charter/crime integration (the real GovernmentPolicy rate lever and the
real crime complaints desk) is exercised end to end in
test_head_tax_scenario.py rather than here; this file stays scoped to
what head_tax.py owns on its own: the ledger seam.
"""

from __future__ import annotations

import pytest

from world.ledger.errors import InsufficientFundsError
from world.ledger.head_tax import (
    BPS_DENOMINATOR,
    HEAD_TAX_BASE_SIM,
    DebtOverpaymentError,
    HeadTaxDebtEventKind,
    HeadTaxDebtStore,
    InvalidTaxRateError,
    NoOutstandingDebtError,
    charge_daily_head_tax,
    head_tax_sim_per_head,
    head_tax_view,
    repay_head_tax_debt,
)
from world.ledger.money import TREASURY_ACCOUNT_ID
from world.ledger.tests.ledger_factories import RecordingEventSink, build_genesis_ledger


def test_zero_rate_charges_nothing() -> None:
    assert head_tax_sim_per_head(0) == 0


def test_full_rate_charges_the_base_amount() -> None:
    assert head_tax_sim_per_head(BPS_DENOMINATOR) == HEAD_TAX_BASE_SIM


def test_ten_percent_rate_charges_a_tenth_of_the_base() -> None:
    assert head_tax_sim_per_head(1_000) == HEAD_TAX_BASE_SIM // 10


@pytest.mark.parametrize("bad_rate", [-1, 10_001, 50_000])
def test_out_of_range_rate_raises(bad_rate: int) -> None:
    with pytest.raises(InvalidTaxRateError):
        head_tax_sim_per_head(bad_rate)


@pytest.mark.asyncio
async def test_charges_one_head_and_credits_the_treasury() -> None:
    ledger = await build_genesis_ledger(["adult"], agent_usd_cents=200, sim_per_agent=1_000)
    debts = HeadTaxDebtStore()

    result = await charge_daily_head_tax(
        ledger,
        debts,
        payer_agent_id="adult",
        head_agent_ids=["adult"],
        tax_rate_bps=1_000,
        tick=144,
    )

    assert result.sim_per_head == 100
    assert result.total_paid_sim == 100
    assert result.total_new_debt_sim == 0
    assert ledger.sim_balance("adult") == 900
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) == 100
    assert len(result.paid) == 1
    assert result.paid[0].on_behalf_of is None


@pytest.mark.asyncio
async def test_charges_a_household_of_three_with_per_head_attribution() -> None:
    ledger = await build_genesis_ledger(
        ["amara", "beni", "chiazo"], agent_usd_cents=200, sim_per_agent=0
    )
    ledger.accounts["amara"].sim_units = 1_000
    debts = HeadTaxDebtStore()

    result = await charge_daily_head_tax(
        ledger,
        debts,
        payer_agent_id="amara",
        head_agent_ids=["amara", "beni", "chiazo"],
        tax_rate_bps=1_000,
        tick=144,
        household_id="house-1",
    )

    assert result.total_paid_sim == 300
    assert ledger.sim_balance("amara") == 700
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) == 300

    on_behalf_of = {t.on_behalf_of for t in result.paid}
    assert on_behalf_of == {None, "beni", "chiazo"}
    for transaction in result.paid:
        assert transaction.from_account == "amara"
        assert transaction.to_account == TREASURY_ACCOUNT_ID
        assert transaction.amount == 100


@pytest.mark.asyncio
async def test_zero_rate_moves_no_money() -> None:
    ledger = await build_genesis_ledger(["adult"], agent_usd_cents=200, sim_per_agent=500)
    debts = HeadTaxDebtStore()

    result = await charge_daily_head_tax(
        ledger,
        debts,
        payer_agent_id="adult",
        head_agent_ids=["adult"],
        tax_rate_bps=0,
        tick=144,
    )

    assert result.paid == []
    assert result.new_debts == []
    assert ledger.sim_balance("adult") == 500


@pytest.mark.asyncio
async def test_shortfall_pays_what_it_can_and_debts_the_rest() -> None:
    ledger = await build_genesis_ledger(
        ["amara", "beni", "chiazo"], agent_usd_cents=200, sim_per_agent=0
    )
    ledger.accounts["amara"].sim_units = 150
    debts = HeadTaxDebtStore()
    sink = RecordingEventSink()

    result = await charge_daily_head_tax(
        ledger,
        debts,
        payer_agent_id="amara",
        head_agent_ids=["amara", "beni", "chiazo"],
        tax_rate_bps=1_000,
        tick=144,
        household_id="house-1",
        event_sink=sink,
    )

    assert result.total_paid_sim == 100
    assert result.total_new_debt_sim == 200
    assert ledger.sim_balance("amara") == 50
    assert ledger.sim_balance("amara") >= 0

    debtors = {d.head_agent_id for d in result.new_debts}
    assert debtors == {"beni", "chiazo"}
    assert await debts.outstanding("amara") == 200
    assert [e.type for e in sink.events] == [
        "economy.head_tax_debt.accrued",
        "economy.head_tax_debt.accrued",
    ]


@pytest.mark.asyncio
async def test_insufficient_funds_never_leaks_out_of_charge_daily_head_tax() -> None:
    """Ledger.move() raises InsufficientFundsError internally; this module
    must catch it and turn it into a recorded debt, never let it escape."""
    ledger = await build_genesis_ledger(["adult"], agent_usd_cents=200, sim_per_agent=0)
    debts = HeadTaxDebtStore()

    result = await charge_daily_head_tax(
        ledger,
        debts,
        payer_agent_id="adult",
        head_agent_ids=["adult"],
        tax_rate_bps=BPS_DENOMINATOR,
        tick=144,
    )

    assert result.paid == []
    assert result.total_new_debt_sim == HEAD_TAX_BASE_SIM
    assert await debts.outstanding("adult") == HEAD_TAX_BASE_SIM


@pytest.mark.asyncio
async def test_debt_still_accrues_with_an_absent_event_sink() -> None:
    ledger = await build_genesis_ledger(["adult"], agent_usd_cents=200, sim_per_agent=0)
    debts = HeadTaxDebtStore()

    result = await charge_daily_head_tax(
        ledger,
        debts,
        payer_agent_id="adult",
        head_agent_ids=["adult"],
        tax_rate_bps=BPS_DENOMINATOR,
        tick=144,
    )

    assert result.total_new_debt_sim == HEAD_TAX_BASE_SIM


@pytest.mark.asyncio
async def test_debt_accrues_across_multiple_days_for_the_same_debtor() -> None:
    debts = HeadTaxDebtStore()
    await debts.accrue(
        debtor_agent_id="amara", head_agent_id="beni", amount_sim=100, tick=144, game_day=1
    )
    await debts.accrue(
        debtor_agent_id="amara", head_agent_id="beni", amount_sim=100, tick=288, game_day=2
    )

    assert await debts.outstanding("amara") == 200
    events = await debts.list_events("amara")
    assert len(events) == 2
    assert all(e.kind is HeadTaxDebtEventKind.ACCRUED for e in events)


@pytest.mark.asyncio
async def test_all_outstanding_lists_only_nonzero_debtors() -> None:
    debts = HeadTaxDebtStore()
    await debts.accrue(
        debtor_agent_id="amara", head_agent_id="beni", amount_sim=100, tick=144, game_day=1
    )
    await debts.accrue(
        debtor_agent_id="kenji", head_agent_id="kenji", amount_sim=50, tick=144, game_day=1
    )
    await debts.repay(debtor_agent_id="kenji", amount_sim=50, tick=288, game_day=2)

    assert await debts.all_outstanding() == {"amara": 100}


@pytest.mark.asyncio
async def test_snapshot_restore_round_trips_balances_and_events() -> None:
    debts = HeadTaxDebtStore()
    await debts.accrue(
        debtor_agent_id="amara", head_agent_id="beni", amount_sim=100, tick=144, game_day=1
    )

    snapshot = debts.snapshot()

    restored = HeadTaxDebtStore()
    restored.restore(snapshot)

    assert await restored.outstanding("amara") == 100
    events = await restored.list_events()
    assert len(events) == 1
    assert events[0].debtor_agent_id == "amara"
    assert events[0].head_agent_id == "beni"


@pytest.mark.asyncio
async def test_repay_moves_real_sim_and_shrinks_the_debt() -> None:
    ledger = await build_genesis_ledger(["amara"], agent_usd_cents=200, sim_per_agent=500)
    debts = HeadTaxDebtStore()
    await debts.accrue(
        debtor_agent_id="amara", head_agent_id="beni", amount_sim=200, tick=144, game_day=1
    )

    event = await repay_head_tax_debt(
        ledger, debts, debtor_agent_id="amara", amount_sim=150, tick=288
    )

    assert event.kind is HeadTaxDebtEventKind.REPAID
    assert event.amount_sim == 150
    assert ledger.sim_balance("amara") == 350
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) == 150
    assert await debts.outstanding("amara") == 50


@pytest.mark.asyncio
async def test_repay_with_no_outstanding_debt_raises() -> None:
    ledger = await build_genesis_ledger(["amara"], agent_usd_cents=200, sim_per_agent=500)
    debts = HeadTaxDebtStore()

    with pytest.raises(NoOutstandingDebtError):
        await repay_head_tax_debt(ledger, debts, debtor_agent_id="amara", amount_sim=10, tick=144)


@pytest.mark.asyncio
async def test_repay_more_than_owed_raises_and_moves_no_money() -> None:
    ledger = await build_genesis_ledger(["amara"], agent_usd_cents=200, sim_per_agent=500)
    debts = HeadTaxDebtStore()
    await debts.accrue(
        debtor_agent_id="amara", head_agent_id="beni", amount_sim=50, tick=144, game_day=1
    )

    with pytest.raises(DebtOverpaymentError):
        await repay_head_tax_debt(ledger, debts, debtor_agent_id="amara", amount_sim=200, tick=288)

    assert ledger.sim_balance("amara") == 500
    assert await debts.outstanding("amara") == 50


@pytest.mark.asyncio
async def test_repay_when_debtor_cannot_afford_it_raises_and_leaves_debt_untouched() -> None:
    ledger = await build_genesis_ledger(["amara"], agent_usd_cents=200, sim_per_agent=10)
    debts = HeadTaxDebtStore()
    await debts.accrue(
        debtor_agent_id="amara", head_agent_id="beni", amount_sim=200, tick=144, game_day=1
    )

    with pytest.raises(InsufficientFundsError):
        await repay_head_tax_debt(ledger, debts, debtor_agent_id="amara", amount_sim=200, tick=288)

    assert ledger.sim_balance("amara") == 10
    assert await debts.outstanding("amara") == 200


@pytest.mark.asyncio
async def test_view_discloses_the_rule_and_the_numbers() -> None:
    debts = HeadTaxDebtStore()
    await debts.accrue(
        debtor_agent_id="amara", head_agent_id="beni", amount_sim=100, tick=144, game_day=1
    )

    view = await head_tax_view(
        debts,
        debtor_agent_id="amara",
        head_agent_ids=["amara", "beni", "chiazo"],
        tax_rate_bps=1_000,
    )

    assert view.tax_rate_bps == 1_000
    assert view.sim_per_head == 100
    assert view.head_count == 3
    assert view.amount_owed_today_sim == 300
    assert view.outstanding_debt_sim == 100


@pytest.mark.asyncio
async def test_view_with_no_debt_reports_zero() -> None:
    debts = HeadTaxDebtStore()

    view = await head_tax_view(
        debts, debtor_agent_id="amara", head_agent_ids=["amara"], tax_rate_bps=0
    )

    assert view.sim_per_head == 0
    assert view.amount_owed_today_sim == 0
    assert view.outstanding_debt_sim == 0
