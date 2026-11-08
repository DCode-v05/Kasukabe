"""Proves world/governance_api.py against a real, fully-wired WorldServer
(build_world_server - the same composition root a live process boots),
not a mock of world.charter. Elections and amendments are exercised
through the real charter/ engine calls (schedule_election ->
declare_candidacy -> cast_vote -> tally; propose_amendment ->
cast_amendment_vote -> sign_amendment) so the router is proven against
real mutated CharterStore state, matching this track's "never trust a
mocked seam" mandate.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from world.charter.models import EligibilityFacts, GovernmentState, LOCATION_GOVERNMENT_COMPLEX, LOCATION_TOWN_HALL
from world.governance_api import GovernanceDeps, build_governance_router
from world.wiring import WorldConfig, build_world_server
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map


def make_client(server) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_governance_router(lambda: GovernanceDeps(world=server.world, genesis=server.genesis))
    )
    return TestClient(app)


def adult_agent_ids(server, n: int) -> list[str]:
    return [a.agent_id for a in server.world.agents.values() if a.age_years >= 18][:n]


async def run_full_election(server, candidate_ids: list[str], voter_ids: list[str]) -> str:
    """Runs a REAL election end to end through world.charter.elections -
    the same engine calls civic.py's `vote` tool and a future `declare_
    candidacy` tool would make - and returns the tallied election_id."""
    elections = server.world.charter.elections
    election = await elections.schedule_election(now_tick=0)

    for agent_id in candidate_ids:
        facts = EligibilityFacts(agent_id=agent_id, age_years=99, is_resident=True)
        await elections.declare_candidacy(election.election_id, agent_id, facts, now_tick=0, location=LOCATION_TOWN_HALL)

    voting_tick = election.voting_opens_tick
    for i, voter_id in enumerate(voter_ids):
        facts = EligibilityFacts(agent_id=voter_id, age_years=99, is_resident=True)
        candidate = candidate_ids[i % len(candidate_ids)]
        await elections.cast_vote(
            election.election_id, voter_id, candidate, facts, now_tick=voting_tick, location=LOCATION_TOWN_HALL
        )

    await elections.tally(election.election_id, now_tick=election.voting_closes_tick)
    return election.election_id


@pytest.mark.asyncio
async def test_charter_and_policy_reflect_real_genesis_defaults() -> None:
    server = await build_world_server(WorldConfig())
    try:
        client = make_client(server)

        charter_resp = client.get("/governance/charter")
        assert charter_resp.status_code == 200
        assert charter_resp.headers["access-control-allow-origin"] == "*"
        charter = charter_resp.json()
        assert charter["version"] == 0
        assert charter["pm_seats"] == 1
        assert charter["minister_seats"] == 4
        assert charter["amendment_votes_required"] == 3

        policy_resp = client.get("/governance/policy")
        assert policy_resp.status_code == 200
        assert policy_resp.json()["version"] == 0

        history_resp = client.get("/governance/charter/history")
        assert len(history_resp.json()) == 1
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_active_policies_reflects_a_real_police_hire() -> None:
    server = await build_world_server(WorldConfig())
    try:
        hirer = adult_agent_ids(server, 1)[0]
        officer = adult_agent_ids(server, 2)[1]
        await server.world.charter.store.set_government_state(
            GovernmentState(pm_agent_id=hirer, minister_agent_ids=[])
        )
        await server.world.charter.government.hire_police(
            officer, salary_sim=5_000, hired_by_agent_id=hirer, location=LOCATION_GOVERNMENT_COMPLEX, now_tick=0
        )

        client = make_client(server)
        resp = client.get("/governance/active-policies")
        assert resp.status_code == 200
        body = resp.json()
        assert body["police_force_size"] == 1
        assert body["police_officers"][0]["assigned_agent"]["agent_id"] == officer
        assert body["jobs_filled"] == 1
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_election_endpoint_shows_real_tally_and_ballots() -> None:
    server = await build_world_server(WorldConfig())
    try:
        candidates = adult_agent_ids(server, 3)
        voters = adult_agent_ids(server, 12)
        election_id = await run_full_election(server, candidates, voters)

        client = make_client(server)
        list_resp = client.get("/voting/elections")
        assert list_resp.status_code == 200
        summaries = list_resp.json()
        assert any(s["election_id"] == election_id for s in summaries)

        detail_resp = client.get(f"/voting/elections/{election_id}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["phase"] == "tallied"
        assert len(detail["ballots"]) == len(voters)
        assert detail["result"]["prime_minister"]["agent_id"] in candidates
        assert sum(detail["running_tally"].values()) == len(voters)

        gov_resp = client.get("/governance/government")
        assert gov_resp.json()["prime_minister"]["agent_id"] == detail["result"]["prime_minister"]["agent_id"]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_amendment_log_shows_a_real_charter_diff_after_enactment() -> None:
    server = await build_world_server(WorldConfig())
    try:
        candidates = adult_agent_ids(server, 5)
        voters = adult_agent_ids(server, 20)
        await run_full_election(server, candidates, voters)

        gov_state = await server.world.charter.store.get_government_state()
        seat_holders = gov_state.all_seat_holders()
        assert len(seat_holders) == 5

        amendments = server.world.charter.amendments
        proposal = await amendments.propose_amendment(
            field_name="permit_fee_sim",
            new_value=999,
            title="Raise permit fees",
            rationale="test",
            proposed_by_agent_id=seat_holders[0],
            now_tick=500,
        )
        for voter in seat_holders[:3]:
            proposal = await amendments.cast_amendment_vote(proposal.proposal_id, voter, True, now_tick=501)
        assert proposal.status.value == "passed_awaiting_president"

        enacted = await amendments.sign_amendment(proposal.proposal_id, now_tick=502)
        assert enacted.status.value == "enacted"

        client = make_client(server)
        resp = client.get("/governance/amendments")
        assert resp.status_code == 200
        entries = resp.json()
        entry = next(e for e in entries if e["proposal_id"] == proposal.proposal_id)
        assert entry["status"] == "enacted"
        assert entry["field_name"] == "permit_fee_sim"
        assert len(entry["votes"]) == 3
        assert entry["yes_votes"] == 3
        assert entry["proposed_by"]["agent_id"] == seat_holders[0]
        assert entry["diff"] is not None
        assert entry["diff"]["before_version"] == 0
        assert entry["diff"]["after_version"] == 1
        changed = {c["field_name"]: (c["before"], c["after"]) for c in entry["diff"]["changed_fields"]}
        assert changed["permit_fee_sim"] == (200, 999)
        assert "permit_fee_sim" in entry["diff"]["unified_text_diff"]
        assert "999" in entry["diff"]["unified_text_diff"]
    finally:
        await server.close()
