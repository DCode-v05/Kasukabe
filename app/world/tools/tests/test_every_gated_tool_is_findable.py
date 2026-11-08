"""§4.7 — a tool you cannot reach is a tool the town does not have.

Fifteen tools carry a static location gate. Seven of the places those
gates demand existed only as tool ids: mill, kitchen, sawmill, smithy,
carpentry, loom_house and patent_office. `list_places` returned forty
places and not one of them was a workshop, so no resident could learn
they existed.

Measured on a live run before this test: 15 of 19 refusals were `craft`
and `cook` telling an agent to go to a mill or a kitchen. 15 moves that
run, none to a workshop. Marta Thorne stood in her own bakery and was
refused for not being in a kitchen. `tools_registry` had never held a row
in the history of the project, because `define_tool` is gated on
`patent_office`.

The gates were correct. The places were real. Nothing ever said so.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from world.tools.bootstrap import build_world
from world.tools.place_aliases import known_tool_locations, satisfies_gate
from world.tools.primitives.places import known_places
from world.tools.tests.conftest import needs_the_map

pytestmark = needs_the_map


@pytest_asyncio.fixture
async def world_and_registry():
    return await build_world(genesis_tick=0, seed=1)


def _static_gates(registry) -> dict[str, str]:
    """Only the gates that are a fixed string. Eight tools compute a gate
    per call from their own parameters (which business, which crop) and
    cannot be resolved without inventing those parameters - comparing the
    function object to a place name is how an earlier audit reported nine
    unreachable tools when the real figure was seven."""
    gates = {}
    for name, spec in registry.tools.items():
        gate = getattr(spec, "location_gate", None)
        if isinstance(gate, str) and gate:
            gates[name] = gate
    return gates


@pytest.mark.asyncio
async def test_every_statically_gated_tool_is_reachable_from_a_listed_place(world_and_registry):
    world, registry = world_and_registry
    listed = [p["place_id"] for p in known_places(world)]
    gates = _static_gates(registry)
    assert gates, "no statically gated tools found - the audit would be vacuous"

    unreachable = {
        tool: gate
        for tool, gate in gates.items()
        if not any(satisfies_gate(place, gate) for place in listed)
    }
    assert not unreachable, (
        "these tools demand a place no resident can discover through "
        f"list_places: {unreachable}"
    )


@pytest.mark.asyncio
async def test_every_tool_minted_location_is_discoverable(world_and_registry):
    """Wider than the gates: any place the town's own tools mint must be
    findable, or it is a room with no door."""
    world, _registry = world_and_registry
    listed = [p["place_id"] for p in known_places(world)]
    missing = [
        loc for loc in sorted(known_tool_locations())
        if not any(satisfies_gate(place, loc) for place in listed)
    ]
    assert not missing, f"minted but undiscoverable: {missing}"


@pytest.mark.asyncio
async def test_the_workshops_are_named_in_the_listing(world_and_registry):
    """The specific seven, by name, so a regression names itself."""
    world, _ = world_and_registry
    listed = [p["place_id"] for p in known_places(world)]
    for workshop in ("mill", "kitchen", "sawmill", "smithy",
                     "carpentry", "loom_house", "patent_office"):
        assert any(satisfies_gate(p, workshop) for p in listed), (
            f"{workshop!r} is not reachable from anything list_places shows"
        )


@pytest.mark.asyncio
async def test_a_listed_place_says_what_it_is_for(world_and_registry):
    """A name alone is not discovery. Every listed place carries a purpose,
    or an agent reading the list learns nothing about why to walk there."""
    world, _ = world_and_registry
    for place in known_places(world):
        assert place.get("purpose"), f"{place['place_id']} has no purpose"


@pytest.mark.asyncio
async def test_the_listing_does_not_duplicate_a_sector(world_and_registry):
    """The civic locations already resolve through their map sectors -
    temple, town_hall and government_complex all pass without help. Adding
    them again would show a resident two names for one room."""
    world, _ = world_and_registry
    ids = [p["place_id"] for p in known_places(world)]
    assert len(ids) == len(set(ids))
    assert "temple" not in ids
    assert "government_complex" not in ids


@pytest.mark.asyncio
async def test_a_workshop_purpose_is_not_a_substring_accident(world_and_registry):
    """PLACE_PURPOSES matches on substrings, and substrings get these wrong
    three ways: "sawmill" contains "mill", "loom_house" contains "house",
    "patent_office" contains "office". The first pass told residents the
    sawmill grinds grain and the patent office is a desk job."""
    world, _ = world_and_registry
    by_id = {p["place_id"]: p["purpose"] for p in known_places(world)}

    assert "flour" in by_id["mill"]
    assert "planks" in by_id["sawmill"] and "flour" not in by_id["sawmill"]
    assert "weave" in by_id["loom_house"] and "sleep" not in by_id["loom_house"]
    assert "invention" in by_id["patent_office"] and "desk" not in by_id["patent_office"]


@pytest.mark.asyncio
async def test_each_workshop_names_the_tool_that_works_there(world_and_registry):
    """Discovery is not just a name: the listing has to say why to walk
    there, or an agent reading it learns nothing it can act on."""
    world, _ = world_and_registry
    for place in known_places(world):
        if place["kind"] == "workshop":
            assert "`" in place["purpose"], (
                f"{place['place_id']} does not name the tool it enables"
            )
