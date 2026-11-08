"""Event retention job for the observability subsystem.

Rolls up old raw events into daily counters, then drops the raw rows.
Drops counters beyond a separate TTL. Designed to run once at boot and
then periodically (default: every 24 hours) via an asyncio background
task scheduled by ``bootstrap.py``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.raven.store import RavenStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetentionPolicy:
    """Configurable retention windows."""

    raw_events_ttl_days: int = 30
    counters_ttl_days: int = 365


@dataclass
class RetentionResult:
    """Outcome of a single retention run."""

    rollup_count: int = 0
    drop_count: int = 0
    counters_dropped: int = 0
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "rollup_count": self.rollup_count,
            "drop_count": self.drop_count,
            "counters_dropped": self.counters_dropped,
            "duration_ms": self.duration_ms,
        }


async def run_retention(
    store: RavenStore,
    policy: RetentionPolicy | None = None,
) -> RetentionResult:
    """Execute one retention pass. Idempotent and safe to call repeatedly."""
    if policy is None:
        policy = RetentionPolicy()

    t0 = time.monotonic()
    now = time.time()

    raw_cutoff = now - (policy.raw_events_ttl_days * 86400)
    counters_cutoff_day = time.strftime(
        "%Y-%m-%d",
        time.gmtime(now - (policy.counters_ttl_days * 86400)),
    )

    rollup_count = await store.rollup_events_to_counters(before=raw_cutoff)
    drop_count = await store.drop_events_before(cutoff=raw_cutoff)
    counters_dropped = await store.drop_counters_before(cutoff_day=counters_cutoff_day)

    duration_ms = (time.monotonic() - t0) * 1000

    result = RetentionResult(
        rollup_count=rollup_count,
        drop_count=drop_count,
        counters_dropped=counters_dropped,
        duration_ms=duration_ms,
    )

    logger.info(
        "Retention complete: rollup=%d drop=%d counters_dropped=%d duration=%.1fms",
        rollup_count,
        drop_count,
        counters_dropped,
        duration_ms,
    )
    return result


async def retention_loop(
    store: RavenStore,
    policy: RetentionPolicy | None = None,
    interval_seconds: float = 86400,
) -> None:
    """Background loop that runs retention periodically. First run at boot."""
    while True:
        try:
            await run_retention(store, policy)
        except Exception:
            logger.exception("Retention run failed")
        await asyncio.sleep(interval_seconds)
