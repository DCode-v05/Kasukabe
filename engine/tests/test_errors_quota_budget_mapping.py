"""errors.py — the citadel quota/budget → EngineError mapping contract.

Two distinct caps with two distinct HTTP statuses:
  * token quota exhausted  → code ``quota_exhausted``  / HTTP 429
  * metered budget exhausted → code ``budget_exhausted`` / HTTP 402

This pins error_from_exception + the ERROR_META status table directly
(no mocks): the product layer switches on ``code``, but the HTTP status
is the transport contract the live badge / retry logic depends on.
"""

from __future__ import annotations

import pytest

from src.errors import ErrorCode, EngineError, error_from_exception
from src.herald.provider import (
    CitadelBudgetExhausted,
    CitadelQuotaExhausted,
    CitadelSubscriptionSuspended,
    maybe_raise_subscription_suspended,
)
from src.herald.transport import CitadelAPIError


def test_quota_exhausted_code_is_429():
    """QUOTA_EXHAUSTED is a 429 (flipped from 402 — budget owns 402)."""
    err = EngineError(code=ErrorCode.QUOTA_EXHAUSTED)
    assert err.http_status == 429
    assert err.retryable is False


def test_budget_exhausted_code_is_402():
    err = EngineError(code=ErrorCode.BUDGET_EXHAUSTED)
    assert err.http_status == 402
    assert err.retryable is False


def test_citadel_quota_exhausted_maps_to_429():
    exc = CitadelQuotaExhausted(
        "used 1000/500 tokens in last 2592000s",
        used_tokens=1000,
        max_tokens=500,
        window_seconds=2592000,
    )
    err = error_from_exception(exc)
    assert err.code == ErrorCode.QUOTA_EXHAUSTED
    assert err.http_status == 429
    assert err.details == {
        "used_tokens": 1000,
        "max_tokens": 500,
        "window_seconds": 2592000,
    }
    assert err.to_dict()["error"]["code"] == "quota_exhausted"


def test_citadel_budget_exhausted_maps_to_402():
    exc = CitadelBudgetExhausted(
        "spent 12.50/10.00 usd in last 2592000s",
        spent_usd=12.5,
        budget_usd=10.0,
        window_seconds=2592000,
    )
    err = error_from_exception(exc)
    assert err.code == ErrorCode.BUDGET_EXHAUSTED
    assert err.http_status == 402
    assert err.retryable is False
    assert err.details == {
        "spent_usd": 12.5,
        "budget_usd": 10.0,
        "window_seconds": 2592000,
    }
    body = err.to_dict()
    assert body["error"]["code"] == "budget_exhausted"
    assert body["error"]["details"]["spent_usd"] == 12.5


def test_subscription_suspended_code_is_403():
    err = EngineError(code=ErrorCode.SUBSCRIPTION_SUSPENDED)
    assert err.http_status == 403
    assert err.retryable is False


def test_maybe_raise_subscription_suspended_raises_on_403():
    """Citadel 403 + SUBSCRIPTION_SUSPENDED → CitadelSubscriptionSuspended."""
    api_exc = CitadelAPIError(
        403,
        body={
            "error": {
                "code": "SUBSCRIPTION_SUSPENDED",
                "message": "subscription is suspended",
                "subscription_id": "sub_123",
                "status": "suspended",
            }
        },
    )
    with pytest.raises(CitadelSubscriptionSuspended) as ei:
        maybe_raise_subscription_suspended(api_exc)
    assert ei.value.subscription_id == "sub_123"
    assert ei.value.status == "suspended"


def test_citadel_subscription_suspended_maps_to_403():
    exc = CitadelSubscriptionSuspended(
        "subscription is suspended",
        subscription_id="sub_123",
        status="suspended",
    )
    err = error_from_exception(exc)
    assert err.code == ErrorCode.SUBSCRIPTION_SUSPENDED
    assert err.http_status == 403
    assert err.retryable is False
    assert err.details == {
        "subscription_id": "sub_123",
        "status": "suspended",
    }
    assert err.to_dict()["error"]["code"] == "subscription_suspended"
