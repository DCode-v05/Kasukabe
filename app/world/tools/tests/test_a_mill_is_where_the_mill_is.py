"""TOOL-02: the miller could not mill in her own mill.

`craft` gates on "mill", "sawmill", "smithy", "carpentry", "loom_house";
`cook` on "kitchen". Those six were bare strings. No map sector, no tile,
nothing registered there - `locations.py:87-95` said they named "where a
*built* one is expected to register itself", and `build_structure` has been
unreachable since it was written, so nothing ever did.

Meanwhile genesis seeds a mill business with a miller, thirty wheat and
twenty-four flour in stock, standing at `business:b_mill`. Run 23:

    craft: requires location 'mill', actor is at 'business:b_mill'   x2
    craft: requires location 'sawmill', actor is at 'business:b_mill' x2

`craft` refused 18 of 23 calls. `cook` refused 2 of 2. The baker's only
route to an oven was `move("kitchen")` - an opaque jump into an empty
string-room no customer could find - bake, and jump back.

Operator decision, 2026-09-03: the business's premises provides the
workshop its trade needs. Not six stamped sectors, and not "the town must
build them first".
"""

from __future__ import annotations

import pytest

from world.tools.errors import LocationGateError
from world.tools.locations import workshops_for_business_type
from world.tools.models import Business
from world.tools.place_aliases import satisfies_gate, satisfies_gate_in
from world.tools.primitives.places import known_places
from world.tools.state import WorldState

from .conftest import spawn_agent
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map

MILL_AT = "Kasukabe Market"
BAKERY_AT = "Kasukabe Town Square"


async def _mill(world: WorldState, *, owner: str = "n11") -> Business:
    await spawn_agent(world, owner, MILL_AT)
    business = Business(
        business_id="b_mill",
        name="Vance Mill",
        business_type="mill",
        owner_agent_id=owner,
        location_id=MILL_AT,
        provides_workshops={"mill"},
    )
    world.businesses["b_mill"] = business
    await world.ledger.register_business("b_mill", owner, fee_sim=1, tick=world.tick())
    return business


async def _bakery(world: WorldState, *, owner: str = "n07") -> Business:
    await spawn_agent(world, owner, BAKERY_AT)
    business = Business(
        business_id="b_bakery",
        name="Thorne's Bakery",
        business_type="bakery",
        owner_agent_id=owner,
        location_id=BAKERY_AT,
        provides_workshops={"kitchen"},
    )
    world.businesses["b_bakery"] = business
    await world.ledger.register_business("b_bakery", owner, fee_sim=1, tick=world.tick())
    return business


def test_a_trade_brings_its_own_workshop():
    assert workshops_for_business_type("mill") == {"mill"}
    assert workshops_for_business_type("bakery") == {"kitchen"}
    assert workshops_for_business_type("construction") == {"carpentry", "sawmill"}
    assert workshops_for_business_type("grocery") == set()
    assert workshops_for_business_type("nonsense") == set()


@pytest.mark.asyncio
async def test_standing_in_the_mill_satisfies_the_mill_gate(world):
    await _mill(world)

    assert not satisfies_gate(MILL_AT, "mill")
    assert satisfies_gate_in(world, MILL_AT, "mill")


@pytest.mark.asyncio
async def test_a_shop_lends_only_the_workshops_its_trade_has(world):
    await _mill(world)
    assert not satisfies_gate_in(world, MILL_AT, "smithy")
    assert not satisfies_gate_in(world, "Kasukabe Farmland", "mill")


@pytest.mark.asyncio
async def test_two_shops_on_one_square_each_lend_their_own(world):
    """`resolve_business` declines an ambiguous name; the gate must not.

    A bakery and a grocer sharing the market square is not a spelling to
    fix. Standing there is standing in the bakery's kitchen, and the
    grocer's presence does not take that away.
    """
    await _bakery(world)
    world.businesses["b_grocery"] = Business(
        business_id="b_grocery",
        name="Kasukabe Grocery",
        business_type="grocery",
        owner_agent_id="n13",
        location_id=BAKERY_AT,
        provides_workshops=set(),
    )

    assert satisfies_gate_in(world, BAKERY_AT, "kitchen")


@pytest.mark.asyncio
async def test_the_miller_can_mill_standing_in_her_own_mill(world, registry):
    await _mill(world)
    world.inventory.credit("n11", "wheat", 10)

    outcome = await registry.execute(
        world, "n11", "craft", {"recipe_id": "craft:wheat->flour", "batches": 1}
    )

    assert outcome.result
    assert world.inventory.quantity("n11", "flour") > 0


@pytest.mark.asyncio
async def test_the_baker_can_cook_standing_in_her_own_bakery(world, registry):
    await _bakery(world)
    world.inventory.credit("n07", "flour", 10)

    outcome = await registry.execute(
        world, "n07", "cook", {"recipe_id": "cook:flour->bread", "batches": 1}
    )

    assert outcome.result
    assert world.inventory.quantity("n07", "bread") > 0


@pytest.mark.asyncio
async def test_a_stranger_in_the_street_still_cannot_mill(world, registry):
    """The gate has to keep refusing, or it is not a gate."""
    await _mill(world)
    await spawn_agent(world, "n02", "Kasukabe Farmland")
    world.inventory.credit("n02", "wheat", 10)

    with pytest.raises(LocationGateError):
        await registry.execute(
            world, "n02", "craft", {"recipe_id": "craft:wheat->flour", "batches": 1}
        )


@pytest.mark.asyncio
async def test_a_grocer_cannot_bake_in_a_grocery(world, registry):
    await spawn_agent(world, "n13", "Kasukabe Market")
    world.businesses["b_grocery"] = Business(
        business_id="b_grocery",
        name="Kasukabe Grocery",
        business_type="grocery",
        owner_agent_id="n13",
        location_id="Kasukabe Market",
        provides_workshops=workshops_for_business_type("grocery"),
    )
    world.inventory.credit("n13", "flour", 10)

    with pytest.raises(LocationGateError):
        await registry.execute(
            world, "n13", "cook", {"recipe_id": "cook:flour->bread", "batches": 1}
        )


@pytest.mark.asyncio
async def test_a_workshop_a_business_provides_is_not_listed_as_its_own_place(world):
    """GROW-28: `known_places` was the invitation into the empty room.

    A resident who `move`s to "mill" takes the opaque-jump fallback and
    stands alone in a place with no tiles, where nobody can see or speak to
    them and no customer can follow.
    """
    await _mill(world)
    ids = {p["place_id"] for p in known_places(world)}

    assert "mill" not in ids
    assert any("mill" in p.lower() for p in ids)


@pytest.mark.asyncio
async def test_a_workshop_no_business_provides_is_still_listed(world):
    """The town has no smithy yet, and should be told so honestly.

    Kept in the list, and kept as a gate: a town that builds one (GROW-05)
    satisfies it the same way a business does.
    """
    await _mill(world)
    ids = {p["place_id"] for p in known_places(world)}
    assert "smithy" in ids


@pytest.mark.asyncio
async def test_registering_a_bakery_gives_it_a_kitchen(world, registry):
    await spawn_agent(
        world, "n09", "Kasukabe Government Complex", age_years=40.0, household_id="h1"
    )

    await registry.execute(
        world,
        "n09",
        "register_business",
        {
            "business_id": "b_new_bakery",
            "name": "Second Bakery",
            "business_type": "bakery",
            "premises": "Hobbs Cafe",
        },
    )

    assert world.businesses["b_new_bakery"].provides_workshops == {"kitchen"}
    assert satisfies_gate_in(world, "Hobbs Cafe", "kitchen")
