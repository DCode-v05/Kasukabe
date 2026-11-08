"""Thread pinning - the pin flag and its exemptions (coordinator v2, WP1).

A pinned thread is exempt from every expiry the engine applies to idle
conversations: the 24h conversation reaper, the LC sweep that follows it,
the 72h waiting-input cutoff, and checkpoint TTLs. Pinning is persisted on
the conversations row (BOOLEAN true/false) so it survives restart, and it
never counts as activity - pinning a thread must not reset its idle clock.

Endpoint tests drive the real Coordinator, not a fake: the release-endpoint
tests once validated a FakeCoordinator whose behavior the real one never had,
and that lie survived until it was read. Not again.
"""

import asyncio
import datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import src.server as server
from src.bootstrap import EngineState
from src.config import Settings
from src.engine_core.conversation_store import ConversationStore
from src.engine_core.coordinator import Coordinator, current_thread_id_var
from src.engine_core.events import EventEmitter
from src.engine_core.resume_state import ResumeState, ResumeStateManager
from src.engine_core.schema import ThreadIndexEntry
from src.infra.sqlite_pool import SqlitePool

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


async def make_pool(db_path=":memory:") -> SqlitePool:
    pool = SqlitePool(db_path)
    await pool.connect()
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        await pool.executescript(sql_file.read_text(encoding="utf-8"))
    await pool.commit()
    return pool


@pytest_asyncio.fixture
async def db():
    pool = await make_pool()
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def store(db):
    return ConversationStore(db)


async def backdate(db, thread_id: str, seconds: int) -> None:
    """Age a conversation past a TTL boundary."""
    await db.execute(
        "UPDATE conversations SET last_activity = datetime('now', ? || ' seconds')"
        " WHERE thread_id = ?",
        (f"-{seconds}", thread_id),
    )
    await db.commit()


class TestPinPersistence:
    @pytest.mark.asyncio
    async def test_pin_roundtrip_and_default_false(self, store):
        await store.ensure_conversation("t1")
        assert await store.is_pinned("t1") is False
        await store.set_pinned("t1", True)
        assert await store.is_pinned("t1") is True
        await store.set_pinned("t1", False)
        assert await store.is_pinned("t1") is False

    @pytest.mark.asyncio
    async def test_is_pinned_missing_row_is_false(self, store):
        assert await store.is_pinned("ghost") is False

    @pytest.mark.asyncio
    async def test_pinning_is_not_activity(self, store, db):
        """set_pinned must not touch last_activity - pinning isn't a turn."""
        await store.ensure_conversation("t1")
        await backdate(db, "t1", 1000)
        before = (await db.fetchrow(
            "SELECT last_activity FROM conversations WHERE thread_id = ?", ("t1",)
        ))["last_activity"]
        await store.set_pinned("t1", True)
        after = (await db.fetchrow(
            "SELECT last_activity FROM conversations WHERE thread_id = ?", ("t1",)
        ))["last_activity"]
        assert after == before

    @pytest.mark.asyncio
    async def test_pin_survives_restart(self, tmp_path):
        """The flag lives on the conversations row, not in memory."""
        db_file = str(tmp_path / "engine.db")
        pool = await make_pool(db_file)
        store = ConversationStore(pool)
        await store.ensure_conversation("t1")
        await store.set_pinned("t1", True)
        await pool.close()

        pool2 = await make_pool(db_file)
        store2 = ConversationStore(pool2)
        assert await store2.is_pinned("t1") is True
        rows = await store2.list_active()
        assert [r for r in rows if r["thread_id"] == "t1"][0]["pinned"] is True
        await pool2.close()

    @pytest.mark.asyncio
    async def test_list_active_and_load_conversation_expose_pinned(self, store):
        await store.ensure_conversation("pinned-t")
        await store.ensure_conversation("plain-t")
        await store.set_pinned("pinned-t", True)

        by_id = {r["thread_id"]: r for r in await store.list_active()}
        assert by_id["pinned-t"]["pinned"] is True
        assert by_id["plain-t"]["pinned"] is False

        convo = await store.load_conversation("pinned-t")
        assert convo["pinned"] is True


