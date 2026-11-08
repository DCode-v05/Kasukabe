"""Deep health probe — citadel reachability via the SDK client.

Exercises all four outcomes of GET /health/deep without a real citadel:
healthy (200), unhealthy (citadel answers non-200), unreachable (network
failure), and unconfigured. Uses httpx.MockTransport like the catalog
sync tests — the SDK builds its transport on httpx.AsyncClient, so the
monkeypatched class intercepts at the wire layer.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.config import Settings
from src.server import health_deep


def make_settings(cdt_url="http://citadel-mock", cdt_key="sk-sept-cit-test") -> Settings:
    return Settings(cdt_url=cdt_url, cdt_key=cdt_key, llm_model="claude-haiku-4-5")


def patch_wire(monkeypatch, handler) -> None:
    real_client_cls = httpx.AsyncClient

    class Patched(real_client_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)


def body_of(resp) -> dict:
    return json.loads(resp.body)


@pytest.mark.asyncio
async def test_healthy_citadel_returns_200_with_latency(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/health")
        return httpx.Response(200, json={"status": "ok", "version": "test"})

    patch_wire(monkeypatch, handler)
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, env_path=None: make_settings()))

    result = await health_deep()
    assert result["status"] == "healthy"
    assert result["citadel_status"] == 200
    assert isinstance(result["latency_ms"], int)


@pytest.mark.asyncio
async def test_sick_citadel_is_unhealthy_not_unreachable(monkeypatch):
    """citadel answered but non-200 — must be labeled citadel-unhealthy
    with the actual upstream status, distinct from a network failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503, json={"error": {"code": "OVERLOADED", "message": "overloaded", "retryable": True}}
        )

    patch_wire(monkeypatch, handler)
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, env_path=None: make_settings()))

    resp = await health_deep()
    assert resp.status_code == 503
    body = body_of(resp)
    assert body["status"] == "citadel-unhealthy"
    assert body["citadel_status"] == 503


@pytest.mark.asyncio
async def test_unreachable_citadel(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    patch_wire(monkeypatch, handler)
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, env_path=None: make_settings()))

    resp = await health_deep()
    assert resp.status_code == 503
    body = body_of(resp)
    assert body["status"] == "citadel-unreachable"


@pytest.mark.asyncio
async def test_no_citadel_configured(monkeypatch):
    monkeypatch.setattr(
        Settings,
        "load",
        classmethod(lambda cls, env_path=None: make_settings(cdt_url="", cdt_key="")),
    )

    resp = await health_deep()
    assert resp.status_code == 503
    assert body_of(resp)["status"] == "no-citadel-configured"
