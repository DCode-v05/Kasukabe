import asyncio
import os
import tempfile
import time

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from pathlib import Path
from src.infra.sqlite_pool import SqlitePool
from src.memory.sqlite_working_memory import SqliteWorkingMemory
from src.memory.working_memory import EntryType, LogEntry


def run(coro):
    return asyncio.run(coro)


async def setup_db() -> SqlitePool:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    db = SqlitePool(tmp.name)
    await db.connect()
    migration = Path(__file__).resolve().parent.parent / "migrations" / "010_working_memory_log.sql"
    await db.executescript(migration.read_text())
    await db.commit()
    return db


async def teardown_db(db: SqlitePool):
    path = db.db_path
    await db.close()
    os.unlink(path)


class TestWriteAndRead:
    def test_round_trip(self):
        async def go():
            db = await setup_db()
            try:
                wm = SqliteWorkingMemory(db, "sess1")
                await wm.write("task1", EntryType.MESSAGE_USER, "hello world")
                entries = await wm.read("task1")
                assert len(entries) == 1
                assert entries[0].thread_id == "task1"
                assert entries[0].type == EntryType.MESSAGE_USER
                assert entries[0].content == "hello world"
            finally:
                await teardown_db(db)

        run(go())

    def test_write_batch(self):
        async def go():
            db = await setup_db()
            try:
                wm = SqliteWorkingMemory(db, "sess1")
                items = [
                    LogEntry(thread_id="task1", type=EntryType.TOOL_CALL, content="call1"),
                    LogEntry(thread_id="task1", type=EntryType.TOOL_RESULT, content="result1"),
                ]
                await wm.write_batch("task1", items)
                entries = await wm.read("task1")
                assert len(entries) == 2
                assert entries[0].type == EntryType.TOOL_CALL
                assert entries[1].type == EntryType.TOOL_RESULT
            finally:
                await teardown_db(db)

        run(go())

    def test_multiple_writes_ordered(self):
        async def go():
            db = await setup_db()
            try:
                wm = SqliteWorkingMemory(db, "sess1")
                await wm.write("task1", EntryType.MESSAGE_USER, "first")
                await wm.write("task1", EntryType.MESSAGE_ASSISTANT, "second")
                await wm.write("task1", EntryType.TOOL_CALL, "third")
                entries = await wm.read("task1")
                assert [e.content for e in entries] == ["first", "second", "third"]
            finally:
                await teardown_db(db)

        run(go())


class TestReadFilters:
    def test_read_by_type(self):
        async def go():
            db = await setup_db()
            try:
                wm = SqliteWorkingMemory(db, "sess1")
                await wm.write("task1", EntryType.MESSAGE_USER, "msg")
                await wm.write("task1", EntryType.TOOL_CALL, "call")
                await wm.write("task1", EntryType.MESSAGE_ASSISTANT, "reply")
                entries = await wm.read_by_type("task1", EntryType.TOOL_CALL)
                assert len(entries) == 1
                assert entries[0].content == "call"
            finally:
                await teardown_db(db)

        run(go())

    def test_read_messages(self):
        async def go():
            db = await setup_db()
            try:
                wm = SqliteWorkingMemory(db, "sess1")
                await wm.write("task1", EntryType.MESSAGE_USER, "hi")
                await wm.write("task1", EntryType.TOOL_CALL, "tool")
                await wm.write("task1", EntryType.MESSAGE_ASSISTANT, "hey")
                entries = await wm.read_messages("task1")
                assert len(entries) == 2
                assert entries[0].content == "hi"
                assert entries[1].content == "hey"
            finally:
                await teardown_db(db)

        run(go())

    def test_read_for_sub_agent(self):
        async def go():
            db = await setup_db()
            try:
                wm = SqliteWorkingMemory(db, "sess1")
                await wm.write("task1", EntryType.TOOL_CALL, "main", sub_agent_id="")
                await wm.write("task1", EntryType.TOOL_CALL, "sub", sub_agent_id="agent-A")
                entries = await wm.read_for_sub_agent("task1", "agent-A")
                assert len(entries) == 1
                assert entries[0].content == "sub"
            finally:
                await teardown_db(db)

        run(go())


