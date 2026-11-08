"""memory_stream: an engine turn becomes retrievable memory.

The `memories` table has been queried by world/agent_viewer_api.py since
genesis and had no writer at all. These cover the producer half - that a
turn's events become observation / plan / reflection records, scored and
chained the way Generative Agents' retrieval model needs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.engine_core.events import EventEmitter, ExecutionSummary, ThinkingDelta, ToolExecution
from src.sim.memory_stream import (
    MemoryStreamWriter,
    read_memory_stream,
    score_importance,
)

TID = "thread-1"


def call(tool: str, **params) -> ToolExecution:
    return ToolExecution(thread_id=TID, tool_name=tool, source="engine", phase="call", params=params)


def result(tool: str, text: str, status: str = "ok") -> ToolExecution:
    return ToolExecution(
        thread_id=TID, tool_name=tool, source="engine", phase="result", result=text, status=status
    )


async def run_turn(writer: MemoryStreamWriter, emitter: EventEmitter, perception: str, events) -> None:
    writer.start_turn(perception)
    for event in events:
        await emitter.emit(event)
    await emitter.emit(ExecutionSummary(thread_id=TID, status="completed"))


@pytest.fixture
def writer(tmp_path: Path):
    emitter = EventEmitter()
    w = MemoryStreamWriter("agent-1", tmp_path / "memory_stream.jsonl")
    w.attach(emitter)
    return w, emitter


@pytest.mark.asyncio
async def test_a_completed_tool_call_becomes_an_observation(writer):
    w, emitter = writer
    await run_turn(w, emitter, "You are at the market.", [
        call("buy", item="bread"), result("buy", "bought 3 bread for 300 sim"),
    ])
    records = read_memory_stream(w.log_path)
    assert len(records) == 1
    assert records[0]["memory_type"] == "observation"
    assert "buy" in records[0]["content"]
    assert "bought 3 bread" in records[0]["content"]


@pytest.mark.asyncio
async def test_stated_intent_becomes_a_plan(writer):
    w, emitter = writer
    await run_turn(w, emitter, "Morning.", [
        ThinkingDelta(thread_id=TID, text="I will go to the market and buy bread today."),
    ])
    kinds = [r["memory_type"] for r in read_memory_stream(w.log_path)]
    assert kinds == ["plan"]


@pytest.mark.asyncio
async def test_looking_back_becomes_a_reflection_that_cites_its_sources(writer):
    """A chain, not a flat log - the property the paper's retrieval needs."""
    w, emitter = writer
    await run_turn(w, emitter, "At the market.", [
        call("buy", item="bread"), result("buy", "bought bread"),
    ])
    await run_turn(w, emitter, "At the market.", [
        call("sell", item="wheat"), result("sell", "sold wheat"),
        ThinkingDelta(thread_id=TID, text="Earlier I bought bread here, so I already know the price."),
    ])
    records = read_memory_stream(w.log_path)
    reflection = [r for r in records if r["memory_type"] == "reflection"]
    assert len(reflection) == 1
    assert reflection[0]["related_memory_ids"], "a reflection must cite what it was drawn from"


@pytest.mark.asyncio
async def test_a_failed_call_is_remembered_as_a_failure_and_outranks_a_success(writer):
    w, emitter = writer
    await run_turn(w, emitter, "At the market.", [
        call("buy", item="bread"), result("buy", "insufficient funds", status="error"),
    ])
    record = read_memory_stream(w.log_path)[0]
    assert "failed" in record["content"]
    assert record["importance"] > score_importance("observation", tool="buy", failed=False)


def test_importance_ranking_is_the_defensible_part():
    assert score_importance("reflection") > score_importance("plan")
    assert score_importance("plan") > score_importance("observation", tool="buy")
    assert score_importance("observation", tool="transfer") > score_importance(
        "observation", tool="read_will"
    )
    assert 1 <= score_importance("observation") <= 10


@pytest.mark.asyncio
async def test_a_dangling_call_never_becomes_a_memory(writer):
    """A tool call whose result never came back is not something the agent
    learned - notably, that is exactly what a permission denial looks like."""
    w, emitter = writer
    await run_turn(w, emitter, "At the market.", [call("buy", item="bread")])
    assert read_memory_stream(w.log_path) == []


