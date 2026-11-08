"""A sale is agreed between two people who are talking to each other.

`buy`, `sell` and `trade` checked only that buyer and seller stood in the
same place. Reproduced against a booted town on 2026-09-21:

  * two residents in the same room who had never spoken to each other -
    `buy` and `sell` both SUCCEEDED;
  * a shop whose owner and staff were all somewhere else - a buyer walked
    in and `buy` SUCCEEDED from the empty shop.

So an agent could buy from somebody it had never addressed, across a crowded
square, or from a shop nobody was minding - an economy of vending machines,
not of people. Every price in it was paid to somebody who never agreed to it.

The rule now: the two parties must share an OPEN conversation where they are
both standing. For a shop, somebody who runs it - its owner or an employee -
has to be there and in that conversation with the buyer. `say` addressed to
somebody already puts both of you on the roster ("being spoken to is being
in the conversation", `primitives/social.py`), so the honest path costs one
remark: speak to the seller, then buy.

Left alone on purpose: `give` and `transfer` are gifts, not purchases; and
the world's own scripted control bakery (owner `control:*`) keeps selling,
because it is the experiment's baseline and has no resident to talk to.

AMENDED for shops, on the evidence of election30. A SHOP that has posted a
price for an item sells it to whoever pays, minder present or not: the
posted price is the agreement, and `post_price` is an act a shopkeeper
performed standing in its own shop. The rule above is unchanged for
everything else, which is the part that matters - a PERSON is not a shop, so
two residents in a room who have never spoken still cannot trade, and a shop
that has priced nothing is still shut. What was reproduced on 2026-09-21
remains impossible; what was also true, that a shop trading only while its
owner stands in it is a person rather than a business, is allowed again.
"""

from __future__ import annotations

import pytest

from world.ledger.models import Account, AccountKind
from world.tools.models import Business, PricePost
from world.tools.tests.conftest import spawn_agent

MARKET = "market_square"


def _open_shop_account(world, business_id: str) -> None:
    """A business trades through its own sim account; a bare test world
    has none until one is opened."""
    if business_id not in world.ledger.accounts:
        world.ledger.accounts[business_id] = Account(business_id, AccountKind.BUSINESS)


def _talking(world, *agents) -> str:
    """Open a conversation where the first agent stands, with all of them
    on the roster - what `say` addressed to somebody does."""
    return world.open_conversation(
        place=world.location_of(agents[0]), topic="",
        participants=list(agents), now=world.tick(),
    )


async def _buyer_and_seller(world):
    await spawn_agent(world, "buyer", MARKET)
    await spawn_agent(world, "seller", MARKET)
    world.ledger.get_account("buyer").sim_units = 5_000
    world.price_board["seller"] = {"potato": PricePost(item="potato", ask_sim=8)}
    world.inventory.credit("seller", "potato", 5)


async def _refused(registry, world, actor, tool, params) -> str:
    """The refusal text, or '' if it went through."""
    try:
        result = await registry.execute(world, actor, tool, params)
    except Exception as exc:  # noqa: BLE001 - a raised refusal is a refusal
        return str(exc)
    return str(result.error) if getattr(result, "error", None) else ""


@pytest.mark.asyncio
async def test_strangers_in_the_same_room_cannot_buy_from_each_other(world, registry):
    await _buyer_and_seller(world)
    sim_before = world.ledger.sim_balance("buyer")

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "seller", "item": "potato", "quantity": 1})

    assert why, "a buyer bought from somebody it had never spoken to"
    assert "conversation" in why.lower()
    assert world.ledger.sim_balance("buyer") == sim_before, "a refused sale moved money"
    assert world.inventory.quantity("seller", "potato") == 5, "a refused sale moved goods"


@pytest.mark.asyncio
async def test_once_they_are_talking_the_sale_goes_through(world, registry):
    await _buyer_and_seller(world)
    _talking(world, "buyer", "seller")

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "seller", "item": "potato", "quantity": 2})

    assert why == ""
    assert world.inventory.quantity("buyer", "potato") == 2


@pytest.mark.asyncio
async def test_speaking_to_the_seller_is_enough(world, registry):
    """The path a resident actually takes - one `say`, addressed - with no
    test fixture opening the conversation for it."""
    await _buyer_and_seller(world)

    await registry.execute(world, "buyer", "say",
                           {"content": "Two potatoes, please.", "to_agent_id": "seller"})
    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "seller", "item": "potato", "quantity": 1})

    assert why == "", f"a buyer who asked the seller directly was refused: {why}"


@pytest.mark.asyncio
async def test_selling_needs_the_conversation_too(world, registry):
    await _buyer_and_seller(world)
    world.price_board["seller"]["wheat"] = PricePost(item="wheat", bid_sim=6)
    world.inventory.credit("buyer", "wheat", 3)
    params = {"buyer_id": "seller", "item": "wheat", "quantity": 1}

    assert "conversation" in (await _refused(registry, world, "buyer", "sell", params)).lower()
    _talking(world, "buyer", "seller")
    assert await _refused(registry, world, "buyer", "sell", params) == ""


