"""The commonest spelling in the whole corpus, still refused.

`sell(business_id=..., item, quantity, unit_price_sim)` succeeded **57
times** in recorded runs and is refused today; `buy` with the same shape,
25 times. Between them that is 82 calls a real model actually made, in
runs that cost four to ten dollars each, and the replay audit reports both
as broken contracts.

Half of it should never have been refused. `buy`/`sell` already let
`seller_id`/`buyer_id` name a business - commerce.py says so in its own
words, "A4 lets seller_id/buyer_id be a business OR a co-located person" -
so a resident writing `business_id` for the shop it is trading with has
named the right thing in the wrong word. The synonym table already folds
`to_agent_id`, `buyer_agent_id` and `buyer` for exactly this reason; it
never learned the one the models reached for most.

The other half stays refused, and should: `unit_price_sim` is not a
parameter these verbs have. A sale settles at a posted price, and folding
a price the world would then ignore is the TRADE-01 defect - "the silent
drop is the defect. It returns SUCCESS for an act nobody asked for."
"""

from __future__ import annotations

from world.tools.param_repair import repair


def test_a_shop_named_as_the_buyer_is_understood() -> None:
    fixed, notes = repair("sell", {"business_id": "b_grocery", "item": "wheat", "quantity": 2})

    assert fixed == {"buyer_id": "b_grocery", "item": "wheat", "quantity": 2}
    assert notes, "the fold is recorded, never silent"


def test_a_shop_named_as_the_seller_is_understood() -> None:
    fixed, _ = repair("buy", {"business_id": "b_bakery", "item": "bread", "quantity": 1})

    assert fixed == {"seller_id": "b_bakery", "item": "bread", "quantity": 1}


def test_the_business_prefix_survives_the_fold() -> None:
    """`buy(seller_id="business:b_bakery")` is the spelling commerce.py cites."""
    fixed, _ = repair("buy", {"business_id": "business:b_bakery", "item": "bread", "quantity": 1})

    assert fixed["seller_id"] == "business:b_bakery"


def test_a_price_at_sale_time_is_not_quietly_folded() -> None:
    """The half that must keep failing: a sale settles at the posted price."""
    fixed, _ = repair(
        "sell", {"business_id": "b_grocery", "item": "wheat", "quantity": 2, "unit_price_sim": 12}
    )

    assert "unit_price_sim" in fixed, "a price the world would ignore must not be dropped for it"
    assert "ask_sim" not in fixed and "price_sim" not in fixed


def test_an_explicit_counterparty_is_not_overwritten() -> None:
    """If the model said both, the precise word wins and nothing is lost."""
    fixed, _ = repair(
        "sell", {"buyer_id": "a0042", "business_id": "b_grocery", "item": "wheat", "quantity": 1}
    )

    assert fixed["buyer_id"] == "a0042"
