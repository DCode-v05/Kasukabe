"""InMemoryLedgerClient: the fake used by every test and by MOCK_MODE=true,
including the minor-debits-parent resolution DESIGN.md section 4 requires.
HttpLedgerClient's wire contract is exercised via a mocked httpx transport
so the test never makes a real network call."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from broker.errors import InsufficientFundsError, UnknownAgentError
from broker.ledger import HttpLedgerClient, InMemoryLedgerClient


@pytest.mark.asyncio
async def test_get_payer_balance_for_adult_is_self() -> None:
    ledger = InMemoryLedgerClient()
    ledger.seed("agent-1", balance_cents=500)

    payer = await ledger.get_payer_balance("agent-1")

    assert payer.agent_id == "agent-1"
    assert payer.payer_agent_id == "agent-1"
    assert payer.balance_cents == 500
    assert payer.is_minor is False


@pytest.mark.asyncio
async def test_get_payer_balance_for_minor_resolves_to_parent() -> None:
    ledger = InMemoryLedgerClient()
    ledger.seed("parent-1", balance_cents=1000)
    ledger.seed("child-1", balance_cents=0, is_minor=True, parent_agent_id="parent-1")

    payer = await ledger.get_payer_balance("child-1")

    assert payer.agent_id == "child-1"
    assert payer.payer_agent_id == "parent-1"
    assert payer.balance_cents == 1000
    assert payer.is_minor is True


@pytest.mark.asyncio
async def test_get_payer_balance_unknown_agent_raises() -> None:
    ledger = InMemoryLedgerClient()
    with pytest.raises(UnknownAgentError):
        await ledger.get_payer_balance("ghost")


@pytest.mark.asyncio
async def test_debit_reduces_own_balance_for_an_adult() -> None:
    ledger = InMemoryLedgerClient()
    ledger.seed("agent-1", balance_cents=500)

    result = await ledger.debit("agent-1", 120, purpose="tick", metadata={})

    assert result.payer_agent_id == "agent-1"
    assert result.balance_cents_after == 380
    assert ledger.accounts["agent-1"].balance_cents == 380


@pytest.mark.asyncio
async def test_debit_for_a_minor_reduces_the_parents_balance() -> None:
    ledger = InMemoryLedgerClient()
    ledger.seed("parent-1", balance_cents=1000)
    ledger.seed("child-1", balance_cents=0, is_minor=True, parent_agent_id="parent-1")

    result = await ledger.debit("child-1", 250, purpose="tick", metadata={})

    assert result.payer_agent_id == "parent-1"
    assert result.balance_cents_after == 750
    assert ledger.accounts["parent-1"].balance_cents == 750
    assert ledger.accounts["child-1"].balance_cents == 0  # the minor's own balance is untouched


@pytest.mark.asyncio
async def test_debit_unknown_agent_raises() -> None:
    ledger = InMemoryLedgerClient()
    with pytest.raises(UnknownAgentError):
        await ledger.debit("ghost", 100, purpose="tick", metadata={})


@pytest.mark.asyncio
async def test_debit_enforces_a_non_negative_balance_floor() -> None:
    """Mirrors world/ledger/core.py's real apply_movement enforcement
    (confirmed while building this module - see ledger.py's module
    docstring): a debit that would take the payer negative is rejected,
    not silently allowed. broker/service.py is responsible for treating
    this as rare-but-real and never letting it discard an already-good
    inference response - see test_service.py's ledger-failure tests."""
    ledger = InMemoryLedgerClient()
    ledger.seed("agent-1", balance_cents=10)

    with pytest.raises(InsufficientFundsError):
        await ledger.debit("agent-1", 500, purpose="tick", metadata={})

    assert ledger.accounts["agent-1"].balance_cents == 10  # untouched, not partially applied


@pytest.mark.asyncio
async def test_debit_exactly_at_balance_succeeds() -> None:
    ledger = InMemoryLedgerClient()
    ledger.seed("agent-1", balance_cents=100)

    result = await ledger.debit("agent-1", 100, purpose="tick", metadata={})

    assert result.balance_cents_after == 0


@pytest.mark.asyncio
async def test_multiple_children_of_the_same_parent_share_one_lock() -> None:
    ledger = InMemoryLedgerClient()
    ledger.seed("parent-1", balance_cents=1000)
    ledger.seed("child-a", balance_cents=0, is_minor=True, parent_agent_id="parent-1")
    ledger.seed("child-b", balance_cents=0, is_minor=True, parent_agent_id="parent-1")

    assert ledger.lock_for("parent-1") is ledger.lock_for("parent-1")

    await ledger.debit("child-a", 100, purpose="tick", metadata={})
    await ledger.debit("child-b", 200, purpose="tick", metadata={})

    assert ledger.accounts["parent-1"].balance_cents == 700


# ── HttpLedgerClient: wire contract, no real network ────────────────────


def make_http_ledger_client(handler) -> HttpLedgerClient:
    mock_client = httpx.AsyncClient(
        base_url="http://world.invalid", transport=httpx.MockTransport(handler)
    )
    return HttpLedgerClient("http://world.invalid", client=mock_client)


