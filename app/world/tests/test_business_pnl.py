"""B4: business profit and loss over time (The 250 section 23).

"Visible profit/loss over time, so a failing business is something the town
can see coming, not just a business that silently vanishes."

A new query over rows `buy`/`sell`/`hire_employee` already write - not a new
ledger. Proven against the real wired world.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from world.agents_api import AgentsDeps, build_agents_router, classify_leg
from world.ledger.models import Currency, TransactionReason
from world.tools.models import Business
from world.wiring import WorldConfig, build_world_server
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map

TICKS_PER_DAY = 144


def make_client(server) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_agents_router(lambda: AgentsDeps(world=server.world, genesis=server.genesis))
    )
    return TestClient(app)


async def open_shop(server, business_id="biz-1", starting_sim=0) -> str:
    owner = next(iter(server.world.agents))
    await server.world.ledger.register_business(business_id, owner, fee_sim=100, tick=0)
    if starting_sim:
        await server.world.ledger.mint_sim(business_id, starting_sim, tick=0)
    server.world.businesses[business_id] = Business(
        business_id=business_id, name="Kasukabe Grocery", business_type="grocery",
        owner_agent_id=owner, location_id="market",
    )
    return owner


async def sale(server, business_id, buyer, amount, tick):
    await server.world.ledger.move(
        from_account_id=buyer, to_account_id=business_id, amount=amount,
        currency=Currency.SIM, reason=TransactionReason.EXCHANGE, tick=tick,
    )


async def payroll(server, business_id, worker, amount, tick):
    await server.world.ledger.move(
        from_account_id=business_id, to_account_id=worker, amount=amount,
        currency=Currency.SIM, reason=TransactionReason.WAGE, tick=tick,
    )


def test_direction_alone_decides_which_column_a_leg_lands_in():
    """A Transaction is one two-party leg, so the business is on exactly one
    side of it - no reason-guessing needed for the sign."""
    class T:
        from_account, to_account, amount = "buyer", "biz-1", 300
    assert classify_leg("biz-1", T()) == (300, 0)

    class U:
        from_account, to_account, amount = "biz-1", "worker", 120
    assert classify_leg("biz-1", U()) == (0, 120)


def test_a_transaction_the_business_is_not_party_to_is_neither():
    class T:
        from_account, to_account, amount = "alice", "bob", 50
    assert classify_leg("biz-1", T()) == (0, 0)


@pytest.mark.asyncio
async def test_revenue_costs_and_profit_come_out_of_the_real_ledger():
    server = await build_world_server(WorldConfig())
    owner = await open_shop(server)
    buyer, worker = list(server.world.agents)[1:3]
    await sale(server, "biz-1", buyer, 900, tick=1)
    await payroll(server, "biz-1", worker, 400, tick=2)

    row = make_client(server).get("/businesses/biz-1/pnl").json()
    assert row["revenue_sim"] == 900
    assert row["costs_sim"] == 400
    assert row["profit_sim"] == row["revenue_sim"] - row["costs_sim"]
    assert row["wages_paid_sim"] == 400
    assert row["owner_agent_id"] == owner
    assert row["owner_name"] != owner, "a display name, not an id"


@pytest.mark.asyncio
async def test_the_books_are_broken_out_over_time_not_just_a_total():
    """"Profit/loss OVER TIME" - one total cannot show a business dying."""
    server = await build_world_server(WorldConfig())
    await open_shop(server)
    buyer = list(server.world.agents)[1]
    await sale(server, "biz-1", buyer, 500, tick=1)
    await sale(server, "biz-1", buyer, 100, tick=TICKS_PER_DAY + 1)
    row = make_client(server).get("/businesses/biz-1/pnl").json()
    days = {p["day"]: p for p in row["by_day"]}
    assert days[0]["revenue_sim"] == 500
    assert days[1]["revenue_sim"] == 100


@pytest.mark.asyncio
async def test_a_failing_business_reads_as_declining_before_it_vanishes():
    server = await build_world_server(WorldConfig())
    await open_shop(server, starting_sim=5_000)
    buyer, worker = list(server.world.agents)[1:3]
    await sale(server, "biz-1", buyer, 900, tick=1)
    await payroll(server, "biz-1", worker, 100, tick=2)
    await sale(server, "biz-1", buyer, 50, tick=TICKS_PER_DAY + 1)
    await payroll(server, "biz-1", worker, 700, tick=TICKS_PER_DAY + 2)
    row = make_client(server).get("/businesses/biz-1/pnl").json()
    assert row["trend"] == "declining"


@pytest.mark.asyncio
async def test_a_business_that_cannot_make_payroll_says_so():
    """The question the books actually have to answer."""
    server = await build_world_server(WorldConfig())
    await open_shop(server, starting_sim=200)
    worker = list(server.world.agents)[1]
    await payroll(server, "biz-1", worker, 150, tick=1)
    row = make_client(server).get("/businesses/biz-1/pnl").json()
    assert row["trend"] == "cannot make payroll"
    assert row["days_of_wages_covered"] <= 1


@pytest.mark.asyncio
async def test_an_insolvent_business_is_called_insolvent():
    server = await build_world_server(WorldConfig())
    await open_shop(server)
    server.world.ledger.accounts["biz-1"].sim_units = 0
    row = make_client(server).get("/businesses/biz-1/pnl").json()
    assert row["trend"] == "insolvent"


@pytest.mark.asyncio
async def test_a_brand_new_business_does_not_claim_a_trend_it_cannot_know():
    server = await build_world_server(WorldConfig())
    await open_shop(server, starting_sim=1_000)
    row = make_client(server).get("/businesses/biz-1/pnl").json()
    assert row["trend"] == "too early to say"


@pytest.mark.asyncio
async def test_the_town_wide_list_puts_the_worst_first():
    server = await build_world_server(WorldConfig())
    await open_shop(server, "biz-good", starting_sim=5_000)
    await open_shop(server, "biz-bad", starting_sim=5_000)
    buyer, worker = list(server.world.agents)[1:3]
    await sale(server, "biz-good", buyer, 5_000, tick=1)
    await payroll(server, "biz-bad", worker, 4_000, tick=1)
    rows = make_client(server).get("/businesses/pnl").json()
    assert rows[0]["business_id"] == "biz-bad"
    assert rows[0]["profit_sim"] < rows[-1]["profit_sim"]


@pytest.mark.asyncio
async def test_since_tick_windows_the_books():
    server = await build_world_server(WorldConfig())
    await open_shop(server)
    buyer = list(server.world.agents)[1]
    await sale(server, "biz-1", buyer, 900, tick=1)
    row = make_client(server).get("/businesses/biz-1/pnl?since_tick=500").json()
    assert row["revenue_sim"] == 0


@pytest.mark.asyncio
async def test_an_unknown_business_is_a_404_not_a_500():
    server = await build_world_server(WorldConfig())
    assert make_client(server).get("/businesses/nope/pnl").status_code == 404


@pytest.mark.asyncio
async def test_it_records_nothing_and_mutates_nothing():
    """A new query, not a new ledger."""
    server = await build_world_server(WorldConfig())
    await open_shop(server, starting_sim=1_000)
    before = len(server.world.ledger.transactions)
    balance = server.world.ledger.accounts["biz-1"].sim_units
    make_client(server).get("/businesses/pnl")
    make_client(server).get("/businesses/biz-1/pnl")
    assert len(server.world.ledger.transactions) == before
    assert server.world.ledger.accounts["biz-1"].sim_units == balance
