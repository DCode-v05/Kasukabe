"""Generates `llm_calls` and `events` DDL straight from broker/trace.py's
TraceRecord and BrokerEvent dataclasses - the fix for the exact failure
mode that stopped the 2026-07-31 genesis run: "TraceRecord has 21 fields,
the llm_calls table had 13 different columns, built by two tracks that
never reconciled." Every trace write failed silently that day (~4400 real
agent decisions, ~$25 of thinking, zero rows saved) because nothing forced
the hand-maintained sqlite_schema.sql to track trace.py's dataclasses as
they evolved.

ONE SOURCE OF TRUTH, mechanically: TraceRecord and BrokerEvent are what
broker/service.py actually constructs and hands to BatchWriter (see
trace.py's own module docstring) - not an aspiration, the literal Python
objects it builds every call. This module reads their real field names and
real type annotations (via `dataclasses.fields()` + `typing.get_type_hints`,
which resolves the `from __future__ import annotations`-deferred string
annotations trace.py uses, including the quoted `"str | None"` forms) and
turns each field into a SQLite column of a compatible type. There is no
second, hand-typed list of "what TraceRecord contains" anywhere in this
module for the two to drift apart on - supabase/tests/test_schema_gen.py
applies the generated DDL to a real (temporary) SQLite connection and
diffs its real `PRAGMA table_info` against sqlite_schema.sql's checked-in
DDL applied the same way, so a field added to either dataclass without a
matching sqlite_schema.sql edit fails a test loudly instead of silently
losing rows again.

Why this module imports FROM broker (`from broker.trace import
TraceRecord, BrokerEvent`) when app/CONVENTIONS.md's module boundaries
otherwise read as one-directional (broker -> supabase, never back): those
two dataclasses are not broker's private internals, they are the public
contract CONVENTIONS.md itself names supabase/ as backing ("`llm_calls`
... Supabase's tables") and this TASK's own instructions single out as
"authoritative." Reading them to generate a schema is the sanctioned use
of that contract, not a boundary violation - and it stays safe against an
import cycle because broker/trace.py imports nothing from supabase (only
asyncio/httpx/stdlib), and broker/app.py's own supabase imports are local
to build_service()'s non-mock branch, not module-level. If TraceRecord or
BrokerEvent ever start importing anything from supabase/, that assumption
needs re-checking; nothing here silently masks it if it stops holding
(the import at the top of this module would just fail to resolve).

Not every column below is a mechanical one-to-one mapping from a
dataclass field's Python type, because `events` is a genuinely
two-writer table (app/CONVENTIONS.md's event-naming section, and
broker/trace.py's own docstring: broker owns `broker.*` events on this
table, world/wiring.py's TraceStore.record_event owns every other
domain). BrokerEvent types `agent_id`/`model` as plain `str` because
broker itself never constructs one without both - but world's real
insert dict (world/wiring.py's `record_event`, read directly, not
assumed) is `{"type", "tick", "agent_id", "payload"}`: no `model` key at
all, and `agent_id` explicitly `None` for a non-agent-scoped event like
`world.tick.advanced`. A column NOT NULL because one writer's dataclass
never omits it would reject the OTHER writer's real, valid rows - so
EVENTS_COLUMN_OVERRIDES widens exactly those two columns to nullable,
documented here rather than silently mismatching either writer.
`created_at` is overridden on both tables for the opposite reason: to
ADD a database-side default (never to narrow anything) so a row built
by hand (this repo's live-DB repair path) still gets a timestamp without
having to know the dataclass's exact float convention.
"""

from __future__ import annotations

import types
import typing
from dataclasses import fields
from typing import Any, get_args, get_origin, get_type_hints

from broker.trace import BrokerEvent, TraceRecord

# Ticks per game-day (DESIGN.md section 1), matching every other
# *_game_day generated column in sqlite_schema.sql. Not imported from
# research/parquet_export.py's own TICKS_PER_GAME_DAY constant (that
# would make supabase/ depend on research/, backwards per
# app/CONVENTIONS.md) - both are 144 because DESIGN.md section 1 is 144,
# not because one imports the other.
TICKS_PER_GAME_DAY = 144


