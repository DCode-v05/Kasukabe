"""One place, two names: the map's and the tools'.

world/map/expansion.py names the sectors an agent WALKS to; charter/models.py
and tools/locations.py name the places a tool GATES on. `move` paths by
sector name and sets the agent's location to it; every civic tool then
compared that string against its constant and refused.

Observed live, mid-run: four agents standing at "Kasukabe Government
Complex" - physically inside the town hall, and unable to vote, petition or
register a business, because the gate wanted "government_complex".
"""

from __future__ import annotations

import pytest

from world.charter.models import LOCATION_GOVERNMENT_COMPLEX, LOCATION_TOWN_HALL
from world.map.expansion import (
    FARMLAND_SECTOR_NAME,
    FOREST_SECTOR_NAME,
    GOVERNMENT_SECTOR_NAME,
    TEMPLE_SECTOR_NAME,
)
from world.tools.locations import LOCATION_FOREST, LOCATION_TEMPLE, farmland_location
from world.tools.place_aliases import canonical_place, satisfies_gate


@pytest.mark.parametrize(
    ("standing_at", "gate"),
    [
        (GOVERNMENT_SECTOR_NAME, LOCATION_GOVERNMENT_COMPLEX),
        (TEMPLE_SECTOR_NAME, LOCATION_TEMPLE),
        (FOREST_SECTOR_NAME, LOCATION_FOREST),
    ],
)
def test_the_sector_you_walked_to_satisfies_the_gate(standing_at, gate):
    assert satisfies_gate(standing_at, gate)


@pytest.mark.parametrize(
    ("standing_at", "gate"),
    [
        (LOCATION_GOVERNMENT_COMPLEX, LOCATION_GOVERNMENT_COMPLEX),
        (LOCATION_TEMPLE, LOCATION_TEMPLE),
    ],
)
def test_the_constant_still_satisfies_its_own_gate(standing_at, gate):
    """The existing vocabulary must keep working untouched."""
    assert satisfies_gate(standing_at, gate)


def test_the_town_hall_is_inside_the_government_complex():
    """One building; the charter gates some acts on the hall and some on
    the complex. An agent standing in it is standing in both."""
    assert satisfies_gate(GOVERNMENT_SECTOR_NAME, LOCATION_TOWN_HALL)
    assert satisfies_gate(LOCATION_TOWN_HALL, LOCATION_GOVERNMENT_COMPLEX)
    assert satisfies_gate(LOCATION_GOVERNMENT_COMPLEX, LOCATION_TOWN_HALL)


def test_a_gate_still_refuses_somewhere_else():
    """The point is to stop refusing people who ARE there - not to stop
    refusing people who are not."""
    assert not satisfies_gate("Hobbs Cafe", LOCATION_GOVERNMENT_COMPLEX)
    assert not satisfies_gate("Hobbs Cafe", LOCATION_TEMPLE)
    assert not satisfies_gate(None, LOCATION_TEMPLE)


def test_the_farmland_sector_contains_its_crop_bands():
    """REVERSED, deliberately. This test used to assert the opposite, on the
    grounds that folding the sector "would let an agent at the edge of the
    fields harvest a crop it is nowhere near". That cost is real and is
    accepted here.

    What holding the line cost instead: the map draws one place called
    "Kasukabe Farmland", so that is where an agent walks and what
    `look_around` reports back. The harvest gate wanted "farmland:wheat" and
    refused - a resident standing in the middle of the fields, told by the
    tool that it was not in the fields. `harvest: requires location
    'farmland:wheat', actor is at 'Kasukabe Farmland'` was among the last
    recurring refusals in the town, and not one an agent could see coming.

    The sector is still its own name (nothing is folded away); it simply no
    longer fails a gate for a place it physically contains.
    """
    assert satisfies_gate(FARMLAND_SECTOR_NAME, farmland_location("wheat"))
    assert canonical_place(FARMLAND_SECTOR_NAME) == FARMLAND_SECTOR_NAME


def test_an_unknown_place_is_left_as_itself():
    assert canonical_place("Hobbs Cafe") == "Hobbs Cafe"
    assert canonical_place(None) is None
    assert canonical_place("") is None


# ── walking somewhere that does not exist ───────────────────────────────
#
# `move` allowed an opaque jump to ANY string, which is right for the town's
# own non-sector locations and wrong for everything else. Observed live, at
# one moment, alongside four residents correctly at "Harvey Oak Supply Store":
#
#     'store_harvey_oak'  'loc_farmland'  'place:t_temple'  'marketplace'
#
# One resident each, every one standing alone in a place of its own making
# where nobody could see them, speak to them, or trade with them.

