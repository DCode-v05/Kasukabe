"""The treasury fallback for learning was in the wrong place.

86789dc let the treasury pay for a broke resident's learning and memory
calls, at `Ledger.burn_for_inference` - the debit the broker makes AFTER a
model call. But the 50-resident run's 58 failures are recorded at `0/0`
tokens and `$0.000000`: they never reached the model. The broker refuses
BEFORE the call (`broker/service.py`, the payer-balance check), comparing
the resident's own wallet against a worst-case estimate, and a broke
resident's learning call died there - so the fallback downstream never ran
and the 58 refusals would have happened exactly as before.

The decision now lives in one place, `Ledger.payer_for`, and both checks ask
it: the pre-call balance route and the post-call debit. The broker passes
what the call is FOR and what it may cost; the world answers who pays. The
broker still never learns what a treasury is, and the world still never
learns what a lane is.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from world.ledger.money import TREASURY_ACCOUNT_ID
from world.ledger.tests.ledger_factories import build_genesis_ledger

LEARNING = "auto:learning_centre.knowledge_extraction"
FLUSH = "auto:summarizer.working_memory"
A_TURN = "auto:agent_turn"


# ── the ledger's one answer ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_ledger_names_the_treasury_for_a_broke_residents_learning() -> None:
    ledger = await build_genesis_ledger(["n01"])
    ledger.accounts["n01"].usd_cents = 0
    ledger.accounts[TREASURY_ACCOUNT_ID].usd_cents += 200

    assert ledger.payer_for("n01", 5, LEARNING) == TREASURY_ACCOUNT_ID
    assert ledger.payer_for("n01", 5, FLUSH) == TREASURY_ACCOUNT_ID


@pytest.mark.asyncio
async def test_thinking_is_still_the_residents_own() -> None:
    ledger = await build_genesis_ledger(["n01"])
    ledger.accounts["n01"].usd_cents = 0

    assert ledger.payer_for("n01", 5, A_TURN) == "n01"
    assert ledger.payer_for("n01", 5, "") == "n01"


@pytest.mark.asyncio
async def test_a_resident_who_can_pay_is_its_own_payer_for_learning_too() -> None:
    ledger = await build_genesis_ledger(["n01"])

    assert ledger.payer_for("n01", 5, LEARNING) == "n01"


# ── the route the broker asks before every call ───────────────────────


def _client_for(ledger):
    """The world's own `/ledger/payer` route over a real ledger."""
    import world.main as main

    class _World:
        pass

    class _Server:
        pass

    server = _Server()
    server.world = _World()
    server.world.ledger = ledger
    main.app.dependency_overrides = {}
    original = main.server_dep
    main.server_dep = lambda: server
    return TestClient(main.app), (main, original)


@pytest.mark.asyncio
async def test_the_pre_call_check_sees_the_treasury_for_learning() -> None:
    """The whole finding: the broker's balance check, asked about a broke
    resident's learning call, must see money it can spend."""
    ledger = await build_genesis_ledger(["n01"])
    ledger.accounts["n01"].usd_cents = 0
    client, (main, original) = _client_for(ledger)
    try:
        body = client.get("/ledger/payer/n01",
                          params={"purpose": LEARNING, "amount_cents": 5}).json()
    finally:
        main.server_dep = original

    assert body["payer_agent_id"] == TREASURY_ACCOUNT_ID
    assert body["balance_cents"] >= 5, "the check would refuse the call exactly as before"
    assert body["agent_id"] == "n01", "the call is still the resident's"


@pytest.mark.asyncio
async def test_the_pre_call_check_still_refuses_a_broke_residents_turn() -> None:
    ledger = await build_genesis_ledger(["n01"])
    ledger.accounts["n01"].usd_cents = 0
    client, (main, original) = _client_for(ledger)
    try:
        body = client.get("/ledger/payer/n01",
                          params={"purpose": A_TURN, "amount_cents": 5}).json()
    finally:
        main.server_dep = original

    assert body["payer_agent_id"] == "n01"
    assert body["balance_cents"] == 0


@pytest.mark.asyncio
async def test_a_caller_that_says_nothing_gets_the_old_answer() -> None:
    """An older broker, or anything else calling this route, asks without a
    purpose and must get exactly what it always got."""
    ledger = await build_genesis_ledger(["n01"])
    ledger.accounts["n01"].usd_cents = 0
    client, (main, original) = _client_for(ledger)
    try:
        body = client.get("/ledger/payer/n01").json()
    finally:
        main.server_dep = original

    assert body["payer_agent_id"] == "n01"


def test_the_embedded_world_serves_the_same_answer() -> None:
    """There are two copies of this route - `world/main.py` and the
    embedded twin inside `run_genesis.py` - and every run uses the twin. A
    fix in one alone is how 86789dc came to fix nothing."""
    import inspect

    import world.run_genesis as rg

    source = inspect.getsource(rg)
    twin = source[source.index('@app.get("/ledger/payer/{agent_id}")'):]
    twin = twin[:twin.index("@app.", 10)]
    assert "payer_for" in twin


# ── the broker asks the question ──────────────────────────────────────


@pytest.mark.asyncio
async def test_the_broker_says_what_the_call_is_for_when_it_asks_who_pays() -> None:
    """The HTTP client carries purpose and estimate to the route above."""
    import httpx

    from broker.ledger import HttpLedgerClient

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={
            "agent_id": "n01", "payer_agent_id": TREASURY_ACCOUNT_ID,
            "balance_cents": 400, "is_minor": False, "tick": 3,
        })

    client = HttpLedgerClient.__new__(HttpLedgerClient)
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                      base_url="http://world")
    balance = await client.get_payer_balance("n01", purpose=LEARNING, amount_cents=5)
    await client.client.aclose()

    assert seen.get("purpose") == LEARNING
    assert seen.get("amount_cents") == "5"
    assert balance.payer_agent_id == TREASURY_ACCOUNT_ID


def test_the_service_passes_the_purpose_and_the_estimate() -> None:
    """And the broker's own pre-call check is the caller."""
    import inspect

    from broker import service

    source = inspect.getsource(service)
    call = source[source.index("self.ledger.get_payer_balance("):]
    call = call[:call.index(")") + 1]
    assert "purpose=" in call and "amount_cents=" in call, call
