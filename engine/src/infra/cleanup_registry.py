"""Infrastructure-level cleanup registry.

Subsystems register async teardown callbacks here at boot time.
On graceful shutdown, run_cleanups() fires them in LIFO order (last registered,
first to run), matching the reverse of boot order so dependencies close safely.
For example: coordinator closes before SQLite because it was registered after it.

Separate from src/sandbox/cleanup_registry.py, which tracks subprocess-level
cleanup for backgrounded shell commands.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

CleanupFn = Callable[[], Awaitable[None]]

cleanup_fns: list[tuple[str, CleanupFn]] = []


def register_cleanup(name: str, fn: CleanupFn) -> Callable[[], None]:
    """Register a named async cleanup function.

    Returns a no-arg unregister callable.  Subsystems that may be torn down
    before process exit (e.g., during test teardown or hot-reload) should
    call the returned function to deregister.
    """
    entry = (name, fn)
    cleanup_fns.append(entry)

    def unregister() -> None:
        try:
            cleanup_fns.remove(entry)
        except ValueError:
            pass

    return unregister


async def run_cleanups(timeout_per_fn: float = 2.0) -> None:
    """Run all registered cleanups in LIFO order with per-function timeout.

    Failures are logged and swallowed — one broken cleanup must not prevent
    the rest from running.  On timeout, logs a warning and moves to the next.
    """
    for name, fn in reversed(cleanup_fns):
        try:
            await asyncio.wait_for(fn(), timeout=timeout_per_fn)
            logger.debug("cleanup '%s' complete", name)
        except asyncio.TimeoutError:
            logger.warning("cleanup '%s' timed out after %.1fs — skipping", name, timeout_per_fn)
        except Exception as e:
            logger.warning("cleanup '%s' failed: %s", name, e)


def cleanup_count() -> int:
    """Return the number of currently registered cleanup functions."""
    return len(cleanup_fns)


def clear_registry() -> None:
    """Remove all registered cleanups.  For test teardown only."""
    cleanup_fns.clear()
