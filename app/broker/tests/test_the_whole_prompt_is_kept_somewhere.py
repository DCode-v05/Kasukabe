"""What the trace row cannot hold is kept where it can be found.

`INFRA-08`. `capped_body` truncates at 64 KB. The median request in this
project is 76 KB and the largest measured 154 KB, so MOST calls are
stored partial, and the system prompt - persona plus law, about 150 KB -
is the first thing cut. DESIGN section 9 calls this store "a complete
causal record"; it was not one.

What would have made it one already existed.
`supabase/archiver.py`'s `PayloadArchiver.archive_payload` was written
for exactly this - gzip, sha256, S3 or a local directory depending only
on the client it holds - fully tested, and called by NOTHING in
`broker/`. Five mechanisms in this project have been complete, tested and
unwired; this is one of the more expensive, because the thing lost is the
prompt a resident actually thought against.
"""
from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from broker.trace import (
    CAPPED_FIELD_CHARS,
    SqliteTraceSink,
    TraceRecord,
    new_trace_id,
    trace_record_to_row,
)

SCHEMA = Path(__file__).resolve().parents[2] / "supabase" / "sqlite_schema.sql"


class _Archive:
    """Stands in for S3, and remembers exactly what it was handed."""

    def __init__(self, fail: bool = False) -> None:
        self.stored: dict[str, dict] = {}
        self.fail = fail

    async def archive_payload(self, *, key: str, payload: dict):
        if self.fail:
            raise RuntimeError("the bucket is on fire")
        self.stored[key] = payload
        return type("Result", (), {"path": f"llm-payloads/{key}"})()


class _Client:
    def __init__(self) -> None:
        self.inserted: list[dict] = []
        self.config = None

    async def insert(self, table: str, rows: list[dict]) -> None:
        self.inserted.extend(rows)


def _record(system_prompt: str) -> TraceRecord:
    return TraceRecord(
        id=new_trace_id(), agent_id="a0042", payer_agent_id="a0042",
        model="m", requested_model="m", region="us-east-1",
        call_class="cognition", purpose="auto:agent_turn",
        request_body={"system": system_prompt, "messages": [{"role": "user", "content": "hi"}]},
        response_body={"content": [{"type": "text", "text": "ok"}]},
        stop_reason="end_turn", input_tokens=1, output_tokens=1,
        cost_nano_dollars=1, cents_debited=1, latency_ms=1,
        rate_limit_wait_ms=0, success=True, error_type=None,
        error_message=None, created_at=0.0,
    )


HUGE = "the law of the town. " * 12_000
SMALL = "a short system prompt"


@pytest.mark.asyncio
async def test_a_truncated_row_says_where_the_rest_is() -> None:
    """The finding itself."""
    archive, client = _Archive(), _Client()
    sink = SqliteTraceSink(client, "llm_calls", trace_record_to_row, archiver=archive)
    record = _record(HUGE)

    await sink.write_batch([record])

    row = client.inserted[0]
    assert row["request_archive_key"], (
        "INFRA-08: the row stores a fraction of the prompt and no pointer "
        "to the rest, so the prompt this resident thought against is gone"
    )
    assert archive.stored, "nothing was actually archived"


@pytest.mark.asyncio
async def test_what_was_archived_is_the_whole_thing() -> None:
    """A truncated archive would be the same bug one directory along."""
    archive, client = _Archive(), _Client()
    sink = SqliteTraceSink(client, "llm_calls", trace_record_to_row, archiver=archive)
    record = _record(HUGE)

    await sink.write_batch([record])

    stored = next(iter(archive.stored.values()))
    assert stored["system"] == HUGE
    assert len(stored["system"]) > CAPPED_FIELD_CHARS * 10

    row = client.inserted[0]
    kept = json.loads(row["request_body"]) if isinstance(row["request_body"], str) else row["request_body"]
    assert len(json.dumps(kept)) < len(HUGE), "the row was not actually capped"


@pytest.mark.asyncio
async def test_a_body_that_fits_is_not_archived_twice() -> None:
    """The row already holds it. Archiving it again doubles the storage
    for no reader's benefit."""
    archive, client = _Archive(), _Client()
    sink = SqliteTraceSink(client, "llm_calls", trace_record_to_row, archiver=archive)

    await sink.write_batch([_record(SMALL)])

    assert archive.stored == {}
    assert client.inserted[0]["request_archive_key"] is None


@pytest.mark.asyncio
async def test_a_failed_archive_does_not_lose_the_row() -> None:
    """The row is the record; the archive is the appendix. A key that was
    never written must read as "not archived", never as "archived
    somewhere you cannot find"."""
    archive, client = _Archive(fail=True), _Client()
    sink = SqliteTraceSink(client, "llm_calls", trace_record_to_row, archiver=archive)

    await sink.write_batch([_record(HUGE)])

    assert len(client.inserted) == 1, "INFRA-08: a failed archive cost the trace row"
    assert client.inserted[0].get("request_archive_key") is None


@pytest.mark.asyncio
async def test_no_archiver_configured_still_writes_the_trace() -> None:
    client = _Client()
    sink = SqliteTraceSink(client, "llm_calls", trace_record_to_row)
    await sink.write_batch([_record(HUGE)])
    assert len(client.inserted) == 1


def test_the_column_exists_to_write_into(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    columns = {row[1] for row in conn.execute("pragma table_info(llm_calls)")}
    assert {"request_archive_key", "response_archive_key"} <= columns, (
        "INFRA-08: the trace has nowhere to record where the full body went"
    )


def test_the_broker_actually_builds_one() -> None:
    """The whole finding was that a complete, tested mechanism had no
    caller. A test of the mechanism alone would have passed before the fix
    and after it."""
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "PayloadArchiver(trace_client)" in source, (
        "INFRA-08: broker/app.py builds no archiver, so nothing is archived "
        "on any real run however well the archiver works"
    )
