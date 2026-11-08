"""The 429 retry contract: retry is off for quota exhaustion ONLY.

Citadel always labels a quota-driven 429 with the stable body code
``QUOTA_EXCEEDED`` — that shape is guaranteed on the gateway side. So the
engine's classification is exact, never a guess:

- ``429 + QUOTA_EXCEEDED`` → converted to the typed ``CitadelQuotaExhausted``,
  which carries no ``status_code`` and therefore can never be retried, in any
  retry mode. Quota will not recover before its window resets; retrying is
  doomed by definition.
- EVERY other 429 — provider rate limits, opaque intermediary responses,
  body-less errors, with or without ``Retry-After`` — passes through as the
  raw transient ``CitadelAPIError`` and keeps the engine's normal
  transient/persistent retry machinery. Retry stays on in general.

These build the same ``CitadelAPIError`` the transport raises and assert the
conversion + the persistent-retry split end to end — no mocks.
"""

from __future__ import annotations

import pytest

from src.herald.transport import CitadelAPIError
from src.herald.provider import (
    CitadelQuotaExhausted,
    maybe_raise_quota_exhausted,
)
from src.herald.retry import OverloadTracker, get_status_code


def build_429(body: dict | None = None, headers: dict | None = None) -> CitadelAPIError:
    """A 429 as the transport raises it, with optional parsed body + headers."""
    return CitadelAPIError(status_code=429, body=body, headers=headers or {})


# ── The conversion: only the QUOTA_EXCEEDED label is terminal ───────────────


def test_quota_exceeded_code_is_terminal_regardless_of_retry_after():
    """An explicit QUOTA_EXCEEDED code is terminal even if a Retry-After header
    is present — quota won't recover before the window resets."""
    exc = build_429(
        body={"error": {"code": "QUOTA_EXCEEDED", "message": "engine used 1000/500"}},
        headers={"retry-after": "30"},
    )
    with pytest.raises(CitadelQuotaExhausted):
        maybe_raise_quota_exhausted(exc)


def test_bare_429_no_body_passes_through():
    """No body means no quota label — not quota, so the 429 stays a raw
    transient ``CitadelAPIError`` and keeps its retries."""
    exc = build_429(body=None, headers={})
    # Must return without raising.
    maybe_raise_quota_exhausted(exc)


def test_unrecognized_code_passes_through():
    """A non-quota body code (e.g. an upstream rate limit) is not quota —
    pass through, retry machinery stays on."""
    exc = build_429(
        body={"error": {"code": "RATE_LIMIT", "message": "slow down"}},
        headers={},
    )
    maybe_raise_quota_exhausted(exc)


def test_429_with_retry_after_passes_through():
    """Retry-After changes nothing about classification — still not quota,
    still transient."""
    exc = build_429(body=None, headers={"retry-after": "12"})
    maybe_raise_quota_exhausted(exc)


# ── The persistent-retry split the conversion protects ─────────────────────


def test_quota_exhausted_never_persistent_retries():
    """The typed terminal exception carries no status_code, so the loop's
    get_persistent_delay cannot persistent-retry it even with persistent=True.
    This is what keeps quota exhaustion out of the 6h loop."""
    tracker = OverloadTracker(
        original_model="claude-opus",
        fallback_model=None,
        query_source="foreground",
        persistent=True,
    )
    exc = build_429(body={"error": {"code": "QUOTA_EXCEEDED", "message": "engine used 1000/500"}})
    with pytest.raises(CitadelQuotaExhausted) as info:
        maybe_raise_quota_exhausted(exc)

    assert get_status_code(info.value) is None
    assert tracker.get_persistent_delay(info.value) is None


def test_bare_429_still_persistent_retries():
    """The other half of the split: a non-quota 429 stays transient and, under
    persistent mode, DOES yield retry advice — retry is on in general."""
    tracker = OverloadTracker(
        original_model="claude-opus",
        fallback_model=None,
        query_source="foreground",
        persistent=True,
    )
    exc = build_429(body=None, headers={})
    # Not converted — remains the raw transient 429.
    maybe_raise_quota_exhausted(exc)
    assert tracker.get_persistent_delay(exc) is not None


def test_429_with_retry_after_honors_server_delay():
    """A Retry-After-bearing 429 retries on the server's schedule."""
    tracker = OverloadTracker(
        original_model="claude-opus",
        fallback_model=None,
        query_source="foreground",
        persistent=True,
    )
    exc = build_429(body=None, headers={"retry-after": "12"})
    maybe_raise_quota_exhausted(exc)
    advice = tracker.get_persistent_delay(exc)
    assert advice is not None
    assert advice.delay_s == 12  # honors the server-provided Retry-After
