"""A tick arriving mid-turn used to throw away the turn in progress.

`send_tick` called session_log.start_turn() BEFORE handle_input, and
handle_input blocks on the thread's own turn lock. So a tick that arrived
while the previous turn was still working reset the recorder's accumulator,
and the ExecutionSummary that landed moments later flushed a record carrying
the NEW perception, no actions, and 0 turns.

That is exactly what "0 turn(s) · 0 tool call(s) · exit cancelled" was.
Measured on one ten-agent run: three engines cancelled EVERY session - n02,
n07, n09 - while making 25, 56 and 19 successful model calls between them.
The work happened; the record of it was destroyed. They were the three whose
turns ran longer than their cadence.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.engine_core.events import ExecutionSummary, TextDelta, ToolExecution
from src.sim.session_log import SessionLogWriter


def summary(turns: int = 1, tools: int = 0) -> ExecutionSummary:
    return ExecutionSummary(
        thread_id="t", status="completed", exit_reason="completed",
        tool_calls_count=tools, num_turns=turns, model_id="moonshotai.kimi-k2.5",
    )


@pytest.mark.asyncio
async def test_a_second_start_turn_destroys_the_first(tmp_path):
    """The bug itself, pinned so it cannot come back unnoticed. This is the
    behaviour send_tick's lock now prevents by never letting two turns
    overlap on one engine."""
    log = tmp_path / "session_log.jsonl"
    w = SessionLogWriter(agent_id="n07", log_path=log)

    w.start_turn("It is game-day 0, 06:20.")
    await w._on_event(ToolExecution(
        thread_id="t", tool_name="world_harvest", phase="call",
        params={"crop": "wheat"}, source="platform"))
    # A second tick lands before the first turn's summary.
    w.start_turn("It is game-day 0, 06:50.")
    await w._on_event(summary())

    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert entry["actions"] == [], "the harvest is gone - this is the defect"
    assert entry["input"] == "It is game-day 0, 06:50."


@pytest.mark.asyncio
async def test_serialised_turns_keep_their_work(tmp_path):
    """With send_tick serialised, the first turn finishes and flushes before
    the second can start, so nothing is lost."""
    log = tmp_path / "session_log.jsonl"
    w = SessionLogWriter(agent_id="n07", log_path=log)
    lock = asyncio.Lock()

    async def tick(text: str, tool: str) -> None:
        async with lock:
            w.start_turn(text)
            await w._on_event(ToolExecution(
                thread_id="t", tool_name=tool, phase="call",
                params={}, source="platform"))
            await asyncio.sleep(0)  # let the other tick try to interleave
            await w._on_event(TextDelta(thread_id="t", text=f"did {tool}"))
            await w._on_event(summary(tools=1))

    await asyncio.gather(
        tick("It is game-day 0, 06:20.", "world_harvest"),
        tick("It is game-day 0, 06:50.", "world_mine"),
    )

    entries = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(entries) == 2, "both turns must be recorded"
    tools = {e["actions"][0]["tool"] for e in entries}
    assert tools == {"world_harvest", "world_mine"}, "neither turn's work is lost"
    for e in entries:
        assert e["done_status"]["num_turns"] == 1
        assert e["output"], "each turn kept what it said"


def test_send_tick_holds_a_lock_across_the_whole_turn():
    """The recorders must start inside the lock, not before it - starting
    them outside is precisely what allowed the clobber."""
    import inspect

    from src.sim.pinned_boot import SimEngineState

    # Comments in this method discuss handle_input and start_turn by name,
    # so match the CODE: strip comment lines before looking at the order.
    source = inspect.getsource(SimEngineState.send_tick)
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    lock_at = code.index("async with self._turn_lock")
    start_at = code.index("self.session_log.start_turn")
    handle_at = code.index("self.coordinator.handle_input")
    assert lock_at < start_at < handle_at, (
        "start_turn and handle_input must both sit inside the turn lock - "
        "starting the recorders outside it is exactly what allowed the clobber"
    )
