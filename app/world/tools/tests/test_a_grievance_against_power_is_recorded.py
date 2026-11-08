"""A complaint about the government is written down.

`GOV-22`. DESIGN section 7 makes the immutable complaint ledger one of
only TWO genesis checks on government power.
`ComplaintLedger.file_complaint` has existed since genesis and had no
caller anywhere in the tree, so `GET /governance/complaints` served a
permanently empty list. "This town has no grievances" and "this ledger
has no writer" were the same observation.

The live `file_complaint` tool wrote a `CrimeCase` instead - a
prosecution, which is a different act. You cannot arrest the government.

Routed through the existing verb rather than a new
`complain_about_government`: residents already know `file_complaint`,
and a second word for the same act is a word a resident has to discover
before it can hold anybody to account.

The ledger is append-only and returns no verdict, deliberately. A
complaint about power is a RECORD that the government cannot erase; that
is what makes it a check. Turning it into a case with an outcome would
hand the accused institution the judgment.
"""
from __future__ import annotations

import pytest

from world.tools.errors import ToolValidationError

from .test_construction import spawn_agent  # noqa: F401


@pytest.mark.asyncio
async def test_a_complaint_about_the_government_reaches_the_ledger(
    world, registry
) -> None:
    """The finding. Before this the call had no caller at all."""
    await spawn_agent(world, "citizen", "town_hall")

    result = await registry.execute(
        world, "citizen", "file_complaint",
        {
            "against_agent_id": "government",
            "against_kind": "government",
            "description": "The tax was raised without a sitting.",
        },
    )

    assert result.result["recorded"] is True
    entries = await world.charter.complaints.list_all()
    assert entries, (
        "GOV-22: the complaint ledger is still empty, so the design's own "
        "check on government power records nothing"
    )
    assert entries[-1].complainant_agent_id == "citizen"
    assert entries[-1].against_kind == "government"


@pytest.mark.asyncio
async def test_a_complaint_about_an_official_names_a_real_person(
    world, registry
) -> None:
    """An official is a resident holding a seat, so the id must exist -
    otherwise the ledger fills with grievances against nobody."""
    from world.tools.errors import NotFoundError

    await spawn_agent(world, "citizen", "town_hall")

    with pytest.raises(NotFoundError):
        await registry.execute(
            world, "citizen", "file_complaint",
            {
                "against_agent_id": "a-minister-who-does-not-exist",
                "against_kind": "official",
                "description": "took a bribe",
            },
        )


@pytest.mark.asyncio
async def test_the_government_itself_needs_no_agent_id(world, registry) -> None:
    """"The government" is an institution, not a resident. Requiring it to
    be in `world.agents` would make the institution the one thing nobody
    can complain about."""
    await spawn_agent(world, "citizen", "town_hall")

    result = await registry.execute(
        world, "citizen", "file_complaint",
        {
            "against_agent_id": "government",
            "against_kind": "government",
            "description": "the treasury is unaccounted for",
        },
    )
    assert result.result["ledger_index"] >= 0


@pytest.mark.asyncio
async def test_an_ordinary_complaint_still_opens_a_case(world, registry) -> None:
    """The control, and it matters: routing everything to the ledger
    would silently disable the justice system."""
    await spawn_agent(world, "citizen", "town_hall")
    await spawn_agent(world, "suspect", "town_hall")

    before = len(world.crime_cases)
    await registry.execute(
        world, "citizen", "file_complaint",
        {"against_agent_id": "suspect", "description": "took my grain"},
    )

    assert len(world.crime_cases) == before + 1, (
        "GOV-22: an ordinary complaint no longer opens a criminal case"
    )
    assert not await world.charter.complaints.list_all(), (
        "an ordinary complaint was written to the constitutional ledger"
    )


@pytest.mark.asyncio
async def test_the_default_is_the_old_behaviour(world, registry) -> None:
    """Every existing caller omits `against_kind`, and must keep working
    exactly as it did."""
    from world.tools.primitives.civic import FileComplaintParams

    assert FileComplaintParams.model_fields["against_kind"].default == "resident"


@pytest.mark.asyncio
async def test_a_nonsense_kind_is_refused(world, registry) -> None:
    """Silently treating an unknown kind as `resident` would file a
    complaint about the government as a criminal case against whoever the
    id happened to name."""
    await spawn_agent(world, "citizen", "town_hall")

    with pytest.raises(ToolValidationError):
        await registry.execute(
            world, "citizen", "file_complaint",
            {
                "against_agent_id": "government",
                "against_kind": "the-vibes",
                "description": "unhappy",
            },
        )


@pytest.mark.asyncio
async def test_the_ledger_is_append_only_across_complaints(world, registry) -> None:
    """What makes it a check: a second complaint does not replace the
    first, and the indices are monotonic."""
    await spawn_agent(world, "citizen", "town_hall")

    first = await registry.execute(
        world, "citizen", "file_complaint",
        {"against_agent_id": "government", "against_kind": "government",
         "description": "one"},
    )
    second = await registry.execute(
        world, "citizen", "file_complaint",
        {"against_agent_id": "government", "against_kind": "government",
         "description": "two"},
    )

    assert second.result["ledger_index"] > first.result["ledger_index"]
    assert len(await world.charter.complaints.list_all()) == 2
