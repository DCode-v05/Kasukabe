"""SupabaseClient over local SQLite: the trace store, in dev/test and in
production alike.

Applies app/supabase/sqlite_schema.sql on first use (idempotent, every
statement in that file is "create table/index if not exists") so a caller
gets a fully-shaped database with zero setup - construct a SqliteClient
and start inserting. In a test this is a throwaway :memory: or temp-file
database; on the genesis host (DESIGN.md section 9) it is the one real
WAL file every world/ and broker/ process in that microVM fleet writes
through, continuously replicated to S3 by Litestream running alongside
it (app/infra/litestream.yml) - the same schema, the same client code,
the same crash-safety pragmas either way. There is no second backend to
keep in sync with: this module used to be "the local-dev fallback" next
to a PostgrestClient talking to a real Supabase project; DESIGN.md
section 9 (decided 2026-07-30) replaced that project with this file, so
now there is exactly one implementation of SupabaseClient.

Crash safety (see connect_sqlite() below) is three pragmas, not zero:
WAL journal mode, a busy_timeout so a lock contended by Litestream's
checkpoint or a concurrent reader does not surface as an immediate
"database is locked" error, and synchronous=NORMAL, which SQLite's own
documentation describes as safe from corruption in WAL mode and durable
across an application crash (a killed process) - the only thing it does
not guarantee is durability across an OS-level power loss, which this
project accepts in exchange for not paying an fsync on every commit.
supabase/tests/test_supabase_crash_safety.py proves this against a real
SIGKILL, not just cites the documentation.

sqlite3 is synchronous; every public method wraps its blocking call in
asyncio.to_thread so this class satisfies SupabaseClient's async
interface (app/CONVENTIONS.md: "Async for anything that does I/O")
without pulling in aiosqlite as a dependency for what is, in the end, a
single-file local database with no meaningful concurrent I/O latency.

Two type gaps between SQLite's dynamic typing and the schema it
implements need explicit handling so a caller always gets back native
Python types (dict/list/bool), never a JSON-text string or a 0/1 int:

- jsonb/vector/array columns are stored as TEXT (JSON-encoded) in
  sqlite_schema.sql; SQLITE_JSON_COLUMNS lists exactly which columns need
  json.dumps on the way in and json.loads on the way out.
- boolean columns are stored as INTEGER 0/1 (SQLite has no boolean type);
  SQLITE_BOOL_COLUMNS lists which columns convert bool <-> 0/1.

Both tables are exhaustive over the schema, not best-effort: a column
present in sqlite_schema.sql as JSON-or-boolean-shaped but missing from
these tables is a bug, and test_sqlite_client.py checks every table in the
schema is at least represented in the coercion machinery's table list.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from supabase.client import Row, SupabaseClient, SupabaseError
from supabase.config import SupabaseConfig
from supabase.s3_client import ARCHIVE_BUCKET_PREFIXES, S3Client, S3Config, S3Error

SCHEMA_PATH = Path(__file__).resolve().parent / "sqlite_schema.sql"

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def schema_columns() -> dict[str, dict[str, str]]:
    """table -> column -> its type declaration, read off the DDL itself.

    Parsed rather than hand-listed, so a column added to
    sqlite_schema.sql needs no second edit here - a migration list that
    has to be maintained alongside a schema is a migration list that
    drifts from it, which is the failure this whole function exists to
    end.

    Deliberately shallow: it takes the column NAME and the first word of
    its type, and skips anything it cannot read cleanly. A generated
    column, a table constraint, or a line this parser does not understand
    is left to the DDL above rather than guessed at.
    """
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {}
    for match in re.finditer(
        r"create table if not exists\s+(\w+)\s*\((.*?)\n\);",
        text,
        re.IGNORECASE | re.DOTALL,
    ):
        table, body = match.group(1), match.group(2)
        columns: dict[str, str] = {}
        for raw in body.split("\n"):
            line = raw.split("--", 1)[0].strip().rstrip(",")
            if not line:
                continue
            parts = line.split()
            name = parts[0]
            if len(parts) < 2 or not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
                continue
            if name in {
                "primary", "foreign", "unique", "check", "constraint", "references",
            }:
                continue
            kind = parts[1].lower()
            if kind not in {"text", "integer", "real", "blob", "numeric"}:
                continue
            # Only additive-safe columns. A NOT NULL or a generated column
            # cannot be ALTERed in without a default and is a real
            # migration, not a boot-time fixup.
            if "not null" in line.lower() or "generated" in line.lower():
                continue
            if "primary key" in line.lower():
                continue
            columns[name] = kind
        out[table] = columns
    return out


def connect_sqlite(sqlite_path: str, *, busy_timeout_ms: int) -> sqlite3.Connection:
    """Open a connection with the trace store's crash-safety pragmas set.

    A free function (not inlined in __init__) so
    test_supabase_crash_safety.py's writer subprocess opens the database
    exactly the way SqliteClient does, rather than duplicating the pragma
    list and risking the two drifting apart.

    WAL mode only applies to a real file: SQLite has no write-ahead log
    for ":memory:" databases (there is nothing to check-point against, the
    whole database is already just memory), and asking for it there is a
    silent no-op rather than an error - skip it explicitly so the
    intent is documented, not accidental.

    synchronous=NORMAL is set unconditionally (it is also SQLite's
    default), named explicitly here rather than left implicit so the
    module docstring's crash-safety claim is something this function
    visibly does, not something a reader has to trust.
    """
    conn = sqlite3.connect(sqlite_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    conn.execute("PRAGMA synchronous = NORMAL")
    if sqlite_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn

# See module docstring. Keep alphabetized by table for easy diffing against
# migrations/ when a new jsonb/vector/array column is added there.
SQLITE_JSON_COLUMNS: dict[str, frozenset[str]] = {
    # A list of agent ids, written whole and read whole - see the
    # utterances table in sqlite_schema.sql for why it is a list at all.
    "utterances": frozenset({"addressed_agent_ids"}),
    "buildings": frozenset({"footprint", "rooms", "bill_of_materials"}),
    "eval_metrics": frozenset({"metric_payload"}),
    "events": frozenset({"payload"}),
    # request_body/response_body: dict[str, Any] on broker/trace.py's
    # TraceRecord (see supabase/schema_gen.py's llm_calls_ddl()) - built
    # from schema_gen.json_columns(TraceRecord) would report the same set;
    # kept as an explicit literal here, like every other table in this
    # map, so this dict alone is a complete answer to "what does the
    # schema actually need coerced", not something a reader has to run
    # schema_gen to discover. test_schema_gen.py cross-checks the two
    # stay in sync.
    "llm_calls": frozenset({"request_body", "response_body"}),
    "memories": frozenset({"embedding", "related_memory_ids"}),
    "petitions": frozenset({"payload"}),
    "tools_registry": frozenset({"composition"}),
}

SQLITE_BOOL_COLUMNS: dict[str, frozenset[str]] = {
    "agents": frozenset({"is_minor", "is_dormant", "is_alive"}),
    "businesses": frozenset({"is_control_business"}),
    # Renamed from "succeeded": TraceRecord's real field is `success` (see
    # broker/trace.py) - the old name here never matched the dataclass it
    # was meant to serve.
    "llm_calls": frozenset({"success"}),
    "organizations": frozenset({"is_registered"}),
    "tools_registry": frozenset({"is_licensable", "is_teachable"}),
    "utterances": frozenset({"is_public"}),
}

# Every filter operator both backends support. postgrest_client.py sends
# these strings to PostgREST verbatim; here they translate to SQL. Adding
# an operator to client.py's helpers without a translation here would make
# it silently postgrest-only, so keep this in sync with that module's
# helper list.
OP_TO_SQL: dict[str, str] = {
    "eq": "=",
    "neq": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "like": "LIKE",
    "ilike": "LIKE",  # SQLite's LIKE is already case-insensitive for ASCII.
}


def build_where(filters: dict[str, str]) -> tuple[str, list[Any]]:
    """Translate a client.py-style filter dict into a SQL WHERE clause.

    Returns ("1=1", []) for an empty filter dict so callers can always
    interpolate the result without a special case for "no filters".
    """
    if not filters:
        return "1=1", []

    clauses: list[str] = []
    params: list[Any] = []
    for column, expr in filters.items():
        op, _, operand = expr.partition(".")
        if op == "is":
            if operand == "null":
                clauses.append(f"{column} IS NULL")
            elif operand == "true":
                clauses.append(f"{column} IS 1")
            elif operand == "false":
                clauses.append(f"{column} IS 0")
            else:
                raise ValueError(f"unsupported is.{operand!r} filter on column {column!r}")
        elif op == "in":
            values = operand.removeprefix("(").removesuffix(")")
            items = [v for v in values.split(",") if v != ""]
            placeholders = ",".join("?" for _ in items)
            clauses.append(f"{column} IN ({placeholders})")
            params.extend(items)
        elif op in OP_TO_SQL:
            clauses.append(f"{column} {OP_TO_SQL[op]} ?")
            params.append(operand)
        else:
            raise ValueError(f"unsupported filter operator {op!r} on column {column!r}")
    return " AND ".join(clauses), params


def build_order(order: str) -> str:
    """Translate PostgREST's "column.asc"/"column.desc" into SQL ORDER BY.

    A bare column name (no ".asc"/".desc" suffix) means ascending, matching
    PostgREST's own default.
    """
    parts = []
    for term in order.split(","):
        column, _, direction = term.strip().partition(".")
        direction = direction.lower() or "asc"
        if direction not in ("asc", "desc"):
            raise ValueError(f"unsupported order direction {direction!r} in {term!r}")
        parts.append(f"{column} {direction.upper()}")
    return ", ".join(parts)


def encode_row(table: str, row: Row) -> dict[str, Any]:
    """Row as the caller wrote it (native dict/list/bool) -> row as SQLite
    stores it (JSON text for jsonb-shaped columns, 0/1 for booleans).
    """
    json_columns = SQLITE_JSON_COLUMNS.get(table, frozenset())
    bool_columns = SQLITE_BOOL_COLUMNS.get(table, frozenset())
    encoded: dict[str, Any] = {}
    for column, value in row.items():
        if column in json_columns and value is not None and not isinstance(value, str):
            encoded[column] = json.dumps(value)
        elif column in bool_columns and isinstance(value, bool):
            encoded[column] = 1 if value else 0
        else:
            encoded[column] = value
    return encoded


def decode_row(table: str, row: Row) -> Row:
    """Inverse of encode_row: what SQLite returns -> what a caller of
    either backend expects (dict/list for jsonb columns, bool for boolean
    columns).
    """
    json_columns = SQLITE_JSON_COLUMNS.get(table, frozenset())
    bool_columns = SQLITE_BOOL_COLUMNS.get(table, frozenset())
    decoded: dict[str, Any] = {}
    for column, value in row.items():
        if column in json_columns and isinstance(value, str):
            decoded[column] = json.loads(value)
        elif column in bool_columns and value is not None:
            decoded[column] = bool(value)
        else:
            decoded[column] = value
    return decoded


class SqliteClient(SupabaseClient):
    """Local-dev / test fallback. See client.py for the interface contract.

    conn_lock (2026-07-31, live-fire fix): broker/app.py's build_service()
    hands ONE SqliteClient to TWO independent BatchWriters (trace_writer
    for llm_calls, event_writer for events), each with its own background
    flush loop - a real, observed-in-production race, not a hypothetical
    one: the very first real Bedrock call this fix was tested against
    tripped it. insert_sync's "insert, then `select ... where rowid = ?`
    using cursor.lastrowid" pattern relies on
    sqlite3_last_insert_rowid(), which SQLite's own docs call out as
    connection-scoped and "unpredictable" if a second thread inserts on
    the SAME connection while it's being read - exactly what happens when
    both writers' periodic flushes land in the same instant (each
    dispatched via asyncio.to_thread onto a worker thread, both against
    this one self.conn). Observed failure mode: cursor.lastrowid read back
    a rowid that belonged to the OTHER writer's table, `select * from
    llm_calls where rowid = <a rowid that only exists in events>` returned
    no row, and `dict(None)` raised "'NoneType' object is not iterable" -
    silently dropping the batch, counted and logged by trace.py's
    BatchWriter, but a real loss all the same. Every insert/select/update
    below now serializes on conn_lock, closing this for every current and
    future caller of this class, not just broker's two writers - mirrors
    world/wiring.py's own TraceStore.write_lock, which independently
    reached the identical conclusion for its own single-writer case; this
    pushes the same guarantee down into the shared client so nothing else
    that ever calls SqliteClient concurrently has to rediscover it.
    """

    def __init__(self, config: SupabaseConfig) -> None:
        self.config = config
        self.conn = connect_sqlite(config.sqlite_path, busy_timeout_ms=config.busy_timeout_ms)
        self.ensure_schema()
        # Serializes every insert/select/update against self.conn - see
        # each method's own comment for why. Not init=False/dataclass
        # machinery: this class is a plain class, so a normal instance
        # attribute is enough; every asyncio.to_thread body below runs
        # under this lock, held for the coroutine's duration (an
        # asyncio.Lock, not a threading.Lock - correct because the
        # caller awaiting it is a coroutine, even though the work it
        # guards runs in a worker thread).
        self.conn_lock = asyncio.Lock()
        # Storage stand-in: bucket/path pairs become files under this
        # directory when no S3 bucket is configured (every test, and any
        # local dev run without KASUKABE_S3_BUCKET set) so a caller
        # storing that "bucket/path" string in payload_archive_path never
        # has to know whether it landed on disk or in S3.
        if config.sqlite_path == ":memory:":
            import tempfile

            self.local_storage_root = Path(tempfile.gettempdir()) / "kasukabe-local-storage"
        else:
            self.local_storage_root = Path(config.sqlite_path).resolve().parent / "local_storage"
        # Built lazily (not here) because constructing it is cheap but
        # pointless when config.s3_bucket is empty, which is every test in
        # this repo per app/CONVENTIONS.md's "never call a real ... API in
        # a test" - see upload_archive_sync.
        self.s3: S3Client | None = None
        self.pk_kind_cache: dict[str, str] = {}

    def ensure_schema(self) -> None:
        """Bring this database up to the checked-in schema, every boot.

        THIS USED TO RUN ONLY ON AN EMPTY DATABASE. Every statement in
        sqlite_schema.sql is `if not exists` - all 79 of them - so the
        guard was not protecting anything, and what it actually did was
        make the schema unreachable the moment a database had one table in
        it. A column added to the DDL never arrived; a whole new table
        never arrived either.

        Measured on a six-tick boot against an existing data/ directory:
        **213 transaction rows dropped in six ticks**, every one of them
        "table transactions has no column named counterparty_id". The
        column has been in the schema since the counterparty work; the
        file on disk predated it, and `create table if not exists` does
        not add columns to a table that already exists. Every buy, sell
        and transfer in the run went to the trace store and was thrown
        away, at WARNING level, while the run reported success.

        So: run the script always, then add any column the schema has and
        this database does not. SQLite's ALTER TABLE ADD COLUMN handles
        exactly the nullable-no-default case every additive column here
        is, and nothing below ever drops or retypes anything - a
        destructive repair is `supabase/repair_live_schema.py`'s job, and
        it deliberately refuses to touch a table with rows in it.
        """
        # Columns FIRST, then the rest of the DDL. sqlite_schema.sql
        # creates indexes over columns an older table may not have yet,
        # and `executescript` aborts at the first error - so applying the
        # script before the ALTERs would abandon every statement after the
        # first index it could not build.
        self.add_missing_columns()
        self.apply_ddl()
        # And again, for anything the DDL just created. A no-op on a
        # database that was already current, which is the usual case.
        self.add_missing_columns()
        self.conn.commit()

    def apply_ddl(self) -> str | None:
        """Run sqlite_schema.sql. Returns the error if it could not finish.

        `executescript`, because it is the only thing that parses this file
        correctly - the DDL carries semicolons inside comment blocks and
        any hand-rolled split on ";" mangles them.

        It does stop at the first error and abandon the rest, which is why
        `add_missing_columns` runs BEFORE it: the failure that actually
        happens is an index over a column an older table does not have
        yet, and adding the column first removes it. Anything left is
        logged at ERROR rather than swallowed - a schema this database
        could not take is exactly the kind of thing that has been dropping
        two hundred rows a run in silence.
        """
        try:
            self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        except sqlite3.Error as exc:
            logger.error(
                "sqlite_schema.sql could not be applied to %s - rows using "
                "anything it declares will be DROPPED until this is fixed: %s",
                self.config.sqlite_path, exc,
            )
            return str(exc)
        return None

    def add_missing_columns(self) -> list[tuple[str, str]]:
        """ALTER in any column the schema declares and this file lacks.

        Returns what it added, so a caller (and the test) can see it
        happen rather than trust that it did. Additive only: a column the
        DATABASE has and the schema does not is left alone, because that
        is either older data worth keeping or somebody else's migration in
        flight, and neither is this method's to decide about.
        """
        added: list[tuple[str, str]] = []
        for table, columns in schema_columns().items():
            try:
                live = {
                    row["name"] if not isinstance(row, tuple) else row[1]
                    for row in self.conn.execute(f"PRAGMA table_info({table})")
                }
            except sqlite3.Error:
                continue
            if not live:
                continue  # the table is not here at all; the DDL above owns that
            for name, decl in columns.items():
                if name in live:
                    continue
                try:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                except sqlite3.Error as exc:
                    # A column SQLite will not add additively (one with a
                    # non-constant default, say) is a real migration and
                    # not something to do silently at boot. Say so and
                    # leave it: a loud gap beats a quiet one.
                    logger.error(
                        "could not add %s.%s (%s) - rows using it will be "
                        "DROPPED until this is migrated by hand: %s",
                        table, name, decl, exc,
                    )
                    continue
                added.append((table, name))
                logger.info("schema: added missing column %s.%s", table, name)
        return added

    # ── primary key generation ────────────────────────────────────────

    def id_column_kind(self, table: str) -> str:
        """"uuid" for entity tables (id TEXT PRIMARY KEY, app-generated) or
        "autoincrement" for log tables (id INTEGER PRIMARY KEY
        AUTOINCREMENT, SQLite-generated). Cached per table since the
        schema never changes within a connection's lifetime.
        """
        if table in self.pk_kind_cache:
            return self.pk_kind_cache[table]
        columns = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        id_column = next((c for c in columns if c["name"] == "id"), None)
        if id_column is None:
            kind = "none"
        elif id_column["type"].lower() == "text":
            kind = "uuid"
        else:
            kind = "autoincrement"
        self.pk_kind_cache[table] = kind
        return kind

    def fill_generated_id(self, table: str, row: Row) -> Row:
        if row.get("id"):
            return row
        if self.id_column_kind(table) == "uuid":
            return {**row, "id": str(uuid.uuid4())}
        # autoincrement (or no id column at all): leave unset, SQLite
        # assigns it on insert.
        row = dict(row)
        row.pop("id", None)
        return row

    # ── SupabaseClient interface ────────────────────────────────────────

    async def insert(self, table: str, rows: list[Row]) -> list[Row]:
        if not rows:
            return []
        async with self.conn_lock:
            return await asyncio.to_thread(self.insert_sync, table, rows)

    def insert_sync(self, table: str, rows: list[Row]) -> list[Row]:
        cursor = self.conn.cursor()
        results: list[Row] = []
        try:
            for row in rows:
                row = self.fill_generated_id(table, row)
                encoded = encode_row(table, row)
                columns = list(encoded.keys())
                placeholders = ",".join("?" for _ in columns)
                cursor.execute(
                    f"insert into {table} ({','.join(columns)}) values ({placeholders})",
                    [encoded[c] for c in columns],
                )
                inserted = cursor.execute(
                    f"select * from {table} where rowid = ?", (cursor.lastrowid,)
                ).fetchone()
                results.append(decode_row(table, dict(inserted)))
            self.conn.commit()
        except sqlite3.Error as exc:
            self.conn.rollback()
            raise SupabaseError(table=table, detail=str(exc)) from exc
        return results

    async def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: dict[str, str] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[Row]:
        async with self.conn_lock:
            return await asyncio.to_thread(self.select_sync, table, columns, filters, order, limit)

    def select_sync(
        self,
        table: str,
        columns: str,
        filters: dict[str, str] | None,
        order: str | None,
        limit: int | None,
    ) -> list[Row]:
        where_clause, params = build_where(filters or {})
        query = f"select {columns} from {table} where {where_clause}"
        if order:
            query += f" order by {build_order(order)}"
        if limit is not None:
            query += f" limit {int(limit)}"
        try:
            rows = self.conn.execute(query, params).fetchall()
        except sqlite3.Error as exc:
            raise SupabaseError(table=table, detail=str(exc)) from exc
        return [decode_row(table, dict(r)) for r in rows]

    async def update(self, table: str, *, filters: dict[str, str], values: Row) -> list[Row]:
        async with self.conn_lock:
            return await asyncio.to_thread(self.update_sync, table, filters, values)

    def update_sync(self, table: str, filters: dict[str, str], values: Row) -> list[Row]:
        where_clause, where_params = build_where(filters)
        try:
            matched = self.conn.execute(
                f"select rowid from {table} where {where_clause}", where_params
            ).fetchall()
            rowids = [r["rowid"] for r in matched]
            if not rowids:
                return []
            encoded = encode_row(table, values)
            set_clause = ", ".join(f"{c} = ?" for c in encoded)
            placeholders = ",".join("?" for _ in rowids)
            self.conn.execute(
                f"update {table} set {set_clause} where rowid in ({placeholders})",
                [*encoded.values(), *rowids],
            )
            self.conn.commit()
            updated = self.conn.execute(
                f"select * from {table} where rowid in ({placeholders})", rowids
            ).fetchall()
        except sqlite3.Error as exc:
            self.conn.rollback()
            raise SupabaseError(table=table, detail=str(exc)) from exc
        return [decode_row(table, dict(r)) for r in updated]

    async def upload_archive(
        self, *, bucket: str, path: str, data: bytes, content_type: str = "application/gzip"
    ) -> str:
        """Storage backend for archiver.py: S3 when config.s3_bucket is
        set (the genesis host), a local directory otherwise (every test,
        and any local-dev run with no S3 configured).

        `bucket` is a logical bucket name ("llm-payloads",
        "world-snapshots" - see archiver.py), not a second real S3 bucket
        to create. The return value is always "<bucket>/<path>" whether or
        not S3 is configured, so payload_archive_path in llm_calls is
        identical in shape either way; when S3 is configured, the actual
        object lands at a different, fixed key
        (s3_client.ARCHIVE_BUCKET_PREFIXES[bucket] + "/" + path, e.g.
        "archives/payloads/agent-001/....json.gz") because that prefix
        layout is infra/scripts/01-setup-s3-and-iam.sh's contract for this
        bucket, not this method's to invent - reconstructing the real key
        from a stored payload_archive_path is one dict lookup away, not a
        guess.
        """
        if self.config.s3_bucket:
            if self.s3 is None:
                self.s3 = S3Client(
                    S3Config(
                        bucket=self.config.s3_bucket,
                        region=self.config.s3_region,
                        prefix=self.config.s3_prefix,
                        request_timeout_s=self.config.request_timeout_s,
                    )
                )
            s3_key = f"{ARCHIVE_BUCKET_PREFIXES.get(bucket, bucket)}/{path}"
            try:
                await self.s3.put_object(key=s3_key, data=data, content_type=content_type)
            except S3Error as exc:
                raise SupabaseError(table=f"s3:{bucket}", detail=str(exc)) from exc
            return f"{bucket}/{path}"
        return await asyncio.to_thread(self.upload_archive_sync, bucket, path, data)

    def upload_archive_sync(self, bucket: str, path: str, data: bytes) -> str:
        target = self.local_storage_root / bucket / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return f"{bucket}/{path}"

    async def close(self) -> None:
        self.conn.close()
        if self.s3 is not None:
            await self.s3.close()
