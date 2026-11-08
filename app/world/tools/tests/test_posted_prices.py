"""A3 and A4: a price on the door, and a `buy` that has to respect it.

From docs/kasukabetoolspec.md A3/A4 and the addendum's sections 3 and 4.

`buy` took the BUYER's own `unit_price_sim` and checked it against
nothing. A bakery could not have a price, only the baker's speech; a
customer could pay one sim a loaf; and price discovery had nothing to
converge to, which is why evaluation E2 - does the spread for one item
across shops narrow from day 1 to day 5 - could not be asked at all.

Nothing is for sale at genesis. Opening stock exists and stays unpriced
until somebody posts, so the tick of the town's first posted price is a
finding rather than a fixture. That is E1, and
`test_nothing_is_for_sale_at_genesis` is what keeps it honest.
"""

from __future__ import annotations

import pytest

from world.tools.errors import (
    ActorRequirementError,
    InsufficientInventoryError,
    LocationGateError,
    NotFoundError,
    ToolValidationError,
)
from world.tools.models import Business
from world.tools.state import WorldState

from .conftest import in_conversation, spawn_agent

BAKERY_AT = "Kasukabe Town Square"
GROCERY_AT = "Kasukabe Market"
FIELD = "Kasukabe Farmland"


async def _bakery(world: WorldState, *, owner: str = "n05") -> Business:
    await spawn_agent(world, owner, BAKERY_AT)
    world.agent_names[owner] = "Marta Thorne"
    business = Business(
        business_id="b_bakery",
        name="Thorne's Bakery",
        business_type="bakery",
        owner_agent_id=owner,
        location_id=BAKERY_AT,
    )
    world.businesses["b_bakery"] = business
    await world.ledger.register_business(
        "b_bakery", owner, fee_sim=1, tick=world.tick()
    )
    world.inventory.credit("b_bakery", "bread", 8)
    await world.ledger.mint_sim("b_bakery", 5_000, tick=world.tick())
    return business


@pytest.mark.asyncio
async def test_nothing_is_for_sale_at_genesis(world: WorldState, registry) -> None:
    """E1 has to be a measurement, not a fixture. A town seeded with prices
    has already answered the question the run is asking."""
    assert world.price_board == {}
    assert world.earliest_standing_post_tick() is None


@pytest.mark.asyncio
async def test_a_business_price_is_visible_to_a_customer_standing_in_the_shop(
    world: WorldState, registry
) -> None:
    """The spec's first named test."""
    await _bakery(world)
    await spawn_agent(world, "n09", BAKERY_AT)
    world.agent_names["n09"] = "Farid Vance"

    await registry.execute(world, "n05", "post_price", {
        "item": "bread", "ask_sim": 30, "on_behalf_of_business": "b_bakery",
    })

    outcome = await registry.execute(world, "n09", "look_around", {})
    prices = outcome.result["prices_here"]
    assert any("bread 30 to buy" in line for lines in prices.values() for line in lines)


@pytest.mark.asyncio
async def test_a_shops_price_hangs_there_when_the_owner_walks_out(
    world: WorldState, registry
) -> None:
    """A business's board is a sign, not the owner's presence. This is what
    makes "absent owner still sells" possible at all."""
    await _bakery(world)
    await spawn_agent(world, "n09", BAKERY_AT)
    await registry.execute(world, "n05", "post_price", {
        "item": "bread", "ask_sim": 30, "on_behalf_of_business": "b_bakery",
    })

    await world.event_bus.emit(
        "agent.moved", tick=1, payload={"agent_id": "n05", "location_id": FIELD}
    )

    outcome = await registry.execute(world, "n09", "look_around", {})
    assert outcome.result["prices_here"], "the sign came down when the baker left"


@pytest.mark.asyncio
async def test_an_individual_carries_their_offer_from_field_to_market(
    world: WorldState, registry
) -> None:
    """The spec's second named test. A person's price travels with them
    because a farmer with three cabbages is doing what a shop does, at a
    smaller scale."""
    await spawn_agent(world, "n06", FIELD)
    world.agent_names["n06"] = "Wren Thorne"
    world.inventory.credit("n06", "cabbage", 3)
    await registry.execute(world, "n06", "post_price", {"item": "cabbage", "ask_sim": 12})

    assert "n06" in world.prices_visible_at(FIELD)
    await world.event_bus.emit(
        "agent.moved", tick=2, payload={"agent_id": "n06", "location_id": GROCERY_AT}
    )
    assert "n06" in world.prices_visible_at(GROCERY_AT)
    assert "n06" not in world.prices_visible_at(FIELD)


