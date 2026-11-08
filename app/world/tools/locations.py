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


LOCATION_TEMPLE = "temple"

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
    "construction": frozenset({"carpentry", "sawmill"}),
    "yard": frozenset({"carpentry", "sawmill"}),
}


def workshops_for_business_type(business_type: str) -> set[str]:
    """The workshops a newly registered business of this trade provides."""
    return set(WORKSHOPS_BY_BUSINESS_TYPE.get(business_type.strip().lower(), frozenset()))
