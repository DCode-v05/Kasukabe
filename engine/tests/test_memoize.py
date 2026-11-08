"""src/infra/memoize.py - the one shared TTL cache.

Per the consolidation contract: expiry (backdated, no sleeps), invalidate
semantics, error-not-cached, stampede dedup (loader runs exactly once for N
concurrent cold-missers), and the invalidation identity guard (a load in
flight when invalidate() lands must not store its result).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.infra.memoize import CacheEntry, TTLCache


def backdate(cache: TTLCache, key: str) -> None:
    cache.entries[key].expires_at = time.monotonic() - 1


class TestCacheEntryAndExpiry:
    def test_readable_before_ttl_none_after(self):
        cache = TTLCache(default_ttl=900)
        cache.set("k", "value")
        assert cache.get("k") == "value"
        backdate(cache, "k")
        assert cache.get("k") is None

    def test_missing_key_is_none(self):
        assert TTLCache().get("nope") is None

    def test_per_set_ttl_override(self):
        cache = TTLCache(default_ttl=900)
        cache.set("k", "v", ttl=1)
        entry = cache.entries["k"]
        assert entry.expires_at - time.monotonic() < 2

    def test_cache_entry_slots(self):
        entry = CacheEntry("data", ttl=900)
        with pytest.raises(AttributeError):
            entry.other = 1


class TestInvalidate:
    def test_empty_prefix_clears_all(self):
        cache = TTLCache()
        cache.set("a:1", 1)
        cache.set("b:2", 2)
        cache.invalidate("")
        assert len(cache) == 0

    def test_prefix_removes_matching_only(self):
        cache = TTLCache()
        cache.set("conn1:read", "allow")
        cache.set("conn1:write", "ask")
        cache.set("conn2:read", "deny")
        cache.invalidate("conn1:")
        assert cache.get("conn1:read") is None
        assert cache.get("conn1:write") is None
        assert cache.get("conn2:read") == "deny"


class TestGetOrLoad:
    def test_error_not_cached(self):
        cache = TTLCache()
        calls = {"n": 0}

        async def failing_loader():
            calls["n"] += 1
            raise RuntimeError("load failed")

        async def run():
            with pytest.raises(RuntimeError):
                await cache.get_or_load("k", failing_loader)
            with pytest.raises(RuntimeError):
                await cache.get_or_load("k", failing_loader)

        asyncio.run(run())
        assert calls["n"] == 2  # retried, not served from cache
        assert cache.get("k") is None
        assert cache.in_flight == {}

    def test_stampede_loader_runs_once(self):
        cache = TTLCache()
        calls = {"n": 0}

        async def slow_loader():
            calls["n"] += 1
            await asyncio.sleep(0.02)
            return "loaded"

        async def run():
            results = await asyncio.gather(
                *(cache.get_or_load("k", slow_loader) for _ in range(8))
            )
            return results

        results = asyncio.run(run())
        assert results == ["loaded"] * 8
        assert calls["n"] == 1
        assert cache.get("k") == "loaded"
        assert cache.in_flight == {}

    def test_identity_guard_invalidate_mid_flight(self):
        cache = TTLCache()
        started = asyncio.Event()

        async def slow_loader():
            started.set()
            await asyncio.sleep(0.05)
            return "stale-result"

        async def run():
            task = asyncio.create_task(cache.get_or_load("k", slow_loader))
            await started.wait()
            cache.invalidate("")  # intent: discard what you knew AND what you're learning
            result = await task
            return result

        result = asyncio.run(run())
        assert result == "stale-result"  # the caller still gets its value...
        assert cache.get("k") is None  # ...but nothing was stored
        assert cache.in_flight == {}

    def test_cache_hit_skips_loader(self):
        cache = TTLCache()
        cache.set("k", "cached")

        async def loader():
            raise AssertionError("must not be called on a hit")

        assert asyncio.run(cache.get_or_load("k", loader)) == "cached"
