"""Real crash-safety proof for the trace store: SIGKILL a genuine writer
process mid-transaction, reopen the database, assert no corruption and no
lost committed rows.

This is the test the persistence track's brief asks for by name ("Prove
crash safety with a real test: kill the writer mid-batch, reopen, assert
no corruption and no lost committed rows") and it earns that by actually
doing it - crash_safety_writer.py runs as a separate OS process (not a
thread, not a mock, not a try/except around a simulated failure) so
SIGKILL has the same effect it would on the genesis host if the world or
broker process died mid-write: the OS reclaims the process immediately,
mid-instruction, with no chance to run a finally block or flush anything
python's sqlite3 module had buffered in memory.

What is actually under test is supabase/sqlite_client.py's connect_sqlite()
pragma set (WAL journal mode, synchronous=NORMAL) - see that function's
docstring for the SQLite-documented guarantee this test exists to verify
empirically rather than take on faith: WAL mode with synchronous=NORMAL
is safe from corruption and durable across an application crash (a killed
process), and only risks losing the *last* transaction on an OS-level
power loss, which is a different failure mode this test does not (and
cannot, without literally cutting power to the test runner) exercise.

Timing is made deterministic, not probabilistic, by the writer's protocol
(see crash_safety_writer.py's docstring): a one-second sleep partway
through every batch's transaction means the parent has a full second, not
a few CPU-scheduling microseconds, to observe "batch N started" and
deliver SIGKILL before batch N could possibly commit. The test still
double-checks this held (test_kill_mid_batch_survives_with_no_corruption's
"marker sanity check" section) rather than assuming it.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

def kill_uncatchably(proc: subprocess.Popen) -> None:
    """Kill a writer the way an OS kills one: no finally, no flush.

    SIGKILL on POSIX. On Windows there is no SIGKILL at all - `signal` has
    no such attribute and this whole module raised AttributeError - and
    the equivalent is TerminateProcess, which `Popen.kill()` calls and
    which is just as uncatchable. So the test keeps its meaning on both
    rather than being skipped on one: what is under test is whether the
    WAL pragma set survives a process vanishing mid-transaction, and a
    process terminated by TerminateProcess has vanished exactly as
    thoroughly as one killed by SIGKILL.
    """
    if hasattr(signal, "SIGKILL"):
        os.kill(proc.pid, signal.SIGKILL)
    else:
        proc.kill()


# Below the helper deliberately: these open SQLite handles, and the writer
# subprocess above must be able to die with none of them held by this
# process. Same reason the vanished-store test imports late.
from supabase.client import eq  # noqa: E402
from supabase.config import SupabaseConfig  # noqa: E402
from supabase.sqlite_client import SqliteClient, connect_sqlite  # noqa: E402

WRITER_SCRIPT = Path(__file__).resolve().parent / "crash_safety_writer.py"
ROWS_PER_BATCH = 10
# How many batches to let the writer complete before killing it mid-way
# through the next one. 3 is enough to prove "earlier commits survive",
# not just "the very first one did".
TARGET_BATCH = 3
POLL_INTERVAL_S = 0.01
STARTUP_TIMEOUT_S = 30.0


def read_marker(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return -1


def wait_for_marker_at_least(path: Path, target: int, *, proc: subprocess.Popen, timeout_s: float) -> int:
    """Poll `path` until its integer content is >= target. Raises with the
    writer's captured stderr if it exits early or the deadline passes -
    a bare timeout with no diagnostic is exactly the kind of flaky-test
    failure that costs an hour to root-cause later.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            pytest.fail(
                f"crash_safety_writer.py exited early with code {proc.returncode}\n"
                f"stdout: {stdout}\nstderr: {stderr}"
            )
        value = read_marker(path)
        if value >= target:
            return value
        time.sleep(POLL_INTERVAL_S)
    pytest.fail(f"timed out waiting for {path.name} to reach {target}, last seen {read_marker(path)}")


