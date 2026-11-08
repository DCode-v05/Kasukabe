"""B5: the treasury made visible, and policy changes attributed to a term.

The 250 section 23: "a real budget an elected government actually manages...
so a policy choice has a traceable financial consequence, not just a rule
change", and "a new Prime Minister's actual policy choices should be
attributable, in the trace, to that specific term in office."

Read paths over state that already exists. Proven against a real wired
WorldServer and real charter engine calls, same as test_governance_api.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from world.charter.models import (
    EligibilityFacts,
    LOCATION_TOWN_HALL,
)
from world.governance_api import (
    GovernanceDeps,
    build_governance_router,
    build_terms,
)
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import TREASURY_ACCOUNT_ID
from world.wiring import WorldConfig, build_world_server
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map


def make_client(server) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_governance_router(lambda: GovernanceDeps(world=server.world, genesis=server.genesis))
    )
    return TestClient(app)


async def run_election(server, winner: str, voters: list[str], at_tick_offset: int = 0):
    elections = server.world.charter.elections
    election = await elections.schedule_election(now_tick=at_tick_offset)
    facts = EligibilityFacts(agent_id=winner, age_years=99, is_resident=True)
    await elections.declare_candidacy(
        election.election_id, winner, facts, now_tick=at_tick_offset, location=LOCATION_TOWN_HALL
    )
    for voter in voters:
        await elections.cast_vote(
            election.election_id, voter, winner,
            EligibilityFacts(agent_id=voter, age_years=99, is_resident=True),
            now_tick=election.voting_opens_tick, location=LOCATION_TOWN_HALL,
        )
    await elections.tally(election.election_id, now_tick=election.voting_closes_tick)
    return election


@pytest.mark.asyncio
async def test_the_treasury_balance_is_visible():
    server = await build_world_server(WorldConfig())
    view = make_client(server).get("/governance/treasury").json()
    account = server.world.ledger.accounts[TREASURY_ACCOUNT_ID]
    assert view["balance_sim"] == account.sim_units
    assert view["balance_usd_cents"] == account.usd_cents


@pytest.mark.asyncio
async def test_tax_in_and_pensions_out_are_broken_out():
    """"tax income in, pensions/police/permits/tenders out"."""
    server = await build_world_server(WorldConfig())
    payer, pensioner = list(server.world.agents)[:2]
    await server.world.ledger.move(
        from_account_id=payer, to_account_id=TREASURY_ACCOUNT_ID, amount=500,
        currency=Currency.SIM, reason=TransactionReason.TAX, tick=1,
    )
    await server.world.ledger.move(
        from_account_id=TREASURY_ACCOUNT_ID, to_account_id=pensioner, amount=200,
        currency=Currency.SIM, reason=TransactionReason.PENSION, tick=2,
    )
    view = make_client(server).get("/governance/treasury").json()
    assert view["tax_collected_sim"] == 500
    assert view["pensions_paid_sim"] == 200


@pytest.mark.asyncio
async def test_the_policy_is_served_next_to_the_balance_it_feeds():
    """A rate is a rule; a rule with no visible consequence is what "exists
    but is not lived in" means."""
    server = await build_world_server(WorldConfig())
    body = make_client(server).get("/governance/policy-and-treasury").json()
    assert "tax_rate_bps" in body["policy"]
    assert "balance_sim" in body["treasury"]
    policy_only = make_client(server).get("/governance/policy").json()
    assert body["policy"]["tax_rate_bps"] == policy_only["tax_rate_bps"]


@pytest.mark.asyncio
async def test_a_real_election_produces_a_term():
    server = await build_world_server(WorldConfig())
    adults = [a.agent_id for a in server.world.agents.values() if a.age_years >= 18][:6]
    await run_election(server, adults[0], adults[1:])
    terms = make_client(server).get("/governance/terms").json()
    assert terms, "a tallied election should produce a term"
    assert terms[0]["pm_agent_id"] == adults[0]
    assert terms[0]["is_current"] is True
    assert terms[0]["pm_name"] != adults[0], "a display name, not an id"


def test_an_amendment_lands_in_exactly_the_term_it_was_enacted_under():
    """The whole point of B5's second half."""
    class Result:
        def __init__(self, pm, tick):
            self.pm_agent_id, self.tallied_tick = pm, tick
            self.minister_agent_ids = []

    class Election:
        def __init__(self, eid, pm, tick):
            self.election_id, self.result = eid, Result(pm, tick)

    class Amend:
        def __init__(self, pid, title, tick):
            self.proposal_id, self.title, self.enacted_tick = pid, title, tick

    terms = build_terms(
        {"alice": "Alice", "bob": "Bob"},
        [Election("e1", "alice", 100), Election("e2", "bob", 500)],
        [Amend("a1", "raise tax", 200), Amend("a2", "cut police", 700)],
        None,
    )
    by_pm = {t.pm_agent_id: t for t in terms}
    assert by_pm["alice"].amendments_enacted == ["a1"]
    assert by_pm["bob"].amendments_enacted == ["a2"]
    assert by_pm["alice"].left_office_tick == 500
    assert by_pm["bob"].is_current is True


def test_an_amendment_from_before_any_election_belongs_to_nobody():
    """Attributing it to the first PM would be a lie - they were not in
    office when it happened."""
    class Result:
        pm_agent_id, tallied_tick, minister_agent_ids = "alice", 100, []

    class Election:
        election_id, result = "e1", Result()

    class Amend:
        proposal_id, title, enacted_tick = "a0", "genesis rule", 5

    terms = build_terms({"alice": "Alice"}, [Election()], [Amend()], None)
    assert terms[0].amendments_enacted == []


def test_a_government_seated_without_an_election_is_still_a_term():
    class Current:
        pm_agent_id, minister_agent_ids = "alice", ["bob"]

    class Amend:
        proposal_id, title, enacted_tick = "a1", "genesis rule", 5

    terms = build_terms({"alice": "Alice"}, [], [Amend()], Current())
    assert len(terms) == 1
    assert terms[0].election_id is None
    assert terms[0].amendments_enacted == ["a1"]


def test_an_unenacted_proposal_is_never_attributed_to_anyone():
    class Result:
        pm_agent_id, tallied_tick, minister_agent_ids = "alice", 0, []

    class Election:
        election_id, result = "e1", Result()

    class Proposed:
        proposal_id, title, enacted_tick = "a1", "still being argued about", None

    terms = build_terms({"alice": "Alice"}, [Election()], [Proposed()], None)
    assert terms[0].amendments_enacted == []


@pytest.mark.asyncio
async def test_terms_are_newest_first():
    server = await build_world_server(WorldConfig())
    rows = make_client(server).get("/governance/terms").json()
    ticks = [r["took_office_tick"] for r in rows]
    assert ticks == sorted(ticks, reverse=True)


@pytest.mark.asyncio
async def test_these_routes_mutate_nothing():
    server = await build_world_server(WorldConfig())
    before = len(server.world.ledger.transactions)
    client = make_client(server)
    client.get("/governance/treasury")
    client.get("/governance/policy-and-treasury")
    client.get("/governance/terms")
    assert len(server.world.ledger.transactions) == before
