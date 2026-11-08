"""Self-orphan detection — detect when the engine process itself is orphaned.

On macOS, closing the terminal revokes TTY file descriptors without sending
SIGHUP.  On Linux containers, the orchestrator dying reparents the engine
to PID 1.  This module polls for both conditions and triggers graceful
shutdown when detected.

Defaults OFF (FEATURE_SELF_ORPHAN_DETECT=false) because the engine may
legitimately run as PID 1 inside a container.  Enable explicitly in
orchestrated deployments.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

FEATURE_FLAG = "FEATURE_SELF_ORPHAN_DETECT"
FEATURE_DEFAULT = "false"

detector_task: asyncio.Task | None = None


def self_orphan_detection_enabled() -> bool:
    """Check whether self-orphan detection is active (feature flag)."""
    return os.environ.get(FEATURE_FLAG, FEATURE_DEFAULT).strip().lower() == "true"


def is_self_orphaned() -> bool:
    """Return True if this process appears to be orphaned."""
    if os.getppid() == 1:
        return True

    try:
        if hasattr(sys.stdout, "writable") and not sys.stdout.writable():
            return True
        if hasattr(sys.stdin, "readable") and not sys.stdin.readable():
            return True
    except (OSError, ValueError):
        return True

    return False


async def detector_loop(
    shutdown_callback: Callable[[], Awaitable[None]],
    interval_sec: float,
) -> None:
    """Poll for orphan condition every *interval_sec*, trigger shutdown."""
    while True:
        await asyncio.sleep(interval_sec)
        try:
            if is_self_orphaned():
                logger.warning(
                    "self-orphan detected (ppid=%d) — initiating shutdown",
                    os.getppid(),
                )
                await shutdown_callback()
                return
        except Exception:
            logger.debug("self-orphan check failed", exc_info=True)


async def start_self_orphan_detector(
    shutdown_callback: Callable[[], Awaitable[None]],
    interval_sec: float = 30.0,
) -> None:
    """Launch the self-orphan detector as a background asyncio task."""
    global detector_task
    if detector_task is not None and not detector_task.done():
        return
    if not self_orphan_detection_enabled():
        return
    detector_task = asyncio.create_task(detector_loop(shutdown_callback, interval_sec))
    logger.info("self-orphan detector started (interval=%.0fs)", interval_sec)


async def stop_self_orphan_detector() -> None:
    """Cancel the self-orphan detector task."""
    global detector_task
    if detector_task is not None and not detector_task.done():
        detector_task.cancel()
        try:
            await detector_task
        except asyncio.CancelledError:
            pass
    detector_task = None
    logger.info("self-orphan detector stopped")
