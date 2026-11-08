"""Parent-debit: a minor's USD spends auto-charge the parent (DESIGN.md section 4)."""

from __future__ import annotations

import pytest

from world.ledger.core import Ledger
from world.ledger.errors import ParentNotConfiguredError
from world.ledger.money import BURNED_ACCOUNT_ID, TOTAL_USD_CENTS


@pytest.mark.asyncio
async def test_minor_inference_burn_charges_the_parent() -> None:
    ledger = Ledger()
    await ledger.genesis(
        ["parent", "child"], agent_usd_cents=200, treasury_usd_cents=TOTAL_USD_CENTS - 400
    )
    ledger.accounts["child"].is_minor = True
    ledger.accounts["child"].parent_id = "parent"

    transaction = await ledger.burn_for_inference("child", 50, tick=3)

    assert transaction.from_account == "parent"
    assert transaction.to_account == BURNED_ACCOUNT_ID
    assert transaction.on_behalf_of == "child"
    assert ledger.usd_balance("parent") == 150
    assert ledger.usd_balance("child") == 200  # untouched
    assert ledger.usd_balance(BURNED_ACCOUNT_ID) == 50


@pytest.mark.asyncio
async def test_adult_inference_burn_charges_self() -> None:
    ledger = Ledger()
    await ledger.genesis(["adult"], agent_usd_cents=200, treasury_usd_cents=TOTAL_USD_CENTS - 200)

    transaction = await ledger.burn_for_inference("adult", 50, tick=3)

    assert transaction.from_account == "adult"
    assert transaction.on_behalf_of is None
    assert ledger.usd_balance("adult") == 150


@pytest.mark.asyncio
async def test_minor_without_a_parent_on_file_raises_at_open() -> None:
    ledger = Ledger()
    await ledger.genesis(["parent"], agent_usd_cents=200, treasury_usd_cents=TOTAL_USD_CENTS - 200)

    with pytest.raises(ParentNotConfiguredError):
        ledger.open_agent("orphan", is_minor=True)


@pytest.mark.asyncio
async def test_generic_usd_debit_also_resolves_parent_debit() -> None:
    """DESIGN.md's parent-debit rule covers any minor USD spend, not
    just inference; debit_agent_usd is the general primitive
    burn_for_inference specialises."""
    ledger = Ledger()
    await ledger.genesis(
        ["parent", "child", "vendor"],
        agent_usd_cents=200,
        treasury_usd_cents=TOTAL_USD_CENTS - 600,
    )
    ledger.accounts["child"].is_minor = True
    ledger.accounts["child"].parent_id = "parent"

    transaction = await ledger.debit_agent_usd("child", "vendor", 25, tick=1)

    assert transaction.from_account == "parent"
    assert transaction.on_behalf_of == "child"
    assert ledger.usd_balance("parent") == 175
    assert ledger.usd_balance("vendor") == 225
