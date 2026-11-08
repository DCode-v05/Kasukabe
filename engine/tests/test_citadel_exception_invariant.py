"""The load-bearing absence: typed citadel exceptions carry no status_code.

The agent loop classifies "is this retryable?" by reading an HTTP status off the
exception (``retry.get_status_code`` → ``OverloadTracker.get_persistent_delay``).
A raw ``CitadelAPIError`` exposes ``status_code`` (429/529 → TRANSIENT_CODES), so
the persistent loop can back off and re-try it. The three TERMINAL conditions —
quota exhausted, budget exhausted, subscription suspended — are surfaced as
DISTINCT typed exceptions precisely so that this status_code probe returns None
and the loop cannot retry them.

If a future refactor unified these onto a base that re-exposed ``status_code``
(e.g. subclassing CitadelAPIError, or carrying the originating 429), the engine
would silently re-enable a 6-hour persistent-retry loop on quota exhaustion —
hammering a citadel key that will only ever 429 until the window resets. These
tests lock the invariant: no status_code attribute, and the retry classifier
treats the typed exceptions as non-retryable.
"""

from __future__ import annotations

import pytest

from src.herald.provider import (
    CitadelBudgetExhausted,
    CitadelQuotaExhausted,
    CitadelSubscriptionSuspended,
)
from src.herald.retry import OverloadTracker, get_status_code


TYPED_TERMINALS = [
    CitadelQuotaExhausted(
        "used 1000/500", used_tokens=1000, max_tokens=500, window_seconds=2592000
    ),
    CitadelBudgetExhausted(
        "spent 12.50/10.00", spent_usd=12.5, budget_usd=10.0, window_seconds=2592000
    ),
    CitadelSubscriptionSuspended("suspended", subscription_id="sub_123", status="suspended"),
]


@pytest.mark.parametrize("exc", TYPED_TERMINALS, ids=lambda e: type(e).__name__)
def test_typed_terminal_has_no_status_code_attribute(exc):
    """No ``status_code`` attribute at all — not 0, not None-valued, ABSENT.

    ``hasattr`` is the precise probe: a refactor that adds ``status_code = None``
    would still let ``getattr(exc, 'status_code', None)`` pass, but it would
    signal intent drift, so we forbid the attribute outright."""
    assert not hasattr(exc, "status_code")


@pytest.mark.parametrize("exc", TYPED_TERMINALS, ids=lambda e: type(e).__name__)
def test_typed_terminal_has_no_response_status_code(exc):
    """The retry layer also falls back to ``exc.response.status_code``. A typed
    terminal must expose neither path, or the fallback would resurrect a code."""
    assert not hasattr(exc, "response")
    assert get_status_code(exc) is None


@pytest.mark.parametrize("exc", TYPED_TERMINALS, ids=lambda e: type(e).__name__)
def test_typed_terminal_is_not_persistent_retryable(exc):
    """End of the chain: with no status_code, ``get_persistent_delay`` returns
    None even under persistent=True — the loop cannot enter a doomed retry."""
    tracker = OverloadTracker(
        original_model="claude-opus",
        fallback_model=None,
        query_source="foreground",
        persistent=True,
    )
    assert tracker.get_persistent_delay(exc) is None


def test_raw_citadel_api_error_DOES_expose_status_code():
    """Contrast / guardrail: the RAW transport error still carries status_code,
    so transient 429/529 keep their existing retryable classification. This is
    what the typed terminals deliberately diverge from — proving the divergence
    is real, not an artifact of the test exceptions being toothless."""
    from src.herald.transport import CitadelAPIError

    raw = CitadelAPIError(status_code=429, body=None, headers={})
    assert raw.status_code == 429
    assert get_status_code(raw) == 429
