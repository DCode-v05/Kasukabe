"""The /rpc route is retired (pre-SDK): assert it is gone, and keep the usage
hook → raven telemetry integration that used to live alongside it.

The dispatcher (src/rpc.py) and every @exposed op are deleted; the typed routes
that replaced them live in tests/test_routes_presdk.py. All that survives here is
(1) proof POST /rpc 404s and (2) the herald usage hook that emits a token_usage
event into raven — neither of which goes through dispatch anymore.
"""

import pytest
from httpx import AsyncClient, ASGITransport

import src.server as server
from src.raven import api as raven_api
from src.herald.usage_tracker import on_usage_hooks
from src.herald.types import Usage


@pytest.fixture(autouse=True)
def init_raven_and_reset(db):
    """Initialize raven with test db and reset hooks after each test."""
    raven_api.init(db)
    yield
    raven_api.store = None
    raven_api.db_pool = None
    on_usage_hooks.clear()


@pytest.mark.asyncio
async def test_rpc_route_is_retired_404(monkeypatch):
    """POST /rpc no longer exists — the dispatcher and route are gone."""
    monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/rpc",
            json={"subsystem": "telemetry", "op": "query", "metric": "tokens_used"},
            headers={"x-engine-key": "ok"},
        )
    assert resp.status_code == 404


class TestUsageHookIntegration:
    @pytest.mark.asyncio
    async def test_usage_hook_registered_on_init(self, db):
        assert len(on_usage_hooks) >= 1

    @pytest.mark.asyncio
    async def test_usage_hook_emits_token_usage_event(self, db):
        usage = Usage(input_tokens=100, output_tokens=50, cache_read_input_tokens=20)
        for hook in on_usage_hooks:
            import asyncio

            result = hook(usage, "test-model")
            if asyncio.iscoroutine(result):
                await result

        events = await raven_api.events(since=0.0, kind="token_usage")
        assert len(events) >= 1
        assert events[0]["model"] == "test-model"
        assert events[0]["input_tokens"] == 100
        assert events[0]["output_tokens"] == 50
