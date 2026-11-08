"""ENG-14: the memory stream listened to a channel this model never uses,
and attributed each tool result to the wrong call.

Two halves of one finding, from `docs/review/10-engine-seam-and-mind.md`.
First: `memory_stream.py:165-166` accumulated `ThinkingDelta` and nothing
else, while the broker emits a thinking delta only for a provider's
`reasoningContent` field (`translate.py:590-600`). kimi-k2.5 does not fill
that field - run 23 shows `</think>` arriving inside *text*
(`run-23-report.md:882,6949`) - so `reasoning_chunks` was empty on every
real turn, `_INTENT` and `_BACKWARD_LOOKING` had no string to match, and
of the module's three advertised record types only `observation` could
ever be written. The agent's memory panel showed a tool log. session_log.py
has had a `TextDelta` branch since it was written (`:130-131`), which is
exactly why session logs carry an agent's words and the memory stream did
not. Second: a call and its result were paired by tool NAME,
newest-unmatched-first (`:167-181`), which is the REVERSE of the order
results come back in - so two parallel `say` calls had their outcomes
swapped and a refusal was remembered against the neighbour who had
answered. `ToolExecution` carries no tool_use_id in either phase (see
`thread.py:4631,4937` - the result event carries neither the id nor even
the params), so the review's own fallback is order, scoped by the one
correlation field both phases do carry, `parallel_batch_id`.

The last test here is the wiring one and the one that matters most: it
boots a real engine, drives a real `send_tick`, and lets the frozen
engine's own emitter deliver the events. Every test above it would stay
green if `memory_stream.attach(emitter)` were deleted from `pinned_boot`.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.engine_core.events import (
    EventEmitter,
    ExecutionSummary,
    TextDelta,
    ThinkingDelta,
    ToolExecution,
)
from src.sim.memory_stream import MEMORY_STREAM_FILENAME, MemoryStreamWriter

TID = "thread-1"


def call(tool: str, batch: str = "", **params) -> ToolExecution:
    return ToolExecution(
        thread_id=TID, tool_name=tool, source="engine", phase="call",
        params=params, parallel_batch_id=batch,
    )


def result(tool: str, text: str, status: str = "success", batch: str = "") -> ToolExecution:
    return ToolExecution(
        thread_id=TID, tool_name=tool, source="engine", phase="result",
        result=text, status=status, parallel_batch_id=batch,
    )


def records_in_write_order(log_path: Path) -> list[dict]:
    """The file as written. `read_memory_stream` re-sorts by importance, and
    the order these were written in is the thing under test here."""
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture
def writer(tmp_path: Path):
    emitter = EventEmitter()
    w = MemoryStreamWriter("agent-1", tmp_path / MEMORY_STREAM_FILENAME)
    w.attach(emitter)
    return w, emitter


async def run_turn(writer, emitter, perception: str, events) -> None:
    writer.start_turn(perception)
    for event in events:
        await emitter.emit(event)
    await emitter.emit(ExecutionSummary(thread_id=TID, status="completed"))


@pytest.mark.asyncio
async def test_reasoning_that_arrives_as_text_still_becomes_a_plan(writer):
    """The finding itself. Nothing here emits a ThinkingDelta, because on
    kimi-k2.5 nothing ever does."""
    w, emitter = writer
    await run_turn(w, emitter, "It is morning.", [
        TextDelta(thread_id=TID, text="I will go to the bakery "),
        TextDelta(thread_id=TID, text="and buy bread with what is in my purse."),
    ])

    kinds = [r["memory_type"] for r in records_in_write_order(w.log_path)]
    assert kinds == ["plan"], (
        "ENG-14: a turn whose reasoning arrived as text recorded no plan - "
        "the memory stream had two record types that could not occur"
    )


@pytest.mark.asyncio
async def test_a_think_block_is_read_as_reasoning_and_the_speech_is_not(writer):
    """Run 23's actual shape: the reasoning and the reply in one text
    stream, separated by a closing think tag."""
    w, emitter = writer
    await run_turn(w, emitter, "At the well.", [
        TextDelta(thread_id=TID, text="<think>Earlier I promised Mara grain.</think>"),
        TextDelta(thread_id=TID, text="Morning, Mara."),
    ])

    written = records_in_write_order(w.log_path)
    assert [r["memory_type"] for r in written] == ["reflection"]
    assert "promised Mara grain" in written[0]["content"]
    assert "Morning, Mara." not in written[0]["content"], (
        "the greeting is what the town heard, not what the resident worked out"
    )


@pytest.mark.asyncio
async def test_text_after_the_first_tool_call_is_not_mistaken_for_a_plan(writer):
    """Text before the first call is deliberation; text after it narrates a
    result the observation record already holds. Folding the narration back
    in would turn every report of a finished action into a plan for it."""
    w, emitter = writer
    await run_turn(w, emitter, "At the field.", [
        call("harvest", crop="wheat"),
        result("harvest", "harvested 8 wheat"),
        TextDelta(thread_id=TID, text="I will have the wheat milled."),
    ])

    kinds = [r["memory_type"] for r in records_in_write_order(w.log_path)]
    assert kinds == ["observation"], f"narration became a plan: {kinds}"


@pytest.mark.asyncio
async def test_thinking_deltas_still_win_where_a_provider_emits_them(writer):
    """The old channel is not replaced, it is joined. A provider that does
    fill `reasoningContent` must keep producing exactly what it did."""
    w, emitter = writer
    await run_turn(w, emitter, "Morning.", [
        ThinkingDelta(thread_id=TID, text="I will go to the market."),
        TextDelta(thread_id=TID, text="Good morning to you all."),
    ])

    written = records_in_write_order(w.log_path)
    assert [r["memory_type"] for r in written] == ["plan"]
    assert written[0]["content"] == "I will go to the market."


@pytest.mark.asyncio
async def test_parallel_calls_to_one_tool_keep_their_own_results(writer):
    """Two `say` calls in one batch. Paired newest-first, the refusal was
    remembered against the neighbour who had answered."""
    w, emitter = writer
    await run_turn(w, emitter, "In the square.", [
        call("say", batch="b1", to="mara", content="morning"),
        call("say", batch="b1", to="tobias", content="morning"),
        result("say", "mara heard you", batch="b1"),
        result("say", "tobias is not here", status="error", batch="b1"),
    ])

    written = records_in_write_order(w.log_path)
    assert len(written) == 2
    assert written[0]["content"] == "I did say: mara heard you", (
        "ENG-14: the first call was recorded with the second call's result"
    )
    assert written[1]["content"] == "I tried and failed to say: tobias is not here"


@pytest.mark.asyncio
async def test_a_serial_pair_is_unaffected(writer):
    """The ordinary case, which newest-first happened to get right because
    there was only ever one pending call. It must stay right."""
    w, emitter = writer
    await run_turn(w, emitter, "At the field.", [
        call("harvest", crop="wheat"),
        result("harvest", "harvested 8 wheat"),
        call("harvest", crop="barley"),
        result("harvest", "the field is bare", status="error"),
    ])

    written = records_in_write_order(w.log_path)
    assert [r["content"] for r in written] == [
        "I did harvest: harvested 8 wheat",
        "I tried and failed to harvest: the field is bare",
    ]


def world_client_that_accepts_memories(agent_id: str):
    """A world that answers the registry calls a tick makes and accepts the
    memory rows the stream's sink POSTs to it."""
    from src.sim.world_tools import WorldClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry":
            return httpx.Response(200, json=[])
        if request.url.path == "/tools/registry/version":
            return httpx.Response(200, json={"version": 0})
        if request.url.path == f"/agents/{agent_id}/memories":
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected world call: {request.url.path}")

    return WorldClient(
        "http://world.test",
        agent_id=agent_id,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_a_real_turn_records_the_plan_the_resident_reasoned(tmp_path, monkeypatch):
    """THE WIRING TEST. Everything above drives the writer directly and
    would stay green if `memory_stream.attach(emitter)` or
    `memory_stream.start_turn(...)` were deleted from `pinned_boot.py`.

    This one boots a real engine against the fake broker, POSTs nothing and
    scripts nothing but the model's own reply, and drives `send_tick` - the
    route `agent_adapter.py` calls on every awake tick. The TextDelta it
    asserts on is emitted by frozen `thread.py:2766`, not by this test.
    """
    from src.sim.config import SimProfile
    from src.sim.model_profile import MODEL_ID
    from src.sim.pinned_boot import boot_sim_engine
    from tests.sim_fake_citadel import FakeBroker, install_fake_broker, text_turn_sse

    monkeypatch.setenv("FORK_SUBAGENT", "false")
    broker = FakeBroker()
    install_fake_broker(monkeypatch, broker)

    profile = SimProfile(
        agent_id="agent-boot",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    engine = await boot_sim_engine(
        profile,
        "http://world.test",
        world_client=world_client_that_accepts_memories("agent-boot"),
    )
    try:
        broker.queue(
            MODEL_ID,
            "sse",
            text_turn_sse(
                "<think>I will walk to the bakery and buy bread.</think>"
                "Off to the bakery, then."
            ),
        )
        broker.queue(MODEL_ID, "sse", text_turn_sse("Off to the bakery, then."))
        await engine.send_tick("It is 07:10 on day 0. You are at home.")

        written = records_in_write_order(
            tmp_path / "agent-boot" / MEMORY_STREAM_FILENAME
        )
        assert written, (
            "ENG-14: a real turn produced no memory at all. The only channel "
            "this writer listened to is one kimi-k2.5 never emits on."
        )
        assert [r["memory_type"] for r in written] == ["plan"]
        assert "walk to the bakery" in written[0]["content"]
        assert written[0]["agent_id"] == "agent-boot"
    finally:
        await engine.shutdown()