@pytest.mark.asyncio
async def test_http_ledger_get_payer_balance_parses_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ledger/payer/agent-1"
        return httpx.Response(
            200,
            json={
                "agent_id": "agent-1",
                "payer_agent_id": "parent-1",
                "balance_cents": 999,
                "is_minor": True,
            },
        )

    client = make_http_ledger_client(handler)
    payer = await client.get_payer_balance("agent-1")

    assert payer.payer_agent_id == "parent-1"
    assert payer.balance_cents == 999
    await client.close()


@pytest.mark.asyncio
async def test_http_ledger_get_payer_balance_404_raises_unknown_agent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    client = make_http_ledger_client(handler)
    with pytest.raises(UnknownAgentError):
        await client.get_payer_balance("ghost")
    await client.close()


@pytest.mark.asyncio
async def test_http_ledger_debit_posts_expected_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ledger/debit"
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"payer_agent_id": "agent-1", "balance_cents_after": 42}
        )

    client = make_http_ledger_client(handler)
    result = await client.debit("agent-1", 58, purpose="tick", metadata={"model": "kimi"})

    assert result.balance_cents_after == 42
    assert captured["body"] == {
        "agent_id": "agent-1",
        "amount_cents": 58,
        "purpose": "tick",
        "metadata": {"model": "kimi"},
    }
    await client.close()


@pytest.mark.asyncio
async def test_http_ledger_debit_404_raises_unknown_agent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    client = make_http_ledger_client(handler)
    with pytest.raises(UnknownAgentError):
        await client.debit("ghost", 10, purpose="tick", metadata={})
    await client.close()


@pytest.mark.asyncio
async def test_http_ledger_debit_402_raises_insufficient_funds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text="payer cannot cover this debit")

    client = make_http_ledger_client(handler)
    with pytest.raises(InsufficientFundsError):
        await client.debit("agent-1", 500, purpose="tick", metadata={})
    await client.close()


# -- opt-in provisioning for MOCK_MODE local bring-up ------------------
#
# Found by booting real engines against a mock broker: every inference call
# returned 404 "agent 'a0001' is not known to the ledger", each engine
# turned that into a 500 from its own /tick, and a full 10-agent game day
# produced no agent activity whatsoever.


def test_provisioning_is_off_by_default() -> None:
    """The strict fake stays strict unless asked otherwise."""
    ledger = InMemoryLedgerClient()
    assert ledger.autoseed_balance_cents is None
    with pytest.raises(UnknownAgentError):
        asyncio.run(ledger.get_payer_balance("never-seeded"))


def test_provisions_on_first_balance_check() -> None:
    ledger = InMemoryLedgerClient(autoseed_balance_cents=200)
    balance = asyncio.run(ledger.get_payer_balance("a0001"))
    assert balance.balance_cents == 200
    assert balance.payer_agent_id == "a0001"
    assert balance.is_minor is False


def test_provisions_on_first_debit() -> None:
    ledger = InMemoryLedgerClient(autoseed_balance_cents=200)
    result = asyncio.run(ledger.debit("a0002", 50, "inference", {}))
    assert result.balance_cents_after == 150


def test_a_provisioned_wallet_still_runs_dry() -> None:
    """Provisioning creates an account ONCE. It does not top it up.

    This is the precise bound on the limitation: an individual agent can
    still go broke and InsufficientFundsError still fires, so the
    per-agent spend mechanics are real. What is NOT real is conservation -
    see the next test.
    """
    ledger = InMemoryLedgerClient(autoseed_balance_cents=200)
    asyncio.run(ledger.debit("a0003", 200, "inference", {}))
    with pytest.raises(InsufficientFundsError):
        asyncio.run(ledger.debit("a0003", 1, "inference", {}))


def test_provisioning_mints_money_and_breaks_conservation() -> None:
    """The actual reason this must never be on in a real run.

    world/ledger/core.py re-checks on every move() that the town's total
    USD is unchanged - DESIGN.md's $1000 invariant, "structurally
    unbreakable". A provisioning ledger breaks it by construction: each new
    agent conjures a fresh wallet from nothing, so the total grows with the
    population. Survival results - who ran out of money, and when - are
    therefore meaningless under MOCK_MODE and must come from a real run
    against HttpLedgerClient.
    """
    ledger = InMemoryLedgerClient(autoseed_balance_cents=200)
    for agent_id in ("a1", "a2", "a3", "a4", "a5"):
        asyncio.run(ledger.get_payer_balance(agent_id))
    total = sum(account.balance_cents for account in ledger.accounts.values())
    assert total == 1000, "five agents minted 1000 cents that no treasury paid for"


def test_build_service_honours_the_setting() -> None:
    """Wiring: the setting has to reach the constructed ledger."""
    from broker.app import build_service
    from broker.config import BrokerSettings

    strict = build_service(BrokerSettings(mock_mode=True, mock_ledger_balance_cents=0))
    assert strict.ledger.autoseed_balance_cents is None

    lenient = build_service(BrokerSettings(mock_mode=True, mock_ledger_balance_cents=200))
    assert lenient.ledger.autoseed_balance_cents == 200
