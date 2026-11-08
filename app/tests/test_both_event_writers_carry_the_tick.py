"""Two writers, one `events` table, one clock.

`INFRA-26`. The world and the broker both insert into `events`, and the
review recorded that the broker's rows carried no `tick` - so half the
table could not be placed on the world's clock, and a reader joining
broker activity to what the town was doing had nothing to join on.

`INFRA-02` has since given the broker the world's tick (carried on the
payer-balance response it already fetches before every call), so both
writers now supply it. This file exists because that is easy to lose
again: `tick` is optional on the broker's dataclass, and a future writer
that forgets it would silently produce rows nobody notices are unplaceable
until they try to use them.

`INFRA-16` is the neighbouring finding and is closed by a different
mechanism - `run_genesis.is_worth_recording`, which decides WHICH world
events earn a row. Before it, `record_event` was skipped entirely and the
"complete causal record" had no world rows at all; recording everything
had previously produced a 21 GB store. Asserted here too, because the two
findings share the table and a change to either could quietly undo the
other.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
SCHEMA = APP / "supabase" / "sqlite_schema.sql"


def test_the_brokers_rows_carry_a_tick() -> None:
    from broker.trace import BrokerEvent, broker_event_to_row

    row = broker_event_to_row(
        BrokerEvent(id="e1", type="broker.call.completed", agent_id="a1",
                    model="m", payload={}, created_at=0.0, tick=42)
    )
    assert row["tick"] == 42, (
        "INFRA-26: the broker writes into `events` without the world's "
        "tick, so half the table cannot be placed on the town's clock"
    )


def test_the_worlds_rows_carry_a_tick() -> None:
    import inspect

    from world.wiring import TraceStore

    source = inspect.getsource(TraceStore.record_event)
    assert '"tick": event.tick' in source


def test_the_column_is_there_for_both() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    columns = {row[1] for row in conn.execute("pragma table_info(events)")}
    assert "tick" in columns


# ── INFRA-16: the world writes SOME events, not none and not all ───────


def test_a_decision_earns_a_row() -> None:
    from world.run_genesis import is_worth_recording

    for event_type in ("economy.wallet.debited", "politics.regime.changed",
                       "health.agent.died", "crime.case.opened"):
        assert is_worth_recording(event_type), (
            f"INFRA-16: {event_type} is a decision or a resource moving and "
            "earns no row, so the causal record cannot show it happened"
        )


def test_the_twenty_one_gigabytes_do_not_come_back() -> None:
    """Recording everything is what produced a 21 GB store. The exclusions
    are by NAME so a reader can weigh the cost rather than guess at it."""
    from world.run_genesis import is_worth_recording

    assert not is_worth_recording("agent.moved")
    assert not is_worth_recording("agent.perceived")


def test_it_is_actually_consulted() -> None:
    """Both halves of this file are about mechanisms that had no caller
    once. The rule existing is not the same as the rule running."""
    source = (APP / "world" / "run_genesis.py").read_text(encoding="utf-8")
    assert "if is_worth_recording(event.type):" in source, (
        "INFRA-16: the recording rule is defined and nothing consults it, "
        "so the events table is empty again"
    )
