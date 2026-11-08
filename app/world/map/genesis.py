"""Kasukabe's genesis map config: which of the base Ville's buildings are
open for business on day one, plus minting the buildings and resource
zones the base map does not contain at all.

DESIGN.md section 1: "Genesis buildings only: houses, cafe, grocery
store, pharmacy, park, college, office spaces, streets/public spaces,
government complex, temple. Deliberately absent: hospital, police station
- the town must build its institutions." Plus: "Resource zones at map
edges (not buildings): farmland (10 crop varieties), ore outcrops (5
metals), forest (timber, common)."

The base Ville map (loader.load_ville_map) was built for a different
project and contains buildings DESIGN.md's genesis list does not
mention (a pub) and is missing buildings DESIGN.md requires that were
never part of it (a government complex, a temple). Reconciling the two
is this module's whole job, in two steps:

1. classify every loaded Sector by name and mark it active/inactive
   against Kasukabe's genesis-active category set (apply_genesis_config).
   A sector classified into a category outside that set (the pub) stays
   on the map, fully walkable and modeled, just closed - DESIGN.md
   section 3's "all business types contestable" already covers someone
   re-registering it later.
2. mint the missing genesis-only sectors (government complex, temple,
   the three resource zones) by growing the map at its south edge and
   carving them in, via expansion.append_kasukabe_genesis_zones.

build_kasukabe_genesis_map does both and is the one function every other
module should call to get Kasukabe's actual starting WorldMap; loader.py
and this module's classify_sector are reusable independent of that
top-level entry point (a test, or a future re-classification pass after
a charter amendment changes what "active" means, can call them directly).
"""

from __future__ import annotations

from pathlib import Path

from world.map.blocks import load_special_blocks
from world.map.expansion import (
    DEFAULT_GENESIS_STRIP_DEPTH,
    FARMLAND_SECTOR_NAME,
    FOREST_SECTOR_NAME,
    GOVERNMENT_SECTOR_NAME,
    ORE_OUTCROP_SECTOR_NAME,
    TEMPLE_SECTOR_NAME,
    GenesisZoneIds,
    append_kasukabe_genesis_zones,
    open_walkable_corridors,
)
from world.map.loader import load_ville_map
from world.map.model import BuildingCategory, ResourceKind, WorldMap
from world.map.tiled_json import build_world_map_from_tiled, load_tiled_map

# DESIGN.md section 1's genesis-active building list, in this module's
# vocabulary. STREET has no Sector (WorldMap.is_street), so it is not a
# gate any sector's classification needs to pass - it is listed here only
# so callers that want "every genesis-active category" in one place
# (a future tool-registry gate, say) get the complete set including it.
GENESIS_ACTIVE_CATEGORIES: frozenset[BuildingCategory] = frozenset(
    {
        BuildingCategory.HOUSE,
        BuildingCategory.CAFE,
        BuildingCategory.GROCERY,
        BuildingCategory.PHARMACY,
        BuildingCategory.PARK,
        BuildingCategory.COLLEGE,
        BuildingCategory.OFFICE,
        BuildingCategory.STREET,
        BuildingCategory.GOVERNMENT,
        BuildingCategory.TEMPLE,
    }
)

# Every one of the base Ville map's 19 sectors, classified by hand against
# DESIGN.md section 1's genesis list, plus the 5 sectors this module mints
# itself (see expansion.py's *_SECTOR_NAME constants). Checked exhaustively
# against the real data (this task's own investigation of
# matrix/special_blocks/sector_blocks.csv confirms exactly these 19 names,
# no more, no fewer) - a name not in this table falls through to
# FALLBACK_KEYWORD_RULES below, which exists for sectors a future map
# expansion introduces that this table was never updated for, not for the
# base map itself.
KNOWN_SECTOR_CATEGORIES: dict[str, frozenset[BuildingCategory]] = {
    "artist's co-living space": frozenset({BuildingCategory.HOUSE}),
    "Arthur Burton's apartment": frozenset({BuildingCategory.HOUSE}),
    "Ryan Park's apartment": frozenset({BuildingCategory.HOUSE}),
    "Isabella Rodriguez's apartment": frozenset({BuildingCategory.HOUSE}),
    "Giorgio Rossi's apartment": frozenset({BuildingCategory.HOUSE}),
    "Carlos Gomez's apartment": frozenset({BuildingCategory.HOUSE}),
    "The Rose and Crown Pub": frozenset({BuildingCategory.PUB}),
    "Hobbs Cafe": frozenset({BuildingCategory.CAFE}),
    "Oak Hill College": frozenset({BuildingCategory.COLLEGE}),
    "Johnson Park": frozenset({BuildingCategory.PARK}),
    "Harvey Oak Supply Store": frozenset({BuildingCategory.OFFICE}),
    "The Willows Market and Pharmacy": frozenset(
        {BuildingCategory.GROCERY, BuildingCategory.PHARMACY}
    ),
    "Adam Smith's house": frozenset({BuildingCategory.HOUSE}),
    "Yuriko Yamamoto's house": frozenset({BuildingCategory.HOUSE}),
    "Moore family's house": frozenset({BuildingCategory.HOUSE}),
    "Tamara Taylor and Carmen Ortiz's house": frozenset({BuildingCategory.HOUSE}),
    "Moreno family's house": frozenset({BuildingCategory.HOUSE}),
    "Lin family's house": frozenset({BuildingCategory.HOUSE}),
    # Institutionally owned student housing, not a family home - grouped
    # with the college rather than BuildingCategory.HOUSE so the
    # institution that runs it (Oak Hill College) governs both.
    "Dorm for Oak Hill College": frozenset({BuildingCategory.COLLEGE}),
    GOVERNMENT_SECTOR_NAME: frozenset({BuildingCategory.GOVERNMENT}),
    TEMPLE_SECTOR_NAME: frozenset({BuildingCategory.TEMPLE}),
}