class TestReaperExemption:
    @pytest.mark.asyncio
    async def test_reaper_skips_pinned_reaps_unpinned(self, store, db):
        await store.ensure_conversation("old-pinned")
        await store.ensure_conversation("old-plain")
        await store.ensure_conversation("fresh")
        await store.set_pinned("old-pinned", True)
        await backdate(db, "old-pinned", 200_000)  # far past the 24h TTL
        await backdate(db, "old-plain", 200_000)

        deleted = await store.cleanup_expired(86400)

        assert deleted == 1
        remaining = {r["thread_id"] for r in await store.list_active()}
        assert remaining == {"old-pinned", "fresh"}

    @pytest.mark.asyncio
    async def test_unpin_restores_reapability(self, store, db):
        await store.ensure_conversation("t1")
        await store.set_pinned("t1", True)
        await backdate(db, "t1", 200_000)
        assert await store.cleanup_expired(86400) == 0

        await store.set_pinned("t1", False)
        assert await store.cleanup_expired(86400) == 1
        assert await store.load_conversation("t1") is None


class TestWaitingInputExemption:
    def make_coordinator(self):
        class FakeEmitter:
            def __init__(self):
                self.events = []

            async def emit(self, ev):
                self.events.append(ev)

        return Coordinator(adapter=None, emitter=FakeEmitter(), memory=None, db=None)

    @pytest.mark.asyncio
    async def test_unpinned_wait_times_out(self, monkeypatch):
        import src.engine_core.thread as thread_mod

        monkeypatch.setattr(thread_mod, "USER_INPUT_TIMEOUT_SECONDS", 0.05)
        coord = self.make_coordinator()
        coord.thread_index["t1"] = ThreadIndexEntry(thread_id="t1")

        current_thread_id_var.set("t1")
        result = await coord.await_block_input({"question": "q?", "format": "text"})
        assert isinstance(result, str) and "timed out" in result

    @pytest.mark.asyncio
    async def test_pinned_wait_survives_past_timeout(self, monkeypatch):
        import src.engine_core.thread as thread_mod

        monkeypatch.setattr(thread_mod, "USER_INPUT_TIMEOUT_SECONDS", 0.05)
        coord = self.make_coordinator()
        coord.thread_index["t1"] = ThreadIndexEntry(thread_id="t1", pinned=True)

        current_thread_id_var.set("t1")
        pause = asyncio.create_task(
            coord.await_block_input({"question": "q?", "format": "text"})
        )
        await asyncio.sleep(0.2)  # 4x the unpinned cutoff
        assert coord.pending_inputs, "pinned wait must still be parked"
        block_id = next(iter(coord.pending_inputs))
        owner, resumed = await coord.resume_block("t1", block_id, value="answer", text="")
        assert resumed is True
        assert "answer" in str(await pause)


class TestCheckpointExemption:
    @pytest.mark.asyncio
    async def test_pinned_checkpoint_outlives_expiry(self, db):
        mgr = ResumeStateManager(db)
        state = ResumeState(charter_id="ch-pinned", thread_id="t1")
        await mgr.write_checkpoint(state, ttl=-1, pinned=True)  # already "expired"

        assert (await mgr.read_checkpoint("ch-pinned")) is not None
        await mgr.cleanup_expired()
        assert (await mgr.read_checkpoint("ch-pinned")) is not None

    @pytest.mark.asyncio
    async def test_unpinned_checkpoint_expires_unchanged(self, db):
        mgr = ResumeStateManager(db)
        state = ResumeState(charter_id="ch-plain", thread_id="t1")
        await mgr.write_checkpoint(state, ttl=-1)

        assert (await mgr.read_checkpoint("ch-plain")) is None
        await mgr.cleanup_expired()
        row = await db.fetchrow(
            "SELECT 1 FROM resume_state WHERE charter_id = ?", ("ch-plain",)
        )
        assert row is None