@pytest.mark.asyncio
async def test_records_carry_the_tick_and_agent_they_belong_to(writer):
    w, emitter = writer
    w.tick = 4321
    await run_turn(w, emitter, "At the market.", [
        call("say", content="hello"), result("say", "spoke"),
    ])
    record = read_memory_stream(w.log_path)[0]
    assert record["agent_id"] == "agent-1"
    assert record["tick"] == 4321
    assert 1 <= record["importance"] <= 10


@pytest.mark.asyncio
async def test_a_sink_receives_every_record_as_it_is_written(tmp_path):
    """The sink is how world/wiring.py gets these into the memories table."""
    seen = []
    emitter = EventEmitter()
    w = MemoryStreamWriter("agent-1", tmp_path / "m.jsonl", sink=lambda r: seen.append(r) or _noop())
    w.attach(emitter)
    await run_turn(w, emitter, "At the market.", [
        call("buy", item="bread"), result("buy", "bought bread"),
        ThinkingDelta(thread_id=TID, text="I will buy more tomorrow."),
    ])
    assert [r["memory_type"] for r in seen] == ["observation", "plan"]
    assert len(read_memory_stream(w.log_path)) == 2


async def _noop() -> None:
    return None


@pytest.mark.asyncio
async def test_an_agent_can_read_back_what_it_remembered(writer):
    """Write a turn, then recall it through the tool the model calls."""
    from src.sim.memory_stream import build_memory_stream_tool

    w, emitter = writer
    await run_turn(w, emitter, "You are in the town square.", [
        call("petition", subject="the mill tax"), result("petition", "petition filed"),
        ThinkingDelta(thread_id=TID, text="I will ask the others to sign it."),
    ])

    tool = build_memory_stream_tool(w.log_path)
    text = await tool.handler({})

    assert "no memories recorded" not in text
    assert "petition" in text.lower(), text
    assert "importance" in text


@pytest.mark.asyncio
async def test_recall_can_be_narrowed_to_one_kind_of_memory(writer):
    from src.sim.memory_stream import build_memory_stream_tool

    w, emitter = writer
    await run_turn(w, emitter, "Morning.", [
        call("harvest", crop="wheat"), result("harvest", "harvested 8 wheat"),
        ThinkingDelta(thread_id=TID, text="I will sell the wheat at market tomorrow."),
    ])

    tool = build_memory_stream_tool(w.log_path)
    everything = await tool.handler({})
    assert "observed" in everything and "planned" in everything, everything

    plans = await tool.handler({"kind": "plan"})
    assert "planned" in plans
    assert "observed" not in plans, "the kind filter let an observation through"


@pytest.mark.asyncio
async def test_an_empty_stream_says_so_rather_than_failing(tmp_path):
    from src.sim.memory_stream import build_memory_stream_tool

    tool = build_memory_stream_tool(tmp_path / "nothing-here.jsonl")
    assert "no memories" in (await tool.handler({})).lower()


def test_the_recall_tool_is_actually_permitted():
    """thread.py's check_permission() denies by default, so a ToolDef with no
    rule is registered and silently never callable - the exact failure that
    swallowed recall_session_log's first test."""
    from src.sim.memory_stream import (
        MEMORY_STREAM_TOOL_ALLOW_RULE,
        MEMORY_STREAM_TOOL_NAME,
        install_memory_stream_permissions,
    )

    class FakeThread:
        def __init__(self):
            self.session_rules = []

    thread = FakeThread()
    install_memory_stream_permissions(thread)
    assert MEMORY_STREAM_TOOL_ALLOW_RULE in thread.session_rules
    assert MEMORY_STREAM_TOOL_ALLOW_RULE.tool == MEMORY_STREAM_TOOL_NAME

    install_memory_stream_permissions(thread)
    assert len(thread.session_rules) == 1, "granting twice must not duplicate the rule"


def test_the_engine_boot_registers_and_grants_the_reader():
    """The tool has to be wired at the composition root, not merely exist."""
    import inspect

    from src.sim import pinned_boot

    source = inspect.getsource(pinned_boot)
    assert "build_memory_stream_tool(" in source, "recall_memories is registered nowhere"
    assert "install_memory_stream_permissions(" in source, "recall_memories is granted nowhere"
