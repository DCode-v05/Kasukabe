"""Canonical location_id strings (DESIGN.md section 2: "every tool is
location-gated").

world.core.perception.LocationIndex tracks agent position purely as an
opaque string keyed off `agent.moved` / `agent.spawned` event payloads -
it has no opinion on what the string looks like. world.charter.models
already pins three of them (LOCATION_TOWN_HALL, LOCATION_GOVERNMENT_COMPLEX,
LOCATION_PATENT_OFFICE) because charter/'s own docstrings describe this
package (world/tools/, "the future tool dispatcher" charter/models.py's
own comment names) as the thing that will decide the rest and pass
whatever string it decides through to charter's location checks.

This module is that decision, made in exactly one place so every
primitive that needs a location constant imports it from here instead of
inventing its own slug. Values charter/ already fixed are re-exported
unchanged (never redefined) so the two packages can never drift apart;
everything charter/ does not own (temple, resource zones, production
arenas) is named here for the first time, following the same
lowercase-slug convention charter/models.py already established.

A real Tiled-map-backed resolver (turning a WorldMap sector/arena name
into one of these strings, and validating an agent's target destination
against actual map geometry) is future integration work for whoever
wires world/map's genesis classification step to this module - see
`move`'s primitive docstring in primitives/movement.py. Until then these
are the agreed-on names a genesis map is expected to expose (an arena or
sector literally named e.g. "temple", "mill", "smithy") and every
primitive in this package treats location_id as an opaque string, exactly
like charter/ already does.
"""

from __future__ import annotations

from world.charter.models import (
    LOCATION_GOVERNMENT_COMPLEX,
    LOCATION_PATENT_OFFICE,
    LOCATION_TOWN_HALL,
)
from world.map.model import CROP_VARIETIES, ORE_METALS

# -- re-exported from charter/ (never redefined here) -----------------
__all__ = [
    "LOCATION_TOWN_HALL",
    "LOCATION_GOVERNMENT_COMPLEX",
    "LOCATION_PATENT_OFFICE",
    "LOCATION_TEMPLE",
    "LOCATION_FOREST",
    "farmland_location",
    "ore_outcrop_location",
    "mill_location",
    "loom_house_location",
    "sawmill_location",
    "smithy_location",
    "carpentry_location",
    "kitchen_location",
]

# -- new here: not owned by any other module yet -----------------------

# DESIGN.md section 1: "temple" is a genesis building; section 4: "Marriage:
# temple ceremony", section 7: "Temple: ... Weddings, prayers, donations."
LOCATION_TEMPLE = "temple"

# DESIGN.md section 1: "forest (timber, common)" - a single map-edge zone,
# unlike farmland/ore which are per-variety, so one constant suffices.
LOCATION_FOREST = "forest"


def farmland_location(crop: str) -> str:
    """DESIGN.md section 1: "farmland (10 crop varieties)" - one strip per
    crop. Raises ValueError for a crop outside map.model.CROP_VARIETIES so
    a typo'd crop name fails at the call site, not three layers later as a
    location-gate mismatch nobody can explain.
    """
    if crop not in CROP_VARIETIES:
        raise ValueError(f"{crop!r} is not one of the 10 crop varieties {CROP_VARIETIES}")
    return f"farmland:{crop}"


def ore_outcrop_location(metal: str) -> str:
    """DESIGN.md section 1: "ore outcrops (5 metals)" - one outcrop per metal."""
    if metal not in ORE_METALS:
        raise ValueError(f"{metal!r} is not one of the 5 ore metals {ORE_METALS}")
    return f"ore_outcrop:{metal}"


# Production arenas (DESIGN.md section 2: craft is "location gated
# (required sector/arena)"). None of these are genesis buildings
# (DESIGN.md section 1's genesis list has no mill/smithy/kitchen) - they
# are exactly the kind of institution "the town must build" via
# build_structure, so these constants name where a *built* one is
# expected to register itself, the same way LOCATION_TEMPLE names a
# genesis building that already exists on day one. A town with no mill
# yet simply cannot satisfy craft's location gate for a milling recipe -
# that is the intended physics, not a bug.
def mill_location() -> str:
    return "mill"


def loom_house_location() -> str:
    return "loom_house"


def sawmill_location() -> str:
    return "sawmill"


def smithy_location() -> str:
    return "smithy"


def carpentry_location() -> str:
    return "carpentry"


def kitchen_location() -> str:
    return "kitchen"


# Which trade gives a town which workshop (TOOL-02).
#
# The paragraph above was right that DESIGN.md's genesis list has no
# mill/smithy/kitchen, and wrong about the consequence. It concluded "a
# town with no mill yet simply cannot craft - that is the intended
# physics". But genesis seeds a MILL BUSINESS with a miller, thirty wheat
# and twenty-four flour in stock; the town has a mill in every sense
# except the one the gate checks. The intended physics was that a town
# without the trade cannot do the trade, and that is preserved: a town
# with no baker still has no kitchen.
#
# Read by `register_business` for its default and by genesis for the
# seeded shops. A business may hold workshops beyond its type's default -
# the set is the record, this table only says where it starts.
WORKSHOPS_BY_BUSINESS_TYPE: dict[str, frozenset[str]] = {
    "mill": frozenset({"mill"}),
    "bakery": frozenset({"kitchen"}),
    "cafe": frozenset({"kitchen"}),
    "inn": frozenset({"kitchen"}),
    "tavern": frozenset({"kitchen"}),
    "smithy": frozenset({"smithy"}),
    "forge": frozenset({"smithy"}),
    "sawmill": frozenset({"sawmill"}),
    "carpentry": frozenset({"carpentry"}),
    "loom_house": frozenset({"loom_house"}),
    "weaver": frozenset({"loom_house"}),
    # The construction yard works timber and raises frames, so it is both
    # the sawmill and the carpentry shop. Genesis gives the town exactly
    # one of these and `WORKPLACE_BY_OCCUPATION` already sends every
    # construction worker to "carpentry" (GROW-29).
    "construction": frozenset({"carpentry", "sawmill"}),
    "yard": frozenset({"carpentry", "sawmill"}),
    # Deliberately absent: grocery, shop, pharmacy, market. A grocer sells
    # what others made; giving a shop a kitchen would mean anybody who
    # registered "grocery" could bake, and the gate would stop meaning
    # anything.
}


def workshops_for_business_type(business_type: str) -> set[str]:
    """The workshops a newly registered business of this trade provides."""
    return set(WORKSHOPS_BY_BUSINESS_TYPE.get(business_type.strip().lower(), frozenset()))
