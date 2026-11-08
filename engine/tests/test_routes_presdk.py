"""The /rpc replacement routes (pre-SDK): coordinator + raven + admin faces.

/rpc is retired; its survivors are rehomed as typed routes. These tests drive
the real app through ASGITransport with a valid x-engine-key and assert the
documented response shapes:

  GET  /coordinator/threads                       -> {boot_time, count, threads}
  POST /raven/track_untyped + POST /raven/events  -> {"ok": true} then the event

No mocks for the wire path - raven runs against the real in-memory ``db`` and the
coordinator exposes a real ``thread_index`` so the routes read genuine state.
"""

import datetime

import pytest
from httpx import AsyncClient, ASGITransport

import src.server as server
from src.bootstrap import EngineState
from src.config import Settings
from src.engine_core.events import EventEmitter
from src.engine_core.schema import ThreadIndexEntry
from src.raven import api as raven_api


class FakeCoordinator:
    """The coordinator surface these routes touch: the thread index they read."""

    def __init__(self, entries):
        self.thread_index = dict(entries)
        self.pending_inputs = {}


def make_state():
    emitter = EventEmitter()
    state = EngineState(settings=Settings(), emitter=emitter)
    entry = ThreadIndexEntry(
        thread_id="t1",
        summary="first thread",
        current_state="working",
        turn_count=3,
        last_activity="2026-06-07T00:00:00+00:00",
    )
    state.coordinator = FakeCoordinator({"t1": entry})
    state.restored_thread_ids = {"t1"}
    return state


@pytest.fixture(autouse=True)
def wire_server(monkeypatch, db):
    """Bypass key verification (like the other server tests), pin boot time,
    install a real EngineState, and init raven against the test db."""
    monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
    monkeypatch.setattr(
        server,
        "boot_completed_at",
        datetime.datetime(2026, 6, 7, tzinfo=datetime.timezone.utc),
    )
    monkeypatch.setattr(server, "state", make_state())
    raven_api.init(db)
    yield
    raven_api.store = None
    raven_api.db_pool = None


@pytest.mark.asyncio
async def test_coordinator_threads_shape():
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/coordinator/threads", headers={"x-engine-key": "ok"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"boot_time", "count", "threads"}
    assert body["boot_time"] == "2026-06-07T00:00:00+00:00"
    assert body["count"] == 1
    assert len(body["threads"]) == 1
    t = body["threads"][0]
    assert t["thread_id"] == "t1"
    assert t["turn_count"] == 3
    assert t["summary"] == "first thread"
    assert t["current_state"] == "working"
    assert t["restored_on_boot"] is True


@pytest.mark.asyncio
async def test_raven_track_untyped_then_events():
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        track = await client.post(
            "/raven/track_untyped",
            json={"name": "smoke_test", "metadata": {"k": "v"}},
            headers={"x-engine-key": "ok"},
        )
        assert track.status_code == 200
        assert track.json() == {"ok": True}

        events = await client.post(
            "/raven/events",
            json={"since": 0.0, "kind": "smoke_test", "limit": 10},
            headers={"x-engine-key": "ok"},
        )
    assert events.status_code == 200
    body = events.json()
    assert "events" in body
    assert len(body["events"]) >= 1
    assert body["events"][0]["kind"] == "smoke_test"


@pytest.mark.asyncio
async def test_rpc_route_is_retired_404():
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/rpc",
            json={"subsystem": "telemetry", "op": "query"},
            headers={"x-engine-key": "ok"},
        )
    assert resp.status_code == 404
