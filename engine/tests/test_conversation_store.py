"""Tests for ConversationStore — SQLite conversation persistence."""

import json
import pytest
import pytest_asyncio

from src.infra.sqlite_pool import SqlitePool
from src.engine_core.conversation_store import ConversationStore


@pytest_asyncio.fixture
async def db():
    pool = SqlitePool(":memory:")
    await pool.connect()
    from pathlib import Path

    migrations_dir = Path(__file__).parent.parent / "migrations"
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        await pool.executescript(sql_file.read_text(encoding="utf-8"))
    await pool.commit()
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def store(db):
    return ConversationStore(db)


class TestConversationStore:
    @pytest.mark.asyncio
    async def test_ensure_and_load_empty(self, store):
        await store.ensure_conversation("thread-1")
        msgs = await store.load_messages("thread-1")
        assert msgs == []

    @pytest.mark.asyncio
    async def test_save_and_load_messages(self, store):
        await store.ensure_conversation("thread-1")
        await store.save_message("thread-1", 0, "user", json.dumps("Hello"))
        await store.save_message("thread-1", 1, "assistant", json.dumps("Hi there!"))
        await store.save_message("thread-1", 2, "user", json.dumps("How are you?"))

        msgs = await store.load_messages("thread-1")
        assert len(msgs) == 3
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello"
        assert msgs[0]["seq"] == 0
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "Hi there!"
        assert msgs[2]["role"] == "user"
        assert msgs[2]["content"] == "How are you?"

    @pytest.mark.asyncio
    async def test_message_count(self, store):
        await store.ensure_conversation("thread-1")
        assert await store.message_count("thread-1") == 0
        await store.save_message("thread-1", 0, "user", json.dumps("Hello"))
        assert await store.message_count("thread-1") == 1
        await store.save_message("thread-1", 1, "assistant", json.dumps("Hi"))
        assert await store.message_count("thread-1") == 2

    @pytest.mark.asyncio
    async def test_turn_count_increments(self, store, db):
        await store.ensure_conversation("thread-1")
        await store.save_message("thread-1", 0, "user", json.dumps("Hello"))
        await store.save_message("thread-1", 1, "assistant", json.dumps("Hi"))

        row = await db.fetchrow(
            "SELECT turn_count FROM conversations WHERE thread_id = ?",
            ("thread-1",),
        )
        assert row["turn_count"] == 2

    @pytest.mark.asyncio
    async def test_save_and_load_metadata(self, store):
        await store.ensure_conversation("thread-1")
        await store.save_metadata(
            "thread-1",
            charter_json=json.dumps({"goal": "analyze revenue"}),
            scratchpad_json=json.dumps({"key": "value"}),
            lc_watermark=5,
        )

        convo = await store.load_conversation("thread-1")
        assert convo is not None
        assert convo["charter"] == {"goal": "analyze revenue"}
        assert convo["scratchpad"] == {"key": "value"}
        assert convo["lc_watermark"] == 5

    @pytest.mark.asyncio
    async def test_load_conversation_with_messages(self, store):
        await store.ensure_conversation("thread-1")
        await store.save_message("thread-1", 0, "user", json.dumps("Hello"))
        await store.save_message("thread-1", 1, "assistant", json.dumps("Hi"))

        convo = await store.load_conversation("thread-1")
        assert convo is not None
        assert convo["thread_id"] == "thread-1"
        assert len(convo["messages"]) == 2

    @pytest.mark.asyncio
    async def test_load_nonexistent(self, store):
        convo = await store.load_conversation("nonexistent")
        assert convo is None

    @pytest.mark.asyncio
    async def test_list_active(self, store):
        await store.ensure_conversation("thread-1")
        await store.ensure_conversation("thread-2")
        await store.ensure_conversation("thread-3")

        active = await store.list_active()
        assert len(active) == 3
        thread_ids = {c["thread_id"] for c in active}
        assert thread_ids == {"thread-1", "thread-2", "thread-3"}

    @pytest.mark.asyncio
    async def test_delete_thread_cascades(self, store):
        await store.ensure_conversation("thread-1")
        await store.save_message("thread-1", 0, "user", json.dumps("Hello"))
        await store.save_message("thread-1", 1, "assistant", json.dumps("Hi"))

        await store.delete_thread("thread-1")

        convo = await store.load_conversation("thread-1")
        assert convo is None
        msgs = await store.load_messages("thread-1")
        assert msgs == []

    @pytest.mark.asyncio
    async def test_corrupt_json_partial_recovery(self, store, db):
        await store.ensure_conversation("thread-1")
        await store.save_message("thread-1", 0, "user", json.dumps("Hello"))
        # Inject a corrupt message directly
        await db.execute(
            "INSERT INTO conversation_messages (thread_id, seq, role, content_json) VALUES (?, ?, ?, ?)",
            ("thread-1", 1, "assistant", "{invalid json!!!"),
        )
        await db.commit()
        await store.save_message("thread-1", 2, "user", json.dumps("Still here"))

        msgs = await store.load_messages("thread-1")
        # Corrupt message at seq=1 is skipped
        assert len(msgs) == 2
        assert msgs[0]["seq"] == 0
        assert msgs[1]["seq"] == 2

    @pytest.mark.asyncio
    async def test_ensure_conversation_idempotent(self, store):
        await store.ensure_conversation("thread-1")
        await store.ensure_conversation("thread-1")
        active = await store.list_active()
        assert len(active) == 1

    @pytest.mark.asyncio
    async def test_multiple_threads_isolation(self, store):
        await store.ensure_conversation("thread-1")
        await store.ensure_conversation("thread-2")
        await store.save_message("thread-1", 0, "user", json.dumps("Thread 1 msg"))
        await store.save_message("thread-2", 0, "user", json.dumps("Thread 2 msg"))

        msgs1 = await store.load_messages("thread-1")
        msgs2 = await store.load_messages("thread-2")
        assert len(msgs1) == 1
        assert len(msgs2) == 1
        assert msgs1[0]["content"] == "Thread 1 msg"
        assert msgs2[0]["content"] == "Thread 2 msg"

    # ── save_thread_index / read paths (migration 015) ─────────────

    @pytest.mark.asyncio
    async def test_save_and_load_thread_index(self, store):
        await store.ensure_conversation("thread-1")
        await store.save_thread_index(
            "thread-1",
            summary="Q4 revenue analysis",
            current_state="Comparing Q4 to Q3 numbers",
            recent_turns=["Analyze Q4", "Compare to Q3", "Forecast Q1"],
        )
        convo = await store.load_conversation("thread-1")
        assert convo is not None
        assert convo["thread_index"]["summary"] == "Q4 revenue analysis"
        assert convo["thread_index"]["current_state"] == "Comparing Q4 to Q3 numbers"
        assert convo["thread_index"]["recent_turns"] == [
            "Analyze Q4",
            "Compare to Q3",
            "Forecast Q1",
        ]

    @pytest.mark.asyncio
    async def test_thread_index_empty_defaults(self, store):
        await store.ensure_conversation("thread-1")
        convo = await store.load_conversation("thread-1")
        assert convo["thread_index"] == {
            "summary": "",
            "current_state": "",
            "recent_turns": [],
        }

    @pytest.mark.asyncio
    async def test_thread_index_in_list_active(self, store):
        await store.ensure_conversation("thread-a")
        await store.save_thread_index(
            "thread-a",
            summary="alpha",
            current_state="ongoing",
            recent_turns=["hi"],
        )
        active = await store.list_active()
        assert len(active) == 1
        assert active[0]["thread_index"]["summary"] == "alpha"
        assert active[0]["thread_index"]["current_state"] == "ongoing"
        assert active[0]["thread_index"]["recent_turns"] == ["hi"]

    @pytest.mark.asyncio
    async def test_thread_index_overwrites(self, store):
        """Each save_thread_index fully replaces prior state (not appended)."""
        await store.ensure_conversation("thread-1")
        await store.save_thread_index(
            "thread-1",
            summary="first",
            current_state="a",
            recent_turns=["x"],
        )
        await store.save_thread_index(
            "thread-1",
            summary="second",
            current_state="b",
            recent_turns=["y", "z"],
        )
        convo = await store.load_conversation("thread-1")
        assert convo["thread_index"]["summary"] == "second"
        assert convo["thread_index"]["current_state"] == "b"
        assert convo["thread_index"]["recent_turns"] == ["y", "z"]

    @pytest.mark.asyncio
    async def test_thread_index_recent_turns_corrupt(self, store, db):
        """Corrupt JSON in recent_turns column degrades to empty list, not crash."""
        await store.ensure_conversation("thread-1")
        await db.execute(
            "UPDATE conversations SET recent_turns = ? WHERE thread_id = ?",
            ("{not valid json", "thread-1"),
        )
        await db.commit()
        convo = await store.load_conversation("thread-1")
        assert convo["thread_index"]["recent_turns"] == []
