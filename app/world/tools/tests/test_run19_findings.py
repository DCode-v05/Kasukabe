"""Findings read out of run 19's log, one test each.

Each of these is a behaviour I traced in docs/runs/run-19-report.md back
to a cause in the code. The report line numbers are in
docs/runs/run-19-issues.md; the reasoning is repeated here because a test
without its evidence is a rule nobody can re-derive.
"""

from __future__ import annotations


from world.tools.state import WorldState
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map


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
