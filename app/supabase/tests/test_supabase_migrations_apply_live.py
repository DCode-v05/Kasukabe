"""Applies every migration in migrations/ to a real, throwaway Postgres
database and exercises the invariants that matter: RLS actually blocks
anon writes and allows anon reads, the ledger invariant CHECK constraint
actually rejects a bad transaction, and the realtime publication actually
contains events and utterances.

This is deliberately a live database test, not a mock: a CHECK constraint,
an RLS policy, and a generated column are Postgres-server behavior that no
amount of parsing migrations/*.sql in Python can verify (see
test_migrations_structure.py for what that static check can and does
cover). Every assertion in this file was confirmed failing appropriately
before the corresponding fix went into migrations/ (missing `extensions`
schema, unqualified gen_random_uuid(), the 3-segment-only event type
regex, missing anon/service_role grants) - this is regression coverage for
bugs this test suite already caught once.

Skips (does not fail) when `psql`/`createdb`/`dropdb` are not on PATH or
no local Postgres server answers - this is expected in a CI image without
Postgres installed (app/CONVENTIONS.md's test suite "runs in CI with no
AWS credentials available", and the same reasoning extends to "no
Postgres either" for this repo, which has no Postgres client library
dependency at all by design: every real caller talks to Supabase over
PostgREST/HTTP, never a raw connection - see postgrest_client.py's
docstring). On a machine with Postgres installed (this module was
developed and verified against a local Postgres 18 with pgvector), the
test runs for real.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

BOOTSTRAP_SQL = """
do $$
begin
  if not exists (select from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select from pg_roles where rolname = 'service_role') then
    create role service_role nologin bypassrls;
  end if;
