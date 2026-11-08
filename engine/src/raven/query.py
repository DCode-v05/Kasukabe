"""Aggregation query DSL for the observability subsystem.

Provides ``query()`` which aggregates ``obs_events`` and
``obs_counters_daily`` into rollups by metric, time window, and
optional group-by dimensions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.infra.sqlite_pool import SqlitePool


WINDOW_DURATIONS: dict[str, float] = {
    "1h": 3600,
    "5h": 18000,
    "1d": 86400,
    "7d": 604800,
    "30d": 2592000,
}


@dataclass
class Rollup:
    """Result of an aggregation query."""

    metric: str
    window_start: float
    window_end: float
    total: int = 0
    groups: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "metric": self.metric,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "total": self.total,
        }
        if self.groups:
            d["groups"] = self.groups
        return d


# The daily-counter table carries exactly one dimension besides the metric
# (see migrations/013: day, metric, model, value). A day-granularity query can
# therefore honour a filter on "model" and nothing else - the other dimensions
# were aggregated away when the counter row was written.
COUNTER_FILTER_KEYS: frozenset[str] = frozenset({"model"})


def filterable_keys(metric: str) -> set[str]:
    """Payload fields a sub-day query can filter on, for this metric.

    Derived from the event dataclass registered for the metric's kind, so the
    set tracks the schema instead of drifting from it.
    """
    from dataclasses import fields as dataclass_fields

    from src.raven.events import KIND_MAP

    kind = metric.replace("tokens_used", "token_usage")
    cls = KIND_MAP.get(kind)
    if cls is None:
        return set()
    return {f.name for f in dataclass_fields(cls)} - {"kind", "timestamp"}


def check_filters(filters: dict | None, allowed: set[str] | frozenset[str], where: str) -> None:
    """Reject a filter the query cannot honour.

    Silently ignoring an unsupported filter is the one failure mode a caller
    cannot detect: the response is a well-formed 200 whose ``total`` is the
    unfiltered figure, so a budget alert attributes the whole fleet's spend to
    the single model it filtered for. ValueError is turned into an
    INVALID_PARAMS 4xx by the route handler.
    """
    if not filters:
        return
    unsupported = sorted(set(filters) - set(allowed))
    if unsupported:
        raise ValueError(
            f"unsupported filter {unsupported} for {where}; "
            f"supported: {sorted(allowed) or 'none'}"
        )


def payload_matches(data: dict, filters: dict | None) -> bool:
    """True when every filter key equals its value in the event payload.

    Compared as strings so a caller filtering ``{"success": "true"}`` over the
    wire matches a payload that stored a JSON boolean.
    """
    if not filters:
        return True
    for key, want in filters.items():
        if str(data.get(key, "")) != str(want):
            return False
    return True


def resolve_window(window: str | dict) -> tuple[float, float]:
    """Convert a window spec to (start, end) timestamps."""
    now = time.time()
    if isinstance(window, str):
        duration = WINDOW_DURATIONS.get(window)
        if duration is None:
            raise ValueError(f"unknown window shorthand: {window}")
        return now - duration, now
    if isinstance(window, dict):
        return float(window.get("since", 0)), float(window.get("until", now))
    raise ValueError(f"invalid window type: {type(window)}")


async def query(
    db: SqlitePool,
    metric: str,
    window: str | dict,
    group_by: list[str] | None = None,
    filters: dict | None = None,
) -> Rollup:
    """Run an aggregation query.

    For sub-day windows (< 86400s): scans ``obs_events`` by exact timestamp
    so intra-day filtering is accurate. For day-or-larger windows: uses
    ``obs_counters_daily`` for speed.

    ``filters`` narrows the aggregation to matching events. What can be
    filtered depends on which store answers the window: the raw event log
    carries the whole payload, the daily counters carry only ``model``. A
    filter the chosen store cannot honour raises ValueError rather than being
    dropped, because a dropped filter returns the unfiltered total under a 200.
    """
    start, end = resolve_window(window)
    rollup = Rollup(metric=metric, window_start=start, window_end=end)
    use_exact = (end - start) < 86400

    if use_exact:
        check_filters(filters, filterable_keys(metric), f"metric {metric!r}")
        rollup.total, rollup.groups = await query_exact(db, metric, start, end, group_by, filters)
    else:
        check_filters(
            filters,
            COUNTER_FILTER_KEYS,
            "windows of a day or more (they read day-granularity counters; "
            "use a sub-day window for payload filters)",
        )
        start_day = time.strftime("%Y-%m-%d", time.gmtime(start))
        end_day = time.strftime("%Y-%m-%d", time.gmtime(end))
        rollup.total, rollup.groups = await query_counters(
            db, metric, start_day, end_day, group_by, filters
        )

    return rollup


async def query_exact(
    db: SqlitePool,
    metric: str,
    start: float,
    end: float,
    group_by: list[str] | None,
    filters: dict | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Scan obs_events by exact timestamp for sub-day accuracy.

    ``filters`` are matched against the event payload; non-matching events are
    excluded from both the total and the groups.
    """
    import json
    from collections import defaultdict

    # token_usage events store their metric in the kind column
    kind = metric.replace("tokens_used", "token_usage")
    rows = await db.fetch(
        "SELECT payload FROM obs_events WHERE kind=? AND timestamp >= ? AND timestamp <= ?",
        (kind, start, end),
    )

    if group_by:
        buckets: dict[str, int] = defaultdict(int)
        for row in rows:
            data = json.loads(row["payload"])
            if not payload_matches(data, filters):
                continue
            total = data.get("input_tokens", 0) + data.get("output_tokens", 0)
            key = "|".join(str(data.get(g, "")) for g in group_by)
            buckets[key] += total
        groups = []
        grand_total = 0
        for key, total in buckets.items():
            parts = key.split("|")
            entry: dict[str, Any] = {g: parts[i] for i, g in enumerate(group_by)}
            entry["total"] = total
            groups.append(entry)
            grand_total += total
        return grand_total, groups
    else:
        grand_total = 0
        for row in rows:
            data = json.loads(row["payload"])
            if not payload_matches(data, filters):
                continue
            grand_total += data.get("input_tokens", 0) + data.get("output_tokens", 0)
        return grand_total, []


async def query_counters(
    db: SqlitePool,
    metric: str,
    start_day: str,
    end_day: str,
    group_by: list[str] | None,
    filters: dict | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Use obs_counters_daily for fast day-granularity rollups.

    Only ``model`` can be filtered here; the caller (``query``) has already
    rejected anything else, so an unhandled key cannot reach this SQL.
    """
    predicate = ""
    extra: tuple = ()
    if filters and "model" in filters:
        predicate = " AND model = ?"
        extra = (str(filters["model"]),)

    if group_by and group_by == ["model"]:
        rows = await db.fetch(
            "SELECT model, SUM(value) as total FROM obs_counters_daily "
            f"WHERE metric=? AND day >= ? AND day <= ?{predicate} GROUP BY model",
            (metric, start_day, end_day) + extra,
        )
        groups = []
        grand_total = 0
        for row in rows:
            groups.append({"model": row["model"], "total": row["total"]})
            grand_total += row["total"]
        return grand_total, groups
    else:
        total_row = await db.fetchrow(
            "SELECT SUM(value) as total FROM obs_counters_daily "
            f"WHERE metric=? AND day >= ? AND day <= ?{predicate}",
            (metric, start_day, end_day) + extra,
        )
        total = total_row["total"] if total_row and total_row["total"] else 0
        return total, []
