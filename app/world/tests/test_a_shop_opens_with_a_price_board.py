"""Stock with no price is the same dead end as no stock.

Seeding `opening_stock` put bread on the grocery's shelf and `buy` still
answered:

    Kasukabe Grocery has not posted a price for bread. Nothing is for sale
    here at a fixed price. `trade` is how two people agree one.

A shop opens with a price list; that is most of what being open means. Until
an owner happens to call `post_price`, a stocked shop is a locked one, and
election30 posted 56 prices in a whole run and repriced none of them.

THE BID SIDE IS THE HALF THAT PAYS FOR THINGS. Kasukabe has no outside
market, so every sim a producer earns has to come out of another resident
who is also losing money - which is why 29 of election30's 30 residents
ended poorer than they started. Its miner was told three times that
"government doesn't buy ore", 52 fellings of timber found no buyer, and the
town finished holding 89 wheat and 78 flour that nobody in it would buy.
A shop that bids for its own inputs is where a farmer's morning turns into
income.

NOT A PRICE CONTROL. These are a starting point: `post_price` overwrites any
of them, and what the town does with them afterwards is the measurement -
evaluation E2 asks whether the spread across shops narrows, which needs a
spread to start from.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from world.life.models import SmallBusiness

GENESIS = pathlib.Path(__file__).resolve().parents[2] / "world"
GENESIS_FILES = sorted(p.name for p in GENESIS.glob("genesis*.json"))

SELLS_A_SERVICE_NOT_GOODS: set[str] = set()

PRODUCE = ("wheat", "flour", "timber", "iron_ore")


def _holders(data: dict) -> list[dict]:
    return [
        *(data.get("small_businesses") or []),
        *([data["construction_company"]] if data.get("construction_company") else []),
    ]


def test_the_model_has_somewhere_to_put_it() -> None:
    assert "opening_prices" in SmallBusiness.model_fields


@pytest.mark.parametrize("path", GENESIS_FILES)
def test_a_stocked_shelf_has_a_price_on_it(path: str) -> None:
    """The defect exactly: goods the shop cannot sell."""
    data = json.loads((GENESIS / path).read_text(encoding="utf-8"))
    for shop in data.get("small_businesses") or []:
        if shop["business_type"] in SELLS_A_SERVICE_NOT_GOODS:
            continue
        board = shop.get("opening_prices") or {}
        for item in (shop.get("opening_stock") or {}):
            ask = (board.get(item) or {}).get("ask")
            assert ask, (
                f"{shop['business_id']} in {path} holds {item} and names no "
                "price for it, so `buy` refuses and the shelf might as well "
                "be empty"
            )


@pytest.mark.parametrize("path", GENESIS_FILES)
def test_everything_a_resident_can_make_has_a_buyer(path: str) -> None:
    """The income half, and the gap that hid behind the stock fix.

    Seeding the shelves fixed buying and left selling exactly where it was:
    a farmer with wheat in a town whose only shops bid for apples still has
    no way to turn a morning's work into money.
    """
    data = json.loads((GENESIS / path).read_text(encoding="utf-8"))
    goods_shops = [
        shop for shop in (data.get("small_businesses") or [])
        if shop["business_type"] not in SELLS_A_SERVICE_NOT_GOODS
    ]
    if not goods_shops:
        assert len(data.get("agents") or []) <= 6, (
            f"{path} has {len(data['agents'])} residents and no shop selling "
            "goods, which is too many people to feed hand to hand"
        )
        return
    for item in PRODUCE:
        bidders = [
            holder["business_id"]
            for holder in _holders(data)
            if ((holder.get("opening_prices") or {}).get(item) or {}).get("bid")
        ]
        assert bidders, (
            f"nobody in {path} buys {item} - a resident who produces it has "
            "no way to earn from it, and in a town with no outside market "
            "that is the whole of where income comes from"
        )


@pytest.mark.parametrize("path", GENESIS_FILES)
def test_a_shop_buys_cheaper_than_it_sells(path: str) -> None:
    """A shop that pays more than it charges is a money pump, and this world
    has a fixed money supply."""
    data = json.loads((GENESIS / path).read_text(encoding="utf-8"))
    for holder in _holders(data):
        for item, sides in (holder.get("opening_prices") or {}).items():
            ask, bid = sides.get("ask"), sides.get("bid")
            if ask is not None and bid is not None:
                assert bid < ask, (
                    f"{holder['business_id']} in {path} pays {bid} for {item} "
                    f"and sells it for {ask}"
                )


@pytest.mark.parametrize("path", GENESIS_FILES)
def test_every_price_is_a_positive_number_of_sim(path: str) -> None:
    data = json.loads((GENESIS / path).read_text(encoding="utf-8"))
    for holder in _holders(data):
        for item, sides in (holder.get("opening_prices") or {}).items():
            assert sides, f"{holder['business_id']} in {path}: {item} names neither side"
            for side, value in sides.items():
                assert side in ("ask", "bid"), f"{path}: unknown side {side!r}"
                assert isinstance(value, int) and value > 0, f"{path}: {item} {side}={value}"


@pytest.mark.parametrize("path", GENESIS_FILES)
def test_bread_is_affordable_on_a_genesis_purse(path: str) -> None:
    """election30's residents were quoted 30-45 sim a loaf and said so:
    "too dear for my blood, and they got none anyway". A price nobody pays
    is not a market."""
    data = json.loads((GENESIS / path).read_text(encoding="utf-8"))
    purses = [a.get("starting_sim_balance", 0) for a in (data.get("agents") or [])]
    if not purses:
        pytest.skip("no cast in this file")
    poorest = min(purses)
    for holder in _holders(data):
        ask = ((holder.get("opening_prices") or {}).get("bread") or {}).get("ask")
        if ask:
            assert ask <= poorest / 10, (
                f"{holder['business_id']} in {path} opens bread at {ask} sim "
                f"against the poorest purse of {poorest}"
            )
