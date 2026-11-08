"""One place, two names: the map's and the tools'.

Kasukabe grew two vocabularies for the same ground and nothing ever
reconciled them.

  world/map/expansion.py names the sectors an agent WALKS to:
      "Kasukabe Government Complex", "Kasukabe Temple", "Kasukabe Forest"

  world/charter/models.py and world/tools/locations.py name the places a
  tool GATES on:
      "government_complex", "town_hall", "temple", "forest"

`move` paths by sector name and sets the agent's location to it. Every civic
tool then compares that location against its constant, string-for-string, and
refuses. Observed live, mid-run: FOUR agents standing at "Kasukabe Government
Complex" - physically at the town hall, and unable to vote, petition, or
register a business, because the gate wanted "government_complex".

Nothing here is invented. Every pair below is one constant from the map
module and one from the tool/charter modules, imported rather than retyped,
so a rename on either side is a NameError here and not a town that silently
stops working.

Direction: both names resolve to a shared canonical key. The gate compares
keys, so an agent may walk to the sector the map knows and still use the
verbs the tools gate on - neither vocabulary has to win, and neither module
has to change.
"""

from __future__ import annotations

from world.charter.models import LOCATION_GOVERNMENT_COMPLEX, LOCATION_TOWN_HALL
from world.map.expansion import (
    FARMLAND_SECTOR_NAME,
    FOREST_SECTOR_NAME,
    GOVERNMENT_SECTOR_NAME,
    ORE_OUTCROP_SECTOR_NAME,
    TEMPLE_SECTOR_NAME,
)
from world.tools.locations import LOCATION_FOREST, LOCATION_TEMPLE

SECTOR_TO_TOOL_LOCATION: dict[str, str] = {
    GOVERNMENT_SECTOR_NAME: LOCATION_GOVERNMENT_COMPLEX,
    TEMPLE_SECTOR_NAME: LOCATION_TEMPLE,
    FOREST_SECTOR_NAME: LOCATION_FOREST,
}

SECTOR_CONTAINS_FAMILY: dict[str, str] = {
    FARMLAND_SECTOR_NAME: "farmland",
    ORE_OUTCROP_SECTOR_NAME: "ore_outcrop",
}

ALSO_SATISFIES: dict[str, frozenset[str]] = {
    LOCATION_GOVERNMENT_COMPLEX: frozenset({LOCATION_TOWN_HALL}),
    LOCATION_TOWN_HALL: frozenset({LOCATION_GOVERNMENT_COMPLEX}),
}


import world.tools.locations as _locations  # noqa: E402

_SECTOR_BY_CASEFOLD = {k.casefold(): v for k, v in SECTOR_TO_TOOL_LOCATION.items()}

_GATED_TOOL_LOCATIONS = frozenset(SECTOR_TO_TOOL_LOCATION.values()) | frozenset(
    value
    for name, value in vars(_locations).items()
    if name.startswith("LOCATION_") and isinstance(value, str)
)


TOOL_LOCATION_TO_SECTOR: dict[str, str] = {
    tool_location: sector for sector, tool_location in SECTOR_TO_TOOL_LOCATION.items()
}


def display_place(location_id: str | None) -> str:
    """What to CALL this place when telling a resident where to go.

    TOOL-24. The tool index said "Only at government_complex; `move` takes
    you there" while `list_places` calls the same ground "Kasukabe
    Government Complex". A resident given the first name and looking for
    it in the second list finds nothing - so the one recovery path an
    index row exists to provide (a name, and `move`) pointed at a name the
    map does not use.

    Two hops, because one is not enough. `vote` gates on `town_hall`, and
    the sector table has no entry for it - the map draws ONE building and
    `ALSO_SATISFIES` already records that the hall and the complex are the
    same ground. Without the second hop this returned "town_hall", which
    `list_places` has never heard of, so the fix would have missed the
    very gate a resident votes at.

    Falls back to the id itself: a gated place with no sector on this map
    is still better named than not named.
    """
    if not location_id:
        return "elsewhere"
    direct = TOOL_LOCATION_TO_SECTOR.get(location_id)
    if direct is not None:
        return direct
    for equivalent in ALSO_SATISFIES.get(location_id, frozenset()):
        named = TOOL_LOCATION_TO_SECTOR.get(equivalent)
        if named is not None:
            return named
    return location_id


