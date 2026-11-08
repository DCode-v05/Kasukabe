"""Global cleanup registry — centralized shutdown safety net.

Tasks register async cleanup callbacks on spawn.  On graceful shutdown,
run_all_cleanup() fires every registered callback in parallel with a
timeout.  Backgrounded processes call the returned unregister function
so they survive agent-level cleanup (Gap 8: self-registration lifecycle).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

CleanupCallback = Callable[[], Awaitable[None]]

callbacks: dict[str, CleanupCallback] = {}


def register_cleanup(name: str, callback: CleanupCallback) -> Callable[[], None]:
    """Register an async cleanup callback by *name*.

    Returns an idempotent unregister function.  Backgrounded processes
    should call unregister() so they are not killed on the next tool run
    or agent exit.
    """
    callbacks[name] = callback

    def unregister() -> None:
        callbacks.pop(name, None)

    return unregister


async def run_all_cleanup(timeout: float = 2.0) -> int:
    """Fire all registered callbacks in parallel with *timeout* seconds.

    Returns count of callbacks that completed successfully.  Individual
    failures are logged and swallowed — one broken callback must not
    prevent the rest from running.
    """
    if not callbacks:
        return 0

    snapshot = list(callbacks.items())
    callbacks.clear()

    async def safe_run(name: str, cb: CleanupCallback) -> bool:
        try:
            await asyncio.wait_for(cb(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning("cleanup callback %r timed out after %.1fs", name, timeout)
            return False
        except Exception:
            logger.warning("cleanup callback %r failed", name, exc_info=True)
            return False

    results = await asyncio.gather(
        *[safe_run(n, cb) for n, cb in snapshot],
        return_exceptions=True,
    )
    return sum(1 for r in results if r is True)


def clear_registry() -> None:
    """Remove all callbacks.  For test teardown."""
    callbacks.clear()