@pytest.mark.asyncio
async def test_an_employee_can_post_for_the_shop_and_a_stranger_cannot(
    world: WorldState, registry
) -> None:
    """The spec's third named test. The same grant `hire_employee` gives, so
    a wage and a price key off one relationship rather than two."""
    business = await _bakery(world)
    await spawn_agent(world, "n14", BAKERY_AT)
    business.employee_agent_ids.add("n14")
    await spawn_agent(world, "n09", BAKERY_AT)

    await registry.execute(world, "n14", "post_price", {
        "item": "bread", "ask_sim": 28, "on_behalf_of_business": "b_bakery",
    })
    assert world.posted("b_bakery", "bread").ask_sim == 28

    with pytest.raises(ToolValidationError) as exc:
        await registry.execute(world, "n09", "post_price", {
            "item": "bread", "ask_sim": 1, "on_behalf_of_business": "b_bakery",
        })
    assert "on_behalf_of_business" in str(exc.value)


@pytest.mark.asyncio
async def test_a_shops_price_is_posted_on_the_premises(
    world: WorldState, registry
) -> None:
    await _bakery(world)
    await world.event_bus.emit(
        "agent.moved", tick=1, payload={"agent_id": "n05", "location_id": FIELD}
    )

    with pytest.raises(LocationGateError):
        await registry.execute(world, "n05", "post_price", {
            "item": "bread", "ask_sim": 30, "on_behalf_of_business": "b_bakery",
        })


@pytest.mark.asyncio
async def test_posting_neither_price_names_the_verb_that_takes_one_down(
    world: WorldState, registry
) -> None:
    await spawn_agent(world, "n06", FIELD)

    with pytest.raises(ToolValidationError) as exc:
        await registry.execute(world, "n06", "post_price", {"item": "cabbage"})

    assert "withdraw_price" in str(exc.value)


@pytest.mark.asyncio
async def test_reposting_replaces_and_says_what_it_replaced(
    world: WorldState, registry
) -> None:
    """Re-pricing is how haggling shows up on a board, so the previous
    number has to reach the trace - E2 reads exactly this."""
    await _bakery(world)
    await registry.execute(world, "n05", "post_price", {
        "item": "bread", "ask_sim": 30, "on_behalf_of_business": "b_bakery",
    })

    outcome = await registry.execute(world, "n05", "post_price", {
        "item": "bread", "ask_sim": 26, "on_behalf_of_business": "b_bakery",
    })

    assert outcome.result["replaced"] == {"ask_sim": 30, "bid_sim": None}
    posted = [e for e in outcome.events if e.type == "economy.price.posted"][0]
    assert posted.payload["previous_ask"] == 30
    assert posted.payload["ask_sim"] == 26


@pytest.mark.asyncio
async def test_withdrawing_a_price_that_was_never_posted(
    world: WorldState, registry
) -> None:
    await spawn_agent(world, "n06", FIELD)

    with pytest.raises(ToolValidationError) as exc:
        await registry.execute(world, "n06", "withdraw_price", {"item": "cabbage"})

    assert "no price is posted" in str(exc.value)


@pytest.mark.asyncio
async def test_a_buyer_pays_the_posted_price_not_their_own(
    world: WorldState, registry
) -> None:
    """The spec's first named test for A4, and the reason the parameter
    went away: there is no price to name."""
    await _bakery(world)
    await spawn_agent(world, "n09", BAKERY_AT)
    in_conversation(world, "n09", "n05")
    await registry.execute(world, "n05", "post_price", {
        "item": "bread", "ask_sim": 30, "on_behalf_of_business": "b_bakery",
    })
    before = world.ledger.sim_balance("n09")

    outcome = await registry.execute(world, "n09", "buy", {
        "seller_id": "b_bakery", "item": "bread", "quantity": 2,
    })

    assert outcome.result["unit_price_sim"] == 30
    assert outcome.result["total_sim"] == 60
    assert world.inventory.quantity("n09", "bread") == 2
    from world.ledger.action_costs import ActionActor, cost_for

    fee = cost_for("buy", {"quantity": 2, "total_price_sim": 60}, ActionActor("n09"))
    assert world.ledger.sim_balance("n09") == before - 60 - fee


