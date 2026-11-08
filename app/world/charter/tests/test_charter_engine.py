"""Integration tests for world/charter/engine.py: the CharterEngine
facade wiring every sub-engine to one shared store and event sink,
exercised end to end the way world/'s eventual tool dispatcher would.
"""

from __future__ import annotations

import pytest

from world.charter.engine import create_genesis_charter_engine
from world.charter.models import (
    LOCATION_GOVERNMENT_COMPLEX,
    LOCATION_PATENT_OFFICE,
    LOCATION_TOWN_HALL,
    AmendmentStatus,
    EligibilityFacts,
    OrganizationKind,
)
from world.charter.tests.conftest import EventRecorder


def voter(agent_id: str) -> EligibilityFacts:
    return EligibilityFacts(agent_id=agent_id, age_years=25, is_resident=True)


@pytest.mark.asyncio
async def test_genesis_engine_has_no_sitting_government_until_first_election() -> None:
    engine = await create_genesis_charter_engine(genesis_tick=0)
    state = await engine.store.get_government_state()
    assert state.pm_agent_id is None


@pytest.mark.asyncio
async def test_full_election_then_government_action_end_to_end() -> None:
    """The vertical slice: schedule an election, elect a government, and
    have the elected PM immediately exercise a government power. If this
    doesn't work, the sub-engines are not actually wired together through
    one shared store the way the facade promises.
    """
    recorder = EventRecorder()
    engine = await create_genesis_charter_engine(genesis_tick=0, event_sink=recorder.sink)

    election = await engine.elections.schedule_election(now_tick=0)
    await engine.elections.declare_candidacy(
        election.election_id, "agent-pm", voter("agent-pm"), now_tick=0, location=LOCATION_TOWN_HALL
    )
    await engine.elections.cast_vote(
        election.election_id,
        "voter-1",
        "agent-pm",
        voter("voter-1"),
        now_tick=300,
        location=LOCATION_TOWN_HALL,
    )
    tallied = await engine.elections.tally(election.election_id, now_tick=500)

    assert tallied.result is not None
    assert tallied.result.pm_agent_id == "agent-pm"

    policy = await engine.government.set_tax_rate(
        300, "agent-pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=501
    )
    assert policy.tax_rate_bps == 300

    event_types = recorder.types()
    assert "politics.election.tallied" in event_types
    assert "politics.tax_rate.set" in event_types


@pytest.mark.asyncio
async def test_full_amendment_flow_end_to_end() -> None:
    engine = await create_genesis_charter_engine(genesis_tick=0)
    election = await engine.elections.schedule_election(now_tick=0)
    for agent_id in ("pm-cand", "min-a", "min-b", "min-c", "min-d"):
        await engine.elections.declare_candidacy(
            election.election_id, agent_id, voter(agent_id), now_tick=0, location=LOCATION_TOWN_HALL
        )
    ballots = {
        "v1": "pm-cand",
        "v2": "min-a",
        "v3": "min-b",
        "v4": "min-c",
        "v5": "min-d",
    }
    for voter_id, candidate_id in ballots.items():
        await engine.elections.cast_vote(
            election.election_id,
            voter_id,
            candidate_id,
            voter(voter_id),
            now_tick=300,
            location=LOCATION_TOWN_HALL,
        )
    await engine.elections.tally(election.election_id, now_tick=500)

    proposal = await engine.amendments.propose_amendment(
        "patent_filing_fee_sim",
        5000,
        "raise filing fee",
        "treasury needs it",
        "pm-cand",
        now_tick=501,
    )
    for seat in ("pm-cand", "min-a", "min-b"):
        await engine.amendments.cast_amendment_vote(proposal.proposal_id, seat, True, now_tick=502)
    signed = await engine.amendments.sign_amendment(proposal.proposal_id, now_tick=503)
    assert signed.status == AmendmentStatus.ENACTED

    charter = await engine.store.get_charter()
    assert charter.patent_filing_fee_sim == 5000

    application = await engine.patent_office.file_application(
        "inventor-1", "auto-plow", "d", ["harvest"], 5000, LOCATION_PATENT_OFFICE, now_tick=504
    )
    patent = await engine.patent_office.approve_application(
        application.application_id, "pm-cand", LOCATION_GOVERNMENT_COMPLEX, now_tick=505
    )
    assert patent.owner_agent_id == "inventor-1"


@pytest.mark.asyncio
async def test_land_petition_and_organization_registration_end_to_end() -> None:
    engine = await create_genesis_charter_engine(genesis_tick=0)
    current_state = await engine.store.get_government_state()
    await engine.store.set_government_state(
        current_state.model_copy(update={"pm_agent_id": "pm-1"})
    )

    petition = await engine.land_petitions.file_petition(
        "pm-1", "expand east", "more forest", 15, LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    approved = await engine.land_petitions.approve_petition(
        petition.petition_id, "approved", now_tick=1
    )
    assert approved.status.value == "approved"

    org = await engine.organizations.register_organization(
        OrganizationKind.RELIGION,
        "The New Faith",
        "founder-1",
        "one rule: choose a keeper",
        LOCATION_GOVERNMENT_COMPLEX,
        now_tick=0,
    )
    assert org.members == {"founder-1": "founder"}

    entry = await engine.complaints.file_complaint(
        "agent-2", "pm-1", "government", "unfair permit denial", now_tick=2
    )
    assert entry.ledger_index == 0
