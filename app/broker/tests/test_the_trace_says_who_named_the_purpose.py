"""A purpose the broker guessed is recorded as a guess.

`CORE-30`. Nothing in the engine sends `X-Kasukabe-Purpose` - `herald` is
frozen under ROADMAP Law 1 - so `broker/purpose.py` works the purpose out
from verbatim markers in the request. That is a legitimate way to route a
call and a poor foundation for a research claim, and ROADMAP T4's gate
("background share > 0 and matches sleep-onset edges") reads the result
as though the engine had asserted it.

The `auto:` prefix carried the distinction implicitly. Implicitly is not
good enough for a column a paper will be written off: it is a substring
check on free text that a future tag could break silently, and it cannot
tell the marker match - the only inference that MOVES a call to another
lane - from the shape tag that merely names it. `purpose_source` can.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from broker.purpose import (
    SOURCE_HEADER,
    SOURCE_MARKER,
    SOURCE_SHAPE,
    infer_purpose,
    purpose_and_source,
)
from broker.trace import TraceRecord, new_trace_id, trace_record_to_row

SCHEMA = Path(__file__).resolve().parents[2] / "supabase" / "sqlite_schema.sql"

AGENT_TURN = {
    "tools": [{"name": "world_say"}],
    "messages": [{"role": "user", "content": "what shall I do today"}],
}
SUMMARISER = {
    "messages": [{"role": "user", "content": "Summarize this exchange in two lines."}],
}


def test_a_caller_that_says_so_is_believed() -> None:
    purpose, source = purpose_and_source("cognition.turn", AGENT_TURN)
    assert (purpose, source) == ("cognition.turn", SOURCE_HEADER)


def test_a_marker_match_is_recorded_as_inferred() -> None:
    """This is the one that reroutes the call, and the one every
    background figure in every run so far actually came from."""
    purpose, source = purpose_and_source("", SUMMARISER)
    assert source == SOURCE_MARKER, (
        f"CORE-30: the broker moved this call to the background lane on its "
        f"own inference and recorded it as {source!r}, indistinguishable in "
        "the trace from a purpose the engine asserted"
    )
    assert purpose.startswith("auto:")


def test_a_shape_tag_is_not_called_inferred() -> None:
    """A resident's turn named by shape did NOT move anywhere, and calling
    it 'inferred' would inflate exactly the number T4 reads."""
    _purpose, source = purpose_and_source("", AGENT_TURN)
    assert source == SOURCE_SHAPE


def test_the_classification_itself_is_unchanged() -> None:
    """`purpose_and_source` must be `infer_purpose` plus one fact, not a
    second opinion - two classifiers would route and record differently."""
    for header, body in (
        ("cognition.turn", AGENT_TURN),
        ("", AGENT_TURN),
        ("", SUMMARISER),
        ("", {}),
        ("unspecified", SUMMARISER),
    ):
        assert purpose_and_source(header, body)[0] == infer_purpose(header, body)


def test_a_malformed_body_still_answers() -> None:
    """The broker's bookkeeping must never be able to fail a turn."""
    purpose, source = purpose_and_source("", "not a dict")  # type: ignore[arg-type]
    assert purpose and source in {SOURCE_HEADER, SOURCE_MARKER, SOURCE_SHAPE}


# ── it reaches the table ───────────────────────────────────────────────


def _record(**over) -> TraceRecord:
    fields = dict(
        id=new_trace_id(), agent_id="a1", payer_agent_id="a1",
        model="m", requested_model="m", region="us-east-1",
        call_class="background", purpose="auto:summarizer.post_turn",
        purpose_source=SOURCE_MARKER,
        request_body={}, response_body={}, stop_reason="end_turn",
        input_tokens=1, output_tokens=1, cost_nano_dollars=1, cents_debited=1,
        latency_ms=1, rate_limit_wait_ms=0, success=True,
        error_type=None, error_message=None, created_at=0.0,
    )
    fields.update(over)
    return TraceRecord(**fields)


def test_the_row_carries_it() -> None:
    assert trace_record_to_row(_record())["purpose_source"] == SOURCE_MARKER


def test_the_column_exists_and_constrains_itself(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    columns = {row[1] for row in conn.execute("pragma table_info(llm_calls)")}
    assert "purpose_source" in columns, (
        "CORE-30: the trace has no column for where a purpose came from"
    )
    row = trace_record_to_row(_record())
    cols = ", ".join(row)
    conn.execute(
        f"insert into llm_calls ({cols}) values ({', '.join('?' * len(row))})",
        [
            v if not isinstance(v, (dict, list)) else str(v)
            for v in row.values()
        ],
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("update llm_calls set purpose_source = 'made up'")


def test_an_old_row_is_null_not_a_guess(tmp_path: Path) -> None:
    """A build that did not record this genuinely does not know, and a
    default would be a fabricated answer to the one question the column
    exists to ask."""
    assert trace_record_to_row(_record(purpose_source=None))["purpose_source"] is None
