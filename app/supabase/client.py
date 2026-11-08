"""The SupabaseClient interface: the trace store's contract.

world/ and broker/ are the only writers of trace-store state
(app/CONVENTIONS.md's module boundaries); both call this interface, never
sqlite3 directly, so callers never open their own connection or hand-roll
their own SQL. Callers get a client from factory.get_client(), never
construct SqliteClient directly.

This used to be "one contract, two backends" (a real Supabase project
over PostgREST, or this SQLite fallback for local dev). DESIGN.md section
9 (decided 2026-07-30) collapsed that to one: SQLite (WAL mode) is the
production trace store, replicated to S3 by Litestream instead of by
being a second network service. The ABC stays an abstract interface
rather than collapsing into SqliteClient directly because tests still
benefit from depending on the interface, not the implementation, and
because it is the one seam a future backend (DESIGN.md section 9: "swap
to native Postgres only at ~1000 agents / multi-host - same client
interface") would need.

Filters follow PostgREST's own operator convention (a filter value is
"<operator>.<operand>", e.g. "eq.some-uuid" or "gte.144") - a naming
choice from when this package still talked to PostgREST directly, kept
because sqlite_client.py's build_where already parses it and there is no
reason to invent a second filter syntax now that SQLite is the only
backend. The helper functions below (eq, neq, gt, ...) are how a caller
builds one without memorizing the string format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# A table row, decoded to native Python types (str/int/float/bool/None/
# dict/list). Both backends hand back this shape from every method:
# PostgREST because its response body already is JSON; SqliteClient
# because it decodes JSON-text and 0/1-int columns back to dict/list/bool
# before returning (see sqlite_client.py's SQLITE_JSON_COLUMNS and
# SQLITE_BOOL_COLUMNS) so a caller never has to know which backend it got.
Row = dict[str, Any]


class SupabaseError(RuntimeError):
    """A backend request failed. Carries enough to debug without a retry.

    table and detail are always populated; status_code is None for the
    SQLite backend (there is no HTTP status to report) and set for the
    PostgREST backend to the response's HTTP status.
    """

    def __init__(self, *, table: str, detail: str, status_code: int | None = None) -> None:
        self.table = table
        self.detail = detail
        self.status_code = status_code
        where = f"table={table!r}"
        if status_code is not None:
            where += f" status={status_code}"
        super().__init__(f"supabase request failed ({where}): {detail}")


class SupabaseClient(ABC):
    """Async CRUD + storage-upload surface every caller in this repo uses.

    Deliberately narrow: this is the "thin async Python client" the module
    brief asks for, not a general PostgREST/Supabase SDK. It covers batch
    insert (the write path every module actually needs, since world/ and
    broker/ write in ticks' worth of rows at a time, not one at a time),
    filtered select, update (for the STATE tables that change in place -
    see app/supabase/migrations' table-header comments for which tables
    are STATE versus LOG), and the one Storage operation the
    payload-archiver needs.
    """

    @abstractmethod
    async def insert(self, table: str, rows: list[Row]) -> list[Row]:
        """Batch insert. One HTTP round-trip (or one transaction) for the
        whole list, not one per row - this is what "batch insert" means in
        the module brief. Returns the inserted rows as the database wrote
        them (defaults, generated columns, and all), same as PostgREST's
        `Prefer: return=representation`. Empty input returns an empty list
        without a round-trip.
        """
        raise NotImplementedError

    @abstractmethod
    async def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: dict[str, str] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[Row]:
        """Filtered read. `filters` values use the eq/gt/.../in_ helpers
        below. `order` follows PostgREST's own "column.asc"/"column.desc"
        format (a bare column name means ascending).
        """
        raise NotImplementedError

    @abstractmethod
    async def update(self, table: str, *, filters: dict[str, str], values: Row) -> list[Row]:
        """Update every row matching `filters`, setting `values`. Returns
        the updated rows. Reserved for STATE tables (agents, businesses,
        ...); LOG tables (events, llm_calls, ...) are append-only by
        design and callers should not update them, though this method does
        not itself enforce that - see app/CONVENTIONS.md's module
        boundaries for who is allowed to write at all.
        """
        raise NotImplementedError

    @abstractmethod
    async def upload_archive(
        self, *, bucket: str, path: str, data: bytes, content_type: str = "application/gzip"
    ) -> str:
        """Upload already-compressed bytes to Storage (or the local-dev
        filesystem stand-in). Returns a bucket-relative path
        ("bucket/path") stable across both backends, suitable for storing
        directly in a column like llm_calls.payload_archive_path. Callers
        go through archiver.PayloadArchiver rather than calling this
        directly; it exists on the interface because the archiver is
        backend-agnostic and needs somewhere to send bytes.
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Release the backend's connection (httpx.AsyncClient or the
        sqlite3 connection). Safe to call once at shutdown; not required
        between individual calls.
        """
        raise NotImplementedError

    async def __aenter__(self) -> SupabaseClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()


# ── PostgREST filter helpers ─────────────────────────────────────────
# Every function returns the "<op>.<operand>" string PostgREST expects as
# a query parameter value; sqlite_client.py's build_where parses the same
# strings. Keep this list to the operators both backends actually
# implement (see sqlite_client.py's OP_TO_SQL) - adding a helper here
# without a matching sqlite translation would make that operator silently
# postgrest-only.


def eq(value: Any) -> str:
    return f"eq.{value}"


def neq(value: Any) -> str:
    return f"neq.{value}"


def gt(value: Any) -> str:
    return f"gt.{value}"


def gte(value: Any) -> str:
    return f"gte.{value}"


def lt(value: Any) -> str:
    return f"lt.{value}"


def lte(value: Any) -> str:
    return f"lte.{value}"


def like(pattern: str) -> str:
    return f"like.{pattern}"


def ilike(pattern: str) -> str:
    return f"ilike.{pattern}"


def is_(value: str) -> str:
    """value is one of "null", "true", "false" (PostgREST's IS operand set)."""
    if value not in ("null", "true", "false"):
        raise ValueError(f"is_() only supports 'null', 'true', or 'false', got {value!r}")
    return f"is.{value}"


def in_(values: list[Any]) -> str:
    return "in.(" + ",".join(str(v) for v in values) + ")"
