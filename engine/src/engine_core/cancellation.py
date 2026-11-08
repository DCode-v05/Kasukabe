"""Cancellation infrastructure for cooperative tool abort.

CancellationToken is the Python equivalent of JS AbortController.
Tools check token.cancelled to bail out early; parent tokens propagate
cancellation to children (but not vice versa unless bubble_up=True).

AbortReason provides structured reasons so synthetic error messages
can differ per cancellation cause (sibling error vs user interrupt vs
streaming fallback).
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Callable


class AbortReason(str, Enum):
    """Why a tool was cancelled. Determines the synthetic error message."""

    SIBLING_ERROR = "sibling_error"
    USER_INTERRUPTED = "user_interrupted"
    STREAMING_FALLBACK = "streaming_fallback"
    TIMEOUT = "timeout"


class InterruptBehavior(str, Enum):
    """What happens when a tool is interrupted mid-execution."""

    CANCEL = "cancel"
    BLOCK = "block"


class CancellationToken:
    """Cooperative cancellation signal for async tool execution."""

    def __init__(self) -> None:
        self.cancelled = False
        self.reason = ""
        self.abort_reason: AbortReason | None = None
        self.callbacks: list[Callable[[], None]] = []

    def cancel(self, reason: str = "", abort_reason: AbortReason | None = None) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        self.reason = reason
        self.abort_reason = abort_reason
        for cb in self.callbacks:
            cb()
        self.callbacks.clear()

    def on_cancel(self, callback: Callable[[], None]) -> None:
        if self.cancelled:
            callback()
        else:
            self.callbacks.append(callback)


def create_child_token(parent: CancellationToken) -> CancellationToken:
    """Create a token that cancels when parent cancels, but not vice versa."""
    child = CancellationToken()
    parent.on_cancel(lambda: child.cancel(parent.reason, parent.abort_reason))
    return child


def create_bubble_token(parent: CancellationToken) -> CancellationToken:
    """Create a token that propagates both ways: parent→child AND child→parent.

    Used when a tool cancellation (e.g. permission rejection) should abort
    the entire turn, not just the tool.  Sibling errors are excluded from
    upward propagation — they only cancel siblings, not the parent turn.
    """
    child = CancellationToken()
    # parent → child (always)
    parent.on_cancel(lambda: child.cancel(parent.reason, parent.abort_reason))

    # child → parent (only for non-sibling reasons)
    def bubble_up() -> None:
        if child.abort_reason != AbortReason.SIBLING_ERROR and not parent.cancelled:
            parent.cancel(child.reason, child.abort_reason)

    child.on_cancel(bubble_up)
    return child


class CombinedCancellationToken(CancellationToken):
    """Merges parent + sibling + timeout into one token.

    First source to fire wins; later sources are no-ops because
    CancellationToken.cancel() is idempotent.  cleanup() disposes the
    timeout timer and marks the wrapper dead so any late parent/sibling
    fires become no-ops (source tokens have no listener-unregister API,
    so the disposed flag is the safe way to neutralize late callbacks).
    """

    def __init__(
        self,
        parent: CancellationToken | None = None,
        sibling: CancellationToken | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        super().__init__()
        self.disposed = False
        self.timer: asyncio.TimerHandle | None = None

        if parent is not None:

            def on_parent() -> None:
                if self.disposed:
                    return
                self.cancel(parent.reason, parent.abort_reason)

            parent.on_cancel(on_parent)

        if sibling is not None:

            def on_sibling() -> None:
                if self.disposed:
                    return
                self.cancel(
                    sibling.reason,
                    sibling.abort_reason or AbortReason.SIBLING_ERROR,
                )

            sibling.on_cancel(on_sibling)

        if timeout_ms is not None and timeout_ms > 0:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop — timeout arming is best-effort; caller
                # can still use parent/sibling sources.
                return

            def on_timeout() -> None:
                if self.disposed:
                    return
                self.cancel(
                    f"Timeout after {timeout_ms}ms",
                    AbortReason.TIMEOUT,
                )

            self.timer = loop.call_later(timeout_ms / 1000.0, on_timeout)

    def cleanup(self) -> None:
        """Dispose listeners and cancel the timeout timer.  Idempotent."""
        self.disposed = True
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
