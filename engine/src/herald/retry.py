"""Error classification + overload tracking for citadel-routed LLM calls.

Engine talks to a single citadel gateway, so there is no key rotation in
this layer — citadel handles per-credential cooldown server-side. What
remains here is the error-classification surface engine_core depends on:

  - status-code / retry-after extraction from citadel error responses
  - context-overflow detection (drives compaction recovery)
  - 529 overload tracking with optional fallback-model triggering
  - persistent-retry backoff advice for foreground queries

These primitives are imported by ``src/engine_core/thread.py`` and
``src/engine_core/context_engineering.py``. The legacy KeyPool /
call_with_rotation / with_retries helpers were removed when engine
collapsed to a single citadel client.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from src.engine_core.schema import FOREGROUND_QUERY_SOURCES

logger = logging.getLogger(__name__)

NON_RETRYABLE_CODES: frozenset[int] = frozenset({402})
BASE_DELAY: float = 1.0
MAX_DELAY: float = 30.0

OVERFLOW_PATTERNS = (
    "maximum context length",
    "prompt is too long",
    "context_length_exceeded",
    "request too large",
    "exceeds the model's maximum",
    "content size exceeds",
    "too many tokens",
)


def get_status_code(exc: Exception) -> int | None:
    """Extract HTTP status code from a citadel error (CitadelAPIError or any
    exception exposing ``.status_code`` / ``.response.status_code``)."""
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    resp = getattr(exc, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def get_retry_after(exc: Exception) -> float | None:
    """Extract retry-after seconds from exception headers."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    headers = getattr(resp, "headers", None)
    if headers is None:
        return None
    retry_after = headers.get("retry-after")
    if retry_after is None:
        return None
    try:
        return float(retry_after)
    except (ValueError, TypeError):
        return None


def is_context_overflow(exc: Exception) -> bool:
    """Detect context length exceeded errors. Citadel passes upstream messages through."""
    msg = str(exc).lower()
    return any(p in msg for p in OVERFLOW_PATTERNS)


def backoff_delay(attempt: int, max_delay: float = MAX_DELAY) -> float:
    """Exponential backoff with jitter, hard-capped at max_delay."""
    return min(BASE_DELAY * (2**attempt) + random.uniform(0, 0.5), max_delay)


OVERLOAD_STATUS: int = 529
OVERLOAD_CONSECUTIVE_THRESHOLD: int = 3
PROMPT_CACHE_RETRY_CEILING: float = 20.0
TRANSIENT_CODES: frozenset[int] = frozenset({429, 529})
PERSISTENT_MAX_BACKOFF_S: float = 300.0
PERSISTENT_RESET_CAP_S: float = 21600.0
HEARTBEAT_INTERVAL_S: float = 30.0


class FallbackTriggeredError(Exception):
    """Raised when consecutive 529s exceed threshold and a fallback model is configured."""

    def __init__(self, original_model: str, fallback_model: str, consecutive_count: int):
        self.original_model = original_model
        self.fallback_model = fallback_model
        self.consecutive_count = consecutive_count
        super().__init__(
            f"{consecutive_count} consecutive 529 errors on {original_model}, "
            f"falling back to {fallback_model}"
        )


@dataclass
class PersistentRetryAdvice:
    """Returned by OverloadTracker when persistent mode wants to retry."""

    delay_s: float
    attempt: int


class OverloadTracker:
    """Tracks consecutive HTTP 529 (overloaded) errors for a single agent loop run.

    Instantiated per loop run in thread.agent_loop(). Not thread-safe (single async task).
    """

    def __init__(
        self,
        original_model: str,
        fallback_model: str | None,
        query_source: str = "foreground",
        threshold: int = OVERLOAD_CONSECUTIVE_THRESHOLD,
        persistent: bool = False,
    ):
        self.original_model = original_model
        self.fallback_model = fallback_model or ""
        self.query_source = query_source
        self.threshold = threshold
        self.persistent = persistent
        self.persistent_attempt: int = 0
        self.consecutive_529s: int = 0

    def is_foreground(self) -> bool:
        return self.query_source in FOREGROUND_QUERY_SOURCES

    def record_success(self) -> None:
        self.consecutive_529s = 0

    def record_error(self, exc: Exception) -> int | None:
        """Record an API error. Returns the HTTP status code. Raises FallbackTriggeredError when threshold is met."""
        code = get_status_code(exc)
        if code != OVERLOAD_STATUS:
            self.consecutive_529s = 0
            return code

        self.consecutive_529s += 1

        if not self.is_foreground() and not self.persistent:
            raise exc

        if not self.fallback_model:
            return code

        retry_after = get_retry_after(exc)
        if retry_after is not None and retry_after < PROMPT_CACHE_RETRY_CEILING:
            self.consecutive_529s -= 1
            return code

        if self.consecutive_529s >= self.threshold:
            raise FallbackTriggeredError(
                self.original_model,
                self.fallback_model,
                self.consecutive_529s,
            )
        return code

    def get_persistent_delay(
        self, exc: Exception, status_code: int | None = None
    ) -> PersistentRetryAdvice | None:
        """Return retry advice if persistent mode applies to this error, else None."""
        if not self.persistent:
            return None
        code = status_code if status_code is not None else get_status_code(exc)
        if code is None or code not in TRANSIENT_CODES:
            return None

        self.persistent_attempt += 1
        retry_after = get_retry_after(exc)
        if retry_after is not None:
            delay = min(retry_after, PERSISTENT_RESET_CAP_S)
        else:
            delay = backoff_delay(self.persistent_attempt, PERSISTENT_MAX_BACKOFF_S)
        return PersistentRetryAdvice(delay_s=delay, attempt=self.persistent_attempt)
