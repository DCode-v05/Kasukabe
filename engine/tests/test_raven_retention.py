"""Tests for observability retention job (src/raven/retention.py)."""

import pytest

from src.raven.events import TokenUsageEvent, ToolCallEvent
from src.raven.retention import RetentionPolicy, run_retention
from src.raven.store import RavenStore


class TestRetention:
    @pytest.mark.asyncio
    async def test_old_events_rolled_up_and_dropped(self, db):
        store = RavenStore(db)
        old_ts = 100.0
        await store.track(
            TokenUsageEvent(model="m", input_tokens=100, output_tokens=50, timestamp=old_ts)
        )
        await store.track(
            TokenUsageEvent(model="m", input_tokens=200, output_tokens=0, timestamp=old_ts + 1)
        )

        policy = RetentionPolicy(raw_events_ttl_days=0, counters_ttl_days=9999)
        result = await run_retention(store, policy)

        assert result.rollup_count == 2
        assert result.drop_count == 2
        remaining = await store.events(since=0.0)
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_recent_events_untouched(self, db):
        store = RavenStore(db)
        import time

        now = time.time()
        await store.track(TokenUsageEvent(model="m", input_tokens=100, timestamp=now))

        policy = RetentionPolicy(raw_events_ttl_days=30, counters_ttl_days=365)
        result = await run_retention(store, policy)

        assert result.rollup_count == 0
        assert result.drop_count == 0
        remaining = await store.events(since=0.0)
        assert len(remaining) == 1

    @pytest.mark.asyncio
    async def test_non_token_events_dropped_without_rollup(self, db):
        store = RavenStore(db)
        await store.track(ToolCallEvent(tool_name="bash", timestamp=100.0))

        policy = RetentionPolicy(raw_events_ttl_days=0, counters_ttl_days=9999)
        result = await run_retention(store, policy)

        assert result.rollup_count == 0
        assert result.drop_count == 1

    @pytest.mark.asyncio
    async def test_old_counters_dropped(self, db):
        store = RavenStore(db)
        await store.track(TokenUsageEvent(model="m", input_tokens=100, timestamp=100.0))

        policy = RetentionPolicy(raw_events_ttl_days=0, counters_ttl_days=0)
        result = await run_retention(store, policy)

        assert result.counters_dropped >= 1

    @pytest.mark.asyncio
    async def test_idempotent(self, db):
        store = RavenStore(db)
        await store.track(TokenUsageEvent(model="m", input_tokens=100, timestamp=100.0))

        policy = RetentionPolicy(raw_events_ttl_days=0, counters_ttl_days=9999)
        r1 = await run_retention(store, policy)
        r2 = await run_retention(store, policy)

        assert r1.rollup_count == 1
        assert r1.drop_count == 1
        assert r2.rollup_count == 0
        assert r2.drop_count == 0

    @pytest.mark.asyncio
    async def test_result_to_dict(self, db):
        store = RavenStore(db)
        policy = RetentionPolicy()
        result = await run_retention(store, policy)
        d = result.to_dict()
        assert "rollup_count" in d
        assert "drop_count" in d
        assert "counters_dropped" in d
        assert "duration_ms" in d
