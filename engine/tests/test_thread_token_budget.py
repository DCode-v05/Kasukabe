"""End-to-end scenarios for the TOKEN_BUDGET feature.

These tests drive the full feature chain — parser → LoopState snapshot →
BudgetTracker → check_token_budget → re-snapshot — through realistic turn
sequences that mirror what `agent_loop` does when the feature flag is on.

They complement the per-module unit tests in:
  - tests/test_token_budget_parser.py  (parser patterns + formatter)
  - tests/test_token_budget.py          (tracker + decision logic)
  - tests/test_loop_state.py            (LoopState snapshot + serialization)

The actual `agent_loop` wiring in thread.py uses these same primitives;
this file verifies the primitives compose correctly across multi-turn
scenarios including the spec's edge cases.
"""

from dataclasses import dataclass


from src.config import Settings
from src.engine_core.loop_state import LoopState
from src.engine_core.schema import RequestOptions
from src.herald.token_budget_parser import parse_token_budget
from src.engine_core.token_budget import (
    ContinueDecision,
    StopDecision,
    check_token_budget,
    create_budget_tracker,
)
from src.herald.types import Message


@dataclass
class FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


def resolve_budget(
    user_input: str,
    options_token_budget: int | None,
    state: LoopState,
) -> int | None:
    """Mirror the precedence logic in thread.py agent_loop entry point.

    Text shorthand wins, then API param, then carry-across from prior turn.
    """
    parsed = parse_token_budget(user_input)
    if parsed:
        return parsed
    if options_token_budget is not None:
        return options_token_budget
    return state.current_turn_token_budget


class TestSettingsFlag:
    def test_default_false(self):
        assert Settings().token_budget_enabled is False

    def test_flag_via_env(self, monkeypatch):
        monkeypatch.setenv("TOKEN_BUDGET_ENABLED", "true")
        s = Settings.load()
        assert s.token_budget_enabled is True

    def test_flag_no_env_false(self, monkeypatch):
        monkeypatch.delenv("TOKEN_BUDGET_ENABLED", raising=False)
        s = Settings.load()
        assert s.token_budget_enabled is False


class TestBudgetResolution:
    """Precedence: parsed > api_param > carry-across > None."""

    def test_parsed_wins_over_api_param(self):
        state = LoopState()
        budget = resolve_budget("+50k refactor this", 200_000, state)
        assert budget == 50_000

    def test_api_param_used_when_no_shorthand(self):
        state = LoopState()
        budget = resolve_budget("refactor this module", 200_000, state)
        assert budget == 200_000

    def test_carry_across_when_neither(self):
        state = LoopState(current_turn_token_budget=75_000)
        budget = resolve_budget("plain message", None, state)
        assert budget == 75_000

    def test_none_when_nothing(self):
        state = LoopState()
        budget = resolve_budget("plain message", None, state)
        assert budget is None

    def test_request_options_token_budget_default_none(self):
        assert RequestOptions().token_budget is None

    def test_request_options_token_budget_accepts_int(self):
        opts = RequestOptions(token_budget=500_000)
        assert opts.token_budget == 500_000


