"""Engine-local quota cooldown — stop firing doomed background LLM calls.

When citadel reports quota exhaustion, the whole plan-tier window is dead until
reset; nothing in the engine remembered that. The learning_centre scheduler
kept starting LLM batches and the planner/sub-agent fan-out kept issuing calls
until the window reset on its own — every one a guaranteed 429.

The fix is a small in-process, module-level cooldown (no persistence): when
``CitadelQuotaExhausted`` is raised, ``enter_cooldown`` records an end time at
``now + window_seconds`` (the citadel-reported window on the exception).
Background consumers — the LC scheduler at batch start, the sub-agent fan-out
gate — check ``cooldown_active`` and skip/reschedule instead of calling.
FOREGROUND turns never consult the gate, so a user-driven turn still attempts
normally (and, if quota really is gone, gets the clean structured error).

These tests drive real exceptions and the real cooldown state (no mocks), and
pass an explicit ``now`` so timing is deterministic without sleeping. The
module-level state is reset in a fixture so tests don't leak into each other.
"""

from __future__ import annotations

import pytest

from src.herald.provider import CitadelQuotaExhausted
from src.herald import quota_cooldown


@pytest.fixture(autouse=True)
def clean_cooldown():
    """Each test starts and ends with no cooldown armed (module-level state)."""
    quota_cooldown.reset()
    yield
    quota_cooldown.reset()


def test_no_cooldown_by_default():
    """Nothing armed → not active, zero remaining."""
    assert quota_cooldown.cooldown_active(now=1000.0) is False
    assert quota_cooldown.cooldown_remaining(now=1000.0) == 0.0


def test_enter_cooldown_arms_until_window_reset():
    """The cooldown end is now + the exception's window_seconds."""
    exc = CitadelQuotaExhausted(
        "engine used 5000/5000",
        used_tokens=5000,
        max_tokens=5000,
        window_seconds=3600,
    )
    quota_cooldown.enter_cooldown(exc, now=1000.0)

    # Inside the window → active, remaining counts down toward the reset.
    assert quota_cooldown.cooldown_active(now=1000.0) is True
    assert quota_cooldown.cooldown_remaining(now=1000.0) == 3600.0
    assert quota_cooldown.cooldown_active(now=1000.0 + 1800) is True
    assert quota_cooldown.cooldown_remaining(now=1000.0 + 1800) == 1800.0


def test_cooldown_expires_at_window_reset():
    """At/after the reset instant the cooldown is no longer active — a fresh
    attempt is allowed (the window may have rolled over)."""
    exc = CitadelQuotaExhausted("gone", window_seconds=3600)
    quota_cooldown.enter_cooldown(exc, now=1000.0)

    assert quota_cooldown.cooldown_active(now=1000.0 + 3600) is False
    assert quota_cooldown.cooldown_active(now=1000.0 + 3600 + 1) is False
    assert quota_cooldown.cooldown_remaining(now=1000.0 + 5000) == 0.0


def test_later_exhaustion_extends_the_cooldown():
    """A second exhaustion with a fresh window pushes the reset out — the gate
    tracks the furthest known reset, never shrinks it."""
    quota_cooldown.enter_cooldown(CitadelQuotaExhausted("first", window_seconds=600), now=1000.0)
    quota_cooldown.enter_cooldown(CitadelQuotaExhausted("second", window_seconds=3600), now=1100.0)
    # Furthest reset = 1100 + 3600 = 4700, not the earlier 1600.
    assert quota_cooldown.cooldown_active(now=2000.0) is True
    assert quota_cooldown.cooldown_remaining(now=1100.0) == 3600.0


def test_zero_window_does_not_arm_a_permanent_cooldown():
    """A missing/zero window_seconds must not wedge background work forever —
    with no reset horizon there is nothing to wait for, so no cooldown arms."""
    quota_cooldown.enter_cooldown(CitadelQuotaExhausted("no window", window_seconds=0), now=1000.0)
    assert quota_cooldown.cooldown_active(now=1000.0) is False


def test_reset_clears_the_cooldown():
    quota_cooldown.enter_cooldown(CitadelQuotaExhausted("gone", window_seconds=3600), now=1000.0)
    assert quota_cooldown.cooldown_active(now=1000.0) is True
    quota_cooldown.reset()
    assert quota_cooldown.cooldown_active(now=1000.0) is False


# ── Consumer-shaped gates: how background work uses the cooldown ───────────


def test_background_consumer_skips_while_cooldown_active():
    """An LC-scheduler-style batch-start gate: when the cooldown is active, the
    consumer skips the LLM batch (it would only 429). This mirrors the check the
    scheduler.loop adds before pipeline.run_batch()."""
    quota_cooldown.enter_cooldown(CitadelQuotaExhausted("gone", window_seconds=3600), now=1000.0)

    calls = {"batches": 0}

    def run_batch_if_allowed(now: float) -> bool:
        # Background gate: skip while cooled down; otherwise run.
        if quota_cooldown.cooldown_active(now=now):
            return False
        calls["batches"] += 1
        return True

    assert run_batch_if_allowed(now=1500.0) is False  # mid-window → skipped
    assert calls["batches"] == 0
    assert run_batch_if_allowed(now=1000.0 + 3600) is True  # window reset → runs
    assert calls["batches"] == 1


def test_foreground_turn_is_unaffected_by_cooldown():
    """The foreground path never consults the gate — a user-driven turn still
    attempts normally even while a cooldown is armed (so the user gets a live
    answer, or a clean structured quota error, not a silent skip)."""
    quota_cooldown.enter_cooldown(CitadelQuotaExhausted("gone", window_seconds=3600), now=1000.0)

    attempts = {"foreground": 0}

    def foreground_attempt() -> None:
        # Foreground code does NOT gate on the cooldown — it always attempts.
        attempts["foreground"] += 1

    foreground_attempt()
    assert attempts["foreground"] == 1
    # The cooldown is still armed for background work, untouched by the fg call.
    assert quota_cooldown.cooldown_active(now=1500.0) is True
