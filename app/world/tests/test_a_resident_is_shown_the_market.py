"""The market board has to reach the resident, or it is a function nobody calls.

`market_offers` answers "who is selling what, anywhere in town". This is the
half that matters: it has to arrive in the perception a resident reads every
turn, and in the refusal it gets when it buys from the wrong person.

Run 26's report described run 25's cast because a mechanism had no caller.
The 50-resident run's price board appeared 0 times in 56,623 lines for the
same class of reason: it was place-scoped, so in practice it never fired.
These tests are about the wiring, not the arithmetic.
"""

from __future__ import annotations

import inspect

from world.retrieval_depth import format_market_board


def _offers():
    return [
        {"item": "potato", "seller": "The Willows Market", "seller_id": "b_grocery",
         "where": "business:b_grocery", "ask_sim": 8, "bid_sim": None, "stock": 6},
        {"item": "wheat", "seller": "Wren Thorne", "seller_id": "n03",
         "where": "farmland:wheat", "ask_sim": 9, "bid_sim": None, "stock": 4},
        {"item": "timber", "seller": "Ulrike Greenhow", "seller_id": "n04",
         "where": "ore_outcrop:iron", "ask_sim": None, "bid_sim": 45, "stock": 0},
        {"item": "bread", "seller": "The Willows Market", "seller_id": "b_grocery",
         "where": "business:b_grocery", "ask_sim": 30, "bid_sim": None, "stock": 0},
    ]


def test_it_names_the_thing_the_price_the_seller_and_the_place() -> None:
    """All four, because a resident missing any one of them cannot act: it
    needs to know what, for how much, from whom, and where to stand."""
    text = format_market_board(_offers())

    assert "potato" in text and "8" in text
    assert "The Willows Market" in text
    assert "business:b_grocery" in text


def test_an_empty_shelf_is_marked_as_one() -> None:
    """Vikram Ingram's complaint, answered before the call is paid for."""
    text = format_market_board(_offers())

    bread = next(line for line in text.splitlines() if "bread" in line)
    assert "none" in bread.lower() or "0" in bread


def test_who_is_buying_reads_as_buying_not_selling() -> None:
    text = format_market_board(_offers())

    timber = next(line for line in text.splitlines() if "timber" in line)
    assert "45" in timber
    assert "buy" in timber.lower() or "wanted" in timber.lower()


def test_a_town_that_priced_nothing_gets_no_line() -> None:
    """The rule `format_price_board` already follows: a line every resident
    learns to skip is worse than no line."""
    assert format_market_board([]) is None
    assert format_market_board(None) is None


def test_the_perception_carries_it() -> None:
    """The wiring: `render_perception_text` must accept and render it."""
    from world.retrieval_depth import render_perception_text

    assert "market" in inspect.signature(render_perception_text).parameters
    assert "format_market_board" in inspect.getsource(render_perception_text)


def test_the_runner_supplies_it_every_turn() -> None:
    """A perception field with no caller is what made the old board
    invisible. The runner builds the market for each resident's turn."""
    import world.run_genesis as rg

    source = inspect.getsource(rg.GenesisRunner)
    assert "market_offers" in source, (
        "nothing in the runner ever builds the town-wide board, so no "
        "resident will ever be shown it"
    )
    assert "market=" in source


def test_a_refused_purchase_says_where_the_thing_is_sold() -> None:
    """`buy`'s refusal listed what THIS seller had priced - useful only to
    somebody who already picked the right seller. 87 of 140 refusals were
    this. It now names who does sell the thing."""
    from world.tools.primitives import commerce

    source = inspect.getsource(commerce.buy_handler)
    assert "market_offers" in source or "sold_elsewhere" in source
