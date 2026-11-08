"""Proves schema_gen.py's generated DDL and sqlite_schema.sql's checked-in
DDL for llm_calls/events genuinely agree, against real SQLite (not by
comparing source text) - this is the loud-failure-on-drift test
schema_gen.py's own module docstring promises, and it is the direct fix
for the exact incident that motivated this whole track: a genesis run
where broker/trace.py's TraceRecord (21 fields) and sqlite_schema.sql's
hand-maintained llm_calls table (13 unrelated columns) silently diverged
and every trace write failed.

`PRAGMA table_xinfo` (not `table_info`) is what these tests read: this
SQLite build (confirmed directly - see the "hidden" assertions below)
omits STORED generated columns from `table_info` entirely, but `select *`
and application code both still see them (proven directly here too), and
`table_xinfo` reports them with a nonzero `hidden` flag. Using `table_info`
here would make this test blind to exactly the columns
(game_day/game_minute_of_day) most likely to silently disagree.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from broker.trace import BrokerEvent, TraceRecord, broker_event_to_row, trace_record_to_row
from supabase import schema_gen
from supabase.sqlite_client import SQLITE_BOOL_COLUMNS, SQLITE_JSON_COLUMNS

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sqlite_schema.sql"


def table_columns(ddl_sql: str, table: str) -> set[tuple[str, str, int]]:
    """(name, declared_type, notnull) for every column `table` actually
    has once `ddl_sql` is applied to a real, throwaway SQLite connection -
    including generated columns (table_xinfo, not table_info; see module
    docstring), excluding sqlite3's internal rowid bookkeeping.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(ddl_sql)
        rows = conn.execute(f"PRAGMA table_xinfo({table})").fetchall()
        return {(r[1], r[2].lower(), r[3]) for r in rows}
    finally:
        conn.close()


def checked_in_schema() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def test_llm_calls_generated_ddl_matches_checked_in_schema() -> None:
    generated = table_columns(schema_gen.llm_calls_ddl(), "llm_calls")
    checked_in = table_columns(checked_in_schema(), "llm_calls")

    assert generated == checked_in, (
        f"llm_calls has drifted from TraceRecord - only in generated: "
        f"{generated - checked_in}; only in checked-in schema: {checked_in - generated}. "
        f"Regenerate with `python -m supabase.schema_gen` and paste the llm_calls block "
        f"into supabase/sqlite_schema.sql."
    )


def test_events_generated_ddl_matches_checked_in_schema() -> None:
    generated = table_columns(schema_gen.events_ddl(), "events")
    checked_in = table_columns(checked_in_schema(), "events")

    assert generated == checked_in, (
        f"events has drifted from BrokerEvent - only in generated: "
        f"{generated - checked_in}; only in checked-in schema: {checked_in - generated}. "
        f"Regenerate with `python -m supabase.schema_gen` and paste the events block "
        f"into supabase/sqlite_schema.sql."
    )


def test_every_tracerecord_field_has_a_column_in_generated_llm_calls_ddl() -> None:
    """The literal TASK spec assertion: "every dataclass field has a
    matching column of compatible type." Belt-and-suspenders alongside
    the two tests above - this one fails even if llm_calls_ddl() itself
    were buggy (skips a field), since it checks against TraceRecord's
    real field list directly rather than trusting the generator's output
    to be complete.
    """
    from dataclasses import fields as dc_fields

    columns = {name for name, _, _ in table_columns(schema_gen.llm_calls_ddl(), "llm_calls")}
    for f in dc_fields(TraceRecord):
        assert f.name in columns, f"TraceRecord.{f.name} has no llm_calls column"


def test_every_brokerevent_field_has_a_column_in_generated_events_ddl() -> None:
    from dataclasses import fields as dc_fields

    columns = {name for name, _, _ in table_columns(schema_gen.events_ddl(), "events")}
    for f in dc_fields(BrokerEvent):
        assert f.name in columns, f"BrokerEvent.{f.name} has no events column"


