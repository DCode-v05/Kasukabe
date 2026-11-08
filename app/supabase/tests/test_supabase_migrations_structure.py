"""Structural tests over the migration files themselves: no database, no
network, always runs in CI. Complements
test_migrations_apply_live.py, which does the real thing (applies every
migration to an actual Postgres) but skips when no local Postgres is
reachable - this file is what still catches a regression (a dropped
index, a forgotten RLS grant, a renamed table) on a machine without
Postgres installed.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

EXPECTED_TABLES = {
    "agents", "llm_calls", "events", "utterances", "memories", "transactions",
    "businesses", "organizations", "petitions", "cases", "buildings", "skills",
    "tools_registry", "relationships", "world_snapshots", "eval_metrics",
}

FILENAME_PATTERN = re.compile(r"^\d{14}_[a-z][a-z0-9_]*\.sql$")


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


SCHEMA_BUILDING_MIGRATIONS = 19


def creation_migrations() -> list:
    return [
        path for path in migration_files()
        if "create table if not exists public." in path.read_text(encoding="utf-8")
        or path.name.endswith(("_extensions.sql", "_realtime_publication.sql", "_storage_buckets.sql"))
    ]


def alteration_migrations() -> list:
    creations = {path.name for path in creation_migrations()}
    return [path for path in migration_files() if path.name not in creations]


def test_migrations_directory_has_extensions_16_tables_realtime_and_storage() -> None:
    files = creation_migrations()
    assert len(files) == SCHEMA_BUILDING_MIGRATIONS, [f.name for f in files]


def test_every_alteration_runs_after_the_schema_it_alters() -> None:
    """An `alter table` that sorts before the `create table` it depends on
    is a migration set that cannot be applied from empty."""
    creations = creation_migrations()
    last_creation = max(path.name for path in creations)
    for path in alteration_migrations():
        assert path.name > last_creation, (
            f"{path.name} alters the schema but sorts before {last_creation}, "
            "so a fresh database would apply it to tables that do not exist yet"
        )


def test_every_migration_filename_matches_timestamp_prefix_convention() -> None:
    for path in migration_files():
        assert FILENAME_PATTERN.match(path.name), (
            f"{path.name} does not match '<14-digit timestamp>_<name>.sql', "
            f"the Supabase CLI migration naming convention"
        )


def test_migrations_apply_in_a_valid_dependency_order() -> None:
    """extensions first, then agents (everything else references it,
    directly or transitively), then realtime/storage last (they reference
    tables that must already exist).
    """
    names = [f.name for f in creation_migrations()]
    assert names[0] == "20260730120000_extensions.sql"
    assert names[1] == "20260730120100_agents.sql"
    assert names[-2] == "20260730121700_realtime_publication.sql"
    assert names[-1] == "20260730121800_storage_buckets.sql"


def test_every_expected_table_has_exactly_one_migration_file() -> None:
    found_tables: set[str] = set()
    for path in migration_files():
        text = path.read_text(encoding="utf-8")
        matches = re.findall(r"create table if not exists public\.(\w+)", text)
        found_tables.update(matches)

    assert found_tables == EXPECTED_TABLES


def test_every_table_migration_enables_rls_grants_and_a_select_policy() -> None:
    for path in migration_files():
        text = path.read_text(encoding="utf-8")
        table_match = re.search(r"create table if not exists public\.(\w+)", text)
        if table_match is None:
            continue
        table = table_match.group(1)

        assert f"alter table public.{table} enable row level security;" in text, table
        assert f"grant select on public.{table} to anon, authenticated;" in text, table
        assert f"create policy {table}_select_all on public.{table}" in text, table


def test_every_table_migration_derives_game_day_and_minute_from_a_tick_column() -> None:
    """DESIGN.md section 1 (144 ticks/game-day, 10 game-minutes/tick) and
    the module brief ("every table keyed by tick + game_time where
    sensible") are enforced identically everywhere: a STORED generated
    column, never an app-computed value that could drift.
    """
    for path in migration_files():
        text = path.read_text(encoding="utf-8")
        if "create table if not exists public." not in text:
            continue
        assert "generated always as" in text, path.name
        assert "/ 144) stored" in text, path.name
        assert "% 144) * 10) stored" in text, path.name


def test_events_type_check_matches_every_example_in_conventions_md() -> None:
    """Mirrors migrations/20260730120900_events.sql's regex in Python and
    checks it against every example event type in app/CONVENTIONS.md's
    naming-scheme table (both the 3-segment majority and the 2-segment
    agent.*/business.registered shorthand), so a future edit to that
    regex is checked against the actual spec, not just re-typed by hand.
    """
    pattern = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,2}$")

    examples_from_conventions_md = [
        "world.tick.advanced", "world.land.expanded",
        "agent.moved", "agent.spawned", "agent.died",
        "economy.wallet.debited", "economy.wallet.credited", "economy.ledger.violated",
        "business.registered", "business.contract.won",
        "crime.theft.attempted", "crime.warrant.issued",
        "politics.vote.cast", "politics.charter.amended", "politics.regime.changed",
        "religion.prayer.offered", "religion.doctrine.emerged",
        "health.ailment.onset", "health.agent.died",
        "broker.call.completed", "broker.call.rejected", "broker.rpm.throttled",
        "research.probe.completed", "research.metric.computed",
        "system.checkpoint.written", "system.sim.halted",
    ]
    for event_type in examples_from_conventions_md:
        assert pattern.match(event_type), event_type

    for bad in ["BadType", "no_dot_at_all", ".leadingdot", "trailing.", "UPPER.case.bad", "a.b.c.d"]:
        assert not pattern.match(bad), bad


def test_transactions_and_world_snapshots_hardcode_the_1000_dollar_invariant() -> None:
    transactions_sql = (MIGRATIONS_DIR / "20260730121200_transactions.sql").read_text(encoding="utf-8")
    snapshots_sql = (MIGRATIONS_DIR / "20260730121500_world_snapshots.sql").read_text(encoding="utf-8")

    assert "= 100000" in transactions_sql
    assert "= 100000" in snapshots_sql


def test_realtime_publication_covers_events_and_utterances_only() -> None:
    text = (MIGRATIONS_DIR / "20260730121700_realtime_publication.sql").read_text(encoding="utf-8")

    assert "alter publication supabase_realtime add table public.events;" in text
    assert "alter publication supabase_realtime add table public.utterances;" in text
    added_tables = re.findall(r"add table public\.(\w+);", text)
    assert set(added_tables) == {"events", "utterances"}


def test_storage_buckets_migration_creates_llm_payloads_and_world_snapshots() -> None:
    text = (MIGRATIONS_DIR / "20260730121800_storage_buckets.sql").read_text(encoding="utf-8")

    assert "values ('llm-payloads', 'llm-payloads', false)" in text
    assert "values ('world-snapshots', 'world-snapshots', false)" in text


def test_no_migration_file_contains_an_em_dash() -> None:
    for path in migration_files():
        text = path.read_text(encoding="utf-8")
        assert "—" not in text, path.name


def test_agents_employer_business_id_fk_is_added_after_businesses_exists() -> None:
    """Regression guard for the circular-FK ordering: agents.sql must NOT
    declare the employer_business_id foreign key inline (businesses does
    not exist yet at that point in the migration order); businesses.sql
    must add it via ALTER TABLE after creating businesses.
    """
    agents_sql = (MIGRATIONS_DIR / "20260730120100_agents.sql").read_text(encoding="utf-8")
    businesses_sql = (MIGRATIONS_DIR / "20260730120200_businesses.sql").read_text(encoding="utf-8")

    assert "employer_business_id uuid," in agents_sql
    assert "references public.businesses" not in agents_sql
    assert "alter table public.agents" in businesses_sql
    assert "add constraint agents_employer_business_id_fkey" in businesses_sql