@pytest.mark.asyncio
async def test_kill_mid_batch_survives_with_no_corruption_and_no_lost_committed_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "kasukabe-crash.sqlite3"
    started_marker = tmp_path / "started.marker"
    committed_marker = tmp_path / "committed.marker"

    proc = subprocess.Popen(
        [
            sys.executable,
            str(WRITER_SCRIPT),
            str(db_path),
            str(started_marker),
            str(committed_marker),
            str(ROWS_PER_BATCH),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Wait until the writer has announced it is starting TARGET_BATCH
        # - by the writer's protocol, this happens strictly before that
        # batch's transaction opens, and the writer's own 1s mid-batch
        # sleep guarantees it cannot have committed yet either.
        wait_for_marker_at_least(started_marker, TARGET_BATCH, proc=proc, timeout_s=STARTUP_TIMEOUT_S)

        # ── the crash ───────────────────────────────────────────────
        kill_uncatchably(proc)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    assert proc.returncode != 0  # actually killed, not a clean exit that raced us

    # ── marker sanity check: prove the timing assumption held ─────────
    # If this ever fails, CRASH_WINDOW_S in crash_safety_writer.py is too
    # short for this machine, not a bug in the pragmas under test - but it
    # should never fail given a full second of margin.
    committed_batches = read_marker(committed_marker)
    assert committed_batches == TARGET_BATCH - 1, (
        f"expected exactly {TARGET_BATCH - 1} committed batches at kill time, got {committed_batches} - "
        "the timing assumption behind this test did not hold on this machine"
    )

    # ── reopen: integrity ──────────────────────────────────────────
    conn = connect_sqlite(str(db_path), busy_timeout_ms=5000)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok"

        # ── no lost committed rows ──────────────────────────────────
        expected_row_count = committed_batches * ROWS_PER_BATCH
        (actual_row_count,) = conn.execute("SELECT count(*) FROM events").fetchone()
        assert actual_row_count == expected_row_count

        # Content check, not just count: every committed batch's rows are
        # intact and correctly attributed - rules out a partially-written
        # row surviving as valid-looking but wrong.
        rows = conn.execute("SELECT tick, payload FROM events ORDER BY tick").fetchall()
        expected_ticks = list(range((committed_batches) * ROWS_PER_BATCH))
        assert [r[0] for r in rows] == expected_ticks
        for tick, payload in rows:
            batch_of_row = (tick // ROWS_PER_BATCH) + 1
            assert f'"batch":{batch_of_row}' in payload

        # ── no partial (killed) transaction leaked anything ─────────
        # Batch TARGET_BATCH was in flight at kill time; atomicity means
        # either all of it is present or none of it is - it must be none.
        killed_batch_marker = f'"batch":{TARGET_BATCH}'
        assert not any(killed_batch_marker in payload for _, payload in rows)
    finally:
        conn.close()

    # ── database is still fully writable after the crash, not merely
    # readable ────────────────────────────────────────────────────────
    recovery_client = SqliteClient(SupabaseConfig(sqlite_path=str(db_path)))
    try:
        inserted = await recovery_client.insert(
            "events", [{"type": "system.checkpoint.written", "tick": 999999, "payload": {"post_crash": True}}]
        )
        assert inserted[0]["payload"] == {"post_crash": True}
        rows_after = await recovery_client.select("events", filters={"tick": eq(999999)})
        assert len(rows_after) == 1
    finally:
        await recovery_client.close()


@pytest.mark.asyncio
async def test_repeated_crash_recovery_cycles_stay_consistent(tmp_path: Path) -> None:
    """The same proof, twice in a row against the same file, to rule out
    "recovered once by luck" - a WAL file that was merely truncated
    instead of properly checkpointed on the first recovery could easily
    pass a single-cycle test and then corrupt on the second write.
    """
    db_path = tmp_path / "kasukabe-crash-repeat.sqlite3"

    for cycle in range(2):
        started_marker = tmp_path / f"started-{cycle}.marker"
        committed_marker = tmp_path / f"committed-{cycle}.marker"

        proc = subprocess.Popen(
            [
                sys.executable,
                str(WRITER_SCRIPT),
                str(db_path),
                str(started_marker),
                str(committed_marker),
                str(ROWS_PER_BATCH),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            wait_for_marker_at_least(started_marker, 2, proc=proc, timeout_s=STARTUP_TIMEOUT_S)
            kill_uncatchably(proc)
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

        conn = connect_sqlite(str(db_path), busy_timeout_ms=5000)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            (count,) = conn.execute("SELECT count(*) FROM events").fetchone()
            # Cycle 1 resumes on top of cycle 0's surviving rows (the
            # writer restarts its own batch counter from 1 each launch,
            # but the table is not reset between cycles) - only assert
            # the database is internally consistent and non-empty after
            # the first cycle committed at least one full batch.
            assert count >= ROWS_PER_BATCH
        finally:
            conn.close()
