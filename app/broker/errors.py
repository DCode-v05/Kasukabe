"""Broker error hierarchy - one exception per HTTP outcome, one renderer.

Every error the broker can raise while handling POST /v1/messages carries
its own HTTP status code and maps to the Anthropic Messages API's error
body shape (``{"type": "error", "error": {"type": ..., "message": ...}}``)
so a herald that only understands the Anthropic wire format still gets a
body it can parse - it just sees an error instead of a message. See
DESIGN.md section 9 ("hard-fails any model outside the allowlist") and
the broker TASK spec (403 for the allowlist, wallet refusal, upstream
failure).
"""

from __future__ import annotations


class BrokerError(Exception):
    """Base for every error the broker's request pipeline can raise.

    ``status_code`` and ``anthropic_error_type`` are class attributes so
    every subclass gets a correct HTTP response for free; ``message`` is
    the one thing each raise site actually supplies.
    """

    status_code: int = 500
    anthropic_error_type: str = "api_error"
    failed_at: str = "broker-internal"

    def __init__(self, message: str, *, detail: "dict[str, object] | None" = None) -> None:
        self.message = message
        self.detail = detail
        super().__init__(message)

    def to_anthropic_body(self) -> dict[str, object]:
        """Render this error in the Anthropic Messages API's error shape."""
        return {
            "type": "error",
            "error": {"type": self.anthropic_error_type, "message": self.message},
        }


class AuthenticationError(BrokerError):
    """No agent id could be extracted from the request's auth header, or
    the signature (when AUTH_SHARED_SECRET is set) did not verify."""

    status_code = 401
    failed_at = "broker-internal"
    anthropic_error_type = "authentication_error"


class ModelNotAllowedError(BrokerError):
    """The requested model id is not exactly one of MODEL_ALLOWLIST.

    DESIGN.md section 9: "Hard-fails any model outside the allowlist."
    403, not 400 - this is an authorization boundary (broker's ONE job),
    not a malformed request.
    """

    status_code = 403
    failed_at = "broker-internal"
    anthropic_error_type = "permission_error"


class InvalidRequestError(BrokerError):
    """The request body does not parse as an Anthropic Messages request,
    or a content block inside it is a shape translate.py doesn't know how
    to carry to Bedrock converse."""

    status_code = 400
    failed_at = "broker-internal"
    anthropic_error_type = "invalid_request_error"


class InsufficientFundsError(BrokerError):
    """Pre-dispatch wallet check: the payer's balance (the agent's own,
    or - for a minor - the parent's, per DESIGN.md section 4) can't cover
    even a worst-case estimate of this call's cost. The call is never
    dispatched to Bedrock, so no money moves and no trace of an attempt
    is billed - see broker/service.py for why this is a hard refusal and
    not a partial-request truncation."""

    status_code = 402
    failed_at = "ledger"
    anthropic_error_type = "insufficient_funds_error"


class UnknownAgentError(BrokerError):
    """The auth header names an agent id the ledger has never heard of."""

    status_code = 404
    failed_at = "ledger"
    anthropic_error_type = "not_found_error"


class UpstreamOverloadedError(BrokerError):
    """The PROVIDER rejected the call for capacity reasons - Bedrock's
    ThrottlingException and friends, or OpenAI's 429. Distinct from
    ModelNotAllowedError: the model IS allowed, the provider just cannot serve
    it right now. Retried by the caller's own retry loop (herald has one),
    broker does not retry on its behalf."""

    status_code = 529
    failed_at = "upstream"
    anthropic_error_type = "overloaded_error"


class UpstreamError(BrokerError):
    """Any other non-2xx from the provider, translated to a 502: broker is a
    gateway and this is a bad-gateway condition from broker's client's point
    of view."""

    status_code = 502
    failed_at = "upstream"
    anthropic_error_type = "api_error"


class LedgerUnavailableError(BrokerError):
    """World's ledger could not be reached, or answered with something this
    broker cannot use.

    THE error this session cost a day to. Without it, an unreachable ledger
    raised a bare `httpx.HTTPStatusError` that no handler here recognised, so
    it fell through to app.py's catch-all and was reported as a generic
    internal error - indistinguishable, in the trace, from Bedrock failing.
    Every dashboard read it as an upstream problem and pointed at AWS.

    502 because it IS an upstream failure from the caller's point of view: the
    broker could not complete the call for a reason that is not the caller's
    fault. `failed_at` is what separates it from a Bedrock 502 - same status to
    the caller, entirely different thing to fix.
    """

    status_code = 502
    failed_at = "ledger"


class LedgerHaltedError(BrokerError):
    """World's ledger has HALTED. Not the same thing as unreachable.

    INFRA-25. A halted ledger answers 409, and `HttpLedgerClient` mapped
    every non-success alike into `LedgerUnavailableError` - a 502 that
    reads, in the trace and on every dashboard, as "the world is down".
    It is not down. It stopped itself, deliberately, because its $1000
    invariant was violated, and the thing to do about that is read the
    ledger's halt reason rather than restart the world.

    An operator who cannot tell "the world crashed" from "the world
    refused" spends the outage looking in the wrong place, which is
    precisely the failure `LedgerUnavailableError`'s own docstring was
    written about.

    503 rather than 502: this is a refusal to serve, not a failure to
    reach, and herald backs off on both.
    """

    status_code = 503
    failed_at = "ledger"


class SpendCapExceededError(BrokerError):
    """broker/spend.py's process-wide hard USD cap has been reached
    (TASK spec, broker-lanes track: "at 100% stop dispatch"). Every call
    refused this way never reaches the wallet check or a lane, let alone
    Bedrock - see spend.py's module docstring for why the cap can only be
    confirmed crossed AFTER the call that crossed it has already been
    billed (it is computed from real response usage, never an estimate),
    which means this error stops every call AFTER that one, not that one
    itself. 429/rate_limit_error, not 402/insufficient_funds_error: this
    is not about any one payer's wallet, it is a global refusal - and
    herald already knows how to back off on a rate-limit-shaped error."""

    status_code = 429
    failed_at = "broker-internal"
    anthropic_error_type = "rate_limit_error"
