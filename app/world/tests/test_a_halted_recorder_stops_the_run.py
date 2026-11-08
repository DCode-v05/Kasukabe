"""A broker that has stopped recording is a reason to stop.

`INFRA-18`. When both of the broker's `BatchWriter`s halt, every
`POST /v1/messages` returns 503 - deliberately, because "losing
observations must never again be survivable". Herald retries a 5xx three
times and the turn fails.

The runner found out only as barren ticks: twenty consecutive all-fail
ticks, which CORE-32 measured at anywhere from five to forty minutes of a
town not thinking, with every failed call still a round trip. Meanwhile
`GET /admin/trace` had been reporting `dispatch_halted` since it was
written and nothing asked it.

The probe runs only on a tick that already saw a dispatch failure, so a
healthy run adds no requests.
"""
from __future__ import annotations

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class _Client:
    """Answers /admin/spend healthily and /admin/trace as told."""

    def __init__(self, trace_payload):
        self.trace_payload = trace_payload
        self.asked: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        self.asked.append(url)
        if url.endswith("/admin/spend"):
            return _Resp({"state": "normal", "spent_usd": 0.1, "cap_usd": 10.0})
        return _Resp(self.trace_payload)


def _runner(tmp_path) -> GenesisRunner:
    return GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    ))


@pytest.mark.asyncio
async def test_a_halted_writer_is_terminal(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    client = _Client({
        "dispatch_halted": True,
        "trace_writer": {"last_failure_message": "disk full"},
    })
    monkeypatch.setattr("world.run_genesis.httpx.AsyncClient", lambda *a, **k: client)

    reason = await runner.broker_terminal_reason()

    assert reason, (
        "INFRA-18: a broker refusing every call because it cannot record "
        "was invisible to the runner except as barren ticks"
    )
    assert "disk full" in reason, "the operator is not told why"


@pytest.mark.asyncio
async def test_a_healthy_writer_is_not(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    client = _Client({"dispatch_halted": False, "trace_writer": {}})
    monkeypatch.setattr("world.run_genesis.httpx.AsyncClient", lambda *a, **k: client)

    assert await runner.broker_terminal_reason() is None


@pytest.mark.asyncio
async def test_an_unreachable_trace_route_does_not_end_the_run(tmp_path, monkeypatch):
    """A probe must never end a run by failing - that is the barren-tick
    guard's business, not this one's."""
    class _Broken(_Client):
        async def get(self, url):
            if url.endswith("/admin/trace"):
                raise RuntimeError("connection reset")
            return await super().get(url)

    runner = _runner(tmp_path)
    monkeypatch.setattr(
        "world.run_genesis.httpx.AsyncClient", lambda *a, **k: _Broken({})
    )
    assert await runner.broker_terminal_reason() is None
