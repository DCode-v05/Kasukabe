"""Tests for observability store (src/raven/store.py)."""

import pytest

from src.raven.events import (
    TokenUsageEvent,
    ToolCallEvent,
    UntypedEvent,
)
from src.raven.store import RavenStore


class TestTrackAndRead:
    @pytest.mark.asyncio
    async def test_track_token_usage(self, db):
        store = RavenStore(db)
        evt = TokenUsageEvent(
            model="test-model", input_tokens=100, output_tokens=50, timestamp=1000.0
        )
        await store.track(evt)
        events = await store.events(since=0.0)
        assert len(events) == 1
        assert events[0].kind == "token_usage"

    @pytest.mark.asyncio
    async def test_track_tool_call(self, db):
        store = RavenStore(db)
        evt = ToolCallEvent(tool_name="bash", duration_ms=42.0, timestamp=1000.0)
        await store.track(evt)
        events = await store.events(since=0.0, kind="tool_call")
        assert len(events) == 1
        assert events[0].tool_name == "bash"

    @pytest.mark.asyncio
    async def test_track_untyped(self, db):
        store = RavenStore(db)
        evt = UntypedEvent(kind="custom.ping", metadata={"v": 1}, timestamp=1000.0)
        await store.track(evt)
        events = await store.events(since=0.0, kind="custom.ping")
        assert len(events) == 1
        assert events[0].kind == "custom.ping"

    @pytest.mark.asyncio
    async def test_events_since_filter(self, db):
        store = RavenStore(db)
        await store.track(TokenUsageEvent(model="m", timestamp=100.0))
        await store.track(TokenUsageEvent(model="m", timestamp=200.0))
        await store.track(TokenUsageEvent(model="m", timestamp=300.0))
        events = await store.events(since=150.0)
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_events_kind_filter(self, db):
        store = RavenStore(db)
        await store.track(TokenUsageEvent(model="m", timestamp=100.0))
        await store.track(ToolCallEvent(tool_name="t", timestamp=101.0))
        events = await store.events(since=0.0, kind="tool_call")
        assert len(events) == 1
        assert events[0].kind == "tool_call"

    @pytest.mark.asyncio
    async def test_events_limit(self, db):
        store = RavenStore(db)
        for i in range(10):
            await store.track(TokenUsageEvent(model="m", timestamp=float(i)))
        events = await store.events(since=0.0, limit=3)
        assert len(events) == 3


class TestDailyCounters:
    @pytest.mark.asyncio
    async def test_token_usage_increments_counter(self, db):
        store = RavenStore(db)
        await store.track(
            TokenUsageEvent(model="m1", input_tokens=100, output_tokens=50, timestamp=1712840000.0)
        )
        val = await store.counter("tokens_used", "2024-04-11", "m1")
        assert val == 150

    @pytest.mark.asyncio
    async def test_counter_accumulates(self, db):
        store = RavenStore(db)
        await store.track(
            TokenUsageEvent(model="m1", input_tokens=100, output_tokens=0, timestamp=1712840000.0)
        )
        await store.track(
            TokenUsageEvent(model="m1", input_tokens=200, output_tokens=0, timestamp=1712840001.0)
        )
        val = await store.counter("tokens_used", "2024-04-11", "m1")
        assert val == 300

    @pytest.mark.asyncio
    async def test_counter_per_model(self, db):
        store = RavenStore(db)
        await store.track(
            TokenUsageEvent(model="m1", input_tokens=100, output_tokens=0, timestamp=1712840000.0)
        )
        await store.track(
            TokenUsageEvent(model="m2", input_tokens=200, output_tokens=0, timestamp=1712840000.0)
        )
        assert await store.counter("tokens_used", "2024-04-11", "m1") == 100
        assert await store.counter("tokens_used", "2024-04-11", "m2") == 200

    @pytest.mark.asyncio
    async def test_counter_sum_all_models(self, db):
        store = RavenStore(db)
        await store.track(
            TokenUsageEvent(model="m1", input_tokens=100, output_tokens=0, timestamp=1712840000.0)
        )
        await store.track(
            TokenUsageEvent(model="m2", input_tokens=200, output_tokens=0, timestamp=1712840000.0)
        )
        total = await store.counter("tokens_used", "2024-04-11")
        assert total == 300

    @pytest.mark.asyncio
    async def test_non_token_event_no_counter(self, db):
        store = RavenStore(db)
        await store.track(ToolCallEvent(tool_name="bash", timestamp=1712840000.0))
        val = await store.counter("tokens_used", "2024-04-11")
        assert val == 0


class TestRetentionHelpers:
    @pytest.mark.asyncio
    async def test_drop_events_before(self, db):
        store = RavenStore(db)
        await store.track(TokenUsageEvent(model="m", timestamp=100.0))
        await store.track(TokenUsageEvent(model="m", timestamp=200.0))
        await store.track(TokenUsageEvent(model="m", timestamp=300.0))
        dropped = await store.drop_events_before(250.0)
        assert dropped == 2
        remaining = await store.events(since=0.0)
        assert len(remaining) == 1

    @pytest.mark.asyncio
    async def test_rollup_events_to_counters(self, db):
        store = RavenStore(db)
        await store.track(
            TokenUsageEvent(model="m", input_tokens=100, output_tokens=50, timestamp=100.0)
        )
        await store.track(
            TokenUsageEvent(model="m", input_tokens=200, output_tokens=0, timestamp=101.0)
        )
        count = await store.rollup_events_to_counters(before=200.0)
        assert count == 2
        val = await store.counter("tokens_used", "1970-01-01", "m")
        assert val >= 350

    @pytest.mark.asyncio
    async def test_drop_counters_before(self, db):
        store = RavenStore(db)
        await store.track(
            TokenUsageEvent(model="m", input_tokens=100, output_tokens=0, timestamp=1712840000.0)
        )
        dropped = await store.drop_counters_before("2025-01-01")
        assert dropped == 1