class SchemaGenError(ValueError):
    """A dataclass this module was asked to generate DDL for has a field
    shape schema_gen does not know how to map, or a caller referenced a
    field name that does not actually exist on that dataclass (a typo'd
    foreign_keys/column_overrides key is exactly the kind of silent-typo
    bug this whole module exists to catch, so it is checked, not trusted).
    """


# ── Python type -> SQLite column type ──────────────────────────────────

# SQLite's own type affinities (INTEGER/REAL/TEXT), keyed by the "base"
# Python type once Optional has been unwrapped. `bool` maps to the same
# `integer` affinity as `int` - SQLite has no boolean storage class - and
# every bool-typed column additionally gets a `check (col in (0, 1))`
# constraint, matching every other boolean column in sqlite_schema.sql
# (see column_ddl below). `dict` (always `dict[str, Any]` in these two
# dataclasses) maps to `text`: JSON-encoded, exactly how
# supabase/sqlite_client.py's SQLITE_JSON_COLUMNS already treats every
# other jsonb-shaped column in this schema.
SQLITE_TYPE_BY_PY_BASE: dict[Any, str] = {
    str: "text",
    int: "integer",
    float: "real",
    bool: "integer",
    dict: "text",
}


def is_optional(py_type: Any) -> tuple[bool, Any]:
    """(True, X) for `X | None` (either PEP 604's `types.UnionType` or
    `typing.Optional`/`typing.Union[X, None]` - `get_type_hints` can hand
    back either depending on how the source spelled it, and trace.py's
    fields use the `"X | None"` string-annotation form throughout, which
    resolves to `types.UnionType`), else (False, py_type) unchanged.
    Only single-type-plus-None unions are supported (all this repo's
    dataclasses ever need) - anything else raises rather than guessing.
    """
    origin = get_origin(py_type)
    if origin is typing.Union or origin is types.UnionType:
        args = get_args(py_type)
        non_none = [a for a in args if a is not type(None)]
        if type(None) in args and len(non_none) == 1:
            return True, non_none[0]
        raise SchemaGenError(f"schema_gen only supports 'X | None' unions, got {py_type!r}")
    return False, py_type


def base_py_type(py_type: Any) -> Any:
    """The type schema_gen actually maps to a column: `dict[str, Any]`
    reduced to its origin `dict` (SQLITE_TYPE_BY_PY_BASE is keyed by the
    origin, not the full parameterized generic, since SQLite does not
    care what a JSON column's Python value type parameter is)."""
    _, unwrapped = is_optional(py_type)
    origin = get_origin(unwrapped)
    return origin if origin is not None else unwrapped


def is_nullable_field(py_type: Any) -> bool:
    nullable, _ = is_optional(py_type)
    return nullable


def is_json_field(py_type: Any) -> bool:
    return base_py_type(py_type) is dict


def is_bool_field(py_type: Any) -> bool:
    return base_py_type(py_type) is bool


def sqlite_column_type(py_type: Any) -> str:
    base = base_py_type(py_type)
    if base not in SQLITE_TYPE_BY_PY_BASE:
        raise SchemaGenError(f"schema_gen has no SQLite mapping for Python type {py_type!r}")
    return SQLITE_TYPE_BY_PY_BASE[base]


def column_ddl(name: str, py_type: Any, *, primary_key: bool = False, foreign_key: "str | None" = None) -> str:
    """One column definition, mechanically derived from a dataclass
    field's real (resolved) type annotation. Order matches
    sqlite_schema.sql's existing house style: name, type, not null,
    primary key, references, check.
    """
    tokens = [name, sqlite_column_type(py_type)]
    # Every non-Optional field is NOT NULL, including the primary key -
    # SQLite, unlike most databases, does NOT imply NOT NULL for a
    # non-INTEGER PRIMARY KEY column (only an INTEGER PRIMARY KEY, which
    # aliases rowid, gets that for free), so `str`-typed id columns need
    # it spelled out explicitly or a NULL id would silently be legal.
    if not is_nullable_field(py_type):
        tokens.append("not null")
    if primary_key:
        tokens.append("primary key")
    if foreign_key:
        tokens.append(f"references {foreign_key}")
    if is_bool_field(py_type):
        tokens.append(f"check ({name} in (0, 1))")
    return " ".join(tokens)


