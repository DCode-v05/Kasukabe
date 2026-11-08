"""Every fix of 17-18 September, driven through a REAL tick, not read as text.

Most of the tests written alongside those fixes check that a call exists -
`"market=" in inspect.getsource(...)`. That proves a line was typed, not that
anything arrives. 86789dc is the proof it matters: its tests passed, its
fallback was correct, and it fixed nothing, because the broker refused the
call one step earlier than the code it changed. So these drive the runner's
own `run_tick` with a gateway that captures what each resident is actually
SENT, and ask the world's own routes what they actually ANSWER.
"""

from __future__ import annotations

import re

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from world.call_budget import MAX_CALLS_PER_TICK
from world.ledger.money import TREASURY_ACCOUNT_ID
from world.run_genesis import GenesisRunner, GenesisRunnerConfig, TickOutcome
from world.tools.models import PricePost


class CapturingGateway:
    """Records the perception and call budget every resident is sent."""

    def __init__(self) -> None:
        self.sent: dict[str, dict] = {}

    async def send_tick(self, agent_id, *, tick, **kw) -> TickOutcome:
        self.sent[agent_id] = {"tick": tick, **kw}
        return TickOutcome(
            agent_id=agent_id, reply="ok", learning_ran=False, is_sleeping=False,
            input_tokens=0, output_tokens=0, cost_usd_cents=0, succeeded=True,
        )

    def __getattr__(self, name):
        async def _any(*a, **k):
            return None
        return _any


@pytest_asyncio.fixture
async def town(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "checkpoints",
        home_root=str(tmp_path / "homes"), world_mode="embedded", world_port=0,
        budget_cap_cents=40_000,
        start_at_dawn=True,
    ))
    await runner.boot(resume=False)
    runner.build_world_app()
    world = runner.world_state
    seller = sorted(world.agents)[0]
    world.price_board[seller] = {"potato": PricePost(item="potato", ask_sim=8)}
    world.inventory.credit(seller, "potato", 5)
    gateway = CapturingGateway()
    runner.gateway = gateway
    return runner, gateway, seller


async def _run_until_dispatched(runner, gateway, ticks: int = 30) -> None:
    for _ in range(ticks):
        await runner.run_tick()
        if gateway.sent:
            return


@pytest.mark.asyncio
async def test_a_resident_is_actually_shown_the_market(town) -> None:
    runner, gateway, seller = town

    await _run_until_dispatched(runner, gateway)

    assert gateway.sent, "no resident was dispatched in thirty ticks"
    perceptions = [s.get("perception") or "" for s in gateway.sent.values()]
    assert any("For sale in the town" in p and "potato" in p for p in perceptions), (
        "a stocked offer existed and no resident's perception mentioned it"
    )


@pytest.mark.asyncio
async def test_every_resident_is_sent_the_new_ceiling(town) -> None:
    runner, gateway, _ = town

    await _run_until_dispatched(runner, gateway)

    budgets = [s.get("call_budget") for s in gateway.sent.values()]
    assert budgets and all(b is not None for b in budgets), "the budget never left the runner"
    assert max(budgets) <= MAX_CALLS_PER_TICK == 6


@pytest.mark.asyncio
async def test_a_wrong_seller_refusal_names_the_right_one(town) -> None:
    runner, _, seller = town
    world = runner.world_state
    buyer = sorted(world.agents)[1]
    stranger = sorted(world.agents)[2]
    world.locations.place(buyer, world.location_of(stranger))

    outcome_error = None
    try:
        await runner.registry.dispatch(world, buyer, "buy", {
            "seller_id": stranger, "item": "potato", "quantity": 1,
        })
    except Exception as exc:  # noqa: BLE001 - we want the refusal text
        outcome_error = str(exc) + " " + str(getattr(exc, "hint", "") or "")

    assert outcome_error, "buying from someone who sells nothing was not refused"
    assert re.search(r"sells potato at \d+ sim", outcome_error), (
        f"the refusal did not say who does sell it: {outcome_error[:300]}"
    )


@pytest.mark.asyncio
async def test_the_broker_would_see_the_treasury_for_a_broke_residents_learning(town) -> None:
    """The route every run actually uses - the embedded twin - asked the
    question the broker asks before every call."""
    runner, _, _ = town
    resident = sorted(runner.world_state.agents)[3]
    runner.ledger.get_account(resident).usd_cents = 0
    client = TestClient(runner.world_app)

    learning = client.get(f"/ledger/payer/{resident}", params={
        "purpose": "auto:learning_centre.knowledge_extraction", "amount_cents": 3,
    }).json()
    turn = client.get(f"/ledger/payer/{resident}", params={
        "purpose": "auto:agent_turn", "amount_cents": 3,
    }).json()

    assert learning["payer_agent_id"] == TREASURY_ACCOUNT_ID and learning["balance_cents"] >= 3
    assert turn["payer_agent_id"] == resident and turn["balance_cents"] == 0


@pytest.mark.asyncio
async def test_the_route_an_engine_fetches_offers_buy_only_in_conversation(town) -> None:
    """`GET /tools/registry?agent_id=...` on the embedded app every run
    serves - the exact request an engine makes each tick. `buy` must be
    absent, then present once the resident is talking to somebody, and the
    ETag must change with it: an unchanged ETag is answered 304 and the
    engine would keep the old list."""
    runner, _, _ = town
    world = runner.world_state
    a, b = sorted(world.agents)[4], sorted(world.agents)[5]
    world.locations.place(a, world.location_of(b))
    client = TestClient(runner.world_app)

    before = client.get("/tools/registry", params={"agent_id": a})
    world.open_conversation(place=world.location_of(a), topic="", participants=[a, b], now=world.tick())
    after = client.get("/tools/registry", params={"agent_id": a})

    assert "buy" not in {row["name"] for row in before.json()}
    assert "buy" in {row["name"] for row in after.json()}
    assert before.headers.get("etag") != after.headers.get("etag"), (
        "the list changed and its ETag did not - an engine would be told 304 and keep the old one"
    )
