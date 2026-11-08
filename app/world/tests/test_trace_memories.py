"""TraceStore.record_memory_if_new: the consumer half of the memory pipeline.

The `memories` table has been read by world/agent_viewer_api.py since
genesis and, until this, had no writer - the same shape of gap RC-2 found
in `utterances`. These prove the branch that closes it.
"""

from __future__ import annotations

from typing import Any

import pytest

from world.core.events import WorldEvent
from world.wiring import TraceStore


class RecordingClient:
    """Stands in for the SQLite trace client: remembers what was inserted."""

    def __init__(self) -> None:
        self.inserted: list[tuple[str, list[dict[str, Any]]]] = []

    async def insert(self, table: str, rows: list[dict[str, Any]]) -> None:
        self.inserted.append((table, rows))


@pytest.fixture
def store() -> tuple[TraceStore, RecordingClient]:
    client = RecordingClient()
    trace = TraceStore.__new__(TraceStore)
    import asyncio

    trace.client = client
    trace.known_agent_ids = {"agent-1"}
    trace.persisted_transaction_ids = set()
    trace.write_lock = asyncio.Lock()
    return trace, client


def memory_event(**payload: Any) -> WorldEvent:
    base = {
        "agent_id": "agent-1",
        "memory_type": "observation",
        "content": "I did buy: bought 3 bread",
        "importance": 5,
        "related_memory_ids": [1, 2],
    }
    base.update(payload)
    return WorldEvent(type="agent.memory.recorded", tick=99, payload=base)


@pytest.mark.asyncio
async def test_a_memory_event_becomes_a_memories_row(store):
    trace, client = store
    await trace.record_memory_if_new(memory_event())
    assert len(client.inserted) == 1
    table, rows = client.inserted[0]
    assert table == "memories"
    assert rows[0]["agent_id"] == "agent-1"
    assert rows[0]["memory_type"] == "observation"
    assert rows[0]["tick"] == 99
    assert rows[0]["related_memory_ids"] == [1, 2]


@pytest.mark.asyncio
async def test_it_ignores_every_other_kind_of_event(store):
    trace, client = store
    await trace.record_memory_if_new(
        WorldEvent(type="agent.utterance.spoken", tick=1, payload={"speaker_agent_id": "agent-1"})
    )
    assert client.inserted == []


@pytest.mark.asyncio
async def test_an_unknown_agent_is_dropped(store):
    """Same guard as the utterance branch: the table has an FK to agents."""
    trace, client = store
    await trace.record_memory_if_new(memory_event(agent_id="ghost"))
    assert client.inserted == []


@pytest.mark.asyncio
async def test_a_dream_probe_can_never_be_written(store):
    """DESIGN.md section 10: dream probes are never written to agent memory.
    The table's CHECK would reject it; this refuses before the insert so one
    bad row cannot fail a whole batch."""
    trace, client = store
    await trace.record_memory_if_new(memory_event(memory_type="dream_probe"))
    assert client.inserted == []


@pytest.mark.asyncio
async def test_importance_is_clamped_into_the_tables_range(store):
    trace, client = store
    await trace.record_memory_if_new(memory_event(importance=99))
    assert client.inserted[0][1][0]["importance"] == 10
    await trace.record_memory_if_new(memory_event(importance=-4))
    assert client.inserted[1][1][0]["importance"] == 1


@pytest.mark.asyncio
async def test_no_citations_is_written_as_null_not_an_empty_array(store):
    trace, client = store
    await trace.record_memory_if_new(memory_event(related_memory_ids=[]))
    assert client.inserted[0][1][0]["related_memory_ids"] is None
