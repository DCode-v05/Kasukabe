from __future__ import annotations

import pytest

from world.charter.models import EligibilityFacts, GovernmentState, OrganizationKind
from world.tools.errors import ToolNotFoundError
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX, LOCATION_TOWN_HALL
from world.tools.tests.conftest import spawn_agent


@pytest.mark.asyncio
async def test_vote_records_a_ballot(world, registry) -> None:
    # GOV-04: watch the charter's own sink, which is where the event is
    # emitted from now that the handler no longer duplicates it.
    sink_rows: list[dict] = []
    previous = world.charter.elections.event_sink

    async def _capture(row):
        sink_rows.append(row)
        await previous(row)

    world.charter.elections.event_sink = _capture

    election = await world.charter.elections.schedule_election(0)
    world.current_tick = 10
    await world.charter.elections.declare_candidacy(
        election.election_id,
        "candidate",
        EligibilityFacts(agent_id="candidate", age_years=30.0, is_resident=True),
        10,
        LOCATION_TOWN_HALL,
    )
    await spawn_agent(world, "voter", LOCATION_TOWN_HALL, age_years=25.0, household_id="hh-1")
    world.current_tick = 300

    result = await registry.execute(
        world, "voter", "vote", {"election_id": election.election_id, "candidate_agent_id": "candidate", "because": "She has kept the mill weights honest all season."}
    )

    stored = await world.charter.store.get_election(election.election_id)
    assert stored.ballots["voter"] == "candidate"
    # GOV-04: the event comes from the charter engine's sink now, not from
    # the handler's return value. This test used to assert the HANDLER's
    # copy, which is exactly the duplicate row the finding is about - the
    # engine emitted one too, and once the runner wired the sink every
    # vote wrote two. `test_one_row_per_political_act.py` holds the
    # no-duplicates rule; this holds that the act is still recorded.
    seen = [row["type"] for row in sink_rows]
    assert "politics.vote.cast" in seen, (
        "GOV-04: the vote reached the store and no event was emitted at "
        "all - removing the handler's duplicate removed the only record"
    )
    assert not any(e.type == "politics.vote.cast" for e in result.events), (
        "GOV-04: the handler is emitting its own copy again"
    )


@pytest.mark.asyncio
async def test_vote_requires_town_hall(world, registry) -> None:
    election = await world.charter.elections.schedule_election(0)
    world.current_tick = 10
    await world.charter.elections.declare_candidacy(
        election.election_id,
        "candidate",
        EligibilityFacts(agent_id="candidate", age_years=30.0, is_resident=True),
        10,
        LOCATION_TOWN_HALL,
    )
    await spawn_agent(world, "voter", "park", age_years=25.0, household_id="hh-1")
    world.current_tick = 300

    from world.tools.errors import LocationGateError

    with pytest.raises(LocationGateError):
        await registry.execute(
            world, "voter", "vote", {"election_id": election.election_id, "candidate_agent_id": "candidate", "because": "She has kept the mill weights honest all season."}
        )


@pytest.mark.asyncio
async def test_petition_requires_sitting_government(world, registry) -> None:
    await spawn_agent(world, "citizen", LOCATION_GOVERNMENT_COMPLEX)
    with pytest.raises(ToolNotFoundError):
        # petition is GOVERNMENT-namespace: an ordinary citizen cannot even
        # see it, matching DESIGN.md's "non-members cannot see a
        # confidential tool exists."
        await registry.execute(
            world, "citizen", "petition", {"title": "x", "description": "y", "tile_count_requested": 1}
        )


@pytest.mark.asyncio
async def test_petition_filed_by_sitting_minister(world, registry) -> None:
    await spawn_agent(world, "minister1", LOCATION_GOVERNMENT_COMPLEX)
    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id="minister1", minister_agent_ids=[])
    )

    result = await registry.execute(
        world,
        "minister1",
        "petition",
        {"title": "More farmland", "description": "we need it", "tile_count_requested": 20},
    )
    assert result.result["petition_id"]
    pending = await world.charter.land_petitions.list_pending()
    assert any(p.petition_id == result.result["petition_id"] for p in pending)


@pytest.mark.asyncio
async def test_register_business_charges_fee_and_creates_business(world, registry) -> None:
    await spawn_agent(world, "owner", LOCATION_GOVERNMENT_COMPLEX)
    balance_before = world.ledger.sim_balance("owner")

    result = await registry.execute(
        world,
        "owner",
        "register_business",
        {"business_id": "cafe-1", "name": "Corner Cafe", "business_type": "cafe"},
    )

    assert result.result == {"business_id": "cafe-1"}
    assert world.businesses["cafe-1"].owner_agent_id == "owner"
    assert world.ledger.sim_balance("owner") == balance_before - 5_000
    assert world.ledger.sim_balance("cafe-1") == 0


@pytest.mark.asyncio
async def test_register_business_duplicate_id_rejected(world, registry) -> None:
    await spawn_agent(world, "owner", LOCATION_GOVERNMENT_COMPLEX)
    await registry.execute(
        world, "owner", "register_business", {"business_id": "dup", "name": "A", "business_type": "grocery"}
    )
    from world.tools.errors import AlreadyExistsError

    with pytest.raises(AlreadyExistsError):
        await registry.execute(
            world, "owner", "register_business", {"business_id": "dup", "name": "B", "business_type": "grocery"}
        )


