"""Four of five purchases failed because nobody could see what was for sale.

The 50-resident run: `buy` refused 140 times in 178 attempts, and 87 of
those refusals are "…has not posted a price for potato". `trade` refused 108
more, almost all of them "you hold 0 potato" - residents offering the very
thing they had just failed to buy. 248 paid model calls bought nothing.

The cause is not in the verbs. `buy` needs the exact seller to have posted an
ask for the exact item (`primitives/commerce.py`), and a resident can only
see prices WHERE IT IS STANDING: `run_genesis.prices_for` is
`describe_prices(world, location_of(agent))`. Over 56,623 lines of that run's
record the perception's price board appears NOT ONCE, and of the 167
`look_around` answers carrying `prices_here`, 122 say "Nothing is priced
here."

So there was no market - only prices a resident could bump into. Asked to
find food, it did the one thing left: ask whoever was standing next to it,
and get refused because that person had never priced potatoes.

`market_offers` is the town-wide answer to "who is selling what": every
standing ask and bid anywhere in town, cheapest first, WITH the stock behind
it - because the third failure mode is a price posted over an empty shelf,
which a resident reported himself at tick 36: "you've posted prices for
potatoes and wheat, but when I try to buy them, the system says you have
none."
"""

from __future__ import annotations

import pytest

from world.tools.models import Business, PricePost
from world.tools.primitives.places import describe_prices, market_offers
from world.tools.tests.conftest import spawn_agent


def _a_town_with_sellers(world):
    """A grocer with stock at the market, a farmer with none at the farm."""
    world.businesses["b_grocery"] = Business(
        business_id="b_grocery", name="The Willows Market", business_type="grocery",
        owner_agent_id="n02", location_id="business:b_grocery",
    )
    world.price_board["b_grocery"] = {
        "potato": PricePost(item="potato", ask_sim=8, bid_sim=None),
        "wheat": PricePost(item="wheat", ask_sim=12, bid_sim=None),
    }
    world.inventory.credit("b_grocery", "potato", 6)
    world.inventory.credit("b_grocery", "wheat", 3)
    world.price_board["n03"] = {
        "wheat": PricePost(item="wheat", ask_sim=9, bid_sim=None),
        "timber": PricePost(item="timber", ask_sim=None, bid_sim=45),
    }
    world.inventory.credit("n03", "wheat", 4)


@pytest.mark.asyncio
async def test_a_resident_can_see_what_is_for_sale_anywhere(world):
    """The whole finding: standing in the street, you can learn that a
    grocery across town has potatoes at 8."""
    _a_town_with_sellers(world)

    offers = market_offers(world)

    items = {o["item"] for o in offers}
    assert "potato" in items and "wheat" in items
    potato = next(o for o in offers if o["item"] == "potato")
    assert potato["ask_sim"] == 8
    assert potato["seller_id"] == "b_grocery"


@pytest.mark.asyncio
async def test_it_says_whether_the_shelf_is_actually_stocked(world):
    """A price over an empty shelf is what Vikram Ingram complained about:
    `buy` accepts the post, then refuses for want of stock."""
    _a_town_with_sellers(world)
    world.price_board["b_grocery"]["bread"] = PricePost(item="bread", ask_sim=30, bid_sim=None)

    offers = market_offers(world)

    bread = next(o for o in offers if o["item"] == "bread")
    assert bread["stock"] == 0
    potato = next(o for o in offers if o["item"] == "potato")
    assert potato["stock"] == 6


@pytest.mark.asyncio
async def test_what_is_in_stock_is_listed_before_what_is_not(world):
    """A resident reading a short list should meet the offers it can
    actually take up."""
    _a_town_with_sellers(world)
    await spawn_agent(world, "n02", "business:b_grocery")
    world.price_board["b_grocery"]["apple"] = PricePost(item="apple", ask_sim=1, bid_sim=None)

    offers = market_offers(world)
    apple_at = next(i for i, o in enumerate(offers) if o["item"] == "apple")
    potato_at = next(i for i, o in enumerate(offers) if o["item"] == "potato")

    assert potato_at < apple_at, "an out-of-stock apple at 1 sim outranked stocked food"


@pytest.mark.asyncio
async def test_who_is_buying_is_on_the_board_too(world):
    """`sell` failed 32 times for want of a posted bid - the same blindness
    in the other direction."""
    _a_town_with_sellers(world)

    offers = market_offers(world)

    timber = next(o for o in offers if o["item"] == "timber")
    assert timber["bid_sim"] == 45
    assert timber["ask_sim"] is None


@pytest.mark.asyncio
async def test_it_says_where_to_go(world):
    """Knowing wheat is 9 somewhere is no use without the somewhere."""
    _a_town_with_sellers(world)

    offers = market_offers(world)

    assert next(o for o in offers if o["seller_id"] == "b_grocery")["where"] == "business:b_grocery"


@pytest.mark.asyncio
async def test_the_cheapest_seller_of_a_thing_comes_first(world):
    """Price decides between offers a resident can actually take up.

    The farmer is put in the fields on purpose. Without a location it is
    nobody a buyer could deal with, and the board's first sort key is
    whether an offer can be taken at all - so the test would be measuring
    reachability and calling it price. The grocer's shelf is reachable
    either way now: a shop's posted price is its standing offer.
    """
    _a_town_with_sellers(world)
    await spawn_agent(world, "n03", "farmland")

    wheat = [o for o in market_offers(world) if o["item"] == "wheat"]

    assert [o["ask_sim"] for o in wheat] == [9, 12]


@pytest.mark.asyncio
async def test_the_list_is_bounded(world):
    """Every token on this board is paid for on every call of every turn,
    and a town of fifty could post hundreds of offers."""
    _a_town_with_sellers(world)
    world.price_board["n04"] = {
        f"thing{i}": PricePost(item=f"thing{i}", ask_sim=i + 1, bid_sim=None) for i in range(40)
    }

    assert len(market_offers(world, limit=12)) == 12


@pytest.mark.asyncio
async def test_a_town_that_has_priced_nothing_says_nothing(world):
    """No "the market is empty" line on every prompt of a town that has not
    discovered prices - the same rule `format_price_board` follows."""
    assert market_offers(world) == []


@pytest.mark.asyncio
async def test_the_local_board_still_works(world):
    """The place-scoped board is not replaced; a resident standing in the
    shop still reads the shop's own prices."""
    _a_town_with_sellers(world)

    here = describe_prices(world, "business:b_grocery")

    assert any("potato" in line for lines in here.values() for line in lines)
