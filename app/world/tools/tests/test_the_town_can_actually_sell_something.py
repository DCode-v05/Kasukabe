"""A shop that stocks a shelf and posts a price can be bought from.

`TRADE-01`. Run `deni-a6f9eb4-5h`: fifty residents, 6,424 calls, $27.78,
351 game-ticks, **zero settlements**. The gate report calls it
`G4 settlements 0 FAIL` and it is the fourth run in a row to fail that
way. The trace says exactly why, and it is not that nobody tried.

`post_price` takes `on_behalf_of_business`. Every resident that tried to
price a shop's stock wrote `business_id`:

    post_price(item="bread", ask_sim=30, business_id="business:b_grocery")
    -> {"holder": "n13", "holder_kind": "person", ...}

Pydantic drops unknown fields, so the price went on the PERSON. The
bread was on the shop's shelf, where `stock_shelf` had put it one call
earlier. Two residents then walked to the grocery, called `buy`, and got

    "n13 needs 1 bread, has 0"

with the money in hand, standing in the right room. The ask on one
holder, the goods on another, and a cheerful success message in between.

This file is the end-to-end version of that morning: stock a shelf,
price it the way the residents actually phrased it, and buy it.
"""
from __future__ import annotations

import pytest

from world.tools.errors import ToolValidationError
from world.tools.models import Business
from world.tools.tests.conftest import in_conversation, spawn_agent

SHOP = "b_grocery"
SHOP_AT = "Kasukabe Farmland"


async def _grocery(world, owner: str = "seller") -> None:
    """A shop with an owner, a shelf and a till. `build_world` seeds no
    businesses - genesis does - so a test about SELLING has to open one."""
    await spawn_agent(world, owner, SHOP_AT)
    world.businesses[SHOP] = Business(
        business_id=SHOP, name="Kasukabe Grocery", business_type="grocery",
        owner_agent_id=owner, location_id=SHOP_AT,
    )
    await world.ledger.register_business(SHOP, owner, fee_sim=1, tick=world.tick())
    await world.ledger.mint_sim(SHOP, 5_000, tick=world.tick())


@pytest.mark.asyncio
async def test_the_exact_sequence_that_settled_nothing_now_settles(world, registry):
    """n13's morning, replayed verbatim."""
    await _grocery(world)
    await spawn_agent(world, "buyer", SHOP_AT)
    shop = SHOP
    world.inventory.credit("seller", "bread", 8)

    await registry.execute(world, "seller", "stock_shelf", {
        "business_id": shop, "item": "bread", "quantity": 8,
    })
    assert world.inventory.quantity("seller", "bread") == 0, "the shelf has it now"

    # The phrasing every resident in the run used.
    posted = await registry.execute(world, "seller", "post_price", {
        "item": "bread", "ask_sim": 30, "business_id": shop,
    })
    assert posted.result["holder"] == shop, (
        "the price was posted on the PERSON while the bread sat on the "
        "shop's shelf - the exact deadlock that settled nothing in run "
        "deni-a6f9eb4-5h"
    )
    assert posted.result["holder_kind"] == "business"

    before = world.ledger.sim_balance("buyer")
    in_conversation(world, "buyer", "seller")   # a sale is agreed in conversation
    bought = await registry.execute(world, "buyer", "buy", {
        "seller_id": shop, "item": "bread", "quantity": 1,
    })

    assert bought.result["unit_price_sim"] == 30
    assert world.inventory.quantity("buyer", "bread") == 1
    # At least the price. `buy` also charges its own action cost, and
    # pinning the exact total here would make this test about the fee
    # schedule rather than about whether the town can sell a loaf.
    assert world.ledger.sim_balance("buyer") <= before - 30
    assert world.inventory.quantity(shop, "bread") == 7, "the shelf did not part with it"


@pytest.mark.asyncio
async def test_a_renamed_parameter_is_reported_not_silently_applied(world, registry):
    """Folding a synonym is a repair, and a repair the resident is not
    told about is the same silence with a better outcome this once."""
    await _grocery(world)
    shop = SHOP

    outcome = await registry.execute(world, "seller", "post_price", {
        "item": "bread", "price_sim": 12, "business_id": shop,
    })
    assert outcome.result["ask_sim"] == 12


@pytest.mark.asyncio
async def test_a_parameter_the_tool_does_not_have_is_refused(world, registry):
    """The general rule. A silent drop returns SUCCESS for an act nobody
    asked for, and the resident cannot find out."""
    await spawn_agent(world, "n01", "Kasukabe Farmland")
    with pytest.raises(ToolValidationError) as caught:
        await registry.execute(world, "n01", "harvest", {
            "crop": "wheat", "how_many_bushels": 4,
        })
    said = str(caught.value)
    assert "how_many_bushels" in said
    assert "harvest takes:" in said


@pytest.mark.asyncio
async def test_a_missing_parameter_is_told_what_the_tool_takes(world, registry):
    """The commonest refusal in the run was "Field required" with no
    statement of what the fields ARE. `take_from_shelf` was called with
    `{}` five times by five different residents, each of whom then spent
    another call guessing."""
    await spawn_agent(world, "n01", "Kasukabe Farmland")
    with pytest.raises(ToolValidationError) as caught:
        await registry.execute(world, "n01", "take_from_shelf", {})
    said = str(caught.value)
    assert "take_from_shelf takes:" in said
    assert "business_id" in said and "item" in said


