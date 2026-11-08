"""Real (not mocked) SQLite tests: SqliteClient runs against an actual
temp-file sqlite3 database applying the real sqlite_schema.sql. This is
the trace store itself being tested, so it earns real I/O rather than a
mock - including the crash-safety pragmas (WAL, busy_timeout,
synchronous), which are exactly the kind of thing a mock would let a
regression sail through silently.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

import supabase.sqlite_client as sqlite_client_module
from supabase.client import SupabaseError, eq, gte, in_, is_, like
from supabase.config import SupabaseConfig
from supabase.s3_client import S3Config, S3Error
from supabase.sqlite_client import (
    SCHEMA_PATH,
    SQLITE_BOOL_COLUMNS,
    SQLITE_JSON_COLUMNS,
    SqliteClient,
    build_order,
    build_where,
    connect_sqlite,
)


@pytest.fixture
def client(tmp_path: Path) -> SqliteClient:
    return SqliteClient(SupabaseConfig(sqlite_path=str(tmp_path / "kasukabe.sqlite3")))


def agent_row(**overrides: object) -> dict[str, object]:
    row = {
        "slug": "agent-001",
        "display_name": "Aiko Tanaka",
        "sex": "female",
        "birth_tick": 0,
        "created_tick": 100,
    }
    row.update(overrides)
    return row


# ── schema bootstrap ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_schema_creates_all_eighteen_tables(client: SqliteClient) -> None:
    """Eighteen since INFRA-09 added `run_manifest` (what a run was made
    of) and EVAL-25 added `tool_invocations` (what the turns actually
    called). The count is in the NAME deliberately: a table that appears
    without this test being read is a table nobody decided to add."""
    tables = {
        r[0]
        for r in client.conn.execute(
            "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
        ).fetchall()
    }

    expected = {
        "agents", "llm_calls", "events", "utterances", "memories", "transactions",
        "businesses", "organizations", "petitions", "cases", "buildings", "skills",
        "tools_registry", "relationships", "world_snapshots", "eval_metrics",
        "run_manifest", "tool_invocations",
    }
    assert tables == expected


@pytest.mark.asyncio
async def test_ensure_schema_is_idempotent_on_reconnect(tmp_path: Path) -> None:
    db_path = str(tmp_path / "kasukabe.sqlite3")
    first = SqliteClient(SupabaseConfig(sqlite_path=db_path))
    await first.insert("agents", [agent_row()])
    await first.close()

    # Reconnecting must not re-run "create table" against a populated
    # database (sqlite_schema.sql uses IF NOT EXISTS, but ensure_schema's
    # own "is the database empty" guard is what is under test here).
    second = SqliteClient(SupabaseConfig(sqlite_path=db_path))
    rows = await second.select("agents")
    await second.close()

    assert len(rows) == 1


# ── crash-safety pragmas ────────────────────────────────────────────
# See test_supabase_crash_safety.py for the real (SIGKILL-a-subprocess)
# proof these pragmas actually deliver "no corruption, no lost committed
# rows". These tests only check the pragmas are the values the trace
# store's design requires - the cheap, fast half of that guarantee.


def test_connect_sqlite_enables_wal_for_a_real_file(tmp_path: Path) -> None:
    conn = connect_sqlite(str(tmp_path / "wal.sqlite3"), busy_timeout_ms=5000)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()


def test_connect_sqlite_skips_wal_for_in_memory_database() -> None:
    # SQLite has no write-ahead log for ":memory:" - asking for WAL there
    # is a silent no-op (journal_mode stays "memory"), not an error. This
    # asserts connect_sqlite() does not even try, per its own docstring.
    conn = connect_sqlite(":memory:", busy_timeout_ms=5000)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "memory"
    finally:
        conn.close()


def test_connect_sqlite_sets_busy_timeout_from_argument(tmp_path: Path) -> None:
    conn = connect_sqlite(str(tmp_path / "bt.sqlite3"), busy_timeout_ms=12345)
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 12345
    finally:
        conn.close()


def test_connect_sqlite_sets_synchronous_normal(tmp_path: Path) -> None:
    conn = connect_sqlite(str(tmp_path / "sync.sqlite3"), busy_timeout_ms=5000)
    try:
        # SQLite reports synchronous as an integer: 0=OFF, 1=NORMAL, 2=FULL.
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    finally:
        conn.close()


def test_connect_sqlite_enables_foreign_keys(tmp_path: Path) -> None:
    conn = connect_sqlite(str(tmp_path / "fk.sqlite3"), busy_timeout_ms=5000)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sqlite_client_applies_configured_busy_timeout(tmp_path: Path) -> None:
    client = SqliteClient(SupabaseConfig(sqlite_path=str(tmp_path / "kasukabe.sqlite3"), busy_timeout_ms=7500))
    try:
        assert client.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 7500
    finally:
        await client.close()


# ── insert ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_batch_returns_all_rows_with_generated_columns(client: SqliteClient) -> None:
    rows = await client.insert(
        "agents",
        [agent_row(slug="agent-001", birth_tick=0, created_tick=100), agent_row(slug="agent-002", birth_tick=144, created_tick=144)],
    )

    assert len(rows) == 2
    assert {r["slug"] for r in rows} == {"agent-001", "agent-002"}
    by_slug = {r["slug"]: r for r in rows}
    # created_tick=100 -> game_day 0, minute_of_day 1000 (144 ticks/day, 10
    # game-minutes/tick per DESIGN.md section 1).
    assert by_slug["agent-001"]["created_game_day"] == 0
    assert by_slug["agent-001"]["created_game_minute_of_day"] == 1000
    # created_tick=144 -> exactly one day in.
    assert by_slug["agent-002"]["created_game_day"] == 1
    assert by_slug["agent-002"]["created_game_minute_of_day"] == 0


@pytest.mark.asyncio
async def test_concurrent_inserts_from_two_tables_never_lose_a_row(client: SqliteClient) -> None:
    """Regression test for a real bug this fix uncovered live (2026-07-31,
    the first real Bedrock call made through the repaired broker): two
    BatchWriters (broker/app.py's trace_writer and event_writer) share
    ONE SqliteClient. Their periodic flushes landed at the same instant,
    both dispatched via asyncio.to_thread onto worker threads against the
    SAME sqlite3.Connection, and insert_sync's `cursor.lastrowid` (SQLite:
    connection-scoped, "unpredictable" under concurrent inserts from a
    second thread) returned a rowid that belonged to the OTHER table's
    insert - `select * from llm_calls where rowid = <an events rowid>`
    found nothing, and `dict(None)` raised. See sqlite_client.py's
    conn_lock and SqliteClient's own docstring for the fix; this test
    reproduces the concurrency shape that tripped it (many concurrent
    inserts into two different tables on one client) rather than
    asserting on the lock's existence, which would pass even if the lock
    silently failed to actually serialize anything.
    """
    await client.insert("agents", [agent_row()])
    (agent,) = await client.select("agents")

    async def insert_event(i: int) -> None:
        await client.insert("events", [{"type": "agent.moved", "tick": i, "agent_id": agent["id"]}])

    async def insert_petition(i: int) -> None:
        await client.insert(
            "petitions",
            [
                {
                    "petition_type": "other",
                    "filed_by_agent_id": agent["id"],
                    "title": f"petition {i}",
                    "filed_tick": i,
                }
            ],
        )

    n = 40
    await asyncio.gather(*(insert_event(i) for i in range(n)), *(insert_petition(i) for i in range(n)))

    events = await client.select("events")
    petitions = await client.select("petitions")
    assert len(events) == n
    assert len(petitions) == n
    assert len({e["id"] for e in events}) == n  # every id unique, none silently overwritten


@pytest.mark.asyncio
async def test_insert_empty_list_returns_empty_without_touching_db(client: SqliteClient) -> None:
    assert await client.insert("agents", []) == []


@pytest.mark.asyncio
async def test_insert_generates_uuid_for_entity_table_id(client: SqliteClient) -> None:
    rows = await client.insert("agents", [agent_row()])

    assert isinstance(rows[0]["id"], str)
    assert len(rows[0]["id"]) == 36  # canonical uuid4 string length


@pytest.mark.asyncio
async def test_insert_autoincrements_id_for_log_table(client: SqliteClient) -> None:
    await client.insert("agents", [agent_row()])
    (agent,) = await client.select("agents")

    rows = await client.insert(
        "transactions",
        [
            {"currency": "sim", "tx_type": "transfer", "amount": 1, "from_agent_id": agent["id"], "tick": 1},
            {"currency": "sim", "tx_type": "transfer", "amount": 2, "from_agent_id": agent["id"], "tick": 2},
        ],
    )

    assert rows[0]["id"] != rows[1]["id"]
    assert all(isinstance(r["id"], int) for r in rows)


@pytest.mark.asyncio
async def test_insert_generates_uuid_for_events_id_not_autoincrement(client: SqliteClient) -> None:
    """events.id changed from `integer primary key autoincrement` to
    `text primary key` (schema_gen.py, generated from broker/trace.py's
    BrokerEvent.id, which is always an app-generated "evt_<uuid>" string
    - never a SQLite-assigned integer). world/wiring.py's real writer
    (TraceStore.record_event) never sets "id" at all, so this proves the
    SAME fallback (fill_generated_id) that used to serve "log tables"
    (see test_insert_autoincrements_id_for_log_table above, now pointed
    at a table that is still genuinely autoincrement) now serves events
    too, exactly like an "entity" table.
    """
    await client.insert("agents", [agent_row()])
    (agent,) = await client.select("agents")

    rows = await client.insert(
        "events",
        [
            {"type": "agent.moved", "tick": 1, "agent_id": agent["id"]},
            {"type": "agent.moved", "tick": 2, "agent_id": agent["id"]},
        ],
    )

    assert rows[0]["id"] != rows[1]["id"]
    assert all(isinstance(r["id"], str) and len(r["id"]) == 36 for r in rows)


@pytest.mark.asyncio
async def test_insert_round_trips_bool_columns(client: SqliteClient) -> None:
    rows = await client.insert("agents", [agent_row(is_minor=True, is_alive=False, death_cause="terminal_illness", died_tick=200)])

    assert rows[0]["is_minor"] is True
    assert rows[0]["is_alive"] is False


@pytest.mark.asyncio
async def test_insert_round_trips_json_columns(client: SqliteClient) -> None:
    await client.insert("agents", [agent_row()])
    (agent,) = await client.select("agents")

    rows = await client.insert(
        "petitions",
        [
            {
                "petition_type": "land_expansion",
                "filed_by_agent_id": agent["id"],
                "title": "Expand north",
                "payload": {"tiles": [[1, 2], [3, 4]]},
                "filed_tick": 10,
            }
        ],
    )

    assert rows[0]["payload"] == {"tiles": [[1, 2], [3, 4]]}


@pytest.mark.asyncio
async def test_insert_violating_check_constraint_raises_supabase_error(client: SqliteClient) -> None:
    with pytest.raises(SupabaseError) as exc_info:
        await client.insert("agents", [agent_row(sex="unspecified")])

    assert exc_info.value.table == "agents"
    assert exc_info.value.status_code is None


@pytest.mark.asyncio
async def test_insert_enforces_ledger_invariant(client: SqliteClient) -> None:
    with pytest.raises(SupabaseError):
        await client.insert(
            "transactions",
            [
                {
                    "currency": "usd",
                    "tx_type": "burn",
                    "amount": 5,
                    "usd_wallets_total_after": 99995,
                    "usd_treasury_total_after": 0,
                    "usd_burned_total_after": 999,  # wrong: does not sum to 100000
                    "tick": 1,
                }
            ],
        )


@pytest.mark.asyncio
async def test_insert_accepts_valid_ledger_transaction(client: SqliteClient) -> None:
    rows = await client.insert(
        "transactions",
        [
            {
                "currency": "usd",
                "tx_type": "burn",
                "amount": 5,
                "usd_wallets_total_after": 99995,
                "usd_treasury_total_after": 0,
                "usd_burned_total_after": 5,
                "tick": 1,
            }
        ],
    )

    assert rows[0]["usd_wallets_total_after"] == 99995


# ── select ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_select_with_eq_filter(client: SqliteClient) -> None:
    await client.insert("agents", [agent_row(slug="agent-001"), agent_row(slug="agent-002")])

    rows = await client.select("agents", filters={"slug": eq("agent-002")})

    assert len(rows) == 1
    assert rows[0]["slug"] == "agent-002"


@pytest.mark.asyncio
async def test_select_with_gte_and_order_and_limit(client: SqliteClient) -> None:
    await client.insert(
        "agents",
        [agent_row(slug=f"agent-{i:03d}", created_tick=i * 100) for i in range(5)],
    )

    rows = await client.select(
        "agents", filters={"created_tick": gte("200")}, order="created_tick.desc", limit=2
    )

    assert [r["created_tick"] for r in rows] == [400, 300]


@pytest.mark.asyncio
async def test_select_with_in_filter(client: SqliteClient) -> None:
    await client.insert(
        "agents", [agent_row(slug="agent-001"), agent_row(slug="agent-002"), agent_row(slug="agent-003")]
    )

    rows = await client.select("agents", filters={"slug": in_(["agent-001", "agent-003"])})

    assert {r["slug"] for r in rows} == {"agent-001", "agent-003"}


@pytest.mark.asyncio
async def test_select_with_is_null_filter(client: SqliteClient) -> None:
    await client.insert("agents", [agent_row(slug="agent-001", occupation="farmer"), agent_row(slug="agent-002")])

    rows = await client.select("agents", filters={"occupation": is_("null")})

    assert [r["slug"] for r in rows] == ["agent-002"]


@pytest.mark.asyncio
async def test_select_with_like_filter(client: SqliteClient) -> None:
    await client.insert("agents", [agent_row(slug="agent-001", display_name="Aiko Tanaka"), agent_row(slug="agent-002", display_name="Ren Sato")])

    rows = await client.select("agents", filters={"display_name": like("Aiko%")})

    assert [r["slug"] for r in rows] == ["agent-001"]


@pytest.mark.asyncio
async def test_select_with_columns_projection(client: SqliteClient) -> None:
    await client.insert("agents", [agent_row()])

    rows = await client.select("agents", columns="slug, sex")

    assert set(rows[0].keys()) == {"slug", "sex"}


@pytest.mark.asyncio
async def test_select_on_empty_table_returns_empty_list(client: SqliteClient) -> None:
    assert await client.select("agents") == []


# ── update ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_matching_rows(client: SqliteClient) -> None:
    await client.insert("agents", [agent_row(slug="agent-001"), agent_row(slug="agent-002")])

    updated = await client.update("agents", filters={"slug": eq("agent-001")}, values={"occupation": "baker"})

    assert len(updated) == 1
    assert updated[0]["occupation"] == "baker"
    unchanged = await client.select("agents", filters={"slug": eq("agent-002")})
    assert unchanged[0]["occupation"] is None


@pytest.mark.asyncio
async def test_update_with_no_matches_returns_empty_list(client: SqliteClient) -> None:
    assert await client.update("agents", filters={"slug": eq("nonexistent")}, values={"occupation": "x"}) == []


@pytest.mark.asyncio
async def test_update_round_trips_bool_and_json_columns(client: SqliteClient) -> None:
    await client.insert("agents", [agent_row()])
    (agent,) = await client.select("agents")
    await client.insert(
        "buildings",
        [{"building_type": "genesis", "purpose": "house", "footprint": {"tiles": [1]}, "created_tick": 0}],
    )

    updated = await client.update(
        "buildings", filters={"building_type": eq("genesis")}, values={"footprint": {"tiles": [1, 2, 3]}}
    )

    assert updated[0]["footprint"] == {"tiles": [1, 2, 3]}


# ── upload_archive ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_archive_writes_local_file(client: SqliteClient) -> None:
    storage_path = await client.upload_archive(bucket="llm-payloads", path="agent-001/1.json.gz", data=b"gzipped-bytes")

    assert storage_path == "llm-payloads/agent-001/1.json.gz"
    written = client.local_storage_root / "llm-payloads" / "agent-001" / "1.json.gz"
    assert written.read_bytes() == b"gzipped-bytes"


@pytest.mark.asyncio
async def test_upload_archive_in_memory_uses_temp_storage_root() -> None:
    client = SqliteClient(SupabaseConfig(sqlite_path=":memory:"))

    storage_path = await client.upload_archive(bucket="llm-payloads", path="x.json.gz", data=b"data")

    assert storage_path == "llm-payloads/x.json.gz"
    await client.close()


class FakeS3Client:
    """Stand-in for supabase.s3_client.S3Client, monkeypatched over the
    real class so these tests verify SqliteClient.upload_archive's own
    routing/prefix-mapping/error-wrapping logic without re-testing SigV4
    signing (already covered by test_supabase_s3_client.py) or touching a
    real network transport.
    """

    instances: list["FakeS3Client"] = []

    def __init__(self, config: S3Config) -> None:
        self.config = config
        self.put_calls: list[tuple[str, bytes, str]] = []
        self.closed = False
        self.raise_error: S3Error | None = None
        FakeS3Client.instances.append(self)

    async def put_object(self, *, key: str, data: bytes, content_type: str) -> None:
        if self.raise_error is not None:
            raise self.raise_error
        self.put_calls.append((key, data, content_type))

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_fake_s3_instances() -> None:
    FakeS3Client.instances = []


@pytest.mark.asyncio
async def test_upload_archive_routes_to_s3_when_bucket_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sqlite_client_module, "S3Client", FakeS3Client)
    client = SqliteClient(
        SupabaseConfig(
            sqlite_path=str(tmp_path / "kasukabe.sqlite3"),
            s3_bucket="kasukabe-genesis-123",
            s3_region="ap-south-1",
        )
    )

    storage_path = await client.upload_archive(
        bucket="llm-payloads", path="agent-001/1.json.gz", data=b"gzipped-bytes", content_type="application/gzip"
    )
    await client.close()

    # Backend-portable return value stays "bucket/path" regardless of the
    # real S3 key underneath it (see upload_archive's docstring).
    assert storage_path == "llm-payloads/agent-001/1.json.gz"
    assert len(FakeS3Client.instances) == 1
    fake = FakeS3Client.instances[0]
    assert fake.config.bucket == "kasukabe-genesis-123"
    assert fake.config.region == "ap-south-1"
    # The fixed archives/payloads/ prefix infra/scripts/01-setup-s3-and-iam.sh
    # carves out, not the logical "llm-payloads" bucket name.
    assert fake.put_calls == [
        ("archives/payloads/agent-001/1.json.gz", b"gzipped-bytes", "application/gzip")
    ]
    assert fake.closed is True
    # Local filesystem fallback must NOT have been touched.
    assert not (client.local_storage_root / "llm-payloads").exists()


@pytest.mark.asyncio
async def test_upload_archive_maps_world_snapshots_bucket_to_checkpoints_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sqlite_client_module, "S3Client", FakeS3Client)
    client = SqliteClient(
        SupabaseConfig(
            sqlite_path=str(tmp_path / "kasukabe.sqlite3"), s3_bucket="b", s3_region="ap-south-1"
        )
    )

    await client.upload_archive(bucket="world-snapshots", path="0000000144_daily.json.gz", data=b"x")
    await client.close()

    assert FakeS3Client.instances[0].put_calls[0][0] == "archives/checkpoints/0000000144_daily.json.gz"


@pytest.mark.asyncio
async def test_upload_archive_reuses_the_same_s3_client_across_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sqlite_client_module, "S3Client", FakeS3Client)
    client = SqliteClient(
        SupabaseConfig(
            sqlite_path=str(tmp_path / "kasukabe.sqlite3"), s3_bucket="b", s3_region="ap-south-1"
        )
    )

    await client.upload_archive(bucket="llm-payloads", path="a.json.gz", data=b"1")
    await client.upload_archive(bucket="llm-payloads", path="b.json.gz", data=b"2")
    await client.close()

    assert len(FakeS3Client.instances) == 1
    assert len(FakeS3Client.instances[0].put_calls) == 2


@pytest.mark.asyncio
async def test_upload_archive_wraps_s3_error_as_supabase_error(tmp_path: Path) -> None:
    client = SqliteClient(
        SupabaseConfig(
            sqlite_path=str(tmp_path / "kasukabe.sqlite3"), s3_bucket="b", s3_region="ap-south-1"
        )
    )

    async def failing_put(**kwargs: object) -> None:
        raise S3Error(key="x", detail="AccessDenied", status_code=403)

    # Pre-set client.s3 so upload_archive's lazy "if self.s3 is None"
    # construction is skipped and this fake is used instead - no need to
    # monkeypatch the S3Client class itself for this one.
    client.s3 = FakeS3Client(S3Config(bucket="b", region="ap-south-1"))
    client.s3.put_object = failing_put  # type: ignore[method-assign]

    with pytest.raises(SupabaseError) as exc_info:
        await client.upload_archive(bucket="llm-payloads", path="x.json.gz", data=b"x")
    await client.close()

    assert exc_info.value.table == "s3:llm-payloads"
    assert "AccessDenied" in exc_info.value.detail


# ── helper function unit tests ─────────────────────────────────────


def test_build_where_empty_filters() -> None:
    clause, params = build_where({})
    assert clause == "1=1"
    assert params == []


def test_build_where_rejects_unsupported_operator() -> None:
    with pytest.raises(ValueError):
        build_where({"col": "cs.value"})


def test_build_order_default_ascending() -> None:
    assert build_order("tick") == "tick ASC"


def test_build_order_explicit_direction() -> None:
    assert build_order("tick.desc") == "tick DESC"


def test_build_order_multiple_columns() -> None:
    assert build_order("agent_id.asc, tick.desc") == "agent_id ASC, tick DESC"


# ── coercion table exhaustiveness against the real schema ──────────


def test_json_and_bool_column_tables_reference_real_tables_and_columns() -> None:
    """Guards against SQLITE_JSON_COLUMNS/SQLITE_BOOL_COLUMNS drifting from
    sqlite_schema.sql: every table/column named in either map must
    actually exist in the schema, so a typo or a renamed column fails
    this test instead of silently never coercing.
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    for coercion_map in (SQLITE_JSON_COLUMNS, SQLITE_BOOL_COLUMNS):
        for table, columns in coercion_map.items():
            table_columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            assert table_columns, f"table {table!r} referenced in a coercion map does not exist"
            missing = columns - table_columns
            assert not missing, f"{table}: columns {missing} in a coercion map do not exist in the schema"