@pytest.mark.asyncio
async def test_a_buyer_cannot_name_a_price_at_all(world: WorldState, registry) -> None:
    """The old parameter is gone, not ignored. A resident that keeps
    passing it is TOLD, rather than quietly overcharged or undercharged.

    TRADE-01. This docstring has always said that and the assertion below
    used to say the opposite - it relied on pydantic dropping the key and
    checked only that the price came out right. The drop is the defect:
    the same silence posted a shop's price on a person in run
    `deni-a6f9eb4-5h` and cost that run every transaction in it.

    Both halves are asserted now: naming a price is refused, and a
    resident that does not name one pays what the seller posted.
    """
    from world.tools.errors import ToolValidationError

    await _bakery(world)
    await spawn_agent(world, "n09", BAKERY_AT)
    in_conversation(world, "n09", "n05")
    await registry.execute(world, "n05", "post_price", {
        "item": "bread", "ask_sim": 30, "on_behalf_of_business": "b_bakery",
    })

    with pytest.raises(ToolValidationError) as caught:
        await registry.execute(world, "n09", "buy", {
            "seller_id": "b_bakery", "item": "bread", "quantity": 1,
            "unit_price_sim": 1,
        })
    said = str(caught.value)
    assert "unit_price_sim" in said
    assert "seller_id" in said and "quantity" in said

    outcome = await registry.execute(world, "n09", "buy", {
        "seller_id": "b_bakery", "item": "bread", "quantity": 1,
    })
    assert outcome.result["unit_price_sim"] == 30


@pytest.mark.asyncio
async def test_buying_an_unpriced_item_is_refused_and_names_trade(
    world: WorldState, registry
) -> None:
    """The spec's second named test.

    Run 19's zero-transaction morning was a description pointing the wrong
    way: `sell` said "at its shop" and nothing about people, and the town
    spent a morning trying to register businesses. This one names the verb
    that fits.
    """
    await _bakery(world)
    await spawn_agent(world, "n09", BAKERY_AT)

    with pytest.raises(NotFoundError) as exc:
        await registry.execute(world, "n09", "buy", {
            "seller_id": "b_bakery", "item": "bread", "quantity": 1,
        })

    assert "trade" in str(exc.value)
    assert "has not posted a price" in str(exc.value)


@pytest.mark.asyncio
async def test_buying_from_a_person_requires_standing_with_them(
    world: WorldState, registry
) -> None:
    """The spec's third named test."""
    await spawn_agent(world, "n06", FIELD)
    world.agent_names["n06"] = "Wren Thorne"
    world.inventory.credit("n06", "cabbage", 5)
    await registry.execute(world, "n06", "post_price", {"item": "cabbage", "ask_sim": 12})
    await spawn_agent(world, "n09", GROCERY_AT)

    with pytest.raises(LocationGateError):
        await registry.execute(world, "n09", "buy", {
            "seller_id": "n06", "item": "cabbage", "quantity": 1,
        })

    await world.event_bus.emit(
        "agent.moved", tick=3, payload={"agent_id": "n09", "location_id": FIELD}
    )
    in_conversation(world, "n09", "n06")
    outcome = await registry.execute(world, "n09", "buy", {
        "seller_id": "n06", "item": "cabbage", "quantity": 2,
    })
    assert outcome.result["total_sim"] == 24
    assert world.inventory.quantity("n06", "cabbage") == 3


