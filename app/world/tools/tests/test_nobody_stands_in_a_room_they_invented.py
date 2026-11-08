"""A resident must not be able to walk into a room of its own making.

Run 19, issue 2. The resident table ends with three people standing in
three different spellings of two shops:

    Delia Okonjo   pharmacist      loc_shop_willows
    Silas Vance    sawyer          The Willows Market and Pharmacy
    Junia Vance    grocery_clerk   business:b_grocery

Silas stood in the pharmacy and asked "How do I buy here?" while the
pharmacist who works there stood in it too, under a name the world treats
as somewhere else. Rosalind asked "Seen Marta? Or anyone needing flour?"
into the same silence. Nobody heard either of them.

`loc_shop_willows` appears nowhere in this codebase. Delia invented it,
`move` accepted it - the tool deliberately allows opaque jumps, because
"temple" and "farmland:wheat" are real places with no map sector - and
`canonical_destination` could not fold it, because it folds on shared
words and the agent's word "shop" is not in "The Willows Market and
Pharmacy".

Two holes, and this file covers both:

  * folding gives up when the agent's name for a place uses a synonym,
    even when exactly one place in the whole town shares its distinctive
    word ("willows");
  * a business's premises are somewhere residents work and shop, and
    `list_places` never lists them, so the only way to name one is to
    guess.
"""

from __future__ import annotations

import pytest

from world.tools.primitives.movement import canonical_destination
from world.tools.primitives.places import known_places
from world.tools.state import WorldState

from .conftest import spawn_agent
from world.tools.tests.conftest import needs_the_map

pytestmark = needs_the_map


@pytest.mark.parametrize(
    "invented, expected",
    [
        ("loc_shop_willows", "The Willows Market and Pharmacy"),
        ("willows_shop", "The Willows Market and Pharmacy"),
        ("the willows shop", "The Willows Market and Pharmacy"),
        ("hobbs", "Hobbs Cafe"),
        ("johnson", "Johnson Park"),
    ],
)
def test_a_synonym_still_lands_in_the_real_room(
    world: WorldState, invented: str, expected: str
) -> None:
    assert canonical_destination(world, invented) == expected


def test_an_ambiguous_name_prefers_the_place_over_the_place_named_after_it(
    world: WorldState,
) -> None:
    """"college" matches both "Oak Hill College" and "Dorm for Oak Hill
    College". Liora Thorne spent run 19 at `l_college`, alone. The
    college is the place; the dorm is a place named after it."""
    assert canonical_destination(world, "l_college") == "Oak Hill College"


def test_a_name_matching_nothing_is_still_left_alone(world: WorldState) -> None:
    """The folding rule must not start inventing matches. Three earlier
    attempts to refuse unknown destinations broke real places, so an
    unmatched name is returned unchanged - a miss changes nothing."""
    assert canonical_destination(world, "zzz_not_a_place_at_all") == "zzz_not_a_place_at_all"


def test_folding_never_swallows_a_real_place_into_a_longer_one(
    world: WorldState,
) -> None:
    """The rule that already existed, which the new one must not undo."""
    assert canonical_destination(world, "Oak Hill College") == "Oak Hill College"
    assert canonical_destination(world, "forest") == "forest"
    assert canonical_destination(world, "mill") == "mill"


@pytest.mark.asyncio
async def test_business_premises_are_places_a_resident_can_find(
    world: WorldState, registry
) -> None:
    """Junia Vance is the grocery clerk. Run 19 left her at
    `business:b_grocery` for fifty-nine ticks - a room `list_places` never
    mentions, so no customer could have found it even by trying."""
    from world.tools.models import Business

    world.businesses["b_grocery"] = Business(
        business_id="b_grocery",
        name="Kasukabe Grocery",
        business_type="grocery",
        owner_agent_id="n13",
        employee_agent_ids=set(),
        location_id="business:b_grocery",
    )

    listed = {p["place_id"] for p in known_places(world)}
    assert "business:b_grocery" in listed or "Kasukabe Grocery" in listed, (
        "a shop the town runs is not among the places a resident can be told about"
    )


@pytest.mark.asyncio
async def test_a_shop_is_reachable_by_the_name_people_call_it(
    world: WorldState, registry
) -> None:
    """Residents say "Kasukabe Grocery", not "business:b_grocery"."""
    from world.tools.models import Business

    world.businesses["b_grocery"] = Business(
        business_id="b_grocery",
        name="Kasukabe Grocery",
        business_type="grocery",
        owner_agent_id="n13",
        employee_agent_ids=set(),
        location_id="business:b_grocery",
    )

    landed = canonical_destination(world, "Kasukabe Grocery")
    assert landed in {"business:b_grocery", "Kasukabe Grocery"}, (
        f"walking to the shop by its own name lands at {landed!r}"
    )


@pytest.mark.asyncio
async def test_two_residents_who_spell_the_shop_differently_can_hear_each_other(
    world: WorldState, registry
) -> None:
    """The whole point. Run 19's exact failure, end to end."""
    await spawn_agent(world, "n16", canonical_destination(world, "loc_shop_willows"))
    await spawn_agent(world, "n12", canonical_destination(world, "The Willows Market and Pharmacy"))
    world.agent_names.update({"n16": "Delia Okonjo", "n12": "Silas Vance"})

    outcome = await registry.execute(world, "n12", "say", {
        "content": "Need food. How do I buy here?",
    })
    assert "Delia Okonjo" in outcome.result["heard_by"], (
        "the shopkeeper is standing in her own shop and did not hear the customer"
    )
