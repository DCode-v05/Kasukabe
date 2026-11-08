"""Tests for CancellationToken, AbortReason, child tokens, and bubble tokens."""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.engine_core.cancellation import (
    AbortReason,
    CancellationToken,
    CombinedCancellationToken,
    create_bubble_token,
    create_child_token,
)


def test_initial_state():
    token = CancellationToken()
    assert not token.cancelled
    assert token.reason == ""
    assert token.abort_reason is None


def test_cancel():
    token = CancellationToken()
    token.cancel("test reason")
    assert token.cancelled
    assert token.reason == "test reason"


def test_cancel_with_abort_reason():
    token = CancellationToken()
    token.cancel("sibling broke", AbortReason.SIBLING_ERROR)
    assert token.cancelled
    assert token.reason == "sibling broke"
    assert token.abort_reason == AbortReason.SIBLING_ERROR


def test_cancel_idempotent():
    token = CancellationToken()
    token.cancel("first", AbortReason.SIBLING_ERROR)
    token.cancel("second", AbortReason.USER_INTERRUPTED)
    assert token.reason == "first"
    assert token.abort_reason == AbortReason.SIBLING_ERROR


def test_on_cancel_fires():
    token = CancellationToken()
    called = []
    token.on_cancel(lambda: called.append(1))
    token.on_cancel(lambda: called.append(2))
    token.cancel("go")
    assert called == [1, 2]


def test_on_cancel_fires_immediately_if_already_cancelled():
    token = CancellationToken()
    token.cancel("already")
    called = []
    token.on_cancel(lambda: called.append(1))
    assert called == [1]


def test_callbacks_cleared_after_cancel():
    token = CancellationToken()
    called = []
    token.on_cancel(lambda: called.append(1))
    token.cancel("go")
    assert len(called) == 1
    assert len(token.callbacks) == 0


def test_child_token_propagates():
    parent = CancellationToken()
    child = create_child_token(parent)
    assert not child.cancelled
    parent.cancel("parent cancelled", AbortReason.USER_INTERRUPTED)
    assert child.cancelled
    assert child.reason == "parent cancelled"
    assert child.abort_reason == AbortReason.USER_INTERRUPTED


def test_child_does_not_propagate_to_parent():
    parent = CancellationToken()
    child = create_child_token(parent)
    child.cancel("child only")
    assert not parent.cancelled


def test_child_of_already_cancelled_parent():
    parent = CancellationToken()
    parent.cancel("already")
    child = create_child_token(parent)
    assert child.cancelled
    assert child.reason == "already"


def test_bubble_token_propagates_parent_to_child():
    parent = CancellationToken()
    child = create_bubble_token(parent)
    parent.cancel("parent", AbortReason.USER_INTERRUPTED)
    assert child.cancelled
    assert child.abort_reason == AbortReason.USER_INTERRUPTED


def test_bubble_token_propagates_child_to_parent():
    parent = CancellationToken()
    child = create_bubble_token(parent)
    child.cancel("permission denied", AbortReason.USER_INTERRUPTED)
    assert parent.cancelled
    assert parent.reason == "permission denied"


def test_bubble_token_sibling_error_does_not_bubble_up():
    parent = CancellationToken()
    child = create_bubble_token(parent)
    child.cancel("bash broke", AbortReason.SIBLING_ERROR)
    assert child.cancelled
    assert not parent.cancelled


def test_bubble_token_no_reason_bubbles_up():
    parent = CancellationToken()
    child = create_bubble_token(parent)
    child.cancel("generic cancel")
    assert parent.cancelled
    assert parent.reason == "generic cancel"


def test_bubble_no_double_cancel():
    parent = CancellationToken()
    parent.cancel("already gone")
    child = create_bubble_token(parent)
    assert child.cancelled
    child.cancel("too late")


def test_abort_reason_values():
    assert AbortReason.SIBLING_ERROR == "sibling_error"
    assert AbortReason.USER_INTERRUPTED == "user_interrupted"
    assert AbortReason.STREAMING_FALLBACK == "streaming_fallback"


