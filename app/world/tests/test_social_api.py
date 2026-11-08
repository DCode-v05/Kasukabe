"""Proves world/social_api.py against the real SQLite trace store
(build_world_server's own TraceStore, not a mock): scenario events flow
through the real EventBus -> TraceStore.record_event persistence path
before this router ever reads them back, and conversation rows are
inserted through the real SupabaseClient.insert() this module reads with
- the same client world/ and broker/ would use, proving the
select()/filter/order contract this router depends on actually holds
against the real backend.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from world.social_api import SocialDeps, build_social_router
from world.wiring import WorldConfig, build_world_server
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map


def make_client(server) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_social_router(
            lambda: SocialDeps(world=server.world, genesis=server.genesis, trace=server.trace_store.client)
        )
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_scenarios_feed_shows_a_real_disaster_event() -> None:
    server = await build_world_server(WorldConfig())
    try:
        event = await server.god_disaster_plague(affected_fraction=1.0)
        assert event.type == "health.disaster.plague"

        client = make_client(server)
        resp = client.get("/scenarios", params={"category": "disaster"})
        assert resp.status_code == 200
        entries = resp.json()
        assert any(e["type"] == "health.disaster.plague" for e in entries)
        matched = next(e for e in entries if e["type"] == "health.disaster.plague")
        assert matched["category"] == "disaster"
        assert matched["payload"]["affected_agent_ids"]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_scenario_trace_correlates_events_sharing_a_case_id() -> None:
    server = await build_world_server(WorldConfig())
    try:
        accused = next(iter(server.world.agents))
        await server.event_bus.emit(
            "crime.case.opened", tick=10, payload={"case_id": "case-xyz", "accused_agent_id": accused}
        )
        await server.event_bus.emit(
            "crime.warrant.issued", tick=20, payload={"case_id": "case-xyz", "warrant_id": "warrant-1"}
        )
        await server.event_bus.emit(
            "crime.case.opened", tick=15, payload={"case_id": "case-other", "accused_agent_id": accused}
        )

        client = make_client(server)
        feed = client.get("/scenarios", params={"category": "crime", "limit": 50}).json()
        anchor = next(e for e in feed if e["payload"].get("case_id") == "case-xyz" and e["type"] == "crime.case.opened")

        trace_resp = client.get(f"/scenarios/{anchor['event_id']}/trace")
        assert trace_resp.status_code == 200
        trace = trace_resp.json()
        types_and_ticks = [(e["type"], e["tick"]) for e in trace]
        assert types_and_ticks == [("crime.case.opened", 10), ("crime.warrant.issued", 20)]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_conversations_cluster_individual_and_group_from_real_rows() -> None:
    server = await build_world_server(WorldConfig())
    try:
        agent_ids = list(server.world.agents.keys())
        a, b, c, d, e = agent_ids[0], agent_ids[1], agent_ids[2], agent_ids[3], agent_ids[4]

        await server.trace_store.client.insert(
            "utterances",
            [
                {
                    "speaker_agent_id": a, "listener_agent_id": b, "conversation_id": "conv-1",
                    "location": "cafe", "content": "hello", "tick": 1,
                },
                {
                    "speaker_agent_id": b, "listener_agent_id": a, "conversation_id": "conv-1",
                    "location": "cafe", "content": "hi back", "tick": 2,
                },
            ],
        )
        await server.trace_store.client.insert(
            "utterances",
            [
                {
                    "speaker_agent_id": c, "listener_agent_id": d, "conversation_id": "conv-2",
                    "location": "town_hall", "content": "point one", "tick": 100,
                },
                {
                    "speaker_agent_id": c, "listener_agent_id": None, "conversation_id": "conv-3",
                    "location": "town_hall", "content": "point two, everyone", "tick": 101,
                },
                {
                    "speaker_agent_id": d, "listener_agent_id": None, "conversation_id": "conv-3",
                    "location": "town_hall", "content": "reply", "tick": 102,
                },
                {
                    "speaker_agent_id": e, "listener_agent_id": d, "conversation_id": "conv-3",
                    "location": "town_hall", "content": "second reply", "tick": 103,
                },
            ],
        )

        client = make_client(server)
        resp = client.get("/conversations")
        assert resp.status_code == 200
        summaries = resp.json()
        assert len(summaries) == 2

        individual = next(s for s in summaries if a in s["participant_ids"])
        assert individual["kind"] == "individual"
        assert individual["utterance_count"] == 2
        assert set(individual["participant_ids"]) == {a, b}

        group = next(s for s in summaries if c in s["participant_ids"])
        assert group["kind"] == "group"
        assert group["utterance_count"] == 4
        assert set(group["participant_ids"]) == {c, d, e}

        detail = client.get(f"/conversations/{group['conversation_cluster_id']}").json()
        assert len(detail["utterances"]) == 4
        assert [u["tick"] for u in detail["utterances"]] == [100, 101, 102, 103]

        place_filtered = client.get("/conversations", params={"place": "cafe"}).json()
        assert len(place_filtered) == 1
        assert place_filtered[0]["location"] == "cafe"

        kind_filtered = client.get("/conversations", params={"kind": "group"}).json()
        assert len(kind_filtered) == 1
    finally:
        await server.close()
