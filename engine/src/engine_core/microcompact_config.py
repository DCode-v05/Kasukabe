"""Microcompact configuration and tool eligibility set.

Configuration for the time-based and forced microcompact strategies that live
in ``src/engine_core/time_based_microcompact.py``. The config is persisted in
SQLite (``microcompact_config`` table, migration 011) and loaded once per
session via :meth:`MicrocompactConfig.load`.

The ``COMPACTABLE_TOOLS`` frozenset enumerates the tools whose results are
safe to age-clear. Results from tools outside this set (memory, permission,
agent orchestration) may still be semantically relevant hours later and are
never cleared, regardless of age or pressure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.infra.sqlite_pool import SqlitePool


# Tools whose results become stale as time passes (or as context pressure
# grows). File reads reflect disk state that may have changed, shell output
# is ephemeral, search results change as code changes, web content changes
# upstream. Every tool in this set is safe to replace with the staleness
# marker during a microcompact pass.
#
# Tools NOT in this set (by design):
#   - Memory / working-memory tools   — persistent by intent.
#   - Permission / security tools     — decisions are session-scoped.
#   - Agent / Task orchestration      — orchestration state must survive.
COMPACTABLE_TOOLS: frozenset[str] = frozenset(
    {
        "Read",
        "Bash",
        "Grep",
        "Glob",
        "WebSearch",
        "WebFetch",
        "Edit",
        "Write",
    }
)


@dataclass(frozen=True)
class MicrocompactConfig:
    """SQLite-persisted thresholds for the microcompact strategies.

    Frozen because the config is immutable for the lifetime of a session —
    ``load()`` returns a fully-constructed instance rather than mutating
    fields after the fact, matching the src reference's read-only semantics.

    Attributes:
        enabled: Master kill switch. When False, neither the time-based nor
            the forced strategy runs, regardless of caller.
        gap_threshold_minutes: Idle gap (minutes since the last assistant
            message) that must elapse before the TIME_BASED trigger fires.
            The FORCED trigger ignores this field.
        keep_recent: Number of most-recent compactable tool results to keep
            intact. Every compactable result older than this is replaced
            with the staleness marker. Always floored at 1 at the call site
            so a misconfiguration can never clear every tool result.
    """

    enabled: bool = True
    gap_threshold_minutes: float = 60.0
    keep_recent: int = 5

    @classmethod
    async def load(cls, db: "SqlitePool | None") -> "MicrocompactConfig":
        """Load config from SQLite. Returns defaults if DB is unavailable
        or the ``microcompact_config`` table is empty.

        Malformed rows fall back to the dataclass default for that field
        rather than raising — a broken config row should never crash the
        agent loop.
        """
        if db is None:
            return cls()
        try:
            rows = await db.fetch("SELECT key, value FROM microcompact_config")
        except Exception:
            logger.debug(
                "MicrocompactConfig: SQLite fetch failed, using defaults",
            )
            return cls()

        kv = {row["key"]: row["value"] for row in rows}
        defaults = cls()

        def parse_bool(raw: str | None, fallback: bool) -> bool:
            if raw is None:
                return fallback
            return raw.strip().lower() in ("1", "true", "yes", "on")

        def parse_float(raw: str | None, fallback: float) -> float:
            if raw is None:
                return fallback
            try:
                return float(raw)
            except (TypeError, ValueError):
                logger.debug(
                    "MicrocompactConfig: invalid float %r, using default",
                    raw,
                )
                return fallback

        def parse_int(raw: str | None, fallback: int) -> int:
            if raw is None:
                return fallback
            try:
                return int(raw)
            except (TypeError, ValueError):
                logger.debug(
                    "MicrocompactConfig: invalid int %r, using default",
                    raw,
                )
                return fallback

        return cls(
            enabled=parse_bool(kv.get("enabled"), defaults.enabled),
            gap_threshold_minutes=parse_float(
                kv.get("gap_threshold_minutes"),
                defaults.gap_threshold_minutes,
            ),
            keep_recent=parse_int(
                kv.get("keep_recent"),
                defaults.keep_recent,
            ),
        )