def generate_create_table(
    dataclass_type: type,
    table_name: str,
    *,
    primary_key_field: str = "id",
    foreign_keys: "dict[str, str] | None" = None,
    column_overrides: "dict[str, str] | None" = None,
    extra_columns: "list[str] | None" = None,
) -> str:
    """`create table if not exists <table_name> (...)` with one column per
    dataclass_type field, in field declaration order, plus any
    extra_columns appended after them (generated/derived columns with no
    dataclass field of their own - `domain`, `game_day`,
    `game_minute_of_day`).

    foreign_keys / column_overrides are keyed by dataclass field name and
    checked against the dataclass's REAL field set before use
    (`fields(dataclass_type)`, not a hand-typed list) - a typo'd key
    (e.g. a field renamed in TraceRecord but not here) raises
    SchemaGenError immediately rather than silently generating a table
    that quietly ignores the override, which would be exactly the kind
    of drift this module exists to make impossible.
    """
    hints = get_type_hints(dataclass_type)
    declared_fields = {f.name for f in fields(dataclass_type)}
    foreign_keys = foreign_keys or {}
    column_overrides = column_overrides or {}

    unknown = (set(foreign_keys) | set(column_overrides)) - declared_fields
    if unknown:
        raise SchemaGenError(
            f"{table_name}: foreign_keys/column_overrides reference field(s) not on "
            f"{dataclass_type.__name__}: {sorted(unknown)}"
        )
    if primary_key_field not in declared_fields:
        raise SchemaGenError(
            f"{table_name}: primary_key_field {primary_key_field!r} is not a field of "
            f"{dataclass_type.__name__}"
        )

    lines: list[str] = []
    for f in fields(dataclass_type):
        if f.name in column_overrides:
            lines.append(column_overrides[f.name])
            continue
        lines.append(
            column_ddl(
                f.name,
                hints[f.name],
                primary_key=(f.name == primary_key_field),
                foreign_key=foreign_keys.get(f.name),
            )
        )
    lines.extend(extra_columns or [])

    body = ",\n    ".join(lines)
    return f"create table if not exists {table_name} (\n    {body}\n);"


def json_columns(dataclass_type: type) -> frozenset[str]:
    """Field names schema_gen mapped to a JSON-encoded `text` column -
    exactly the set supabase/sqlite_client.py's SQLITE_JSON_COLUMNS must
    contain for this table, so a caller can build that map from the same
    source of truth instead of retyping it (see sqlite_client.py's own
    module docstring: this table is meant to be "exhaustive over the
    schema, not best-effort").
    """
    hints = get_type_hints(dataclass_type)
    return frozenset(f.name for f in fields(dataclass_type) if is_json_field(hints[f.name]))


def bool_columns(dataclass_type: type) -> frozenset[str]:
    """Same idea as json_columns(), for SQLITE_BOOL_COLUMNS."""
    hints = get_type_hints(dataclass_type)
    return frozenset(f.name for f in fields(dataclass_type) if is_bool_field(hints[f.name]))


# ── llm_calls (TraceRecord) ─────────────────────────────────────────────
# llm_calls has exactly one writer (broker/app.py's SqliteTraceSink,
# "llm_calls" - see grep of the repo cited in this module's docstring: no
# other package inserts into it), so every column below is a pure,
# unwidened function of TraceRecord's own field types - no cross-writer
# nullability compromise is needed here the way events.py's is.