def test_table_info_hides_generated_columns_but_xinfo_and_select_star_do_not() -> None:
    """Documents (and pins) the SQLite quirk this test file's docstring
    relies on, so a future SQLite upgrade that changes this behavior
    fails a test instead of silently making the drift tests above blind
    to game_day/game_minute_of_day again.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("create table t (tick integer, game_day integer generated always as (tick / 144) stored)")
    conn.execute("insert into t (tick) values (300)")

    info_columns = [r[1] for r in conn.execute("PRAGMA table_info(t)").fetchall()]
    xinfo_columns = [r[1] for r in conn.execute("PRAGMA table_xinfo(t)").fetchall()]
    star_row = conn.execute("select * from t").fetchone()

    assert "game_day" not in info_columns
    assert "game_day" in xinfo_columns
    assert len(star_row) == 2
    conn.close()


def test_llm_calls_json_columns_match_schema_gen() -> None:
    assert SQLITE_JSON_COLUMNS["llm_calls"] == schema_gen.json_columns(TraceRecord)


def test_llm_calls_bool_columns_match_schema_gen() -> None:
    assert SQLITE_BOOL_COLUMNS["llm_calls"] == schema_gen.bool_columns(TraceRecord)


def test_events_has_no_bool_columns_and_payload_is_the_only_json_column() -> None:
    assert schema_gen.json_columns(BrokerEvent) == {"payload"}
    assert schema_gen.bool_columns(BrokerEvent) == set()
    assert SQLITE_JSON_COLUMNS["events"] == {"payload"}


def test_is_optional_unwraps_pep604_union() -> None:
    nullable, inner = schema_gen.is_optional(int | None)
    assert nullable is True
    assert inner is int


def test_is_optional_false_for_a_plain_type() -> None:
    nullable, inner = schema_gen.is_optional(str)
    assert nullable is False
    assert inner is str


def test_is_optional_rejects_multi_type_unions() -> None:
    with pytest.raises(schema_gen.SchemaGenError):
        schema_gen.is_optional(int | str | None)


def test_sqlite_column_type_mapping() -> None:
    assert schema_gen.sqlite_column_type(str) == "text"
    assert schema_gen.sqlite_column_type(int) == "integer"
    assert schema_gen.sqlite_column_type(float) == "real"
    assert schema_gen.sqlite_column_type(bool) == "integer"
    assert schema_gen.sqlite_column_type(dict[str, Any]) == "text"
    assert schema_gen.sqlite_column_type(str | None) == "text"


def test_sqlite_column_type_raises_on_unmapped_type() -> None:
    with pytest.raises(schema_gen.SchemaGenError):
        schema_gen.sqlite_column_type(list[int])


def test_column_ddl_not_null_for_required_field() -> None:
    assert schema_gen.column_ddl("purpose", str) == "purpose text not null"


def test_column_ddl_nullable_for_optional_field() -> None:
    assert schema_gen.column_ddl("region", str | None) == "region text"


def test_column_ddl_bool_gets_check_constraint() -> None:
    assert schema_gen.column_ddl("success", bool) == "success integer not null check (success in (0, 1))"


def test_column_ddl_primary_key_and_foreign_key() -> None:
    ddl = schema_gen.column_ddl("agent_id", str, foreign_key="agents (id)")
    assert ddl == "agent_id text not null references agents (id)"

    ddl_pk = schema_gen.column_ddl("id", str, primary_key=True)
    assert ddl_pk == "id text not null primary key"


def test_generate_create_table_rejects_unknown_override_field() -> None:
    with pytest.raises(schema_gen.SchemaGenError):
        schema_gen.generate_create_table(
            TraceRecord, "llm_calls", column_overrides={"not_a_real_field": "x text"}
        )


def test_generate_create_table_rejects_unknown_foreign_key_field() -> None:
    with pytest.raises(schema_gen.SchemaGenError):
        schema_gen.generate_create_table(
            TraceRecord, "llm_calls", foreign_keys={"not_a_real_field": "agents (id)"}
        )


def test_generate_create_table_rejects_unknown_primary_key_field() -> None:
    with pytest.raises(schema_gen.SchemaGenError):
        schema_gen.generate_create_table(TraceRecord, "llm_calls", primary_key_field="nope")


@dataclass(frozen=True)
class SmallExample:
    id: str
    count: int
    label: "str | None"


def test_generate_create_table_end_to_end_on_a_small_dataclass() -> None:
    ddl = schema_gen.generate_create_table(SmallExample, "small_example")
    conn = sqlite3.connect(":memory:")
    conn.executescript(ddl)
    conn.execute("insert into small_example (id, count, label) values ('a', 1, null)")
    row = conn.execute("select * from small_example").fetchone()
    assert row == ("a", 1, None)
    conn.close()


@pytest.mark.asyncio
async def test_real_tracerecord_row_inserts_into_a_real_llm_calls_table() -> None:
    from supabase.config import SupabaseConfig
    from supabase.sqlite_client import SqliteClient

    client = SqliteClient(SupabaseConfig(sqlite_path=":memory:"))
    try:
        await client.insert("agents", [{"slug": "a1", "display_name": "A", "sex": "male", "birth_tick": 0, "created_tick": 0}])
        (agent,) = await client.select("agents")

        record = TraceRecord(
            id="trc_test123",
            agent_id=agent["id"],
            payer_agent_id=agent["id"],
            model="moonshotai.kimi-k2.5",
            requested_model="moonshotai.kimi-k2.5",
            region="ap-south-1",
            call_class="cognition",
            purpose="agent_turn",
            request_body={"messages": [{"role": "user", "content": "hi"}]},
            response_body={"content": [{"type": "text", "text": "hello"}]},
            stop_reason="end_turn",
            input_tokens=42,
            output_tokens=17,
            cost_nano_dollars=123456,
            cents_debited=1,
            latency_ms=850,
            rate_limit_wait_ms=0,
            success=True,
            error_type=None,
            error_message=None,
            created_at=1732000000.5,
        )

        rows = await client.insert("llm_calls", [trace_record_to_row(record)])

        assert rows[0]["id"] == "trc_test123"
        assert rows[0]["request_body"] == {"messages": [{"role": "user", "content": "hi"}]}
        assert rows[0]["response_body"] == {"content": [{"type": "text", "text": "hello"}]}
        assert rows[0]["success"] is True
        assert rows[0]["input_tokens"] == 42
        assert rows[0]["tick"] is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_both_real_event_writers_insert_into_the_same_events_table() -> None:
    from supabase.config import SupabaseConfig
    from supabase.sqlite_client import SqliteClient

    client = SqliteClient(SupabaseConfig(sqlite_path=":memory:"))
    try:
        await client.insert("agents", [{"slug": "a1", "display_name": "A", "sex": "male", "birth_tick": 0, "created_tick": 0}])
        (agent,) = await client.select("agents")

        broker_event = BrokerEvent(
            id="evt_broker1",
            type="broker.call.completed",
            agent_id=agent["id"],
            model="moonshotai.kimi-k2.5",
            payload={"input_tokens": 42, "output_tokens": 17},
            created_at=1732000000.5,
        )
        world_row = {
            "type": "agent.moved",
            "tick": 42,
            "agent_id": agent["id"],
            "payload": {"destination_location_id": "town_hall"},
        }

        broker_rows = await client.insert("events", [broker_event_to_row(broker_event)])
        world_rows = await client.insert("events", [world_row])

        assert broker_rows[0]["id"] == "evt_broker1"
        assert broker_rows[0]["model"] == "moonshotai.kimi-k2.5"
        assert broker_rows[0]["domain"] == "broker"
        assert broker_rows[0]["tick"] is None

        assert isinstance(world_rows[0]["id"], str)
        assert world_rows[0]["model"] is None
        assert world_rows[0]["domain"] == "agent"
        assert world_rows[0]["tick"] == 42
        assert world_rows[0]["game_day"] == 0

        all_events = await client.select("events")
        assert len(all_events) == 2
    finally:
        await client.close()
