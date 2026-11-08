"""Edge cases the specific-operation tests do not each need their own file for."""

from __future__ import annotations

import pytest

from world.ledger.errors import (
    AccountNotFoundError,
    DuplicateAccountError,
    InsufficientFundsError,
    InvalidAmountError,
    SameAccountTransferError,
)
from world.ledger.models import Currency, TransactionReason
from world.ledger.tests.ledger_factories import build_genesis_ledger


@pytest.mark.asyncio
async def test_zero_amount_transfer_rejected() -> None:
    ledger = await build_genesis_ledger(["a1", "a2"], agent_usd_cents=200)

    with pytest.raises(InvalidAmountError):
        await ledger.move(
            from_account_id="a1",
            to_account_id="a2",
            amount=0,
            currency=Currency.USD,
            reason=TransactionReason.TRANSFER,
            tick=1,
        )


@pytest.mark.asyncio
async def test_negative_amount_transfer_rejected() -> None:
    ledger = await build_genesis_ledger(["a1", "a2"], agent_usd_cents=200)

    with pytest.raises(InvalidAmountError):
        await ledger.move(
            from_account_id="a1",
            to_account_id="a2",
            amount=-5,
            currency=Currency.USD,
            reason=TransactionReason.TRANSFER,
            tick=1,
        )


@pytest.mark.asyncio
async def test_self_transfer_rejected() -> None:
    ledger = await build_genesis_ledger(["a1"], agent_usd_cents=200)

    with pytest.raises(SameAccountTransferError):
        await ledger.move(
            from_account_id="a1",
            to_account_id="a1",
            amount=10,
            currency=Currency.USD,
            reason=TransactionReason.TRANSFER,
            tick=1,
        )


@pytest.mark.asyncio
async def test_unknown_account_rejected() -> None:
    ledger = await build_genesis_ledger(["a1"], agent_usd_cents=200)

    with pytest.raises(AccountNotFoundError):
        await ledger.move(
            from_account_id="a1",
            to_account_id="ghost",
            amount=10,
            currency=Currency.USD,
            reason=TransactionReason.TRANSFER,
            tick=1,
        )


@pytest.mark.asyncio
async def test_overdraft_rejected_and_leaves_balances_untouched() -> None:
    ledger = await build_genesis_ledger(["a1", "a2"], agent_usd_cents=200)

    with pytest.raises(InsufficientFundsError):
        await ledger.move(
            from_account_id="a1",
            to_account_id="a2",
            amount=201,
            currency=Currency.USD,
            reason=TransactionReason.TRANSFER,
            tick=1,
        )

    assert ledger.usd_balance("a1") == 200
    assert ledger.usd_balance("a2") == 200


@pytest.mark.asyncio
async def test_duplicate_agent_id_rejected() -> None:
    ledger = await build_genesis_ledger(["a1"], agent_usd_cents=200)

    with pytest.raises(DuplicateAccountError):
        ledger.open_agent("a1")


@pytest.mark.asyncio
async def test_transactions_are_appended_in_order_with_monotonic_ticks() -> None:
    ledger = await build_genesis_ledger(["a1", "a2"], agent_usd_cents=200)
    start = len(ledger.transactions)

    await ledger.move(
        from_account_id="a1",
        to_account_id="a2",
        amount=10,
        currency=Currency.USD,
        reason=TransactionReason.TRANSFER,
        tick=5,
    )
    await ledger.move(
        from_account_id="a2",
        to_account_id="a1",
        amount=5,
        currency=Currency.USD,
        reason=TransactionReason.TRANSFER,
        tick=6,
    )

    new_rows = ledger.transactions[start:]
    assert [t.tick for t in new_rows] == [5, 6]
    assert [t.amount for t in new_rows] == [10, 5]
