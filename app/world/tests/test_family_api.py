"""B3: world/family_api.py, against a real fully-wired WorldServer.

The 250 section 19's six views. Proven against build_world_server() - the
same composition root a live process boots - not a mock, matching
test_governance_api.py's own "never trust a mocked seam" mandate.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from world.family_api import (
    FamilyDeps,
    RUNWAY_CAP_DAYS,
    build_family_router,
    household_shape,
    money_flow,
    runway_days,
    status_for,
)
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import TREASURY_ACCOUNT_ID
from world.life.models import Household
from world.wiring import WorldConfig, build_world_server
from world.tests.map_assets import needs_the_map

# TOOL-27. This module needs the Ville map assets, which live in the
# sibling `smallville/` checkout, are gitignored, and are absent on any
# host that did not clone them. Without them these FAILED rather than
# skipped, and red for environment hides real regressions inside the
# noise - which is how TOOL-09 stayed invisible for a whole cycle.
pytestmark = needs_the_map


def make_client(server) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_family_router(lambda: FamilyDeps(world=server.world, genesis=server.genesis))
    )
    return TestClient(app)


# -- the arithmetic, directly ---------------------------------------------

def test_runway_is_balance_over_the_daily_bill():
    assert runway_days(900, 90) == 10
    assert runway_days(95, 90) == 1


def test_a_broke_household_has_no_runway_not_infinite_runway():
    assert runway_days(0, 90) == 0
    assert runway_days(-500, 90) == 0


def test_a_household_that_consumes_nothing_is_capped_not_infinite():
    """So the watchlist cannot be gamed by a division trending to infinity,
    and the JSON never carries an inf."""
    assert runway_days(1000, 0) == RUNWAY_CAP_DAYS


def test_status_labels_track_the_escalation_ladder():
    assert status_for(0) == "destitute"
    assert status_for(1) == "critical"
    assert status_for(5) == "at risk"
    assert status_for(20) == "stable"
    assert status_for(400) == "comfortable"
    assert status_for(400, alive=False) == "deceased"


def test_household_shape_is_named_from_composition():
    assert household_shape(1, 0) == "single, no dependents"
    assert household_shape(2, 0) == "dual-earner, no dependents"
    assert household_shape(1, 3) == "single-earner with dependents"
    assert household_shape(2, 2) == "dual-earner with dependents"
    assert household_shape(0, 0) == "empty"


# -- 19.1 -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_per_agent_survival_covers_the_whole_town():
    server = await build_world_server(WorldConfig())
    rows = make_client(server).get("/family/agents").json()
    assert len(rows) == len(server.world.agents)
    row = rows[0]
    assert row["name"] and row["name"] != row["agent_id"], "a display name, not an id"
    assert row["days_of_runway"] >= 0
    assert row["status"] in {"destitute", "critical", "at risk", "stable", "comfortable", "deceased"}


@pytest.mark.asyncio
async def test_a_dead_agent_reads_as_deceased_not_as_broke():
    server = await build_world_server(WorldConfig())
    victim = next(iter(server.world.agents))
    server.world.agents[victim] = server.world.agents[victim].model_copy(update={"alive": False})
    rows = {r["agent_id"]: r for r in make_client(server).get("/family/agents").json()}
    assert rows[victim]["status"] == "deceased"


# -- 19.2 -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_household_solvency_uses_the_real_upkeep_bill():
    """Not a re-derivation: a change to the upkeep constants must move this
    view too, instead of the two silently disagreeing."""
    from world.life.upkeep import compute_household_bill

    server = await build_world_server(WorldConfig())
    rows = make_client(server).get("/family/households").json()
    assert rows, "genesis should have households"
    row = rows[0]
    members = [server.world.agents[m] for m in row["member_ids"] if m in server.world.agents]
    bill = compute_household_bill(row["household_id"], 0, members)
    assert row["daily_bill_sim"] == bill.total_sim
    assert row["earners"] + row["dependents"] == len(bill.earner_ids) + len(bill.dependent_ids)


@pytest.mark.asyncio
async def test_solvency_is_the_combined_balance_because_the_household_is_billed_as_one():
    server = await build_world_server(WorldConfig())
    row = make_client(server).get("/family/households").json()[0]
    expected = sum(
        server.world.ledger.accounts[m].sim_units
        for m in row["member_ids"]
        if m in server.world.ledger.accounts and server.world.agents[m].alive
    )
    assert row["combined_sim"] == expected


# -- 19.3 -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_watchlist_floats_the_closest_to_the_edge_to_the_top():
    server = await build_world_server(WorldConfig())
    # drain one household so it is genuinely the worst off
    doomed = next(iter(server.world.households.values()))
    for member in doomed.member_ids:
        account = server.world.ledger.accounts.get(member)
        if account is not None:
            account.sim_units = 0
    rows = make_client(server).get("/family/at-risk").json()
    assert rows[0]["household_id"] == doomed.household_id
    assert rows[0]["days_of_runway"] == 0
    assert [r["days_of_runway"] for r in rows] == sorted(r["days_of_runway"] for r in rows)


@pytest.mark.asyncio
async def test_the_watchlist_can_be_limited():
    server = await build_world_server(WorldConfig())
    assert len(make_client(server).get("/family/at-risk?limit=3").json()) == 3


# -- 19.4 -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_wealth_board_ranks_richest_and_poorest():
    server = await build_world_server(WorldConfig())
    board = make_client(server).get("/family/wealth?top=3").json()
    assert len(board["richest"]) == 3
    assert board["richest"][0]["amount_sim"] >= board["richest"][-1]["amount_sim"]
    assert board["poorest"][0]["amount_sim"] <= board["richest"][0]["amount_sim"]


@pytest.mark.asyncio
async def test_a_real_transfer_shows_up_as_a_giver_and_an_earner():
    server = await build_world_server(WorldConfig())
    giver, taker = list(server.world.agents)[:2]
    await server.world.ledger.move(
        from_account_id=giver, to_account_id=taker, amount=1_000,
        currency=Currency.SIM, reason=TransactionReason.TRANSFER, tick=1,
    )
    board = make_client(server).get("/family/wealth?top=1").json()
    assert board["biggest_givers"][0]["subject_id"] == giver
    assert board["biggest_earners"][0]["subject_id"] == taker


# -- 19.5 -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_family_tree_carries_a_live_balance_on_every_node():
    server = await build_world_server(WorldConfig())
    parented = next(
        (a for a in server.world.agents.values() if a.parent_ids), None
    )
    if parented is None:
        pytest.skip("this genesis produced no agent with parents on file")
    tree = make_client(server).get(f"/family/tree/{parented.agent_id}").json()
    assert tree["root"]["agent_id"] == parented.agent_id
    assert tree["root"]["sim_balance"] == server.world.ledger.accounts[parented.agent_id].sim_units
    for node in tree["parents"]:
        assert "sim_balance" in node and "days_of_runway" in node


@pytest.mark.asyncio
async def test_siblings_are_derived_not_stored():
    """Deriving beats a second field that can drift out of sync."""
    server = await build_world_server(WorldConfig())
    by_parents: dict[tuple, list[str]] = {}
    for a in server.world.agents.values():
        if a.parent_ids:
            by_parents.setdefault(tuple(sorted(a.parent_ids)), []).append(a.agent_id)
    pair = next((v for v in by_parents.values() if len(v) >= 2), None)
    if pair is None:
        pytest.skip("this genesis produced no two agents sharing parents")
    tree = make_client(server).get(f"/family/tree/{pair[0]}").json()
    assert pair[1] in [n["agent_id"] for n in tree["siblings"]]


@pytest.mark.asyncio
async def test_an_unknown_agent_is_a_404_not_a_500():
    server = await build_world_server(WorldConfig())
    assert make_client(server).get("/family/tree/nobody").status_code == 404


# -- 19.6 -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_money_flow_aggregates_the_ledgers_own_transactions():
    server = await build_world_server(WorldConfig())
    a, b = list(server.world.agents)[:2]
    await server.world.ledger.move(
        from_account_id=a, to_account_id=b, amount=250,
        currency=Currency.SIM, reason=TransactionReason.TRANSFER, tick=5,
    )
    await server.world.ledger.move(
        from_account_id=b, to_account_id=TREASURY_ACCOUNT_ID, amount=100,
        currency=Currency.SIM, reason=TransactionReason.TAX, tick=6,
    )
    flow = make_client(server).get("/family/money-flow?since_tick=5").json()
    assert flow["transactions"] == 2
    assert flow["total_moved_sim"] == 350
    assert flow["into_treasury_sim"] == 100
    assert flow["between_agents_sim"] == 250
    reasons = {e["reason"]: e["total_sim"] for e in flow["by_reason"]}
    assert reasons["transfer"] == 250 and reasons["tax"] == 100


@pytest.mark.asyncio
async def test_money_flow_since_a_tick_excludes_what_came_before():
    server = await build_world_server(WorldConfig())
    a, b = list(server.world.agents)[:2]
    await server.world.ledger.move(
        from_account_id=a, to_account_id=b, amount=10,
        currency=Currency.SIM, reason=TransactionReason.TRANSFER, tick=1,
    )
    assert money_flow(server.world, since_tick=99).transactions == 0


@pytest.mark.asyncio
async def test_every_route_is_a_window_never_an_authority():
    """DESIGN.md section 8: viewers "are windows, never authorities"."""
    server = await build_world_server(WorldConfig())
    router = build_family_router(lambda: FamilyDeps(world=server.world, genesis=server.genesis))
    methods = {m for route in router.routes for m in route.methods}
    assert methods == {"GET"}


@pytest.mark.asyncio
async def test_a_member_with_no_ledger_account_does_not_500_the_view():
    """A resident who arrived some other way - a birth, mid-run - must not
    take the whole view down."""
    server = await build_world_server(WorldConfig())
    server.world.households["hh-new"] = Household(household_id="hh-new", member_ids=["ghost"])
    rows = {r["household_id"]: r for r in make_client(server).get("/family/households").json()}
    assert rows["hh-new"]["combined_sim"] == 0
    assert rows["hh-new"]["status"] == "destitute"
