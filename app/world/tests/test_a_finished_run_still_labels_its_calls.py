"""Run 29's report said every one of 2,717 model calls was "idle".

The C1 section - what thinking cost, by what the turn was FOR - labels a
call by joining it, on (agent_id, tick), to the tools the same turn called
(`stakes_report.label_calls`, EVAL-03). `task_label` is never written by
the broker, by design: when a model call is made nobody knows yet what the
turn will do. Run 27, 28, 28b and 29 all have it NULL on every row.

The join's only source was `/world/tool_invocations`, a LIVE endpoint, on
the stated grounds that invocations are "an in-memory ring with no table
behind it". That stopped being true at EVAL-25: the trace store has a
`tool_invocations` table, and run 29's holds 2,323 rows with exactly the
agent, tick and tool the join needs. But a report written after the world
stops - which is how every final report is written - got an empty live
answer, joined against nothing, and labelled a town that harvested,
felled, crafted and traded all morning as having done nothing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
SCHEMA = APP / "supabase" / "sqlite_schema.sql"


def _a_finished_trace(tmp_path) -> Path:
    db = tmp_path / "trace.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    for i, (agent, tick, tool) in enumerate([
        ("n01", 40, "harvest"), ("n01", 40, "harvest"),
        ("n02", 41, "craft"), ("n03", 42, "say"),
    ]):
        conn.execute(
            "insert into tool_invocations (invocation_id, agent_id, tick, tool, namespace) "
            "values (?, ?, ?, ?, 'public')",
            (f"inv-{i}", agent, tick, tool),
        )
    conn.commit()
    conn.close()
    return db


def test_the_trace_can_answer_the_join_the_live_world_used_to(tmp_path) -> None:
    from world.run_report import trace_invocations

    rows = trace_invocations(_a_finished_trace(tmp_path))

    assert len(rows) == 4
    assert {r["tool_name"] for r in rows} == {"harvest", "craft", "say"}
    assert all("agent_id" in r and "tick" in r for r in rows)


def test_a_stopped_world_falls_back_to_the_trace(tmp_path) -> None:
    """The source the report joins against: live when the world answers,
    the trace when it does not - never nothing."""
    from world.run_report import invocations_for_labelling

    db = _a_finished_trace(tmp_path)

    assert invocations_for_labelling([], db), "a finished run joined against nothing"
    live = [{"agent_id": "n09", "tick": 1, "tool_name": "move"}]
    assert invocations_for_labelling(live, db) == live, "the live answer should win while it exists"


def test_a_turn_that_worked_is_not_labelled_idle(tmp_path) -> None:
    """The consequence, end to end: a harvesting turn gets a harvesting label."""
    from world.run_report import invocations_for_labelling
    from world.stakes_report import label_calls

    db = _a_finished_trace(tmp_path)
    calls = [{"agent_id": "n01", "tick": 40, "input_tokens": 1000}]

    labelled = label_calls(calls, invocations_for_labelling([], db))

    assert labelled[0].get("task_label") not in (None, "idle"), labelled
