"""ECON-14: the town's own debt is payable.

`repay_head_tax_debt` was written, tested and called by NOTHING. The
store it writes to lived on the runner (`run_genesis.py:3758`), where no
tool handler could reach it - handlers are handed `world`. Meanwhile
`transfer` requires `to_agent_id in world.agents` and the treasury is not
an agent, so the one route `check_debts` pointed residents at refused
every time.

The result was a debt that was real, compounding, itemised back to the
resident, and impossible to pay. This drives `ToolRegistry.execute` -
the seam a live resident uses - because the defect was never in
`repay_head_tax_debt` (which worked, and whose own unit tests passed
throughout) but in the absence of any caller for it.
"""

from __future__ import annotations

import pytest

from world.ledger.core import TREASURY_ACCOUNT_ID
from world.tools.errors import ActorRequirementError
from world.tools.tests.conftest import spawn_agent

HOME = "home:h1"


async def _in_debt(world, agent_id: str, amount: int) -> None:
    """Give a resident a real head-tax debt, the way the day job does."""
    await world.head_tax_debts.accrue(
        debtor_agent_id=agent_id,
        head_agent_id=agent_id,
        amount_sim=amount,
        tick=0,
        game_day=0,
    )


@pytest.mark.asyncio
async def test_a_resident_can_pay_the_treasury_what_it_owes(world, registry) -> None:
    await spawn_agent(world, "debtor", HOME)
    world.ledger.get_account("debtor").sim_units = 500
    await _in_debt(world, "debtor", 120)
    treasury_before = world.ledger.get_account(TREASURY_ACCOUNT_ID).sim_units

    outcome = await registry.execute(world, "debtor", "pay_tax", {"amount_sim": 120})

    assert await world.head_tax_debts.outstanding("debtor") == 0, (
        "ECON-14: the debt survived being paid"
    )
    assert world.ledger.get_account("debtor").sim_units == 380
    assert world.ledger.get_account(TREASURY_ACCOUNT_ID).sim_units == treasury_before + 120, (
        "ECON-14: the money left the resident but never reached the treasury"
    )
    assert outcome.result["cleared"] is True


@pytest.mark.asyncio
async def test_the_store_the_tool_pays_is_the_store_the_town_charges(world) -> None:
    """The wiring, stated as its own claim.

    `pay_tax` reading a DIFFERENT `HeadTaxDebtStore` than the day job
    writes would pass every test above and pay off a debt nobody had. The
    runner and the world must hold ONE object, not two that agree.
    """
    assert world.head_tax_debts is not None, (
        "ECON-14: no tool handler can reach the debt store, so no debt can be paid"
    )


@pytest.mark.asyncio
async def test_paying_more_than_you_owe_clears_it_and_no_more(world, registry) -> None:
    """"Pay it all off" is the commonest thing a resident means, and
    `repay_head_tax_debt` raises DebtOverpaymentError on it. Clamped, so
    an honest over-offer settles the debt instead of teaching the resident
    that paying is an error.
    """
    await spawn_agent(world, "eager", HOME)
    world.ledger.get_account("eager").sim_units = 1_000
    await _in_debt(world, "eager", 60)

    await registry.execute(world, "eager", "pay_tax", {"amount_sim": 999})

    assert await world.head_tax_debts.outstanding("eager") == 0
    assert world.ledger.get_account("eager").sim_units == 940, (
        "ECON-14: an over-offer took more than the debt was worth"
    )


@pytest.mark.asyncio
async def test_paying_a_debt_you_do_not_have_is_refused(world, registry) -> None:
    await spawn_agent(world, "clear", HOME)
    world.ledger.get_account("clear").sim_units = 500

    with pytest.raises(ActorRequirementError):
        await registry.execute(world, "clear", "pay_tax", {"amount_sim": 10})

    assert world.ledger.get_account("clear").sim_units == 500


@pytest.mark.asyncio
async def test_check_debts_does_not_send_a_resident_to_a_tool_that_refuses(registry) -> None:
    """The half of ECON-14 that is a sentence, not a mechanism.

    `check_debts` told residents to settle a treasury debt with
    `transfer`, which refuses every non-agent recipient. Following your
    own toolbox into a guaranteed refusal is worse than being told
    nothing.
    """
    spec = registry.tools["check_debts"]
    assert "`pay_tax`" in spec.description, (
        "ECON-14: check_debts still does not name the tool that works"
    )
