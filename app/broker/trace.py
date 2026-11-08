"""Full trace capture, buffered and flushed async in batches.

TASK spec: "Full trace capture (request, response, tokens, cost,
latency, purpose) buffered and flushed async in batches." DESIGN.md
section 9: "Batches trace writes async (never blocks inference)" and
lists `llm_calls` among Supabase's tables (section 9's table list);
app/CONVENTIONS.md's module-boundary table assigns broker.* events
(broker.call.completed, broker.call.rejected, broker.rpm.throttled) to
broker/ as their owner, on the `kasukabe:broker` realtime channel.
`llm_calls` itself is notably NOT in app/CONVENTIONS.md's list of tables
world/ exclusively writes - broker writing it directly (not through
world's API) is consistent with that boundary, not a violation of it:
world owns world STATE, broker owns the record of its own Bedrock calls.

BatchWriter is generic over the row type so the same buffering + flush
logic backs both the trace sink (TraceRecord -> llm_calls) and the event
sink (BrokerEvent -> events) without duplicating the batching logic
twice. It starts its own background flush loop lazily, on the first
`record()` call, rather than depending on a FastAPI startup event firing
first - see broker/app.py for why that matters for TestClient.

TRACE-STORE FIX (2026-07-31, post-mortem of the halted genesis run): the
live run produced ~4400 real agent decisions and saved none of them.
Every batch write failed - first "table events has no column named
model", then "datatype mismatch" - because TraceRecord/BrokerEvent below
and supabase/sqlite_schema.sql's `llm_calls`/`events` tables were built
by two tracks that never reconciled: 21 dataclass fields against 13
unrelated columns. supabase/schema_gen.py now generates the SQLite DDL
for both tables directly from these two dataclasses (see its own module
docstring), so this module - not a second, hand-typed copy of its shape
- is the actual, enforced source of truth. Two consequences of that
visible right in this file:

- `tick` is new on both dataclasses, and Optional. Every other trace-
  store table buckets rows into a game-day via a `tick` column; broker
  has no access to world's current tick today (broker/ledger.py's real
  HTTP contract, confirmed against world/main.py's live `/ledger/payer`
  and `/ledger/debit` routes, carries no tick field) - so this is left
  honestly unset rather than defaulted to a false 0 that would silently
  mis-bucket every row into "game day zero" forever. Whoever wires
  world's current tick through (one line in each of world/main.py's two
  `/ledger/*` routes: add `"tick": server.world.tick()`/
  `world_state.current_tick` to the response body; one line in
  broker/service.py: read it off the ledger response and pass it into
  `PreparedCall`/`settle()`/`record_rejection()`) can start populating
  it - this dataclass already has somewhere for it to go.
- `success`/`request_body`/`response_body` are named to match this
  file's own fields exactly (the old schema called the boolean
  `succeeded` and had no JSON columns for the request/response bodies at
  all - `dict` was never valid SQLite bind parameter, so even a
  structurally-matching insert would have raised `sqlite3.
  ProgrammingError: Error binding parameter`). See
  supabase/sqlite_client.py's SQLITE_JSON_COLUMNS/SQLITE_BOOL_COLUMNS,
  updated alongside this fix.

BatchWriter itself also now counts failures instead of only logging them
(TASK spec: "a failed batch write currently vanishes into a log line
while the sim keeps spending... losing observations must never again be
survivable"). See `is_halted`/`stats()` below and broker/app.py's
`GET /admin/trace` + its pre-dispatch halt check on `POST /v1/messages`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

import httpx

logger = logging.getLogger("broker.trace")

T = TypeVar("T")


@dataclass(frozen=True)
class TraceRecord:
    """One row for Supabase's `llm_calls` table - request, response,
    tokens, cost, latency, purpose, exactly as the TASK spec lists them.

    `model` is the lane's model that actually SERVED the call (what got
    billed); `requested_model` is whatever the client's own `model` field
    asked for; `region` is the serving lane's Bedrock region (None when a
    call was rejected before any lane was selected - see
    broker/service.py's record_rejection); `call_class` is
    broker/lanes.py's CallClass ("cognition"/"background") the purpose
    header classified to. TASK spec (broker-lanes track): "the trace MUST
    record which lane+model actually served each call (research validity
    depends on it)" - `model` + `region` together ARE that lane
    identifier (broker/lanes.py's `lane_id()` is exactly
    f"{model}@{region}"), and `requested_model` alongside `model` is what
    makes a cognition-overflowed-to-DeepSeek call a queryable signal
    instead of a silent substitution (see broker/lanes.py's module
    docstring).
    """

    id: str
    agent_id: str
    payer_agent_id: str
    model: str
    requested_model: str
    region: "str | None"
    call_class: str
    purpose: str
    request_body: dict[str, Any]
    response_body: "dict[str, Any] | None"
    stop_reason: "str | None"
    input_tokens: int
    output_tokens: int
    cost_nano_dollars: int
    cents_debited: int
    latency_ms: int
    rate_limit_wait_ms: int
    success: bool
    error_type: "str | None"
    error_message: "str | None"
    created_at: float
    purpose_source: "str | None" = None
    tick: "int | None" = None
    failed_at: "str | None" = None
    task_label: "str | None" = None


@dataclass(frozen=True)
class BrokerEvent:
    """One row for Supabase's `events` table, `kasukabe:broker` channel.
    `type` is one of app/CONVENTIONS.md's broker.* event names."""

    id: str
    type: str
    agent_id: str
    model: str
    payload: dict[str, Any]
    created_at: float
    tick: "int | None" = None