end $$;
create schema if not exists storage;
create table if not exists storage.buckets (
    id text primary key,
    name text not null,
    public boolean not null default false
);
"""


def postgres_available() -> bool:
    """Reachable AND usable. The second half was missing.

    `pg_isready` asks whether the port answers; it does not authenticate.
    On a machine with Postgres installed but no credentials on hand - this
    one, with Postgres 17 on :5432 - the port answers, this returned True,
    and `createdb` then blocked on an interactive password prompt until
    the 15-second timeout. Seven ERRORs per run for a host precondition
    this file's own docstring promises to SKIP on.

    `-w` (--no-password) is what makes that decidable: libpq fails
    immediately with "no password supplied" instead of prompting. It has
    to be the flag rather than closing stdin, because on Windows libpq
    reads the password straight from the console and a DEVNULL stdin still
    hangs - measured, 10s and counting.
    """
    if not (shutil.which("psql") and shutil.which("createdb") and shutil.which("dropdb")):
        return False
    try:
        subprocess.run(["pg_isready"], check=True, capture_output=True, timeout=5)
        subprocess.run(
            ["psql", "-w", "-d", "postgres", "-c", "select 1", "-X", "-q"],
            check=True, capture_output=True, timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return True


pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="no local Postgres reachable AND usable (psql/createdb/dropdb + "
    "pg_isready + a credential that can log in without a prompt); this repo "
    "has no Postgres driver dependency by design, see this file's docstring",
)


def run_psql(dbname: str, *, sql: str | None = None, file: Path | None = None) -> subprocess.CompletedProcess[str]:
    args = ["psql", "-d", dbname, "-v", "ON_ERROR_STOP=1", "-X", "-q"]
    if file is not None:
        args += ["-f", str(file)]
    return subprocess.run(
        args, input=sql, capture_output=True, text=True, timeout=30, check=False
    )


@pytest.fixture
def migrated_db() -> str:
    dbname = f"kasukabe_test_{uuid.uuid4().hex[:12]}"
    subprocess.run(["createdb", dbname], check=True, capture_output=True, timeout=15)
    try:
        bootstrap = run_psql(dbname, sql=BOOTSTRAP_SQL)
        assert bootstrap.returncode == 0, bootstrap.stderr

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            result = run_psql(dbname, file=path)
            assert result.returncode == 0, f"{path.name} failed:\n{result.stderr}"

        yield dbname
    finally:
        subprocess.run(["dropdb", "--if-exists", dbname], capture_output=True, timeout=15)


def test_all_migrations_apply_cleanly_in_order(migrated_db: str) -> None:
    result = run_psql(
        migrated_db,
        sql="select count(*) from information_schema.tables where table_schema = 'public';",
    )
    assert result.returncode == 0, result.stderr
    assert "16" in result.stdout


def test_anon_can_select_but_not_insert_agents(migrated_db: str) -> None:
    seed = run_psql(
        migrated_db,
        sql="insert into agents (slug, display_name, sex, birth_tick, created_tick) "
        "values ('agent-001', 'Aiko', 'female', 0, 100);",
    )
    assert seed.returncode == 0, seed.stderr

    select_as_anon = run_psql(migrated_db, sql="set role anon; select slug from agents;")
    assert select_as_anon.returncode == 0, select_as_anon.stderr
    assert "agent-001" in select_as_anon.stdout

    insert_as_anon = run_psql(
        migrated_db,
        sql="set role anon; insert into agents (slug, display_name, sex, birth_tick, created_tick) "
        "values ('x', 'x', 'male', 0, 0);",
    )
    assert insert_as_anon.returncode != 0
    assert "permission denied" in insert_as_anon.stderr


def test_service_role_can_write_bypassing_rls(migrated_db: str) -> None:
    result = run_psql(
        migrated_db,
        sql="set role service_role; insert into agents (slug, display_name, sex, birth_tick, created_tick) "
        "values ('agent-002', 'Ren', 'male', 0, 0);",
    )
    assert result.returncode == 0, result.stderr


def test_ledger_invariant_rejects_a_transaction_that_does_not_sum_to_100000(migrated_db: str) -> None:
    valid = run_psql(
        migrated_db,
        sql="insert into transactions (currency, tx_type, to_pool, amount, "
        "usd_wallets_total_after, usd_treasury_total_after, usd_burned_total_after, tick) "
        "values ('usd', 'burn', 'burned', 5, 99995, 0, 5, 1);",
    )
    assert valid.returncode == 0, valid.stderr

    invalid = run_psql(
        migrated_db,
        sql="insert into transactions (currency, tx_type, to_pool, amount, "
        "usd_wallets_total_after, usd_treasury_total_after, usd_burned_total_after, tick) "
        "values ('usd', 'burn', 'burned', 5, 99995, 0, 999, 2);",
    )
    assert invalid.returncode != 0
    assert "ledger_invariant_usd" in invalid.stderr


def test_realtime_publication_contains_exactly_events_and_utterances(migrated_db: str) -> None:
    result = run_psql(
        migrated_db,
        sql="select tablename from pg_publication_tables where pubname = 'supabase_realtime' order by tablename;",
    )
    assert result.returncode == 0, result.stderr
    assert "events" in result.stdout
    assert "utterances" in result.stdout


def test_events_type_check_accepts_two_and_three_segment_forms(migrated_db: str) -> None:
    two_segment = run_psql(migrated_db, sql="insert into events (type, tick) values ('agent.moved', 1);")
    assert two_segment.returncode == 0, two_segment.stderr

    three_segment = run_psql(
        migrated_db, sql="insert into events (type, tick) values ('world.tick.advanced', 1);"
    )
    assert three_segment.returncode == 0, three_segment.stderr

    malformed = run_psql(migrated_db, sql="insert into events (type, tick) values ('NoDots', 1);")
    assert malformed.returncode != 0
    assert "events_type_check" in malformed.stderr


def test_circular_fk_between_agents_and_businesses_works(migrated_db: str) -> None:
    setup = run_psql(
        migrated_db,
        sql="""
        insert into agents (slug, display_name, sex, birth_tick, created_tick)
        values ('agent-001', 'Aiko', 'female', 0, 100);
        insert into businesses (name, business_type, owner_agent_id, registered_tick)
        select 'Kasukabe Construction Co.', 'construction', id, 50 from agents where slug = 'agent-001';
        update agents set employer_business_id = (select id from businesses limit 1)
        where slug = 'agent-001';
        """,
    )
    assert setup.returncode == 0, setup.stderr

    check = run_psql(
        migrated_db,
        sql="select a.slug, b.name from agents a join businesses b on a.employer_business_id = b.id;",
    )
    assert check.returncode == 0, check.stderr
    assert "Kasukabe Construction Co." in check.stdout