@pytest.mark.asyncio
async def test_trading_needs_the_conversation_too(world, registry):
    await _buyer_and_seller(world)
    world.inventory.credit("buyer", "timber", 2)
    params = {"counterparty_agent_id": "seller", "item": "timber", "quantity": 1, "price_sim": 5}
    world.ledger.get_account("seller").sim_units = 5_000

    assert "conversation" in (await _refused(registry, world, "buyer", "trade", params)).lower()
    _talking(world, "buyer", "seller")
    assert await _refused(registry, world, "buyer", "trade", params) == ""


@pytest.mark.asyncio
async def test_a_conversation_that_went_quiet_no_longer_counts(world, registry):
    """Conversations close after a quiet spell (`CONVERSATION_WINDOW_TICKS`).
    A deal struck an hour ago is not an open negotiation."""
    from world.tools.state import CONVERSATION_WINDOW_TICKS

    await _buyer_and_seller(world)
    _talking(world, "buyer", "seller")
    world.current_tick += CONVERSATION_WINDOW_TICKS + 1

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "seller", "item": "potato", "quantity": 1})

    assert "conversation" in why.lower()


@pytest.mark.asyncio
async def test_a_third_person_talking_to_the_seller_does_not_make_you_part_of_it(world, registry):
    await _buyer_and_seller(world)
    await spawn_agent(world, "someone_else", MARKET)
    _talking(world, "someone_else", "seller")

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "seller", "item": "potato", "quantity": 1})

    assert "conversation" in why.lower()


async def _a_shop(world, *, staff_here: bool):
    await spawn_agent(world, "buyer", "business:b_grocery")
    await spawn_agent(world, "grocer", "business:b_grocery" if staff_here else "street")
    world.ledger.get_account("buyer").sim_units = 5_000
    world.businesses["b_grocery"] = Business(
        business_id="b_grocery", name="The Willows Market", business_type="grocery",
        owner_agent_id="grocer", location_id="business:b_grocery",
    )
    _open_shop_account(world, "b_grocery")
    world.price_board["b_grocery"] = {"bread": PricePost(item="bread", ask_sim=12)}
    world.inventory.credit("b_grocery", "bread", 4)


@pytest.mark.asyncio
async def test_a_shop_that_posted_a_price_sells_with_nobody_minding_it(world, registry):
    await _a_shop(world, staff_here=False)

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "b_grocery", "item": "bread", "quantity": 1})

    assert why == "", f"a shop with bread on the shelf and 12 sim on the board refused: {why}"
    assert world.inventory.quantity("buyer", "bread") == 1
    assert world.inventory.quantity("b_grocery", "bread") == 3


@pytest.mark.asyncio
async def test_a_shop_that_has_priced_nothing_is_still_shut(world, registry):
    """The half of the old rule that survives, and the reason the new one is
    safe: being open is something a shopkeeper DID, not a default."""
    await _a_shop(world, staff_here=False)
    world.price_board["b_grocery"] = {}

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "b_grocery", "item": "bread", "quantity": 1})

    assert why, "an unpriced, unattended shop sold bread"
    assert world.inventory.quantity("b_grocery", "bread") == 4


@pytest.mark.asyncio
async def test_a_shop_is_open_only_for_what_it_has_priced(world, registry):
    """A price on bread is not consent to sell the milk."""
    await _a_shop(world, staff_here=False)
    world.inventory.credit("b_grocery", "milk", 5)

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "b_grocery", "item": "milk", "quantity": 1})

    assert why, "a shop sold an item it had never named a price for"
    assert world.inventory.quantity("b_grocery", "milk") == 5


@pytest.mark.asyncio
async def test_a_shopkeeper_in_the_shop_who_has_not_been_spoken_to_still_sells(world, registry):
    """One remark was the honest path when the shop had not spoken for
    itself. A price board speaks for it, so the remark is no longer the
    thing that makes the sale lawful - it is just politeness."""
    await _a_shop(world, staff_here=True)

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "b_grocery", "item": "bread", "quantity": 1})

    assert why == ""
    assert world.inventory.quantity("buyer", "bread") == 1


@pytest.mark.asyncio
async def test_talking_to_the_shopkeeper_buys_from_the_shop(world, registry):
    await _a_shop(world, staff_here=True)
    _talking(world, "buyer", "grocer")

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "b_grocery", "item": "bread", "quantity": 1})

    assert why == ""
    assert world.inventory.quantity("buyer", "bread") == 1
    assert world.ledger.sim_balance("b_grocery") >= 12, "the shop, not the grocer, is paid"


@pytest.mark.asyncio
async def test_an_employee_can_mind_the_shop(world, registry):
    await _a_shop(world, staff_here=False)
    await spawn_agent(world, "assistant", "business:b_grocery")
    world.businesses["b_grocery"].employee_agent_ids.add("assistant")
    _talking(world, "buyer", "assistant")

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "b_grocery", "item": "bread", "quantity": 1})

    assert why == ""