@pytest.mark.asyncio
async def test_an_absent_owner_still_sells_at_the_posted_price(
    world: WorldState, registry
) -> None:
    """RESTORES the spec's fourth named test, and its reason: "a shop that
    only trades while its owner is standing in it is a person, not a
    business".

    This file reversed it once, for the operator's rule of 2026-09-21 - a
    purchase happens only between a buyer and a seller in a conversation,
    because otherwise an agent buys with nobody there to agree the sale.
    That rule was right about the defect and too wide by one case.

    THE POSTED PRICE IS THE AGREEMENT. `post_price` is an act this
    shopkeeper performed, standing in this shop, naming in public and in
    advance what a loaf costs. A customer who pays exactly that is not
    buying from somebody who never agreed; it is the shop's own published
    offer being taken up.

    What the 2026-09-21 rule caught is still caught: a PERSON is not a shop
    and two residents who have never spoken still cannot trade, and a shop
    that has priced nothing is still shut. Both are pinned in
    `test_a_sale_is_agreed_in_conversation.py`.

    The cost of the wider rule was election30: buyers turned away because
    "nobody is minding Kasukabe Cafe right now", 9 of 47 sell calls landing,
    and a town eating raw flour beside shelves it could not buy from.
    """
    await _bakery(world)
    await spawn_agent(world, "n09", BAKERY_AT)
    await registry.execute(world, "n05", "post_price", {
        "item": "bread", "ask_sim": 30, "on_behalf_of_business": "b_bakery",
    })
    await world.event_bus.emit(
        "agent.moved", tick=4, payload={"agent_id": "n05", "location_id": FIELD}
    )
    stock_before = world.inventory.quantity("b_bakery", "bread")

    await registry.execute(world, "n09", "buy", {
        "seller_id": "b_bakery", "item": "bread", "quantity": 1,
    })

    assert world.inventory.quantity("b_bakery", "bread") == stock_before - 1
    assert world.inventory.quantity("n09", "bread") == 1


@pytest.mark.asyncio
async def test_a_shop_minded_by_its_employee_sells_while_the_owner_is_away(
    world: WorldState, registry
) -> None:
    """The half of the spec's fourth test that still holds: a business is
    not a person, so somebody who works there can sell for it."""
    business = await _bakery(world)
    await spawn_agent(world, "n09", BAKERY_AT)
    await spawn_agent(world, "n11", BAKERY_AT)
    business.employee_agent_ids.add("n11")
    await registry.execute(world, "n05", "post_price", {
        "item": "bread", "ask_sim": 30, "on_behalf_of_business": "b_bakery",
    })
    await world.event_bus.emit(
        "agent.moved", tick=4, payload={"agent_id": "n05", "location_id": FIELD}
    )
    in_conversation(world, "n09", "n11")

    outcome = await registry.execute(world, "n09", "buy", {
        "seller_id": "b_bakery", "item": "bread", "quantity": 1,
    })

    assert outcome.result["total_sim"] == 30


@pytest.mark.asyncio
async def test_buying_more_than_the_shelf_holds(world: WorldState, registry) -> None:
    await _bakery(world)
    await spawn_agent(world, "n09", BAKERY_AT)
    in_conversation(world, "n09", "n05")
    await registry.execute(world, "n05", "post_price", {
        "item": "bread", "ask_sim": 30, "on_behalf_of_business": "b_bakery",
    })
    before = world.ledger.sim_balance("n09")

    with pytest.raises(InsufficientInventoryError):
        await registry.execute(world, "n09", "buy", {
            "seller_id": "b_bakery", "item": "bread", "quantity": 99,
        })

    assert world.ledger.sim_balance("n09") == before, "a refusal must cost nothing"


@pytest.mark.asyncio
async def test_a_seller_receives_the_posted_bid(world: WorldState, registry) -> None:
    await _bakery(world)
    await spawn_agent(world, "n06", BAKERY_AT)
    in_conversation(world, "n06", "n05")
    world.inventory.credit("n06", "flour", 10)
    await registry.execute(world, "n05", "post_price", {
        "item": "flour", "bid_sim": 6, "on_behalf_of_business": "b_bakery",
    })
    before = world.ledger.sim_balance("n06")

    outcome = await registry.execute(world, "n06", "sell", {
        "buyer_id": "b_bakery", "item": "flour", "quantity": 4,
    })

    assert outcome.result["unit_price_sim"] == 6
    from world.ledger.action_costs import ActionActor, cost_for

    fee = cost_for("sell", {"quantity": 4, "total_price_sim": 24}, ActionActor("n06"))
    assert world.ledger.sim_balance("n06") == before + 24 - fee
    assert world.inventory.quantity("b_bakery", "flour") == 4