def canonical_place(location_id: str | None) -> str | None:
    """The shared key for a place, whichever vocabulary named it.

    Case-insensitive, because the vocabularies disagree about case and a
    resident is not choosing between them - it is writing a place name.
    The map draws "Kasukabe Temple"; the tool constant is "temple"; a
    resident typing "Temple" was matching neither, so `pray` and `marry`
    refused it while it was standing in the temple.
    """
    if not location_id:
        return None
    folded = location_id.casefold()
    mapped = _SECTOR_BY_CASEFOLD.get(folded)
    if mapped is not None:
        return mapped
    return location_id if location_id.islower() else _lower_if_known(location_id)


def _lower_if_known(location_id: str) -> str:
    """Lowercase a name that is a tool location under some other casing.

    Deliberately narrow: only collapses case when the lowered form is a
    place the TOOLS already gate on. A resident's invented room keeps the
    capitals it was given, so `canonical_destination`'s folding still has
    something to work with and two different invented places do not merge.
    """
    lowered = location_id.lower()
    if lowered in _GATED_TOOL_LOCATIONS:
        return lowered
    return location_id


def satisfies_gate(actual: str | None, required: str) -> bool:
    """Is an agent at `actual` standing where `required` demands?

    True when the two name the same ground in either vocabulary. The
    string comparison this replaces is why four residents stood in the
    government complex unable to use a single civic tool.
    """
    if actual is None:
        return False
    here = canonical_place(actual)
    want = canonical_place(required)
    if here == want:
        return True
    if want in ALSO_SATISFIES.get(here or "", frozenset()):
        return True
    family = SECTOR_CONTAINS_FAMILY.get(here or "")
    return bool(family and want and want.split(":", 1)[0] == family)


def _business_rows(world):
    """Every real `Business` in the world, and nothing else.

    `world.businesses` is a plain dict and the suite legitimately puts
    non-Business sentinels in it (`test_not_found_names_the_options`
    checks that a malformed row still produces a clear NotFoundError).
    Filtering once here is what lets everything below read `.name` and
    `.location_id` directly instead of reaching for a defaulting
    `getattr` at every use - a three-argument getattr answers its default
    when a field is RENAMED, which is exactly the silent failure
    `test_no_silent_defaults` exists to stop.
    """
    from world.tools.models import Business

    return [b for b in world.businesses.values() if isinstance(b, Business)]


def places_that_satisfy(world, required: str) -> list[str]:
    """Where a resident could stand to satisfy this gate, as walkable names.

    The mirror of `satisfies_gate_in`: that answers "is here good enough",
    this answers "then where". A workshop like `kitchen` or `mill` is not a
    door - it is a room a business provides (TOOL-02) - so a refusal naming
    only the workshop sends a resident somewhere `move` cannot route to.

    Written as "Name (business:id)" because a resident needs the id to move
    and the name to recognise it, the same reason refusals elsewhere run
    through `names.name_mentions`. Empty when no business in this town
    provides it, which is a true and useful answer: the town has no kitchen.
    """
    found: dict[str, str] = {}
    for business in _business_rows(world):
        if required in (business.provides_workshops or ()) and business.location_id:
            found.setdefault(
                business.location_id,
                f"{business.name} ({business.location_id})",
            )
    return sorted(found.values())


def satisfies_gate_in(world, actual: str | None, required: str) -> bool:
    """`satisfies_gate`, plus the workshops the town's businesses provide.

    The pure function above cannot answer "is this a mill?" because being a
    mill is not a property of the string - it is a property of the business
    standing there, which only the world knows. TOOL-02: the miller was at
    `business:b_mill` and `craft` told her she was not at a mill.

    Kept as a separate function rather than folded into `satisfies_gate`
    because `satisfies_gate` is called from places that have no world (the
    charter boundary, tests, the registry's own index rendering) and a
    signature change would ripple through all of them for no gain. The
    registry's gate check is the one caller that both has a world and
    decides whether a resident may act.
    """
    if satisfies_gate(actual, required):
        return True
    if not actual or not required:
        return False
    for business in _business_rows(world):
        if not business.location_id or not satisfies_gate(actual, business.location_id):
            continue
        if required in business.provides_workshops:
            return True
    return _a_finished_building_provides(world, actual, required)


