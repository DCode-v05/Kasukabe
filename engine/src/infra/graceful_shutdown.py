"""Graceful shutdown — signal handlers, failsafe timer, ordered teardown.

Registers OS signal handlers for SIGTERM, SIGINT, and SIGHUP.  On any
shutdown trigger, executes an ordered teardown sequence:

  1. Set shutting_down flag (new requests are rejected)
  2. Arm failsafe timer (force-exits if cleanup hangs)
  3. Run pre-shutdown drain callbacks in order (SSE queues, input futures)
  4. Run infra cleanup registry in LIFO order (coordinator → scheduler → db)
  5. Cancel failsafe and call sys.exit()

Works for all execution modes: FastAPI/Uvicorn, CLI/TUI, direct python -m.

In Uvicorn server mode: Uvicorn overwrites SIGTERM/SIGINT handlers after
setup_signal_handlers() is called, so those signals still go through
Uvicorn's own handler → FastAPI lifespan exit → bootstrap.shutdown().
SIGHUP is not handled by Uvicorn, so our SIGHUP handler remains active.

Reference: src/utils/gracefulShutdown.ts (setupGracefulShutdown, gracefulShutdown)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

shutting_down: bool = False
sigint_count: int = 0
failsafe_handle: asyncio.TimerHandle | None = None
drain_callbacks: list[Callable[[], Awaitable[None]]] = []
handlers_installed: bool = False
prior_excepthook: Callable[..., None] | None = None


def is_shutting_down() -> bool:
    """Return True if graceful shutdown has been initiated."""
    return shutting_down


def register_drain_callback(fn: Callable[[], Awaitable[None]]) -> None:
    """Register an async drain callback to run before the cleanup registry.

    Used by server.py to drain SSE queues and resolve input futures before
    subsystem teardown.  Callbacks run sequentially in registration order,
    each bounded by a 30s timeout.
    """
    drain_callbacks.append(fn)


def setup_signal_handlers(register_sigint: bool = True) -> None:
    """Register OS signal handlers for graceful shutdown.

    Idempotent — second call is a no-op.

    Args:
        register_sigint: Set False when another handler owns SIGINT
            (e.g., bootstrap's enable_sigint_interrupt mode).  SIGTERM
            and SIGHUP are always registered.
    """
    global handlers_installed
    if handlers_installed:
        return
    handlers_installed = True

    def schedule_shutdown(exit_code: int) -> None:
        """Schedule graceful_shutdown on the running event loop."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(graceful_shutdown(exit_code))
            else:
                sys.exit(exit_code)
        except RuntimeError:
            sys.exit(exit_code)

    def handle_sigterm(signum: int, frame: object) -> None:
        logger.info("SIGTERM received — initiating graceful shutdown")
        schedule_shutdown(143)

    def handle_sigint(signum: int, frame: object) -> None:
        global sigint_count
        sigint_count += 1
        if sigint_count > 1:
            logger.warning("Double SIGINT — force exit")
            os._exit(1)
        logger.info("SIGINT received — initiating graceful shutdown (Ctrl+C again to force)")
        schedule_shutdown(0)

    def handle_sighup(signum: int, frame: object) -> None:
        logger.info("SIGHUP received — initiating graceful shutdown")
        schedule_shutdown(129)

    signal.signal(signal.SIGTERM, handle_sigterm)
    if register_sigint:
        signal.signal(signal.SIGINT, handle_sigint)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, handle_sighup)

    sigs = ["SIGTERM"]
    if register_sigint:
        sigs.append("SIGINT")
    if hasattr(signal, "SIGHUP"):
        sigs.append("SIGHUP")
    logger.info("Signal handlers installed (%s)", ", ".join(sigs))

    install_exception_handlers()


def sys_excepthook(exc_type, exc_value, tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        if prior_excepthook is not None:
            prior_excepthook(exc_type, exc_value, tb)
        return
    logger.error(
        "uncaught_exception: %s: %s",
        exc_type.__name__,
        exc_value,
        exc_info=(exc_type, exc_value, tb),
    )


def asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    exc = context.get("exception")
    msg = context.get("message", "unhandled asyncio exception")
    if exc is not None:
        logger.error(
            "unhandled_rejection: %s: %s",
            type(exc).__name__,
            exc,
            exc_info=exc,
        )
    else:
        logger.error("unhandled_rejection: %s", msg)


def install_exception_handlers() -> None:
    """Install process-wide handlers for uncaught sync and asyncio exceptions.

    Log-only — does not trigger shutdown.  Idempotent:
    ``sys.excepthook`` is replaced exactly once (the prior hook is
    remembered for KeyboardInterrupt passthrough and for
    ``reset_for_testing`` to restore).  The asyncio handler is
    re-attached on each call so a re-invocation under a newly-started
    event loop picks up the correct loop.
    """
    global prior_excepthook

    if prior_excepthook is None:
        prior_excepthook = sys.excepthook
        sys.excepthook = sys_excepthook

    try:
        loop = asyncio.get_running_loop()
        if loop.get_exception_handler() is not asyncio_exception_handler:
            loop.set_exception_handler(asyncio_exception_handler)
    except RuntimeError:
        pass

    logger.debug("Exception handlers installed (sys.excepthook + asyncio)")


async def graceful_shutdown(exit_code: int = 0) -> None:
    """Execute the ordered shutdown sequence.

    Idempotent — subsequent calls while shutdown is in progress return immediately.
    """
    global shutting_down

    if shutting_down:
        return
    shutting_down = True

    logger.info("Graceful shutdown started (exit_code=%d)", exit_code)

    from src.infra.cleanup_registry import cleanup_count

    cleanup_budget = cleanup_count() * 2.0
    failsafe_delay = max(5.0, cleanup_budget + 3.5)
    arm_failsafe(exit_code, failsafe_delay)

    for cb in drain_callbacks:
        try:
            await asyncio.wait_for(cb(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("drain callback timed out after 30s")
        except Exception as e:
            logger.warning("drain callback failed: %s", e)

    from src.infra.cleanup_registry import run_cleanups

    await run_cleanups()

    cancel_failsafe()
    logger.info("Graceful shutdown complete — exiting with code %d", exit_code)
    sys.exit(exit_code)


def arm_failsafe(exit_code: int, delay: float) -> None:
    """Arm a failsafe timer that force-exits if shutdown hangs past *delay* seconds."""
    global failsafe_handle
    cancel_failsafe()

    def force_exit() -> None:
        logger.error("Failsafe timer fired after %.1fs — force exit (code=%d)", delay, exit_code)
        os._exit(exit_code)

    try:
        loop = asyncio.get_event_loop()
        failsafe_handle = loop.call_later(delay, force_exit)
        logger.debug("Failsafe timer armed (%.1fs)", delay)
    except RuntimeError:
        pass


def cancel_failsafe() -> None:
    """Cancel the failsafe timer if armed."""
    global failsafe_handle
    if failsafe_handle is not None:
        failsafe_handle.cancel()
        failsafe_handle = None


def reset_for_testing() -> None:
    """Reset all module state.  For use in tests only."""
    global shutting_down, sigint_count, failsafe_handle, drain_callbacks
    global handlers_installed, prior_excepthook
    shutting_down = False
    sigint_count = 0
    cancel_failsafe()
    drain_callbacks.clear()
    handlers_installed = False
    if prior_excepthook is not None:
        sys.excepthook = prior_excepthook
        prior_excepthook = None