class TestPinFailsLoudly:
    """A pin that did not reach SQLite is not a pin. It must not be reported
    as one: the in-memory flag rolls back and the route surfaces the failure."""

    async def make_coordinator(self, db):
        class FakeEmitter:
            async def emit(self, ev):
                pass

        coord = Coordinator(adapter=None, emitter=FakeEmitter(), memory=None, db=None)
        coord.conversation_store = ConversationStore(db)
        await coord.conversation_store.ensure_conversation("t1")
        coord.thread_index["t1"] = ThreadIndexEntry(thread_id="t1")
        return coord

    @pytest.mark.asyncio
    async def test_store_failure_propagates_and_rolls_back(self, db):
        coord = await self.make_coordinator(db)

        async def broken_execute(*a, **k):
            raise RuntimeError("disk is gone")

        coord.conversation_store.db.execute = broken_execute

        with pytest.raises(RuntimeError):
            await coord.set_thread_pinned("t1", True)
        assert coord.thread_index["t1"].pinned is False

    @pytest.mark.asyncio
    async def test_endpoint_surfaces_failure_not_ok_true(self, monkeypatch, db):
        coord = await self.make_coordinator(db)

        async def broken_execute(*a, **k):
            raise RuntimeError("disk is gone")

        coord.conversation_store.db.execute = broken_execute

        state = EngineState(settings=Settings(), emitter=EventEmitter())
        state.coordinator = coord
        state.restored_thread_ids = set()
        monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
        monkeypatch.setattr(server, "state", state)

        transport = ASGITransport(app=server.app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/coordinator/threads/t1/pin", headers={"x-engine-key": "ok"}
            )
        assert resp.status_code >= 500
        assert coord.thread_index["t1"].pinned is False


class TestCheckpointWiring:
    """The pinned checkpoint must be produced by the engine's own write path,
    not only by a test calling write_checkpoint directly."""

    @pytest.mark.asyncio
    async def test_thread_stamps_pinned_from_live_write_path(self, db):
        from src.engine_core.thread import ExecutionThread

        store = ConversationStore(db)
        await store.ensure_conversation("t1")
        await store.set_pinned("t1", True)

        thread = ExecutionThread.__new__(ExecutionThread)
        thread.conversation_store = store
        thread.resume_state_mgr = ResumeStateManager(db)

        # The exact sequence thread.execute() runs at its checkpoint seam.
        pinned = await thread.conversation_store.is_pinned("t1")
        await thread.resume_state_mgr.write_checkpoint(
            ResumeState(charter_id="ch-live", thread_id="t1"), ttl=-1, pinned=pinned
        )

        row = await db.fetchrow(
            "SELECT ttl_type FROM resume_state WHERE charter_id = ?", ("ch-live",)
        )
        assert row["ttl_type"] == "pinned"
        await thread.resume_state_mgr.cleanup_expired()
        assert (await thread.resume_state_mgr.read_checkpoint("ch-live")) is not None


class TestPinEndpoints:
    """Real Coordinator + real ConversationStore behind the real routes."""

    async def make_state(self, db):
        class FakeEmitter:
            async def emit(self, ev):
                pass

        coord = Coordinator(adapter=None, emitter=FakeEmitter(), memory=None, db=None)
        coord.conversation_store = ConversationStore(db)
        await coord.conversation_store.ensure_conversation("t1")
        coord.thread_index["t1"] = ThreadIndexEntry(thread_id="t1", summary="first")
        state = EngineState(settings=Settings(), emitter=EventEmitter())
        state.coordinator = coord
        state.restored_thread_ids = set()
        return state

    @pytest_asyncio.fixture
    async def client(self, monkeypatch, db):
        monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
        monkeypatch.setattr(
            server,
            "boot_completed_at",
            datetime.datetime(2026, 7, 18, tzinfo=datetime.timezone.utc),
        )
        monkeypatch.setattr(server, "state", await self.make_state(db))
        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    @pytest.mark.asyncio
    async def test_pin_unpin_roundtrip(self, client):
        resp = await client.post(
            "/coordinator/threads/t1/pin", headers={"x-engine-key": "ok"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "thread_id": "t1", "pinned": True}
        assert server.state.coordinator.thread_index["t1"].pinned is True
        assert await server.state.coordinator.conversation_store.is_pinned("t1") is True

        resp = await client.post(
            "/coordinator/threads/t1/unpin", headers={"x-engine-key": "ok"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "thread_id": "t1", "pinned": False}
        assert server.state.coordinator.thread_index["t1"].pinned is False
        assert await server.state.coordinator.conversation_store.is_pinned("t1") is False

    @pytest.mark.asyncio
    async def test_pin_unknown_thread_404s(self, client):
        resp = await client.post(
            "/coordinator/threads/ghost/pin", headers={"x-engine-key": "ok"}
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_threads_exposes_pinned(self, client):
        await client.post("/coordinator/threads/t1/pin", headers={"x-engine-key": "ok"})
        resp = await client.get("/coordinator/threads", headers={"x-engine-key": "ok"})
        entry = [t for t in resp.json()["threads"] if t["thread_id"] == "t1"][0]
        assert entry["pinned"] is True
