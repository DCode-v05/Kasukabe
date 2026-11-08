"""Verifies thread routing fields persist across simulated engine restart.

Pre-fix, `coordinator.update_thread_index` only mutated the in-memory
`ThreadIndexEntry`.  `conversations.task_index_json` was never written, so
restarted engines lost all routing context.  Migration 015 promoted the
fields to dedicated columns; `save_thread_index` writes them per turn.

This test simulates restart by closing the SQLite pool and reopening the
same on-disk file — same semantics as a process bounce.  Real SQLite,
not mocks (project rule).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from src.infra.sqlite_pool import SqlitePool
from src.engine_core.conversation_store import ConversationStore


@pytest_asyncio.fixture
async def fresh_db(tmp_path):
    """Real on-disk SQLite so 'restart' means closing + reopening the file."""
    db_path = tmp_path / "test.db"
    pool = SqlitePool(str(db_path))
    await pool.connect()
    migrations_dir = Path(__file__).parent.parent / "migrations"
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        await pool.executescript(sql_file.read_text(encoding="utf-8"))
    await pool.commit()
    yield pool, str(db_path)
    await pool.close()


@pytest.mark.asyncio
async def test_thread_index_survives_restart(fresh_db):
    pool, db_path = fresh_db
    store = ConversationStore(pool)
    await store.ensure_conversation("thread-1")
    await store.save_thread_index(
        "thread-1",
        summary="Plan Q1 launch",
        current_state="Drafted timeline",
        recent_turns=["What's the launch date?", "Who owns marketing?"],
    )
    await pool.close()

    pool2 = SqlitePool(db_path)
    await pool2.connect()
    try:
        store2 = ConversationStore(pool2)
        active = await store2.list_active()
        assert len(active) == 1
        ti = active[0]["thread_index"]
        assert ti["summary"] == "Plan Q1 launch"
        assert ti["current_state"] == "Drafted timeline"
        assert ti["recent_turns"] == [
            "What's the launch date?",
            "Who owns marketing?",
        ]
    finally:
        await pool2.close()


@pytest.mark.asyncio
async def test_thread_index_empty_survives_restart(fresh_db):
    """Threads that never had save_thread_index called restore to safe defaults."""
    pool, db_path = fresh_db
    store = ConversationStore(pool)
    await store.ensure_conversation("thread-bare")
    await pool.close()

    pool2 = SqlitePool(db_path)
    await pool2.connect()
    try:
        store2 = ConversationStore(pool2)
        active = await store2.list_active()
        assert len(active) == 1
        ti = active[0]["thread_index"]
        assert ti == {"summary": "", "current_state": "", "recent_turns": []}
    finally:
        await pool2.close()


@pytest.mark.asyncio
async def test_load_conversation_after_restart(fresh_db):
    """load_conversation (used by crash recovery) returns the same shape."""
    pool, db_path = fresh_db
    store = ConversationStore(pool)
    await store.ensure_conversation("thread-1")
    await store.save_thread_index(
        "thread-1",
        summary="Migrate auth layer",
        current_state="Investigating JWT impl",
        recent_turns=["Where does sign() live?"],
    )
    await pool.close()

    pool2 = SqlitePool(db_path)
    await pool2.connect()
    try:
        store2 = ConversationStore(pool2)
        convo = await store2.load_conversation("thread-1")
        assert convo is not None
        ti = convo["thread_index"]
        assert ti["summary"] == "Migrate auth layer"
        assert ti["current_state"] == "Investigating JWT impl"
        assert ti["recent_turns"] == ["Where does sign() live?"]
    finally:
        await pool2.close()