def _a_finished_building_provides(world, actual: str, required: str) -> bool:
    """Whether a COMPLETE building at `actual` serves the `required` gate."""
    from world.tools.models import Building, BuildingStatus

    for building in world.buildings.values():
        if not isinstance(building, Building):
            continue
        if building.status is not BuildingStatus.COMPLETE:
            continue
        plot = world.plots.get(building.plot_id)
        if plot is None or not plot.location_id:
            continue
        if not satisfies_gate(actual, plot.location_id):
            continue
        if canonical_place(building.purpose) == canonical_place(required):
            return True
    return False


def place_for_charter(world, agent_id: str, required: str) -> str:
    """The place to hand a charter engine, given the registry already passed.

    Returns `required` when the agent is standing somewhere that satisfies
    it, so the engine's exact-match check sees what it expects. Returns the
    agent's actual location otherwise, so an engine called without the
    registry in front of it still raises a truthful error naming where the
    resident really is.
    """
    here = world.location_of(agent_id) or ""
    return required if satisfies_gate(here, required) else here


def resolve_business(world, ref: str | None):
    """The business a resident named, whichever spelling it used.

    Accepts the bare key ("b_bakery"), the prefixed form the world itself
    prints ("business:b_bakery"), the registered name ("Thorne's Bakery"),
    the premises it stands at, and a distinctive-word match on the name.
    Returns the `Business`, or None when nothing matched or - deliberately -
    when more than one did.
    """
    if not ref:
        return None
    businesses = world.businesses

    direct = businesses.get(ref)
    if direct is not None:
        return direct
    prefix, sep, rest = ref.partition(":")
    if sep and prefix == "business":
        direct = businesses.get(rest)
        if direct is not None:
            return direct

    def squash(text: str) -> str:
        return "".join(ch for ch in text.lower() if ch.isalnum())

    wanted = squash(ref)
    if not wanted:
        return None
    rows = _business_rows(world)
    named = [b for b in rows if squash(b.name) == wanted]
    if len(named) == 1:
        return named[0]

    sited = [b for b in rows if b.location_id and squash(b.location_id) == wanted]
    if len(sited) == 1:
        return sited[0]

    import re as _re

    def words(text: str) -> set[str]:
        return {w for w in _re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 2}

    target = words(ref)
    if not target:
        return None
    matched = [
        b
        for b in rows
        if (other := words(f"{b.name} {b.business_id}"))
        and (target <= other or other <= target)
    ]
    if len(matched) == 1:
        return matched[0]
    return None


def known_tool_locations() -> frozenset[str]:
    """Location ids the town's own tools mint, as a set.

    Built by CALLING the constructors rather than retyping their output, so
    a change to any of them is picked up here automatically.
    """
    from world.tools import locations as loc

    fixed = {
        loc.LOCATION_TEMPLE, loc.LOCATION_FOREST,
        loc.LOCATION_TOWN_HALL, loc.LOCATION_GOVERNMENT_COMPLEX,
        loc.LOCATION_PATENT_OFFICE,
        loc.mill_location(), loc.loom_house_location(), loc.sawmill_location(),
        loc.smithy_location(), loc.carpentry_location(), loc.kitchen_location(),
    }
    from world.map.model import CROP_VARIETIES, ORE_METALS

    fixed |= {loc.farmland_location(c) for c in CROP_VARIETIES}
    fixed |= {loc.ore_outcrop_location(m) for m in ORE_METALS}
    return frozenset(fixed)


def is_real_destination(world, location_id: str) -> bool:
    """Can an agent actually stand here?

    True for a named place on the map, a resource zone, a location the tools
    mint, and the two prefixed families the world creates per-record -
    "home:h0074" and "business:b_grocery". False for anything a model
    invented, which is the whole point.
    """
    if not location_id:
        return False
    from world.tools.models import Building
    from world.tools.primitives.places import known_places

    if any(p["place_id"] == location_id for p in known_places(world)):
        return True
    if location_id in known_tool_locations():
        return True
    prefix, _, rest = location_id.partition(":")
    if prefix == "home" and rest in getattr(world, "households", {}):
        return True
    if prefix == "business" and rest in getattr(world, "businesses", {}):
        return True
    for business in getattr(world, "businesses", {}).values():
        if getattr(business, "location_id", None) == location_id:
            return True
    for building in world.buildings.values():
        if not isinstance(building, Building):
            continue
        plot = world.plots.get(building.plot_id)
        if plot is not None and plot.location_id == location_id:
            return True
    for storage in getattr(world, "storages", {}).values():
        if getattr(storage, "location_id", None) == location_id:
            return True
    return False
