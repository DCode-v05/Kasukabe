"""Death and estates (DESIGN.md section 5: "estates become inheritance")."""

from __future__ import annotations

import pytest

from world.ledger.errors import AccountClosedError, AccountNotFoundError
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import TREASURY_ACCOUNT_ID
from world.ledger.tests.ledger_factories import build_genesis_ledger


@pytest.mark.asyncio
async def test_single_heir_receives_the_full_estate() -> None:
    ledger = await build_genesis_ledger(
        ["deceased", "heir"], agent_usd_cents=200, sim_per_agent=1_000
    )

    transactions = await ledger.settle_inheritance("deceased", ["heir"], tick=10)

    assert ledger.usd_balance("heir") == 400
    assert ledger.sim_balance("heir") == 2_000
    assert ledger.usd_balance("deceased") == 0
    assert ledger.sim_balance("deceased") == 0
    assert len(transactions) == 2


@pytest.mark.asyncio
async def test_estate_splits_evenly_across_heirs() -> None:
    ledger = await build_genesis_ledger(
        ["deceased", "heir1", "heir2", "heir3"], agent_usd_cents=201, sim_per_agent=0
    )
    treasury_before = ledger.usd_balance(TREASURY_ACCOUNT_ID)

    await ledger.settle_inheritance("deceased", ["heir1", "heir2", "heir3"], tick=10)

    assert ledger.usd_balance("heir1") == 201 + 67
    assert ledger.usd_balance("heir2") == 201 + 67
    assert ledger.usd_balance("heir3") == 201 + 67
    assert ledger.usd_balance(TREASURY_ACCOUNT_ID) == treasury_before


@pytest.mark.asyncio
async def test_estate_remainder_escheats_when_split_is_uneven() -> None:
    ledger = await build_genesis_ledger(
        ["deceased", "heir1", "heir2", "heir3"], agent_usd_cents=200, sim_per_agent=100
    )
    treasury_sim_before = ledger.sim_balance(TREASURY_ACCOUNT_ID)

    await ledger.settle_inheritance("deceased", ["heir1", "heir2", "heir3"], tick=10)

    assert ledger.sim_balance("heir1") == 100 + 33
    assert ledger.sim_balance("heir2") == 100 + 33
    assert ledger.sim_balance("heir3") == 100 + 33
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) == treasury_sim_before + 1


@pytest.mark.asyncio
async def test_no_heirs_escheats_the_whole_estate_to_treasury() -> None:
    ledger = await build_genesis_ledger(["deceased"], agent_usd_cents=200, sim_per_agent=500)
    treasury_usd_before = ledger.usd_balance(TREASURY_ACCOUNT_ID)
    treasury_sim_before = ledger.sim_balance(TREASURY_ACCOUNT_ID)

    await ledger.settle_inheritance("deceased", [], tick=10)

    assert ledger.usd_balance(TREASURY_ACCOUNT_ID) == treasury_usd_before + 200
    assert ledger.sim_balance(TREASURY_ACCOUNT_ID) == treasury_sim_before + 500


@pytest.mark.asyncio
async def test_deceased_account_is_closed_after_settlement() -> None:
    ledger = await build_genesis_ledger(["deceased", "heir"], agent_usd_cents=200)

    await ledger.settle_inheritance("deceased", ["heir"], tick=10)

    assert ledger.get_account("deceased").closed is True
    with pytest.raises(AccountClosedError):
        await ledger.move(
            from_account_id="heir",
            to_account_id="deceased",
            amount=1,
            currency=Currency.SIM,
            reason=TransactionReason.TRANSFER,
            tick=11,
        )


@pytest.mark.asyncio
async def test_settling_an_already_settled_estate_raises() -> None:
    ledger = await build_genesis_ledger(["deceased", "heir"], agent_usd_cents=200)
    await ledger.settle_inheritance("deceased", ["heir"], tick=10)

    with pytest.raises(AccountClosedError):
        await ledger.settle_inheritance("deceased", ["heir"], tick=11)


@pytest.mark.asyncio
async def test_unknown_heir_raises_before_any_money_moves() -> None:
    ledger = await build_genesis_ledger(["deceased"], agent_usd_cents=200, sim_per_agent=500)

    with pytest.raises(AccountNotFoundError):
        await ledger.settle_inheritance("deceased", ["nobody"], tick=10)

    assert ledger.usd_balance("deceased") == 200
    assert ledger.sim_balance("deceased") == 500
    assert ledger.get_account("deceased").closed is False
