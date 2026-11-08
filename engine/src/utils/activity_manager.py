"""Session lifecycle activity manager.

Tracks user interaction and engine activity to drive idle/away/disconnected
state transitions. Emits ActivityStateChanged events on every transition so
consumers (TUI, product layer) can react — pause background tasks on idle,
flush buffers on away, show a reconnect summary after away.

State machine (default thresholds, configurable per deployment):

    active ──(5 min no input)──▶ idle
    idle   ──(30 min no input)──▶ away
    away   ──(timeout)─────────▶ disconnected
    idle / away / disconnected ──(user input)──▶ active   (reconnect)

On reconnect from away, ``get_reconnect_summary()`` returns a structured
snapshot of what happened while the user was absent (tool calls, errors,
active duration) so callers can generate "while you were away" copy.

Thread-safety: all public methods are safe to call from any coroutine on the
same event loop.  The internal background task runs on the same loop.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.engine_core.events import EventEmitter

logger = logging.getLogger(__name__)


class ActivityState(str, Enum):
    """Lifecycle states a session can occupy."""

    ACTIVE = "active"
    IDLE = "idle"
    AWAY = "away"
    DISCONNECTED = "disconnected"


@dataclass
class SessionStats:
    """Snapshot of session activity collected since the last user input.

    Two time buckets (mirroring reference activityManager.ts but engine-native):

    * ``user_active_seconds`` — seconds the user was actively engaging. Each
      ``record_user_activity`` call contributes ``delta`` iff the gap since the
      previous call is under ``USER_ACTIVITY_WINDOW_S`` (treats bursty typing
      as active, long gaps as idle — matches reference bucketing semantics).

    * ``engine_active_seconds`` — seconds the engine executed tools. Sum of
      ``record_tool_call_end - record_tool_call_start`` durations.
    """

    tool_calls: int = 0
    errors: int = 0
    user_active_seconds: float = 0.0
    engine_active_seconds: float = 0.0
    last_user_input_at: str = ""
    away_since: str = ""


@dataclass
class ReconnectSummary:
    """Payload returned to callers when a user reconnects from away/disconnected."""

    previous_state: ActivityState
    idle_seconds: float
    stats: SessionStats

    def format_recap(self) -> str:
        """Deterministic natural-language recap (no LLM).

        Compact, one-sentence "while you were away" line derived from the
        structured stats. Callers that want LLM-flavored prose should use
        ``src.services.away_summary.generate_away_summary`` instead — this
        method is the zero-cost, zero-latency fallback that always works.
        """
        parts: list[str] = []
        away_s = self.idle_seconds
        if away_s < 60:
            parts.append(f"While you were away ({int(away_s)}s)")
        elif away_s < 3600:
            parts.append(f"While you were away ({int(away_s // 60)}m)")
        else:
            hours = away_s / 3600
            parts.append(f"While you were away ({hours:.1f}h)")

        body: list[str] = []
        if self.stats.tool_calls:
            body.append(
                f"{self.stats.tool_calls} tool call{'s' if self.stats.tool_calls != 1 else ''}"
            )
        if self.stats.engine_active_seconds > 0:
            body.append(f"{self.stats.engine_active_seconds:.1f}s of compute")
        if self.stats.errors:
            body.append(f"{self.stats.errors} error{'s' if self.stats.errors != 1 else ''}")
        if not body:
            body.append("no autonomous activity")

        return f"{parts[0]}: {', '.join(body)}."


class ActivityManager:
    """Session lifecycle tracker with configurable idle/away/disconnect thresholds.

    Instantiate once per engine process (singleton via ``get_instance()``).
    Call ``start()`` after the event loop is running and ``stop()`` on shutdown.

    Args:
        emitter:            EventEmitter to fan ActivityStateChanged events to.
        idle_timeout_s:     Seconds of inactivity before transitioning active→idle.
        away_timeout_s:     Seconds of inactivity before transitioning idle→away.
        disconnect_timeout_s: Seconds of inactivity before transitioning away→disconnected.
        poll_interval_s:    How often the background task checks for transitions.
        get_now:            Injectable clock for testing.
    """

    instance: ActivityManager | None = None

    USER_ACTIVITY_WINDOW_S: float = 5.0

    def __init__(
        self,
        emitter: EventEmitter | None = None,
        idle_timeout_s: float = 300.0,
        away_timeout_s: float = 1800.0,
        disconnect_timeout_s: float = 3600.0,
        poll_interval_s: float = 30.0,
        get_now: Callable[[], float] | None = None,
    ) -> None:
        self.emitter = emitter
        self.idle_timeout_s = idle_timeout_s
        self.away_timeout_s = away_timeout_s
        self.disconnect_timeout_s = disconnect_timeout_s
        self.poll_interval_s = poll_interval_s
        self.get_now: Callable[[], float] = get_now or time.monotonic

        self.state = ActivityState.ACTIVE
        self.last_user_input_at: float = self.get_now()
        self.session_start: float = self.get_now()
        self.state_entered_at: float = self.get_now()

        self.tool_calls_since_active: int = 0
        self.errors_since_active: int = 0
        self.engine_active_s: float = 0.0
        self.user_active_s: float = 0.0
        self.tool_start_times: dict[str, float] = {}

        self.bg_task: asyncio.Task | None = None


    @classmethod
    def get_instance(cls) -> ActivityManager:
        if cls.instance is None:
            cls.instance = ActivityManager()
        return cls.instance

    @classmethod
    def set_instance(cls, instance: ActivityManager) -> None:
        """Replace the singleton — used by bootstrap and tests."""
        cls.instance = instance

    @classmethod
    def reset_instance(cls) -> None:
        """Clear singleton — for tests."""
        cls.instance = None

    def set_emitter(self, emitter: EventEmitter) -> None:
        """Wire in an emitter after construction (bootstrap sets this)."""
        self.emitter = emitter

    async def start(self) -> None:
        """Start the background polling task."""
        if self.bg_task is None or self.bg_task.done():
            self.bg_task = asyncio.create_task(self.poll_loop(), name="activity_manager_poll")
            logger.info(
                "ActivityManager started: idle=%ds away=%ds disconnect=%ds",
                int(self.idle_timeout_s),
                int(self.away_timeout_s),
                int(self.disconnect_timeout_s),
            )

    async def stop(self) -> None:
        """Cancel the background task and record shutdown."""
        if self.bg_task and not self.bg_task.done():
            self.bg_task.cancel()
            try:
                await self.bg_task
            except asyncio.CancelledError:
                pass
        self.bg_task = None
        logger.info("ActivityManager stopped")


    def record_user_activity(self) -> ReconnectSummary | None:
        """Record a user interaction (e.g. incoming execute request).

        Bucketing: if the gap since the previous user input is under
        ``USER_ACTIVITY_WINDOW_S`` the delta counts toward user_active_seconds
        (treats bursty typing as continuous engagement); larger gaps are
        treated as the user having walked away in between.

        Returns a ``ReconnectSummary`` if the session was away/disconnected
        (so callers can generate an away-recap), otherwise None.
        """
        now = self.get_now()
        prev_state = self.state
        delta = now - self.last_user_input_at
        if 0 < delta <= self.USER_ACTIVITY_WINDOW_S:
            self.user_active_s += delta
        self.last_user_input_at = now

        if prev_state in (ActivityState.AWAY, ActivityState.DISCONNECTED):
            snapshot = self.snapshot_stats()
            summary = ReconnectSummary(
                previous_state=prev_state,
                idle_seconds=delta,
                stats=snapshot,
            )
            self.reset_stats()
            self.transition(ActivityState.ACTIVE, now, snapshot=snapshot)
            return summary

        if prev_state == ActivityState.IDLE:
            self.transition(ActivityState.ACTIVE, now)

        return None

    def record_tool_call_start(self, operation_id: str) -> None:
        """Mark the start of a tool execution (call from thread.py)."""
        self.tool_start_times[operation_id] = self.get_now()
        self.tool_calls_since_active += 1

    def record_tool_call_end(self, operation_id: str) -> None:
        """Mark the end of a tool execution and accumulate engine active time."""
        start = self.tool_start_times.pop(operation_id, None)
        if start is not None:
            self.engine_active_s += self.get_now() - start

    def record_tool_call(self, tool_name: str) -> None:
        """Lightweight single-call recording (no duration tracking)."""
        self.tool_calls_since_active += 1

    def record_error(self) -> None:
        """Record that a tool or agent-loop error occurred."""
        self.errors_since_active += 1

    def record_shutdown(self) -> None:
        """Mark session as disconnected (called on engine shutdown)."""
        self.transition(ActivityState.DISCONNECTED, self.get_now())


    def idle_seconds(self) -> float:
        """Seconds since the last user input."""
        return self.get_now() - self.last_user_input_at

    def get_reconnect_summary(self) -> ReconnectSummary:
        """Return current session snapshot (for away-recap generation)."""
        return ReconnectSummary(
            previous_state=self.state,
            idle_seconds=self.idle_seconds(),
            stats=self.snapshot_stats(),
        )


    def snapshot_stats(self) -> SessionStats:
        now_mono = self.get_now()
        return SessionStats(
            tool_calls=self.tool_calls_since_active,
            errors=self.errors_since_active,
            engine_active_seconds=round(self.engine_active_s, 1),
            user_active_seconds=round(self.user_active_s, 1),
            last_user_input_at=self.iso_at(now_mono, self.last_user_input_at)
            if self.last_user_input_at > 0
            else "",
            away_since=self.iso_at(now_mono, self.state_entered_at)
            if self.state in (ActivityState.IDLE, ActivityState.AWAY, ActivityState.DISCONNECTED)
            else "",
        )

    def iso_at(self, now_mono: float, target_mono: float) -> str:
        """Convert a monotonic timestamp to an ISO-8601 UTC wall-clock string."""
        wall = time.time() - (now_mono - target_mono)
        return datetime.datetime.fromtimestamp(wall, tz=datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    def reset_stats(self) -> None:
        self.tool_calls_since_active = 0
        self.errors_since_active = 0
        self.engine_active_s = 0.0
        self.user_active_s = 0.0
        self.tool_start_times.clear()

    def transition(
        self,
        new_state: ActivityState,
        now: float,
        snapshot: SessionStats | None = None,
    ) -> None:
        """Flip the state machine and schedule an emit.

        Pass ``snapshot`` when the caller is about to (or has just) reset the
        counter fields, so the async emit reports the pre-reset numbers
        instead of zeros.
        """
        old_state = self.state
        if old_state == new_state:
            return
        self.state = new_state
        self.state_entered_at = now
        logger.info(
            "Activity state transition: %s → %s (idle=%.0fs)",
            old_state.value,
            new_state.value,
            now - self.last_user_input_at,
        )
        if self.emitter is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.emit_transition(old_state, new_state, now, snapshot))
            except RuntimeError:
                pass

    async def emit_transition(
        self,
        old_state: ActivityState,
        new_state: ActivityState,
        now: float,
        snapshot: SessionStats | None = None,
    ) -> None:
        from src.engine_core.events import ActivityStateChanged

        if self.emitter is None:
            return
        stats = snapshot if snapshot is not None else self.snapshot_stats()
        try:
            await self.emitter.emit(
                ActivityStateChanged(
                    old_state=old_state.value,
                    new_state=new_state.value,
                    idle_seconds=round(now - self.last_user_input_at, 1),
                    tool_calls=stats.tool_calls,
                    errors=stats.errors,
                    user_active_seconds=stats.user_active_seconds,
                    engine_active_seconds=stats.engine_active_seconds,
                )
            )
        except Exception:
            logger.debug("ActivityStateChanged emit failed", exc_info=True)

    async def poll_loop(self) -> None:
        """Background task: check for idle/away/disconnected transitions."""
        try:
            while True:
                await asyncio.sleep(self.poll_interval_s)
                self.check_transitions()
        except asyncio.CancelledError:
            pass

    def check_transitions(self) -> None:
        now = self.get_now()
        idle_s = now - self.last_user_input_at

        if self.state == ActivityState.ACTIVE and idle_s >= self.idle_timeout_s:
            self.transition(ActivityState.IDLE, now)
        elif self.state == ActivityState.IDLE and idle_s >= self.away_timeout_s:
            self.transition(ActivityState.AWAY, now)
        elif self.state == ActivityState.AWAY and idle_s >= self.disconnect_timeout_s:
            self.transition(ActivityState.DISCONNECTED, now)


def get_activity_manager() -> ActivityManager:
    """Return the process-wide ActivityManager singleton."""
    return ActivityManager.get_instance()
