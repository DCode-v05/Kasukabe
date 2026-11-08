"""LoopState — explicit state container for the agent loop.

Replaces ~15 loose local variables in agent_loop() with a single
dataclass that every loop phase can read and write. Foundational for
streaming tool execution, budget enforcement, model fallback, and
session resume.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from src.herald import StopReason
from src.engine_core.schema import LoopExitReason, QueryChainTracking
from src.engine_core.token_budget import BudgetTracker

STRUGGLE_CONSECUTIVE_ERRORS = 3
STRUGGLE_TURNS_NO_CHECKPOINT = 12
STRUGGLE_TURNS_NO_TEXT = 8
STRUGGLE_REPEATED_CALLS = 3


@dataclass
class StrugglingState:
    """Tracks whether the agent loop is making progress or spinning."""

    consecutive_errors: int = 0
    turns_since_checkpoint: int = 0
    turns_without_text: int = 0
    recent_tool_sigs: list[str] = field(default_factory=list)
    struggle_triggered: bool = False

    def check(self, has_charter: bool) -> str | None:
        """Return a struggle reason string, or None if progressing fine."""
        if self.consecutive_errors >= STRUGGLE_CONSECUTIVE_ERRORS:
            return f"{self.consecutive_errors} consecutive tool errors"
        if self.turns_since_checkpoint >= STRUGGLE_TURNS_NO_CHECKPOINT and has_charter:
            return f"{self.turns_since_checkpoint} turns without checkpoint"
        if self.turns_without_text >= STRUGGLE_TURNS_NO_TEXT:
            return f"{self.turns_without_text} turns without text output"
        counts = Counter(self.recent_tool_sigs)
        for sig, count in counts.items():
            if count >= STRUGGLE_REPEATED_CALLS:
                return f"repeated tool call: {sig}"
        return None

    def record_tool_results(
        self,
        tool_calls: list[Any],
        tool_results: list[Any],
        had_text: bool,
        had_checkpoint: bool,
    ) -> None:
        """Update counters after tool execution within one turn."""
        if tool_calls and tool_results:
            all_errors = all(getattr(r, "is_error", False) for r in tool_results)
            if all_errors:
                self.consecutive_errors += len(tool_results)
            else:
                self.consecutive_errors = 0

        self.turns_since_checkpoint += 1
        if had_checkpoint:
            self.turns_since_checkpoint = 0

        if had_text:
            self.turns_without_text = 0
        else:
            self.turns_without_text += 1

        for c in tool_calls:
            sig = f"{c.name}:{sorted(c.input.keys())}"
            self.recent_tool_sigs.append(sig)
        self.recent_tool_sigs = self.recent_tool_sigs[-12:]

    def reset(self) -> None:
        """Reset all counters after auto-replan. Does not reset struggle_triggered (one-shot per loop)."""
        self.consecutive_errors = 0
        self.turns_since_checkpoint = 0
        self.turns_without_text = 0
        self.recent_tool_sigs = []

    def to_dict(self) -> dict:
        """Serialize for WM persistence."""
        return {
            "consecutive_errors": self.consecutive_errors,
            "turns_since_checkpoint": self.turns_since_checkpoint,
            "turns_without_text": self.turns_without_text,
            "recent_tool_sigs": self.recent_tool_sigs,
            "struggle_triggered": self.struggle_triggered,
        }

    @classmethod
    def from_dict(cls, data: dict) -> StrugglingState:
        """Restore from WM-persisted dict."""
        return cls(
            consecutive_errors=data.get("consecutive_errors", 0),
            turns_since_checkpoint=data.get("turns_since_checkpoint", 0),
            turns_without_text=data.get("turns_without_text", 0),
            recent_tool_sigs=data.get("recent_tool_sigs", []),
            struggle_triggered=data.get("struggle_triggered", False),
        )


@dataclass
class LoopState:
    """All mutable state carried through the agent loop."""

    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_create: int = 0
    exit_reason: LoopExitReason = LoopExitReason.COMPLETED
    last_stop: StopReason = StopReason.END_TURN
    struggle: StrugglingState = field(default_factory=StrugglingState)
    full_text: str = ""
    max_turns: int = 200
    thread_id: str = ""
    output_tokens_at_turn_start: int = 0
    current_turn_token_budget: int | None = None
    budget_tracker: BudgetTracker | None = None
    chain: QueryChainTracking | None = None
    last_transition_reason: str = ""

    def record_turn_usage(self, usage: Any) -> None:
        """Accumulate token counts from one turn's Usage object."""
        self.turns += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_read += getattr(usage, "cache_read_input_tokens", 0)
        self.cache_create += getattr(usage, "cache_creation_input_tokens", 0)

    def snapshot_turn_budget(self, budget: int | None) -> None:
        """Record the turn start for delta computation and set the active budget.

        Called at the top of each new user turn in `agent_loop` AND after a
        compaction event (so the per-turn delta is not artificially inflated
        by the compaction boundary — matches src bootstrap/state.ts:733).
        """
        self.output_tokens_at_turn_start = self.output_tokens
        self.current_turn_token_budget = budget

    def get_turn_output_tokens(self) -> int:
        """Output tokens produced by the model since the last turn snapshot."""
        return max(0, self.output_tokens - self.output_tokens_at_turn_start)

    def serialize(self) -> dict:
        """Serialize full loop state for crash recovery / session resume."""
        return {
            "turns": self.turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read": self.cache_read,
            "cache_create": self.cache_create,
            "exit_reason": self.exit_reason.value,
            "last_stop": self.last_stop.value,
            "struggle": self.struggle.to_dict(),
            "max_turns": self.max_turns,
            "thread_id": self.thread_id,
            "output_tokens_at_turn_start": self.output_tokens_at_turn_start,
            "current_turn_token_budget": self.current_turn_token_budget,
            "budget_tracker": self.budget_tracker.to_dict() if self.budget_tracker else None,
            "chain": {
                "chain_id": self.chain.chain_id,
                "depth": self.chain.depth,
                "turn_id": self.chain.turn_id,
                "turn_counter": self.chain.turn_counter,
                "parent_chain_id": self.chain.parent_chain_id,
                "query_source": self.chain.query_source,
            }
            if self.chain
            else None,
            "last_transition_reason": self.last_transition_reason,
        }

    @classmethod
    def deserialize(cls, data: dict) -> LoopState:
        """Restore from serialized state."""
        last_stop_str = data.get("last_stop", "end_turn")
        try:
            last_stop = StopReason(last_stop_str)
        except ValueError:
            last_stop = StopReason.END_TURN

        exit_reason_str = data.get("exit_reason", "completed")
        try:
            exit_reason = LoopExitReason(exit_reason_str)
        except ValueError:
            exit_reason = LoopExitReason.COMPLETED

        budget_tracker_data = data.get("budget_tracker")
        budget_tracker = (
            BudgetTracker.from_dict(budget_tracker_data)
            if budget_tracker_data is not None
            else None
        )

        return cls(
            turns=data.get("turns", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cache_read=data.get("cache_read", 0),
            cache_create=data.get("cache_create", 0),
            exit_reason=exit_reason,
            last_stop=last_stop,
            struggle=StrugglingState.from_dict(data.get("struggle", {})),
            max_turns=data.get("max_turns", 200),
            thread_id=data.get("thread_id", ""),
            output_tokens_at_turn_start=data.get("output_tokens_at_turn_start", 0),
            current_turn_token_budget=data.get("current_turn_token_budget"),
            budget_tracker=budget_tracker,
            chain=QueryChainTracking(**data["chain"]) if data.get("chain") else None,
            last_transition_reason=data.get("last_transition_reason", ""),
        )
