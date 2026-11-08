"""TOOL-01: the spelling the world teaches is the spelling it refused.

Run 23, with seventeen people standing in her shop asking for bread:

    post_price(on_behalf_of_business="business:b_bakery")
        -> no business with id 'business:b_bakery'
    buy(seller_id="business:b_bakery")
        -> no seller or buyer with id 'business:b_bakery'
           - the seller or buyers here are: b_bakery, ...

Twenty loaves sat in stock and the baker could not put a price on them.
`buy` succeeded once in nine attempts across the whole run.

The prefixed form is not something the model invented. Genesis places
every shop at `business:b_bakery`; the standing brief says "You are at
business:b_bakery"; `post_price`'s own success payload returns
`visible_at: "business:b_bakery"`. The town taught the form and then
refused it.

Each test below is one of those refusals, written so it fails on this
file's own terms if `resolve_business` is removed.
"""

from __future__ import annotations

import pytest

from world.tools.errors import NotFoundError
from world.tools.models import Business
from world.tools.place_aliases import resolve_business
from world.tools.state import WorldState

from .conftest import in_conversation, move_agent, spawn_agent
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map

BAKERY_AT = "Kasukabe Town Square"
MILL_AT = "Kasukabe Market"


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
    await world.ledger.register_business("b_bakery", owner, fee_sim=1, tick=world.tick())
    world.inventory.credit("b_bakery", "bread", 20)
    await world.ledger.mint_sim("b_bakery", 5_000, tick=world.tick())
    return business


async def _mill(world: WorldState, *, owner: str = "n13") -> Business:
    await spawn_agent(world, owner, MILL_AT)
    business = Business(
        business_id="b_mill",
        name="Kasukabe Mill",
        business_type="mill",
        owner_agent_id=owner,
        location_id=MILL_AT,
    )
    world.businesses["b_mill"] = business
    await world.ledger.register_business("b_mill", owner, fee_sim=1, tick=world.tick())
    return business


@pytest.mark.asyncio
async def test_every_spelling_the_town_uses_finds_the_same_business(world):
    bakery = await _bakery(world)

    for spelling in (
        "b_bakery",
        "business:b_bakery",
        "Thorne's Bakery",
        "thornes bakery",
        "Kasukabe Town Square",
        "bakery",
    ):
        assert resolve_business(world, spelling) is bakery, spelling


@pytest.mark.asyncio
async def test_an_ambiguous_name_is_declined_rather_than_guessed(world):
    """Two candidates is not a spelling to fix. It is a guess to decline."""
    await _bakery(world)
    await spawn_agent(world, "n21", BAKERY_AT)
    world.businesses["b_bakery_two"] = Business(
        business_id="b_bakery_two",
        name="Second Bakery",
        business_type="bakery",
        owner_agent_id="n21",
        location_id=MILL_AT,
    )

    assert resolve_business(world, "bakery") is None
    assert resolve_business(world, "b_bakery").business_id == "b_bakery"


@pytest.mark.asyncio
async def test_a_business_that_does_not_exist_still_refuses(world):
    await _bakery(world)
    assert resolve_business(world, "business:b_smithy") is None
    assert resolve_business(world, "") is None
    assert resolve_business(world, None) is None


@pytest.mark.asyncio
async def test_the_baker_can_price_her_bread_using_the_id_the_world_printed(
    world, registry
):
    """`post_price(on_behalf_of_business="business:b_bakery")`.

    The refusal that kept twenty loaves off the board for a whole run.
    """
    bakery = await _bakery(world)

    outcome = await registry.execute(
        world,
        "n05",
        "post_price",
        {
            "item": "bread",
            "ask_sim": 20,
            "on_behalf_of_business": "business:b_bakery",
        },
    )

    assert outcome.result["holder"] == bakery.business_id == "b_bakery"


@pytest.mark.asyncio
async def test_a_customer_can_buy_using_the_id_the_world_printed(world, registry):
    """`buy(seller_id="business:b_bakery")`. One of nine that failed."""
    await _bakery(world)
    await registry.execute(
        world,
        "n05",
        "post_price",
        {"item": "bread", "ask_sim": 20, "on_behalf_of_business": "b_bakery"},
    )

    await spawn_agent(world, "n02", BAKERY_AT)
    before = world.ledger.sim_balance("n02")

    in_conversation(world, "n02", "n05")
    outcome = await registry.execute(
        world,
        "n02",
        "buy",
        {"seller_id": "business:b_bakery", "item": "bread", "quantity": 2},
    )

    assert outcome.result["quantity"] == 2
    assert world.inventory.quantity("n02", "bread") == 2
    assert world.ledger.sim_balance("n02") < before