class TestSessionIsolation:
    def test_different_sessions_isolated(self):
        async def go():
            db = await setup_db()
            try:
                wm1 = SqliteWorkingMemory(db, "sess1")
                wm2 = SqliteWorkingMemory(db, "sess2")
                await wm1.write("task1", EntryType.MESSAGE_USER, "from sess1")
                await wm2.write("task1", EntryType.MESSAGE_USER, "from sess2")
                entries1 = await wm1.read("task1")
                entries2 = await wm2.read("task1")
                assert len(entries1) == 1
                assert entries1[0].content == "from sess1"
                assert len(entries2) == 1
                assert entries2[0].content == "from sess2"
            finally:
                await teardown_db(db)

        run(go())

    def test_read_all_scoped_to_session(self):
        async def go():
            db = await setup_db()
            try:
                wm1 = SqliteWorkingMemory(db, "sess1")
                wm2 = SqliteWorkingMemory(db, "sess2")
                await wm1.write("t1", EntryType.MESSAGE_USER, "a")
                await wm1.write("t2", EntryType.MESSAGE_USER, "b")
                await wm2.write("t1", EntryType.MESSAGE_USER, "c")
                all1 = await wm1.read_all()
                assert len(all1) == 2
                all2 = await wm2.read_all()
                assert len(all2) == 1
            finally:
                await teardown_db(db)

        run(go())


class TestSearch:
    def test_fts5_search(self):
        async def go():
            db = await setup_db()
            try:
                wm = SqliteWorkingMemory(db, "sess1")
                await wm.write("task1", EntryType.MESSAGE_USER, "implement REST API endpoints")
                await wm.write("task1", EntryType.TOOL_CALL, "reading database schema")
                await wm.write("task1", EntryType.MESSAGE_ASSISTANT, "created the routes")
                results = await wm.search("REST API")
                assert len(results) >= 1
                assert any("REST API" in r.content for r in results)
            finally:
                await teardown_db(db)

        run(go())


class TestContextSummary:
    def test_basic_summary(self):
        async def go():
            db = await setup_db()
            try:
                wm = SqliteWorkingMemory(db, "sess1")
                await wm.write("task1", EntryType.MESSAGE_USER, "build a form")
                await wm.write("task1", EntryType.MESSAGE_ASSISTANT, "created form component")
                summary = await wm.get_context_summary(thread_id="task1")
                assert "Previous context" in summary
                assert "build a form" in summary
            finally:
                await teardown_db(db)

        run(go())

    def test_summary_with_thread_id_filter(self):
        async def go():
            db = await setup_db()
            try:
                wm = SqliteWorkingMemory(db, "sess1")
                await wm.write("task1", EntryType.MESSAGE_USER, "task one content")
                await wm.write("task2", EntryType.MESSAGE_USER, "task two content")
                summary = await wm.get_context_summary(thread_id="task1")
                assert "task one content" in summary
                assert "task two content" not in summary
            finally:
                await teardown_db(db)

        run(go())

    def test_summary_truncation(self):
        async def go():
            db = await setup_db()
            try:
                wm = SqliteWorkingMemory(db, "sess1")
                for i in range(20):
                    await wm.write("task1", EntryType.TOOL_RESULT, f"result {'x' * 2000} #{i}")
                summary = await wm.get_context_summary(
                    thread_id="task1", max_items=20, max_tokens=1000
                )
                assert "truncated" in summary.lower()
            finally:
                await teardown_db(db)

        run(go())


class TestClearAndCleanup:
    def test_clear_task(self):
        async def go():
            db = await setup_db()
            try:
                wm = SqliteWorkingMemory(db, "sess1")
                await wm.write("task1", EntryType.MESSAGE_USER, "keep")
                await wm.write("task2", EntryType.MESSAGE_USER, "delete")
                await wm.clear_task("task2")
                assert len(await wm.read("task1")) == 1
                assert len(await wm.read("task2")) == 0
            finally:
                await teardown_db(db)

        run(go())

    def test_cleanup_expired(self):
        async def go():
            db = await setup_db()
            try:
                wm = SqliteWorkingMemory(db, "sess1")
                await wm.write("task1", EntryType.MESSAGE_USER, "old entry")
                # Backdate the entry
                await db.execute(
                    "UPDATE working_memory_log SET created_at = ?",
                    (time.time() - 100_000,),
                )
                await db.commit()
                await wm.write("task1", EntryType.MESSAGE_USER, "new entry")
                deleted = await wm.cleanup_expired(ttl_seconds=86400)
                assert deleted == 1
                entries = await wm.read("task1")
                assert len(entries) == 1
                assert entries[0].content == "new entry"
            finally:
                await teardown_db(db)

        run(go())


class TestSessionIdProperty:
    def test_session_id(self):
        async def go():
            db = await setup_db()
            try:
                wm = SqliteWorkingMemory(db, "my-session")
                assert wm.session_id == "my-session"
            finally:
                await teardown_db(db)

        run(go())
