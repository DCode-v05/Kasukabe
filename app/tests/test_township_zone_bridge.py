"""The engine location_id -> township district bridge (game/position.py).

UI-14. This file lives in `app/tests/` rather than `game/tests/` because
it imports BOTH `game.position` and `world.map.*`, and
app/CONVENTIONS.md's test conventions put cross-module tests here for
exactly that reason: "things no single package owns ... live in the
top-level app/tests/". In `game/tests/` it was a package-local suite
reaching across the boundary its own package is forbidden to cross, and
nothing said so.


The township viewer draws its own 104x104 town, not the Ville map world/
paths agents over, so a live engine tile cannot be plotted on it. The named
place is the bridge, and these tests pin both halves of it:

  1. every sector name this bridge keys on still exists on the real map
     (the strings are copied across the game/ <- world/ boundary, so a
     rename upstream has to fail loudly here rather than silently unbind
     every resident standing in that sector);
  2. an unmapped location resolves to None rather than to some arbitrary
     district - the viewer keeps those residents on its built-in schedule,
     which is only correct if None really means "no answer".
"""

from __future__ import annotations

import pytest

from game.position import (
    TOWNSHIP_ZONE_BY_LOCATION,
    TOWNSHIP_ZONE_BY_SECTOR,
    township_zone_for,
)


def test_every_bridged_sector_name_still_exists_on_the_real_map():
    """Copied strings, verified against the source of truth."""
    from world.map.expansion import (
        FOREST_SECTOR_NAME,
        GOVERNMENT_SECTOR_NAME,
        TEMPLE_SECTOR_NAME,
    )
    from world.map.genesis import KNOWN_SECTOR_CATEGORIES

    real = {name.lower() for name in KNOWN_SECTOR_CATEGORIES}
    real |= {n.lower() for n in (FOREST_SECTOR_NAME, GOVERNMENT_SECTOR_NAME, TEMPLE_SECTOR_NAME)}
    # Farmland and ore are minted by expansion.py under their own constants.
    real |= {"kasukabe farmland", "kasukabe ore outcrop"}

    unknown = sorted(set(TOWNSHIP_ZONE_BY_SECTOR) - real)
    assert not unknown, f"bridge names no sector on the real map has: {unknown}"


@pytest.mark.parametrize(
    ("location_id", "expected"),
    [
        ("Hobbs Cafe", "cafe"),
        ("hobbs cafe", "cafe"),
        ("Kasukabe Government Complex", "government"),
        ("Oak Hill College", "college"),
        # Institutional housing is the college, not the housing district.
        ("Dorm for Oak Hill College", "college"),
        ("Adam Smith's house", "housing"),
        ("Moore family's house", "housing"),
        ("Ryan Park's apartment", "housing"),
        ("artist's co-living space", "housing"),
        # The opaque slug namespace agents actually stand in.
        ("home:h0021", "housing"),
        ("farmland:wheat", "farmland"),
        ("ore:iron", "oreoutcrop"),
        ("cafe", "cafe"),
        ("town_hall", "government"),
    ],
)
def test_known_locations_bind_to_a_district(location_id: str, expected: str):
    assert township_zone_for(location_id) == expected


@pytest.mark.parametrize("location_id", [None, "", "   ", "somewhere-nobody-named", "xyzzy"])
def test_an_unmapped_location_is_none_not_a_guess(location_id):
    """None keeps that resident on the viewer's own schedule. A wrong
    district would teleport them across town every poll.

    This function stays strict on purpose. game/server.py's _drawable_zone
    is the layer that decides what to do about a live, occupied location
    that lands here - it knows the place exists, and this function does
    not, which is exactly why the decision belongs there and not here."""
    assert township_zone_for(location_id) is None


@pytest.mark.parametrize(
    ("location_id", "expected"),
    [
        # The real one: a genesis-minted business in neither table. One
        # resident of thirty vanished from the map and the badge read
        # "29 of 30 on Bedrock" with nothing to explain the missing one.
        ("Kasukabe Grocery", "grocery"),
        ("Kasukabe Bakery", "bakery"),
        ("Ferris Hardware Store", "marketrow"),
        ("Oak Street Clinic", "hospital"),
        ("Riverside Pub", "inn"),
    ],
)
def test_a_named_business_binds_by_its_trade_word(location_id, expected):
    """The trade word in the name is weaker evidence than an exact match,
    but it IS evidence - and drawing a grocer in the grocery beats leaving
    them off the map entirely."""
    assert township_zone_for(location_id) == expected


