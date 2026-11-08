"""Structured error responses for the engine HTTP API.

Every error returned to the product layer has a consistent shape:
    {"error": {"code": "...", "message": "...", "retryable": bool, "details": {}}}

Error codes map to HTTP status codes. The product layer switches on `code`,
not on HTTP status — status codes are a transport convenience.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class ErrorCode(str, enum.Enum):
    # Auth
    INVALID_KEY = "invalid_key"
    KEY_NOT_CONFIGURED = "key_not_configured"

    # Engine state
    ENGINE_NOT_OPERATIONAL = "engine_not_operational"
    SUBSYSTEM_UNAVAILABLE = "subsystem_unavailable"

    # Execution
    EXECUTION_FAILED = "execution_failed"
    LLM_UNAVAILABLE = "llm_unavailable"
    TOOL_FAILED = "tool_failed"
    MEDIA_CONTENT_ERROR = "media_content_error"
    CONTEXT_OVERFLOW_UNRECOVERABLE = "context_overflow_unrecoverable"

    # User input
    INPUT_TIMEOUT = "input_timeout"
    INPUT_NOT_FOUND = "input_not_found"

    # Generic request validation (STATUS_TO_CODE's 422 target)
    INVALID_PARAMS = "invalid_params"

    # Rate limiting
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SUBSCRIPTION_SUSPENDED = "subscription_suspended"

    # Key rotation
    ROTATION_IN_PROGRESS = "rotation_in_progress"

    # Catch-all
    INTERNAL_ERROR = "internal_error"

    # Generic transport-level codes — used when an HTTP status has no
    # domain-specific code (unmatched-route 404s, ad-hoc HTTPExceptions).
    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    SERVICE_UNAVAILABLE = "service_unavailable"


# Maps error code → (HTTP status, retryable, default message)
ERROR_META: dict[ErrorCode, tuple[int, bool, str]] = {
    ErrorCode.INVALID_KEY: (401, False, "Invalid engine API key"),
    ErrorCode.KEY_NOT_CONFIGURED: (503, True, "Engine key not configured"),
    ErrorCode.ENGINE_NOT_OPERATIONAL: (503, True, "Engine not operational"),
    ErrorCode.SUBSYSTEM_UNAVAILABLE: (503, True, "Required subsystem not available"),
    ErrorCode.EXECUTION_FAILED: (500, False, "Execution failed"),
    ErrorCode.LLM_UNAVAILABLE: (502, True, "LLM provider unavailable"),
    ErrorCode.TOOL_FAILED: (500, False, "Tool execution failed"),
    ErrorCode.MEDIA_CONTENT_ERROR: (
        400,
        False,
        "Media content caused API failure after recovery attempts",
    ),
    ErrorCode.CONTEXT_OVERFLOW_UNRECOVERABLE: (
        500,
        False,
        "Context overflow could not be resolved — all compaction tiers exhausted",
    ),
    ErrorCode.INPUT_TIMEOUT: (408, False, "User input timed out"),
    ErrorCode.INPUT_NOT_FOUND: (404, False, "No pending input request found"),
    ErrorCode.INVALID_PARAMS: (422, False, "Invalid parameters"),
    ErrorCode.RATE_LIMITED: (429, True, "Rate limit exceeded"),
    ErrorCode.QUOTA_EXHAUSTED: (
        429,
        False,
        "Plan usage limit reached - upgrade your plan or wait for the window to reset",
    ),
    ErrorCode.BUDGET_EXHAUSTED: (
        402,
        False,
        "Pay-per-use budget reached - raise your budget or wait for the window to reset",
    ),
    ErrorCode.SUBSCRIPTION_SUSPENDED: (
        403,
        False,
        "Subscription suspended — contact your administrator",
    ),
    ErrorCode.ROTATION_IN_PROGRESS: (
        409,
        True,
        "A key rotation is already in progress — retry after the overlap window",
    ),
    ErrorCode.INTERNAL_ERROR: (500, False, "Internal engine error"),
    ErrorCode.BAD_REQUEST: (400, False, "Bad request"),
    ErrorCode.UNAUTHORIZED: (401, False, "Unauthorized"),
    ErrorCode.FORBIDDEN: (403, False, "Forbidden"),
    ErrorCode.NOT_FOUND: (404, False, "Not Found"),
    ErrorCode.CONFLICT: (409, False, "Conflict"),
    ErrorCode.SERVICE_UNAVAILABLE: (503, True, "Service unavailable"),
}


# Maps a transport-level HTTP status → the generic ErrorCode used when no
# domain-specific code applies (an unmatched-route 404, or an ad-hoc
# HTTPException raised with a plain-string detail). Statuses absent here fall
# back to INTERNAL_ERROR in engine_error_from_status().
STATUS_TO_CODE: dict[int, "ErrorCode"] = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.INVALID_PARAMS,
    429: ErrorCode.RATE_LIMITED,
    500: ErrorCode.INTERNAL_ERROR,
    503: ErrorCode.SERVICE_UNAVAILABLE,
}


@dataclass(eq=False)
class EngineError(Exception):
    """Structured error returned to the product layer.

    It is both the value error_from_exception() returns AND a raisable
    exception: handlers raise it directly and the EngineError exception
    handler renders {"error": {...}} at its http_status. eq=False keeps the
    normal exception identity semantics (hashable, no value-equality).
    """

    code: ErrorCode
    message: str = ""
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    http_status: int = 500

    def __post_init__(self) -> None:
        meta = ERROR_META.get(self.code)
        if meta:
            status, retryable, default_msg = meta
            if not self.message:
                self.message = default_msg
            self.retryable = retryable
            self.http_status = status
        # Seed Exception.args so str(exc) and tracebacks carry the message
        # (the dataclass __init__ otherwise leaves args empty).
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            body["details"] = self.details
        return {"error": body}


def error_from_exception(exc: Exception) -> EngineError:
    """Map a known exception to a structured EngineError.

    Unknown exceptions become INTERNAL_ERROR with a sanitized message.
    Full stack traces are logged server-side — never sent to the client.
    """
    # An EngineError raised directly is already the structured error — pass
    # it through unchanged (don't collapse it to INTERNAL_ERROR below).
    if isinstance(exc, EngineError):
        return exc

    from src.herald.provider import (
        CitadelBudgetExhausted,
        CitadelQuotaExhausted,
        CitadelSubscriptionSuspended,
    )

    if isinstance(exc, CitadelQuotaExhausted):
        return EngineError(
            code=ErrorCode.QUOTA_EXHAUSTED,
            message=str(exc),
            details={
                "used_tokens": exc.used_tokens,
                "max_tokens": exc.max_tokens,
                "window_seconds": exc.window_seconds,
            },
        )
    if isinstance(exc, CitadelBudgetExhausted):
        return EngineError(
            code=ErrorCode.BUDGET_EXHAUSTED,
            message=str(exc),
            details={
                "spent_usd": exc.spent_usd,
                "budget_usd": exc.budget_usd,
                "window_seconds": exc.window_seconds,
            },
        )
    if isinstance(exc, CitadelSubscriptionSuspended):
        return EngineError(
            code=ErrorCode.SUBSCRIPTION_SUSPENDED,
            message=str(exc),
            details={
                "subscription_id": exc.subscription_id,
                "status": exc.status,
            },
        )
    if isinstance(exc, asyncio.TimeoutError):
        return EngineError(code=ErrorCode.INPUT_TIMEOUT)

    # Sanitize: never expose raw exception messages to clients
    logger.exception("Unhandled exception mapped to INTERNAL_ERROR")
    return EngineError(code=ErrorCode.INTERNAL_ERROR)


def engine_error_from_status(status: int, message: str | None = None) -> EngineError:
    """Build a structured EngineError for a transport-level HTTP status that has
    no domain-specific code — e.g. an unmatched-route 404 raised by Starlette,
    or an ad-hoc ``HTTPException(detail="…")`` raised with a plain-string detail.

    Preserves *message* when given (so an ad-hoc "asset directory not ready"
    survives); otherwise the code's default message is used. The returned error
    always renders at *status*, even when *status* is outside ``STATUS_TO_CODE``
    (the code falls back to INTERNAL_ERROR but the original status is kept so the
    transport contract is never silently rewritten)."""
    code = STATUS_TO_CODE.get(status, ErrorCode.INTERNAL_ERROR)
    err = EngineError(code=code, message=message or "")
    err.http_status = status
    return err


# Needs asyncio for TimeoutError check above
import asyncio  # noqa: E402
