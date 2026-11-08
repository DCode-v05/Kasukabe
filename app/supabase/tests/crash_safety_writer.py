"""Subprocess entry point for test_supabase_crash_safety.py.

Not a pytest module (no test_ prefix, never imported by pytest) - a real,
separate OS process test_supabase_crash_safety.py launches with
subprocess.Popen and SIGKILLs mid-write. That is the whole point: SQLite
crash safety is a claim about what happens when the process holding the
write connection dies without warning, which a same-process mock or a
try/except around a simulated failure cannot exercise. This script IS the
crash.

Protocol with the parent test: writes batches of rows to the `events`
table, one batch per transaction, forever. Before starting batch N it
writes N to started_marker_path (fsynced); after committing batch N it
writes N to committed_marker_path (fsynced). Partway through each batch
(after inserting half its rows) it sleeps for CRASH_WINDOW_S - a
deliberately wide, fully-deterministic window in which the parent is
guaranteed to find the process "started N" but not yet "committed N",
so it can SIGKILL at a point mid-transaction and know exactly which batch
was in flight, without any timing race.

Run directly:
    python3 crash_safety_writer.py <db_path> <started_marker> <committed_marker> <rows_per_batch>
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from supabase.sqlite_client import SCHEMA_PATH, connect_sqlite  # noqa: E402

CRASH_WINDOW_S = 1.0


def write_marker(path: str, value: int) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(value))
        f.flush()
        os.fsync(f.fileno())


def main() -> None:
    db_path, started_marker, committed_marker, rows_per_batch_raw = sys.argv[1:5]
    rows_per_batch = int(rows_per_batch_raw)

    conn = connect_sqlite(db_path, busy_timeout_ms=5000)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()

    write_marker(started_marker, 0)
    write_marker(committed_marker, 0)

    batch = 0
    while True:
        batch += 1
        write_marker(started_marker, batch)

        base_tick = (batch - 1) * rows_per_batch
        for i in range(rows_per_batch):
            conn.execute(
                "insert into events (id, type, tick, payload) values (?, ?, ?, ?)",
                (f"evt_{uuid.uuid4().hex}", "agent.moved", base_tick + i, f'{{"batch":{batch},"row":{i}}}'),
            )
            if i == rows_per_batch // 2:
                time.sleep(CRASH_WINDOW_S)

        conn.commit()
        write_marker(committed_marker, batch)


if __name__ == "__main__":
    main()
