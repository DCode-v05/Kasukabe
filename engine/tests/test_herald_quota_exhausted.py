"""Engine short-circuits citadel quota/budget rejections into typed errors.

Citadel returns ``429 + body.error.code == "QUOTA_EXCEEDED"`` when a
subscription has burned through its plan-tier token allotment, and
``402 + body.error.code == "BUDGET_EXCEEDED"`` when a metered subscription's
spend hits its dollar budget cap. The transport raises ``CitadelAPIError``;
the helpers re-raise as ``CitadelQuotaExhausted`` / ``CitadelBudgetExhausted``
so the agent loop can surface an upgrade-plan / raise-budget prompt instead of
treating it as a transient rate limit.
"""

from __future__ import annotations

import pytest

from src.herald.transport import CitadelAPIError
from src.herald.provider import (
    CitadelBudgetExhausted,
    CitadelQuotaExhausted,
    maybe_raise_budget_exhausted,
    maybe_raise_quota_exhausted,
)
from src.herald import quota_cooldown


@pytest.fixture(autouse=True)
def clean_cooldown():
    """maybe_raise_quota_exhausted now arms the process-global quota cooldown.
    Reset it around each case so a converted 429 here cannot leak into other
    tests (e.g. the LC scheduler gate that skips batches while cooled down)."""
    quota_cooldown.reset()
    yield
    quota_cooldown.reset()


def build_429(body: dict | None, headers: dict | None = None) -> CitadelAPIError:
    """Construct a ``CitadelAPIError`` (429) for the helper to inspect."""
    return CitadelAPIError(status_code=429, body=body, headers=headers or {})


def build_402(body: dict | None) -> CitadelAPIError:
    """Construct a ``CitadelAPIError`` (402) for the budget helper to inspect."""
    return CitadelAPIError(status_code=402, body=body, headers={})


def test_quota_exceeded_body_raises_typed_error():
    exc = build_429(
        {
            "error": {
                "code": "QUOTA_EXCEEDED",
                "message": "engine eng-x used 1000/500 tokens in last 2592000s",
                "used_tokens": 1000,
                "max_tokens": 500,
                "window_seconds": 2592000,
            },
        }
    )
    with pytest.raises(CitadelQuotaExhausted) as exc_info:
        maybe_raise_quota_exhausted(exc)
    err = exc_info.value
    assert err.used_tokens == 1000
    assert err.max_tokens == 500
    assert err.window_seconds == 2592000
    assert "1000/500" in str(err)


def test_other_429_codes_pass_through_untouched():
    """Retry is off for quota ONLY: a 429 with a non-quota code stays a
    transient ``CitadelAPIError`` so the SDK retry + the engine's existing
    529/transient handling still apply to it."""
    exc = build_429(
        {
            "error": {"code": "PROVIDER_RATE_LIMITED", "message": "upstream busy"},
        }
    )
    # Should return without raising
    maybe_raise_quota_exhausted(exc)


def test_429_with_retry_after_passes_through():
    """A Retry-After header changes nothing — non-quota 429s pass through with
    or without it; only the QUOTA_EXCEEDED body code converts."""
    exc = build_429(
        {"error": {"code": "PROVIDER_RATE_LIMITED", "message": "upstream busy"}},
        headers={"retry-after": "12"},
    )
    # Should return without raising — stays a transient CitadelAPIError.
    maybe_raise_quota_exhausted(exc)


def test_429_with_no_body_passes_through():
    """A body-less 429 carries no quota signal — citadel always labels quota
    429s with QUOTA_EXCEEDED, so this is not quota and keeps its retries."""
    exc = build_429(None)
    maybe_raise_quota_exhausted(exc)


def test_429_with_non_dict_body_passes_through():
    """An opaque (non-JSON) 429 body is likewise not a quota signal — pass
    through, retry machinery stays on."""
    exc = build_429("opaque html error page")  # type: ignore[arg-type]
    maybe_raise_quota_exhausted(exc)


