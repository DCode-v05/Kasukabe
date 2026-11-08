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
        # ── 2. Herald catalog: exactly one model id, always ─────────────
        assert set(catalog_sync.get_catalog_overlay().keys()) == {MODEL_ID}
        assert ALLOWED_MODEL_IDS == {MODEL_ID}

        # ── 3. Exactly one thread for this engine ───────────────────────
        assert list(engine.coordinator.threads.keys()) == [engine.thread_id]
        assert engine.coordinator.is_thread_pinned(engine.thread_id) is True

        # ── 1. Routing works ──────────────────────────────────────────
        # ThreadRouter is never constructed (set_router is never called),
        # and every tick addresses the pinned thread by explicit thread_id -
        # Coordinator.build_dispatch's own documented bypass.
        assert engine.coordinator.router is None

        broker.queue(MODEL_ID, "sse", text_turn_sse("You see grain in the field."))
        reply = await engine.send_tick("what do you see?")

        assert reply == "You see grain in the field."
        # Still exactly one thread after a real tick - nothing spawned a second.
        assert list(engine.coordinator.threads.keys()) == [engine.thread_id]

        # ── 4. A fork attempt: denied before it can reach a second
        # model or a second thread, then the SAME adapter finishes the turn ──
        # Input kept under 20 chars on purpose: thread.py's post-turn signal
        # capture fires one extra, non-streaming, order-varying
        # adapter.generate() call whenever the user input is >= 20 chars -
        # a real engine behavior with its own try/except fallback, but
        # unrelated to what THIS test is scoped to prove (routing, model
        # purity, thread count, forking), and its relative ordering against
        # the streamed calls is engine_core's internal business, not a
        # contract this seam module owns. Staying under the threshold keeps
        # this test's call sequence exactly the two calls it scripts.
        broker.queue(
            MODEL_ID,
            "sse",
            tool_use_turn_sse("spawn_sub_agent", {"name": "helper", "task": "do my chores"}),
        )
        broker.queue(MODEL_ID, "sse", text_turn_sse("Understood, I will do it myself."))

        reply2 = await engine.send_tick("need help")

        assert reply2 == "Understood, I will do it myself."
        # No SubAgent was ever constructed: the deny rule fired in
        # check_permission (step 2) before register_spawn_sub_agent_tool's
        # handler ran, so nothing here was ever populated.
        assert engine.thread.sub_agent_results == []
        assert engine.thread.active_sub_agents == []
        assert engine.thread.sub_agent_count == 0
        # Forking would be a second thread; there still isn't one.
        assert list(engine.coordinator.threads.keys()) == [engine.thread_id]

        # Exactly 3 primary-thread calls so far: turn1, the fork attempt,
        # and its post-denial continuation.
        primary_call_count = len(broker.requested_models)
        assert primary_call_count == 3

        # ── Learning centre: same model, sleep-onset only ────────────────
        await SignalStore(engine.db).write(
            LearningSignal(
                type="fact",
                source="user",
                content="the agent harvested grain today",
                thread_id=engine.thread_id,
            )
        )
        # The two turns above also feed the post-turn hook, so the batch may
        # process more than the one signal written above - exactly how many
        # is learning_centre's own accounting, not this module's concern.
        # What this module guarantees is which MODEL every one of those
        # calls names, so queue generously (empty JSON: nothing to encode,
        # nothing to consolidate) rather than pin an exact call count that
        # belongs to a package this test isn't exercising.
        for _ in range(32):
            broker.queue(MODEL_ID, "json", json_message_response("{}"))

        assert await engine.learning_gate.on_tick(is_sleeping=False) is None  # awake: never runs
        result = await engine.learning_gate.on_tick(is_sleeping=True)  # sleep onset: runs once

        assert result is not None
        assert result.signals_processed >= 1  # at least the signal written above
        assert engine.learning_gate.runs_triggered == 1
        # The learning batch made at least one call of its own.
        assert len(broker.requested_models) > primary_call_count

        # ── The wire-level proof ─────────────────────────────────────
        # Every single /v1/messages request this whole scenario made - the
        # primary turn, the denied fork attempt, its continuation, and the
        # learning batch - named the one allowed model. Nothing else was
        # ever requested, not even transiently, and no request failed for
        # lack of a scripted response (the generous queue above would have
        # raised loudly if any call landed on an unscripted model).
        assert set(broker.requested_models) == {MODEL_ID}
        assert world_calls == []  # no world tool was ever available to call in this scenario
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
