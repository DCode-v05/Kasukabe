"""A "not found" that names no alternatives is a dead end.

"no business with id 'b_smelter'" tells an agent its guess was wrong and
leaves it guessing from the same empty vocabulary. Observed live on a
town with exactly two businesses in it: b_smelter,
government_resource_buyer, harvey_supply_store - three inventions, three
wasted turns, against a list nobody had shown them.
"""

from __future__ import annotations

import pytest

from world.tools.errors import NotFoundError
from world.tools.primitives.commerce import require_business


def test_the_refusal_lists_what_exists() -> None:
    message = str(NotFoundError("business", "b_smelter", ["b_yard", "b_bakery"]))
    assert "b_yard" in message and "b_bakery" in message


def test_it_pluralises_properly() -> None:
    """The message is read by a model that will echo the vocabulary back."""
    assert "businesses" in str(NotFoundError("business", "x", ["b_yard"]))
    assert "businesss" not in str(NotFoundError("business", "x", ["b_yard"]))
    assert "storages" in str(NotFoundError("storage", "x", ["s_barn"]))


def test_with_nothing_to_offer_it_reads_as_before() -> None:
    """Raised bare in dozens of places; none of them may grow a dangling
    clause."""
    assert str(NotFoundError("storage", "home")) == "no storage with id 'home'"


def test_require_business_hands_over_the_real_ids() -> None:
    class World:
        businesses = {"b_yard": object(), "b_bakery": object()}

    with pytest.raises(NotFoundError) as caught:
        require_business(World(), "b_smelter")

    assert set(caught.value.available) == {"b_yard", "b_bakery"}
    assert "b_bakery" in str(caught.value)


def test_the_list_is_read_off_the_world_not_frozen() -> None:
    """A business registered mid-run must appear the moment it exists."""
    class World:
        businesses = {"b_yard": object()}

    world = World()
    world.businesses["b_new"] = object()
    with pytest.raises(NotFoundError) as caught:
        require_business(world, "nope")
    assert "b_new" in caught.value.available