def new_trace_id() -> str:
    return f"trc_{uuid.uuid4().hex}"


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


class BatchSink(Protocol, Generic[T]):
    async def write_batch(self, rows: list[T]) -> None:
        """Persist a batch. Must not raise for a partial/retryable
        failure it can't fully recover from - BatchWriter treats any
        exception here as "these rows are lost", logs it, and moves on
        (DESIGN.md: trace writes must never block inference; a sink that
        raises would only be caught and logged one layer up anyway, so
        sinks with their own retry policy should implement it
        internally)."""
        ...


class InMemorySink(Generic[T]):
    """Test double: every batch that reaches write_batch is appended to
    `.batches`; `.rows` flattens all of them for assertions."""

    def __init__(self) -> None:
        self.batches: list[list[T]] = []

    async def write_batch(self, rows: list[T]) -> None:
        self.batches.append(list(rows))

    @property
    def rows(self) -> list[T]:
        return [row for batch in self.batches for row in batch]


class TraceStoreVanishedError(RuntimeError):
    """The SQLite file this sink was opened against is gone or has been
    replaced. Deliberately not a BrokerError: nothing about a request is
    wrong, and this is raised on a background flush, not in the request
    path. BatchWriter treats it as any other sink failure, which is what
    escalates it to a dispatch-stopping halt."""


