"""A trace sink must not report success into a file nobody can read.

The 20-resident genesis run of 31 Aug 2026 finished with:

    GET /admin/trace  ->  batches_attempted: 259, batches_failed: 0,
                          rows_dropped: 0, halted: false
    sqlite3 data/kasukabe.db "select count(*) from llm_calls"  ->  0

Both are true. The store was deleted between runs while the broker was
still up, so the broker's connection kept writing into an unlinked inode
- which SQLite does perfectly happily, since an open file descriptor
does not care that its directory entry is gone. Every insert committed.
Nothing failed. $4.01 of real inference was recorded into a file with no
name, and the health surface said the recording was fine.

The existing halt machinery is not at fault and does not need replacing:
BatchWriter already counts failures, halts after three, and app.py
already refuses dispatch while halted. The gap is that the sink never
raised, because from SQLite's point of view nothing went wrong. So the
sink has to check the one thing SQLite cannot: that the path it was
configured with still names the file it is holding open.

These tests use a real SQLite file on disk and delete it the way an
operator does - `rm`, on a live broker.

WINDOWS. The incident is not reproducible there, and the reason is worth
recording rather than working around: Windows refuses to unlink a file
another handle has open unless that handle was opened with
FILE_SHARE_DELETE, and SQLite does not ask for it. So `rm` on a live
broker fails outright instead of succeeding into an unnamed inode, and
the whole failure mode this file exists for cannot occur. The three tests
that need the unlink to SUCCEED in order to set themselves up are skipped
there - not because they are flaky, but because the operating system
already guarantees what they check. The control test, and the halt
machinery around it, still run everywhere.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

needs_unlink_of_an_open_file = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Windows refuses to unlink a file SQLite holds open, so a trace "
        "store cannot vanish out from under a live broker here"
    ),
)

from broker.trace import BatchWriter, SqliteTraceSink  # noqa: E402
from supabase.config import SupabaseConfig  # noqa: E402
from supabase.sqlite_client import SqliteClient  # noqa: E402


def _row(item: dict) -> dict:
    return dict(item)


def _client(tmp_path: Path) -> tuple[SqliteClient, Path]:
    path = tmp_path / "trace.db"
    return SqliteClient(SupabaseConfig(sqlite_path=str(path))), path


def _call(n: int) -> dict:
    """A real llm_calls row - every NOT NULL column the live schema has,
    so a failure here is the sink's and never the row's."""
    return {
        "agent_id": f"n{n:02d}",
        "payer_agent_id": f"n{n:02d}",
        "model": "moonshotai.kimi-k2.5",
        "requested_model": "moonshotai.kimi-k2.5",
        "call_class": "agent_turn",
        "purpose": "auto:agent_turn",
        "request_body": "{}",
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_nano_dollars": 10_000_000,
        "cents_debited": 1,
        "latency_ms": 900,
        "rate_limit_wait_ms": 0,
        "success": 1,
        "created_at": "2026-08-31T18:03:48Z",
    }


@pytest.mark.asyncio
async def test_a_batch_written_to_a_live_store_lands_and_is_readable(tmp_path: Path) -> None:
    """The control. If this ever goes red the rest of the file proves nothing."""
    client, path = _client(tmp_path)
    sink = SqliteTraceSink(client, "llm_calls", _row)
    await sink.write_batch([_call(1)])

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert conn.execute("select count(*) from llm_calls").fetchone()[0] == 1
    conn.close()


@needs_unlink_of_an_open_file
@pytest.mark.asyncio
async def test_writing_into_a_deleted_store_raises_instead_of_succeeding(
    tmp_path: Path,
) -> None:
    """The exact incident: the file is removed while the sink holds it open."""
    client, path = _client(tmp_path)
    sink = SqliteTraceSink(client, "llm_calls", _row)
    await sink.write_batch([_call(1)])

    path.unlink()

    with pytest.raises(Exception) as caught:
        await sink.write_batch([_call(2)])
    assert "no longer" in str(caught.value) or "vanish" in str(caught.value), (
        f"the refusal must say what happened, got: {caught.value!r}"
    )


@needs_unlink_of_an_open_file
@pytest.mark.asyncio
async def test_a_store_replaced_by_a_different_file_is_also_caught(
    tmp_path: Path,
) -> None:
    """The subtler half, and the one that actually happened: the path
    exists again, because another process recreated it. Checking only
    for a missing path would call this healthy while the rows go to the
    old inode."""
    client, path = _client(tmp_path)
    sink = SqliteTraceSink(client, "llm_calls", _row)
    await sink.write_batch([_call(1)])

    path.unlink()
    replacement = SqliteClient(SupabaseConfig(sqlite_path=str(path)))
    assert path.exists()

    with pytest.raises(Exception):
        await sink.write_batch([_call(2)])
    await asyncio.sleep(0)
    replacement.conn.close()


@needs_unlink_of_an_open_file
@pytest.mark.asyncio
async def test_the_writer_halts_and_says_so_on_admin_trace(tmp_path: Path) -> None:
    """End to end through the machinery that already exists: a vanished
    store must reach `halted: true`, which is what app.py refuses
    dispatch on. Silent loss becomes a stopped sim."""
    client, path = _client(tmp_path)
    sink = SqliteTraceSink(client, "llm_calls", _row)
    writer: BatchWriter = BatchWriter(sink=sink, batch_size=1, flush_interval_s=0.01)

    writer.record(_call(1))
    await writer.flush()
    assert not writer.is_halted, "a healthy store must not halt anything"

    path.unlink()
    for n in range(2, 6):
        writer.record(_call(n))
        await writer.flush()

    stats = writer.stats()
    assert stats["halted"] is True, f"a vanished store left the writer green: {stats}"
    assert stats["batches_failed"] >= 3
    assert stats["last_failure_message"], "a halt with no reason is not loud enough"
    await writer.close()


@pytest.mark.asyncio
async def test_an_in_memory_store_is_never_accused_of_vanishing(tmp_path: Path) -> None:
    """`:memory:` has no path to compare, and every test in this repo
    uses it. A guard that fails closed on it would take the suite down
    with it."""
    client = SqliteClient(SupabaseConfig(sqlite_path=":memory:"))
    sink = SqliteTraceSink(client, "llm_calls", _row)
    for n in range(5):
        await sink.write_batch([_call(n)])
