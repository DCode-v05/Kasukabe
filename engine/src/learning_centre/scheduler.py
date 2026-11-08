"""Learning Centre Scheduler — periodic batch processing via asyncio loop.

Runs LCPipeline.run_batch() on a configurable interval. Supports manual
triggering via trigger_now() and clean shutdown via stop().

Two timers:
  1. LC batch pipeline — every 6 hours (processes lc_queue entries).
  2. Incremental flush sweep — every 30 minutes. Iterates active threads,
     flushes WM entries since watermark to lc_queue. Also cleans up expired
     threads (24h TTL).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.learning_centre.pipeline import LCPipeline, LCPipelineResult

logger = logging.getLogger(__name__)

FLUSH_SWEEP_INTERVAL_SECONDS = 1800  # 30 minutes
FLUSH_TURN_THRESHOLD = 5  # flush threads with 5+ new turns since watermark


class LCScheduler:
    """Periodically runs the LC pipeline batch loop.

    Args:
        pipeline: An LCPipeline instance with a run_batch() method.
        interval_hours: Hours between automatic batch runs (default 6.0).
    """

    def __init__(self, pipeline: "LCPipeline", interval_hours: float = 6.0) -> None:
        self.pipeline = pipeline
        self.interval_seconds = interval_hours * 3600
        self.task: asyncio.Task | None = None
        self.sweep_task: asyncio.Task | None = None
        self.coordinator: Any = None  # set via set_coordinator()
        # paused=True means the batch + sweep loops are suspended.  Lets the
        # ActivityManager/ActivityPolicy halt autonomous work on AWAY /
        # DISCONNECTED and resume it on reconnect without tearing the tasks
        # down and back up.
        self.paused: bool = False
        self.resume_event: asyncio.Event = asyncio.Event()
        self.resume_event.set()
        # Tripped by pause()/stop() so in-flight sleeps wake immediately and
        # re-check the resume gate.  Without this, a paused loop still sits
        # through the remainder of its current sleep (up to interval_seconds,
        # default 6h) before honouring the pause.
        self.interruption: asyncio.Event = asyncio.Event()

    def set_coordinator(self, coordinator: Any) -> None:
        """Inject coordinator reference for sweep access to threads."""
        self.coordinator = coordinator

    async def start(self) -> None:
        """Start the periodic batch loop as a background task."""
        self.task = asyncio.create_task(self.loop())
        self.sweep_task = asyncio.create_task(self.sweep_loop())
        logger.info(
            "LC scheduler started (batch=%.1fh, sweep=%ds)",
            self.interval_seconds / 3600,
            FLUSH_SWEEP_INTERVAL_SECONDS,
        )

    async def stop(self) -> None:
        """Cancel the background loops and wait for cleanup."""
        for task in (self.task, self.sweep_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("LC scheduler stopped")

    async def pause(self) -> None:
        """Suspend batch + sweep loops.

        Loops gate on ``resume_event`` and park until ``resume()`` is
        called.  Also wakes any in-flight sleep via ``interruption`` so
        the pause takes effect immediately rather than after the current
        sleep interval ends.  Safe to call repeatedly; a pause while
        already paused is a no-op.
        """
        if self.paused:
            return
        self.paused = True
        self.resume_event.clear()
        self.interruption.set()
        logger.info("LC scheduler paused")

    async def resume(self) -> None:
        """Release the pause gate. Safe to call when not paused."""
        if not self.paused:
            return
        self.paused = False
        self.resume_event.set()
        logger.info("LC scheduler resumed")

    async def trigger_now(self) -> "LCPipelineResult":
        """Manually trigger a single batch run (bypasses the timer)."""
        return await self.pipeline.run_batch()

    async def trigger_sweep(self) -> int:
        """Manually trigger a flush sweep. Returns entries flushed."""
        return await self.run_sweep()

    async def interruptible_sleep(self, seconds: float) -> None:
        """Sleep for *seconds* but wake early if ``interruption`` fires.

        After wake, ``interruption`` is cleared so the next cycle starts
        clean.  Callers should re-check ``resume_event`` after this returns
        if they need to block on the pause gate.
        """
        try:
            await asyncio.wait_for(self.interruption.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return
        finally:
            self.interruption.clear()

    async def loop(self) -> None:
        """Internal loop: gate → run_batch → interruptible sleep → repeat."""
        from src.herald import quota_cooldown

        while True:
            await self.resume_event.wait()
            # Quota cooldown: while citadel's window is exhausted every batch
            # call would only 429, so skip this cycle instead of burning it.
            if quota_cooldown.cooldown_active():
                logger.info(
                    "LC batch skipped: quota cooldown active (%.0fs remaining)",
                    quota_cooldown.cooldown_remaining(),
                )
                await self.interruptible_sleep(self.interval_seconds)
                continue
            try:
                result = await self.pipeline.run_batch()
                logger.info(
                    "LC batch complete: %d signals, %d errors",
                    result.signals_processed,
                    len(result.errors),
                )
            except Exception:
                logger.exception("LC batch failed")
            await self.interruptible_sleep(self.interval_seconds)

    async def sweep_loop(self) -> None:
        """Incremental flush sweep + thread cleanup. Runs every 30 minutes."""
        while True:
            await self.interruptible_sleep(FLUSH_SWEEP_INTERVAL_SECONDS)
            await self.resume_event.wait()
            try:
                await self.run_sweep()
            except Exception:
                logger.exception("Flush sweep failed")

    async def run_sweep(self) -> int:
        """Iterate active threads, flush WM entries, clean up expired threads."""
        if self.coordinator is None:
            return 0

        from src.engine_core.trajectory import incremental_flush
        from src.engine_core.conversation_store import CONVERSATION_TTL_SECONDS

        total_flushed = 0
        coordinator = self.coordinator
        memory = getattr(coordinator, "memory", None)
        db = getattr(coordinator, "db", None)
        conv_store = getattr(coordinator, "conversation_store", None)

        if memory is None or db is None:
            return 0

        # Incremental flush for threads with enough new turns
        thread_ids = list(getattr(coordinator, "threads", {}).keys())
        thread_index = getattr(coordinator, "thread_index", {})

        for tid in thread_ids:
            entry = thread_index.get(tid)
            if entry is None:
                continue

            thread = coordinator.threads.get(tid)
            if thread is None:
                continue

            watermark = getattr(thread, "lc_watermark", 0)
            turn_count = entry.turn_count

            if turn_count - watermark >= FLUSH_TURN_THRESHOLD:
                new_watermark = await incremental_flush(
                    memory=memory,
                    thread_id=tid,
                    db=db,
                    watermark=watermark,
                    conversation_summary=entry.summary,
                    status="active",
                )
                if new_watermark > watermark:
                    thread.lc_watermark = new_watermark
                    total_flushed += new_watermark - watermark
                    if conv_store:
                        await conv_store.save_metadata(tid, lc_watermark=new_watermark)

        # Clean up expired threads (24h TTL)
        if conv_store:
            deleted = await conv_store.cleanup_expired(CONVERSATION_TTL_SECONDS)
            if deleted:
                # Remove deleted threads from coordinator's dicts
                for tid in list(coordinator.threads.keys()):
                    if tid not in thread_ids:
                        continue
                    # Check if conversation still exists
                    convo = await conv_store.load_conversation(tid)
                    if convo is None:
                        coordinator.threads.pop(tid, None)
                        coordinator.thread_index.pop(tid, None)
                        logger.info("Removed expired thread %s", tid)

        # Clean up expired ingestion chunks (shared conversation-working-set
        # TTL; rows carry their expires_at stamped at write time)
        if coordinator.ingestion_store:
            try:
                ig_deleted = await coordinator.ingestion_store.cleanup_expired()
                if ig_deleted:
                    logger.info("Cleaned up %d expired ingestion chunks", ig_deleted)
            except Exception:
                logger.debug("Ingestion chunk cleanup failed", exc_info=True)

        return total_flushed