def test_an_exact_match_is_distinguishable_from_a_guess():
    """The viewer says so on the card, so the two must not be conflated."""
    from game.position import township_zone_detail

    assert township_zone_detail("Hobbs Cafe") == ("cafe", True)
    assert township_zone_detail("home:h0021") == ("housing", True)
    assert township_zone_detail("Kasukabe Grocery") == ("grocery", False)
    assert township_zone_detail("xyzzy") == (None, False)


def _zones_village2_declares() -> set[str]:
    """Every id `village2.js` actually calls `zone(...)` with.

    UI-23. The check this replaces was
    `f"zone('{z}'" not in source and f"'{z}'" not in source` - and that
    second clause let ANY string literal anywhere in a 460-line file
    satisfy it. `'park'` appears in a label, a comment or an unrelated
    lookup and the zone is declared as far as the test is concerned, so a
    rename upstream would not have been caught: exactly the failure this
    file exists to catch.

    Parsing the declarations instead makes the assertion the one that was
    meant - and lets it answer the other direction too.
    """
    from pathlib import Path
    import re

    village = Path(__file__).resolve().parents[1] / "game/static/js/township/village2.js"
    source = village.read_text(encoding="utf-8")
    # Two declaration forms, both real:
    #   zone('park', 'Town Park', ...)          - a district rectangle
    #   add(..., 'shop', {..., zone:'bakery'})  - a building that IS a zone
    # The ten shop zones (bakery, cafe, grocery, pharmacy, bank, inn,
    # hospital, police, fire, restaurant) are all the second kind, which
    # is why a parser that knew only the first reported them missing.
    return (
        set(re.findall(r"^zone\(\s*'([^']+)'", source, re.M))
        | set(re.findall(r"zone\s*:\s*'([^']+)'", source))
    )


def test_every_district_named_is_one_the_township_actually_draws():
    """A zone id the viewer does not own is a silent no-op in the browser."""
    drawn = _zones_village2_declares()
    assert drawn, "no zone declarations found at all - the parser is wrong"

    named = set(TOWNSHIP_ZONE_BY_SECTOR.values()) | set(TOWNSHIP_ZONE_BY_LOCATION.values())
    named |= {"housing", "farmland", "oreoutcrop", "marketrow"}
    missing = sorted(named - drawn)
    assert not missing, (
        f"village2.js declares no such zone: {missing}. Every resident whose "
        "location maps to one of these is bound to a district the browser "
        "does not draw, which is a silent no-op"
    )


def test_the_weak_form_of_this_check_would_have_passed_anyway():
    """UI-23, pinned. The old assertion accepted a bare string literal, so
    it stayed green while a zone was drawn under a different id.

    Kept as a test rather than a comment because "this check was too weak"
    is a claim, and a claim in a comment is not evidence.
    """
    from pathlib import Path

    village = Path(__file__).resolve().parents[1] / "game/static/js/township/village2.js"
    source = village.read_text(encoding="utf-8")
    invented = "a_district_nobody_draws"
    doctored = source.replace("zone('park'", f"zone('{invented}'")

    assert f"'{invented}'" in doctored          # the old check: satisfied
    assert "'park'" in doctored, (
        "the old check looked for a bare literal, and 'park' survives as a "
        "label elsewhere in the file even after the zone is renamed - so it "
        "passed while the zone was gone"
    )


# -- provenance on the live feed -----------------------------------------
#
# The badge used to have two states and chose between them on bound > 0
# alone, never asking where the rows came from. A mock feed whose ids
# happened to match genesis would have been announced as "LIVE ENGINE",
# in green, with a tick number. The only thing preventing that was
# MockWorldClient naming its agents "mock-agent-000" - naming luck, not a
# check. `source` makes provenance explicit so the viewer can refuse to
# claim a live engine for fabricated rows.


def test_live_feed_reports_mock_source_for_the_mock_world() -> None:
    from fastapi.testclient import TestClient

    from game.config import GameSettings
    from game.server import create_app

    client = TestClient(create_app(settings=GameSettings(mock_mode=True)))
    body = client.get("/api/township/live").json()

    assert body["source"] == "mock"
    assert body["connected"] is True, "the mock world does connect - that is the trap"
    assert all(row["id"].startswith("mock-agent-") for row in body["agents"])


def test_source_follows_the_injected_client_not_the_setting() -> None:
    """Provenance is read off the constructed client, deliberately.

    create_app(world_client=...) lets a caller inject a client that
    disagrees with settings.mock_mode - every test in this package does
    exactly that - and in precisely that case the SETTING would report the
    opposite of what is being served. The object is the truth.
    """
    from fastapi.testclient import TestClient

    from game.config import GameSettings
    from game.mock_world import MockWorldClient
    from game.server import create_app

    settings = GameSettings(mock_mode=False)  # says "real"...
    injected = MockWorldClient(settings)      # ...but serves mock rows
    client = TestClient(create_app(settings=settings, world_client=injected))

    assert client.get("/api/township/live").json()["source"] == "mock"