@pytest.mark.asyncio
async def test_the_towns_own_non_sector_locations_are_real():
    """temple, mill, farmland:wheat have no sector of their own and are
    still real places with gates on them. Refusing these would break
    working tools, which is what makes this a set and not a map lookup."""
    from world.tools.bootstrap import build_world
    from world.tools.place_aliases import is_real_destination

    world, _ = await build_world()
    for place in ("temple", "mill", "kitchen", "farmland:wheat", "ore_outcrop:iron"):
        assert is_real_destination(world, place), place


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invented",
    ["store_harvey_oak", "loc_farmland", "place:t_temple", "marketplace", ""],
)
async def test_an_invented_place_is_not_real(invented):
    from world.tools.bootstrap import build_world
    from world.tools.place_aliases import is_real_destination

    world, _ = await build_world()
    assert not is_real_destination(world, invented)


@pytest.mark.asyncio
async def test_a_business_names_its_own_shop():
    """"business:b_grocery" is one spelling of several - a business may name
    its shop anything, and that name is somewhere to stand."""
    from world.tools.bootstrap import build_world
    from world.tools.models import Business
    from world.tools.place_aliases import is_real_destination

    world, _ = await build_world()
    world.businesses["b1"] = Business(
        business_id="b1", name="Grocery", business_type="grocery_store",
        owner_agent_id="a0001", location_id="grocery-shop",
    )
    assert is_real_destination(world, "grocery-shop")
    assert is_real_destination(world, "business:b1")


@pytest.mark.asyncio
async def test_the_tool_location_set_is_built_by_calling_the_constructors():
    """Retyping their output is how the map and the tools drifted apart in
    the first place."""
    from world.tools.locations import farmland_location, mill_location
    from world.tools.place_aliases import known_tool_locations

    known = known_tool_locations()
    assert mill_location() in known
    assert farmland_location("wheat") in known
    assert len(known) == 26, "10 crops + 5 metals + 11 fixed places"


# ── a sector contains the places it contains ────────────────────────────


def test_standing_in_the_farmland_satisfies_a_crop_strip() -> None:
    """The map draws one place called "Kasukabe Farmland" - that is where an
    agent walks and what `look_around` reports. The harvest gate then wanted
    "farmland:wheat" and refused, telling a resident standing in the middle
    of the fields that it was not in the fields.

    A reversal of this module's earlier rule, taken deliberately: the price
    is that someone at the edge of the fields can harvest a strip they are
    not literally standing on.
    """
    from world.tools.place_aliases import satisfies_gate

    assert satisfies_gate("Kasukabe Farmland", "farmland:wheat")
    assert satisfies_gate("Kasukabe Ore Outcrop", "ore_outcrop:copper")


def test_a_sector_does_not_satisfy_another_sector_family() -> None:
    from world.tools.place_aliases import satisfies_gate

    assert not satisfies_gate("Kasukabe Farmland", "ore_outcrop:copper")
    assert not satisfies_gate("Kasukabe Ore Outcrop", "farmland:wheat")
    assert not satisfies_gate("Kasukabe Forest", "farmland:wheat")


def test_one_crop_strip_is_still_not_another() -> None:
    """Containment runs sector-to-strip only. Standing in the rice does not
    put you in the wheat."""
    from world.tools.place_aliases import satisfies_gate

    assert not satisfies_gate("farmland:rice", "farmland:wheat")


def test_an_unrelated_place_satisfies_nothing() -> None:
    from world.tools.place_aliases import satisfies_gate

    assert not satisfies_gate("Hobbs Cafe", "farmland:wheat")
    assert not satisfies_gate("Kasukabe Farmland", "mill")


def test_the_containment_map_names_real_sectors() -> None:
    """Built from map/expansion.py's own constants, so a renamed sector
    cannot leave this silently pointing at a place that no longer exists."""
    from world.map.expansion import FARMLAND_SECTOR_NAME, ORE_OUTCROP_SECTOR_NAME
    from world.tools.place_aliases import SECTOR_CONTAINS_FAMILY

    assert set(SECTOR_CONTAINS_FAMILY) == {FARMLAND_SECTOR_NAME, ORE_OUTCROP_SECTOR_NAME}
