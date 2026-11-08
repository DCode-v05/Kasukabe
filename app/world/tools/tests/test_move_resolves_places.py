"""A town cannot hold a conversation when its places fork on capitalisation.

`move` took `destination_location_id` as a free string and used it verbatim,
so every spelling of a place became a DIFFERENT place. Observed in one live
30-agent run, all at the same moment:

    'Harvey Oak Supply Store' (6 people)  'harvey_oak_supply_store' (1)
    'The Willows Market and Pharmacy' (1) 'Willows Market and Pharmacy' (1)
    'Kasukabe Farmland' (2)  'Farmland' (1)  'farmland:wheat' (1)
    invented outright: 'marketplace', 'place:t_temple'

Five of thirty residents were standing in phantom locations, invisible to the
people they believed they were beside - and `say`'s location gate meant they
could not be spoken to either.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from world.tools.bootstrap import build_world
from world.tools.primitives.movement import canonical_destination
from world.tools.tests.conftest import needs_the_map

# TOOL-27. Every test here needs the Ville map assets, which live in the
# sibling `smallville/` checkout, are gitignored, and are absent on any
# host that did not clone them. Without them these FAILED rather than
# skipped - and red for environment hides real regressions inside the
# noise, which is how TOOL-09 stayed invisible.
pytestmark = needs_the_map


@pytest_asyncio.fixture
async def world():
    w, _ = await build_world()
    return w


@pytest.mark.asyncio
async def test_a_real_place_resolves_to_itself(world):
    from world.tools.primitives.places import known_places

    for place in known_places(world)[:5]:
        assert canonical_destination(world, place["place_id"]) == place["place_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spelling",
    [
        "harvey oak supply store",
        "HARVEY OAK SUPPLY STORE",
        "harvey_oak_supply_store",
        "Harvey-Oak-Supply-Store",
    ],
)
async def test_every_spelling_lands_in_the_same_place(world, spelling):
    """Case, punctuation and underscores are noise to the eye and must be
    noise to the world too - otherwise six people in a shop cannot see the
    seventh."""
    from world.tools.primitives.places import known_places

    if not any(p["place_id"] == "Harvey Oak Supply Store" for p in known_places(world)):
        pytest.skip("this genesis has no Harvey Oak Supply Store")
    assert canonical_destination(world, spelling) == "Harvey Oak Supply Store"


@pytest.mark.asyncio
async def test_a_leading_the_is_not_a_different_place(world):
    from world.tools.primitives.places import known_places

    named = [p["place_id"] for p in known_places(world)
             if p["place_id"].lower().startswith("the ")]
    if not named:
        pytest.skip("this genesis has no place beginning with 'The'")
    without_the = named[0][4:]
    assert canonical_destination(world, without_the) == named[0]


@pytest.mark.asyncio
async def test_an_unknown_destination_is_left_untouched(world):
    """`move` deliberately supports jumping to location constants that are
    not map sectors - 'temple', 'mill', 'farmland:wheat'. Guessing which
    sector those "really" mean would break a working affordance to fix a
    spelling problem, so anything without an exact match passes through."""
    assert canonical_destination(world, "the moon") == "the moon"
    assert canonical_destination(world, "temple") == "temple"
    assert canonical_destination(world, "farmland:wheat") == "farmland:wheat"


@pytest.mark.asyncio
async def test_an_empty_destination_is_left_untouched(world):
    assert canonical_destination(world, "") == ""


# ── the fold, after three attempts at refusing instead ──────────────────
#
# Refusing an unrecognised destination broke something legitimate every
# time: temple/mill/farmland:wheat (gated tool locations with no map
# sector), grocery-shop (a business naming its own premises), then square
# and park. The town's vocabulary is larger than this module can enumerate.
# Folding is safe in a way refusing is not - the result is always a place
# that already exists, and a miss changes nothing.

@pytest.mark.asyncio
async def test_a_shorter_spelling_folds_onto_the_real_shop(world):
    """'willows_market_pharmacy' and 'The Willows Market and Pharmacy' were
    two places, with a resident standing alone in each, unable to see or
    speak to the other."""
    from world.tools.primitives.places import known_places

    if not any(p["place_id"] == "The Willows Market and Pharmacy"
               for p in known_places(world)):
        pytest.skip("this genesis has no Willows Market")
    assert canonical_destination(world, "willows_market_pharmacy") == \
        "The Willows Market and Pharmacy"


@pytest.mark.asyncio
async def test_a_bare_household_id_folds_to_the_home(world):
    world.households["h_quarry"] = object()
    assert canonical_destination(world, "h_quarry") == "home:h_quarry"


@pytest.mark.asyncio
async def test_a_real_name_is_never_swallowed_by_a_longer_one(world):
    """"Oak Hill College" is a sector and its words are a subset of "Dorm
    for Oak Hill College" - the subset rule sent every student to the dorm
    until an exact match was made to win outright."""
    from world.tools.primitives.places import known_places

    ids = {p["place_id"] for p in known_places(world)}
    if "Oak Hill College" not in ids:
        pytest.skip("this genesis has no Oak Hill College")
    assert canonical_destination(world, "Oak Hill College") == "Oak Hill College"


@pytest.mark.asyncio
@pytest.mark.parametrize("gated", ["temple", "mill", "kitchen", "farmland:wheat"])
async def test_a_gated_tool_location_is_never_folded(world, gated):
    """These have gates on them and no map sector. Folding "temple" into
    "Kasukabe Temple" stopped every gate that wanted "temple" from opening -
    the exact breakage place_aliases.satisfies_gate exists to prevent,
    reintroduced one layer up."""
    assert canonical_destination(world, gated) == gated


@pytest.mark.asyncio
async def test_something_unlike_any_place_is_left_alone(world):
    """Never refused. Three attempts at refusing each broke a legitimate
    destination, so an unmatched name passes through untouched."""
    assert canonical_destination(world, "L_selenes") == "L_selenes"
    assert canonical_destination(world, "the moon") == "the moon"


# ── a family:variant name whose variant does not exist ──────────────────


@pytest.mark.asyncio
async def test_an_invented_crop_strip_folds_to_the_farmland_sector(world):
    """"farmland:general" is shaped exactly like the real "farmland:wheat",
    but 'general' is not one of the ten crop varieties, so no rule matched a
    single word of it and the agent stood in a farmland of its own making.

    Observed live: Tobias spent his morning at "farmland:general" while Wren
    stood in "Kasukabe Farmland". All three times he spoke to her the world
    refused it - "say: requires location 'farmland:general', actor is at
    'Kasukabe Farmland'" - two people in the same field, unable to talk.
    """
    assert canonical_destination(world, "farmland:general") == "Kasukabe Farmland"


@pytest.mark.asyncio
async def test_a_real_crop_strip_is_still_never_folded(world):
    """The whole point of the gated-location rule above: farmland:wheat is
    a real gated place and must reach `harvest` exactly as spelled."""
    assert canonical_destination(world, "farmland:wheat") == "farmland:wheat"
    assert canonical_destination(world, "ore_outcrop:iron") == "ore_outcrop:iron"


@pytest.mark.asyncio
async def test_it_folds_to_the_sector_not_to_a_sibling_variant(world):
    """Somebody who asked for farmland generally must not be dropped into
    one specific crop strip they never named."""
    got = canonical_destination(world, "farmland:general")
    assert ":" not in got


@pytest.mark.asyncio
async def test_a_family_that_names_no_sector_is_left_alone(world):
    """"home:" and "business:" are record families with no sector of their
    own. An unknown one stays unknown rather than being folded somewhere
    arbitrary - the same "one candidate or nothing" discipline."""
    assert canonical_destination(world, "home:h_nonexistent") == "home:h_nonexistent"
    assert canonical_destination(world, "business:b_nonexistent") == "business:b_nonexistent"


@pytest.mark.asyncio
async def test_a_real_home_still_resolves(world):
    """The new rule runs only after every earlier rule has declined, so it
    cannot shadow a real record."""
    households = list(getattr(world, "households", {}))
    if households:
        real = f"home:{households[0]}"
        assert canonical_destination(world, real) == real


# ── a resource site named after its resource ────────────────────────────


@pytest.mark.asyncio
async def test_an_invented_mine_folds_onto_the_real_outcrop(world):
    """Observed live: Bram walked to "loc_quarry_mine", then to
    "loc_mine_copper", and `mine` refused him both times - "requires
    location 'ore_outcrop:copper'", the tool naming the exact place he was
    trying to reach while he stood in one of his own invention."""
    assert canonical_destination(world, "loc_mine_copper") == "ore_outcrop:copper"


@pytest.mark.asyncio
async def test_a_field_named_by_its_crop_folds_to_that_strip(world):
    assert canonical_destination(world, "wheat field") == "farmland:wheat"


@pytest.mark.asyncio
async def test_naming_two_resources_is_a_guess_to_decline(world):
    """"wheat and barley" names two fields. One candidate or nothing - the
    same discipline every other rule here holds to."""
    got = canonical_destination(world, "the wheat and barley fields")
    assert got == "the wheat and barley fields"


@pytest.mark.asyncio
async def test_a_resource_word_inside_a_longer_word_is_not_a_match(world):
    """Matching is word-level: "corner" is not "corn"."""
    assert canonical_destination(world, "the corner shop") == "the corner shop"


@pytest.mark.asyncio
async def test_a_place_naming_no_resource_at_all_is_still_left_alone(world):
    """"loc_quarry_mine" names no specific metal, so there is nothing to
    fold it onto and it passes through - not refused, just unresolved."""
    assert canonical_destination(world, "loc_quarry_mine") == "loc_quarry_mine"


@pytest.mark.asyncio
async def test_an_ambiguous_name_folds_to_the_one_sector_among_the_candidates(world):
    """"l_outcrop" is a subset of "Kasukabe Ore Outcrop" AND of every
    "ore_outcrop:<metal>" - four candidates, so the one-candidate rule
    declined and the agent stood in a place of its own invention while
    `mine` refused it.

    The sector is the answer that commits to least: it is where the map
    would have drawn them anyway, and it satisfies the gates of every
    variety inside it, so nothing is lost by not guessing which."""
    assert canonical_destination(world, "l_outcrop") == "Kasukabe Ore Outcrop"
    assert canonical_destination(world, "loc_ore_outcrop") == "Kasukabe Ore Outcrop"


@pytest.mark.asyncio
async def test_ambiguity_with_no_sector_at_all_is_still_declined(world):
    """The rule needs exactly one sector among the candidates. Two shops is
    still a guess to decline, not a spelling to fix."""
    assert canonical_destination(world, "the moon") == "the moon"
    assert canonical_destination(world, "L_selenes") == "L_selenes"
