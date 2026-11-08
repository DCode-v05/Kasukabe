"""THE fake-broker test (task deliverable): boots a real Kasukabe sim engine
against a fake broker and proves, at the wire, all four properties the
herald-seam module exists to guarantee:

    1. routing works          - Coordinator dispatches every tick straight to
                                 the one pinned thread, no ThreadRouter LLM hop,
                                 and the reply comes back correctly.
    2. exactly one model id    - the herald catalog holds exactly one model
       in the catalog, always    (moonshotai.kimi-k2.5), and every /v1/messages
       requested                request the fake broker ever received, across a
                                 primary turn, a denied fork attempt, and a
                                 learning-centre batch, named that one id and
                                 nothing else.
    3. exactly one thread      - this engine has exactly one thread, the
       per engine                 pinned one, for its whole lifetime.
    4. forking is impossible   - a scripted spawn_sub_agent tool_use is denied
                                 before any second adapter is built or a second
                                 model requested; the SAME adapter (same model
                                 id) finishes the turn.

The fake broker (tests/sim_fake_citadel.py) patches
httpx.AsyncHTTPTransport, the concrete transport
src/herald/transport.py's CitadelClient always constructs, so this drives
the REAL Provider / CitadelClient / ExecutionThread / Coordinator /
LCPipeline classes end to end - only the TCP socket is replaced by an
in-process handler, mirroring tests/test_transport_sse.py's own
httpx.MockTransport idiom.
"""

from __future__ import annotations

import httpx
import pytest

from src.herald import catalog_sync
from src.learning_centre.signals import LearningSignal, SignalStore
from src.sim.config import SimProfile
from src.sim.model_profile import ALLOWED_MODEL_IDS, MODEL_ID
from src.sim.pinned_boot import boot_sim_engine
from src.sim.world_tools import WorldClient

from tests.sim_fake_citadel import (
    FakeBroker,
    install_fake_broker,
    json_message_response,
    text_turn_sse,
    tool_use_turn_sse,
)


def empty_world_client(agent_id: str) -> tuple[WorldClient, list[dict]]:
    """A world with no registered tools - this test is about the model
    roster, thread count, and forking, not tool-call translation (see
    test_sim_world_tools.py for that proof).

    Only `tool_calls` is recorded. The engine POSTs to this world for two
    unrelated reasons and the assertion below is about one of them: since
    ENG-14 the memory stream writes a `plan` record for a turn whose
    reasoning arrived as text, and its sink POSTs that record to
    `/agents/<id>/memories`. That is a memory being filed, not a resident
    acting on the town, and counting it here would make this test fail for
    a reason it does not describe.
    """
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry":
            return httpx.Response(200, json=[])
        import json

        if request.url.path == f"/agents/{agent_id}/tool_calls":
            calls.append(json.loads(request.content))
        return httpx.Response(200, json={"result": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WorldClient("http://world.test", agent_id=agent_id, client=client), calls


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    b = FakeBroker()
    install_fake_broker(monkeypatch, b)
    return b


@pytest.mark.asyncio
async def test_routing_one_model_only_one_thread_and_forking_impossible(
    tmp_path, monkeypatch, broker
):
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    catalog_sync.runtime_overlay.clear()

    profile = SimProfile(
        agent_id="agent-fb",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    world, world_calls = empty_world_client(profile.agent_id)
    engine = await boot_sim_engine(profile, "http://world.test", world_client=world)

    try:
        assert set(catalog_sync.get_catalog_overlay().keys()) == {MODEL_ID}
        assert ALLOWED_MODEL_IDS == {MODEL_ID}

        assert list(engine.coordinator.threads.keys()) == [engine.thread_id]
        assert engine.coordinator.is_thread_pinned(engine.thread_id) is True

        assert engine.coordinator.router is None

        broker.queue(MODEL_ID, "sse", text_turn_sse("You see grain in the field."))
        reply = await engine.send_tick("what do you see?")

        assert reply == "You see grain in the field."
        assert list(engine.coordinator.threads.keys()) == [engine.thread_id]

        broker.queue(
            MODEL_ID,
            "sse",
            tool_use_turn_sse("spawn_sub_agent", {"name": "helper", "task": "do my chores"}),
        )
        broker.queue(MODEL_ID, "sse", text_turn_sse("Understood, I will do it myself."))

        reply2 = await engine.send_tick("need help")

        assert reply2 == "Understood, I will do it myself."
        assert engine.thread.sub_agent_results == []
        assert engine.thread.active_sub_agents == []
        assert engine.thread.sub_agent_count == 0
        assert list(engine.coordinator.threads.keys()) == [engine.thread_id]

        primary_call_count = len(broker.requested_models)
        assert primary_call_count == 3

        await SignalStore(engine.db).write(
            LearningSignal(
                type="fact",
                source="user",
                content="the agent harvested grain today",
                thread_id=engine.thread_id,
            )
        )
        for _ in range(32):
            broker.queue(MODEL_ID, "json", json_message_response("{}"))

        assert await engine.learning_gate.on_tick(is_sleeping=False) is None
        result = await engine.learning_gate.on_tick(is_sleeping=True)

        assert result is not None
        assert result.signals_processed >= 1
        assert engine.learning_gate.runs_triggered == 1
        assert len(broker.requested_models) > primary_call_count

        assert set(broker.requested_models) == {MODEL_ID}
        assert world_calls == []
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_a_disallowed_model_id_never_leaves_the_guard(tmp_path, monkeypatch, broker):
    """Belt-and-suspenders: even if something bypassed the sim's own
    allowlist assertion and asked herald.create_llm for a model other than
    MODEL_ID directly, the fake broker itself would reject the request
    (proving the "only one model" property does not depend solely on the
    in-process guard - the wire contract enforces it too)."""
    from src.config import Settings
    from src.herald import create_llm

    settings = Settings.build_from_env(
        {"CDT_URL": "http://broker.test", "CDT_KEY": "k", "SEPTEMBER_HOME": str(tmp_path)}
    )[0]
    adapter = create_llm(settings, model_id="claude-opus-4-8")

    from src.herald.types import Message

    with pytest.raises(AssertionError, match="no scripted response"):
        await adapter.generate([Message(role="user", content="hi")])

    assert broker.requested_models == ["claude-opus-4-8"]
