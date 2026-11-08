"""A filter the aggregation cannot honour must not come back as a 200.

`filters` was a first-class field on the public request model, forwarded by the
route handler and the API facade, accepted by query() - and then never used.
The endpoint returned a well-formed Rollup whose `total` was the unfiltered
fleet-wide figure, so a budget alert attributed every model's spend to the one
model it filtered for and nothing anywhere said so.

Both branches are covered: the sub-day path reads obs_events (full payload, so
filters are applied), the day-or-larger path reads obs_counters_daily (only
`model` survived the rollup, so anything else is rejected).
"""

import time

import pytest

from src.raven.events import TokenUsageEvent, ToolCallEvent
from src.raven.query import query
from src.raven.store import RavenStore


async def seed_usage(store: RavenStore, model: str, tokens: int, ts: float) -> None:
    await store.track(
        TokenUsageEvent(model=model, input_tokens=tokens, output_tokens=0, timestamp=ts)
    )


class TestSubDayWindowAppliesFilters:
    """window="1h" resolves to query_exact over obs_events."""

    @pytest.mark.asyncio
    async def test_model_filter_narrows_the_total(self, db):
        store = RavenStore(db)
        now = time.time()
        await seed_usage(store, "claude-opus-4-8", 100, now - 60)
        await seed_usage(store, "claude-haiku-4-5", 900, now - 30)

        unfiltered = await query(db, metric="tokens_used", window="1h")
        assert unfiltered.total == 1000

        opus = await query(
            db,
            metric="tokens_used",
            window="1h",
            filters={"model": "claude-opus-4-8"},
        )
        assert opus.total == 100

    @pytest.mark.asyncio
    async def test_filter_also_narrows_the_groups(self, db):
        store = RavenStore(db)
        now = time.time()
        await seed_usage(store, "claude-opus-4-8", 100, now - 60)
        await seed_usage(store, "claude-haiku-4-5", 900, now - 30)

        result = await query(
            db,
            metric="tokens_used",
            window="1h",
            group_by=["model"],
            filters={"model": "claude-opus-4-8"},
        )
        assert result.total == 100
        assert [g["model"] for g in result.groups] == ["claude-opus-4-8"]

    @pytest.mark.asyncio
    async def test_unknown_filter_key_raises_instead_of_returning_the_full_total(self, db):
        store = RavenStore(db)
        await seed_usage(store, "claude-opus-4-8", 100, time.time() - 60)

        with pytest.raises(ValueError) as exc:
            await query(
                db, metric="tokens_used", window="1h", filters={"tenant": "acme"}
            )
        assert "tenant" in str(exc.value)

    @pytest.mark.asyncio
    async def test_a_non_token_metric_filters_on_its_own_payload(self, db):
        store = RavenStore(db)
        now = time.time()
        await store.track(ToolCallEvent(tool_name="bash", timestamp=now - 60))
        await store.track(ToolCallEvent(tool_name="file_read", timestamp=now - 30))

        # tool_name is a real field on ToolCallEvent, so it is accepted.
        result = await query(
            db, metric="tool_call", window="1h", filters={"tool_name": "bash"}
        )
        assert result.groups == []

        # model is not, and must be rejected rather than ignored.
        with pytest.raises(ValueError):
            await query(db, metric="tool_call", window="1h", filters={"model": "x"})


class TestDayWindowRejectsWhatItCannotHonour:
    """window="1d" resolves to query_counters over obs_counters_daily."""

    @pytest.mark.asyncio
    async def test_model_filter_narrows_the_total(self, db):
        store = RavenStore(db)
        now = time.time()
        await seed_usage(store, "claude-opus-4-8", 100, now - 60)
        await seed_usage(store, "claude-haiku-4-5", 900, now - 30)

        unfiltered = await query(db, metric="tokens_used", window="1d")
        assert unfiltered.total == 1000

        opus = await query(
            db,
            metric="tokens_used",
            window="1d",
            filters={"model": "claude-opus-4-8"},
        )
        assert opus.total == 100

    @pytest.mark.asyncio
    async def test_model_filter_narrows_grouped_results(self, db):
        store = RavenStore(db)
        now = time.time()
        await seed_usage(store, "claude-opus-4-8", 100, now - 60)
        await seed_usage(store, "claude-haiku-4-5", 900, now - 30)

        result = await query(
            db,
            metric="tokens_used",
            window="1d",
            group_by=["model"],
            filters={"model": "claude-opus-4-8"},
        )
        assert result.total == 100
        assert [g["model"] for g in result.groups] == ["claude-opus-4-8"]

    @pytest.mark.asyncio
    async def test_payload_filter_raises_rather_than_silently_widening(self, db):
        """agent_step_id is a real payload field but was aggregated away in the
        daily counters, so a day-window query cannot honour it."""
        store = RavenStore(db)
        await seed_usage(store, "claude-opus-4-8", 100, time.time() - 60)

        with pytest.raises(ValueError) as exc:
            await query(
                db,
                metric="tokens_used",
                window="1d",
                filters={"agent_step_id": "step-1"},
            )
        assert "agent_step_id" in str(exc.value)

    @pytest.mark.asyncio
    async def test_no_filters_is_unchanged(self, db):
        store = RavenStore(db)
        await seed_usage(store, "m1", 100, time.time() - 60)
        assert (await query(db, metric="tokens_used", window="1d")).total == 100
        assert (await query(db, metric="tokens_used", window="1d", filters={})).total == 100


class TestOverTheRoute:
    """The rejection must reach the client as a 4xx, not a 500 or a 200."""

    @pytest.mark.asyncio
    async def test_unsupported_filter_is_a_4xx(self, db, monkeypatch):
        from httpx import ASGITransport, AsyncClient

        from src import server
        from src.raven import api as raven_api

        raven_api.init(db)
        monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
        try:
            transport = ASGITransport(app=server.app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/raven/query",
                    json={
                        "metric": "tokens_used",
                        "window": "1d",
                        "filters": {"tenant": "acme"},
                    },
                    headers={"x-engine-key": "k"},
                )
            assert 400 <= resp.status_code < 500
            assert "tenant" in resp.text
        finally:
            raven_api.store = None
            raven_api.db_pool = None

    @pytest.mark.asyncio
    async def test_supported_filter_returns_the_narrowed_total(self, db, monkeypatch):
        from httpx import ASGITransport, AsyncClient

        from src import server
        from src.raven import api as raven_api

        store = RavenStore(db)
        now = time.time()
        await seed_usage(store, "claude-opus-4-8", 100, now - 60)
        await seed_usage(store, "claude-haiku-4-5", 900, now - 30)

        raven_api.init(db)
        monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
        try:
            transport = ASGITransport(app=server.app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/raven/query",
                    json={
                        "metric": "tokens_used",
                        "window": "1h",
                        "filters": {"model": "claude-opus-4-8"},
                    },
                    headers={"x-engine-key": "k"},
                )
            assert resp.status_code == 200
            assert resp.json()["total"] == 100
        finally:
            raven_api.store = None
            raven_api.db_pool = None
