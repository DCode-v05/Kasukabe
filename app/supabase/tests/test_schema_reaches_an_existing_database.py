"""A column added to the schema has to reach a database that already exists.

`ensure_schema` ran the DDL **only when the database had no tables in
it**. Every statement in sqlite_schema.sql is `if not exists` - all
seventy-nine of them - so the guard protected nothing, and what it
actually did was make the schema unreachable the moment a database had
one table in it.

Measured on a six-tick boot against an existing data/ directory:

    world recorder dropped a economy.wallet.credited: table transactions
    has no column named counterparty_id (213 dropped this run)

Two hundred and thirteen transaction rows, in six ticks, at WARNING
level, while the run reported success. `counterparty_id` had been in the
schema since the counterparty work; the file on disk predated it, and
`create table if not exists` does not add a column to a table that is
already there. Every buy, sell and transfer went to the trace store and
was thrown away - which means the economy section of any report built
from that store was reading an empty table and saying so.
"""

from __future__ import annotations

import sqlite3

import pytest

from supabase.config import SupabaseConfig
from supabase.sqlite_client import SqliteClient, schema_columns


def _client(tmp_path) -> SqliteClient:
    return SqliteClient(SupabaseConfig(sqlite_path=str(tmp_path / "kasukabe.db")))


def test_the_schema_is_parsed_from_the_ddl_not_hand_listed():
    """A migration list maintained alongside a schema is a migration list
    that drifts from it, which is the failure this exists to end."""
    columns = schema_columns()

    assert "transactions" in columns
    assert columns["transactions"]["counterparty_id"] == "text"


def test_only_additive_safe_columns_are_offered_for_migration():
    """A NOT NULL column cannot be ALTERed in without a default, and a
    generated column cannot be ALTERed in at all. Both are real
    migrations, not something to do silently at boot."""
    columns = schema_columns()

    # `amount integer not null check (amount > 0)` is in the DDL and must
    # not be in the additive set.
    assert "amount" not in columns["transactions"]
    assert "id" not in columns["transactions"]


def test_a_column_added_to_the_schema_reaches_an_older_database(tmp_path):
    """The defect, in one test.

    Build a database the way one existed before the counterparty work -
    the same table, without that column - and boot against it.
    """
    path = tmp_path / "kasukabe.db"
    old = sqlite3.connect(path)
    old.execute(
        "create table transactions ("
        "  id integer primary key autoincrement,"
        "  currency text not null,"
        "  tx_type text not null,"
        "  from_agent_id text,"
        "  to_agent_id text,"
        "  amount integer not null,"
        "  tick integer"
        ")"
    )
    old.execute(
        "insert into transactions (currency, tx_type, amount) values ('sim', 'trade', 40)"
    )
    old.commit()
    old.close()

    client = SqliteClient(SupabaseConfig(sqlite_path=str(path)))
    client.ensure_schema()

    live = {row[1] for row in client.conn.execute("PRAGMA table_info(transactions)")}
    assert "counterparty_id" in live, (
        "a column added to the schema still cannot reach an existing database"
    )
    # And the row that was already there is untouched. Additive means
    # additive: a destructive repair is repair_live_schema.py's job, and
    # it refuses to touch a table with rows in it.
    assert client.conn.execute("select count(*) from transactions").fetchone()[0] == 1


def test_a_table_added_to_the_schema_also_reaches_an_older_database(tmp_path):
    """The same guard hid whole new TABLES, not only columns."""
    path = tmp_path / "kasukabe.db"
    old = sqlite3.connect(path)
    # Its NOT NULL columns, because those were in the original schema and
    # a real old database has them. Asserting against a shape that never
    # existed would be testing a fiction.
    old.execute(
        "create table transactions ("
        "  id integer primary key autoincrement,"
        "  currency text not null,"
        "  tx_type text not null,"
        "  amount integer not null,"
        "  tick integer"
        ")"
    )
    old.commit()
    old.close()

    client = SqliteClient(SupabaseConfig(sqlite_path=str(path)))
    client.ensure_schema()

    tables = {
        row[0] for row in client.conn.execute(
            "select name from sqlite_master where type = 'table'"
        )
    }
    assert "llm_calls" in tables
    assert "events" in tables


def test_a_fresh_database_still_gets_the_whole_schema(tmp_path):
    client = _client(tmp_path)
    client.ensure_schema()

    tables = {
        row[0] for row in client.conn.execute(
            "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
        )
    }
    assert {"transactions", "llm_calls", "events", "agents"} <= tables


def test_running_it_twice_changes_nothing(tmp_path):
    """It runs on every boot now, so it has to be a no-op on the second."""
    client = _client(tmp_path)
    client.ensure_schema()

    added = client.add_missing_columns()

    assert added == []


def test_a_column_the_database_has_and_the_schema_does_not_is_left_alone(tmp_path):
    """Either older data worth keeping or somebody else's migration in
    flight, and neither is this method's to decide about."""
    path = tmp_path / "kasukabe.db"
    old = sqlite3.connect(path)
    old.execute(
        "create table transactions ("
        "  id integer primary key autoincrement,"
        "  currency text not null,"
        "  tx_type text not null,"
        "  amount integer not null,"
        "  tick integer,"
        "  ancient_note text"
        ")"
    )
    old.commit()
    old.close()

    client = SqliteClient(SupabaseConfig(sqlite_path=str(path)))
    client.ensure_schema()

    live = {row[1] for row in client.conn.execute("PRAGMA table_info(transactions)")}
    assert "ancient_note" in live


@pytest.mark.asyncio
async def test_a_transaction_with_a_counterparty_actually_lands(tmp_path):
    """The end of the story: the row that was being dropped now inserts.

    Asserted against the write path rather than the schema, because the
    schema was never the thing that was wrong - it had the column all
    along.
    """
    path = tmp_path / "kasukabe.db"
    old = sqlite3.connect(path)
    old.execute(
        "create table transactions ("
        "  id integer primary key autoincrement,"
        "  currency text not null,"
        "  tx_type text not null,"
        "  amount integer not null,"
        "  tick integer"
        ")"
    )
    old.commit()
    old.close()

    client = SqliteClient(SupabaseConfig(sqlite_path=str(path)))
    client.ensure_schema()
    client.conn.execute(
        "insert into transactions (currency, tx_type, amount, counterparty_id) "
        "values ('sim', 'purchase', 60, 'b_bakery')"
    )
    client.conn.commit()

    row = client.conn.execute(
        "select counterparty_id from transactions where amount = 60"
    ).fetchone()
    assert row[0] == "b_bakery"