class SqliteTraceSink(Generic[T]):
    """Real sink for the genesis run: batched INSERT into the SQLite trace
    store that replaced Supabase as the system of record.

    Same BatchSink shape as SupabaseRestSink, so BatchWriter is unchanged.
    Takes an already-constructed SqliteClient rather than a path so the
    broker shares one WAL connection instead of opening a second writer
    against the same file.
    """

    def __init__(
        self,
        client: Any,
        table: str,
        to_row: Callable[[T], dict[str, Any]],
        archiver: Any = None,
    ) -> None:
        self.client = client
        self.table = table
        self.to_row = to_row
        self.archiver = archiver
        path = getattr(getattr(client, "config", None), "sqlite_path", None)
        self.store_path: str | None = None if path in (None, ":memory:") else str(path)
        self.store_identity: tuple[int, int] | None = self.file_identity()

    def file_identity(self) -> tuple[int, int] | None:
        """(device, inode) of the configured path right now, or None when
        there is nothing to identify (`:memory:`, or a path that is not
        there yet)."""
        if self.store_path is None:
            return None
        try:
            stat = os.stat(self.store_path)
        except OSError:
            return None
        return (stat.st_dev, stat.st_ino)

    def check_store_still_exists(self) -> None:
        """Refuse to write into a store that has been unlinked or swapped.

        SQLite cannot detect this and is not wrong not to: an open file
        descriptor keeps working after its directory entry is removed, so
        every INSERT commits, every transaction succeeds, and the rows
        land in a file that no longer has a name. On 31 Aug 2026 a broker
        left running across a `rm data/kasukabe.db` recorded 259 clean
        batches - $4.01 of real inference - into exactly such a file,
        while GET /admin/trace reported `batches_failed: 0, halted:
        false`. The loss was total and the health surface was green.

        Comparing inodes rather than existence is the point. The path
        existed again within seconds, because the world server recreated
        it; a check for a missing file would have passed while the rows
        continued going to the old inode.

        Raising here is all that is needed - BatchWriter counts the
        failure, halts after three, and app.py refuses dispatch while
        halted, so silent loss becomes a stopped sim.
        """
        if self.store_identity is None:
            return
        current = self.file_identity()
        if current is None:
            raise TraceStoreVanishedError(
                f"trace store {self.store_path!r} is no longer on disk - it was "
                f"deleted while this broker held it open, so every write since "
                f"has gone into an unlinked file. Restart the broker to reopen "
                f"the store."
            )
        if current != self.store_identity:
            raise TraceStoreVanishedError(
                f"trace store {self.store_path!r} is no longer the file this "
                f"broker opened (inode {self.store_identity[1]} -> {current[1]}): "
                f"it was replaced while the broker was running, and writes are "
                f"still going to the old one. Restart the broker to reopen the "
                f"store."
            )

    async def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                await result

    async def write_batch(self, rows: list[T]) -> None:
        if not rows:
            return
        self.check_store_still_exists()
        prepared = [self.to_row(row) for row in rows]
        if self.archiver is not None:
            for record, row in zip(rows, prepared):
                await self.archive_if_truncated(record, row)
        await self.client.insert(self.table, prepared)

    async def archive_if_truncated(self, record: Any, row: dict[str, Any]) -> None:
        """Store the full body and note where it went. Never raises.

        INFRA-08. Only for rows the cap actually bit: a body that fits is
        already whole in the row, and archiving it twice would double the
        storage for no reader's benefit.

        Best-effort by construction. An archive that fails must not lose
        the trace row - the row is the record, the archive is the appendix
        - so a failure is logged and the row is written with no key,
        which reads as "not archived" rather than as "archived somewhere
        you cannot find".
        """
        for field, column in (("request_body", "request_archive_key"),
                              ("response_body", "response_archive_key")):
            full = getattr(record, field, None)
            if not full:
                continue
            stored = row.get(field)
            if stored is not None and sized(stored) >= sized(full):
                continue
            key = (
                f"{getattr(record, 'agent_id', 'unknown')}/"
                f"{getattr(record, 'id', 'unknown')}.{field}.json.gz"
            )
            try:
                result = await self.archiver.archive_payload(key=key, payload=full)
                row[column] = getattr(result, "path", None) or key
            except Exception:  # noqa: BLE001 - the row matters more
                logger.exception("broker.trace: could not archive %s for %s", field, key)


DEFAULT_HALT_AFTER_CONSECUTIVE_FAILURES = 3