def test_non_429_exception_passes_through():
    """Helper only inspects 429s; other 4xx/5xx flow through untouched."""
    exc = CitadelAPIError(
        status_code=503,
        body={"error": {"code": "QUOTA_EXCEEDED"}},
        headers={},
    )
    # Even a 503 with QUOTA_EXCEEDED in body shouldn't trip the helper —
    # quota is by definition a 429 contract.
    maybe_raise_quota_exhausted(exc)


def test_quota_exhausted_maps_to_429_engine_error():
    """``CitadelQuotaExhausted`` must surface as a structured 429 to BAP.

    BAP front-end keys off ``error.code == "quota_exhausted"`` to render
    upgrade-plan UI; ``details`` carries used/max/window for display. The
    token quota cap is a 429 (BUDGET_EXCEEDED 402 is the metered counterpart).
    """
    from src.errors import ErrorCode, error_from_exception

    exc = CitadelQuotaExhausted(
        "engine eng-x used 1000/500 tokens in last 2592000s",
        used_tokens=1000,
        max_tokens=500,
        window_seconds=2592000,
    )
    err = error_from_exception(exc)
    assert err.code == ErrorCode.QUOTA_EXHAUSTED
    assert err.http_status == 429
    assert err.retryable is False
    assert err.details == {
        "used_tokens": 1000,
        "max_tokens": 500,
        "window_seconds": 2592000,
    }
    body = err.to_dict()
    assert body["error"]["code"] == "quota_exhausted"
    assert body["error"]["details"]["max_tokens"] == 500


# ── Metered budget: 402 + BUDGET_EXCEEDED → CitadelBudgetExhausted ─────


def test_budget_exceeded_body_raises_typed_error():
    """Citadel returns ``402 + body.error.code == "BUDGET_EXCEEDED"`` when a
    metered subscription's spend over the budget window hits the cap. The
    helper re-raises as ``CitadelBudgetExhausted`` so the agent loop can
    surface a raise-budget prompt instead of treating it as transient."""
    exc = build_402(
        {
            "error": {
                "code": "BUDGET_EXCEEDED",
                "message": "sub sub-x spent 12.50/10.00 usd in last 2592000s",
                "spent_usd": 12.5,
                "budget_usd": 10.0,
                "window_seconds": 2592000,
            },
        }
    )
    with pytest.raises(CitadelBudgetExhausted) as exc_info:
        maybe_raise_budget_exhausted(exc)
    err = exc_info.value
    assert err.spent_usd == 12.5
    assert err.budget_usd == 10.0
    assert err.window_seconds == 2592000
    assert "12.50/10.00" in str(err)


def test_other_402_codes_pass_through_untouched():
    """Generic 402s (no BUDGET_EXCEEDED code) must not be re-raised."""
    exc = build_402(
        {
            "error": {"code": "PAYMENT_REQUIRED", "message": "card declined"},
        }
    )
    # Should return without raising
    maybe_raise_budget_exhausted(exc)


def test_402_with_no_body_passes_through():
    exc = build_402(None)
    maybe_raise_budget_exhausted(exc)


def test_402_with_non_dict_body_passes_through():
    exc = build_402("opaque html error page")  # type: ignore[arg-type]
    maybe_raise_budget_exhausted(exc)


def test_non_402_exception_passes_through_budget_helper():
    """Helper only inspects 402s; other 4xx/5xx flow through untouched."""
    exc = CitadelAPIError(
        status_code=503,
        body={"error": {"code": "BUDGET_EXCEEDED"}},
        headers={},
    )
    # Even a 503 with BUDGET_EXCEEDED in body shouldn't trip the helper —
    # budget is by definition a 402 contract.
    maybe_raise_budget_exhausted(exc)


def test_budget_exhausted_maps_to_402_engine_error():
    """``CitadelBudgetExhausted`` must surface as a structured 402 to BAP.

    BAP front-end keys off ``error.code == "budget_exhausted"`` to render
    raise-budget UI; ``details`` carries spent/budget/window for display.
    """
    from src.errors import ErrorCode, error_from_exception

    exc = CitadelBudgetExhausted(
        "sub sub-x spent 12.50/10.00 usd in last 2592000s",
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
    assert body["error"]["details"]["budget_usd"] == 10.0
