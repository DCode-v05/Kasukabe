"""A failed conversation write must be audible, not inferred.

These are regression tests for the silent half-written turn: save_message used
to log CRITICAL and return None on permanent failure, which is byte-identical
to success from the caller's side. The caller then wrote the assistant half of
the turn on top of a missing user half and reported the turn completed.

Everything here runs against a real SqlitePool with the real migrations - the
UNIQUE(thread_id, seq) constraint is what produces the failure, not a mock.
"""

import json

import pytest
import pytest_asyncio

from src.engine_core.conversation_store import (
    ConversationPersistenceError,
    ConversationStore,
)
from src.infra.sqlite_pool import SqlitePool


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
    s = ConversationStore(db)
    await s.ensure_conversation("thread-1")
    return s


class TestSaveMessageIsLoud:
    @pytest.mark.asyncio
    async def test_seq_collision_raises(self, store):
        """A real UNIQUE(thread_id, seq) violation reaches the caller."""
        await store.save_message("thread-1", 0, "user", json.dumps("Hello"))

        with pytest.raises(ConversationPersistenceError) as exc:
            await store.save_message("thread-1", 0, "user", json.dumps("Hello again"))

        assert "seq=0" in str(exc.value)
        assert exc.value.__cause__ is not None

    @pytest.mark.asyncio
    async def test_failed_write_does_not_bump_turn_count(self, store, db):
        await store.save_message("thread-1", 0, "user", json.dumps("Hello"))
        with pytest.raises(ConversationPersistenceError):
            await store.save_message("thread-1", 0, "user", json.dumps("dup"))

        count = await db.fetchval(
            "SELECT turn_count FROM conversations WHERE thread_id = ?", ("thread-1",)
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_first_write_failing_aborts_the_pair(self, store):
        """The turn stays atomic: a lost user message stops the assistant write.

        This is the shape the caller in ExecutionThread relies on. Before the
        fix both writes ran unconditionally and the store ended up holding an
        answer with no question.
        """
        await store.save_message("thread-1", 5, "user", json.dumps("occupied"))

        async def save_turn(seq: int) -> None:
            await store.save_message("thread-1", seq, "user", json.dumps("q"))
            await store.save_message("thread-1", seq + 1, "assistant", json.dumps("a"))

        with pytest.raises(ConversationPersistenceError):
            await save_turn(5)

        msgs = await store.load_messages("thread-1")
        assert [m["seq"] for m in msgs] == [5]
        assert [m["role"] for m in msgs] == ["user"]


class TestNextSeqComesFromTheDatabase:
    @pytest.mark.asyncio
    async def test_empty_thread_starts_at_zero(self, store):
        assert await store.next_seq("thread-1") == 0

    @pytest.mark.asyncio
    async def test_follows_max_seq(self, store):
        await store.save_message("thread-1", 0, "user", json.dumps("a"))
        await store.save_message("thread-1", 1, "assistant", json.dumps("b"))
        assert await store.next_seq("thread-1") == 2

    @pytest.mark.asyncio
    async def test_corrupt_highest_row_does_not_rewind_the_counter(self, store, db):
        """load_messages skips corrupt rows; next_seq must not.

        A corrupt row at the highest seq used to make the caller reuse that
        seq, so the next write collided and was discarded with only a log line.
        """
        await store.save_message("thread-1", 0, "user", json.dumps("a"))
        await db.execute(
            "INSERT INTO conversation_messages (thread_id, seq, role, content_json)"
            " VALUES (?, ?, ?, ?)",
            ("thread-1", 1, "assistant", "{invalid json!!!"),
        )
        await db.commit()

        visible = await store.load_messages("thread-1")
        assert [m["seq"] for m in visible] == [0]

        assert await store.next_seq("thread-1") == 2
        await store.save_message("thread-1", 2, "user", json.dumps("c"))

    @pytest.mark.asyncio
    async def test_read_failure_raises_instead_of_guessing_zero(self, store):
        async def broken_fetchval(*a, **k):
            raise RuntimeError("disk is gone")

        store.db.fetchval = broken_fetchval

        with pytest.raises(ConversationPersistenceError):
            await store.next_seq("thread-1")
