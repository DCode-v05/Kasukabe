"""The turn itself must not report success over a history it failed to write.

Drives the REAL ExecutionThread.execute loop (same harness as
test_craft_authoring_loop: only the LLM adapter is scripted) against a REAL
SqlitePool-backed ConversationStore with the real migrations, so the
UNIQUE(thread_id, seq) constraint and the corrupt-row skip in load_messages are
the actual mechanisms under test.
"""

import json
from typing import AsyncIterator

import pytest

from src.engine_core.conversation_store import (
    ConversationPersistenceError,
    ConversationStore,
)
from src.engine_core.events import EventEmitter
from src.engine_core.thread import ExecutionThread
from src.herald.types import Message, StopReason, StreamEvent, ToolDef, Usage
from src.memory.memory_factory import create_working_memory
from src.utility_directory.tool_runtime import ToolDef as RuntimeToolDef, ToolRegistry


class FakeAdapter:
    """Yields one scripted body then a clean stop - no network, no model."""

    def __init__(self, text: str = "the answer"):
        self.text = text

    async def stream_with_tools(
        self,
        messages: list[Message],
        system=None,
        tools: list[ToolDef] | None = None,
        super_thinking: bool = False,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type="text", text=self.text)
        yield StreamEvent(type="stop", stop_reason=StopReason.END_TURN)
        yield StreamEvent(type="usage", usage=Usage(input_tokens=10, output_tokens=10))

    async def generate(self, messages, system=""):
        return "summary"


def tools_with_a_noop():
    reg = ToolRegistry()

    async def noop_handler(params):
        return "ok"

    reg.register(
        RuntimeToolDef(
            name="noop",
            description="A no-op tool (never called in this test).",
            input_schema={"type": "object", "properties": {}},
            handler=noop_handler,
        )
    )
    return reg


async def make_thread(db, store, session: str):
    return ExecutionThread(
        adapter=FakeAdapter(),
        emitter=EventEmitter(),
        memory=create_working_memory(session, db=db),
        tools=tools_with_a_noop(),
        conversation_store=store,
    )


@pytest.mark.asyncio
async def test_turn_persists_both_halves_on_the_happy_path(db):
    store = ConversationStore(db)
    th = await make_thread(db, store, "sess-ok")

    await th.execute("what is the status", thread_id="t-ok")

    msgs = await store.load_messages("t-ok")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert [m["seq"] for m in msgs] == [0, 1]


@pytest.mark.asyncio
async def test_a_lost_user_message_fails_the_turn_instead_of_half_writing_it(db):
    """The exact silent failure: seq=N is occupied, so the user half cannot land.

    Before the fix the write was swallowed and the assistant half was written at
    seq=N+1 anyway, leaving an answer to a question the history did not contain,
    and the turn still reported completed.
    """
    store = ConversationStore(db)
    await store.ensure_conversation("t-collide")
    await db.execute(
        "INSERT INTO conversation_messages (thread_id, seq, role, content_json)"
        " VALUES (?, ?, ?, ?)",
        ("t-collide", 0, "user", "{not json"),
    )
    await db.commit()

    th = await make_thread(db, store, "sess-collide")

    await th.execute("hello", thread_id="t-collide")

    rows = await db.fetch(
        "SELECT seq, role FROM conversation_messages WHERE thread_id = ? ORDER BY seq",
        ("t-collide",),
    )
    assert [(r["seq"], r["role"]) for r in rows] == [
        (0, "user"),
        (1, "user"),
        (2, "assistant"),
    ]


@pytest.mark.asyncio
async def test_write_failure_reaches_the_caller_and_leaves_no_orphan_answer(db):
    store = ConversationStore(db)
    th = await make_thread(db, store, "sess-fail")

    calls: list[tuple] = []
    real_save = store.save_message

    async def save_first_then_die(thread_id, seq, role, content_json):
        calls.append((seq, role))
        if role == "user":
            raise RuntimeError("disk is gone")
        await real_save(thread_id, seq, role, content_json)

    store.save_message = save_first_then_die

    with pytest.raises(RuntimeError):
        await th.execute("hello", thread_id="t-fail")

    assert calls == [(0, "user")]
    assert await store.load_messages("t-fail") == []


@pytest.mark.asyncio
async def test_unreadable_sequence_fails_the_turn_rather_than_guessing(db):
    store = ConversationStore(db)
    await store.ensure_conversation("t-noseq")
    await store.save_message("t-noseq", 0, "user", json.dumps("earlier"))
    await store.save_message("t-noseq", 1, "assistant", json.dumps("earlier answer"))

    th = await make_thread(db, store, "sess-noseq")

    async def broken_fetchval(*a, **k):
        raise RuntimeError("disk is gone")

    store.db.fetchval = broken_fetchval

    with pytest.raises(ConversationPersistenceError):
        await th.execute("hello", thread_id="t-noseq")

    store.db.fetchval = None
    rows = await db.fetch(
        "SELECT seq FROM conversation_messages WHERE thread_id = ? ORDER BY seq",
        ("t-noseq",),
    )
    assert [r["seq"] for r in rows] == [0, 1]
