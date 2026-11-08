"""Sim-currency day-to-day economy: wages, taxes, pensions (DESIGN.md section 4)."""

from __future__ import annotations

import pytest

from world.ledger.errors import PensionIneligibleError
from world.ledger.money import PENSION_GOVERNMENT_PCT, PENSION_PRIVATE_PCT, TREASURY_ACCOUNT_ID
from world.ledger.tests.ledger_factories import build_genesis_ledger


@pytest.mark.asyncio
async def test_pay_wage_from_a_business_account_to_its_employee() -> None:
    ledger = await build_genesis_ledger(["owner", "employee"], agent_usd_cents=200)
    ledger.accounts["owner"].sim_units = 10_000
    await ledger.register_business("bakery", "owner", fee_sim=1_000, tick=0)
    # Stand in for prior sales revenue, which is out of this module's
    # scope (trade/buy/sell primitives belong to the tool registry) but
    # a business still needs sim on hand to pay a wage out of.
    ledger.accounts["bakery"].sim_units = 5_000

    await ledger.pay_wage("bakery", "employee", 500, tick=1)

    assert ledger.sim_balance("bakery") == 4_500
    assert ledger.sim_balance("employee") == 500


@pytest.mark.asyncio
async def test_pay_wage_from_the_government() -> None:
    ledger = await build_genesis_ledger(["officer"], agent_usd_cents=200)
    ledger.accounts[TREASURY_ACCOUNT_ID].sim_units = 5_000

    await ledger.pay_wage(TREASURY_ACCOUNT_ID, "officer", 800, tick=1)

    assert ledger.sim_balance("officer") == 800
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) == 4_200


@pytest.mark.asyncio
async def test_collect_tax_moves_sim_to_treasury() -> None:
    ledger = await build_genesis_ledger(["a1"], agent_usd_cents=200, sim_per_agent=1_000)

    await ledger.collect_tax("a1", 100, tick=1)

    assert ledger.sim_balance("a1") == 900
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) == 100


@pytest.mark.asyncio
async def test_private_pension_pays_25_percent_of_last_salary() -> None:
    ledger = await build_genesis_ledger(["retiree"], agent_usd_cents=200)
    ledger.accounts[TREASURY_ACCOUNT_ID].sim_units = 100_000

    transaction = await ledger.pay_pension("retiree", 72, 4_000, "private", tick=1)

    assert transaction.amount == 1_000  # 25% of 4,000
    assert PENSION_PRIVATE_PCT == 25
    assert ledger.sim_balance("retiree") == 1_000


@pytest.mark.asyncio
async def test_government_pension_pays_50_percent_of_last_salary() -> None:
    ledger = await build_genesis_ledger(["retiree"], agent_usd_cents=200)
    ledger.accounts[TREASURY_ACCOUNT_ID].sim_units = 100_000

    transaction = await ledger.pay_pension("retiree", 70, 4_000, "government", tick=1)

    assert transaction.amount == 2_000  # 50% of 4,000
    assert PENSION_GOVERNMENT_PCT == 50
    assert ledger.sim_balance("retiree") == 2_000


@pytest.mark.asyncio
async def test_pension_rejects_agents_under_the_minimum_age() -> None:
    ledger = await build_genesis_ledger(["worker"], agent_usd_cents=200)
    ledger.accounts[TREASURY_ACCOUNT_ID].sim_units = 100_000

    with pytest.raises(PensionIneligibleError):
        await ledger.pay_pension("worker", 69, 4_000, "private", tick=1)