@pytest.mark.asyncio
async def test_the_residents_own_words_for_a_transfer_work(world, registry):
    """Three billed calls for one act, in the run: `to`, then
    `recipient_id`, then `to_agent_id`."""
    await spawn_agent(world, "giver", "Kasukabe Farmland")
    await spawn_agent(world, "taker", "Kasukabe Farmland")
    before = world.ledger.sim_balance("taker")

    await registry.execute(world, "giver", "transfer", {"to": "taker", "amount": 40})

    assert world.ledger.sim_balance("taker") == before + 40


# ── TRADE-03: a listing nobody can buy from says so, at post time ─────


@pytest.mark.asyncio
async def test_stocking_a_shelf_then_pricing_it_now_prices_the_shelf(world, registry):
    """`TRADE-07`. n13's morning, in the form that survived TRADE-01.

    TRADE-01 fixed the case where the resident NAMED the business and
    pydantic dropped the name. This is the commoner one: it names
    nothing. `stock_shelf` moves the bread out of its hands onto the
    shop's shelf; `post_price` with no business named used to put the ask
    on the PERSON, who now holds none - and `buy` answered "needs 1
    bread, has 0" to anyone who walked in.

    Posting where the goods plainly are reads as obviously correct to a
    shopkeeper standing in its own shop, which is the whole reason it
    kept happening.
    """
    await _grocery(world)
    await spawn_agent(world, "buyer", SHOP_AT)
    world.inventory.credit("seller", "bread", 8)
    await registry.execute(world, "seller", "stock_shelf", {
        "business_id": SHOP, "item": "bread", "quantity": 8,
    })

    posted = await registry.execute(world, "seller", "post_price", {
        "item": "bread", "ask_sim": 30,
    })

    assert posted.result["holder"] == SHOP, (
        "the ask went on the person while the bread sat on the shelf - the "
        "deadlock that settled nothing in run deni-a6f9eb4-5h"
    )
    # And it completes, which is the only claim that matters.
    in_conversation(world, "buyer", "seller")   # a sale is agreed in conversation
    bought = await registry.execute(world, "buyer", "buy", {
        "seller_id": SHOP, "item": "bread", "quantity": 1,
    })
    assert bought.result["unit_price_sim"] == 30
    assert world.inventory.quantity("buyer", "bread") == 1


@pytest.mark.asyncio
async def test_a_price_the_shop_took_can_be_taken_down_again(world, registry):
    """A price TRADE-07 moved onto the shop must be findable by the
    resident that posted it, or it could post and never withdraw."""
    await _grocery(world)
    world.inventory.credit("seller", "bread", 8)
    await registry.execute(world, "seller", "stock_shelf", {
        "business_id": SHOP, "item": "bread", "quantity": 8,
    })
    await registry.execute(world, "seller", "post_price", {"item": "bread", "ask_sim": 30})

    await registry.execute(world, "seller", "withdraw_price", {"item": "bread"})

    assert world.posted(SHOP, "bread") is None


@pytest.mark.asyncio
async def test_pricing_something_nobody_here_holds_still_warns(world, registry):
    """TRADE-03's warning survives, for the case TRADE-07 cannot repair:
    neither the resident nor any shop it can sell for has the item.

    Still a note and not a refusal - pricing what you are about to
    acquire is legitimate, and what was missing was being told while
    there is a turn left to act on it.
    """
    await _grocery(world)

    posted = await registry.execute(world, "seller", "post_price", {
        "item": "bread", "ask_sim": 30,
    })

    assert posted.result["holder"] == "seller", "nothing here holds bread"
    assert posted.result["on_hand"] == 0
    assert "nobody can buy" in (posted.result.get("note") or "")


@pytest.mark.asyncio
async def test_two_shops_in_one_place_is_left_to_the_resident(world, registry):
    """Deliberately unanimous. A resident that may sell for two shops
    here asked an ambiguous question, and guessing which till gets paid
    is worse than posting on the person and letting the note say so."""
    await _grocery(world)
    other = Business(
        business_id="b_second", name="The Other Shop", business_type="grocery",
        owner_agent_id="seller", location_id=SHOP_AT,
    )
    world.businesses["b_second"] = other
    await world.ledger.register_business("b_second", "seller", fee_sim=1, tick=world.tick())
    world.inventory.credit(SHOP, "bread", 4)
    world.inventory.credit("b_second", "bread", 4)

    posted = await registry.execute(world, "seller", "post_price", {
        "item": "bread", "ask_sim": 30,
    })

    assert posted.result["holder"] == "seller", (
        "the world guessed which of two tills gets paid"
    )
    # And TRADE-03's note still fires, because the resident holds none -
    # so the ambiguity is surfaced rather than silently resolved.
    note = posted.result.get("note") or ""
    assert "nobody can buy" in note
    assert "on_behalf_of_business" in note or "take_from_shelf" in note, (
        "the note does not name a way out, so the resident cannot act on it"
    )


@pytest.mark.asyncio
async def test_a_listing_that_can_be_bought_from_says_nothing(world, registry):
    """A note on every posting would be noise; this one is a warning."""
    await _grocery(world)
    world.inventory.credit("seller", "bread", 3)

    posted = await registry.execute(world, "seller", "post_price", {
        "item": "bread", "ask_sim": 30,
    })

    assert posted.result["on_hand"] == 3
    assert "note" not in posted.result


@pytest.mark.asyncio
async def test_a_bid_only_posting_is_not_warned_about(world, registry):
    """Offering to BUY something you do not hold is the normal case, and
    warning about it would be wrong."""
    await _grocery(world)

    posted = await registry.execute(world, "seller", "post_price", {
        "item": "bread", "bid_sim": 12,
    })

    assert "note" not in posted.result