# Ordered (keyword, category) fallback rules for a sector name this
# module has never seen (KNOWN_SECTOR_CATEGORIES misses it): a licensed
# builder's newly registered sector name, or a future map's different
# naming. All matching keywords apply (a name can hit more than one rule,
# same as "The Willows Market and Pharmacy" hits both grocery and
# pharmacy by hand above), keyword checks are case-insensitive substrings.
FALLBACK_KEYWORD_RULES: tuple[tuple[str, BuildingCategory], ...] = (
    ("pub", BuildingCategory.PUB),
    ("cafe", BuildingCategory.CAFE),
    ("pharmacy", BuildingCategory.PHARMACY),
    ("grocery", BuildingCategory.GROCERY),
    ("market", BuildingCategory.GROCERY),
    ("college", BuildingCategory.COLLEGE),
    ("dorm", BuildingCategory.COLLEGE),
    ("school", BuildingCategory.COLLEGE),
    ("park", BuildingCategory.PARK),
    ("temple", BuildingCategory.TEMPLE),
    ("sanctuary", BuildingCategory.TEMPLE),
    ("government", BuildingCategory.GOVERNMENT),
    ("town hall", BuildingCategory.GOVERNMENT),
    ("office", BuildingCategory.OFFICE),
    ("store", BuildingCategory.OFFICE),
    ("shop", BuildingCategory.OFFICE),
    ("house", BuildingCategory.HOUSE),
    ("apartment", BuildingCategory.HOUSE),
    ("co-living", BuildingCategory.HOUSE),
    ("family", BuildingCategory.HOUSE),
    # ── what the town builds for itself (GROW-15) ─────────────────────
    #
    # `MapService.stamp_building` names a built sector "{purpose} ({owner})"
    # - "smithy (n15)". None of the keywords above appears in that, so a
    # building the town raised classified as NOTHING, and an unclassified
    # sector is inactive by design (see apply_genesis_config). Since
    # `location_api` filters on `sector.active`, the town could build a
    # smithy, walk to it and forge in it - and the first resume would
    # quietly close it, with no error anywhere.
    #
    # This only became reachable when the four locks on building came off
    # (GROW-01 to GROW-04), which is why nothing had hit it.
    #
    # A workshop is a workplace, so OFFICE - the same category "store" and
    # "shop" already map to. The civic ones get the category they are.
    ("mill", BuildingCategory.OFFICE),
    ("sawmill", BuildingCategory.OFFICE),
    ("smithy", BuildingCategory.OFFICE),
    ("carpentry", BuildingCategory.OFFICE),
    ("kitchen", BuildingCategory.OFFICE),
    ("loom", BuildingCategory.OFFICE),
    ("granary", BuildingCategory.GROCERY),
    ("hospital", BuildingCategory.PHARMACY),
    ("police", BuildingCategory.GOVERNMENT),
    ("well", BuildingCategory.PARK),
)

RESOURCE_SECTOR_KINDS: dict[str, ResourceKind] = {
    FARMLAND_SECTOR_NAME: ResourceKind.FARMLAND,
    ORE_OUTCROP_SECTOR_NAME: ResourceKind.ORE_OUTCROP,
    FOREST_SECTOR_NAME: ResourceKind.FOREST,
}


