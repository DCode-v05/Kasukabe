"""Tests for research/parquet_export.py.

Real DuckDB + a real temp-file SQLite database (built with the trace
store's own SqliteClient, so the schema under test is exactly the one
production uses) for the export itself - what matters here is that the
right rows land in the right file, which a mock cannot show. The S3
upload half is mocked (app/CONVENTIONS.md: "never call a real ... API in
a test"), the same FakeS3Client-monkeypatch technique
supabase/tests/test_supabase_sqlite_client.py uses for the same reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

import research.parquet_export as parquet_export_module
from research.parquet_export import (
    TABLE_TICK_COLUMNS,
    TICKS_PER_GAME_DAY,
    build_where_clause,
    export_game_day,
    export_game_day_sync,
)
from supabase.config import SupabaseConfig
from supabase.s3_client import S3Config
from supabase.sqlite_client import SqliteClient


def _sqlite_extension_available() -> bool:
    """Whether this host can read SQLite from DuckDB at all.

    EVAL-20. Nine tests in this file failed with
    `_duckdb.HTTPException ... sqlite_scanner ... HTTP 403` - a proxy
    blocking DuckDB's extension download. That is a missing prerequisite
    on the host, not a defect in the export, and a suite that reports it
    as nine red tests sends the reader to the wrong file.

    A SKIP, and a loud one via the summary hook in app/conftest.py, so a
    green run does not silently mean "the Parquet export was checked".
    """
    try:
        connection = duckdb.connect(":memory:")
        try:
            connection.execute("LOAD sqlite")
            return True
        except duckdb.Error:
            return False
        finally:
            connection.close()
    except duckdb.Error:
        return False


needs_sqlite_extension = pytest.mark.skipif(
    not _sqlite_extension_available(),
    reason=(
        "DuckDB's sqlite extension is not cached on this host and could not "
        "be downloaded - the Parquet export cannot be exercised here "
        "(EVAL-20)"
    ),
)

SCHEMA_TABLES = {
    "agents", "llm_calls", "events", "utterances", "memories", "transactions",
    "businesses", "organizations", "petitions", "cases", "buildings", "skills",
    "tools_registry", "relationships", "world_snapshots", "eval_metrics",
}


# ── TABLE_TICK_COLUMNS exhaustiveness ──────────────────────────────


def test_table_tick_columns_covers_every_table_in_the_real_schema() -> None:
    assert set(TABLE_TICK_COLUMNS.keys()) == SCHEMA_TABLES


def test_table_tick_columns_reference_real_columns_in_the_schema() -> None:
    schema_sql = (Path(__file__).resolve().parent.parent.parent / "supabase" / "sqlite_schema.sql").read_text(
        encoding="utf-8"
    )
    for table, columns in TABLE_TICK_COLUMNS.items():
        table_match = re.search(rf"create table if not exists {table} \((.*?)\n\);", schema_sql, re.DOTALL)
        assert table_match is not None, table
        table_body = table_match.group(1)
        for column in columns:
            # The column must exist and be an integer the exporter can bucket
            # on. NOT NULL is deliberately not required any more: the
            # trace-store fix made llm_calls.tick and events.tick nullable
            # because broker has no access to world's current tick, and
            # honestly leaving it unset beats defaulting to a false 0 that
            # would silently mis-bucket every broker row into game day zero
            # forever (see broker/trace.py's TraceRecord.tick).
            assert re.search(rf"\b{column} integer\b", table_body), f"{table}.{column}"


def test_ticks_per_game_day_matches_design_constant() -> None:
    assert TICKS_PER_GAME_DAY == 144


# ── build_where_clause ─────────────────────────────────────────────


def test_build_where_clause_single_column() -> None:
    assert build_where_clause(("tick",)) == "tick // 144 = ?"


def test_build_where_clause_multiple_columns_ored() -> None:
    assert build_where_clause(("birth_tick", "created_tick")) == "birth_tick // 144 = ? OR created_tick // 144 = ?"


# ── export_game_day_sync (real DuckDB, real SQLite) ────────────────


@pytest.fixture
def trace_store(tmp_path: Path) -> tuple[str, SqliteClient]:
    db_path = str(tmp_path / "kasukabe.sqlite3")
    return db_path, SqliteClient(SupabaseConfig(sqlite_path=db_path))


@pytest.mark.asyncio
@needs_sqlite_extension
async def test_export_partitions_a_single_tick_table_by_game_day(trace_store: tuple[str, SqliteClient], tmp_path: Path) -> None:
    db_path, client = trace_store
    await client.insert(
        "events",
        [
            {"type": "agent.moved", "tick": 0, "payload": {"n": "day0"}},
            {"type": "agent.moved", "tick": 143, "payload": {"n": "day0-late"}},
            {"type": "agent.moved", "tick": 144, "payload": {"n": "day1"}},
            {"type": "agent.moved", "tick": 300, "payload": {"n": "day2"}},
        ],
    )
    await client.close()

    out_dir = tmp_path / "export_day0"
    results = export_game_day_sync(sqlite_path=db_path, game_day=0, output_dir=out_dir)

    events_result = next(r for r in results if r.table == "events")
    assert events_result.row_count == 2
    rows = duckdb.connect().execute(f"SELECT payload FROM read_parquet('{events_result.local_path}')").fetchall()
    payloads = {row[0] for row in rows}
    assert all("day0" in payload for payload in payloads)
    assert any("day0-late" in payload for payload in payloads)


@pytest.mark.asyncio
@needs_sqlite_extension
async def test_export_produces_one_parquet_file_per_table(trace_store: tuple[str, SqliteClient], tmp_path: Path) -> None:
    db_path, client = trace_store
    await client.close()

    out_dir = tmp_path / "export_all_tables"
    results = export_game_day_sync(sqlite_path=db_path, game_day=0, output_dir=out_dir)

    assert {r.table for r in results} == SCHEMA_TABLES
    for result in results:
        assert result.local_path.exists()
        assert result.local_path.name == f"{result.table}.parquet"


@pytest.mark.asyncio
@needs_sqlite_extension
async def test_export_empty_game_day_writes_zero_row_parquet_with_correct_schema(
    trace_store: tuple[str, SqliteClient], tmp_path: Path
) -> None:
    db_path, client = trace_store
    await client.insert("events", [{"type": "agent.moved", "tick": 0, "payload": {}}])
    await client.close()

    out_dir = tmp_path / "export_quiet_day"
    results = export_game_day_sync(sqlite_path=db_path, game_day=5, output_dir=out_dir)

    events_result = next(r for r in results if r.table == "events")
    assert events_result.row_count == 0
    columns = duckdb.connect().execute(f"DESCRIBE SELECT * FROM read_parquet('{events_result.local_path}')").fetchall()
    # id, type, tick, agent_id, payload, created_at - the real, visible
    # (non-generated) columns of events per supabase/sqlite_schema.sql;
    # domain/game_day/game_minute_of_day are generated columns DuckDB's
    # sqlite scanner does not expose at all (see this module's docstring).
    # `model` joined this list with the trace-store fix: broker/trace.py's
    # BrokerEvent carries the lane model that served the call, and
    # supabase/schema_gen.py now generates this table's DDL from that
    # dataclass, so the two can no longer drift apart silently.
    assert {c[0] for c in columns} == {
        "id",
        "type",
        "tick",
        "agent_id",
        "model",
        "payload",
        "created_at",
    }


@pytest.mark.asyncio
@needs_sqlite_extension
async def test_export_agents_matches_birth_day_or_created_day(trace_store: tuple[str, SqliteClient], tmp_path: Path) -> None:
    db_path, client = trace_store
    await client.insert(
        "agents",
        [
            {
                "slug": "agent-001",
                "display_name": "Aiko Tanaka",
                "sex": "female",
                "birth_tick": 0,  # day 0
                "created_tick": 288,  # day 2
            }
        ],
    )
    await client.close()

    day0 = export_game_day_sync(sqlite_path=db_path, game_day=0, output_dir=tmp_path / "d0")
    day1 = export_game_day_sync(sqlite_path=db_path, game_day=1, output_dir=tmp_path / "d1")
    day2 = export_game_day_sync(sqlite_path=db_path, game_day=2, output_dir=tmp_path / "d2")

    def agents_count(results: list) -> int:
        return next(r for r in results if r.table == "agents").row_count

    assert agents_count(day0) == 1  # born day 0
    assert agents_count(day1) == 0  # nothing happened day 1
    assert agents_count(day2) == 1  # created (registered in the sim) day 2


@pytest.mark.asyncio
@needs_sqlite_extension
async def test_export_businesses_filters_by_registered_tick(trace_store: tuple[str, SqliteClient], tmp_path: Path) -> None:
    db_path, client = trace_store
    (agent,) = await client.insert(
        "agents", [{"slug": "agent-001", "display_name": "A", "sex": "male", "birth_tick": 0, "created_tick": 0}]
    )
    await client.insert(
        "businesses",
        [
            {
                "name": "Kasukabe Bakery",
                "business_type": "bakery",
                "owner_agent_id": agent["id"],
                "registered_tick": 200,  # day 1
            }
        ],
    )
    await client.close()

    day0 = export_game_day_sync(sqlite_path=db_path, game_day=0, output_dir=tmp_path / "d0")
    day1 = export_game_day_sync(sqlite_path=db_path, game_day=1, output_dir=tmp_path / "d1")

    assert next(r for r in day0 if r.table == "businesses").row_count == 0
    assert next(r for r in day1 if r.table == "businesses").row_count == 1


@pytest.mark.asyncio
@needs_sqlite_extension
async def test_export_does_not_write_to_the_source_database(trace_store: tuple[str, SqliteClient], tmp_path: Path) -> None:
    """READ_ONLY attach: this job must never become a second writer on the
    production WAL file (parquet_export.py's own module docstring).
    """
    db_path, client = trace_store
    await client.insert("events", [{"type": "agent.moved", "tick": 0, "payload": {}}])
    row_count_before = len(await client.select("events"))
    await client.close()

    export_game_day_sync(sqlite_path=db_path, game_day=0, output_dir=tmp_path / "out")

    verify_client = SqliteClient(SupabaseConfig(sqlite_path=db_path))
    row_count_after = len(await verify_client.select("events"))
    await verify_client.close()

    assert row_count_after == row_count_before


# ── export_game_day (async wrapper + S3 upload) ────────────────────


class FakeS3Client:
    instances: list["FakeS3Client"] = []

    def __init__(self, config: S3Config) -> None:
        self.config = config
        self.put_calls: list[tuple[str, bytes, str]] = []
        self.closed = False
        FakeS3Client.instances.append(self)

    async def put_object(self, *, key: str, data: bytes, content_type: str) -> None:
        self.put_calls.append((key, data, content_type))

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_fake_s3_instances() -> None:
    FakeS3Client.instances = []


@pytest.mark.asyncio
@needs_sqlite_extension
async def test_export_game_day_skips_s3_when_no_config_given(trace_store: tuple[str, SqliteClient], tmp_path: Path) -> None:
    db_path, client = trace_store
    await client.close()

    result = await export_game_day(sqlite_path=db_path, game_day=0, output_dir=tmp_path / "out")

    assert all(t.s3_key is None for t in result.tables)


@pytest.mark.asyncio
@needs_sqlite_extension
async def test_export_game_day_uploads_every_table_to_the_parquet_prefix_when_s3_configured(
    trace_store: tuple[str, SqliteClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parquet_export_module, "S3Client", FakeS3Client)
    db_path, client = trace_store
    await client.insert("events", [{"type": "agent.moved", "tick": 0, "payload": {}}])
    await client.close()

    result = await export_game_day(
        sqlite_path=db_path,
        game_day=0,
        output_dir=tmp_path / "out",
        s3_config=S3Config(bucket="kasukabe-genesis-123", region="ap-south-1"),
    )

    assert len(FakeS3Client.instances) == 1
    fake = FakeS3Client.instances[0]
    assert fake.closed is True
    events_upload = next(call for call in fake.put_calls if "events.parquet" in call[0])
    assert events_upload[0] == "exports/parquet/game_day=0/events.parquet"
    events_result = next(t for t in result.tables if t.table == "events")
    assert events_result.s3_key == "exports/parquet/game_day=0/events.parquet"


@pytest.mark.asyncio
async def test_game_day_export_result_total_rows(trace_store: tuple[str, SqliteClient], tmp_path: Path) -> None:
    db_path, client = trace_store
    await client.insert(
        "events",
        [{"type": "agent.moved", "tick": 0, "payload": {}}, {"type": "agent.moved", "tick": 1, "payload": {}}],
    )
    await client.close()

    result = await export_game_day(sqlite_path=db_path, game_day=0, output_dir=tmp_path / "out")

    assert result.total_rows == 2
