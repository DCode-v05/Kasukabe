"""A resident's memory keeps what changed, not every time it waited or looked.

Run 28's complete trace analysis: 1,250 of the town's 1,315 observation
memories (95%) were raw tool echoes - `I did world:rest: {"location_id": ...}`
- 328 from `rest`, 161 from `look_around`. None of them reached a perception,
but the memory stream is what retrieval, reflection and the learning centre
read, and a stream that is 95% "I waited" is a stream in which the morning's
one sale is hard to find.

A successful wait or look is not an event in a life. It changed nothing and
the world already said so in the next perception. What stays:

  * every FAILURE, whatever the tool - a refusal teaches more than a success
    (`score_importance` already ranks it 7);
  * every act that did something - a harvest, a sale, a word said.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine_core.events import EventEmitter, ExecutionSummary, ToolExecution
from src.sim.memory_stream import MEMORY_STREAM_FILENAME, MemoryStreamWriter

TID = "thread-1"


def call(tool: str, **params) -> ToolExecution:
    return ToolExecution(thread_id=TID, tool_name=tool, source="engine", phase="call", params=params)


def result(tool: str, text: str, status: str = "success") -> ToolExecution:
    return ToolExecution(
        thread_id=TID, tool_name=tool, source="engine", phase="result", result=text, status=status,
    )


async def _turn(tmp_path: Path, events) -> list[dict]:
    emitter = EventEmitter()
    writer = MemoryStreamWriter("agent-1", tmp_path / MEMORY_STREAM_FILENAME)
    writer.attach(emitter)
    writer.start_turn("It is game-day 0, 07:40 (day). You are at street.")
    for event in events:
        await emitter.emit(event)
    await emitter.emit(ExecutionSummary(thread_id=TID, status="completed"))
    path = tmp_path / MEMORY_STREAM_FILENAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_a_wait_and_a_look_leave_no_memory_and_the_work_does(tmp_path):
    written = await _turn(tmp_path, [
        call("world:rest"), result("world:rest", '{"still_walking": true, "note": "You paused."}'),
        call("world:look_around"), result("world:look_around", '{"location_id": "street"}'),
        call("world:harvest", crop="wheat"), result("world:harvest", '{"item": "wheat", "quantity": 1}'),
    ])

    observations = [r["content"] for r in written if r["memory_type"] == "observation"]
    assert observations == ['I did world:harvest: {"item": "wheat", "quantity": 1}'], observations


@pytest.mark.asyncio
async def test_a_failed_wait_is_still_remembered(tmp_path):
    """A refusal is a lesson whatever the tool: 'I could not afford to rest'
    is exactly the memory Perrin needed."""
    written = await _turn(tmp_path, [
        call("world:rest"),
        result("world:rest", "Error: 'n04' cannot afford 'rest': costs 1 sim, has 0 sim", status="error"),
    ])

    observations = [r["content"] for r in written if r["memory_type"] == "observation"]
    assert len(observations) == 1 and observations[0].startswith("I tried and failed to world:rest"), observations


@pytest.mark.asyncio
async def test_speech_is_still_remembered(tmp_path):
    written = await _turn(tmp_path, [
        call("world:say", content="Morning, Marta."), result("world:say", '{"heard_by": ["Marta Thorne"]}'),
    ])

    assert [r["memory_type"] for r in written] == ["observation"]
