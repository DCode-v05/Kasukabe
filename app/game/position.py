"""Pure presentation math: day/night tint, sprite-roster assignment, and a
degenerate placement fallback. Nothing here touches the network or holds
state - app/CONVENTIONS.md's carve-out for "pure computation" (ledger
math, tile geometry) extends naturally to the rendering-geometry
equivalent this module is, which is why it is a set of plain functions
instead of a class.

Three separate concerns share this module because all three are "given
what world/ sent, decide how to draw it" - genuinely presentation logic,
not game logic (app/CONVENTIONS.md: "game/ ... never computes game logic
locally"):

1. day/night tint - DESIGN.md section 1's day/night cycle, rendered as an
   alpha-blended navy overlay the browser draws over the whole camera
   view. The tick/day/night math (TICK_MINUTES, TICKS_PER_DAY,
   DAY_START_HOUR, NIGHT_START_HOUR) is copied from world/core/clock.py's
   timestamp_for(), not imported from it - see contracts.py's module
   docstring for why game/ has zero import-time dependency on world/. The
   numbers must stay identical to world/core/clock.py's; they are DESIGN.md
   section 1 constants ("tick = 10 game-minutes; 144 ticks/game-day"), not
   a tunable either module owns independently.
2. sprite roster assignment - DESIGN.md section 4 seeds 250 agents; the
   Apache-2.0 asset set lifted from smallville/ only ships 25 named
   character spritesheets (smallville/environment/frontend_server/
   static_dirs/assets/characters/*.png - the upstream Generative Agents
   demo town had exactly 25 residents). Kasukabe reuses that same 25-
   sprite roster round-robin, keyed by a stable hash of agent_id so a
   given agent always renders with the same sprite across reconnects
   without either side having to persist the assignment.
3. fallback tile placement - world/'s `agent.moved` event today
   (world/core/perception.py's LocationIndex) carries only a string
   location_id, not a tile coordinate; resolving "Hobbs Cafe" to a pixel
   position requires WorldMap.Sector.tiles (world/map/model.py), which is
   world/ state this package must not import (module boundary). The
   contract (contracts.py) asks world/'s HTTP API to resolve and send
   tile_x/tile_y directly once it exists; until then, this fallback
   places an agent at a location-stable, deliberately-jittered point so
   the browser has something sane to draw instead of crashing on a
   missing coordinate. It is presentation-layer damage control, not a
   substitute for world/ doing the real geometry lookup.
"""

from __future__ import annotations

import hashlib
import re

TICK_MINUTES = 10
TICKS_PER_DAY = 144
MINUTES_PER_DAY = 24 * 60
DAY_START_HOUR = 6
NIGHT_START_HOUR = 22

# The_ville is a 140x100 tile grid (world/map/model.py's WorldMap, loaded
# from smallville/.../the_ville_jan7.json: "width": 140, "height": 100).
# Kept here only as the fallback's jitter bound, not as a general map
# size - a grown town (DESIGN.md section 1's land expansion) is strictly
# larger, so this is a conservative "somewhere inside the original town
# square" box, never a claim about the live map's true extent.
FALLBACK_MAP_WIDTH_TILES = 140
FALLBACK_MAP_HEIGHT_TILES = 100
FALLBACK_JITTER_TILES = 6

# DESIGN.md section 4: "~50/50 male/female ... pre-linked households" says
# nothing about sprite art, this roster is purely which Apache-2.0
# spritesheet renders which agent. Names match the .png stems under
# game/static/assets/characters/ exactly (sprite_key is also the Phaser
# texture key preload.js loads each atlas under, see static/js/town.js).
SPRITE_ROSTER: tuple[str, ...] = (
    "Abigail_Chen",
    "Adam_Smith",
    "Arthur_Burton",
    "Ayesha_Khan",
    "Carlos_Gomez",
    "Carmen_Ortiz",
    "Eddy_Lin",
    "Francisco_Lopez",
    "Giorgio_Rossi",
    "Hailey_Johnson",
    "Isabella_Rodriguez",
    "Jane_Moreno",
    "Jennifer_Moore",
    "John_Lin",
    "Klaus_Mueller",
    "Latoya_Williams",
    "Maria_Lopez",
    "Mei_Lin",
    "Rajiv_Patel",
    "Ryan_Park",
    "Sam_Moore",
    "Tamara_Taylor",
    "Tom_Moreno",
    "Wolfgang_Schulz",
    "Yuriko_Yamamoto",
)


