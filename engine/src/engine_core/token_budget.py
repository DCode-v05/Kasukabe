"""BudgetTracker and decision logic for the TOKEN_BUDGET feature.

Ports src/query/tokenBudget.ts. The tracker holds per-loop-invocation state;
`check_token_budget` is pure except for mutating the tracker on continue
decisions. See `architecture/sections/42_engine_core_context_compaction.md` for
the full contract, decision rules, and the sub-agent invariant rationale.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Union

from src.herald.token_budget_parser import get_budget_continuation_message

# ── Thresholds (ported exactly from src/query/tokenBudget.ts:3-4) ───────

COMPLETION_THRESHOLD = 0.9  # stop when >= 90% of budget consumed
DIMINISHING_THRESHOLD = 500  # per-turn delta below this counts as stalled


def pct_of_budget(used: int, budget: int) -> int:
    """Round(used/budget * 100). Shared by check_token_budget and thread.py."""
    return round(used / budget * 100)


# ── Tracker state ───────────────────────────────────────────────────────


@dataclass
class BudgetTracker:
    """Per-loop-invocation budget state. Lives on LoopState.budget_tracker."""

    continuation_count: int = 0
    last_delta_tokens: int = 0
    last_global_turn_tokens: int = 0
    started_at: float = 0.0  # time.time() at construction

    def reset_delta_origin(self) -> None:
        """Reset the delta baseline to 0 after a compaction boundary.

        Compaction reshuffles context but leaves cumulative output_tokens
        alone; the per-turn snapshot is re-taken to 0. Without also
        resetting this, the next `check_token_budget` computes
        `delta = (small, post-compaction) - (large, pre-compaction)` which
        goes negative and trivially passes `delta < DIMINISHING_THRESHOLD`,
        firing diminishing returns falsely on the first post-compaction turn.
        Leaves `last_delta_tokens` and `continuation_count` untouched so the
        tracker's history for the two-consecutive-lows check is preserved.
        """
        self.last_global_turn_tokens = 0

    def to_dict(self) -> dict:
        """Serialize for LoopState crash-recovery roundtrip."""
        return {
            "continuation_count": self.continuation_count,
            "last_delta_tokens": self.last_delta_tokens,
            "last_global_turn_tokens": self.last_global_turn_tokens,
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BudgetTracker:
        """Restore from a serialized dict, tolerating missing keys."""
        return cls(
            continuation_count=data.get("continuation_count", 0),
            last_delta_tokens=data.get("last_delta_tokens", 0),
            last_global_turn_tokens=data.get("last_global_turn_tokens", 0),
            started_at=data.get("started_at", 0.0),
        )


def create_budget_tracker() -> BudgetTracker:
    """Zero-init a fresh tracker with started_at = time.time()."""
    return BudgetTracker(started_at=time.time())


# ── Decision types ──────────────────────────────────────────────────────


@dataclass
class ContinueDecision:
    """Continue the turn loop — inject the nudge and loop back to the model."""

    nudge_message: str
    continuation_count: int
    pct: int
    turn_tokens: int
    budget: int


@dataclass
class CompletionEvent:
    """Payload for TokenBudgetCompleted when a stop decision has an event."""

    continuation_count: int
    pct: int
    turn_tokens: int
    budget: int
    diminishing_returns: bool
    duration_ms: int


@dataclass
class StopDecision:
    """Stop the turn loop. completion_event is None for passive no-op stops."""

    completion_event: CompletionEvent | None


TokenBudgetDecision = Union[ContinueDecision, StopDecision]


# ── Decision function ──────────────────────────────────────────────────


def check_token_budget(
    tracker: BudgetTracker,
    is_sub_agent: bool,
    budget: int | None,
    global_turn_tokens: int,
) -> TokenBudgetDecision:
    """Decide whether to continue the turn loop or stop.

    Mutates `tracker` only on a Continue decision (matches src mutation pattern).
    """
    # 1. Short-circuit stop paths — sub-agents, missing/invalid budget.
    if is_sub_agent or budget is None or budget <= 0:
        return StopDecision(completion_event=None)

    # 2. Compute current state.
    turn_tokens = global_turn_tokens
    pct = pct_of_budget(turn_tokens, budget)
    delta = global_turn_tokens - tracker.last_global_turn_tokens

    is_diminishing = (
        tracker.continuation_count >= 3
        and delta < DIMINISHING_THRESHOLD
        and tracker.last_delta_tokens < DIMINISHING_THRESHOLD
    )

    # 3. Under threshold AND not diminishing → continue (mutate tracker).
    if not is_diminishing and turn_tokens < budget * COMPLETION_THRESHOLD:
        tracker.continuation_count += 1
        tracker.last_delta_tokens = delta
        tracker.last_global_turn_tokens = global_turn_tokens
        return ContinueDecision(
            nudge_message=get_budget_continuation_message(pct, turn_tokens, budget),
            continuation_count=tracker.continuation_count,
            pct=pct,
            turn_tokens=turn_tokens,
            budget=budget,
        )

    # 4. Stopping. Emit an event only if we ever continued or diminishing fired
    #    (matches src/query/tokenBudget.ts:78 — no event noise for passive stops).
    if is_diminishing or tracker.continuation_count > 0:
        duration_ms = int((time.time() - tracker.started_at) * 1000)
        return StopDecision(
            completion_event=CompletionEvent(
                continuation_count=tracker.continuation_count,
                pct=pct,
                turn_tokens=turn_tokens,
                budget=budget,
                diminishing_returns=is_diminishing,
                duration_ms=duration_ms,
            )
        )

    return StopDecision(completion_event=None)
