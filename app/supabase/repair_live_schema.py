"""One-off (but re-runnable and safe) repair tool for the live genesis
host's trace store, per TASK spec: "Repair the live DB at
/data/kasukabe.db on the host - I damaged it with ad-hoc ALTER TABLEs and
a rebuild that left a 'datatype mismatch'. Rebuild those tables from the
authoritative schema, preserving existing rows."

Deliberately pure stdlib (sqlite3, shutil, argparse - nothing from this
repo's own dependency set): the genesis host's system Python has none of
this project's pip-installed dependencies, and a repair tool for a
production database should not need a working venv to run at all.
`supabase/sqlite_schema.sql` (this package's own authoritative DDL,
generated for llm_calls/events by schema_gen.py) is read directly off
disk relative to this file, so there is exactly one place that schema is
defined - this script never carries its own copy that could drift from
it.

Live diagnosis (2026-07-31, against the actual /data/kasukabe.db this
script targets): `llm_calls`/`events` had been ALTERed into tables with no
NOT NULL/CHECK constraints at all and columns from both the old and new
contracts mixed together loosely typed; `transactions.related_llm_call_id`
referenced a phantom `"llm_calls_old"` table, debris from an aborted
rebuild attempt. Every one of `llm_calls`/`events`/`transactions` held
zero rows - the run never successfully wrote a single one (see
broker/trace.py's module docstring for why) - so "preserving existing
rows" is satisfiable, not hypothetical, but this script does not assume
that stays true forever: it counts rows in each target table immediately
before touching anything and refuses to drop a table that is not
genuinely empty, rather than silently discarding real data because a
comment predicted it would be empty.

Usage:
    python3 repair_live_schema.py /data/kasukabe.db
    python3 repair_live_schema.py /data/kasukabe.db --dry-run
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "sqlite_schema.sql"

# The three tables the 2026-07-31 incident actually damaged (see module
# docstring). Deliberately a fixed, named list rather than "every table in
# the schema" - dropping and rebuilding a table this script has not been
# specifically told is broken (agents, businesses, ...) is exactly the
# kind of "quickly fix something nearby" this repo's task split forbids;
# a repair tool for production data should do only what it was asked to.
TARGET_TABLES = ("llm_calls", "events", "transactions")


class RepairAbortedError(RuntimeError):
    """Raised instead of dropping anything when a target table is not
    actually empty - see module docstring: never silently discard rows."""


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?", (table,)
    ).fetchone()
    return row is not None


def row_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"select count(*) from {table}").fetchone()[0]  # noqa: S608 - fixed table list, not user input


def backup_database(db_path: Path) -> Path:
    """A consistent, single-file snapshot taken through SQLite's own
    backup API (not a raw file copy) - correct even while the source has
    an active WAL, which a plain `cp` of just the .db file is not (the
    WAL may hold committed pages the main file does not yet have).
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.name}.pre-repair-{timestamp}.bak")
    source = sqlite3.connect(str(db_path))
    try:
        dest = sqlite3.connect(str(backup_path))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return backup_path


def repair(db_path: str, *, dry_run: bool = False) -> str:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"no database at {db_path!r}")

    report_lines: list[str] = []

    def log(line: str) -> None:
        report_lines.append(line)
        print(line)

    log(f"repair_live_schema: target={db_path} dry_run={dry_run}")

    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = OFF")  # dropping/recreating tables mid-repair
    try:
        pre_counts: dict[str, "int | None"] = {}
        for table in TARGET_TABLES:
            if table_exists(conn, table):
                count = row_count(conn, table)
                pre_counts[table] = count
                log(f"  {table}: exists, {count} row(s)")
                if count > 0:
                    raise RepairAbortedError(
                        f"{table} has {count} row(s) - refusing to drop a table that is not "
                        f"empty. This script only ever rebuilds a genuinely empty table; a "
                        f"table with real rows needs a real data migration, not this tool."
                    )
            else:
                pre_counts[table] = None
                log(f"  {table}: does not exist yet")

        if dry_run:
            log("dry-run: would back up, drop the empty target tables above, and reapply "
                "supabase/sqlite_schema.sql. No changes made.")
            return "\n".join(report_lines)

        backup_path = backup_database(path)
        log(f"  backed up to {backup_path} (SQLite backup API, WAL-safe)")

        for table in TARGET_TABLES:
            if pre_counts[table] is not None:  # existed and was confirmed empty above
                conn.execute(f"drop table {table}")  # noqa: S608 - fixed table list
                log(f"  dropped empty {table}")
        conn.commit()

        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(schema_sql)  # every statement is IF NOT EXISTS - a no-op for
        conn.commit()                    # every table this script did not just drop
        log(f"  reapplied {SCHEMA_PATH.name} ({len(schema_sql)} bytes)")

        integrity = conn.execute("PRAGMA integrity_check").fetchall()
        log(f"  integrity_check: {integrity}")
        if integrity != [("ok",)]:
            raise RuntimeError(f"integrity_check failed after repair: {integrity}")

        for table in TARGET_TABLES:
            count = row_count(conn, table)
            columns = [r[1] for r in conn.execute(f"PRAGMA table_xinfo({table})").fetchall()]  # noqa: S608
            log(f"  post-repair {table}: {count} row(s), columns={columns}")

        # Checkpoint the WAL so the rebuilt schema is durable in the main
        # file immediately, not left waiting for SQLite's own checkpoint
        # policy - the same reasoning connect_sqlite()'s crash-safety
        # pragmas exist for elsewhere in this package.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        log("  WAL checkpointed")
    finally:
        conn.close()

    log("repair_live_schema: done")
    return "\n".join(report_lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", help="path to the live SQLite trace store, e.g. /data/kasukabe.db")
    parser.add_argument("--dry-run", action="store_true", help="report what would happen, change nothing")
    args = parser.parse_args()

    try:
        repair(args.db_path, dry_run=args.dry_run)
    except RepairAbortedError as exc:
        print(f"ABORTED (no changes made): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
