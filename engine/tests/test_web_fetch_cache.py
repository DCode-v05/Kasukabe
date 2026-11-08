"""web_fetch catalog tool: 15-minute URL cache on successful fetches.

Loads the real catalog source (the same exec path hydrate_tools uses),
stubs httpx, and asserts: second fetch inside the TTL is served from cache
(one network call), expiry refetches, and error responses are never cached.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import types
from pathlib import Path

import pytest

ENGINE_ROOT = Path(__file__).resolve().parent.parent
WEB_FETCH_JSON = ENGINE_ROOT / "catalog" / "core" / "tools" / "web_fetch.json"


class StubResponse:
    def __init__(self, text: str, status_error: Exception | None = None) -> None:
        self.text = text
        self.headers = {"content-type": "text/html"}
        self.status_error = status_error

    def raise_for_status(self) -> None:
        if self.status_error is not None:
            raise self.status_error


class StubAsyncClient:
    calls: list[str] = []
    response: StubResponse = StubResponse("<html><body>hello cached world</body></html>")

    def __init__(self, *a, **k) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        StubAsyncClient.calls.append(url)
        return StubAsyncClient.response


@pytest.fixture
def web_fetch_module(monkeypatch):
    """Exec the catalog tool source in-process, with httpx stubbed and DNS
    resolution pinned to a public address."""
    definition = json.loads(WEB_FETCH_JSON.read_text(encoding="utf-8"))
    assert definition["version"] == "1.1.0"
    namespace: dict = {}
    exec(compile(definition["source_code"], "web_fetch", "exec"), namespace)

    httpx_stub = types.ModuleType("httpx")
    httpx_stub.AsyncClient = StubAsyncClient
    monkeypatch.setitem(sys.modules, "httpx", httpx_stub)
    monkeypatch.setattr(
        namespace["socket"],
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    StubAsyncClient.calls = []
    StubAsyncClient.response = StubResponse("<html><body>hello cached world</body></html>")
    return namespace


class TestWebFetchCache:
    def test_second_fetch_within_ttl_is_cached(self, web_fetch_module):
        handler = web_fetch_module["handler"]

        async def run():
            first = await handler({"url": "https://example.com/page"})
            second = await handler({"url": "https://example.com/page"})
            return first, second

        first, second = asyncio.run(run())
        assert "hello cached world" in first
        assert second == first
        assert len(StubAsyncClient.calls) == 1

    def test_expired_entry_refetches(self, web_fetch_module):
        handler = web_fetch_module["handler"]
        cache = web_fetch_module["url_cache"]

        async def run():
            await handler({"url": "https://example.com/page"})
            cache.entries["https://example.com/page"].expires_at = time.monotonic() - 1
            await handler({"url": "https://example.com/page"})

        asyncio.run(run())
        assert len(StubAsyncClient.calls) == 2

    def test_errors_are_not_cached(self, web_fetch_module):
        handler = web_fetch_module["handler"]
        StubAsyncClient.response = StubResponse("boom", status_error=RuntimeError("HTTP 503"))

        async def run():
            first = await handler({"url": "https://example.com/broken"})
            second = await handler({"url": "https://example.com/broken"})
            return first, second

        first, second = asyncio.run(run())
        assert first.startswith("Error fetching")
        assert second.startswith("Error fetching")
        assert len(StubAsyncClient.calls) == 2
        assert web_fetch_module["url_cache"].get("https://example.com/broken") is None

    def test_blocked_url_never_reaches_cache_or_network(self, web_fetch_module):
        handler = web_fetch_module["handler"]

        async def run():
            return await handler({"url": "http://localhost/admin"})

        result = asyncio.run(run())
        assert result.startswith("Error: blocked URL")
        assert StubAsyncClient.calls == []
        assert len(web_fetch_module["url_cache"].entries) == 0

    def test_distinct_urls_cached_separately(self, web_fetch_module):
        handler = web_fetch_module["handler"]

        async def run():
            await handler({"url": "https://example.com/a"})
            await handler({"url": "https://example.com/b"})

        asyncio.run(run())
        assert len(StubAsyncClient.calls) == 2
        assert len(web_fetch_module["url_cache"].entries) == 2
