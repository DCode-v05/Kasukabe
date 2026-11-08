"""Engine catalog_sync — runtime overlay, full sync (poll-only).

Tests the engine-side dynamic catalog code without needing a real
citadel — uses httpx.MockTransport to fake the citadel /v1/models
endpoint. The engine polls citadel at boot and on a periodic loop;
there is no push/webhook receiver (catalog is poll-only).
"""

from __future__ import annotations

import httpx
import pytest

from src.config import Settings
from src.herald import catalog_sync


def settings(cdt_url="http://citadel-mock", cdt_key="sk-sept-cit-test") -> Settings:
    return Settings(cdt_url=cdt_url, cdt_key=cdt_key, llm_model="claude-haiku-4-5")


@pytest.mark.asyncio
async def test_full_sync_populates_runtime_overlay(monkeypatch):
    """Engine boot pulls /v1/models and overlays them on the static catalog."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        captured["auth"] = request.headers.get("authorization")
        captured["path"] = path
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "model_name": "new-citadel-model",
                        "provider": "anthropic",
                        "context_window": 500_000,
                        "max_output": 8192,
                        "tier": "heavy",
                        "supports_tools": True,
                        "supports_vision": True,
                        "input_per_mtok": 5.0,
                        "output_per_mtok": 25.0,
                        "cache_read_per_mtok": 0.5,
                        "cache_write_per_mtok": 6.0,
                    },
                ]
            },
        )

    real_client_cls = httpx.AsyncClient

    class Patched(real_client_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)

    s = settings()
    ok = await catalog_sync.initialize(s)
    assert ok is True
    assert captured["path"].endswith("/v1/models")
    assert captured["auth"].startswith("Bearer ")

    overlay = catalog_sync.get_catalog_overlay()
    assert "new-citadel-model" in overlay
    md = overlay["new-citadel-model"]
    assert md.tier == "heavy"
    assert md.context_window == 500_000
    assert md.cost.input_per_1m == 5.0

    # Lookup serves the synced catalog
    assert catalog_sync.lookup("new-citadel-model") is md
    # The catalog is overlay-only: an id the sync never delivered is unknown
    # (there is no static seed to fall back to)
    assert catalog_sync.lookup("model-never-synced") is None

    await catalog_sync.shutdown()


@pytest.mark.asyncio
async def test_degraded_mode_when_citadel_unreachable(monkeypatch):
    """No CDT_URL/CDT_KEY → returns False without raising. Engine boots
    using static seed."""
    s = Settings(cdt_url="", cdt_key="", llm_model="claude-haiku-4-5")
    ok = await catalog_sync.initialize(s)
    assert ok is False


@pytest.mark.asyncio
async def test_resync_reuses_one_client(monkeypatch):
    """The periodic poll must ride the shared SDK client — no fresh
    connection pool per 5-minute resync — and shutdown() must close it."""
    constructed: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # A non-empty response so full_sync adopts it (an empty pull is now
        # treated as suspicious and intentionally returns False without
        # clearing the overlay — see test_empty_pull_keeps_overlay).
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "model_name": "reuse-probe-model",
                        "provider": "anthropic",
                        "context_window": 200_000,
                        "max_output": 4096,
                        "input_per_mtok": 1.0,
                        "output_per_mtok": 2.0,
                    }
                ]
            },
        )

    real_client_cls = httpx.AsyncClient

    class Patched(real_client_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            constructed.append(1)
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)

    ok = await catalog_sync.initialize(settings())
    assert ok is True
    # The SDK client (used by full_sync) is built once and reused across polls;
    # what matters is the SDK connection pool is not rebuilt per resync.
    pools_after_sdk_boot = catalog_sync.sept_client

    # Two more polls — same SDK client, no fresh SDK connection pool.
    assert await catalog_sync.full_sync() is True
    assert await catalog_sync.full_sync() is True
    assert catalog_sync.sept_client is pools_after_sdk_boot

    await catalog_sync.shutdown()
    assert catalog_sync.sept_client is None


@pytest.mark.asyncio
async def test_full_sync_handles_non_200(monkeypatch):
    """citadel returning 503 → degraded mode, no overlay populated."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="overloaded")

    real_client_cls = httpx.AsyncClient

    class Patched(real_client_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)
    s = settings()
    # Reset overlay before test
    overlay_before = len(catalog_sync.get_catalog_overlay())
    ok = await catalog_sync.initialize(s)
    assert ok is False
    overlay_after = len(catalog_sync.get_catalog_overlay())
    # No new entries added on 503
    assert overlay_after == overlay_before
    await catalog_sync.shutdown()


