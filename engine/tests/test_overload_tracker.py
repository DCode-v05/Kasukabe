"""Tests for OverloadTracker and FallbackTriggeredError."""

from __future__ import annotations

import pytest

from src.herald.retry import (
    FallbackTriggeredError,
    OverloadTracker,
    PersistentRetryAdvice,
    PERSISTENT_MAX_BACKOFF_S,
    PERSISTENT_RESET_CAP_S,
)


class FakeAPIError(Exception):
    """Minimal fake with status_code and retry-after header for testing."""

    def __init__(self, status_code: int, retry_after: float | None = None):
        self.status_code = status_code
        headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
        self.response = type("R", (), {"status_code": status_code, "headers": headers})()
        super().__init__(f"HTTP {status_code}")


def make_tracker(**kwargs) -> OverloadTracker:
    defaults = dict(
        original_model="claude-opus", fallback_model="claude-sonnet", query_source="foreground"
    )
    defaults.update(kwargs)
    return OverloadTracker(**defaults)


# --- Consecutive 529 counter ---


def test_consecutive_529_count_increments_only_on_529():
    tracker = make_tracker(fallback_model=None)
    tracker.record_error(FakeAPIError(529, retry_after=30.0))
    assert tracker.consecutive_529s == 1
    tracker.record_error(FakeAPIError(529, retry_after=30.0))
    assert tracker.consecutive_529s == 2
    tracker.record_error(FakeAPIError(500))
    assert tracker.consecutive_529s == 0


def test_counter_resets_on_success():
    tracker = make_tracker(fallback_model=None)
    tracker.record_error(FakeAPIError(529, retry_after=30.0))
    tracker.record_error(FakeAPIError(529, retry_after=30.0))
    assert tracker.consecutive_529s == 2
    tracker.record_success()
    assert tracker.consecutive_529s == 0


# --- Fallback triggering ---


def test_fallback_triggered_after_3_consecutive_529s():
    tracker = make_tracker()
    tracker.record_error(FakeAPIError(529, retry_after=30.0))
    tracker.record_error(FakeAPIError(529, retry_after=30.0))
    with pytest.raises(FallbackTriggeredError) as exc_info:
        tracker.record_error(FakeAPIError(529, retry_after=30.0))
    assert exc_info.value.original_model == "claude-opus"
    assert exc_info.value.fallback_model == "claude-sonnet"
    assert exc_info.value.consecutive_count == 3


def test_no_fallback_when_fallback_model_is_none():
    tracker = make_tracker(fallback_model=None)
    for _ in range(10):
        tracker.record_error(FakeAPIError(529, retry_after=30.0))
    assert tracker.consecutive_529s == 10


def test_short_retry_after_not_counted():
    tracker = make_tracker()
    tracker.record_error(FakeAPIError(529, retry_after=5.0))
    tracker.record_error(FakeAPIError(529, retry_after=5.0))
    tracker.record_error(FakeAPIError(529, retry_after=5.0))
    assert tracker.consecutive_529s == 0


def test_long_retry_after_counted():
    tracker = make_tracker()
    tracker.record_error(FakeAPIError(529, retry_after=25.0))
    tracker.record_error(FakeAPIError(529, retry_after=25.0))
    with pytest.raises(FallbackTriggeredError):
        tracker.record_error(FakeAPIError(529, retry_after=25.0))


def test_fallback_error_carries_metadata():
    tracker = make_tracker(original_model="opus-4", fallback_model="sonnet-4")
    for _ in range(2):
        tracker.record_error(FakeAPIError(529, retry_after=30.0))
    with pytest.raises(FallbackTriggeredError) as exc_info:
        tracker.record_error(FakeAPIError(529, retry_after=30.0))
    err = exc_info.value
    assert err.original_model == "opus-4"
    assert err.fallback_model == "sonnet-4"
    assert err.consecutive_count == 3
    assert "opus-4" in str(err)
    assert "sonnet-4" in str(err)


# --- Query source gating ---


def test_query_source_foreground_retries_529():
    tracker = make_tracker(query_source="foreground", fallback_model=None)
    tracker.record_error(FakeAPIError(529, retry_after=30.0))
    assert tracker.consecutive_529s == 1


def test_query_source_background_bails_on_529():
    tracker = make_tracker(query_source="summary")
    with pytest.raises(FakeAPIError):
        tracker.record_error(FakeAPIError(529))


def test_query_source_agent_is_foreground():
    tracker = make_tracker(query_source="agent", fallback_model=None)
    tracker.record_error(FakeAPIError(529, retry_after=30.0))
    assert tracker.consecutive_529s == 1


def test_query_source_signal_is_background():
    tracker = make_tracker(query_source="signal")
    with pytest.raises(FakeAPIError):
        tracker.record_error(FakeAPIError(529))


# --- Persistent retry mode ---


def test_persistent_mode_returns_retry_advice():
    tracker = make_tracker(persistent=True, fallback_model=None)
    advice = tracker.get_persistent_delay(FakeAPIError(529))
    assert isinstance(advice, PersistentRetryAdvice)
    assert advice.delay_s > 0
    assert advice.attempt == 1


def test_persistent_mode_backoff_caps_at_5min():
    tracker = make_tracker(persistent=True, fallback_model=None)
    for _ in range(20):
        advice = tracker.get_persistent_delay(FakeAPIError(529))
    assert advice.delay_s <= PERSISTENT_MAX_BACKOFF_S


def test_persistent_mode_not_triggered_for_non_transient():
    tracker = make_tracker(persistent=True, fallback_model=None)
    advice = tracker.get_persistent_delay(FakeAPIError(500))
    assert advice is None


def test_persistent_mode_handles_429():
    tracker = make_tracker(persistent=True, fallback_model=None)
    advice = tracker.get_persistent_delay(FakeAPIError(429))
    assert isinstance(advice, PersistentRetryAdvice)
    assert advice.delay_s > 0


def test_persistent_mode_respects_retry_after_with_cap():
    tracker = make_tracker(persistent=True, fallback_model=None)
    advice = tracker.get_persistent_delay(FakeAPIError(529, retry_after=100_000.0))
    assert advice.delay_s == PERSISTENT_RESET_CAP_S


def test_persistent_mode_not_active_when_disabled():
    tracker = make_tracker(persistent=False, fallback_model=None)
    advice = tracker.get_persistent_delay(FakeAPIError(529))
    assert advice is None


def test_persistent_background_does_not_bail():
    tracker = make_tracker(query_source="summary", persistent=True, fallback_model=None)
    tracker.record_error(FakeAPIError(529, retry_after=30.0))
    assert tracker.consecutive_529s == 1
