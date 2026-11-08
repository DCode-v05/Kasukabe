"""A location gate names a door, not just a room.

`cook: requires location 'kitchen', actor is at 'pharmacy'` tells a resident
the name of a room it cannot walk to. A kitchen is not a place in this world:
it is a workshop a BUSINESS provides (TOOL-02), and which business provides it
differs from town to town, so `move` has never accepted "kitchen" as a
destination and `list_places` has never offered one.

election30 is what that costs. Fourteen cook attempts in two days, half of
them failures, tried at the pharmacy and at the construction yard - and the
town finished holding 78 flour and 2 bread, having eaten mostly raw wheat.
`locations.py` had already written the same sentence about the baker: "the
baker's only oven was an opaque string-room no customer could walk to."

Same rule as the recipe list, the storage list and the open-sites list: a
refusal teaches or it is noise.
"""

from __future__ import annotations

import pytest

from world.tools.errors import LocationGateError
from world.tools.models import Business
from world.tools.locations import kitchen_location
from world.tools.place_aliases import places_that_satisfy
from world.tools.tests.conftest import spawn_agent

PHARMACY = "pharmacy"


def _a_business_providing(world, workshop: str, business_id: str, name: str):
    """Give the town one business that provides `workshop`, at its own
    premises - which is how every kitchen in Kasukabe exists."""
    made = Business(
        business_id=business_id,
        name=name,
        business_type="cafe",
        owner_agent_id="n99",
        location_id=f"business:{business_id}",
        provides_workshops={workshop},
    )
    world.businesses[business_id] = made
    return made


@pytest.mark.asyncio
async def test_a_town_with_no_kitchen_says_so_by_saying_nothing(world) -> None:
    """An empty list is a true answer, not a missing one: some towns have
    no kitchen, and inventing a destination would be worse than silence."""
    for business in world.businesses.values():
        business.provides_workshops = set()

    assert places_that_satisfy(world, kitchen_location()) == []


@pytest.mark.asyncio
async def test_the_kitchen_is_named_with_somewhere_to_walk_to(world) -> None:
    for business in world.businesses.values():
        business.provides_workshops = set()
    _a_business_providing(world, kitchen_location(), "b_cafe", "Kasukabe Cafe")

    named = places_that_satisfy(world, kitchen_location())

    assert len(named) == 1
    said = named[0]
    assert "Kasukabe Cafe" in said, "a resident needs the name to recognise it"
    assert "business:b_cafe" in said, "and the id, because that is what `move` takes"


@pytest.mark.asyncio
async def test_every_kitchen_in_town_is_offered(world) -> None:
    """A resident choosing where to bake needs the list, not one of them."""
    for business in world.businesses.values():
        business.provides_workshops = set()
    _a_business_providing(world, kitchen_location(), "b_cafe", "Kasukabe Cafe")
    _a_business_providing(world, kitchen_location(), "b_bakery", "Whitlock Bakery")

    named = places_that_satisfy(world, kitchen_location())

    assert len(named) == 2
    assert any("Whitlock Bakery" in n for n in named)
    assert any("Kasukabe Cafe" in n for n in named)


@pytest.mark.asyncio
async def test_a_cook_in_the_wrong_place_is_told_the_right_one(world, registry) -> None:
    """The election30 case end to end: cooking at the pharmacy."""
    for business in world.businesses.values():
        business.provides_workshops = set()
    _a_business_providing(world, kitchen_location(), "b_cafe", "Kasukabe Cafe")
    await spawn_agent(world, "n01", PHARMACY)
    world.inventory.credit("n01", "flour", 10)

    with pytest.raises(LocationGateError) as caught:
        await registry.execute(
            world, "n01", "cook", {"recipe_id": "cook:flour->bread", "batches": 1}
        )

    said = str(caught.value)
    assert "business:b_cafe" in said, (
        f"told the name of a room it cannot walk to, and nothing else: {said}"
    )


@pytest.mark.asyncio
async def test_the_refusal_still_says_what_was_wrong(world, registry) -> None:
    """The new sentence is added to the old one, not instead of it - the
    required and actual places are what make the refusal diagnosable."""
    error = LocationGateError("cook", "kitchen", "pharmacy", ["Kasukabe Cafe (business:b_cafe)"])

    said = str(error)

    assert "kitchen" in said and "pharmacy" in said
    assert "Kasukabe Cafe" in said
    assert error.required == "kitchen" and error.actual == "pharmacy"


def test_a_gate_with_nowhere_to_go_reads_as_it_always_did() -> None:
    """No trailing "go to" fragment when the list is empty."""
    said = str(LocationGateError("cook", "kitchen", "pharmacy"))

    assert said.endswith("actor is at 'pharmacy'"), said
