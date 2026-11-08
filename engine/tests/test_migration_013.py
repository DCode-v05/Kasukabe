"""Tests for migration 013 — observability and gateway tables."""

import pytest
import pytest_asyncio
from pathlib import Path

from src.infra.sqlite_pool import SqlitePool

MIGRATION_FILE = (
    Path(__file__).parent.parent / "migrations" / "013_observability_and_gateway_tables.sql"
)

EXPECTED_TABLES = (
    "obs_events",
    "obs_counters_daily",
    "gw_cache_state",
    "gw_config_snapshot",
)


@pytest_asyncio.fixture
async def fresh_db():
    pool = SqlitePool(":memory:")
    await pool.connect()
    yield pool
    await pool.close()


async def apply(pool: SqlitePool) -> None:
    sql = MIGRATION_FILE.read_text(encoding="utf-8")
    await pool.executescript(sql)
    await pool.commit()


async def table_exists(pool: SqlitePool, name: str) -> bool:
    rows = await pool.fetch("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return len(rows) == 1


async def columns(pool: SqlitePool, table: str) -> dict[str, dict]:
    rows = await pool.fetch(f"PRAGMA table_info({table})")
    return {row["name"]: dict(row) for row in rows}


async def index_names(pool: SqlitePool, table: str) -> set[str]:
    rows = await pool.fetch(f"PRAGMA index_list({table})")
    return {row["name"] for row in rows}


class TestMigration013:
    @pytest.mark.asyncio
    async def test_migration_file_exists(self):
        assert MIGRATION_FILE.exists()

    @pytest.mark.asyncio
    async def test_all_tables_created(self, fresh_db):
        await apply(fresh_db)
        for table in EXPECTED_TABLES:
            assert await table_exists(fresh_db, table), f"missing: {table}"

    @pytest.mark.asyncio
    async def test_obs_events_columns(self, fresh_db):
        await apply(fresh_db)
        cols = await columns(fresh_db, "obs_events")
        assert set(cols.keys()) == {
            "id",
            "timestamp",
            "kind",
            "payload",
            "thread_id",
            "agent_step_id",
        }
        assert cols["timestamp"]["notnull"] == 1
        assert cols["kind"]["notnull"] == 1
        assert cols["payload"]["notnull"] == 1

    @pytest.mark.asyncio
    async def test_obs_events_indices(self, fresh_db):
        await apply(fresh_db)
        idx = await index_names(fresh_db, "obs_events")
        assert "idx_obs_events_timestamp" in idx
        assert "idx_obs_events_kind" in idx
        assert "idx_obs_events_thread" in idx

    @pytest.mark.asyncio
    async def test_obs_counters_daily_pk(self, fresh_db):
        await apply(fresh_db)
        cols = await columns(fresh_db, "obs_counters_daily")
        assert set(cols.keys()) == {"day", "metric", "model", "value"}
        assert cols["day"]["pk"] > 0
        assert cols["metric"]["pk"] > 0
        assert cols["model"]["pk"] > 0

    @pytest.mark.asyncio
    async def test_gw_cache_state_columns(self, fresh_db):
        await apply(fresh_db)
        cols = await columns(fresh_db, "gw_cache_state")
        assert set(cols.keys()) == {"day", "model", "cache_hits", "cache_reads", "fresh_input"}
        assert cols["day"]["pk"] > 0
        assert cols["model"]["pk"] > 0

    @pytest.mark.asyncio
    async def test_gw_config_snapshot_columns(self, fresh_db):
        await apply(fresh_db)
        cols = await columns(fresh_db, "gw_config_snapshot")
        assert set(cols.keys()) == {"id", "applied_at", "yaml_blob", "applied_by"}
        assert cols["applied_at"]["notnull"] == 1
        assert cols["yaml_blob"]["notnull"] == 1

    @pytest.mark.asyncio
    async def test_idempotent(self, fresh_db):
        await apply(fresh_db)
        await apply(fresh_db)
        for table in EXPECTED_TABLES:
            assert await table_exists(fresh_db, table)

    @pytest.mark.asyncio
    async def test_insert_obs_events(self, fresh_db):
        await apply(fresh_db)
        await fresh_db.execute(
            "INSERT INTO obs_events (timestamp, kind, payload) VALUES (?, ?, ?)",
            (1712840000.0, "token_usage", '{"model":"test"}'),
        )
        await fresh_db.commit()
        rows = await fresh_db.fetch("SELECT kind FROM obs_events")
        assert len(rows) == 1
        assert rows[0]["kind"] == "token_usage"

    @pytest.mark.asyncio
    async def test_insert_gw_config_snapshot(self, fresh_db):
        await apply(fresh_db)
        await fresh_db.execute(
            "INSERT INTO gw_config_snapshot (applied_at, yaml_blob, applied_by) VALUES (?, ?, ?)",
            (1712840000.0, "version: 1\nupstreams: []", "product_layer"),
        )
        await fresh_db.commit()
        rows = await fresh_db.fetch("SELECT applied_by FROM gw_config_snapshot")
        assert rows[0]["applied_by"] == "product_layer"

    @pytest.mark.asyncio
    async def test_shared_db_fixture_includes_migration(self, db):
        """Verify conftest.py shared db fixture picks up migration 013."""
        for table in EXPECTED_TABLES:
            assert await table_exists(db, table), f"shared db missing: {table}"
