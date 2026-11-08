"""The place the brief calls your work has to be a place you can reach.

`GROW-29`. `WORKPLACE_BY_OCCUPATION` maps an occupation to a workplace and
the standing brief renders it as "You are at your work, X" / tells the
resident where its work is. Three of the twelve values are workshop
CONSTANTS - `carpentry`, `kitchen`, `sawmill` - which `known_places` does
not offer as doors, so a resident told its work is at `carpentry` is being
sent somewhere `list_places` never names.

The comment right above those entries says they were pointed at "the
walkable places that actually do the job", which was true when workshops
were listed as places in their own right. `GROW-28` then correctly stopped
listing a workshop a business provides as its own door - and left these
three pointing at the doors it had just removed.

The fix is the one `TOOL-02` already built: a workshop belongs to a
business's premises, so a miller's work is the mill BUSINESS, at a
location `move` accepts. Resolved against the live world rather than
hard-coded, because which business provides which workshop is genesis's
choice and differs between the 10-, 20- and 50-resident casts.
"""
from __future__ import annotations

import pytest

from world.run_genesis import (
    WORKPLACE_BY_OCCUPATION,
    GenesisRunner,
    GenesisRunnerConfig,
)
from world.tools.primitives.places import known_places


def _runner(tmp_path) -> GenesisRunner:
    return GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    ))


@pytest.mark.asyncio
async def test_every_workplace_the_brief_names_is_reachable(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    reachable = {p["place_id"] for p in known_places(runner.world_state)}

    unreachable = sorted(
        {
            runner.resolve_workplace(occupation)
            for occupation in WORKPLACE_BY_OCCUPATION
        }
        - reachable
        - {None}
    )
    assert not unreachable, (
        "GROW-29: the brief sends residents to places known_places never "
        f"offers: {unreachable}"
    )


@pytest.mark.asyncio
async def test_a_miller_is_sent_to_the_mill_business(tmp_path):
    """The specific case, so a regression names itself. TOOL-02 made a
    business's premises satisfy the workshops its trade needs; this is the
    same fact read from the other end."""
    runner = _runner(tmp_path)
    await runner.boot()
    world = runner.world_state

    mills = [
        business
        for business in world.businesses.values()
        if "mill" in getattr(business, "provides_workshops", set())
    ]
    if not mills:
        pytest.skip("this cast has no business providing a mill")

    resolved = runner.resolve_workplace("miller")
    assert resolved in {m.location_id for m in mills}, (
        f"a miller's work resolved to {resolved!r}, which is no mill business"
    )


@pytest.mark.asyncio
async def test_an_occupation_nobody_holds_resolves_to_nothing_not_a_phantom(tmp_path):
    """Absent is a fine answer. A made-up place is not - it is what sent
    Junia Vance to a room no customer could walk into for a whole run."""
    runner = _runner(tmp_path)
    await runner.boot()
    assert runner.resolve_workplace("astronaut") is None
