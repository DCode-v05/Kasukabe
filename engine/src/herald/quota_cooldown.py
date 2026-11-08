"""Engine-local quota cooldown — stop firing doomed background LLM calls.

When citadel reports quota exhaustion the whole plan-tier window is dead until
reset, but nothing in the engine remembered that: the learning_centre scheduler
kept starting LLM batches and the planner/sub-agent fan-out kept issuing calls
until the window reset on its own — every one a guaranteed 429.

This is a small, in-process, module-level cooldown (no persistence). When a
``CitadelQuotaExhausted`` is raised, ``enter_cooldown`` records the reset horizon
at ``now + window_seconds`` (the citadel-reported window carried on the
exception). Background consumers check ``cooldown_active`` and skip/reschedule
instead of calling. Foreground turns never consult the gate, so a user-driven
turn still attempts normally (and, if quota really is gone, surfaces the clean
structured error).

The horizon never shrinks — a later exhaustion with a further reset extends it,
but a shorter one cannot pull it in. A missing/zero window does not arm the
cooldown: with no reset horizon there is nothing to wait for, so background work
must not be wedged forever.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.herald.provider import CitadelBudgetExhausted, CitadelQuotaExhausted

# Module-level state: the monotonic-free wall-clock instant the current cooldown
# ends, or 0.0 when nothing is armed. Kept deliberately simple and process-local.
cooldown_until: float = 0.0


def enter_cooldown(
    exc: "CitadelQuotaExhausted | CitadelBudgetExhausted", now: float | None = None
) -> None:
    """Arm (or extend) the cooldown until the exception's window reset.

    Accepts either a quota (subscription window) or budget (metered spend)
    exhaustion; both carry ``window_seconds``. ``window_seconds`` of 0 (or
    missing) arms nothing - there is no reset horizon to wait for. A later
    exhaustion that resets further out extends the horizon; it never shrinks.
    """
    global cooldown_until
    window = getattr(exc, "window_seconds", 0) or 0
    if window <= 0:
        return
    if now is None:
        now = time.time()
    end = now + window
    if end > cooldown_until:
        cooldown_until = end


def cooldown_active(now: float | None = None) -> bool:
    """True while the cooldown horizon is still in the future."""
    if now is None:
        now = time.time()
    return now < cooldown_until


def cooldown_remaining(now: float | None = None) -> float:
    """Seconds until the cooldown lifts (0.0 when not active)."""
    if now is None:
        now = time.time()
    remaining = cooldown_until - now
    return remaining if remaining > 0 else 0.0


def reset() -> None:
    """Clear the cooldown (used at startup and in tests)."""
    global cooldown_until
    cooldown_until = 0.0
