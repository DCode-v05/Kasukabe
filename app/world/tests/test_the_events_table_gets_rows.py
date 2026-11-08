"""EVAL-02: a genesis run must leave rows in `events`.

Every run so far wrote zero. Not because the writer was broken - because
`persist()` deliberately did not call `record_event`, after an earlier run
mirrored every WorldEvent and grew the trace store to 21 GB. The reaction
was total abstention, and total abstention reads exactly like a quiet town:
the report, the evals and the observatory all query `events`, all get
nothing back, and none of them can tell "nothing happened" from "nobody
wrote it down".

The fix is an allowlist, so both failure modes are excluded at once. These
tests pin both halves: the decisions get recorded, and the per-tick chatter
that caused the 21 GB still does not.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from world.core.events import WorldEvent
from world.run_genesis import (
    GenesisRunner,
    GenesisRunnerConfig,
    is_worth_recording,
)


# ── the rule itself ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "event_type",
    [
        "economy.price.posted",
        "economy.trade.settled",
        "politics.election.tallied",
        "business.sale.completed",
        "health.agent.dormant",
        "crime.theft.reported",
    ],
)
def test_a_decision_or_a_resource_move_earns_a_row(event_type):
    assert is_worth_recording(event_type), (
        f"{event_type} carries a decision or moves a resource and must be "
        "recoverable from the trace"
    )


@pytest.mark.parametrize("event_type", ["agent.moved", "agent.perceived"])
def test_the_per_tick_chatter_that_cost_21gb_still_does_not(event_type):
    assert not is_worth_recording(event_type), (
        f"{event_type} fires per resident per tick; recording it is what "
        "grew a trace store to 21 GB"
    )


# ── end to end, through the runner's own persist() ─────────────────────

@pytest.mark.asyncio
async def test_an_event_published_on_a_booted_runner_reaches_the_table(tmp_path, monkeypatch):
    db = tmp_path / "kasukabe.db"
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(db))

    runner = GenesisRunner(
        GenesisRunnerConfig(
            mock_mode=True,
            checkpoint_dir=tmp_path / "checkpoints",
            world_mode="embedded",
            world_port=0,
            budget_cap_cents=40_000,
        )
    )
    await runner.boot()
    assert runner.trace_store is not None, "no recorder attached; this test proves nothing"

    await runner.world_state.event_bus.publish(
        WorldEvent(
            type="economy.price.posted",
            tick=7,
            payload={"business_id": "mill", "item": "flour", "sim_price": 12},
        )
    )
    # And one that must NOT be recorded, published the same way.
    await runner.world_state.event_bus.publish(
        WorldEvent(type="agent.moved", tick=7, payload={"agent_id": "n01"})
    )
    await runner.trace_store.flush() if hasattr(runner.trace_store, "flush") else None

    conn = sqlite3.connect(str(db))
    rows = conn.execute("select type, tick from events order by tick").fetchall()
    conn.close()

    types = [t for t, _ in rows]
    assert "economy.price.posted" in types, (
        f"the events table took no row for a posted price (saw {types!r}). "
        "Everything that reads `events` is reporting on an empty table."
    )
    assert "agent.moved" not in types, (
        "agent.moved was recorded - that is the write that grew an earlier "
        "trace store to 21 GB"
    )
