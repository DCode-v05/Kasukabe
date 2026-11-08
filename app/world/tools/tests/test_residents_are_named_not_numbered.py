"""A resident may name the person or the shop it means, and be understood.

Runs 28 and 28b addressed people by number. In run 28b, 367 tool-call
parameters named someone by id - `seller_id: "n07"`, `counterparty_agent_id:
"n06"` - and 42 of those calls failed (11%, against 5% for names). The
commonest failure, 18 times in run 28b alone:

    buy(seller_id="n07", item="bread", quantity=2)
    -> "n07 needs 2 bread, has 0"

`n07` is Marta Thorne. Her own hands were empty; her bakery's shelf held
bread, a few steps away or in the very room. Three things made that call
pointless, and each is fixed here:

1. A resident thinks in names, and a person parameter accepted only an id.
   Now a full name, or a first name that is unique, is resolved to the id
   before the call is validated - for every tool, in one place.
2. Naming a shopkeeper did not reach the shop. Now, standing in the shop, a
   `buy` from its owner or staff is a `buy` from the shop.
3. Refusals spoke in numbers. Now an unknown or ambiguous name is refused
   with the people it could have meant, by name.
"""

from __future__ import annotations

import pytest

from world.tools.errors import ToolError
from world.tools.models import Business
from world.tools.tests.conftest import in_conversation, spawn_agent

HERE = "Kasukabe Farmland"
SHOP = "b_bakery"


async def _neighbours(world) -> None:
    for agent_id in ("n07", "n13", "n18"):
        await spawn_agent(world, agent_id, HERE)
    world.agent_names.update({"n07": "Marta Thorne", "n13": "Junia Vance", "n18": "Tova Okonjo"})


async def _bakery(world, registry, *, at: str = HERE) -> None:
    world.businesses[SHOP] = Business(
        business_id=SHOP, name="Thorne's Bakery", business_type="bakery",
        owner_agent_id="n07", location_id=HERE,
    )
    await world.ledger.register_business(SHOP, "n07", fee_sim=1, tick=world.tick())
    world.inventory.credit(SHOP, "bread", 5)
    await registry.execute(world, "n07", "post_price", {
        "item": "bread", "ask_sim": 15, "business_id": SHOP,
    })
    world.businesses[SHOP] = world.businesses[SHOP].model_copy(update={"location_id": at})


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["Tova Okonjo", "tova okonjo", "Tova"])
async def test_a_person_can_be_named(world, registry, who):
    await _neighbours(world)
    before = world.ledger.sim_balance("n18")

    await registry.execute(world, "n07", "transfer", {"to_agent_id": who, "amount_sim": 150})

    assert world.ledger.sim_balance("n18") == before + 150


@pytest.mark.asyncio
async def test_a_name_two_people_share_is_refused_with_both_names(world, registry):
    await _neighbours(world)
    await spawn_agent(world, "n19", HERE)
    world.agent_names["n19"] = "Tova Smith"

    with pytest.raises(ToolError) as refused:
        await registry.execute(world, "n07", "transfer", {"to_agent_id": "Tova", "amount_sim": 10})

    assert "Tova Okonjo" in str(refused.value) and "Tova Smith" in str(refused.value), str(refused.value)


@pytest.mark.asyncio
async def test_an_unknown_name_is_refused_with_the_people_here(world, registry):
    await _neighbours(world)

    with pytest.raises(ToolError) as refused:
        await registry.execute(world, "n07", "transfer", {"to_agent_id": "Nobody Here", "amount_sim": 10})

    assert "Junia Vance" in str(refused.value), str(refused.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("seller", ["Marta Thorne", "n07"])
async def test_buying_from_the_shopkeeper_in_her_shop_buys_from_the_shop(world, registry, seller):
    """Run 28b's commonest failed call, verbatim, and its named form."""
    await _neighbours(world)
    await _bakery(world, registry)

    in_conversation(world, "n13", "n07")
    bought = await registry.execute(world, "n13", "buy", {"seller_id": seller, "item": "bread", "quantity": 1})

    assert world.inventory.quantity("n13", "bread") == 1
    assert world.inventory.quantity(SHOP, "bread") == 4
    assert "Thorne's Bakery" in bought.result["from"], bought.result


@pytest.mark.asyncio
async def test_a_shopkeeper_away_from_her_shop_says_where_it_is(world, registry):
    """Standing with Marta on the farmland, her bakery elsewhere: the refusal
    names the shop that sells it rather than her empty hands."""
    await _neighbours(world)
    await _bakery(world, registry, at="Kasukabe Market Square")

    with pytest.raises(ToolError) as refused:
        await registry.execute(world, "n13", "buy", {"seller_id": "Marta Thorne", "item": "bread", "quantity": 1})

    assert "Thorne's Bakery" in str(refused.value), str(refused.value)
