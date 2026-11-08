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

# sector name (what you walk to) -> tool constant (what gates on it).
#
# Still NOT here: "Kasukabe Farmland" and "Kasukabe Ore Outcrop". Their tool
# constants are per-variety - farmland_location("wheat") is "farmland:wheat" -
# so the sector is a region containing ten gated places, not another name for
# one, and a one-to-one fold would have to pick a crop the agent never named.
# SECTOR_CONTAINS_FAMILY below handles them as the containment they actually
# are.
SECTOR_TO_TOOL_LOCATION: dict[str, str] = {
    GOVERNMENT_SECTOR_NAME: LOCATION_GOVERNMENT_COMPLEX,
    TEMPLE_SECTOR_NAME: LOCATION_TEMPLE,
    FOREST_SECTOR_NAME: LOCATION_FOREST,
}

# sector -> the family of gated places it physically contains.
#
# A REVERSAL, recorded rather than quietly deleted. The note above used to
# argue these two sectors must not satisfy their own varieties, because
# "folding them would let an agent standing at the edge of the fields harvest
# a crop it is nowhere near". That objection is real and is the price paid
# here.
#
# What it cost to hold the line: the map draws one place called "Kasukabe
# Farmland", so that is where an agent walks and what `look_around` reports.
# The harvest gate then wanted "farmland:wheat" and refused - a resident
# standing in the middle of the fields, told by the tool that it was not in
# the fields. Measured across runs: `harvest: requires location
# 'farmland:wheat', actor is at 'Kasukabe Farmland'` and `mine: requires
# location 'ore_outcrop:copper', actor is at 'Kasukabe Ore Outcrop'` were the
# last recurring refusals left in the town, and neither was a mistake the
# agent could see coming.
#
# The containment is true on this map: expansion.py builds the ten crop strips
# and the five outcrops inside these two sectors. Standing in the sector is
# standing among them. The specific strips remain distinct PLACES an agent can
# walk to and be seen at; they are simply no longer a gate the sector fails.
SECTOR_CONTAINS_FAMILY: dict[str, str] = {
    FARMLAND_SECTOR_NAME: "farmland",
    ORE_OUTCROP_SECTOR_NAME: "ore_outcrop",
}

# The town hall is inside the government complex on this map: there is one
# building and the charter gates some acts on the hall and some on the
# complex. An agent standing in it is standing in both.
ALSO_SATISFIES: dict[str, frozenset[str]] = {
    LOCATION_GOVERNMENT_COMPLEX: frozenset({LOCATION_TOWN_HALL}),
    LOCATION_TOWN_HALL: frozenset({LOCATION_GOVERNMENT_COMPLEX}),
}


# Case-folded view of the sector table, built once. A resident writes what
# a person would write - "Temple", "Kasukabe temple", "the Mill" - and the
# map's own spelling is "Kasukabe Temple". Keying the lookup on exact case
# meant "Temple" folded to nothing and every gate on that place refused a
# resident standing in it.
import world.tools.locations as _locations  # noqa: E402

_SECTOR_BY_CASEFOLD = {k.casefold(): v for k, v in SECTOR_TO_TOOL_LOCATION.items()}

# The lowercase place names the tools actually gate on: every value the
# sector table folds to, plus every LOCATION_* constant. Derived rather
# than listed, so a new gated place is covered the day it is added.
_GATED_TOOL_LOCATIONS = frozenset(SECTOR_TO_TOOL_LOCATION.values()) | frozenset(
    value
    for name, value in vars(_locations).items()
    if name.startswith("LOCATION_") and isinstance(value, str)
)


