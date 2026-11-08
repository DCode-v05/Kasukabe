"""Central user-interrupt dispatcher for active turn executors.

The problem: ``StreamingToolExecutor.cancel_interruptible()`` needs a trigger.
In a CLI/TUI context that trigger is SIGINT (Ctrl+C).  In a server context
the trigger is an HTTP/WebSocket request, and SIGINT belongs to uvicorn for
graceful shutdown.  We want one mechanism that fans out interrupts to every
active turn, regardless of which path delivered the signal.

``InterruptSource`` is that mechanism.  Each per-turn executor registers
itself on creation and unregisters on turn end.  Any interrupt source
(SIGINT handler, HTTP endpoint, programmatic caller) calls ``trigger()``
to fire ``cancel_interruptible`` on every registered executor — which in
turn cancels only the tools that declared ``InterruptBehavior.CANCEL``.

SIGINT binding is opt-in: ``install()`` attaches an asyncio signal handler
only when called explicitly (gated by ``settings.enable_sigint_interrupt``
in bootstrap).  Server deployments leave it off; CLI/TUI deployments turn
it on.  ``install()`` is main-thread-only and guarded — any failure to
attach logs a warning and returns False without raising.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import weakref
from typing import TYPE_CHECKING

from src.engine_core.cancellation import AbortReason

if TYPE_CHECKING:
    from src.engine_core.streaming_tool_executor import StreamingToolExecutor

logger = logging.getLogger(__name__)


class InterruptSource:
    """Registry of active executors plus SIGINT installer.

    The executor set is a ``WeakSet`` so that a turn that exits via an
    uncaught exception (without calling ``unregister``) is self-healing:
    once the executor is no longer referenced by its owning thread, it
    drops out of the registry automatically.
    """

    def __init__(self) -> None:
        self.executors: "weakref.WeakSet[StreamingToolExecutor]" = weakref.WeakSet()
        self.installed = False


    def register(self, executor: "StreamingToolExecutor") -> None:
        self.executors.add(executor)

    def unregister(self, executor: "StreamingToolExecutor") -> None:
        self.executors.discard(executor)

    @property
    def active_count(self) -> int:
        return len(self.executors)


    def trigger(self, reason: str = "User interrupted") -> int:
        """Fan out ``cancel_interruptible`` to every registered executor.

        Errors from individual executors are isolated so one broken
        executor cannot block interrupt delivery to its siblings.
        Returns the number of executors that accepted the interrupt.
        """
        count = 0
        for ex in list(self.executors):
            try:
                ex.cancel_interruptible(reason, AbortReason.USER_INTERRUPTED)
                count += 1
            except Exception:
                logger.exception(
                    "cancel_interruptible failed for executor %r",
                    ex,
                )
        return count


    def install(self) -> bool:
        """Attach SIGINT to :meth:`trigger`.  Idempotent.

        Returns True if the handler is (now) attached, False if the
        environment cannot host one (non-main thread, no running loop,
        Windows ProactorEventLoop, etc.).  Never raises.
        """
        if self.installed:
            return True
        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(
                signal.SIGINT,
                lambda: self.trigger("SIGINT"),
            )
        except (NotImplementedError, ValueError, RuntimeError) as e:
            logger.warning("SIGINT interrupt handler not installed: %s", e)
            return False
        self.installed = True
        logger.info("SIGINT interrupt handler installed")
        return True

    def uninstall(self) -> None:
        """Detach the SIGINT handler if installed.  Idempotent."""
        if not self.installed:
            return
        try:
            asyncio.get_running_loop().remove_signal_handler(signal.SIGINT)
        except Exception:
            logger.debug("Failed to remove SIGINT handler", exc_info=True)
        self.installed = False


INSTANCE: InterruptSource | None = None


def get_interrupt_source() -> InterruptSource:
    """Return the process-wide interrupt source singleton."""
    global INSTANCE
    if INSTANCE is None:
        INSTANCE = InterruptSource()
    return INSTANCE


def reset_for_tests() -> None:
    """Drop the singleton so tests can get a fresh one.  Test-only."""
    global INSTANCE
    if INSTANCE is not None:
        INSTANCE.uninstall()
    INSTANCE = None