class TestFullTurnSequence:
    """Drive LoopState + BudgetTracker through multi-turn sequences that
    mirror what happens inside agent_loop when the flag is on.
    """

    def test_three_turns_under_90pct_all_continue(self):
        state = LoopState()
        budget = resolve_budget("+500k refactor", None, state)
        state.snapshot_turn_budget(budget)
        state.budget_tracker = create_budget_tracker()

        for turn_tokens in (50_000, 100_000, 150_000):
            state.record_turn_usage(FakeUsage(output_tokens=turn_tokens))
            decision = check_token_budget(
                tracker=state.budget_tracker,
                is_sub_agent=False,
                budget=state.current_turn_token_budget,
                global_turn_tokens=state.get_turn_output_tokens(),
            )
            assert isinstance(decision, ContinueDecision)
            state.snapshot_turn_budget(state.current_turn_token_budget)

        assert state.budget_tracker.continuation_count == 3

    def test_turn_over_90pct_after_continuations_stops_with_event(self):
        state = LoopState()
        state.snapshot_turn_budget(500_000)
        state.budget_tracker = create_budget_tracker()

        state.record_turn_usage(FakeUsage(output_tokens=100_000))
        d1 = check_token_budget(
            tracker=state.budget_tracker,
            is_sub_agent=False,
            budget=state.current_turn_token_budget,
            global_turn_tokens=state.get_turn_output_tokens(),
        )
        assert isinstance(d1, ContinueDecision)
        state.snapshot_turn_budget(state.current_turn_token_budget)

        state.record_turn_usage(FakeUsage(output_tokens=460_000))
        d2 = check_token_budget(
            tracker=state.budget_tracker,
            is_sub_agent=False,
            budget=state.current_turn_token_budget,
            global_turn_tokens=state.get_turn_output_tokens(),
        )
        assert isinstance(d2, StopDecision)
        assert d2.completion_event is not None
        assert d2.completion_event.diminishing_returns is False
        assert d2.completion_event.continuation_count == 1

    def test_diminishing_returns_fires_after_warmup(self):
        """Spec's canonical edge case:
        - 3 normal continuations (warm-up)
        - turn 4: 200 tokens (first low)
        - turn 5: 150 tokens (second low) → diminishing fires
        """
        state = LoopState()
        state.snapshot_turn_budget(500_000)
        state.budget_tracker = create_budget_tracker()

        for i, turn_tokens in enumerate([10_000, 20_000, 30_000]):
            state.record_turn_usage(FakeUsage(output_tokens=turn_tokens))
            d = check_token_budget(
                tracker=state.budget_tracker,
                is_sub_agent=False,
                budget=state.current_turn_token_budget,
                global_turn_tokens=state.get_turn_output_tokens(),
            )
            assert isinstance(d, ContinueDecision), f"turn {i + 1} should continue"
            state.snapshot_turn_budget(state.current_turn_token_budget)

        assert state.budget_tracker.continuation_count == 3

        state.record_turn_usage(FakeUsage(output_tokens=200))
        d4 = check_token_budget(
            tracker=state.budget_tracker,
            is_sub_agent=False,
            budget=state.current_turn_token_budget,
            global_turn_tokens=state.get_turn_output_tokens(),
        )
        assert isinstance(d4, ContinueDecision)
        state.snapshot_turn_budget(state.current_turn_token_budget)
        assert state.budget_tracker.continuation_count == 4

        state.record_turn_usage(FakeUsage(output_tokens=150))
        d5 = check_token_budget(
            tracker=state.budget_tracker,
            is_sub_agent=False,
            budget=state.current_turn_token_budget,
            global_turn_tokens=state.get_turn_output_tokens(),
        )
        assert isinstance(d5, StopDecision)
        assert d5.completion_event is not None
        assert d5.completion_event.diminishing_returns is True

    def test_compaction_reset_prevents_inflated_delta(self):
        """After a compaction that reshuffles context, the engine re-snapshots
        so the delta measurement for the next turn starts from the boundary,
        not from the stale pre-compaction value.
        """
        state = LoopState()
        state.snapshot_turn_budget(500_000)
        state.budget_tracker = create_budget_tracker()

        state.record_turn_usage(FakeUsage(output_tokens=200_000))
        delta_before_compact = state.get_turn_output_tokens()
        assert delta_before_compact == 200_000

        state.snapshot_turn_budget(state.current_turn_token_budget)
        assert state.output_tokens_at_turn_start == 200_000

        state.record_turn_usage(FakeUsage(output_tokens=80_000))
        assert state.get_turn_output_tokens() == 80_000

    def test_carry_across_turns_via_state(self):
        """Within a single agent_loop invocation the carry fallback works
        via LoopState. This is relevant after continuations re-snapshot
        with the current budget mid-loop.
        """
        state = LoopState()
        b1 = resolve_budget("+50k refactor", None, state)
        assert b1 == 50_000
        state.snapshot_turn_budget(b1)
        b2 = resolve_budget("continue please", None, state)
        assert b2 == 50_000


class TestThreadLevelCarryAcross:
    """Cross-turn carry-across via Thread.last_turn_budget — the fix that
    beats src's behavior (where REPL.tsx:2967 resets and makes 2895's
    fallback dead code).
    """

    def simulate_agent_loop_entry(
        self,
        last_turn_budget: int | None,
        user_input: str,
        options_token_budget: int | None,
    ) -> tuple[int | None, int | None]:
        """Mirror the exact precedence block at the top of agent_loop:
        parsed > api_param > self.last_turn_budget.
        Returns (effective_budget, next_last_turn_budget).
        """
        parsed = parse_token_budget(user_input)
        effective = parsed if parsed else options_token_budget
        if effective is None:
            effective = last_turn_budget
        return effective, effective

    def test_first_turn_with_shorthand(self):
        effective, carry = self.simulate_agent_loop_entry(
            last_turn_budget=None,
            user_input="+500k refactor this",
            options_token_budget=None,
        )
        assert effective == 500_000
        assert carry == 500_000

    def test_second_turn_plain_uses_carry(self):
        effective, carry = self.simulate_agent_loop_entry(
            last_turn_budget=500_000,
            user_input="continue with the refactor",
            options_token_budget=None,
        )
        assert effective == 500_000
        assert carry == 500_000

    def test_third_turn_plain_still_carries(self):
        effective, carry = self.simulate_agent_loop_entry(
            last_turn_budget=500_000,
            user_input="and finally add tests",
            options_token_budget=None,
        )
        assert effective == 500_000
        assert carry == 500_000

    def test_new_shorthand_overrides_carry(self):
        effective, carry = self.simulate_agent_loop_entry(
            last_turn_budget=500_000,
            user_input="+2m bigger task",
            options_token_budget=None,
        )
        assert effective == 2_000_000
        assert carry == 2_000_000

    def test_api_param_used_when_no_prior_budget(self):
        effective, carry = self.simulate_agent_loop_entry(
            last_turn_budget=None,
            user_input="plain message",
            options_token_budget=750_000,
        )
        assert effective == 750_000
        assert carry == 750_000

    def test_parsed_still_beats_api_and_carry(self):
        effective, carry = self.simulate_agent_loop_entry(
            last_turn_budget=500_000,
            user_input="+100k short task",
            options_token_budget=2_000_000,
        )
        assert effective == 100_000
        assert carry == 100_000

    def test_no_budget_at_all(self):
        effective, carry = self.simulate_agent_loop_entry(
            last_turn_budget=None,
            user_input="plain message",
            options_token_budget=None,
        )
        assert effective is None
        assert carry is None

    def test_sub_agent_never_continues(self):
        """If is_sub_agent=True, the tracker short-circuits immediately."""
        state = LoopState()
        state.snapshot_turn_budget(500_000)
        state.budget_tracker = create_budget_tracker()
        state.record_turn_usage(FakeUsage(output_tokens=100_000))

        decision = check_token_budget(
            tracker=state.budget_tracker,
            is_sub_agent=True,
            budget=state.current_turn_token_budget,
            global_turn_tokens=state.get_turn_output_tokens(),
        )
        assert isinstance(decision, StopDecision)
        assert decision.completion_event is None
        assert state.budget_tracker.continuation_count == 0

    def test_passive_noop_when_feature_not_exercised(self):
        """Budget enabled, model finishes under budget naturally — no events."""
        state = LoopState()
        state.snapshot_turn_budget(500_000)
        state.budget_tracker = create_budget_tracker()

        state.record_turn_usage(FakeUsage(output_tokens=490_000))
        d = check_token_budget(
            tracker=state.budget_tracker,
            is_sub_agent=False,
            budget=state.current_turn_token_budget,
            global_turn_tokens=state.get_turn_output_tokens(),
        )
        assert isinstance(d, StopDecision)
        assert d.completion_event is None


