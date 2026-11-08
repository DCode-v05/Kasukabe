"""Public API for the observability subsystem.

Engine-internal code calls these functions directly as Python imports;
the typed ``/raven/*`` routes call them too.

The singleton ``store`` is initialized by ``init(db)`` at boot time
(called from ``bootstrap.py``). Until init runs, operations that need
the store raise RuntimeError.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.raven.events import (
    Event,
    UntypedEvent,
    TokenUsageEvent,
)
from src.raven.retention import RetentionPolicy, retention_loop, run_retention

if TYPE_CHECKING:
    import asyncio

    from src.infra.sqlite_pool import SqlitePool
    from src.raven.store import RavenStore

store: RavenStore | None = None
db_pool: SqlitePool | None = None
retention_task: "asyncio.Task | None" = None


def fire_and_forget(event: Event) -> None:
    """Schedule an event track as a fire-and-forget async task.

    Safe to call from sync or async contexts. Does nothing if the
    store is not initialized or no event loop is running.
    """
    import asyncio

    if store is None:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(store.track(event))
    except RuntimeError:
        pass


def init(db: SqlitePool) -> None:
    """Initialize the observability subsystem with a database connection.

    Also registers a usage hook so every LLM call automatically emits a
    TokenUsageEvent without requiring per-provider instrumentation code.
    """
    global store, db_pool
    from src.raven.store import RavenStore

    store = RavenStore(db)
    db_pool = db

    from src.herald.usage_tracker import add_on_usage_hook
    from src.herald.types import Usage

    async def on_usage(usage: Usage, model_id: str) -> None:
        if store is None:
            return
        evt = TokenUsageEvent(
            model=model_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens,
            cache_creation_tokens=usage.cache_creation_input_tokens,
        )
        await store.track(evt)

        try:
            from src.herald import herald_api

            ct = herald_api.get_cache_tracker()
            if ct is not None:
                await ct.record_inference(model_id, usage)
        except Exception:
            pass

    add_on_usage_hook(on_usage)


def start_retention(interval_seconds: float = 86400) -> None:
    """Start the periodic retention loop (rolls up + prunes old raw events).

    Idempotent; a no-op if already started or the store is not initialized.
    Mirrors the start/stop lifecycle of the other boot-time background loops.
    """
    global retention_task
    import asyncio

    if retention_task is not None or store is None:
        return
    retention_task = asyncio.get_running_loop().create_task(
        retention_loop(store, interval_seconds=interval_seconds)
    )


async def stop_retention() -> None:
    """Cancel the retention loop. Registered as a boot cleanup."""
    global retention_task
    import asyncio

    if retention_task is not None:
        retention_task.cancel()
        try:
            await retention_task
        except asyncio.CancelledError:
            pass
        retention_task = None


def is_ready() -> bool:
    """True if ``init(db)`` has been called and the store is live."""
    return store is not None


def require_store() -> RavenStore:
    if store is None:
        raise RuntimeError("observability subsystem not initialized")
    return store


async def track(event: dict | Event) -> None:
    """Track a typed event. Accepts an Event dataclass or a dict with 'kind' key."""
    store = require_store()
    if isinstance(event, dict):
        from src.raven.events import event_from_row
        import time

        kind = event.get("kind", "unknown")
        ts = event.get("timestamp", time.time())
        import json

        payload = json.dumps({k: v for k, v in event.items() if k not in ("kind", "timestamp")})
        evt = event_from_row(kind, ts, payload)
    else:
        evt = event
    await store.track(evt)


async def track_untyped(name: str, metadata: dict | None = None) -> None:
    """One-liner escape hatch for ad-hoc telemetry."""
    store = require_store()
    evt = UntypedEvent(kind=name, metadata=metadata or {})
    await store.track(evt)


async def query(
    metric: str,
    window: str | dict = "5h",
    group_by: list[str] | None = None,
    filters: dict | None = None,
) -> dict:
    """Run an aggregation query over the observability data."""
    from src.raven.query import query

    if db_pool is None:
        raise RuntimeError("Raven not initialized")
    rollup = await query(db_pool, metric=metric, window=window, group_by=group_by, filters=filters)
    return rollup.to_dict()


async def events(
    since: float = 0.0,
    kind: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Read raw events, oldest first."""
    page = await events_page(since=since, kind=kind, limit=limit)
    return page["events"]


async def events_page(
    since: float = 0.0,
    kind: str | None = None,
    limit: int = 100,
) -> dict:
    """Read one page of raw events plus the cursor a poller needs to continue.

    ``next_since`` is the timestamp of the newest event returned (or the
    incoming ``since`` when the page is empty), and ``has_more`` says whether
    the cap cut the page short. Without those two a caller had no way to detect
    that a burst had outrun its poll interval: a full page of exactly ``limit``
    was indistinguishable from "exactly ``limit`` events existed".
    """
    from dataclasses import asdict

    store = require_store()
    evts, has_more = await store.events_page(since=since, kind=kind, limit=limit)
    rows = [asdict(e) for e in evts]
    next_since = evts[-1].timestamp if evts else since
    return {"events": rows, "has_more": has_more, "next_since": next_since}


async def run_retention_now(
    raw_events_ttl_days: int = 30,
    counters_ttl_days: int = 365,
) -> dict:
    """Manual trigger for retention. Returns stats about rollup/drop counts."""
    store = require_store()
    policy = RetentionPolicy(
        raw_events_ttl_days=raw_events_ttl_days,
        counters_ttl_days=counters_ttl_days,
    )
    result = await run_retention(store, policy)
    return result.to_dict()
