"""Findings read out of run 19's log, one test each.

Each of these is a behaviour I traced in docs/runs/run-19-report.md back
to a cause in the code. The report line numbers are in
docs/runs/run-19-issues.md; the reasoning is repeated here because a test
without its evidence is a rule nobody can re-derive.
"""

from __future__ import annotations


from world.tools.state import WorldState
from world.tests.map_assets import needs_the_map

# TOOL-27. This module needs the Ville map assets, which live in the
# sibling `smallville/` checkout, are gitignored, and are absent on any
# host that did not clone them. Without them these FAILED rather than
# skipped, and red for environment hides real regressions inside the
# noise - which is how TOOL-09 stayed invisible for a whole cycle.
pytestmark = needs_the_map


# ── Issue 2: the town agreed prices out loud and never moved a coin ───
#
# Run 19 recorded 564 transactions and ZERO between two residents. Corwin
# Thorne offered Nell Holloway three planks for 36 sim in the carpentry
# (report L166-176); Nell answered "Fair price. I'll take them." Nothing
# moved, and Corwin went home saying the planks were an "unsold lesson in
# inventory" (L210).
#
# `trade` exists, is ungated, and does exactly this. Nobody called it in
# either run. Reading the descriptions an agent is handed says why: the
# verb a seller reaches for is `sell`, and `sell` says "Sell an item to a
# business at its shop" - so a resident with goods and no shop concludes
# it must register a business first. That is not a guess; the residents
# said it. Silas: "No business registered. Walking blind." (L146) Hester
# advised him "register yourself first - thirty sim-currency at the
# government store" (L172). Nell asked "You registered at the government
# store, or we doing this between us?" (L176).
#
# `give` already solves this with one clause - "Unlike `sell`, no business
# and no payment is involved" - and `trade` had no such line.


def _describe(registry, name: str) -> str:
    return registry.tools[name].description.lower()


def test_trade_says_no_shop_is_needed(world: WorldState, registry) -> None:
    text = _describe(registry, "trade")
    assert "sell" in text, "nothing connects trade to the verb a seller reaches for"
    assert "business" in text or "shop" in text, (
        "trade never says a shop is not required, so a resident holding goods "
        "reads `sell`, sees it needs a business, and stops"
    )


def test_sell_points_at_the_person_to_person_path(world: WorldState, registry) -> None:
    """A resident who reads `sell` first must be told where else to look."""
    text = _describe(registry, "sell")
    assert "trade" in text, (
        "sell is a dead end for anyone without a shop, and says nothing about "
        "the ungated tool that would have worked"
    )


# ── Issue 4: briefs name workplaces nobody can walk to ────────────────
#
# `standing_brief` tells a resident where its work is. Two of those
# strings are not places `list_places` offers, so the resident is told to
# go somewhere the world will not route it to. Junia Vance is the
# grocery clerk; her brief names "Kasukabe Grocery" and run 19 leaves her
# standing at `business:b_grocery` (report L242), a room no customer can
# walk into.


def test_every_workplace_a_brief_names_is_somewhere_an_agent_can_walk(
    world: WorldState, registry
) -> None:
    from world.run_genesis import WORKPLACE_BY_OCCUPATION
    from world.tools.primitives.places import known_places

    walkable = {p["place_id"] for p in known_places(world)}
    unreachable = sorted(
        {
            place
            for place in WORKPLACE_BY_OCCUPATION.values()
            if place not in walkable
        }
    )
    assert not unreachable, (
        f"a resident is told its work is at {unreachable}, and no such place "
        "can be walked to - the brief names a room the map does not have"
    )
