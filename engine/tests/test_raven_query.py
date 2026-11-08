"""Tests for observability query DSL (src/raven/query.py)."""

import pytest
import time

from src.raven.events import TokenUsageEvent
from src.raven.query import query
from src.raven.store import RavenStore


async def seed_usage(store: RavenStore, model: str, tokens: int, ts: float) -> None:
    await store.track(
        TokenUsageEvent(
            model=model,
            input_tokens=tokens,
            output_tokens=0,
            timestamp=ts,
        )
    )


class TestQuery:
    @pytest.mark.asyncio
    async def test_simple_total(self, db):
        store = RavenStore(db)
        now = time.time()
        await seed_usage(store, "m1", 100, now - 60)
        await seed_usage(store, "m1", 200, now - 30)
        result = await query(db, metric="tokens_used", window="1h")
        assert result.total == 300

    @pytest.mark.asyncio
    async def test_group_by_model(self, db):
        store = RavenStore(db)
        now = time.time()
        await seed_usage(store, "m1", 100, now - 60)
        await seed_usage(store, "m2", 200, now - 30)
        result = await query(db, metric="tokens_used", window="1h", group_by=["model"])
        assert result.total == 300
        models = {g["model"]: g["total"] for g in result.groups}
        assert models["m1"] == 100
        assert models["m2"] == 200

    @pytest.mark.asyncio
    async def test_window_excludes_old(self, db):
        store = RavenStore(db)
        now = time.time()
        await seed_usage(store, "m1", 100, now - 7200)
        await seed_usage(store, "m1", 200, now - 60)
        result = await query(db, metric="tokens_used", window="1h")
        assert result.total == 200

    @pytest.mark.asyncio
    async def test_explicit_window(self, db):
        store = RavenStore(db)
        await seed_usage(store, "m1", 100, 1712840000.0)
        await seed_usage(store, "m1", 200, 1712840001.0)
        result = await query(
            db, metric="tokens_used", window={"since": 1712839999, "until": 1712840002}
        )
        assert result.total == 300

    @pytest.mark.asyncio
    async def test_empty_result(self, db):
        result = await query(db, metric="tokens_used", window="1h")
        assert result.total == 0
        assert result.groups == []

    @pytest.mark.asyncio
    async def test_rollup_to_dict(self, db):
        store = RavenStore(db)
        now = time.time()
        await seed_usage(store, "m1", 100, now - 10)
        result = await query(db, metric="tokens_used", window="1h")
        d = result.to_dict()
        assert "metric" in d
        assert "total" in d
        assert d["metric"] == "tokens_used"

    @pytest.mark.asyncio
    async def test_invalid_window_raises(self, db):
        with pytest.raises(ValueError, match="unknown window"):
            await query(db, metric="tokens_used", window="99x")
