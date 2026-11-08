"""TOOL-08: the tool admitted them and the engine behind it threw them out.

`place_aliases.satisfies_gate` folds "Kasukabe Government Complex" onto
`government_complex`, and folds the complex onto the town hall, because on
this map they are one building. The registry gate uses it. The charter
engines behind those tools did not:

    # world/charter/elections.py:194
    if location != LOCATION_TOWN_HALL:
        raise NotAtRequiredLocation(LOCATION_TOWN_HALL, location)

So a resident standing exactly where `list_places` says to stand passed the
tool's gate and was refused by the engine - and because `CharterError` is
not a `ToolError`, that refusal was recorded nowhere: not `tool_refusals`,
not `gap_feedback`, not the run report's "what the town could not do".

This is a large part of why the civic surface shows zero calls across 23
runs. `vote`, `declare_candidacy`, `register_org` and `define_tool` need no
government seat at all - the vacant seats (GOV-01) explain the seat-gated
half, and this explains the rest.
"""

from __future__ import annotations

import pytest

from world.charter.errors import NotAtRequiredLocation
from world.charter.models import OrganizationKind
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX, LOCATION_TOWN_HALL
from world.tools.place_aliases import place_for_charter

from .conftest import spawn_agent

COMPLEX_SECTOR = "Kasukabe Government Complex"


@pytest.mark.asyncio
async def test_a_satisfied_gate_is_handed_on_as_the_constant(world):
    await spawn_agent(world, "n01", COMPLEX_SECTOR)

    assert place_for_charter(world, "n01", LOCATION_GOVERNMENT_COMPLEX) == (
        LOCATION_GOVERNMENT_COMPLEX
    )
    assert place_for_charter(world, "n01", LOCATION_TOWN_HALL) == LOCATION_TOWN_HALL


@pytest.mark.asyncio
async def test_an_unsatisfied_gate_still_reports_where_the_resident_really_is(
    world,
):
    """The fold must not become "every gate passes".

    An engine called without the registry in front of it has to keep
    raising, and its error has to name the place the resident is actually
    standing in - otherwise the refusal teaches them nothing.
    """
    await spawn_agent(world, "n02", "Kasukabe Farmland")

    assert place_for_charter(world, "n02", LOCATION_TOWN_HALL) == "Kasukabe Farmland"


@pytest.mark.asyncio
async def test_an_organisation_can_be_registered_from_the_sector_the_map_draws(
    world, registry
):
    await spawn_agent(world, "n03", COMPLEX_SECTOR, age_years=40.0, household_id="h1")

    outcome = await registry.execute(
        world,
        "n03",
        "register_org",
        {
            "kind": OrganizationKind.PARTY,
            "name": "The Millers Party",
            "charter_text": "We mill.",
        },
    )

    assert outcome.result
    assert world.tool_refusals == []


@pytest.mark.asyncio
async def test_a_petition_can_be_filed_from_the_sector_the_map_draws(world, registry):
    """`petition` is seat-gated too, so this proves the PLACE half only.

    A resident without a seat is refused for the seat, not for the place -
    and that refusal now reaches the log, which is the other half of this
    finding.
    """
    await spawn_agent(world, "n04", COMPLEX_SECTOR, age_years=40.0, household_id="h1")

    with pytest.raises(Exception) as raised:
        await registry.execute(
            world,
            "n04",
            "petition",
            {
                "title": "More farmland",
                "description": "The fields are full.",
                "tile_count_requested": 20,
            },
        )

    assert "location" not in str(raised.value).lower()


@pytest.mark.asyncio
async def test_a_charter_refusal_is_recorded_as_a_gap_like_any_other(world, registry):
    """`CharterError` fell through `unmet_requirement_for` and vanished.

    The town could not see that its own civic surface was refusing people.
    """
    await spawn_agent(world, "n05", COMPLEX_SECTOR, age_years=40.0, household_id="h1")

    with pytest.raises(Exception):
        await registry.execute(
            world,
            "n05",
            "vote",
            {"election_id": "no-such-election", "candidate_agent_id": "n03", "because": "She has kept the mill weights honest all season."},
        )

    assert world.tool_refusals, "a charter refusal must reach the refusal log"
    assert world.tool_refusals[-1].tool_name == "vote"
    assert world.tool_refusals[-1].unmet_requirement.startswith("charter:")


@pytest.mark.asyncio
async def test_a_place_refusal_from_the_charter_groups_with_the_registry_s(world):
    """One gap, one key, whichever half of the system said no."""
    from world.tools.registry import unmet_requirement_for

    charter_side = unmet_requirement_for(
        NotAtRequiredLocation(LOCATION_TOWN_HALL, "Kasukabe Farmland")
    )
    assert charter_side is not None
    key, _ = charter_side
    assert key == f"location:{LOCATION_TOWN_HALL}"
