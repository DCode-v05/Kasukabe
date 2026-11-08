"""ProgressTracker — in-flight work telemetry for a running SubAgent.

Mirrors ProgressTracker in the reference agentToolUtils.ts: ring buffer of
recent tool invocations, cumulative counters, last-activity timestamp used
by the idle watchdog.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


PROGRESS_RECENT_LIMIT = 5


@dataclass
class ProgressTracker:
    """Mutable per-agent telemetry. Read after each turn to build a
    SubAgentProgress event without re-walking the tool call log."""

    turn: int = 0
    tool_use_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_create_tokens: int = 0
    cost_usd: float = 0.0
    recent: deque[str] = field(
        default_factory=lambda: deque(maxlen=PROGRESS_RECENT_LIMIT),
    )
    last_activity_ts: float = field(default_factory=time.monotonic)

    def note_tool(self, name: str, input_preview: str = "") -> None:
        """Record a tool invocation in the ring buffer and bump the counter."""
        self.tool_use_count += 1
        entry = f"{name}({input_preview[:60]})" if input_preview else name
        self.recent.append(entry)
        self.last_activity_ts = time.monotonic()

    def note_turn(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read: int,
        cache_create: int,
        cost_usd: float,
    ) -> None:
        """Record a completed turn. Called once per run-loop iteration."""
        self.turn += 1
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read
        self.cache_create_tokens = cache_create
        self.cost_usd = cost_usd
        self.last_activity_ts = time.monotonic()

    def idle_ms(self) -> int:
        """Elapsed milliseconds since the last noted activity."""
        return int((time.monotonic() - self.last_activity_ts) * 1000)

    def recent_list(self) -> list[str]:
        return list(self.recent)


__all__ = ["ProgressTracker", "PROGRESS_RECENT_LIMIT"]
