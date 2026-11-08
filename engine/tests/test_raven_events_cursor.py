"""An incremental poller must not be able to skip events it can never re-request.

/raven/events combined a forward-only `since` cursor with
`ORDER BY timestamp DESC LIMIT ?`, so the LIMIT discarded the events CLOSEST to
`since` and kept the newest. A consumer polling every 30s to mirror engine
telemetry into an external audit store advanced its cursor to the newest
timestamp it received; everything between the old cursor and the start of the
returned page became unaddressable, because the endpoint accepts only a lower
bound. The response was `{events: [...]}` with no has_more, no next_since and
no total, so a full page of exactly 100 was indistinguishable from "exactly 100
events existed" and the gap was permanent.

Ascending order makes a truncated read delayed instead of lossy.
"""

import pytest

from src.raven.events import TokenUsageEvent
from src.raven.store import RavenStore


async def seed(store: RavenStore, count: int, start: float = 1000.0) -> None:
    for i in range(count):
        await store.track(TokenUsageEvent(model="m", input_tokens=1, timestamp=start + i))


class TestOrdering:
    @pytest.mark.asyncio
    async def test_a_truncated_page_returns_the_OLDEST_events(self, db):
        store = RavenStore(db)
        await seed(store, 10)
        evts = await store.events(since=0.0, limit=3)
        assert [e.timestamp for e in evts] == [1000.0, 1001.0, 1002.0]

    @pytest.mark.asyncio
    async def test_polling_forward_loses_nothing(self, db):
        """The full loop: 500 events arrive in one interval, the consumer polls
        with limit=100 and advances its cursor to the newest it received."""
        store = RavenStore(db)
        await seed(store, 500)

        seen: list[float] = []
        since = 0.0
        for _ in range(10):
            evts, has_more = await store.events_page(since=since, limit=100)
            if not evts:
                break
            seen.extend(e.timestamp for e in evts)
            since = evts[-1].timestamp
            if not has_more:
                break

        assert len(seen) == 500
        assert seen == sorted(seen)
        assert len(set(seen)) == 500

    @pytest.mark.asyncio
    async def test_the_cursor_never_jumps_a_window(self, db):
        """The precise regression: one poll must not advance `since` past
        events it was not given."""
        store = RavenStore(db)
        await seed(store, 300)

        evts = await store.events(since=0.0, limit=100)
        cursor = evts[-1].timestamp
        skipped = [
            e.timestamp
            for e in await store.events(since=0.0, limit=500)
            if e.timestamp <= cursor and e.timestamp not in {x.timestamp for x in evts}
        ]
        assert skipped == []


class TestHasMore:
    @pytest.mark.asyncio
    async def test_a_cut_short_page_says_so(self, db):
        store = RavenStore(db)
        await seed(store, 10)
        _, has_more = await store.events_page(since=0.0, limit=3)
        assert has_more is True

    @pytest.mark.asyncio
    async def test_an_exactly_full_page_is_distinguishable_from_a_cut_one(self, db):
        """This is the case a full page alone can never report."""
        store = RavenStore(db)
        await seed(store, 5)
        evts, has_more = await store.events_page(since=0.0, limit=5)
        assert len(evts) == 5
        assert has_more is False

    @pytest.mark.asyncio
    async def test_an_empty_page_reports_no_more(self, db):
        store = RavenStore(db)
        evts, has_more = await store.events_page(since=0.0, limit=10)
        assert evts == []
        assert has_more is False

    @pytest.mark.asyncio
    async def test_kind_filtering_still_works(self, db):
        store = RavenStore(db)
        await seed(store, 10)
        evts, has_more = await store.events_page(since=0.0, kind="token_usage", limit=3)
        assert len(evts) == 3
        assert has_more is True
        empty, _ = await store.events_page(since=0.0, kind="tool_call", limit=3)
        assert empty == []


class TestOverTheRoute:
    @pytest.mark.asyncio
    async def test_the_response_carries_the_cursor(self, db, monkeypatch):
        from httpx import ASGITransport, AsyncClient

        from src import server
        from src.raven import api as raven_api

        store = RavenStore(db)
        await seed(store, 10)

        raven_api.init(db)
        monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
        try:
            transport = ASGITransport(app=server.app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/raven/events",
                    json={"since": 0.0, "limit": 3},
                    headers={"x-engine-key": "k"},
                )
            assert resp.status_code == 200
            body = resp.json()
            assert len(body["events"]) == 3
            assert body["has_more"] is True
            assert body["next_since"] == 1002.0
        finally:
            raven_api.store = None
            raven_api.db_pool = None

    @pytest.mark.asyncio
    async def test_the_returned_cursor_drives_the_next_page(self, db, monkeypatch):
        from httpx import ASGITransport, AsyncClient

        from src import server
        from src.raven import api as raven_api

        store = RavenStore(db)
        await seed(store, 7)

        raven_api.init(db)
        monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
        try:
            transport = ASGITransport(app=server.app, raise_app_exceptions=False)
            seen: list[float] = []
            since = 0.0
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for _ in range(5):
                    resp = await client.post(
                        "/raven/events",
                        json={"since": since, "limit": 3},
                        headers={"x-engine-key": "k"},
                    )
                    body = resp.json()
                    seen.extend(e["timestamp"] for e in body["events"])
                    since = body["next_since"]
                    if not body["has_more"]:
                        break
            assert len(seen) == 7
            assert seen == sorted(seen)
        finally:
            raven_api.store = None
            raven_api.db_pool = None
