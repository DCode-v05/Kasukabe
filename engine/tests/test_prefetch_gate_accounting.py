"""Speculative prefetch: the trivial-input gate and the accounting fix.

The defect pair under test:
1. The embed round-trip beats the triage hop, so cancelled prefetches had
   already billed an embedding - the gate keeps chatter from starting one.
2. The prefetch ran before thread.execute() bound its UsageAccumulator, so
   the embed's tokens appeared in no engine-side total - prefetch_memory now
   binds its own accumulator and ships the count on the bundle, which the
   thread merges into its per-turn tracker.
"""

from __future__ import annotations

import asyncio

import pytest

from src.engine_core.coordinator import Coordinator, is_trivial_input
from src.engine_core.thread import merge_prefetch_usage
from src.herald.usage_tracker import (
    UsageAccumulator,
    record_embedding_usage,
    usage_tracker_var,
)


class TestTrivialInputGate:
    @pytest.mark.parametrize(
        "message",
        [
            "hi",
            "Hello!",
            "hey",
            "thanks",
            "Thank you.",
            "ok",
            "good morning",
            "Good Morning!!",
            "bye",
            "short one",
        ],
    )
    def test_trivial(self, message):
        assert is_trivial_input(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "fix the failing test in tests/test_config.py",
            "what changed in the catalog sync module?",
            "hello world program in rust please",
            "summarize yesterday's incident",
        ],
    )
    def test_real_work(self, message):
        assert is_trivial_input(message) is False


class SpySearch:
    """Search stand-in: counts embed calls, records usage like the real
    embedder does (via the ambient tracker contextvar)."""

    def __init__(self) -> None:
        self.embed_calls = 0

    async def embed(self, text: str):
        self.embed_calls += 1
        record_embedding_usage(7)
        return [0.0, 1.0]

    async def episodes(self, *a, **k):
        return []

    async def knowledge(self, *a, **k):
        return []

    async def social(self, *a, **k):
        return []


def make_coordinator_stub(search) -> object:
    class Stub:
        pass

    stub = Stub()
    stub.search = search
    return stub


class TestPrefetchAccounting:
    def test_prefetch_records_into_its_own_accumulator(self):
        async def run():
            search = SpySearch()
            stub = make_coordinator_stub(search)
            usage = UsageAccumulator()
            bundle = await Coordinator.prefetch_memory(stub, "a real message", usage=usage)
            return search, usage, bundle

        search, usage, bundle = asyncio.run(run())
        assert search.embed_calls == 1
        assert usage.embedding_tokens == 7
        assert bundle["embedding_tokens"] == 7

    def test_prefetch_does_not_leak_tracker_binding(self):
        async def run():
            stub = make_coordinator_stub(SpySearch())
            await Coordinator.prefetch_memory(stub, "a real message", usage=UsageAccumulator())
            return usage_tracker_var.get(None)

        assert asyncio.run(run()) is None

    def test_thread_merges_bundle_tokens_into_current_tracker(self):
        tracker = UsageAccumulator()
        token = usage_tracker_var.set(tracker)
        try:
            merge_prefetch_usage({"embedding_tokens": 42})
            assert tracker.embedding_tokens == 42
            merge_prefetch_usage({"embedding_tokens": 0})
            merge_prefetch_usage(None)
            assert tracker.embedding_tokens == 42
        finally:
            usage_tracker_var.reset(token)

    def test_no_search_returns_empty_bundle_with_zero_tokens(self):
        async def run():
            stub = make_coordinator_stub(None)
            return await Coordinator.prefetch_memory(stub, "msg", usage=UsageAccumulator())

        bundle = asyncio.run(run())
        assert bundle["embedding_tokens"] == 0
        assert bundle["query_embedding"] is None