@dataclass
class BatchWriter(Generic[T]):
    """Buffers `record()`d items and flushes them to `sink` either when
    the buffer reaches `batch_size` or every `flush_interval_s`,
    whichever comes first. Never blocks the caller: `record()` is
    synchronous and only ever schedules background work.

    Also counts every flush attempt and failure (TASK spec: "a failed
    batch write currently vanishes into a log line while the sim keeps
    spending ... losing observations must never again be survivable").
    `is_halted` flips true once `consecutive_failures` reaches
    `halt_after_consecutive_failures` and flips back false on the next
    flush that actually succeeds - it does not itself stop anything
    (this class still accepts record()s and still tries to flush; a
    down sink recovering must be able to drain the backlog). Enforcement
    lives in broker/app.py's request handler, which checks `is_halted`
    on both writers BEFORE calling into BrokerService at all, so a
    persistently-failing sink stops new inference (and therefore new
    spend) from ever being dispatched un-recorded - see app.py's
    `trace_writers_halt_error`.
    """

    sink: BatchSink[T]
    batch_size: int = 20
    flush_interval_s: float = 2.0
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    halt_after_consecutive_failures: int = DEFAULT_HALT_AFTER_CONSECUTIVE_FAILURES

    buffer: list[T] = field(default_factory=list, init=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    background_task: "asyncio.Task[None] | None" = field(default=None, init=False)
    closed: bool = field(default=False, init=False)

    batches_attempted: int = field(default=0, init=False)
    batches_failed: int = field(default=0, init=False)
    rows_dropped: int = field(default=0, init=False)
    consecutive_failures: int = field(default=0, init=False)
    last_failure_message: "str | None" = field(default=None, init=False)
    last_failure_at: "float | None" = field(default=None, init=False)

    @property
    def is_halted(self) -> bool:
        return self.consecutive_failures >= self.halt_after_consecutive_failures

    def stats(self) -> dict[str, object]:
        """Everything GET /admin/trace reports for this writer."""
        return {
            "batches_attempted": self.batches_attempted,
            "batches_failed": self.batches_failed,
            "rows_dropped": self.rows_dropped,
            "consecutive_failures": self.consecutive_failures,
            "halt_after_consecutive_failures": self.halt_after_consecutive_failures,
            "halted": self.is_halted,
            "last_failure_message": self.last_failure_message,
            "last_failure_at": self.last_failure_at,
        }

    def record(self, item: T) -> None:
        """Append an item and, if the buffer is now full, schedule an
        immediate flush. Starts the periodic background flusher on first
        use rather than at construction time - see module docstring."""
        if self.closed:
            logger.warning("broker.trace: record() called after close(), dropping item")
            return
        self.buffer.append(item)
        if self.background_task is None:
            self.background_task = asyncio.ensure_future(self.run_periodic_flush())
        if len(self.buffer) >= self.batch_size:
            asyncio.ensure_future(self.flush())

    async def flush(self) -> None:
        async with self.lock:
            if not self.buffer:
                return
            batch, self.buffer = self.buffer, []
        self.batches_attempted += 1
        try:
            await self.sink.write_batch(batch)
        except Exception as exc:
            self.batches_failed += 1
            self.rows_dropped += len(batch)
            self.consecutive_failures += 1
            self.last_failure_message = str(exc)
            self.last_failure_at = now_s()
            logger.exception(
                "broker.trace: failed to flush a batch of %d rows (%d consecutive "
                "failure(s), halts dispatch at %d - see GET /admin/trace)",
                len(batch),
                self.consecutive_failures,
                self.halt_after_consecutive_failures,
            )
        else:
            self.consecutive_failures = 0

    async def run_periodic_flush(self) -> None:
        while not self.closed:
            await self.sleep(self.flush_interval_s)
            await self.flush()

    async def close(self) -> None:
        """Graceful shutdown: stop accepting new items, flush whatever's
        buffered, cancel the background loop. Safe to call multiple
        times."""
        self.closed = True
        await self.flush()
        if self.background_task is not None:
            self.background_task.cancel()
            try:
                await self.background_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("broker.trace: background flush task raised on shutdown")
            self.background_task = None


TRACE_BODY_LIMIT = int(os.environ.get("KASUKABE_TRACE_BODY_LIMIT", str(64 * 1024)))


CAPPED_MESSAGES_KEPT = 6

CAPPED_FIELD_CHARS = 8_000


def sized(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def capped_body(
    body: "dict[str, Any] | None", limit: int | None = None
) -> "dict[str, Any] | None":
    """The body as stored: whole if it fits, a smaller VALID OBJECT if not.

    Measured, data/loop5.db: the previous version stored 342 of 566
    successful calls as `_head`, a raw 64 KB prefix of the JSON cut mid
    token. That is a string, not JSON, so no reader could parse six calls
    in ten - and they were the six that mattered, being the big ones. It
    was announced truncation, which was right, in a shape nothing could
    use, which was not.

    What survives is what gets asked of this store: the model, the
    ceiling, the system prompt, which tools were offered, and the newest
    messages. What goes is the middle of the conversation, replaced by one
    marker saying how many messages went and how large they were - counted,
    never silently gone.

    The cap itself stays. An uncapped trace once filled the disk and took
    the run down with it, losing the record it existed to keep. What is
    no longer true is the claim beside it that "64 KB holds every honest
    prompt this system produces with room to spare": the median request in
    that same run is 76 KB and the largest is 154 KB.

    KASUKABE_TRACE_BODY_LIMIT=0 disables the cap for a deliberate
    deep-trace run on a machine with the disk to afford it.
    """
    ceiling = TRACE_BODY_LIMIT if limit is None else limit
    if body is None or ceiling <= 0:
        return body
    original = sized(body)
    if original <= ceiling:
        return body

    kept: dict[str, Any] = {
        "_truncated": True,
        "_original_bytes": original,
        "_limit_bytes": ceiling,
        "_why": "trace body cap - see broker/trace.py's capped_body",
    }
    messages = body.get("messages")
    for key, value in body.items():
        if key == "messages":
            continue
        if key == "tools" and isinstance(value, list):
            kept["tools"] = [
                {"name": tool.get("name")} if isinstance(tool, dict) else tool
                for tool in value
            ]
            continue
        kept[key] = value

    if isinstance(messages, list):
        dropped = messages[:-CAPPED_MESSAGES_KEPT] if len(messages) > CAPPED_MESSAGES_KEPT else []
        tail = messages[-CAPPED_MESSAGES_KEPT:] if dropped else list(messages)
        kept["messages"] = (
            [{"_dropped": len(dropped), "_dropped_bytes": sized(dropped)}] + tail
            if dropped
            else tail
        )

    while sized(kept) > ceiling:
        biggest = max(
            (
                (key, value)
                for key, value in kept.items()
                if not key.startswith("_") and sized(value) > CAPPED_FIELD_CHARS
            ),
            key=lambda item: sized(item[1]),
            default=None,
        )
        if biggest is None:
            break
        key, value = biggest
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        kept[key] = text[:CAPPED_FIELD_CHARS]
        kept[f"_{key}_original_bytes"] = len(text)

    return kept


def trace_record_to_row(record: TraceRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "agent_id": record.agent_id,
        "payer_agent_id": record.payer_agent_id,
        "model": record.model,
        "requested_model": record.requested_model,
        "region": record.region,
        "call_class": record.call_class,
        "purpose": record.purpose,
        "purpose_source": record.purpose_source,
        "request_body": capped_body(record.request_body),
        "response_body": capped_body(record.response_body),
        "request_archive_key": None,
        "response_archive_key": None,
        "stop_reason": record.stop_reason,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "cost_nano_dollars": record.cost_nano_dollars,
        "cents_debited": record.cents_debited,
        "latency_ms": record.latency_ms,
        "rate_limit_wait_ms": record.rate_limit_wait_ms,
        "success": record.success,
        "error_type": record.error_type,
        "error_message": record.error_message,
        "created_at": record.created_at,
        "tick": record.tick,
        "failed_at": record.failed_at,
        "task_label": record.task_label,
    }


def broker_event_to_row(event: BrokerEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.type,
        "agent_id": event.agent_id,
        "model": event.model,
        "payload": event.payload,
        "created_at": event.created_at,
        "tick": event.tick,
    }


def now_s() -> float:
    return time.time()