def test_abort_reason_has_timeout():
    assert AbortReason.TIMEOUT == "timeout"
    values = {r.value for r in AbortReason}
    assert len(values) == 4


@pytest.mark.asyncio
async def test_combined_token_fires_on_parent():
    parent = CancellationToken()
    combined = CombinedCancellationToken(parent=parent)
    assert not combined.cancelled
    parent.cancel("parent done", AbortReason.USER_INTERRUPTED)
    assert combined.cancelled
    assert combined.reason == "parent done"
    assert combined.abort_reason == AbortReason.USER_INTERRUPTED
    combined.cleanup()


@pytest.mark.asyncio
async def test_combined_token_fires_on_sibling_with_default_reason():
    sibling = CancellationToken()
    combined = CombinedCancellationToken(sibling=sibling)
    sibling.cancel("bash exploded")
    assert combined.cancelled
    assert combined.reason == "bash exploded"
    assert combined.abort_reason == AbortReason.SIBLING_ERROR
    combined.cleanup()


@pytest.mark.asyncio
async def test_combined_token_fires_on_sibling_preserves_explicit_reason():
    sibling = CancellationToken()
    combined = CombinedCancellationToken(sibling=sibling)
    sibling.cancel("custom", AbortReason.USER_INTERRUPTED)
    assert combined.abort_reason == AbortReason.USER_INTERRUPTED
    combined.cleanup()


@pytest.mark.asyncio
async def test_combined_token_fires_on_timeout():
    combined = CombinedCancellationToken(timeout_ms=30)
    assert not combined.cancelled
    await asyncio.sleep(0.08)
    assert combined.cancelled
    assert combined.abort_reason == AbortReason.TIMEOUT
    assert "Timeout" in combined.reason
    combined.cleanup()


@pytest.mark.asyncio
async def test_combined_token_first_source_wins():
    parent = CancellationToken()
    combined = CombinedCancellationToken(parent=parent, timeout_ms=5000)
    parent.cancel("parent first", AbortReason.USER_INTERRUPTED)
    await asyncio.sleep(0.02)
    assert combined.reason == "parent first"
    assert combined.abort_reason == AbortReason.USER_INTERRUPTED
    combined.cleanup()


@pytest.mark.asyncio
async def test_combined_token_cleanup_cancels_timer():
    combined = CombinedCancellationToken(timeout_ms=30)
    combined.cleanup()
    await asyncio.sleep(0.08)
    assert not combined.cancelled


@pytest.mark.asyncio
async def test_combined_token_cleanup_prevents_late_parent_fire():
    parent = CancellationToken()
    combined = CombinedCancellationToken(parent=parent)
    combined.cleanup()
    parent.cancel("late", AbortReason.USER_INTERRUPTED)
    assert not combined.cancelled


@pytest.mark.asyncio
async def test_combined_token_merges_three_sources_parent_wins():
    parent = CancellationToken()
    sibling = CancellationToken()
    combined = CombinedCancellationToken(
        parent=parent,
        sibling=sibling,
        timeout_ms=5000,
    )
    parent.cancel("parent", AbortReason.USER_INTERRUPTED)
    sibling.cancel("sibling")
    await asyncio.sleep(0.02)
    assert combined.reason == "parent"
    assert combined.abort_reason == AbortReason.USER_INTERRUPTED
    combined.cleanup()


@pytest.mark.asyncio
async def test_combined_token_merges_three_sources_sibling_wins():
    parent = CancellationToken()
    sibling = CancellationToken()
    combined = CombinedCancellationToken(
        parent=parent,
        sibling=sibling,
        timeout_ms=5000,
    )
    sibling.cancel("sibling first")
    parent.cancel("late parent", AbortReason.USER_INTERRUPTED)
    assert combined.reason == "sibling first"
    assert combined.abort_reason == AbortReason.SIBLING_ERROR
    combined.cleanup()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