def classify_sector(name: str) -> frozenset[BuildingCategory]:
    """The classification rule: an exact match in KNOWN_SECTOR_CATEGORIES
    wins outright; otherwise every FALLBACK_KEYWORD_RULES keyword found
    in the (lowercased) name contributes its category. Returns an empty
    set for a name that matches nothing - an unclassified sector is
    inactive (see apply_genesis_config) rather than guessed into an
    arbitrary category.
    """
    normalized = name.strip()
    if normalized in KNOWN_SECTOR_CATEGORIES:
        return KNOWN_SECTOR_CATEGORIES[normalized]
    lowered = normalized.lower()
    matched = {category for keyword, category in FALLBACK_KEYWORD_RULES if keyword in lowered}
    return frozenset(matched)


def resource_kind_for(name: str) -> ResourceKind | None:
    return RESOURCE_SECTOR_KINDS.get(name.strip())


def apply_genesis_config(world: WorldMap) -> WorldMap:
    """Classifies and activates every sector currently on `world`, in
    place, and returns it (for chaining) - mutates Sector.categories,
    Sector.resource_kind, and Sector.active directly, which is exactly
    the kind of post-construction fill-in Sector's docstring says this
    function owns.

    A sector is active if either: its classified categories intersect
    GENESIS_ACTIVE_CATEGORIES (an ordinary genesis-open building), or it
    is a resource zone (resource_kind is set) - resource zones have no
    "closed for business" state, DESIGN.md never describes one, they are
    harvestable land from the moment they exist. Everything else (the
    pub, and any sector this run's naming rules genuinely cannot
    classify) starts inactive.
    """
    for sector in world.sectors.values():
        resource_kind = resource_kind_for(sector.name)
        if resource_kind is not None:
            sector.resource_kind = resource_kind
            sector.categories = frozenset()
            sector.active = True
            continue
        categories = classify_sector(sector.name)
        sector.categories = categories
        sector.active = bool(categories & GENESIS_ACTIVE_CATEGORIES)
    return world


def build_kasukabe_base_map(ville_dir: Path) -> WorldMap:
    """Loads the base Ville map and applies genesis classification, with
    no zone minting - the base map's own 19 sectors, correctly
    active/inactive, nothing appended. Split out from
    build_kasukabe_genesis_map so a caller (or a test) can inspect
    classification in isolation from the land-expansion step.
    """
    world = load_ville_map(ville_dir)
    return apply_genesis_config(world)


def build_kasukabe_genesis_map(
    ville_dir: Path,
    *,
    strip_depth: int = DEFAULT_GENESIS_STRIP_DEPTH,
) -> tuple[WorldMap, GenesisZoneIds]:
    """The real Kasukabe genesis entry point: base Ville map, classified,
    plus the government complex, temple, and resource zones minted in.

    Loads visuals/the_ville_jan7.json (the Tiled JSON, not the CSVs) as
    the base to grow from - expansion.py operates on Tiled JSON layers,
    see that module's docstring for why the CSVs and the JSON are two
    views of the same data and growing happens on the JSON side. Returns
    both the finished WorldMap and the ids expansion minted, so a caller
    can immediately look up e.g. `world.sectors[zone_ids.temple_sector_id]`
    without a name-based lookup.
    """
    _tiled, _blocks, world, zone_ids = build_kasukabe_genesis_parts(
        ville_dir, strip_depth=strip_depth
    )
    return world, zone_ids


def build_kasukabe_genesis_parts(
    ville_dir: Path,
    *,
    strip_depth: int = DEFAULT_GENESIS_STRIP_DEPTH,
) -> tuple[dict, "SpecialBlockTables", WorldMap, GenesisZoneIds]:
    """Load, mint, join, build, classify - and hand back all four pieces.

    GROW-21. This sequence existed TWICE, step for step:
    `build_kasukabe_genesis_map` above and
    `MapService.from_kasukabe_genesis`. The only difference was that the
    service kept `tiled` and `blocks` afterwards, because `expand_land`
    needs them to keep growing the same map later.

    Two copies of "load, mint, join, build, classify" is two places a
    change has to land. The corridor-opening step is the example that
    already cost something: the Ville map and the genesis strip were never
    connected - 17 separate walkable regions, residents in one and every
    workplace in another, 107 `move` calls in a single run answering "no
    walkable route" - and a fix applied to one copy and not the other would
    have left one host walkable and the other not, with both looking right.

    Returns every piece so each caller can keep what it needs and drop the
    rest, rather than the two of them rebuilding it.
    """
    blocks = load_special_blocks(ville_dir)
    tiled = load_tiled_map(ville_dir / "visuals" / "the_ville_jan7.json")

    zone_ids = append_kasukabe_genesis_zones(tiled, blocks, depth=strip_depth)
    # Join the town to itself before anything tries to walk it.
    open_walkable_corridors(tiled)

    world = build_world_map_from_tiled(tiled, blocks)
    apply_genesis_config(world)
    return tiled, blocks, world, zone_ids