def stable_hash(key: str) -> int:
    """A hash that is stable across processes and Python versions (unlike
    the builtin `hash()`, which is salted per-process for strings unless
    PYTHONHASHSEED is fixed) - both the sprite roster assignment and the
    fallback jitter need the SAME agent_id to always land on the same
    sprite/offset, including across a game/ server restart."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def sprite_key_for(agent_id: str) -> str:
    """Deterministically assigns one of the 25 lifted spritesheets to an
    agent_id. Same agent_id always yields the same sprite_key."""
    if not agent_id:
        raise ValueError("agent_id must be non-empty")
    index = stable_hash(agent_id) % len(SPRITE_ROSTER)
    return SPRITE_ROSTER[index]


def day_night_tint(tick: int) -> tuple[str, float]:
    """Returns (css_hex_color, alpha) for the day/night overlay at a given
    tick. Alpha ramps smoothly across a 2-hour dawn/dusk window rather
    than snapping instantly at DAY_START_HOUR/NIGHT_START_HOUR, so the
    overlay doesn't visibly pop when the game view is open. Fully
    transparent at solar noon, fully opaque (a deep navy, not black, so
    tiles stay legible) at the middle of the night.
    """
    if tick < 0:
        raise ValueError(f"tick must be >= 0, got {tick}")

    minute_of_day = (tick * TICK_MINUTES) % MINUTES_PER_DAY
    hour = minute_of_day / 60.0

    day_start = float(DAY_START_HOUR)
    night_start = float(NIGHT_START_HOUR)
    ramp_hours = 2.0

    if day_start <= hour < day_start + ramp_hours:
        # Dawn: night alpha ramping down to 0.
        alpha = 1.0 - (hour - day_start) / ramp_hours
    elif day_start + ramp_hours <= hour < night_start - ramp_hours:
        # Full day.
        alpha = 0.0
    elif night_start - ramp_hours <= hour < night_start:
        # Dusk: alpha ramping up to full night.
        alpha = (hour - (night_start - ramp_hours)) / ramp_hours
    else:
        # Full night (wraps past midnight through day_start).
        alpha = 1.0

    alpha = max(0.0, min(1.0, alpha))
    # Capped below 1.0 so even deep night keeps the map faintly legible -
    # a fully opaque overlay would hide agent sprites entirely, which
    # defeats the point of a live town view.
    # 0.38, kept in lockstep with game/static/js/daynight.js's ceiling: the
    # night overlay now covers the whole map in world space, and at 0.72
    # midnight rendered the town as a solid black rectangle.
    return "#0b1230", round(alpha * 0.38, 3)


def is_daytime(tick: int) -> bool:
    minute_of_day = (tick * TICK_MINUTES) % MINUTES_PER_DAY
    hour = minute_of_day // 60
    return DAY_START_HOUR <= hour < NIGHT_START_HOUR


def game_day_and_time(tick: int) -> tuple[int, int, int]:
    """Returns (game_day, hour, minute) for a tick, matching
    world/core/clock.py's timestamp_for() derivation exactly (same
    constants, see module docstring)."""
    if tick < 0:
        raise ValueError(f"tick must be >= 0, got {tick}")
    total_minutes = tick * TICK_MINUTES
    game_day, minute_of_day = divmod(total_minutes, MINUTES_PER_DAY)
    hour, minute = divmod(minute_of_day, 60)
    return game_day, hour, minute


def fallback_tile_for(location_id: str) -> tuple[int, int]:
    """Degenerate placement for an agent whose event/snapshot carried a
    location_id but no tile_x/tile_y (see module docstring, point 3).
    Deterministic per location_id so every agent parked at the same
    location clusters together instead of scattering randomly on every
    reconnect, and jittered so they don't fully overlap each other.
    """
    if not location_id:
        raise ValueError("location_id must be non-empty")
    h = stable_hash(location_id)
    cx = FALLBACK_JITTER_TILES + (h % (FALLBACK_MAP_WIDTH_TILES - 2 * FALLBACK_JITTER_TILES))
    cy = FALLBACK_JITTER_TILES + (
        (h // FALLBACK_MAP_WIDTH_TILES) % (FALLBACK_MAP_HEIGHT_TILES - 2 * FALLBACK_JITTER_TILES)
    )
    return cx, cy


def resolve_tile(location_id: str | None, tile_x: int | None, tile_y: int | None) -> tuple[int, int]:
    """The one call site town.js's Python-side counterpart (relay.py,
    mock_world.py) uses to get a drawable tile: prefer world/'s own
    tile_x/tile_y when present, fall back to fallback_tile_for() derived
    from location_id, and only raise if NEITHER was given (a malformed
    upstream event - see mock_world.py and relay.py for how far upstream
    that would have to originate)."""
    if tile_x is not None and tile_y is not None:
        return tile_x, tile_y
    if location_id:
        return fallback_tile_for(location_id)
    raise ValueError("resolve_tile requires either (tile_x, tile_y) or a location_id")


# ---------------------------------------------------------------------------
# 4. engine location_id -> township district
# ---------------------------------------------------------------------------
# The township viewer (static/township-v2.html) draws its own procedurally
# generated 104x104 town, NOT the Ville map world/ paths agents over. The two
# tile spaces are unrelated, so a live engine tile CANNOT be plotted directly
# on it - the honest bridge is the named place, not the coordinate.
#
# world/ says "this agent is at Hobbs Cafe" / "cafe" / "home:h0021"; the
# viewer owns a district called `cafe` and knows where that is on its own
# map. This table is the whole mapping, and it is deliberately partial:
# anything that does not resolve returns None and the viewer leaves that
# resident on its built-in schedule rather than inventing a position.
#
# Every key here is a real location_id string that already exists elsewhere
# in the repo - charter/models.py's LOCATION_* constants, tools/locations.py's
# constants, genesis business_type values, and wiring.py's "home:<id>" prefix
# - never a string invented for this table. The values are township zone ids
# from static/js/township/village2.js's ZONES.

TOWNSHIP_ZONE_BY_LOCATION: dict[str, str] = {
    "town_hall": "government",
    "government_complex": "government",
    "temple": "government",
    "school": "school",
    "college": "college",
    "library": "library",
    "hospital": "hospital",
    "clinic": "hospital",
    "pharmacy": "pharmacy",
    "police_station": "police",
    "fire_station": "fire",
    "bank": "bank",
    "market": "marketrow",
    "marketplace": "marketrow",
    "shop": "marketrow",
    "grocery": "grocery",
    "cafe": "cafe",
    "restaurant": "restaurant",
    "bakery": "bakery",
    "inn": "inn",
    "tavern": "inn",
    "mill": "marketrow",
    "workshop": "construction",
    "construction_site": "construction",
    "forest": "forest",
    "docks": "docks",
    "quay": "docks",
    "harbour": "docks",
    "pasture": "pasture",
    "orchard": "orchard",
    "town_square": "townsquare",
    "square": "townsquare",
    "park": "park",
}

# Prefixed families: "farmland:wheat" -> farmland, "ore:iron" -> ore outcrop,
# "home:h0021" -> the housing district. Checked after the exact table.
TOWNSHIP_ZONE_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("home:", "housing"),
    ("household:", "housing"),
    ("farmland", "farmland"),
    ("ore", "oreoutcrop"),
    ("business:", "marketrow"),
)


def township_zone_for(location_id: str | None) -> str | None:
    """Which township district a live engine location_id should draw in,
    or None when nothing in the repo's own location vocabulary maps to one.

    None is a real answer, not a failure: the viewer keeps that resident on
    its built-in daily schedule instead of guessing, which is why this never
    falls back to an arbitrary district.
    """
    if not location_id:
        return None
    raw = location_id.strip().lower()

    # A map-resolved sector display name ("Hobbs Cafe") comes through with
    # its spaces intact and is checked first, before the slug forms.
    if raw in TOWNSHIP_ZONE_BY_SECTOR:
        return TOWNSHIP_ZONE_BY_SECTOR[raw]
    # "Dorm for Oak Hill College" must not read as residential, so the
    # exact table above always wins before this substring sweep.
    if any(marker in raw for marker in _RESIDENTIAL_MARKERS):
        return "housing"

    key = raw.replace(" ", "_")
    if key in TOWNSHIP_ZONE_BY_LOCATION:
        return TOWNSHIP_ZONE_BY_LOCATION[key]
    for prefix, zone in TOWNSHIP_ZONE_BY_PREFIX:
        if key.startswith(prefix):
            return zone

    # A genesis-minted business the tables above have never seen. "Kasukabe
    # Grocery" was exactly this: a real, occupied place that resolved to
    # None, so one resident of thirty vanished from the map and the badge
    # read "29 of 30" with nothing to explain the missing one.
    #
    # The trade word in the name is the evidence. It is weaker than an exact
    # match - hence its own return path below - but it is evidence, and
    # drawing a grocer in the grocery beats not drawing them at all.
    zone = _zone_by_trade_word(raw)
    if zone:
        return zone

    # Still nothing. None remains a real answer here: a string this module
    # cannot recognise at all might not be a place, and a wrong district
    # teleports that resident across town on every poll. The game server
    # decides what to do about a live, occupied location that lands here -
    # it knows the place exists; this function does not.
    return None


# Matched against WORDS, not raw substrings. "Ferris Hardware Store" contains
# "ore" and was placed at the ore outcrop; "Public Library" contains "pub".
# Both are why this splits the name up first.
#
# _PREFIX words may start a longer word - "grocer" catches "grocery" and
# "grocers", "farm" catches "farmland". _EXACT words must be the whole word,
# because every one of them is a substring of something unrelated.
#
# Ordered: the first hit wins, so the more specific word comes first. "The
# Willows Market and Pharmacy" holds both "market" and "pharmacy"; the exact
# table above already answers that one, and this ordering keeps the sweep
# honest for the next name like it.
_SECTOR_KEYWORDS_PREFIX: tuple[tuple[str, str], ...] = (
    ("pharmac", "pharmacy"), ("grocer", "grocery"), ("baker", "bakery"),
    ("cafe", "cafe"), ("coffee", "cafe"), ("restaurant", "restaurant"),
    ("tavern", "inn"), ("college", "college"), ("universit", "college"),
    ("school", "school"), ("academ", "college"), ("librar", "library"),
    ("hospital", "hospital"), ("clinic", "hospital"), ("infirmar", "hospital"),
    ("government", "government"), ("temple", "townsquare"),
    ("church", "townsquare"), ("chapel", "townsquare"),
    ("farm", "farmland"), ("orchard", "orchard"), ("pasture", "pasture"),
    ("forest", "forest"), ("woodland", "forest"), ("quarr", "oreoutcrop"),
    ("harbour", "docks"), ("harbor", "docks"),
    ("construction", "construction"), ("workshop", "construction"),
    ("market", "marketrow"), ("emporium", "marketrow"),
)

_SECTOR_KEYWORDS_EXACT: tuple[tuple[str, str], ...] = (
    ("pub", "inn"), ("inn", "inn"), ("police", "police"), ("fire", "fire"),
    ("bank", "bank"), ("park", "park"), ("gardens", "park"),
    ("square", "townsquare"), ("hall", "government"), ("ore", "oreoutcrop"),
    ("mine", "oreoutcrop"), ("mines", "oreoutcrop"), ("dock", "docks"),
    ("docks", "docks"), ("quay", "docks"), ("mill", "marketrow"),
    ("store", "marketrow"), ("stores", "marketrow"), ("shop", "marketrow"),
    ("supply", "marketrow"), ("supplies", "marketrow"),
)


def _zone_by_trade_word(raw: str) -> str | None:
    """The trade word in a place's name, when no table knows the name.

    Weaker evidence than an exact match - hence its own return path in
    township_zone_detail - but it IS evidence, and drawing a grocer in the
    grocery beats leaving them off the map entirely.
    """
    words = [w for w in re.split(r"[^a-z]+", raw) if w]
    if not words:
        return None
    for word, zone in _SECTOR_KEYWORDS_PREFIX:
        if any(w.startswith(word) for w in words):
            return zone
    exact = set(words)
    for word, zone in _SECTOR_KEYWORDS_EXACT:
        if word in exact:
            return zone
    return None


def township_zone_detail(location_id: str | None) -> tuple[str | None, bool]:
    """The zone, plus whether the town's own vocabulary named it exactly.

    The viewer draws both kinds of resident identically - an approximate
    placement is still a placement - but it may say which it is, and a
    growing count of inexact placements is the signal that a table above
    has fallen behind the map.
    """
    if not location_id:
        return None, False
    raw = location_id.strip().lower()
    if raw in TOWNSHIP_ZONE_BY_SECTOR:
        return TOWNSHIP_ZONE_BY_SECTOR[raw], True
    if any(marker in raw for marker in _RESIDENTIAL_MARKERS):
        return "housing", True
    key = raw.replace(" ", "_")
    if key in TOWNSHIP_ZONE_BY_LOCATION:
        return TOWNSHIP_ZONE_BY_LOCATION[key], True
    for prefix, zone in TOWNSHIP_ZONE_BY_PREFIX:
        if key.startswith(prefix):
            return zone, True
    return township_zone_for(location_id), False


# Real Ville sector display names, which is what a map-resolved snapshot
# actually reports (world/location_api.py: "real, human-readable names
# ('Hobbs Cafe', 'Kasukabe Government Complex', 'Adam Smith's house')").
#
# These strings are COPIED from world/map/genesis.py's
# KNOWN_SECTOR_CATEGORIES and world/map/expansion.py's *_SECTOR_NAME
# constants, not imported - same reason this module already copies
# world/core/clock.py's tick constants: game/ has zero import-time
# dependency on world/ (app/CONVENTIONS.md). If the map ever renames a
# sector, test_township_zone_bridge.py fails loudly rather than silently
# unbinding those residents.
TOWNSHIP_ZONE_BY_SECTOR: dict[str, str] = {
    "hobbs cafe": "cafe",
    "the rose and crown pub": "inn",
    "oak hill college": "college",
    "dorm for oak hill college": "college",
    "johnson park": "park",
    "harvey oak supply store": "marketrow",
    # Grocery and pharmacy share one sector on the base map; the township
    # draws them as two. "Market" leads the name, so it binds to grocery.
    "the willows market and pharmacy": "grocery",
    "kasukabe government complex": "government",
    # The township has no temple. The square is the nearest thing it has to
    # a public gathering place that is not an administrative building.
    "kasukabe temple": "townsquare",
    "kasukabe farmland": "farmland",
    "kasukabe ore outcrop": "oreoutcrop",
    "kasukabe forest": "forest",
}

# Any residential sector on the base map - six named family houses and five
# apartments - is the township's housing district. Checked by substring so a
# newly minted "<family> family's house" binds without this table changing.
_RESIDENTIAL_MARKERS: tuple[str, ...] = ("'s house", "family's house", "apartment", "co-living")