# TOOL-24. The reverse of SECTOR_TO_TOOL_LOCATION, derived rather than
# typed: the map above stays the one definition, and this cannot drift
# from it.
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
    # Not a sector. Return the id itself, but folded to a single case so two
    # spellings of the same tool location still compare equal.
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
    # Standing in the sector is standing among the places it contains.
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
            # `name` is required on Business, so it is read directly: a
            # getattr default here could only ever hide a rename, which is
            # what `test_no_silent_defaults` exists to stop.
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
    # Scanned, not resolved. `resolve_business` declines when two candidates
    # match - correct when a resident NAMES a shop, wrong here: if a bakery
    # and a grocer both trade on the market square, standing there is
    # standing in the bakery's kitchen, and the grocer's presence does not
    # take that away. Any business whose premises is where the resident is
    # lends its workshops to the ground.
    for business in _business_rows(world):
        if not business.location_id or not satisfies_gate(actual, business.location_id):
            continue
        if required in business.provides_workshops:
            return True
    # GROW-03. A building the town BUILT is a place, not a row.
    #
    # `build_structure` takes a purpose - "smithy", "kitchen", "mill" - and
    # `complete_construction` flips the building to COMPLETE. Nothing then
    # made standing there satisfy the gate that purpose names, so a
    # finished smithy was a database row: the resident who built it still
    # could not forge in it, and the four findings that stop this town
    # building anything included "a completed smithy would satisfy no gate".
    #
    # Same rule the businesses above get, for the same reason: being a
    # smithy is a property of what stands on the ground, not of the string.
    return _a_finished_building_provides(world, actual, required)


def _a_finished_building_provides(world, actual: str, required: str) -> bool:
    """Whether a COMPLETE building at `actual` serves the `required` gate."""
    from world.tools.models import Building, BuildingStatus

    # Direct access, not a getattr default: `buildings` is a real field on
    # WorldState, and a default here would answer "no buildings" if it were
    # ever renamed - silently putting GROW-03 back.
    for building in world.buildings.values():
        if not isinstance(building, Building):
            continue
        if building.status is not BuildingStatus.COMPLETE:
            continue
        # A building's ground is the plot it was raised on.
        plot = world.plots.get(building.plot_id)
        if plot is None or not plot.location_id:
            continue
        if not satisfies_gate(actual, plot.location_id):
            continue
        if canonical_place(building.purpose) == canonical_place(required):
            return True
    return False


# TOOL-08: the registry folds, the charter does not, and the charter is
# where the refusal lands.
#
# `elections.py:194`, `government.py:72`, `organizations.py:53`,
# `land_petitions.py:61` and `patent_office.py:61,89` all compare the
# location string exactly:
#
#     if location != LOCATION_TOWN_HALL:
#         raise NotAtRequiredLocation(...)
#
# The registry's own gate has already passed by then - `satisfies_gate`
# folds "Kasukabe Government Complex" onto `government_complex`, and
# ALSO_SATISFIES folds the complex onto the town hall, because on this map
# they are one building. So the tool admits the resident and the engine
# behind it throws them out, and `CharterError` is not a `ToolError`, so
# the refusal reaches neither `tool_refusals` nor `gap_feedback`
# (registry.py:655-670). It is invisible in every instrument the town has.
#
# This is why the whole civic surface has never been called in 23 runs:
# not apathy, and not only the vacant seats (GOV-01) - `vote`,
# `declare_candidacy`, `register_org` and `define_tool` need no seat at all
# and could not succeed from the place `list_places` names.
#
# The fix goes here rather than in `charter/`: this module already imports
# FROM charter (the constants at the top), so charter importing
# `satisfies_gate` back would be a cycle. And the handler is the honest
# place for it - the registry proved the gate one frame earlier, so the
# handler is passing on a fact it already has.
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