# Deliberately EMPTY. llm_calls is broker-owned (see broker/trace.py's
# module docstring: "world owns world STATE, broker owns the record of its
# own Bedrock calls"), and broker is a separate process that never writes
# the world-owned `agents` table. A foreign key from here into `agents`
# therefore cannot hold: observed live on 2026-07-31, every insert failed
# with "FOREIGN KEY constraint failed", the writer halted after 3
# consecutive failures exactly as designed, and 122 real agent decisions
# were dropped. An agent_id here is a reference to an agent by id, not a
# database-enforced relationship across a process boundary whose insert
# ordering nothing guarantees.
LLM_CALLS_FOREIGN_KEYS: dict[str, str] = {}

LLM_CALLS_COLUMN_OVERRIDES: dict[str, str] = {
    # TraceRecord.created_at is always a real Python float (trace.py's
    # now_s() = time.time()) at every call site that builds one today, so
    # this default is a pure safety net (a hand-built repair-script row
    # that forgets to set it still gets a timestamp), never something a
    # real broker-authored row relies on. REAL, not the ISO8601 TEXT
    # every other table's created_at uses: translating a Python float to
    # a formatted string at the schema boundary would itself be exactly
    # the kind of assumed-contract seam this module exists to eliminate,
    # so this column stores exactly what TraceRecord actually carries.
    "created_at": "created_at real not null default (cast(strftime('%s', 'now') as real))",
    # Every one of these is a count/cost/duration - never legitimately
    # negative. `int` alone does not encode that (schema_gen has no
    # generic "non-negative int" type), so it is spelled out per-field
    # here, matching the >= 0 checks the schema this replaces already
    # had on its equivalent columns (request_tokens/response_tokens/
    # cost_usd_cents) - a real data-integrity net, not new behavior.
    "input_tokens": "input_tokens integer not null check (input_tokens >= 0)",
    "output_tokens": "output_tokens integer not null check (output_tokens >= 0)",
    "cost_nano_dollars": "cost_nano_dollars integer not null check (cost_nano_dollars >= 0)",
    "cents_debited": "cents_debited integer not null check (cents_debited >= 0)",
    "latency_ms": "latency_ms integer not null check (latency_ms >= 0)",
    "rate_limit_wait_ms": "rate_limit_wait_ms integer not null check (rate_limit_wait_ms >= 0)",
    # Optional (see module docstring), but never negative when present.
    "tick": "tick integer check (tick is null or tick >= 0)",
}

# `tick` (added to TraceRecord alongside this fix - see trace.py's own
# module docstring for why it is Optional) has no live writer yet:
# broker has no access to world's current tick today (broker/ledger.py's
# HttpLedgerClient contract, confirmed against world/main.py's real
# /ledger/payer and /ledger/debit routes, carries no tick field). Nullable
# game_day/game_minute_of_day tolerate that honestly (NULL tick -> NULL
# generated columns -> the row simply never matches a Parquet game-day
# export's WHERE clause) rather than defaulting tick to a false 0 that
# would silently mis-bucket every call into "day zero" forever.
LLM_CALLS_EXTRA_COLUMNS: list[str] = [
    "game_day integer generated always as (tick / 144) stored",
    "game_minute_of_day integer generated always as ((tick % 144) * 10) stored",
    # INFRA-08. Where the FULL prompt and response were archived, when the
    # row itself holds a truncated copy. Extra columns rather than
    # TraceRecord fields because no writer sets them on the record:
    # `broker/trace.py`'s archive step writes them into the row dict after
    # comparing what was stored against what was sent, so the record never
    # carries them and adding them there would create a field nobody fills.
    #
    # NULL means "not archived", which is a different fact from "archived
    # somewhere you cannot find" - see that function's docstring.

    "request_archive_key text",
    "response_archive_key text",
]


def llm_calls_ddl() -> str:
    return generate_create_table(
        TraceRecord,
        "llm_calls",
        foreign_keys=LLM_CALLS_FOREIGN_KEYS,
        column_overrides=LLM_CALLS_COLUMN_OVERRIDES,
        extra_columns=LLM_CALLS_EXTRA_COLUMNS,
    )