def test_source_is_present_even_when_the_world_is_unreachable() -> None:
    """The failure path carries it too.

    A viewer that loses the field on error would fall back to its
    last-known provenance, and "the server went away" is exactly when a
    stale 'world' would be most misleading.
    """
    from fastapi.testclient import TestClient

    from game.config import GameSettings
    from game.server import create_app
    from game.world_client import WorldClientError

    class Unreachable:
        async def get_snapshot(self):
            raise WorldClientError("world/ is unreachable")

        async def close(self):
            return None

    client = TestClient(
        create_app(settings=GameSettings(mock_mode=False), world_client=Unreachable())
    )
    body = client.get("/api/township/live").json()

    assert body["connected"] is False
    assert body["source"] == "world"
    assert "unreachable" in body["detail"]


# -- cognition: are the agents actually thinking? ------------------------
#
# `source` answers "did these rows come from a real world server". It says
# NOTHING about whether that world's agents are backed by real engines - a
# real world driven entirely by MockAgentGateway looks identical. The two
# are reported separately so the viewer can never imply one from the other.


def live_body(settings, world_client=None):
    from fastapi.testclient import TestClient

    from game.server import create_app

    app = create_app(settings=settings, world_client=world_client)
    return TestClient(app).get("/api/township/live").json()


def test_mock_world_reports_mock_cognition() -> None:
    from game.config import GameSettings

    assert live_body(GameSettings(mock_mode=True))["cognition"] == "mock"


def test_cognition_comes_from_the_world_not_the_game_server() -> None:
    """The game server cannot know this; only the world it is pointed at
    can. So it must forward, never infer."""
    from game.config import GameSettings
    from game.contracts import Snapshot

    class EngineBackedWorld:
        async def get_provenance(self):
            return {"cognition": "engine", "inference": "bedrock"}

        async def get_snapshot(self):
            return Snapshot(tick=1, map_tiled_json={}, agents=[])

        async def close(self):
            return None

    body = live_body(GameSettings(mock_mode=False), EngineBackedWorld())
    assert body["source"] == "world"
    assert body["cognition"] == "engine"


def test_a_world_that_cannot_say_reports_null_not_mock() -> None:
    """null means "did not answer", which is NOT "mock".

    Reporting mock for a silent world would be a lie in the other
    direction - it would show a real, thinking town as fake. The viewer
    renders this as ENGINES UNKNOWN.
    """
    from game.config import GameSettings
    from game.contracts import Snapshot

    class SilentWorld:  # no get_provenance at all - an older build
        async def get_snapshot(self):
            return Snapshot(tick=1, map_tiled_json={}, agents=[])

        async def close(self):
            return None

    assert live_body(GameSettings(mock_mode=False), SilentWorld())["cognition"] is None


def test_a_failing_cognition_probe_never_breaks_the_feed() -> None:
    """Provenance is a label. It must not be able to take down the map."""
    from game.config import GameSettings
    from game.contracts import Snapshot

    class AngryWorld:
        async def get_provenance(self):
            raise RuntimeError("boom")

        async def get_snapshot(self):
            return Snapshot(tick=7, map_tiled_json={}, agents=[])

        async def close(self):
            return None

    body = live_body(GameSettings(mock_mode=False), AngryWorld())
    assert body["connected"] is True and body["tick"] == 7
    assert body["cognition"] is None


def test_real_engines_on_a_mock_broker_are_not_reported_as_real() -> None:
    """The exact shape of a credential-free local run, and the one the
    badge used to get wrong.

    Everything about this run is genuine except the completions: real
    sim_main subprocesses, real world state, real wallet debits. But the
    broker is in MOCK_MODE and every completion is the literal string
    "mock response". `cognition` alone says "engine" and the badge used to
    render that as REAL AGENTS in green. The two axes are reported
    separately precisely so the viewer can refuse to.
    """
    from game.config import GameSettings
    from game.contracts import Snapshot

    class EnginesButCannedText:
        async def get_provenance(self):
            return {"cognition": "engine", "inference": "mock"}

        async def get_snapshot(self):
            return Snapshot(tick=42, map_tiled_json={}, agents=[])

        async def close(self):
            return None

    body = live_body(GameSettings(mock_mode=False), EnginesButCannedText())
    assert body["cognition"] == "engine"
    assert body["inference"] == "mock"
    # Both fields present and distinct is the whole point - a consumer that
    # only had `cognition` could not tell this from a genuinely real run.
    assert body["cognition"] != body["inference"]
