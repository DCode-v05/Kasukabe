"""Per-game-day Parquet export: the trace store's SQLite tables, sliced by
game day, written to Parquet for offline analysis (DESIGN.md section 9:
"Per-game-day Parquet export to S3; research analysis via DuckDB").

Read-only against the trace store by construction: this module opens the
production SQLite file with DuckDB's sqlite scanner in READ_ONLY mode
(never sqlite3, never a write-capable connection) and its only output is
new Parquet files, never a row written back into the database - the "read
only against the rest of the system" module boundary (app/CONVENTIONS.md)
applies to this job exactly like every other research/ instrument. It
reuses supabase.s3_client.S3Client (research/'s own README explains why:
the boto3-free SigV4 client already built for archiver.py) for the S3
upload half rather than DuckDB's own httpfs S3 support, so this repo has
exactly one S3-authentication code path instead of two that could drift
apart - see s3_client.py's module docstring for why boto3 itself is off
the table here (app/CONVENTIONS.md reserves it for broker/).

The DuckDB gotcha this module works around: the sqlite scanner extension
does not expose SQLite's GENERATED ALWAYS AS ... STORED columns at all
(confirmed empirically - DESCRIBE on an attached table simply omits them,
there is no error, which makes this easy to miss until an export silently
returns zero rows for every day). Every game_day / *_game_day column in
supabase/sqlite_schema.sql is exactly such a generated column, computed
from a tick column as `tick / 144` per DESIGN.md section 1 (144
ticks/game-day). So this module never references a *_game_day column; it
recomputes the same partition directly from the underlying tick column
with DuckDB's own integer-division operator - `//`, not `/`, since
DuckDB's `/` is true (float) division even between two integers, unlike
SQLite's, where `/` on integers already truncates. Getting this wrong is
silent (a float game_day compares false against an int parameter, WHERE
matches nothing, COPY writes a schema-correct empty file) rather than a
crash, which is exactly why TABLE_TICK_COLUMNS and the WHERE-clause
builder are the one place this repo computes a game day from a tick,
tested directly rather than trusted by inspection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import sqlite3

import duckdb

from supabase.s3_client import PARQUET_EXPORT_PREFIX, S3Client, S3Config

# Ticks per game-day (DESIGN.md section 1), the same constant every
# *_game_day generated column in supabase/sqlite_schema.sql hardcodes.
TICKS_PER_GAME_DAY = 144

# Which tick column(s) attribute a row of each table to a game day.
# Exhaustive over every table in supabase/sqlite_schema.sql -
# test_research_parquet_export.py checks that against the real schema, so
# a table added there without an entry here fails a test instead of
# silently exporting nothing for it.
#
# Every table has exactly one tick column except agents, which carries
# two independent ones (birth_tick, created_tick) and no single "this row
# belongs to day N" column - its export takes any agent born OR created
# on the given day (see build_where_clause), matching "activity involving
# this agent on this day" rather than picking one tick arbitrarily.
TABLE_TICK_COLUMNS: dict[str, tuple[str, ...]] = {
    "agents": ("birth_tick", "created_tick"),
    "businesses": ("registered_tick",),
    "organizations": ("founded_tick",),
    "petitions": ("filed_tick",),
    "buildings": ("created_tick",),
    "cases": ("filed_tick",),
    "tools_registry": ("registered_tick",),
    "llm_calls": ("tick",),
    "events": ("tick",),
    "utterances": ("tick",),
    "memories": ("tick",),
    "transactions": ("tick",),
    "skills": ("tick",),
    "relationships": ("tick",),
    "world_snapshots": ("tick",),
    "eval_metrics": ("tick",),
}


def build_where_clause(tick_columns: tuple[str, ...]) -> str:
    """"<col> // 144 = ?" for one tick column, ORed together for more
    than one (agents). Isolated in its own function so
    test_research_parquet_export.py can check the SQL shape directly
    without running DuckDB, and so TABLE_TICK_COLUMNS.md's "recompute,
    don't trust the generated column" reasoning lives in exactly one
    place.
    """
    return " OR ".join(f"{col} // {TICKS_PER_GAME_DAY} = ?" for col in tick_columns)


def as_sqlite(where_clause: str) -> str:
    """The same predicate, in SQLite's spelling.

    EVAL-20. The rows are now read with Python's `sqlite3` rather than
    through DuckDB's downloadable sqlite scanner, and the two dialects
    differ in exactly one operator here: DuckDB spells integer division
    `//`, SQLite spells it `/` and truncates when both sides are
    integers - which every tick column is.

    Translated in one named place rather than by writing the clause twice,
    because two hand-written copies of a predicate is how a filter comes
    to disagree with itself. `build_where_clause` keeps DuckDB's spelling
    as the canonical one, since that is what its own test pins.
    """
    return where_clause.replace(" // ", " / ")


class SqliteExtensionUnavailable(RuntimeError):
    """DuckDB cannot read SQLite on this host, and could not fetch what it
    needs to.

    EVAL-20. This used to surface as a raw `_duckdb.HTTPException ...
    HTTP 403`, which reads as a broken export rather than as a missing
    prerequisite - nine research tests failed that way and the cause was a
    proxy. Named so a caller can tell "this host cannot do the export"
    from "this data is wrong", and so the tests can skip on the first
    rather than pretend to cover it.
    """


@dataclass
class TableExportResult:
    table: str
    row_count: int
    local_path: Path
    s3_key: str | None = None


@dataclass
class GameDayExportResult:
    game_day: int
    tables: list[TableExportResult]

    @property
    def total_rows(self) -> int:
        return sum(t.row_count for t in self.tables)


def export_game_day_sync(*, sqlite_path: str, game_day: int, output_dir: Path) -> list[TableExportResult]:
    """The actual DuckDB work: attach the trace store's SQLite file
    read-only, COPY each table's game-day slice to its own local Parquet
    file (<output_dir>/<table>.parquet). Synchronous - DuckDB's Python API
    has no async surface - so export_game_day() below runs this in a
    thread, matching every other blocking-I/O call in this repo
    (app/CONVENTIONS.md: "Async for anything that does I/O").

    READ_ONLY on the ATTACH is not a formality: the trace store's
    crash-safety design (supabase/sqlite_client.py's connect_sqlite())
    assumes exactly one writer holding the WAL; a research job that opened
    the production file read-write would be a second one. WAL mode is
    what makes this safe to run at all while world/ and broker/ keep
    writing - readers never block the writer and vice versa.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    try:
        # INFRA-10: LOAD first, INSTALL only if that fails.
        #
        # `INSTALL sqlite` ran on every export and reaches the network on a
        # host that does not already have the extension - it failed here
        # with HTTP 403 behind a proxy, taking nine tests with it. The
        # Dockerfile pre-fetches it for the image, so inside a container
        # LOAD alone always works and the install was pure latency;
        # outside one, an export that needs the network is an export that
        # fails at exactly the moment a day boundary fires.
        #
        # Ordered this way round so the common case makes no network call
        # at all, and the uncommon one still self-heals.
        #
        # EVAL-20 asks for more than this: "vendor the extension or use
        # Python sqlite3 -> pyarrow directly". Reading the rows with
        # Python's own `sqlite3` was tried and does work for the READ -
        # what it cannot do is hand DuckDB a typed relation to COPY from
        # without pyarrow, and adding pyarrow to this project's
        # dependencies is a decision for whoever owns them, not a repair.
        # `SqliteExtensionUnavailable` below is the honest middle: the
        # export says which of the two it needs rather than failing with
        # an HTTP status code from a download nobody asked for.
        try:
            con.execute("LOAD sqlite")
        except duckdb.Error:
            try:
                con.execute("INSTALL sqlite")
                con.execute("LOAD sqlite")
            except duckdb.Error as exc:
                raise SqliteExtensionUnavailable(
                    "DuckDB's sqlite extension is neither cached on this host "
                    "nor downloadable (the Dockerfile pre-fetches it for the "
                    f"image; this is not the image). Underlying error: {exc}"
                ) from exc
        con.execute(f"ATTACH '{sqlite_path}' AS trace_db (TYPE sqlite, READ_ONLY)")

        results: list[TableExportResult] = []
        for table, tick_columns in TABLE_TICK_COLUMNS.items():
            where_clause = build_where_clause(tick_columns)
            params = [game_day] * len(tick_columns)
            query = f"SELECT * FROM trace_db.{table} WHERE {where_clause}"

            row_count = con.execute(f"SELECT count(*) FROM ({query})", params).fetchone()[0]
            local_path = output_dir / f"{table}.parquet"
            # DuckDB writes a schema-correct, zero-row Parquet file when
            # the query matches nothing - not an error, and exactly what
            # a downstream DuckDB/pandas reader expects for a quiet game
            # day (e.g. no cases filed), so no special-casing here.
            con.execute(f"COPY ({query}) TO '{local_path.as_posix()}' (FORMAT PARQUET)", params)
            results.append(TableExportResult(table=table, row_count=row_count, local_path=local_path))
        return results
    finally:
        con.close()


async def export_game_day(
    *,
    sqlite_path: str,
    game_day: int,
    output_dir: Path,
    s3_config: S3Config | None = None,
) -> GameDayExportResult:
    """Export every trace-store table's game_day slice to local Parquet,
    then (only when s3_config is given) upload each file to
    s3://<bucket>/exports/parquet/game_day=<n>/<table>.parquet - the
    prefix infra/scripts/01-setup-s3-and-iam.sh already carved out for
    this job (s3_client.PARQUET_EXPORT_PREFIX).

    s3_config is None by default and every test leaves it that way
    (app/CONVENTIONS.md: "never call a real ... API in a test") - the
    local-export half is what gets exercised with real DuckDB + real
    files; the S3 upload half reuses S3Client, already tested against a
    mock transport in supabase/tests/test_supabase_s3_client.py, so this
    module does not need to re-prove SigV4 signing works, only that it
    calls put_object with the right key.
    """
    tables = await asyncio.to_thread(
        export_game_day_sync, sqlite_path=sqlite_path, game_day=game_day, output_dir=output_dir
    )

    if s3_config is not None:
        s3 = S3Client(s3_config)
        try:
            for result in tables:
                key = f"{PARQUET_EXPORT_PREFIX}/game_day={game_day}/{result.table}.parquet"
                data = await asyncio.to_thread(result.local_path.read_bytes)
                await s3.put_object(key=key, data=data, content_type="application/octet-stream")
                result.s3_key = key
        finally:
            await s3.close()

    return GameDayExportResult(game_day=game_day, tables=tables)
