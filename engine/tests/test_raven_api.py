"""Tests for observability public API (src/raven/api.py).

These exercise the functions directly as engine-internal code calls them
(plain Python imports). The typed ``/raven/*`` routes are covered separately.
"""

import pytest
import time

from src.raven import api as raven_api
from src.raven.events import TokenUsageEvent, ToolCallEvent


@pytest.fixture(autouse=True)
def init_raven(db):
    """Initialize the observability subsystem with test db before each test."""
    raven_api.init(db)
    yield
    raven_api.store = None
    raven_api.db_pool = None


class TestTrack:
    @pytest.mark.asyncio
    async def test_track_typed_event(self, db):
        evt = TokenUsageEvent(model="m", input_tokens=100, timestamp=time.time())
        await raven_api.track(evt)
        result = await raven_api.events(since=0.0)
        assert len(result) == 1
        assert result[0]["kind"] == "token_usage"

    @pytest.mark.asyncio
    async def test_track_dict_event(self, db):
        await raven_api.track(
            {
                "kind": "token_usage",
                "model": "m",
                "input_tokens": 50,
                "output_tokens": 10,
                "timestamp": time.time(),
            }
        )
        result = await raven_api.events(since=0.0)
        assert len(result) == 1


class TestTrackUntyped:
    @pytest.mark.asyncio
    async def test_untyped_persists(self, db):
        await raven_api.track_untyped("custom.ping", {"version": "1.0"})
        result = await raven_api.events(since=0.0, kind="custom.ping")
        assert len(result) == 1


class TestQuery:
    @pytest.mark.asyncio
    async def test_query_returns_dict(self, db):
        evt = TokenUsageEvent(model="m", input_tokens=100, output_tokens=50, timestamp=time.time())
        await raven_api.track(evt)
        result = await raven_api.query(metric="tokens_used", window="1h")
        assert isinstance(result, dict)
        assert result["total"] == 150

    @pytest.mark.asyncio
    async def test_query_group_by_model(self, db):
        now = time.time()
        await raven_api.track(TokenUsageEvent(model="m1", input_tokens=100, timestamp=now))
        await raven_api.track(TokenUsageEvent(model="m2", input_tokens=200, timestamp=now))
        result = await raven_api.query(metric="tokens_used", window="1h", group_by=["model"])
        assert result["total"] == 300
        assert len(result["groups"]) == 2


class TestEvents:
    @pytest.mark.asyncio
    async def test_events_returns_list_of_dicts(self, db):
        await raven_api.track(ToolCallEvent(tool_name="bash", timestamp=time.time()))
        result = await raven_api.events(since=0.0)
        assert isinstance(result, list)
        assert isinstance(result[0], dict)
        assert result[0]["kind"] == "tool_call"

    @pytest.mark.asyncio
    async def test_events_limit(self, db):
        now = time.time()
        for i in range(5):
            await raven_api.track(TokenUsageEvent(model="m", timestamp=now + i))
        result = await raven_api.events(since=0.0, limit=2)
        assert len(result) == 2


class TestRetentionNow:
    @pytest.mark.asyncio
    async def test_manual_retention_returns_stats(self, db):
        await raven_api.track(TokenUsageEvent(model="m", input_tokens=100, timestamp=100.0))
        result = await raven_api.run_retention_now(raw_events_ttl_days=0, counters_ttl_days=9999)
        assert isinstance(result, dict)
        assert result["rollup_count"] >= 1
        assert result["drop_count"] >= 1