def _row(name: str, **over) -> dict:
    base = {
        "model_name": name,
        "provider": "anthropic",
        "context_window": 200_000,
        "max_output": 4096,
        "input_per_mtok": 1.0,
        "output_per_mtok": 2.0,
    }
    base.update(over)
    return base


def _patch_models(monkeypatch, handler) -> None:
    real_client_cls = httpx.AsyncClient

    class Patched(real_client_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)


@pytest.mark.asyncio
async def test_empty_pull_keeps_overlay(monkeypatch):
    """A 0-row /v1/models response must NOT clear the overlay — a
    transiently-empty pull should not strand consumers on the static seed."""
    catalog_sync.active_settings = settings()
    catalog_sync.runtime_overlay.clear()
    catalog_sync.runtime_overlay.update(
        {md.id: md for md in [catalog_sync.row_to_modeldef(_row("keep-me"))]}
    )

    _patch_models(monkeypatch, lambda r: httpx.Response(200, json={"data": []}))

    ok = await catalog_sync.full_sync()
    assert ok is False  # empty pull is not adopted
    assert "keep-me" in catalog_sync.get_catalog_overlay()  # but kept
    await catalog_sync.shutdown()


@pytest.mark.asyncio
async def test_short_pull_merges_without_evicting(monkeypatch):
    """A suspiciously short pull (< shrink floor of current) refreshes the
    rows it carries but does NOT drop the models it omits."""
    catalog_sync.active_settings = settings()
    catalog_sync.runtime_overlay.clear()
    for name in ("m1", "m2", "m3", "m4"):
        md = catalog_sync.row_to_modeldef(_row(name))
        catalog_sync.runtime_overlay[md.id] = md

    # Respond with only 1 of 4 (< 50% floor) — and with a new price for m1.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [_row("m1", input_per_mtok=9.0)]})

    _patch_models(monkeypatch, handler)

    ok = await catalog_sync.full_sync()
    assert ok is True
    overlay = catalog_sync.get_catalog_overlay()
    # m1 refreshed, m2..m4 NOT evicted (partial response, merge-only)
    assert overlay["m1"].cost.input_per_1m == 9.0
    assert {"m1", "m2", "m3", "m4"} <= set(overlay)
    await catalog_sync.shutdown()


@pytest.mark.asyncio
async def test_complete_pull_prunes_removed(monkeypatch):
    """A response at/above the shrink floor IS trusted to prune removed
    models (replace semantics preserved for the healthy case)."""
    catalog_sync.active_settings = settings()
    catalog_sync.runtime_overlay.clear()
    for name in ("a", "b"):
        md = catalog_sync.row_to_modeldef(_row(name))
        catalog_sync.runtime_overlay[md.id] = md

    # 1 of 2 == 50% == floor → complete → prune the omitted one.
    _patch_models(
        monkeypatch,
        lambda r: httpx.Response(200, json={"data": [_row("a")]}),
    )

    ok = await catalog_sync.full_sync()
    assert ok is True
    overlay = catalog_sync.get_catalog_overlay()
    assert set(overlay) == {"a"}  # "b" pruned
    await catalog_sync.shutdown()


@pytest.mark.asyncio
async def test_warns_on_zero_priced_model(monkeypatch, caplog):
    """A routable model with no resolvable price logs a warning rather than
    silently pricing at $0."""
    import logging

    with caplog.at_level(logging.WARNING, logger="src.herald.catalog_sync"):
        md = catalog_sync.row_to_modeldef(
            {
                "model_name": "unpriced-model",
                "provider": "anthropic",
                "context_window": 200_000,
                "max_output": 4096,
                # no *_per_mtok fields at all
            }
        )
    assert md.cost is None
    assert any(
        "unpriced-model" in rec.message and "no resolvable price" in rec.message
        for rec in caplog.records
    )
