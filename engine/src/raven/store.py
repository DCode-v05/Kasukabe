"""SQLite persistence layer for the observability subsystem.

Reads and writes ``obs_events`` and ``obs_counters_daily`` tables in
``brain.sqlite``. All SQL uses neutral table prefixes — no internal
codenames appear in any query string that could surface via error paths.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from src.raven.events import (
    Event,
    TokenUsageEvent,
    event_from_row,
    event_to_payload,
)

if TYPE_CHECKING:
    from src.infra.sqlite_pool import SqlitePool

logger = logging.getLogger(__name__)


class RavenStore:
    """Persistence backend for the observability subsystem."""

    def __init__(self, db: SqlitePool) -> None:
        self.db = db

    async def track(self, event: Event) -> None:
        """Insert an event into ``obs_events`` and update daily counters."""
        kind = event.kind
        ts = event.timestamp
        payload = event_to_payload(event)
        thread_id = getattr(event, "agent_step_id", "") or ""
        step_id = getattr(event, "step_id", "") or ""

        await self.db.execute(
            "INSERT INTO obs_events (timestamp, kind, payload, thread_id, agent_step_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, kind, payload, thread_id, step_id),
        )

        if isinstance(event, TokenUsageEvent) and event.model:
            day = time.strftime("%Y-%m-%d", time.gmtime(ts))
            total = event.input_tokens + event.output_tokens
            await self.db.execute(
                "INSERT INTO obs_counters_daily (day, metric, model, value) "
                "VALUES (?, 'tokens_used', ?, ?) "
                "ON CONFLICT(day, metric, model) DO UPDATE SET value = value + excluded.value",
                (day, event.model, total),
            )

        await self.db.commit()

    async def events_page(
        self,
        since: float = 0.0,
        kind: str | None = None,
        limit: int = 100,
    ) -> tuple[list[Event], bool]:
        """Read one page of raw events, oldest first, plus whether more remain.

        Ordering is ASCENDING and that is the whole point. ``since`` is a
        forward-only lower bound, so with DESC the LIMIT discarded the events
        CLOSEST to the cursor and kept the newest: a consumer polling
        incrementally would advance ``since`` past a window it had never been
        given, and no request could ever ask for it again. During a burst - a
        fanned-out turn emitting a ToolCallEvent per tool call and a
        TokenUsageEvent per LLM call - a mirrored audit trail lost everything
        between the old cursor and the start of the returned page, permanently.

        Ascending makes a truncated read DELAYED rather than lossy: the caller
        gets the oldest events, advances its cursor to the newest it received,
        and picks the rest up on the next poll. Matches the convention in
        AssetDirectory.query_executions.

        One extra row is fetched to distinguish "exactly ``limit`` events
        existed" from "there are more", which a full page alone cannot say.
        """
        probe = limit + 1
        if kind:
            rows = await self.db.fetch(
                "SELECT kind, timestamp, payload FROM obs_events "
                "WHERE timestamp > ? AND kind = ? ORDER BY timestamp ASC LIMIT ?",
                (since, kind, probe),
            )
        else:
            rows = await self.db.fetch(
                "SELECT kind, timestamp, payload FROM obs_events "
                "WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?",
                (since, probe),
            )
        has_more = len(rows) > limit
        rows = rows[:limit]
        return (
            [event_from_row(r["kind"], r["timestamp"], r["payload"]) for r in rows],
            has_more,
        )

    async def events(
        self,
        since: float = 0.0,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Read raw events from ``obs_events``, oldest first."""
        evts, _ = await self.events_page(since=since, kind=kind, limit=limit)
        return evts

    async def counter(
        self,
        metric: str,
        day: str,
        model: str | None = None,
    ) -> int:
        """Read a single daily counter value."""
        if model:
            row = await self.db.fetchrow(
                "SELECT value FROM obs_counters_daily WHERE day=? AND metric=? AND model=?",
                (day, metric, model),
            )
        else:
            row = await self.db.fetchrow(
                "SELECT SUM(value) as value FROM obs_counters_daily WHERE day=? AND metric=?",
                (day, metric),
            )
        return row["value"] if row and row["value"] else 0

    async def drop_events_before(self, cutoff: float) -> int:
        """Delete raw events older than ``cutoff``. Returns count dropped."""
        cursor = await self.db.execute("DELETE FROM obs_events WHERE timestamp < ?", (cutoff,))
        await self.db.commit()
        return cursor.rowcount

    async def rollup_events_to_counters(self, before: float) -> int:
        """Count token_usage events older than ``before`` for retention reporting.

        Does NOT re-aggregate into counters because ``track()`` already
        writes to ``obs_counters_daily`` at insert time. This method
        exists so the retention job can report how many events will be
        dropped without double-counting.
        """
        row = await self.db.fetchrow(
            "SELECT COUNT(*) as cnt FROM obs_events WHERE kind='token_usage' AND timestamp < ?",
            (before,),
        )
        return row["cnt"] if row and row["cnt"] else 0

    async def drop_counters_before(self, cutoff_day: str) -> int:
        """Delete daily counters older than ``cutoff_day`` (YYYY-MM-DD). Returns count."""
        cursor = await self.db.execute(
            "DELETE FROM obs_counters_daily WHERE day < ?", (cutoff_day,)
        )
        await self.db.commit()
        return cursor.rowcount