@pytest.mark.asyncio
async def test_register_org_creates_organization_with_founder(world, registry) -> None:
    await spawn_agent(world, "founder", LOCATION_GOVERNMENT_COMPLEX)

    result = await registry.execute(
        world,
        "founder",
        "register_org",
        {"kind": OrganizationKind.RELIGION.value, "name": "The Riverside Faith", "charter_text": "be kind"},
    )

    org = await world.charter.organizations.load_organization(result.result["org_id"])
    assert org.members["founder"] == "founder"


@pytest.mark.asyncio
async def test_file_complaint_records_a_crime_case(world, registry) -> None:
    await spawn_agent(world, "complainant", LOCATION_GOVERNMENT_COMPLEX)
    await spawn_agent(world, "accused", "park")

    result = await registry.execute(
        world,
        "complainant",
        "file_complaint",
        {"against_agent_id": "accused", "description": "took my bicycle"},
    )

    assert len(world.crime_cases) == 1
    assert world.crime_cases[0].case_id == result.result["case_id"]
    assert world.crime_cases[0].against_agent_id == "accused"


# -- hire_employee / fire_employee / transfer_business -----------------
# DESIGN.md section 2: business namespace is "owner + explicitly granted
# employees ... transfers with sale/inheritance." These exercise the only
# two things that ever change who a BUSINESS-namespace tool is visible to
# (registry.py's `is_visible`): the employee roster and `owner_agent_id`
# itself.


async def register_business(world, registry, owner_id: str, business_id: str = "biz-1"):
    await spawn_agent(world, owner_id, LOCATION_GOVERNMENT_COMPLEX)
    return await registry.execute(
        world, owner_id, "register_business", {"business_id": business_id, "name": "Biz", "business_type": "grocery"}
    )


@pytest.mark.asyncio
async def test_hire_employee_grants_business_roster_membership(world, registry) -> None:
    await register_business(world, registry, "owner")
    await spawn_agent(world, "staffer", "park")

    await registry.execute(world, "owner", "hire_employee", {"business_id": "biz-1", "employee_agent_id": "staffer", "employee_consent": True})

    assert "staffer" in world.businesses["biz-1"].employee_agent_ids


@pytest.mark.asyncio
async def test_hire_employee_rejected_for_non_owner(world, registry) -> None:
    await register_business(world, registry, "owner")
    await spawn_agent(world, "impostor", "park")
    await spawn_agent(world, "staffer", "park")

    from world.tools.errors import ActorRequirementError

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world, "impostor", "hire_employee", {"business_id": "biz-1", "employee_agent_id": "staffer", "employee_consent": True}
        )


@pytest.mark.asyncio
async def test_fire_employee_revokes_business_roster_membership(world, registry) -> None:
    await register_business(world, registry, "owner")
    await spawn_agent(world, "staffer", "park")
    await registry.execute(world, "owner", "hire_employee", {"business_id": "biz-1", "employee_agent_id": "staffer", "employee_consent": True})

    await registry.execute(world, "owner", "fire_employee", {"business_id": "biz-1", "employee_agent_id": "staffer"})

    assert "staffer" not in world.businesses["biz-1"].employee_agent_ids


@pytest.mark.asyncio
async def test_fire_employee_unknown_employee_raises_not_found(world, registry) -> None:
    await register_business(world, registry, "owner")

    from world.tools.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await registry.execute(
            world, "owner", "fire_employee", {"business_id": "biz-1", "employee_agent_id": "nobody"}
        )


@pytest.mark.asyncio
async def test_transfer_business_moves_owner_agent_id(world, registry) -> None:
    await register_business(world, registry, "seller")
    await spawn_agent(world, "buyer", "park")

    result = await registry.execute(
        world, "seller", "transfer_business", {"business_id": "biz-1", "new_owner_agent_id": "buyer"}
    )

    assert result.result == {"business_id": "biz-1"}
    assert world.businesses["biz-1"].owner_agent_id == "buyer"


@pytest.mark.asyncio
async def test_transfer_business_old_owner_loses_authority_immediately(world, registry) -> None:
    """DESIGN.md section 2: business access "transfers with sale/
    inheritance" - the same atomicity a government handover gets, but for
    a business: registry.py's `is_visible` reads `owner_agent_id` fresh on
    every call, so the instant the single-field assignment lands, the old
    owner can no longer manage a business they no longer own, with no
    separate revocation step and no window where a stale grant lingers.
    """
    await register_business(world, registry, "seller")
    await spawn_agent(world, "buyer", "park")
    await registry.execute(
        world, "seller", "transfer_business", {"business_id": "biz-1", "new_owner_agent_id": "buyer"}
    )

    from world.tools.errors import ActorRequirementError

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world, "seller", "hire_employee", {"business_id": "biz-1", "employee_agent_id": "buyer", "employee_consent": True}
        )
    # The new owner's authority is live in the same instant.
    await spawn_agent(world, "staffer2", "park")
    await registry.execute(
        world, "buyer", "hire_employee", {"business_id": "biz-1", "employee_agent_id": "staffer2", "employee_consent": True}
    )
    assert "staffer2" in world.businesses["biz-1"].employee_agent_ids