# A business has as many names as a place does, and the same thing went
# wrong.
#
# `world.businesses` is keyed on the bare id - "b_bakery". But the world
# TEACHES the prefixed form everywhere a resident can read one: genesis
# places every shop at "business:b_bakery" (run_genesis.py:3455,3467), the
# standing brief says "You are at business:b_bakery", and `post_price`'s own
# success payload advertises `visible_at: "business:b_bakery"`. So the
# resident says back the spelling the town gave it, and the town refuses.
#
# Observed live in run 23, with seventeen people waiting in her shop:
#
#     post_price(on_behalf_of_business="business:b_bakery")
#         -> no business with id 'business:b_bakery'
#     buy(seller_id="business:b_bakery")
#         -> no seller or buyer with id 'business:b_bakery'
#         - the seller or buyers here are: b_bakery, ...
#
# Twenty loaves sat in stock and the baker could not put a price on them.
#
# Same direction as `satisfies_gate` above: fold, never refuse. Every rule
# here resolves to a business that already exists, and a miss returns None
# so the caller raises exactly the NotFoundError it raised before - the
# failure mode is unchanged, only the success set grows.
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

    # The key, and the prefixed spelling of the key. Exact wins outright:
    # a business literally named "b_mill" must not be folded away.
    direct = businesses.get(ref)
    if direct is not None:
        return direct
    prefix, sep, rest = ref.partition(":")
    if sep and prefix == "business":
        direct = businesses.get(rest)
        if direct is not None:
            return direct

    # The registered name, case- and space-insensitively.
    def squash(text: str) -> str:
        return "".join(ch for ch in text.lower() if ch.isalnum())

    wanted = squash(ref)
    if not wanted:
        return None
    # `_business_rows` rather than `businesses.values()`: a malformed row
    # must produce the same clear NotFoundError it always did, not an
    # AttributeError five frames from the tool the resident called.
    rows = _business_rows(world)
    named = [b for b in rows if squash(b.name) == wanted]
    if len(named) == 1:
        return named[0]

    # The premises. Only when one business stands there - two shops sharing
    # a sector is not a spelling to fix, it is a guess to decline.
    sited = [b for b in rows if b.location_id and squash(b.location_id) == wanted]
    if len(sited) == 1:
        return sited[0]

    # A distinctive word or two: "bakery" for "Thorne's Bakery", "harvey oak
    # supply" for "Harvey Oak Supply Store". Same one-candidate discipline
    # `canonical_destination` holds to, and for the same reason.
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


# Every location string the town itself uses as a destination or a gate.
#
# `move` deliberately allows an "opaque jump" to a location that is not a map
# sector - "temple", "mill", "farmland:wheat" are real gated places with no
# sector of their own, and refusing them would break working tools. But that
# same latitude let agents invent destinations wholesale. Observed live, at
# one moment, alongside four residents correctly at "Harvey Oak Supply Store":
#
#     'store_harvey_oak'   1 person   - invented, invisible to the other four
#     'loc_farmland'       1 person   - invented
#     'place:t_temple'     1 person   - invented
#     'marketplace'        1 person   - invented
#
# A resident who walks somewhere that does not exist stands alone in a place
# of its own making: nobody can see it, nobody can speak to it, and no gate it
# needs will ever open. This set is the difference between "a destination the
# town has but the map does not draw" and "a string the model made up".
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
    # The per-variety families. The crop and metal lists are the world's own,
    # not a list typed here.
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
    # Records the world mints as it runs. Neither is on the map nor in the
    # constants, and a business names its own shop - "grocery-shop" - so the
    # "business:<id>" form is one spelling of several rather than the rule.
    prefix, _, rest = location_id.partition(":")
    if prefix == "home" and rest in getattr(world, "households", {}):
        return True
    if prefix == "business" and rest in getattr(world, "businesses", {}):
        return True
    for business in getattr(world, "businesses", {}).values():
        if getattr(business, "location_id", None) == location_id:
            return True
    # Direct access, not a getattr default: `buildings` is a real field on
    # WorldState, and a default here would answer "no buildings" if it were
    # ever renamed - silently putting GROW-03 back.
    #
    # GROW-16. A building's ground is the PLOT it was raised on.
    #
    # The two lines under that comment used to fetch a location id off the
    # BUILDING through a three-argument getattr, and `Building`
    # (tools/models.py:314) carries no such field - it has `plot_id` and
    # `footprint`. So the default answered None on every iteration,
    # `None == location_id` was never once true, and the branch was dead:
    # a building the town raised, paid for and finished was never
    # somewhere a resident could walk to. The three-argument getattr is
    # exactly what hid it, two lines below a comment explaining why the
    # line above must not use one.
    #
    # The plot is the same answer `_a_finished_building_provides` already
    # reaches for the gate question, and for the same reason.
    for building in world.buildings.values():
        if not isinstance(building, Building):
            continue          # the suite puts sentinels in this dict
        plot = world.plots.get(building.plot_id)
        if plot is not None and plot.location_id == location_id:
            return True
    # A storage is somewhere a thing was left, so it is somewhere to stand.
    for storage in getattr(world, "storages", {}).values():
        if getattr(storage, "location_id", None) == location_id:
            return True
    return False