@pytest.mark.asyncio
async def test_the_worlds_own_control_shop_still_sells(world, registry):
    """The scripted control bakery is the experiment's baseline and has no
    resident to talk to. Shutting it would silently remove the control."""
    await spawn_agent(world, "buyer", "business:cb01")
    world.ledger.get_account("buyer").sim_units = 5_000
    world.businesses["cb01"] = Business(
        business_id="cb01", name="Control Bakery", business_type="bakery",
        owner_agent_id="control:cb01", location_id="business:cb01",
    )
    _open_shop_account(world, "cb01")
    world.price_board["cb01"] = {"bread": PricePost(item="bread", ask_sim=10)}
    world.inventory.credit("cb01", "bread", 3)

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "cb01", "item": "bread", "quantity": 1})

    assert why == ""


@pytest.mark.asyncio
async def test_a_gift_needs_no_negotiation(world, registry):
    await _buyer_and_seller(world)
    world.inventory.credit("buyer", "bread", 1)

    why = await _refused(registry, world, "buyer", "give",
                         {"to_agent_id": "seller", "item": "bread", "quantity": 1})

    assert why == ""


@pytest.mark.asyncio
async def test_it_can_be_switched_off_for_a_control_arm(world, registry, monkeypatch):
    import world.tools.primitives.commerce as commerce

    monkeypatch.setattr(commerce, "SALES_NEED_CONVERSATION", False)
    await _buyer_and_seller(world)

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "seller", "item": "potato", "quantity": 1})

    assert why == ""


def test_which_rule_was_in_force_is_recorded() -> None:
    from world.run_manifest import RECORDED_ENV

    assert "KASUKABE_SALES_NEED_CONVERSATION" in RECORDED_ENV


@pytest.mark.asyncio
async def test_the_board_does_not_call_a_priced_shop_unattended(world):
    """The board has to say what is actually buyable.

    It used to flag an empty shop, because an empty shop could not sell.
    One now can, so the flag would send residents away from a counter that
    would serve them - the 87 refusals again, by the opposite route.
    """
    from world.retrieval_depth import format_market_board
    from world.tools.primitives.places import market_offers

    await _a_shop(world, staff_here=False)

    bread = next(o for o in market_offers(world) if o["item"] == "bread")
    assert bread["minded"] is True, "a shop with bread and a price was called shut"
    line = next(l for l in format_market_board(market_offers(world)).splitlines() if "bread" in l)
    assert "nobody" not in line.lower(), line


@pytest.mark.asyncio
async def test_the_board_still_flags_a_person_who_is_not_there(world):
    """The flag keeps its job where it still has one. A person selling from
    their own hands has to be present and spoken to, so an offer from one
    who is nowhere is a walk that ends in a refusal."""
    from world.retrieval_depth import format_market_board
    from world.tools.primitives.places import market_offers
    from world.tools.models import PricePost as _Post

    await _a_shop(world, staff_here=False)
    world.price_board["absent_farmer"] = {
        "wheat": _Post(item="wheat", ask_sim=6, poster_id="absent_farmer")
    }
    world.inventory.credit("absent_farmer", "wheat", 5)

    wheat = next(o for o in market_offers(world) if o["item"] == "wheat")
    assert wheat["minded"] is False, "a person who is nowhere was offered as reachable"
    line = next(l for l in format_market_board(market_offers(world)).splitlines() if "wheat" in l)
    assert "nobody there to agree it" in line.lower(), line


@pytest.mark.asyncio
async def test_the_control_bakery_can_still_buy_its_flour(world, registry):
    """The control bakery is a script on BOTH sides of a deal: it sells bread
    and buys flour from residents. Found by `test_control_bakery` failing
    once the rule landed - it had only been exempted as a seller."""
    await spawn_agent(world, "miller", "business:cb01")
    world.control_actors["control:cb01"] = world.agents.get("miller") or next(iter(world.agents.values()))
    world.price_board["miller"] = {"flour": PricePost(item="flour", ask_sim=4)}
    world.inventory.credit("miller", "flour", 5)

    from world.tools.primitives.commerce import _require_dealing

    _require_dealing(world, "buy", "control:cb01", "miller")


@pytest.mark.asyncio
async def test_an_empty_shelf_is_reported_before_the_missing_conversation(world, registry):
    """Order matters, because every refusal is a paid model call. Telling a
    resident to go and talk to a seller whose shelf is empty buys it a
    wasted call: it would speak, and then be told there is nothing to buy.
    So the conversation is checked last, just before the money moves."""
    await _buyer_and_seller(world)
    world.inventory.debit("seller", "potato", 5)

    why = await _refused(registry, world, "buyer", "buy",
                         {"seller_id": "seller", "item": "potato", "quantity": 1})

    assert "conversation" not in why.lower(), why
    assert why, "an empty shelf sold something"
