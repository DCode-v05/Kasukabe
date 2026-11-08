"""The temple exists, and a resident who names it must be able to stand in it.

Observed in a run: agents talked about the temple, and the temple gate
refused them. Both halves of that are true at once, which is why it was
confusing.

The map draws a sector called **"Kasukabe Temple"**. The tools gate on the
constant **`"temple"`**. `canonical_place` folded one to the other through
an exact-case dict, so:

    "Kasukabe Temple"  ->  temple    ✓
    "temple"           ->  temple    ✓
    "Temple"           ->  Temple    ✗   refused
    "kasukabe temple"  ->  ...       ✗   refused

A resident writing "Temple" - the natural capitalisation, and the one a
model produces when it writes a place name in a sentence - was standing in
the temple and being told it was not there. `pray` and `marry` are the only
two tools gated on it, so the town's whole religious and marital life sat
behind a capital letter.
"""
from __future__ import annotations

import pytest

from world.tools.locations import (
    LOCATION_GOVERNMENT_COMPLEX,
    LOCATION_TEMPLE,
)
from world.tools.place_aliases import canonical_place, satisfies_gate


@pytest.mark.parametrize(
    "written",
    ["temple", "Temple", "Kasukabe Temple", "kasukabe temple", "KASUKABE TEMPLE"],
)
def test_every_reasonable_spelling_of_the_temple_reaches_the_gate(written):
    assert satisfies_gate(written, LOCATION_TEMPLE), (
        f"a resident standing at {written!r} is refused by `pray` and "
        "`marry`, which are the only two tools gated on the temple"
    )


@pytest.mark.parametrize(
    "written",
    ["government_complex", "Government_Complex", "Kasukabe Government Complex"],
)
def test_the_same_holds_for_the_government_complex(written):
    """The other place with two vocabularies - and the one where four
    residents once stood unable to use a single civic tool."""
    assert satisfies_gate(written, LOCATION_GOVERNMENT_COMPLEX)


def test_a_different_place_is_still_a_different_place():
    """Case-insensitive must not become place-insensitive."""
    assert not satisfies_gate("Kasukabe Farmland", LOCATION_TEMPLE)
    assert not satisfies_gate("temple", LOCATION_GOVERNMENT_COMPLEX)


def test_an_invented_room_keeps_its_own_name():
    """A resident's invented place must not be folded into another by
    lowercasing - two different rooms would merge."""
    assert canonical_place("Marta's Back Room") == "Marta's Back Room"
    assert canonical_place("The Old Well") == "The Old Well"


def test_none_is_still_nowhere():
    assert canonical_place(None) is None
    assert satisfies_gate(None, LOCATION_TEMPLE) is False


@pytest.mark.asyncio
async def test_the_temple_is_findable_from_inside_the_world(tmp_path):
    """The other half of the confusion: a resident has to be able to FIND
    it before it can stand in it."""
    from world.run_genesis import GenesisRunner, GenesisRunnerConfig
    from world.tools.primitives.places import known_places

    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000,
    ))
    await runner.boot()
    names = [p["place_id"] for p in known_places(runner.world_state)]

    reachable = [n for n in names if satisfies_gate(n, LOCATION_TEMPLE)]
    assert reachable, (
        f"none of the {len(names)} places a resident can discover satisfies "
        "the temple gate, so `pray` and `marry` are unreachable however well "
        "a resident understands the town"
    )
