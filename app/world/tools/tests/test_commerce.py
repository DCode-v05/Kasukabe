from __future__ import annotations

import pytest

from world.tools.errors import InsufficientInventoryError, LocationGateError, NotFoundError
from world.tools.models import Business
from world.tools.tests.conftest import action_cost, in_conversation, spawn_agent


async def open_business(
    world, business_id: str, owner_id: str, location_id: str, starting_sim: int = 10_000
) -> Business:
    await world.ledger.register_business(business_id, owner_id, fee_sim=100, tick=world.tick())
    if starting_sim > 0:
        # register_business opens the account at a zero sim balance
        # (world.ledger.core.Ledger.open_business); a business needs
        # working capital to ever pay a seller, so tests that exercise
        # `sell` fund it here the same way a real business would build up
        # a balance from its own earlier sales.
        await world.ledger.mint_sim(business_id, starting_sim, tick=world.tick())
    business = Business(
        business_id=business_id, name=business_id, business_type="grocery", owner_agent_id=owner_id,
        location_id=location_id,
    )
    world.businesses[business_id] = business
    return business


async def post(world, registry, owner_id: str, business_id: str, item: str, **prices):
    """A4: nothing is for sale until a price is posted, so every buy/sell
    test now has to open the shop first. That is the change, not test
    noise: `buy` used to take the BUYER's own price and check it against
    nothing."""
    await registry.execute(world, owner_id, "post_price", {
        "item": item, "on_behalf_of_business": business_id, **prices,
    })


@pytest.mark.asyncio
async def test_buy_moves_money_and_item(world, registry) -> None:
    await spawn_agent(world, "owner", "shop")
    await spawn_agent(world, "buyer", "shop")
    in_conversation(world, "buyer", "owner")   # a sale is agreed in conversation
    await open_business(world, "biz-1", "owner", "shop", starting_sim=0)
    world.inventory.credit("biz-1", "bread", 10)

    await post(world, registry, "owner", "biz-1", "bread", ask_sim=100)

    result = await registry.execute(
        world, "buyer", "buy", {"seller_id": "biz-1", "item": "bread", "quantity": 3}
    )

    assert result.result["total_price_sim"] == 300
    assert result.result["unit_price_sim"] == 100
    assert world.inventory.quantity("buyer", "bread") == 3
    assert world.inventory.quantity("biz-1", "bread") == 7
    assert world.ledger.sim_balance("biz-1") == 300


@pytest.mark.asyncio
async def test_buy_wrong_location_gated(world, registry) -> None:
    await spawn_agent(world, "owner", "shop")
    await spawn_agent(world, "buyer", "elsewhere")
    await open_business(world, "biz-1", "owner", "shop")
    world.inventory.credit("biz-1", "bread", 10)

    await post(world, registry, "owner", "biz-1", "bread", ask_sim=10)

    with pytest.raises(LocationGateError):
        await registry.execute(
            world, "buyer", "buy", {"seller_id": "biz-1", "item": "bread", "quantity": 1}
        )


@pytest.mark.asyncio
async def test_buy_more_than_in_stock_never_charges_buyer(world, registry) -> None:
    await spawn_agent(world, "owner", "shop")
    await spawn_agent(world, "buyer", "shop")
    in_conversation(world, "buyer", "owner")
    await open_business(world, "biz-1", "owner", "shop")
    world.inventory.credit("biz-1", "bread", 1)
    await post(world, registry, "owner", "biz-1", "bread", ask_sim=10)
    balance_before = world.ledger.sim_balance("buyer")

    with pytest.raises(InsufficientInventoryError):
        await registry.execute(
            world, "buyer", "buy", {"seller_id": "biz-1", "item": "bread", "quantity": 5}
        )

    assert world.ledger.sim_balance("buyer") == balance_before


@pytest.mark.asyncio
async def test_sell_moves_item_to_business_and_pays_seller(world, registry) -> None:
    await spawn_agent(world, "owner", "shop")
    await spawn_agent(world, "seller", "shop")
    in_conversation(world, "seller", "owner")
    await open_business(world, "biz-1", "owner", "shop")
    world.inventory.credit("seller", "wheat", 20)

    await post(world, registry, "owner", "biz-1", "wheat", bid_sim=20)

    result = await registry.execute(
        world, "seller", "sell", {"buyer_id": "biz-1", "item": "wheat", "quantity": 5}
    )

    assert result.result["total_price_sim"] == 100
    assert result.result["unit_price_sim"] == 20
    assert world.inventory.quantity("seller", "wheat") == 15
    assert world.inventory.quantity("biz-1", "wheat") == 5


@pytest.mark.asyncio
async def test_transfer_moves_sim_currency(world, registry) -> None:
    await spawn_agent(world, "payer", "park")
    await spawn_agent(world, "payee", "cafe")
    payer_before = world.ledger.sim_balance("payer")
    payee_before = world.ledger.sim_balance("payee")

    result = await registry.execute(world, "payer", "transfer", {"to_agent_id": "payee", "amount_sim": 500})

    # C2 added `reason` to the result: an untagged transfer is `other`,
    # and nothing is repaid because nothing was lent.
    assert result.result == {"amount_sim": 500, "reason": "other", "repaid_sim": 0}
    assert world.ledger.sim_balance("payer") == payer_before - 500 - action_cost(
        "transfer", {"amount_sim": 500}
    )
    assert world.ledger.sim_balance("payee") == payee_before + 500


@pytest.mark.asyncio
async def test_transfer_to_unknown_agent_rejected(world, registry) -> None:
    await spawn_agent(world, "payer", "park")
    with pytest.raises(NotFoundError):
        await registry.execute(world, "payer", "transfer", {"to_agent_id": "ghost", "amount_sim": 10})


@pytest.mark.asyncio
async def test_store_then_withdraw_item_round_trips(world, registry) -> None:
    await spawn_agent(world, "hoarder", "warehouse-district")
    world.inventory.credit("hoarder", "gold_jewelry", 3)

    await registry.execute(
        world, "hoarder", "store_item", {"storage_id": "stash-1", "item": "gold_jewelry", "quantity": 3}
    )
    assert world.inventory.quantity("hoarder", "gold_jewelry") == 0
    assert world.inventory.quantity("stash-1", "gold_jewelry") == 3
    assert world.storages["stash-1"].location_id == "warehouse-district"

    await registry.execute(
        world, "hoarder", "withdraw_item", {"storage_id": "stash-1", "item": "gold_jewelry", "quantity": 2}
    )
    assert world.inventory.quantity("hoarder", "gold_jewelry") == 2
    assert world.inventory.quantity("stash-1", "gold_jewelry") == 1


@pytest.mark.asyncio
async def test_withdraw_from_unknown_storage_rejected(world, registry) -> None:
    await spawn_agent(world, "hoarder", "warehouse-district")
    with pytest.raises(NotFoundError):
        await registry.execute(
            world, "hoarder", "withdraw_item", {"storage_id": "no-such-stash", "item": "bread", "quantity": 1}
        )


@pytest.mark.asyncio
async def test_withdraw_requires_visiting_the_storage(world, registry) -> None:
    await spawn_agent(world, "hoarder", "warehouse-district")
    world.inventory.credit("hoarder", "planks", 5)
    await registry.execute(world, "hoarder", "store_item", {"storage_id": "stash-1", "item": "planks", "quantity": 5})

    from world.tools.tests.conftest import move_agent

    await move_agent(world, "hoarder", "far-away")
    with pytest.raises(LocationGateError):
        await registry.execute(
            world, "hoarder", "withdraw_item", {"storage_id": "stash-1", "item": "planks", "quantity": 1}
        )
