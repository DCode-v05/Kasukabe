"""Cache hit ratio monitoring and alerting.

Tracks per-turn cache hit ratio and detects prefix instability.
Two alert conditions are supported:

  RATIO_LOW       — cache_ratio < 0.5 after turn 2. Signals that the model is
                    paying for more uncached tokens than cached ones. Expected
                    on the very first turn (no prior cache); becomes a signal
                    worth acting on from turn 2 onward.

  PREFIX_UNSTABLE — cache_read_input_tokens dropped > 10% AND > 2000 tokens
                    from the previous turn. Indicates that the server-side
                    prompt cache prefix changed unexpectedly. Combined with
                    prompt_cache_break_detection (which does full break
                    classification), this provides lightweight operational
                    alerting without requiring the richer hash state that the
                    break detector needs.

                    When the drop is detected, the monitor attributes a likely
                    cause: model change, tool set change, TTL expiry (5min or
                    1h), or unknown. Callers may suppress the next drop via
                    suppress_next_drop() after compaction or cache deletions
                    that legitimately reduce the cached prefix.

Design notes:
  - One monitor per agent_loop invocation (not per session). Reset between
    invocations so the drop calculation is bounded to one conversation turn.
  - Pure computation — no I/O, no logging, no async. Callers log and emit.
  - Drop threshold (10%) is intentionally lower than the break detector's
    50% threshold so minor prefix churn surfaces early.
  - Absolute token floor (2000) prevents false positives from normal variance
    on small token counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CacheAlertType(str, Enum):
    RATIO_LOW = "ratio_low"
    PREFIX_UNSTABLE = "prefix_unstable"


@dataclass
class CacheAlert:
    """One alert emitted by CacheHitMonitor.observe_turn()."""

    alert_type: CacheAlertType
    turn: int
    cache_ratio: float
    cache_read: int
    cache_create: int
    uncached: int
    prev_cache_read: int | None = None
    drop_pct: float | None = None
    message: str = ""


CACHE_TTL_5MIN_MS = 5 * 60 * 1000
CACHE_TTL_1HOUR_MS = 60 * 60 * 1000


class CacheHitMonitor:
    """Per-invocation cache hit ratio tracker.

    Thread.agent_loop creates one per invocation when
    settings.cache_hit_monitor_enabled is True, and calls observe_turn()
    immediately after state.record_turn_usage() on every turn. Each returned
    alert is logged and emitted as a CacheHitRatioAlert event. The loop also
    calls suppress_next_drop() after proactive compaction, microcompact, and
    a reactive compaction tier that made progress, so the engine's own prefix
    edits are not reported as instability.
    """

    DROP_THRESHOLD: float = 0.10

    MIN_CACHE_MISS_TOKENS: int = 2000

    RATIO_THRESHOLD: float = 0.50

    RATIO_MIN_TURN: int = 2

    def __init__(self) -> None:
        self.prev_cache_read: int | None = None
        self.suppress_next: bool = False
        self.prev_model: str = ""
        self.prev_tool_count: int = -1

    def suppress_next_drop(self) -> None:
        """Suppress the next PREFIX_UNSTABLE alert.

        Call after compaction or cache deletion events that legitimately
        reduce the cached prefix. The next observe_turn() will reset the
        baseline without firing PREFIX_UNSTABLE.
        """
        self.suppress_next = True

    def observe_turn(
        self,
        turn: int,
        cache_read: int,
        cache_create: int,
        uncached: int,
        *,
        model: str = "",
        tool_count: int = -1,
        elapsed_ms: int | None = None,
    ) -> list[CacheAlert]:
        """Record one turn's cache usage and return triggered alerts.

        Args:
            turn:         1-based turn number (matches LoopState.turns after
                          record_turn_usage() increments it).
            cache_read:   cache_read_input_tokens from the LLM response Usage.
            cache_create: cache_creation_input_tokens from Usage.
            uncached:     plain input_tokens (not read from cache) from Usage.
            model:        Current model name (for cause attribution).
            tool_count:   Number of tools registered (for cause attribution).
            elapsed_ms:   Milliseconds since the previous turn's LLM response
                          (for TTL classification). None on first turn.

        Returns:
            List of CacheAlert instances. Empty when everything is healthy.
            The agent loop logs each alert and emits it as a CacheHitRatioAlert.
        """
        alerts: list[CacheAlert] = []

        cache_total = cache_read + cache_create + uncached
        cache_ratio = cache_read / cache_total if cache_total > 0 else 0.0

        prev = self.prev_cache_read
        suppressed = self.suppress_next
        if suppressed:
            self.suppress_next = False
        elif prev is not None and prev > 0:
            drop = prev - cache_read
            drop_pct = drop / prev
            if drop_pct > self.DROP_THRESHOLD and drop >= self.MIN_CACHE_MISS_TOKENS:
                cause = self.classify_cause(model, tool_count, elapsed_ms)
                alerts.append(
                    CacheAlert(
                        alert_type=CacheAlertType.PREFIX_UNSTABLE,
                        turn=turn,
                        cache_ratio=cache_ratio,
                        cache_read=cache_read,
                        cache_create=cache_create,
                        uncached=uncached,
                        prev_cache_read=prev,
                        drop_pct=drop_pct,
                        message=(
                            f"cache_read dropped {drop_pct:.0%} "
                            f"({prev} → {cache_read}) on turn {turn} "
                            f"— {cause}"
                        ),
                    )
                )

        if (
            turn >= self.RATIO_MIN_TURN
            and cache_ratio < self.RATIO_THRESHOLD
            and (cache_read + cache_create) > 0
        ):
            alerts.append(
                CacheAlert(
                    alert_type=CacheAlertType.RATIO_LOW,
                    turn=turn,
                    cache_ratio=cache_ratio,
                    cache_read=cache_read,
                    cache_create=cache_create,
                    uncached=uncached,
                    message=(
                        f"low cache hit ratio {cache_ratio:.1%} on turn {turn} "
                        "— prefix may be unstable"
                    ),
                )
            )

        self.prev_cache_read = cache_read
        if not suppressed:
            self.prev_model = model
            self.prev_tool_count = tool_count
        return alerts

    def classify_cause(
        self,
        model: str,
        tool_count: int,
        elapsed_ms: int | None,
    ) -> str:
        """Determine the likely cause of a cache_read drop.

        Checks model change and tool count change first (client-side),
        then falls back to TTL heuristics based on elapsed time.
        """
        if model and self.prev_model and model != self.prev_model:
            return f"model changed ({self.prev_model} → {model})"
        if tool_count >= 0 and self.prev_tool_count >= 0 and tool_count != self.prev_tool_count:
            return f"tools changed ({self.prev_tool_count} → {tool_count})"
        if elapsed_ms is not None:
            if elapsed_ms > CACHE_TTL_1HOUR_MS:
                return "likely 1h TTL expiry"
            if elapsed_ms > CACHE_TTL_5MIN_MS:
                return "likely 5min TTL expiry"
        return "prefix may be unstable"