# ── events (BrokerEvent + world/wiring.py's WorldEvent rows) ───────────
# Two real writers into one physical table (see this module's docstring):
# broker/app.py's SqliteTraceSink("events", broker_event_to_row) and
# world/wiring.py's TraceStore.record_event. BrokerEvent types agent_id
# and model as plain `str` (broker never omits either), but world's real
# insert dict never includes `model` at all and explicitly sends
# `agent_id: None` for a non-agent-scoped event - both overridden to
# nullable here so the schema matches the union of what BOTH writers
# actually produce, not just one dataclass's own idea of required.

# Empty for the same reason as LLM_CALLS_FOREIGN_KEYS above: broker writes
# events for its own broker.* call lifecycle before world has necessarily
# seeded an `agents` row, and the two live in different processes.
EVENTS_FOREIGN_KEYS: dict[str, str] = {}

EVENTS_COLUMN_OVERRIDES: dict[str, str] = {
    # See module docstring: world/wiring.py's record_event() never sets
    # target_agent_id/agent_id for e.g. "world.tick.advanced", and never
    # sets "model" (a broker-only concept) at all.
    "agent_id": "agent_id text",
    "model": "model text",
    # SQLite GLOB has no bounded-repetition operator, so this cannot
    # mirror migrations/20260730120900_events.sql's Postgres regex
    # exactly (2 or 3 lowercase snake_case segments); it checks the
    # properties app/CONVENTIONS.md's naming scheme actually requires
    # instead: all-lowercase, starts with a letter, at least one dot, no
    # leading/trailing/doubled dot. Unchanged from the schema this
    # replaces - supabase/tests/test_schema_gen.py proves it still
    # accepts every example in CONVENTIONS.md's event table.
    "type": (
        "type text not null check ("
        "type = lower(type) "
        "and type glob '[a-z]*' "
        "and instr(type, '.') > 0 "
        "and type not glob '*.' "
        "and type not glob '*..*'"
        ")"
    ),
    # Same reasoning as LLM_CALLS_COLUMN_OVERRIDES['created_at'] above:
    # REAL epoch seconds (what BrokerEvent.created_at actually is), with
    # a database-side default so world's rows (which never set this
    # field at all) still get a real timestamp.
    "created_at": "created_at real not null default (cast(strftime('%s', 'now') as real))",
    # BrokerEvent.payload is `dict[str, Any]`, never Optional, and every
    # real writer (both of them) always supplies it - this default is a
    # pure safety net matching the pre-existing schema's own
    # `default '{}'`, not something either writer depends on.
    "payload": "payload text not null default '{}'",
    # Optional (see module docstring), but never negative when present.
    "tick": "tick integer check (tick is null or tick >= 0)",
}

EVENTS_EXTRA_COLUMNS: list[str] = [
    # split_part() has no SQLite equivalent; substr/instr reproduces
    # "everything before the first dot" instead. Unchanged from the
    # schema this replaces.
    "domain text generated always as (substr(type, 1, instr(type, '.') - 1)) stored",
    "game_day integer generated always as (tick / 144) stored",
    "game_minute_of_day integer generated always as ((tick % 144) * 10) stored",
]


def events_ddl() -> str:
    return generate_create_table(
        BrokerEvent,
        "events",
        foreign_keys=EVENTS_FOREIGN_KEYS,
        column_overrides=EVENTS_COLUMN_OVERRIDES,
        extra_columns=EVENTS_EXTRA_COLUMNS,
    )


if __name__ == "__main__":
    # Regeneration aid for a human keeping sqlite_schema.sql's checked-in
    # DDL in sync by hand: `python -m supabase.schema_gen` prints both
    # blocks so they can be pasted in. Not run automatically at import or
    # test time - supabase/tests/test_schema_gen.py is what actually
    # enforces the two stay identical (see this module's docstring), by
    # applying both to real SQLite connections and diffing PRAGMA
    # table_info, not by trusting a human re-pasted correctly.
    print("-- llm_calls " + "-" * 60)
    print(llm_calls_ddl())
    print()
    print("-- events " + "-" * 60)
    print(events_ddl())