@pytest.mark.asyncio
async def test_the_shop_can_still_be_named_by_a_stranger_who_gets_it_wrong(
    world, registry
):
    """A name the town does not have is still a refusal, with the list."""
    await _bakery(world)
    await spawn_agent(world, "n02", BAKERY_AT)

    with pytest.raises(NotFoundError) as raised:
        await registry.execute(
            world,
            "n02",
            "buy",
            {"seller_id": "the pie shop", "item": "bread", "quantity": 1},
        )
    assert "b_bakery" in str(raised.value)


@pytest.mark.asyncio
async def test_a_trade_is_not_refused_because_two_people_spell_the_room_apart(
    world, registry
):
    """`trade` compared places with `!=` while everything else folded.

    `give`, `revive`, `teach_skill` and the conveyance tools all use
    `satisfies_gate`. Standing in "Kasukabe Government Complex" and
    standing at "government_complex" is standing in one room, and a
    trade that refuses on that teaches the resident something false.
    """
    await spawn_agent(world, "n07", "Kasukabe Government Complex")
    await spawn_agent(world, "n08", "government_complex")
    world.inventory.credit("n07", "wheat", 5)

    in_conversation(world, "n07", "n08")
    outcome = await registry.execute(
        world,
        "n07",
        "trade",
        {
            "counterparty_agent_id": "n08",
            "item": "wheat",
            "quantity": 3,
            "price_sim": 30,
        },
    )

    assert world.inventory.quantity("n08", "wheat") == 3
    assert outcome.events


@pytest.mark.asyncio
async def test_a_trade_across_town_is_still_refused(world, registry):
    """The fold must not become "anywhere satisfies anywhere"."""
    from world.tools.errors import LocationGateError

    await spawn_agent(world, "n07", "Kasukabe Government Complex")
    await spawn_agent(world, "n08", "Kasukabe Farmland")
    world.inventory.credit("n07", "wheat", 5)

    with pytest.raises(LocationGateError):
        await registry.execute(
            world,
            "n07",
            "trade",
            {
                "counterparty_agent_id": "n08",
                "item": "wheat",
                "quantity": 3,
                "price_sim": 30,
            },
        )


@pytest.mark.asyncio
async def test_wages_and_trade_secrets_take_the_prefixed_form_too(world, registry):
    """Every verb that takes a business id had the same defect."""
    await _mill(world)
    await spawn_agent(world, "n03", MILL_AT)

    await registry.execute(
        world,
        "n13",
        "hire_employee",
        {"business_id": "business:b_mill", "employee_agent_id": "n03", "employee_consent": True},
    )
    assert "n03" in world.businesses["b_mill"].employee_agent_ids

    await registry.execute(
        world,
        "n13",
        "set_wage",
        {"business_id": "business:b_mill", "employee_id": "n03", "wage_sim": 40},
    )
    assert "b_mill|n03" in world.wage_promises


@pytest.mark.asyncio
async def test_a_new_business_can_stand_somewhere_other_than_the_town_hall(
    world, registry
):
    """Registering is gated at the complex; the shop should not be.

    Every business registered in any run so far took `location_of(agent)`
    as its premises, and you have to be standing in the government complex
    to register one. So a resident who founded a bakery could only sell
    bread inside the town hall, and `buy`/`post_price`/`pay_wage` for it
    all gated there forever after.
    """
    await spawn_agent(world, "n11", "Kasukabe Government Complex")

    await registry.execute(
        world,
        "n11",
        "register_business",
        {
            "business_id": "b_cafe",
            "name": "Hobbs Cafe",
            "business_type": "cafe",
            "premises": "Hobbs Cafe",
        },
    )

    assert world.businesses["b_cafe"].location_id == "Hobbs Cafe"
    assert resolve_business(world, "business:b_cafe").business_id == "b_cafe"


@pytest.mark.asyncio
async def test_a_premises_the_town_cannot_walk_to_is_refused(world, registry):
    """Inventing a shop nobody can reach is worse than the town hall."""
    await spawn_agent(world, "n12", "Kasukabe Government Complex")

    with pytest.raises(NotFoundError):
        await registry.execute(
            world,
            "n12",
            "register_business",
            {
                "business_id": "b_ghost",
                "name": "Nowhere Emporium",
                "business_type": "shop",
                "premises": "the moon",
            },
        )
    assert "b_ghost" not in world.businesses


@pytest.mark.asyncio
async def test_leaving_premises_unset_keeps_the_old_behaviour(world, registry):
    await spawn_agent(world, "n14", "Kasukabe Government Complex")

    await registry.execute(
        world,
        "n14",
        "register_business",
        {"business_id": "b_plain", "name": "Plain Goods", "business_type": "shop"},
    )

    assert world.businesses["b_plain"].location_id == "Kasukabe Government Complex"
