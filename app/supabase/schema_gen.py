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

TICKS_PER_GAME_DAY = 144


class SchemaGenError(ValueError):
    """A dataclass this module was asked to generate DDL for has a field
    shape schema_gen does not know how to map, or a caller referenced a
    field name that does not actually exist on that dataclass (a typo'd
    foreign_keys/column_overrides key is exactly the kind of silent-typo
    bug this whole module exists to catch, so it is checked, not trusted).
    """


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


LLM_CALLS_FOREIGN_KEYS: dict[str, str] = {}

LLM_CALLS_COLUMN_OVERRIDES: dict[str, str] = {
    "created_at": "created_at real not null default (cast(strftime('%s', 'now') as real))",
    "input_tokens": "input_tokens integer not null check (input_tokens >= 0)",
    "output_tokens": "output_tokens integer not null check (output_tokens >= 0)",
    "cost_nano_dollars": "cost_nano_dollars integer not null check (cost_nano_dollars >= 0)",
    "cents_debited": "cents_debited integer not null check (cents_debited >= 0)",
    "latency_ms": "latency_ms integer not null check (latency_ms >= 0)",
    "rate_limit_wait_ms": "rate_limit_wait_ms integer not null check (rate_limit_wait_ms >= 0)",
    "tick": "tick integer check (tick is null or tick >= 0)",
}

LLM_CALLS_EXTRA_COLUMNS: list[str] = [
    "game_day integer generated always as (tick / 144) stored",
    "game_minute_of_day integer generated always as ((tick % 144) * 10) stored",

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


EVENTS_FOREIGN_KEYS: dict[str, str] = {}

EVENTS_COLUMN_OVERRIDES: dict[str, str] = {
    "agent_id": "agent_id text",
    "model": "model text",
    "type": (
        "type text not null check ("
        "type = lower(type) "
        "and type glob '[a-z]*' "
        "and instr(type, '.') > 0 "
        "and type not glob '*.' "
        "and type not glob '*..*'"
        ")"
    ),
    "created_at": "created_at real not null default (cast(strftime('%s', 'now') as real))",
    "payload": "payload text not null default '{}'",
    "tick": "tick integer check (tick is null or tick >= 0)",
}

EVENTS_EXTRA_COLUMNS: list[str] = [
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
    print("-- llm_calls " + "-" * 60)
    print(llm_calls_ddl())
    print()
    print("-- events " + "-" * 60)
    print(events_ddl())
