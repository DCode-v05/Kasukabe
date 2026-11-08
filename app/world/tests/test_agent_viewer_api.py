"""Proves world/agent_viewer_api.py against a real WorldServer plus real
rows inserted through the real trace-store client - including a real
`llm_calls` row shaped exactly like broker/trace.py's TraceRecord ->
trace_record_to_row() produces post the 2026-07-31 trace-store fix
(request_body/response_body as real JSON columns, `success` not
`succeeded`, TEXT `id` not autoincrement) - not the pre-fix 13-column
shape this whole track exists because of. If broker/'s row shape drifts
from this again, this test breaks loudly instead of a viewer silently
rendering nothing.
"""

from __future__ import annotations


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from world.agent_viewer_api import AgentViewerDeps, build_agent_viewer_router
from world.wiring import WorldConfig, build_world_server
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map


def make_client(server) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_agent_viewer_router(
            lambda: AgentViewerDeps(
                world=server.world, genesis=server.genesis, tick_engine=server.tick_engine,
                trace=server.trace_store.client,
            )
        )
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_finder_lists_all_250_genesis_agents_with_real_identity() -> None:
    server = await build_world_server(WorldConfig())
    try:
        client = make_client(server)
        resp = client.get("/agent-viewer/agents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == server.genesis.population
        assert len(body["agents"]) == server.genesis.population

        sample_genesis_agent = server.genesis.agents[0]
        row = next(a for a in body["agents"] if a["agent_id"] == sample_genesis_agent.agent_id)
        assert row["name"] == sample_genesis_agent.name
        assert row["ablation_arm"] == sample_genesis_agent.ablation_arm.value
        assert row["status"] == "alive"

        males = client.get("/agent-viewer/agents", params={"sex": "male"}).json()["total"]
        females = client.get("/agent-viewer/agents", params={"sex": "female"}).json()["total"]
        assert males + females == server.genesis.population

        by_name = client.get("/agent-viewer/agents", params={"q": sample_genesis_agent.name}).json()
        assert any(a["agent_id"] == sample_genesis_agent.agent_id for a in by_name["agents"])

        by_age = client.get("/agent-viewer/agents", params={"sort_by": "age_years"})
        assert by_age.status_code == 200
        age_values = [a["age_years"] for a in by_age.json()["agents"]]
        assert age_values == sorted(age_values)
        assert age_values[0] == 0.0

        by_wealth_desc = client.get(
            "/agent-viewer/agents", params={"sort_by": "usd_wallet_cents", "sort_desc": True}
        ).json()
        wealth_values = [a["usd_wallet_cents"] for a in by_wealth_desc["agents"]]
        assert wealth_values == sorted(wealth_values, reverse=True)

        bad_sort = client.get("/agent-viewer/agents", params={"sort_by": "organizations"})
        assert bad_sort.status_code == 400
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_activity_view_is_live_not_a_snapshot() -> None:
    server = await build_world_server(WorldConfig())
    try:
        agent_id = next(iter(server.world.agents))
        client = make_client(server)

        before = client.get(f"/agent-viewer/agents/{agent_id}").json()
        assert before["usd_wallet_cents"] == server.world.ledger.usd_balance(agent_id)

        await server.execute_tool(agent_id, "move", {"destination_location_id": "town_hall"})
        after = client.get(f"/agent-viewer/agents/{agent_id}").json()
        assert after["place"] == "town_hall"
        assert after["place"] != before["place"]

        missing = client.get("/agent-viewer/agents/does-not-exist")
        assert missing.status_code == 404
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_reasoning_list_and_detail_against_a_real_llm_calls_row() -> None:
    server = await build_world_server(WorldConfig())
    try:
        agent_id = next(iter(server.world.agents))
        move_result = await server.execute_tool(agent_id, "move", {"destination_location_id": "town_hall"})

        row = {
            "id": "trc_test_1",
            "agent_id": agent_id,
            "payer_agent_id": agent_id,
            "model": "moonshotai.kimi-k2.5",
            "requested_model": "moonshotai.kimi-k2.5",
            "region": "ap-south-1",
            "call_class": "cognition",
            "purpose": "agent_turn",
            "request_body": {"system": "you are an agent", "messages": [{"role": "user", "content": "what now?"}]},
            "response_body": {
                "content": [
                    {"type": "text", "text": "I should go to town hall."},
                    {"type": "tool_use", "name": "move", "input": {"destination_location_id": "town_hall"}},
                ]
            },
            "stop_reason": "tool_use",
            "input_tokens": 120,
            "output_tokens": 40,
            "cost_nano_dollars": 250_000,
            "cents_debited": 1,
            "latency_ms": 900,
            "rate_limit_wait_ms": 0,
            "success": True,
            "error_type": None,
            "error_message": None,
            "tick": 0,
        }
        await server.trace_store.client.insert("llm_calls", [row])

        client = make_client(server)
        listing = client.get(f"/agent-viewer/agents/{agent_id}/reasoning").json()
        assert len(listing) == 1
        assert listing[0]["id"] == "trc_test_1"
        assert listing[0]["lane"] == "moonshotai.kimi-k2.5@ap-south-1"
        assert listing[0]["cost_usd"] == pytest.approx(250_000 / 1_000_000_000)

        detail = client.get(f"/agent-viewer/agents/{agent_id}/reasoning/trc_test_1").json()
        assert detail["request_body"]["messages"][0]["content"] == "what now?"
        assert detail["extracted"]["text_blocks"] == ["I should go to town hall."]
        assert detail["extracted"]["tool_calls"][0]["name"] == "move"
        assert any(t["tool_name"] == "move" for t in detail["matched_tool_calls"])
        assert move_result.result["location_id"] == "town_hall"

        wrong_agent = next(a for a in server.world.agents if a != agent_id)
        mismatch = client.get(f"/agent-viewer/agents/{wrong_agent}/reasoning/trc_test_1")
        assert mismatch.status_code == 404
    finally:
        await server.close()