@pytest.mark.asyncio
async def test_selling_where_nobody_posted_a_bid_names_trade(
    world: WorldState, registry
) -> None:
    await _bakery(world)
    await spawn_agent(world, "n06", BAKERY_AT)
    world.inventory.credit("n06", "flour", 10)
    await registry.execute(world, "n05", "post_price", {
        "item": "flour", "ask_sim": 8, "on_behalf_of_business": "b_bakery",
    })

    with pytest.raises(NotFoundError) as exc:
        await registry.execute(world, "n06", "sell", {
            "buyer_id": "b_bakery", "item": "flour", "quantity": 1,
        })

    assert "trade" in str(exc.value)


@pytest.mark.asyncio
async def test_a_posted_sale_is_distinguishable_from_a_bargain(
    world: WorldState, registry
) -> None:
    """E3 asks for the share of trade done at a posted price against the
    share haggled in speech. The report must not have to parse prose."""
    await _bakery(world)
    await spawn_agent(world, "n09", BAKERY_AT)
    in_conversation(world, "n09", "n05")
    await registry.execute(world, "n05", "post_price", {
        "item": "bread", "ask_sim": 30, "on_behalf_of_business": "b_bakery",
    })

    outcome = await registry.execute(world, "n09", "buy", {
        "seller_id": "b_bakery", "item": "bread", "quantity": 1,
    })

    sale = [e for e in outcome.events if e.type == "business.sale.completed"][0]
    assert sale.payload["posted_price_sim"] == 30
    assert sale.payload["holder_kind"] == "business"


@pytest.mark.asyncio
async def test_the_price_board_survives_a_checkpoint(world: WorldState, registry) -> None:
    """Owned state, per the addendum's rule 6. A resumed run that lost the
    board would put a whole town's stock back to unpriced without a word."""
    await _bakery(world)
    await registry.execute(world, "n05", "post_price", {
        "item": "bread", "ask_sim": 30, "on_behalf_of_business": "b_bakery",
    })

    saved = world.snapshot()
    world.price_board.clear()
    world.restore(saved)

    assert world.posted("b_bakery", "bread").ask_sim == 30


@pytest.mark.asyncio
async def test_the_transaction_cost_still_scales_with_the_posted_price(
    world: WorldState, registry
) -> None:
    """A4 nearly made every trade in the town cost the same to transact.

    The 1% TRADE cost is computed by `extract_trade_value`, which read
    `unit_price_sim` off the params - the exact field A4 removed. Left
    alone it would have returned 0 for every `buy` and `sell`, the
    value_bps term would have vanished, and every trade would have fallen
    to the rule's 2-sim floor: a 10,000-sim purchase costing the same to
    transact as a 30-sim loaf. Nothing would have failed; the town would
    just have had a flat fee and nobody would have known.
    """
    await _bakery(world)
    await spawn_agent(world, "n09", BAKERY_AT)
    in_conversation(world, "n09", "n05")
    await registry.execute(world, "n05", "post_price", {
        "item": "bread", "ask_sim": 4_000, "on_behalf_of_business": "b_bakery",
    })

    before = world.ledger.sim_balance("n09")
    await registry.execute(world, "n09", "buy", {
        "seller_id": "b_bakery", "item": "bread", "quantity": 2,
    })

    spent = before - world.ledger.sim_balance("n09")
    assert spent == 8_000 + 80


@pytest.mark.asyncio
async def test_an_unpriced_trade_resolves_to_no_value_rather_than_a_guess(
    world: WorldState, registry
) -> None:
    """`posted_trade_value` returns None, not 0, when there is no post, so
    the actor records "not resolved" rather than a misleading zero."""
    from world.ledger.action_costs import posted_trade_value
    from world.tools.primitives.commerce import BuyParams

    await _bakery(world)
    params = BuyParams(seller_id="b_bakery", item="bread", quantity=2)

    assert posted_trade_value(world, "buy", params) is None

    await registry.execute(world, "n05", "post_price", {
        "item": "bread", "ask_sim": 30, "on_behalf_of_business": "b_bakery",
    })
    assert posted_trade_value(world, "buy", params) == 60
    assert posted_trade_value(world, "sell", params) is None
