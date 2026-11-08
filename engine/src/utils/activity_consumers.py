"""Session lifecycle policy — what the engine *does* on activity transitions.

ActivityManager detects transitions.  This module decides what to DO with
them.  The policy is the bridge between the event bus and the engine's
autonomous subsystems:

    IDLE          → opportunistic batch work now (no user pressure)
    AWAY          → flush watermarks + pause scheduled batch loop
    DISCONNECTED  → suspend all autonomous work (stop burning $ for nobody)
    → ACTIVE      → resume paused work (reconnect)

Every transition is also mirrored into Raven (``obs_events``) so the
session-lifecycle usage pattern is queryable alongside token and cost data.

Wiring (bootstrap.py): instantiate ``ActivityPolicy`` AFTER lc_scheduler
and Raven are up (its only two real dependencies), then call
``policy.install()`` which subscribes it to the engine event emitter.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.engine_core.events import ActivityStateChanged

if TYPE_CHECKING:
    from src.engine_core.events import Event, EventEmitter
    from src.learning_centre.scheduler import LCScheduler

logger = logging.getLogger(__name__)


class ActivityPolicy:
    """Subscribes to ActivityStateChanged and dispatches to subsystems.

    Every handler is guarded so a missing subsystem (test / degraded boot)
    is a silent no-op instead of a crash.  Exceptions inside a single
    handler are logged but never propagated — one broken consumer should
    not tear down the event bus.
    """

    def __init__(
        self,
        emitter: "EventEmitter",
        *,
        lc_scheduler: "LCScheduler | None" = None,
        raven_track: Any = None,
    ) -> None:
        self.emitter = emitter
        self.lc = lc_scheduler
        self.raven_track = raven_track
        self.installed = False
        self.pending_tasks: set[asyncio.Task] = set()

    def install(self) -> None:
        """Subscribe this policy to the event emitter.  Idempotent."""
        if self.installed:
            return
        self.emitter.on(self.handle)
        self.installed = True
        logger.info(
            "ActivityPolicy installed (lc=%s raven=%s)",
            bool(self.lc),
            bool(self.raven_track),
        )

    def uninstall(self) -> None:
        if not self.installed:
            return
        try:
            self.emitter.off(self.handle)
        except Exception:
            logger.debug("emitter.off failed", exc_info=True)
        self.installed = False

    async def handle(self, event: "Event") -> None:
        if not isinstance(event, ActivityStateChanged):
            return

        await self.record_to_raven(event)

        old, new = event.old_state, event.new_state

        if new == "idle":
            await self.on_idle()
        elif new == "away":
            await self.on_away()
        elif new == "disconnected":
            await self.on_disconnected()
        elif new == "active" and old in ("idle", "away", "disconnected"):
            await self.on_reconnect()


    async def on_idle(self) -> None:
        """User paused — opportunistic window for heavy background work.

        Fire-and-forget a sweep (incremental flush + cleanup) so any
        watermark progress already made is persisted while nothing is
        blocked by user-facing latency.
        """
        if self.lc is None:
            return
        try:
            task = asyncio.create_task(self.lc.trigger_sweep())
            self.pending_tasks.add(task)
            task.add_done_callback(self.pending_tasks.discard)
        except Exception:
            logger.debug("trigger_sweep failed on IDLE", exc_info=True)

    async def on_away(self) -> None:
        """User gone for a long time — flush and pause the batch loop.

        Sweep and pause are independent (sweep drains watermarks, pause
        halts future iterations) so run them concurrently.
        """
        if self.lc is None:
            return
        results = await asyncio.gather(
            self.lc.trigger_sweep(),
            self.lc.pause(),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.debug("AWAY handler step failed", exc_info=r)

    async def on_disconnected(self) -> None:
        """User timed out — suspend all autonomous work (cost control)."""
        if self.lc is None:
            return
        try:
            await self.lc.pause()
        except Exception:
            logger.debug("lc.pause failed on DISCONNECTED", exc_info=True)

    async def on_reconnect(self) -> None:
        """User came back — resume paused work."""
        if self.lc is None:
            return
        try:
            await self.lc.resume()
        except Exception:
            logger.debug("lc.resume failed on reconnect", exc_info=True)


    async def record_to_raven(self, event: ActivityStateChanged) -> None:
        if self.raven_track is None:
            return
        try:
            from src.raven.events import ActivityTransitionEvent

            evt = ActivityTransitionEvent(
                old_state=event.old_state,
                new_state=event.new_state,
                idle_seconds=event.idle_seconds,
                tool_calls=event.tool_calls,
                errors=event.errors,
                user_active_seconds=event.user_active_seconds,
                engine_active_seconds=event.engine_active_seconds,
            )
            result = self.raven_track(evt)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.debug("raven.track failed for activity transition", exc_info=True)
