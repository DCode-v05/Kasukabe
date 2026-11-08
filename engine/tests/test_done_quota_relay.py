"""The live-badge relay: ExecutionSummary.quota → done.payload.quota.

Citadel emits a pre-computed ``quota`` dict on its /v1/messages response;
the engine is a pure relay. The traced path ends:

    ExecutionSummary(quota=...) → block_fan_sink (summary dict) → make_done_block

This drives the real server.block_fan_sink sink and the real
make_done_block synthesizer (no mocks) and asserts the dict lands
verbatim under done.payload["quota"], and that a no-quota summary
produces a None quota (engine constructs/reshapes nothing).
"""

from __future__ import annotations

import pytest

import src.server as server
from src.config import Settings
from src.engine_core.blocks import make_done_block
from src.engine_core.events import EventEmitter, ExecutionSummary


def make_state(emitter):
    from src.bootstrap import EngineState

    return EngineState(settings=Settings(), emitter=emitter)


@pytest.mark.asyncio
async def test_quota_relays_into_done_payload(monkeypatch):
    """A summary carrying citadel's quota dict surfaces it verbatim on the
    synthesized terminal done block."""
    emitter = EventEmitter()
    state = make_state(emitter)
    monkeypatch.setattr(server, "state", state)
    server.last_summary_by_thread.clear()

    tid = "thr_quota_relay"
    quota = {
        "subscription": {
            "session": {"used": 1000, "limit": 5000, "remaining": 4000, "reset": 18000},
            "weekly": {"used": 1000, "limit": 50000, "remaining": 49000, "reset": 604800},
        },
    }

    await server.block_fan_sink(ExecutionSummary(thread_id=tid, status="completed", quota=quota))

    # block_fan_sink records the summary (incl. quota) keyed by thread.
    summary = server.last_summary_by_thread[tid]
    assert summary["quota"] == quota
    assert summary["quota"] is quota  # pure relay — same dict, not reshaped

    # /execute synthesizes the terminal done block from that summary.
    done = make_done_block("rid_quota", summary, tid)
    assert done.type == "done"
    assert done.payload["quota"] == quota

    server.last_summary_by_thread.clear()


@pytest.mark.asyncio
async def test_metered_quota_relays_into_done_payload(monkeypatch):
    """Metered shape relays through identically (engine computes nothing)."""
    emitter = EventEmitter()
    state = make_state(emitter)
    monkeypatch.setattr(server, "state", state)
    server.last_summary_by_thread.clear()

    tid = "thr_metered_relay"
    quota = {
        "metered": {
            "spent_usd": 4.25,
            "budget_usd": 10.0,
            "remaining_usd": 5.75,
            "reset": 2592000,
        },
    }

    await server.block_fan_sink(ExecutionSummary(thread_id=tid, status="completed", quota=quota))

    summary = server.last_summary_by_thread[tid]
    done = make_done_block("rid_metered", summary, tid)
    assert done.payload["quota"] == quota

    server.last_summary_by_thread.clear()


@pytest.mark.asyncio
async def test_no_quota_yields_none(monkeypatch):
    """A summary with no quota (citadel omitted it) → done.payload['quota']
    is None; the engine invents nothing."""
    emitter = EventEmitter()
    state = make_state(emitter)
    monkeypatch.setattr(server, "state", state)
    server.last_summary_by_thread.clear()

    tid = "thr_no_quota"

    await server.block_fan_sink(ExecutionSummary(thread_id=tid, status="completed"))

    summary = server.last_summary_by_thread[tid]
    assert summary["quota"] is None

    done = make_done_block("rid_none", summary, tid)
    assert done.payload["quota"] is None

    server.last_summary_by_thread.clear()