class TestMessageFrozenHashContract:
    """Regression witnesses for the Message dataclass hash contract.

    Before adding the `metadata` field, Message was a frozen dataclass and
    fully hashable. Adding a mutable default (dict) to a frozen dataclass
    would break `hash(msg)` unless the field is excluded from comparison
    via `compare=False`. These tests lock in the correct behavior.
    """

    def test_message_is_hashable(self):
        m = Message(role="user", content="hi")
        assert hash(m) == hash(m)
        assert m in {m}

    def test_hash_ignores_metadata(self):
        m1 = Message(role="user", content="hi")
        m2 = Message(role="user", content="hi", metadata={"is_meta": True})
        assert hash(m1) == hash(m2)

    def test_equality_ignores_metadata(self):
        m1 = Message(role="user", content="hi")
        m2 = Message(role="user", content="hi", metadata={"is_meta": True})
        assert m1 == m2

    def test_message_usable_in_set(self):
        messages = {
            Message(role="user", content="hi"),
            Message(role="user", content="hi", metadata={"is_meta": True}),
            Message(role="assistant", content="hello"),
        }
        assert len(messages) == 2


class TestMessageMetadataTagging:
    def test_nudge_message_carries_is_meta(self):
        nudge = Message(
            role="user",
            content="Stopped at 24% of token target (120,000 / 500,000). "
            "Keep working -- do not summarize.",
            metadata={"is_meta": True},
        )
        assert nudge.metadata == {"is_meta": True}
        assert nudge.metadata.get("is_meta") is True

    def test_regular_message_has_empty_metadata(self):
        msg = Message(role="user", content="plain text")
        assert msg.metadata == {}
        assert msg.metadata.get("is_meta") is None

    def test_filter_meta_messages_from_list(self):
        """Demonstrate how a future transcript exporter / debug UI could
        filter engine-injected messages without regex-matching content.
        """
        messages = [
            Message(role="user", content="original user query"),
            Message(role="assistant", content="first response"),
            Message(role="user", content="nudge", metadata={"is_meta": True}),
            Message(role="assistant", content="continuation output"),
            Message(role="user", content="follow-up from real user"),
        ]
        user_typed = [m for m in messages if not m.metadata.get("is_meta")]
        assert len(user_typed) == 4
        assert user_typed[0].content == "original user query"
        assert user_typed[-1].content == "follow-up from real user"


class TestFeatureGating:
    """When the flag is off, the resolution helper still works but callers
    must check the flag themselves (matches thread.py guard pattern).
    """

    def test_flag_off_no_change(self):
        state = LoopState()
        s = Settings(token_budget_enabled=False)
        if s.token_budget_enabled:
            budget = resolve_budget("+500k do a thing", None, state)
            state.snapshot_turn_budget(budget)
        assert state.current_turn_token_budget is None
        assert state.output_tokens_at_turn_start == 0
        assert state.budget_tracker is None

    def test_flag_on_takes_effect(self):
        state = LoopState()
        s = Settings(token_budget_enabled=True)
        if s.token_budget_enabled:
            budget = resolve_budget("+500k do a thing", None, state)
            state.snapshot_turn_budget(budget)
            state.budget_tracker = create_budget_tracker()
        assert state.current_turn_token_budget == 500_000
        assert state.budget_tracker is not None
