"""boot_sim_engine: the lean sim boot sequence's structural guarantees.

No LLM call happens during boot itself (Provider construction only opens an
httpx.AsyncClient - it does not connect until a request is sent), so most of
these tests never need the fake broker to serve anything; it is still
installed so a bug that DID make boot call out would fail loudly (an
unscripted request) instead of hanging on a real socket.
"""

from __future__ import annotations

import httpx
import pytest

from src.config import ConfigError
from src.herald import catalog_sync
from src.sim.config import FORK_DENIED_TOOLS, SimProfile, pinned_thread_id
from src.sim.model_profile import MODEL_ID
from src.sim.pinned_boot import boot_sim_engine
from src.sim.world_tools import WORLD_TOOL_ALLOW_RULE, WorldClient

import json

from tests.sim_fake_citadel import FakeBroker, install_fake_broker, text_turn_sse, tool_use_turn_sse


def empty_world_client() -> WorldClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry":
            return httpx.Response(200, json=[])
        if request.url.path == "/tools/registry/version":
            return httpx.Response(200, json={"version": 0})
        raise AssertionError(f"unexpected world call: {request.url.path}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WorldClient("http://world.test", agent_id="agent-boot", client=client)


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    b = FakeBroker()
    install_fake_broker(monkeypatch, b)
    return b


@pytest.mark.asyncio
async def test_boot_raises_config_error_when_validation_fails(tmp_path, monkeypatch, broker):
    """boot_sim_engine propagates validate_sim_settings failures as a single
    aggregated ConfigError before touching sqlite or the broker.

    build_settings() is self-healing for FORK_SUBAGENT (see
    tests/test_sim_config.py's test_build_settings_forces_fork_subagent_false_
    even_if_ambient_env_disagrees), so a bad profile can no longer produce a
    naturally-failing Settings through the front door - this test instead
    proves the wiring itself: boot_sim_engine calls validate_sim_settings and
    turns a non-empty result into a raised, aggregated ConfigError.
    """
    monkeypatch.setattr(
        "src.sim.pinned_boot.validate_sim_settings",
        lambda settings: ["synthetic validation failure for this test"],
    )
    profile = SimProfile(
        agent_id="agent-boot",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    with pytest.raises(ConfigError) as ei:
        await boot_sim_engine(profile, "http://world.test", world_client=empty_world_client())
    assert ei.value.errors == ["synthetic validation failure for this test"]
    assert broker.requested_models == []


@pytest.mark.asyncio
async def test_boot_wires_a_coordinator_with_no_router(tmp_path, monkeypatch, broker):
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    profile = SimProfile(
        agent_id="agent-boot",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    engine = await boot_sim_engine(profile, "http://world.test", world_client=empty_world_client())
    try:
        assert engine.coordinator.router is None
        assert engine.coordinator.is_thread_pinned(engine.thread_id) is True
        assert engine.thread_id in engine.coordinator.threads
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_boot_installs_the_world_allow_rule_and_fork_deny_rules(tmp_path, monkeypatch, broker):
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    profile = SimProfile(
        agent_id="agent-boot",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    engine = await boot_sim_engine(profile, "http://world.test", world_client=empty_world_client())
    try:
        assert WORLD_TOOL_ALLOW_RULE in engine.thread.session_rules
        denied = {r.tool for r in engine.thread.deny_rules}
        assert denied == set(FORK_DENIED_TOOLS)
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_boot_registers_exactly_one_model_in_the_catalog(tmp_path, monkeypatch, broker):
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    catalog_sync.runtime_overlay.clear()
    profile = SimProfile(
        agent_id="agent-boot",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    engine = await boot_sim_engine(profile, "http://world.test", world_client=empty_world_client())
    try:
        assert set(catalog_sync.get_catalog_overlay().keys()) == {MODEL_ID}
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_boot_creates_exactly_one_thread_for_this_engine(tmp_path, monkeypatch, broker):
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    profile = SimProfile(
        agent_id="agent-boot",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    engine = await boot_sim_engine(profile, "http://world.test", world_client=empty_world_client())
    try:
        assert list(engine.coordinator.threads.keys()) == [engine.thread_id]
        assert engine.thread_id == pinned_thread_id("agent-boot")
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_boot_never_makes_a_single_broker_request(tmp_path, monkeypatch, broker):
    """Boot only constructs adapters; it never sends a turn. If this ever
    changes, the FakeBroker's empty response queues make the change loud
    (AssertionError) instead of silently passing."""
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    profile = SimProfile(
        agent_id="agent-boot",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    engine = await boot_sim_engine(profile, "http://world.test", world_client=empty_world_client())
    try:
        assert broker.requested_models == []
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_boot_delivers_this_agents_persona_as_the_system_prompt(tmp_path, monkeypatch, broker):
    """RC-1 (ROADMAP.md section 1.2, "the agents ran with no system prompt
    at all"): without a consciousness_mgr, thread.py left sys_prompt=None
    for every turn. Proves the fix at the wire, not just at construction -
    a persona.txt written to this agent's SEPTEMBER_HOME (the exact
    contract world/run_genesis.py's start_agents() writes to before
    spawning the real engine process) reaches the actual /v1/messages
    request's `system` field the model sees.
    """
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    persona_text = "You are Priya Corrigan, a careful haggler who trusts no one at first."
    agent_home = tmp_path / "agent-boot"
    agent_home.mkdir(parents=True)
    (agent_home / "persona.txt").write_text(persona_text, encoding="utf-8")

    profile = SimProfile(
        agent_id="agent-boot",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    engine = await boot_sim_engine(profile, "http://world.test", world_client=empty_world_client())
    try:
        broker.queue(MODEL_ID, "sse", text_turn_sse("I am here."))
        broker.queue(MODEL_ID, "sse", text_turn_sse("I am here."))
        reply = await engine.send_tick("It is morning in Kasukabe.")
        assert reply == "I am here."

        assert broker.request_bodies, "expected at least one /v1/messages request"
        assert any(
            persona_text in " ".join(block.get("text", "") for block in body.get("system", []))
            for body in broker.request_bodies
        )
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_session_log_records_a_plain_turn_with_all_four_fields(tmp_path, monkeypatch, broker):
    """The 250 section 32: every turn gets one JSON line with input,
    reasoning, actions, done_status - "irrespective of what the agent is
    doing." A plain text-only turn (no tool call) still produces a
    complete record.
    """
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    profile = SimProfile(
        agent_id="agent-boot",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    engine = await boot_sim_engine(profile, "http://world.test", world_client=empty_world_client())
    try:
        broker.queue(MODEL_ID, "sse", text_turn_sse("I see grain in the field."))
        reply = await engine.send_tick("It is morning. What do you see?")
        assert reply == "I see grain in the field."

        log_path = tmp_path / "agent-boot" / "session_log.jsonl"
        assert log_path.exists(), "expected session_log.jsonl to be written after one turn"
        lines = [line for line in log_path.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["agent_id"] == "agent-boot"
        assert entry["input"] == "It is morning. What do you see?"
        assert entry["actions"] == []
        assert entry["done_status"]["status"]
        assert "recorded_at" in entry
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_session_log_records_a_tool_call_and_the_recall_tool_reads_it_back(
    tmp_path, monkeypatch, broker
):
    """Proves both halves of section 32.3 in one real run: a tool call
    gets recorded in `actions`, and the SAME agent calling its own
    `recall_session_log` tool on a later turn actually gets back real
    text describing what it did earlier - not a stub, the genuine
    recorded history.
    """
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    profile = SimProfile(
        agent_id="agent-boot",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    engine = await boot_sim_engine(profile, "http://world.test", world_client=empty_world_client())
    try:
        broker.queue(MODEL_ID, "sse", text_turn_sse("Heading to the market."))
        await engine.send_tick("It is 8am. What do you do?")

        broker.queue(MODEL_ID, "sse", tool_use_turn_sse("recall_session_log", {"turns": 5}))
        broker.queue(MODEL_ID, "sse", text_turn_sse("Right, I was heading to the market."))
        reply = await engine.send_tick("You pause for a moment. What were you doing?")
        assert reply == "Right, I was heading to the market."

        log_path = tmp_path / "agent-boot" / "session_log.jsonl"
        lines = [line for line in log_path.read_text().splitlines() if line.strip()]
        assert len(lines) == 2, "expected one record per turn, including the tool-calling turn"

        turn2 = json.loads(lines[1])
        assert turn2["input"] == "You pause for a moment. What were you doing?"
        assert len(turn2["actions"]) == 1
        action = turn2["actions"][0]
        assert action["tool"] == "recall_session_log"
        assert action["params"] == {"turns": 5}
        assert "It is 8am. What do you do?" in action["result"]
    finally:
        await engine.shutdown()
