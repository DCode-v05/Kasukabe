"""The brief must not hand a resident a word `eat` will refuse.

Run 23's taxonomy, quoted in `world/tools/inventory.py` and in doc 01
blocker 8:

    eat: 'food' is not food                        x4

`TOOL-04` fixed the half of this that was a missing read - `holdings_line`
now puts real item names in the brief. What survived is the line directly
above it, which still says

    You are holding 7 units of food. `eat` consumes it;

so the brief names a verb, names a quantity, and names as the object of
that verb the one word in the town that is not an item. A resident that
reads the sentence and does what it says is refused, and the refusal is
correct. That is not a model failure; it is the brief teaching a word the
world does not accept.

The food count stays - DESIGN.md section 3's "an empty larder is a
countdown" is a real signal and `food_units` is how it is told. What
changes is that when there IS food, the brief says what it is.
"""
from __future__ import annotations

from world.retrieval_depth import format_standing_brief
from world.tools.primitives.living import EDIBLE


def _brief(**over):
    base = {
        "name": "Marta Thorne",
        "occupation": "baker",
        "location_id": "business:b_bakery",
        "workplace": "business:b_bakery",
        "food_units": 7,
        "food_items": {"bread": 4, "wheat": 3},
        "holding": {"bread": 4, "wheat": 3, "sim_note": 0},
        "sim_balance": 500,
        "household": [],
        "townspeople": [],
    }
    base.update(over)
    return base


def test_the_line_names_the_items_not_the_category():
    plain = format_standing_brief(_brief())
    assert "bread" in plain and "wheat" in plain


def test_it_does_not_offer_food_as_the_thing_eat_takes():
    """The precise regression. `eat('food')` is refused by
    living.py:78-85, so the brief must never put that word where the
    resident reads an item name."""
    plain = format_standing_brief(_brief())
    assert "units of food. `eat`" not in plain
    assert "unit(s) of food. `eat`" not in plain


def test_every_name_the_brief_offers_is_one_eat_accepts():
    """The invariant behind the case, not just the case. Whatever the
    brief names as edible has to be in EDIBLE, or this bug comes back
    wearing a different word."""
    plain = format_standing_brief(_brief(food_items={"bread": 4, "apple": 1}))
    for item in ("bread", "apple"):
        assert item in EDIBLE, f"the brief would offer {item!r}, which `eat` refuses"
        assert item in plain


def test_an_empty_larder_still_gets_its_countdown():
    """DESIGN.md section 3. The zero case is a survival signal and must
    survive this change untouched."""
    plain = format_standing_brief(_brief(food_units=0, food_items={}))
    assert "no food" in plain


def test_an_old_brief_without_food_items_still_renders():
    """A checkpoint written before this field existed must not break a
    tick. Falls back to the count."""
    stale = _brief()
    del stale["food_items"]
    plain = format_standing_brief(stale)
    assert "7" in plain
