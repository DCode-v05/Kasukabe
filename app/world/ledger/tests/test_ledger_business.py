"""Business registration: fee to treasury, sim-only account (DESIGN.md section 3)."""

from __future__ import annotations

import pytest

from world.ledger.errors import (
    AccountNotFoundError,
    CurrencyNotSupportedError,
    DuplicateAccountError,
    InsufficientFundsError,
)
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import TREASURY_ACCOUNT_ID
from world.ledger.tests.ledger_factories import build_genesis_ledger


@pytest.mark.asyncio
async def test_register_business_charges_fee_and_opens_sim_only_account() -> None:
    ledger = await build_genesis_ledger(["owner"], agent_usd_cents=200, sim_per_agent=10_000)

    await ledger.register_business("bakery", "owner", fee_sim=1_500, tick=0)

    assert ledger.sim_balance("owner") == 8_500
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) == 1_500
    assert ledger.sim_balance("bakery") == 0
    assert ledger.usd_balance("owner") == 200


@pytest.mark.asyncio
async def test_register_business_rejects_when_owner_cannot_afford_the_fee() -> None:
    ledger = await build_genesis_ledger(["owner"], agent_usd_cents=200, sim_per_agent=100)

    with pytest.raises(InsufficientFundsError):
        await ledger.register_business("bakery", "owner", fee_sim=1_500, tick=0)

    with pytest.raises(AccountNotFoundError):
        ledger.get_account("bakery")


@pytest.mark.asyncio
async def test_register_business_rejects_duplicate_business_id() -> None:
    ledger = await build_genesis_ledger(["owner"], agent_usd_cents=200, sim_per_agent=10_000)
    await ledger.register_business("bakery", "owner", fee_sim=100, tick=0)

    with pytest.raises(DuplicateAccountError):
        await ledger.register_business("bakery", "owner", fee_sim=100, tick=1)


@pytest.mark.asyncio
async def test_business_account_never_holds_usd() -> None:
    ledger = await build_genesis_ledger(["owner"], agent_usd_cents=200, sim_per_agent=10_000)
    await ledger.register_business("bakery", "owner", fee_sim=100, tick=0)

    with pytest.raises(CurrencyNotSupportedError):
        await ledger.move(
            from_account_id="owner",
            to_account_id="bakery",
            amount=50,
            currency=Currency.USD,
            reason=TransactionReason.TRANSFER,
            tick=1,
        )
