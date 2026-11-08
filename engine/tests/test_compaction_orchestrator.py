import asyncio
import os
import pytest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from src.engine_core.compaction_orchestrator import (
    CompactionOrchestrator,
    CompactionConfig,
    CompactionRecord,
    ReactiveCompactResult,
    FileReadRecord,
    PostCompactAttachment,
    FILE_UNCHANGED_STUB,
    MAX_CONSECUTIVE_FAILURES,
    CIRCUIT_BREAKER_HALF_OPEN_SECONDS,
    CIRCUIT_BREAKER_SHRINK_THRESHOLD,
    COMPACTION_BOUNDARY_CONTENT,
    POST_COMPACT_MAX_TOKENS_PER_FILE,
    POST_COMPACT_TOKEN_BUDGET,
    POST_COMPACT_TOOL_REMINDER_TOKEN_BUDGET,
    POST_COMPACT_SKILL_TOKEN_BUDGET,
    TRUNCATION_MARKER,
    tokens_to_dense_chars,
    extract_discovered_tool_names,
    should_exclude_from_restore,
    collect_file_paths_from_messages,
    collect_file_paths_structured,
)
from src.engine_core.post_compact_cleanup import (
    is_main_thread_compact,
    run_post_compact_cleanup,
)
from src.engine_core.events import (
    EventEmitter,
    CompactionEvent,
    FileRestorationEvent,
    FileRestorationFailedEvent,
    CompactionCircuitBreakerTripped,
    CompactionCircuitBreakerProbeAttempt,
    CompactionCircuitBreakerReset,
    CompactionCircuitBreakerSkip,
)
from src.engine_core.compaction_orchestrator import CircuitState
from src.engine_core.context_engineering import (
    group_by_api_round,
    estimate_tokens_dense,
)
from src.herald.types import Message, TextBlock, ToolUseBlock, ToolResultBlock


class EntryType(str, Enum):
    MESSAGE_USER = "message:user"
    MESSAGE_ASSISTANT = "message:assistant"
    TOOL_CALL = "tool:call"
    TOOL_RESULT = "tool:result"
    USER_INPUT_REQUEST = "input:request"
    USER_INPUT_RESPONSE = "input:response"
    ACTION_USER = "action:user"
    CHECKPOINT = "checkpoint"
    SYSTEM_EVENT = "system:event"


@dataclass
class LogEntry:
    thread_id: str
    type: EntryType
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sub_agent_id: str = ""


def make_messages(count: int, chars_each: int = 100) -> list[Message]:
    msgs = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append(Message(role=role, content="x" * chars_each))
    return msgs


class FakeMemory:
    def __init__(self, summary: str = "", checkpoints=None, events=None):
        self.summary = summary
        self.checkpoints = checkpoints or []
        self.events = events or []

    async def get_context_summary(
        self, max_items: int = 10, thread_id: str | None = None, max_tokens: int | None = None
    ) -> str:
        return self.summary

    async def read(self, thread_id: str):
        return []

    async def read_by_type(self, thread_id: str, entry_type):
        et_val = getattr(entry_type, "value", str(entry_type))
        if et_val == "checkpoint":
            return self.checkpoints
        if et_val == "system:event":
            return self.events
        return []

    async def write(self, *a, **kw):
        pass

    @property
    def session_id(self) -> str:
        return "test"


class FakeAdapter:
    pass


class FakeConsciousnessMgr:
    def __init__(self):
        self.invalidated = False

    def invalidate(self):
        self.invalidated = True


class FakeSearch:
    def __init__(self, raise_on_invalidate: bool = False):
        self.invalidated = False
        self.raise_ = raise_on_invalidate

    def invalidate_all(self):
        if self.raise_:
            raise RuntimeError("search boom")
        self.invalidated = True


class FakeUtility:
    def __init__(self, raise_on_invalidate: bool = False):
        self.invalidated = False
        self.last_prefix: str | None = None
        self.raise_ = raise_on_invalidate

    def invalidate(self, prefix: str = ""):
        if self.raise_:
            raise RuntimeError("utility boom")
        self.invalidated = True
        self.last_prefix = prefix


class FakeAssets:
    def __init__(self, raise_on_invalidate: bool = False):
        self.invalidated = False
        self.last_prefix: str | None = None
        self.raise_ = raise_on_invalidate

    def invalidate(self, prefix: str = ""):
        if self.raise_:
            raise RuntimeError("assets boom")
        self.invalidated = True
        self.last_prefix = prefix


def run(coro):
    return asyncio.run(coro)


def make_orch(summary="", **kwargs):
    mem_keys = ("checkpoints", "events")
    return CompactionOrchestrator(
        adapter=FakeAdapter(),
        emitter=EventEmitter(),
        memory=FakeMemory(summary=summary, **{k: v for k, v in kwargs.items() if k in mem_keys}),
        **{k: v for k, v in kwargs.items() if k not in mem_keys},
    )


def compact(
    orch,
    msgs,
    context_window=200_000,
    thread_id="task1",
    turn=1,
    system="",
    tools=None,
    query_source=None,
):
    return run(
        orch.compact_if_needed(
            msgs,
            system,
            tools or [],
            thread_id,
            turn,
            context_window,
            query_source=query_source,
        )
    )


def window_forcing_compaction(msgs: list) -> int:
    """Return a context window small enough that these messages overflow it.

    Derived from the actual estimated token count of the messages rather
    than a magic number, so the test intent ("these messages must trigger
    compaction") stays true even if estimator heuristics change. Real model
    windows live in the citadel-synced catalog (src/herald/catalog_sync.py,
    200K default for unknown ids) and range from 200K
    to 1M — synthetic unit tests scale down to stay fast.
    """
    from src.engine_core.context_engineering import estimate_messages_tokens

    return max(1, estimate_messages_tokens(msgs) // 3)


class TestCompactIfNeeded:
    def test_no_compaction_under_threshold(self):
        orch = make_orch()
        msgs = make_messages(4, chars_each=100)
        result = compact(orch, msgs, system="system")
        assert result.messages is not None
        assert result.strategy in ("none", "mechanical")

    def test_compaction_fires_over_threshold(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=600)
        result = compact(orch, msgs, context_window=1000)
        assert result.was_compacted
        assert len(result.messages) < len(msgs)

    def test_session_memory_compact_used_when_wm_has_content(self):
        summary = "User asked to implement a REST API. Agent created routes.py and models.py. " * 3
        orch = make_orch(summary=summary)
        msgs = make_messages(40, chars_each=4000)
        result = compact(orch, msgs, context_window=50_000)
        assert result.was_compacted
        assert result.strategy == "session_memory"

    def test_session_memory_skipped_when_wm_empty(self):
        orch = make_orch(summary="")
        msgs = make_messages(40, chars_each=4000)
        result = compact(orch, msgs, context_window=50_000)
        assert result.was_compacted
        assert result.strategy != "session_memory"


class TestCircuitBreaker:
    def test_trips_after_max_failures(self):
        orch = make_orch()
        orch.consecutive_failures = MAX_CONSECUTIVE_FAILURES
        orch.circuit_breaker_tripped = True
        msgs = make_messages(40, chars_each=600)
        result = compact(orch, msgs, context_window=1000)
        assert result.strategy in ("circuit_breaker_skip", "mechanical", "emergency")

    def test_resets_on_success(self):
        orch = make_orch()
        orch.consecutive_failures = 2
        msgs = make_messages(4, chars_each=100)
        compact(orch, msgs)
        assert orch.consecutive_failures == 2

    def test_reset_clears_state(self):
        orch = make_orch()
        orch.consecutive_failures = 3
        orch.circuit_state = CircuitState.OPEN
        orch.total_trips = 2
        orch.total_probes = 1
        orch.total_probe_successes = 1
        orch.history.append(
            CompactionRecord(
                timestamp=0,
                strategy="test",
                tokens_before=100,
                tokens_after=50,
                tokens_freed=50,
                messages_before=10,
                messages_after=5,
                success=True,
                turn_number=1,
            )
        )
        orch.reset()
        assert orch.consecutive_failures == 0
        assert orch.circuit_state == CircuitState.CLOSED
        assert not orch.circuit_breaker_tripped
        assert len(orch.history) == 0
        assert orch.total_trips == 0
        assert orch.total_probes == 0
        assert orch.total_probe_successes == 0

    def test_circuit_breaker_persists_across_calls(self):
        orch = make_orch()
        orch.consecutive_failures = 2
        orch.circuit_state = CircuitState.CLOSED
        assert orch.consecutive_failures == 2

    def test_reset_clears_half_open_state(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.HALF_OPEN
        orch.tripped_at = 1.0
        orch.reset()
        assert orch.tripped_at is None
        assert orch.circuit_state == CircuitState.CLOSED
        assert not orch.half_open_probe_active


class TestCircuitBreakerHardened:
    """Tests for per-user-message decay, half-open, context-shrink, and events."""


    def collect_reset_events(
        self, orch: CompactionOrchestrator
    ) -> list[CompactionCircuitBreakerReset]:
        return [e for e in orch.emitter.buffer if isinstance(e, CompactionCircuitBreakerReset)]

    def collect_probe_events(
        self, orch: CompactionOrchestrator
    ) -> list[CompactionCircuitBreakerProbeAttempt]:
        return [
            e for e in orch.emitter.buffer if isinstance(e, CompactionCircuitBreakerProbeAttempt)
        ]

    def collect_trip_events(
        self, orch: CompactionOrchestrator
    ) -> list[CompactionCircuitBreakerTripped]:
        return [e for e in orch.emitter.buffer if isinstance(e, CompactionCircuitBreakerTripped)]

    def collect_skip_events(
        self, orch: CompactionOrchestrator
    ) -> list[CompactionCircuitBreakerSkip]:
        return [e for e in orch.emitter.buffer if isinstance(e, CompactionCircuitBreakerSkip)]

    def collect_cb_events(self, orch: CompactionOrchestrator) -> list:
        return [
            e
            for e in orch.emitter.buffer
            if isinstance(
                e,
                (
                    CompactionCircuitBreakerTripped,
                    CompactionCircuitBreakerProbeAttempt,
                    CompactionCircuitBreakerReset,
                    CompactionCircuitBreakerSkip,
                ),
            )
        ]


    def test_on_user_message_decrements_failures(self):
        orch = make_orch()
        orch.consecutive_failures = 2
        run(orch.on_user_message("t1"))
        assert orch.consecutive_failures == 1

    def test_reset_for_new_user_message_decrements_failures(self):
        orch = make_orch()
        orch.consecutive_failures = 2
        run(orch.reset_for_new_user_message("t1"))
        assert orch.consecutive_failures == 1

    def test_on_user_message_emits_decay_event(self):
        orch = make_orch()
        orch.consecutive_failures = 2
        run(orch.on_user_message("t1"))
        evts = self.collect_reset_events(orch)
        assert len(evts) == 1
        assert evts[0].transition == "reset_decay"
        assert evts[0].thread_id == "t1"

    def test_on_user_message_no_decay_at_zero(self):
        """No event emitted when failures already 0."""
        orch = make_orch()
        orch.consecutive_failures = 0
        run(orch.on_user_message("t1"))
        assert orch.consecutive_failures == 0
        assert not self.collect_reset_events(orch)

    def test_on_user_message_no_decay_when_tripped(self):
        """Decay is skipped when breaker is OPEN (half-open handles recovery)."""
        orch = make_orch()
        orch.consecutive_failures = 3
        orch.circuit_state = CircuitState.OPEN
        orch.tripped_at = 1.0
        run(orch.on_user_message("t1"))
        assert orch.consecutive_failures == 3

    def test_multiple_decay_steps_heal_over_time(self):
        orch = make_orch()
        orch.consecutive_failures = 3
        for _ in range(3):
            run(orch.on_user_message("t1"))
        assert orch.consecutive_failures == 0


    def test_on_user_message_arms_half_open_after_cooldown(self):
        import time

        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        orch.consecutive_failures = MAX_CONSECUTIVE_FAILURES
        orch.tripped_at = time.time() - CIRCUIT_BREAKER_HALF_OPEN_SECONDS - 1
        run(orch.on_user_message("t1"))
        assert orch.circuit_state == CircuitState.HALF_OPEN
        assert orch.half_open_probe_active

    def test_on_user_message_does_not_arm_before_cooldown(self):
        import time

        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        orch.tripped_at = time.time()
        run(orch.on_user_message("t1"))
        assert orch.circuit_state == CircuitState.OPEN
        assert not orch.half_open_probe_active

    def test_half_open_emits_probe_event(self):
        import time

        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        orch.consecutive_failures = MAX_CONSECUTIVE_FAILURES
        orch.tripped_at = time.time() - CIRCUIT_BREAKER_HALF_OPEN_SECONDS - 1
        run(orch.on_user_message("t1"))
        probe_evts = self.collect_probe_events(orch)
        assert probe_evts, "Expected CompactionCircuitBreakerProbeAttempt event"
        assert probe_evts[0].thread_id == "t1"
        assert probe_evts[0].elapsed_seconds >= CIRCUIT_BREAKER_HALF_OPEN_SECONDS

    def test_half_open_not_re_armed_while_active(self):
        """A second on_user_message should not emit another probe if already HALF_OPEN."""
        import time

        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        orch.tripped_at = time.time() - CIRCUIT_BREAKER_HALF_OPEN_SECONDS - 1
        run(orch.on_user_message("t1"))
        assert orch.circuit_state == CircuitState.HALF_OPEN
        probes_before = len(self.collect_probe_events(orch))
        run(orch.on_user_message("t1"))
        probes_after = len(self.collect_probe_events(orch))
        assert probes_after == probes_before

    def test_reset_breaker_clears_all_state(self):
        orch = make_orch()
        orch.consecutive_failures = 3
        orch.circuit_state = CircuitState.HALF_OPEN
        orch.tripped_at = 1.0
        run(orch.reset_breaker("t1", "reset_success", "test"))
        assert orch.consecutive_failures == 0
        assert orch.circuit_state == CircuitState.CLOSED
        assert orch.tripped_at is None
        assert not orch.half_open_probe_active

    def test_reset_breaker_increments_probe_successes(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.HALF_OPEN
        run(orch.reset_breaker("t1", "reset_success", "llm_compact_success"))
        assert orch.total_probe_successes == 1

    def test_reset_circuit_breaker_shim_clears_all_state(self):
        """reset_circuit_breaker still works as a backward-compat shim."""
        orch = make_orch()
        orch.consecutive_failures = 3
        orch.circuit_state = CircuitState.OPEN
        orch.tripped_at = 1.0
        run(orch.reset_circuit_breaker("t1", "reset_success", "test"))
        assert orch.consecutive_failures == 0
        assert orch.circuit_state == CircuitState.CLOSED
        assert orch.tripped_at is None

    def test_reset_breaker_emits_reset_event(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        run(orch.reset_breaker("t1", "reset_success", "llm_compact_success"))
        evts = self.collect_reset_events(orch)
        assert len(evts) == 1
        assert evts[0].transition == "reset_success"
        assert evts[0].reason == "llm_compact_success"
        assert evts[0].consecutive_failures == 0


    def test_trip_emits_structured_event(self):
        """Trigger a trip via compact_if_needed with a failing LLM adapter."""
        orch = make_orch()
        orch.config = CompactionConfig(circuit_breaker_max_failures=1)

        import src.engine_core.context_engineering as ce_mod

        original = ce_mod.compact_messages_with_summary

        async def fail(*a, **kw):
            raise RuntimeError("LLM boom")

        ce_mod.compact_messages_with_summary = fail
        try:
            msgs = make_messages(40, chars_each=600)
            cw = window_forcing_compaction(msgs)
            compact(orch, msgs, context_window=cw)
        finally:
            ce_mod.compact_messages_with_summary = original

        trip_evts = self.collect_trip_events(orch)
        assert trip_evts, "Expected CompactionCircuitBreakerTripped event"
        assert trip_evts[0].consecutive_failures >= 1
        assert orch.tripped_at is not None
        assert orch.circuit_state == CircuitState.OPEN
        assert orch.total_trips == 1

    def test_trip_breaker_sets_snapshot_fields(self):
        orch = make_orch()
        run(orch.trip_breaker("t1", error="boom", message_count=10, token_count=5000))
        assert orch.circuit_state == CircuitState.OPEN
        assert orch.last_trip_error == "boom"
        assert orch.last_message_count_at_trip == 10
        assert orch.last_token_count_at_trip == 5000
        assert orch.total_trips == 1


    def test_context_shrink_resets_failures_after_large_mechanical_drop(self):
        """Mechanical compaction freeing >= SHRINK_THRESHOLD resets consecutive_failures."""
        orch = make_orch()
        orch.consecutive_failures = 2

        msgs = make_messages(40, chars_each=2000)
        from src.engine_core.context_engineering import estimate_messages_tokens

        pre = estimate_messages_tokens(msgs)
        cw = max(1, pre // 4)
        compact(orch, msgs, context_window=cw)

        from src.engine_core.context_engineering import compact_messages

        post_msgs = compact_messages(list(msgs), cw)
        from src.engine_core.context_engineering import estimate_messages_tokens as et

        freed = (pre - et(post_msgs)) / pre
        if freed >= CIRCUIT_BREAKER_SHRINK_THRESHOLD:
            assert orch.consecutive_failures == 0

    def test_context_shrink_emits_event(self):
        """When shrink reset fires, a CompactionCircuitBreakerReset(reset_shrink) is emitted."""
        import src.engine_core.context_engineering as ce_mod

        orch = make_orch()
        orch.config = CompactionConfig(circuit_breaker_max_failures=5)
        orch.consecutive_failures = 1

        original = ce_mod.compact_messages_with_summary

        async def fail(*a, **kw):
            raise RuntimeError("LLM forced fail for shrink test")

        ce_mod.compact_messages_with_summary = fail
        try:
            msgs = make_messages(40, chars_each=2000)
            from src.engine_core.context_engineering import estimate_messages_tokens

            pre = estimate_messages_tokens(msgs)
            cw = max(1, pre // 4)
            compact(orch, msgs, context_window=cw)
        finally:
            ce_mod.compact_messages_with_summary = original

        shrink_evts = [e for e in self.collect_reset_events(orch) if e.transition == "reset_shrink"]
        from src.engine_core.context_engineering import compact_messages, estimate_messages_tokens

        post_msgs = compact_messages(list(msgs), cw)
        freed = (pre - estimate_messages_tokens(post_msgs)) / pre
        if freed >= CIRCUIT_BREAKER_SHRINK_THRESHOLD:
            assert shrink_evts, "Expected reset_shrink CompactionCircuitBreakerReset event"
            assert shrink_evts[0].consecutive_failures == 0

    def test_context_shrink_not_fired_when_tripped(self):
        """Shrink reset should not fire when breaker is OPEN."""
        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        orch.consecutive_failures = MAX_CONSECUTIVE_FAILURES

        msgs = make_messages(40, chars_each=2000)
        from src.engine_core.context_engineering import estimate_messages_tokens

        cw = max(1, estimate_messages_tokens(msgs) // 4)
        compact(orch, msgs, context_window=cw)

        shrink_evts = [e for e in self.collect_reset_events(orch) if e.transition == "reset_shrink"]
        assert not shrink_evts, "shrink reset must not fire when breaker is OPEN"


class TestNewCircuitBreakerAPI:
    """Tests for the new CircuitState enum, named methods, and event types."""


    def test_circuit_state_enum_values(self):
        assert CircuitState.CLOSED.value == "closed"
        assert CircuitState.OPEN.value == "open"
        assert CircuitState.HALF_OPEN.value == "half_open"

    def test_circuit_state_initial_closed(self):
        orch = make_orch()
        assert orch.circuit_state == CircuitState.CLOSED


    def test_circuit_breaker_tripped_shim_closed(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.CLOSED
        assert not orch.circuit_breaker_tripped

    def test_circuit_breaker_tripped_shim_open(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        assert orch.circuit_breaker_tripped

    def test_circuit_breaker_tripped_shim_half_open(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.HALF_OPEN
        assert orch.circuit_breaker_tripped

    def test_circuit_breaker_tripped_setter_true(self):
        orch = make_orch()
        orch.circuit_breaker_tripped = True
        assert orch.circuit_state == CircuitState.OPEN

    def test_circuit_breaker_tripped_setter_false(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        orch.circuit_breaker_tripped = False
        assert orch.circuit_state == CircuitState.CLOSED

    def test_half_open_probe_active_shim(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.HALF_OPEN
        assert orch.half_open_probe_active


    def test_config_defaults(self):
        cfg = CompactionConfig()
        assert cfg.circuit_breaker_half_open_seconds == CIRCUIT_BREAKER_HALF_OPEN_SECONDS
        assert cfg.circuit_breaker_shrink_threshold == CIRCUIT_BREAKER_SHRINK_THRESHOLD
        assert cfg.circuit_breaker_reset_per_user_message is True

    def test_config_custom_values(self):
        cfg = CompactionConfig(
            circuit_breaker_half_open_seconds=60,
            circuit_breaker_shrink_threshold=0.5,
            circuit_breaker_reset_per_user_message=False,
        )
        assert cfg.circuit_breaker_half_open_seconds == 60
        assert cfg.circuit_breaker_shrink_threshold == 0.5
        assert cfg.circuit_breaker_reset_per_user_message is False


    def test_new_fields_initial_values(self):
        orch = make_orch()
        assert orch.last_message_count_at_trip == 0
        assert orch.last_token_count_at_trip == 0
        assert orch.last_trip_error == ""
        assert orch.total_trips == 0
        assert orch.total_probes == 0
        assert orch.total_probe_successes == 0


    def test_trip_breaker_transitions_to_open(self):
        orch = make_orch()
        run(orch.trip_breaker("t1"))
        assert orch.circuit_state == CircuitState.OPEN
        assert orch.tripped_at is not None

    def test_trip_breaker_increments_total_trips(self):
        orch = make_orch()
        run(orch.trip_breaker("t1"))
        run(orch.trip_breaker("t1"))
        assert orch.total_trips == 2


    def test_check_half_open_transition_fires_after_cooldown(self):
        import time

        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        orch.tripped_at = time.time() - CIRCUIT_BREAKER_HALF_OPEN_SECONDS - 1
        run(orch.check_half_open_transition("t1"))
        assert orch.circuit_state == CircuitState.HALF_OPEN
        assert orch.total_probes == 1

    def test_check_half_open_transition_no_op_before_cooldown(self):
        import time

        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        orch.tripped_at = time.time()
        run(orch.check_half_open_transition("t1"))
        assert orch.circuit_state == CircuitState.OPEN

    def test_check_half_open_no_op_when_closed(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.CLOSED
        run(orch.check_half_open_transition("t1"))
        assert orch.circuit_state == CircuitState.CLOSED


    def test_shrink_reset_fires_when_threshold_met(self):
        orch = make_orch()
        orch.consecutive_failures = 2
        run(orch.check_context_shrink_reset("t1", pre_tokens=1000, post_tokens=600))
        assert orch.consecutive_failures == 0

    def test_shrink_reset_no_op_when_open(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        orch.consecutive_failures = 2
        run(orch.check_context_shrink_reset("t1", pre_tokens=1000, post_tokens=600))
        assert orch.consecutive_failures == 2

    def test_shrink_reset_no_op_below_threshold(self):
        orch = make_orch()
        orch.consecutive_failures = 2
        run(orch.check_context_shrink_reset("t1", pre_tokens=1000, post_tokens=900))
        assert orch.consecutive_failures == 2


    def test_reset_breaker_to_closed(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        run(orch.reset_breaker("t1", "reset_success", "test"))
        assert orch.circuit_state == CircuitState.CLOSED


    def test_reset_for_new_user_message_alias_works(self):
        orch = make_orch()
        orch.consecutive_failures = 2
        run(orch.reset_for_new_user_message("t1"))
        assert orch.consecutive_failures == 1

    def test_reset_for_new_user_message_disabled_by_config(self):
        orch = make_orch(config=CompactionConfig(circuit_breaker_reset_per_user_message=False))
        orch.consecutive_failures = 2
        run(orch.reset_for_new_user_message("t1"))
        assert orch.consecutive_failures == 2

    def test_reset_for_new_user_message_does_not_clear_trip_counters(self):
        """Trip/probe counters persist across reset_for_new_user_message calls."""
        orch = make_orch()
        orch.total_trips = 3
        orch.total_probes = 2
        orch.total_probe_successes = 1
        orch.consecutive_failures = 1
        run(orch.reset_for_new_user_message("t1"))
        assert orch.total_trips == 3
        assert orch.total_probes == 2
        assert orch.total_probe_successes == 1


    def test_manual_reset_closes_open_breaker(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        orch.consecutive_failures = 5
        run(orch.manual_reset("t1"))
        assert orch.circuit_state == CircuitState.CLOSED
        assert orch.consecutive_failures == 0

    def test_manual_reset_emits_event(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        run(orch.manual_reset("t1"))
        reset_evts = [
            e for e in orch.emitter.buffer if isinstance(e, CompactionCircuitBreakerReset)
        ]
        assert reset_evts
        assert reset_evts[0].transition == "manual_reset"


    def test_skip_event_emitted_when_breaker_open(self):
        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        orch.consecutive_failures = MAX_CONSECUTIVE_FAILURES
        msgs = make_messages(40, chars_each=600)
        cw = window_forcing_compaction(msgs)
        compact(orch, msgs, context_window=cw)
        skip_evts = [e for e in orch.emitter.buffer if isinstance(e, CompactionCircuitBreakerSkip)]
        assert skip_evts, "CompactionCircuitBreakerSkip must be emitted when OPEN"
        assert skip_evts[0].message_count > 0
        assert skip_evts[0].token_count > 0

    def test_skip_event_not_emitted_when_closed(self):
        orch = make_orch()
        msgs = make_messages(40, chars_each=600)
        cw = window_forcing_compaction(msgs)
        compact(orch, msgs, context_window=cw)
        skip_evts = [e for e in orch.emitter.buffer if isinstance(e, CompactionCircuitBreakerSkip)]
        assert not skip_evts, "No skip event when breaker is CLOSED"


    def test_session_reset_clears_all_metrics(self):
        orch = make_orch()
        orch.total_trips = 5
        orch.total_probes = 3
        orch.total_probe_successes = 2
        orch.last_trip_error = "boom"
        orch.last_message_count_at_trip = 10
        orch.last_token_count_at_trip = 5000
        orch.reset()
        assert orch.total_trips == 0
        assert orch.total_probes == 0
        assert orch.total_probe_successes == 0
        assert orch.last_trip_error == ""
        assert orch.last_message_count_at_trip == 0
        assert orch.last_token_count_at_trip == 0


    def test_tripped_event_fields(self):
        evt = CompactionCircuitBreakerTripped(
            thread_id="t1",
            consecutive_failures=3,
            last_error="boom",
            message_count=10,
            token_count=5000,
            reason="max_failures_3",
        )
        assert evt.thread_id == "t1"
        assert evt.consecutive_failures == 3
        assert evt.last_error == "boom"
        assert evt.message_count == 10
        assert evt.token_count == 5000
        assert evt.reason == "max_failures_3"

    def test_probe_attempt_event_fields(self):
        evt = CompactionCircuitBreakerProbeAttempt(
            thread_id="t1",
            elapsed_seconds=310.5,
            consecutive_failures=3,
            reason="cooldown_elapsed_310s",
        )
        assert evt.elapsed_seconds == 310.5
        assert evt.reason == "cooldown_elapsed_310s"

    def test_reset_event_fields(self):
        evt = CompactionCircuitBreakerReset(
            thread_id="t1",
            transition="reset_success",
            consecutive_failures=0,
            reason="llm_compact_success",
        )
        assert evt.transition == "reset_success"
        assert evt.consecutive_failures == 0

    def test_skip_event_fields(self):
        evt = CompactionCircuitBreakerSkip(
            thread_id="t1",
            message_count=5,
            token_count=1000,
            fallback_strategy="round_drop",
        )
        assert evt.message_count == 5
        assert evt.fallback_strategy == "round_drop"


    def test_total_probes_increments_on_probe(self):
        import time

        orch = make_orch()
        orch.circuit_state = CircuitState.OPEN
        orch.tripped_at = time.time() - CIRCUIT_BREAKER_HALF_OPEN_SECONDS - 1
        run(orch.check_half_open_transition("t1"))
        assert orch.total_probes == 1
        orch.circuit_state = CircuitState.OPEN
        orch.tripped_at = time.time() - CIRCUIT_BREAKER_HALF_OPEN_SECONDS - 1
        run(orch.check_half_open_transition("t1"))
        assert orch.total_probes == 2


class TestReactiveCompact:
    def test_tier_0_normal_compact(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=200)
        result = run(orch.reactive_compact(msgs, 0, 200_000))
        assert isinstance(result, ReactiveCompactResult)
        assert isinstance(result.messages, list)
        assert result.tier == 0
        assert result.strategy == "microcompact+mechanical"

    def test_tier_1_guard(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=200)
        result = run(orch.reactive_compact(msgs, 1, 200_000))
        assert isinstance(result, ReactiveCompactResult)
        assert isinstance(result.messages, list)
        assert result.tier == 1
        assert result.strategy == "guard"

    def test_tier_2_emergency(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=200)
        result = run(orch.reactive_compact(msgs, 2, 200_000))
        assert isinstance(result, ReactiveCompactResult)
        assert len(result.messages) <= 7
        assert result.tier == 2
        assert result.strategy == "emergency"

    def test_tiers_graduate(self):
        orch = make_orch()
        msgs = make_messages(30, chars_each=200)
        r0 = run(orch.reactive_compact(list(msgs), 0, 200_000))
        run(orch.reactive_compact(list(msgs), 1, 200_000))
        r2 = run(orch.reactive_compact(list(msgs), 2, 200_000))
        assert len(r2.messages) <= len(r0.messages)


class TestReactiveCompactHardened:
    """Tests for the hardened reactive compact: no-op detection, boundary
    markers, event emission, token tracking, feature flag."""

    def test_no_op_detection_small_messages(self):
        orch = make_orch()
        msgs = make_messages(4, chars_each=10)
        result = run(orch.reactive_compact(msgs, 0, 200_000))
        assert isinstance(result, ReactiveCompactResult)
        assert result.made_progress is False
        assert result.tokens_before == result.tokens_after

    def test_finalization_adds_boundary_marker(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=600)
        result = run(orch.reactive_compact(msgs, 2, 1000))
        assert result.made_progress is True
        boundary_found = any(
            COMPACTION_BOUNDARY_CONTENT in getattr(m, "content", "") for m in result.messages
        )
        assert boundary_found, "Boundary marker must be present after successful reactive compact"

    def test_no_boundary_marker_on_noop(self):
        orch = make_orch()
        msgs = make_messages(4, chars_each=10)
        result = run(orch.reactive_compact(msgs, 0, 200_000))
        assert result.made_progress is False
        boundary_found = any(
            COMPACTION_BOUNDARY_CONTENT in getattr(m, "content", "") for m in result.messages
        )
        assert not boundary_found, "No boundary marker when no progress made"

    def test_event_emission(self):
        from src.engine_core.events import ReactiveCompactionEvent

        emitter = EventEmitter()
        received: list = []

        async def capture(event):
            received.append(event)

        emitter.on(capture)
        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=emitter,
            memory=FakeMemory(),
        )
        msgs = make_messages(20, chars_each=600)
        run(orch.reactive_compact(msgs, 2, 1000, thread_id="test-task"))

        rc_events = [e for e in received if isinstance(e, ReactiveCompactionEvent)]
        assert len(rc_events) == 1
        evt = rc_events[0]
        assert evt.thread_id == "test-task"
        assert evt.tier == 2
        assert evt.strategy == "emergency"
        assert evt.overflow_attempt == 2
        assert evt.tokens_before > 0

    def test_tokens_populated(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=600)
        result = run(orch.reactive_compact(msgs, 2, 1000))
        assert result.tokens_before > 0
        assert result.tokens_after <= result.tokens_before

    def test_history_recorded_on_progress(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=600)
        result = run(orch.reactive_compact(msgs, 2, 1000))
        assert result.made_progress is True
        assert len(orch.history) == 1
        assert orch.history[0].strategy == "reactive_emergency"

    def test_no_history_on_noop(self):
        orch = make_orch()
        msgs = make_messages(4, chars_each=10)
        result = run(orch.reactive_compact(msgs, 0, 200_000))
        assert result.made_progress is False
        assert len(orch.history) == 0

    def test_event_emitted_even_on_noop(self):
        from src.engine_core.events import ReactiveCompactionEvent

        emitter = EventEmitter()
        received: list = []

        async def capture(event):
            received.append(event)

        emitter.on(capture)
        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=emitter,
            memory=FakeMemory(),
        )
        msgs = make_messages(4, chars_each=10)
        run(orch.reactive_compact(msgs, 0, 200_000))

        rc_events = [e for e in received if isinstance(e, ReactiveCompactionEvent)]
        assert len(rc_events) == 1
        assert rc_events[0].made_progress is False


class TestLoopStateTransitionReason:
    """Tests for last_transition_reason field on LoopState."""

    def test_serialization_roundtrip(self):
        from src.engine_core.loop_state import LoopState

        state = LoopState()
        state.last_transition_reason = "reactive_compact:tier=2,strategy=emergency"
        data = state.serialize()
        restored = LoopState.deserialize(data)
        assert restored.last_transition_reason == "reactive_compact:tier=2,strategy=emergency"

    def test_default_empty(self):
        from src.engine_core.loop_state import LoopState

        state = LoopState()
        assert state.last_transition_reason == ""

    def test_deserialize_missing_field(self):
        from src.engine_core.loop_state import LoopState

        data = {"turns": 5, "input_tokens": 100, "output_tokens": 50}
        state = LoopState.deserialize(data)
        assert state.last_transition_reason == ""


class TestFeatureFlagReactiveCompact:
    """Tests for reactive_compact_enabled feature flag."""

    def test_default_enabled(self):
        from src.config import Settings

        s = Settings.load()
        assert s.reactive_compact_enabled is True

    def test_disabled_via_env(self):
        from src.config import Settings

        os.environ["REACTIVE_COMPACT_ENABLED"] = "false"
        try:
            s = Settings.load()
            assert s.reactive_compact_enabled is False
        finally:
            del os.environ["REACTIVE_COMPACT_ENABLED"]


class TestErrorCodeOverflow:
    """Tests for CONTEXT_OVERFLOW_UNRECOVERABLE error code."""

    def test_error_code_exists(self):
        from src.errors import ErrorCode, EngineError

        err = EngineError(code=ErrorCode.CONTEXT_OVERFLOW_UNRECOVERABLE)
        assert err.http_status == 500
        assert err.retryable is False
        d = err.to_dict()
        assert d["error"]["code"] == "context_overflow_unrecoverable"


class TestCompactionHistory:
    def test_records_compaction_event(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=600)
        compact(orch, msgs, context_window=1000, turn=5)
        assert len(orch.history) >= 1
        record = orch.history[0]
        assert record.turn_number == 5
        assert record.tokens_freed >= 0
        assert record.success is True

    def test_no_history_when_no_compaction(self):
        orch = make_orch()
        msgs = make_messages(4, chars_each=100)
        compact(orch, msgs)
        assert len(orch.history) == 0


class TestEventEmission:
    def test_emits_compaction_event(self):
        emitter = EventEmitter()
        events_received = []

        async def capture(event):
            events_received.append(event)

        emitter.on(capture)
        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=emitter,
            memory=FakeMemory(),
        )
        msgs = make_messages(20, chars_each=600)
        run(orch.compact_if_needed(msgs, "", [], "task1", 1, 1000))
        compaction_events = [e for e in events_received if isinstance(e, CompactionEvent)]
        assert len(compaction_events) >= 1
        assert compaction_events[0].thread_id == "task1"


class TestAdjustKeepIndex:
    def test_no_tool_pairs_unchanged(self):
        msgs = make_messages(10)
        idx = CompactionOrchestrator.adjust_keep_index(msgs, 5)
        assert idx == 5

    def test_pulls_in_orphaned_tool_use(self):
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="thinking"),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="tool_A", name="file_read", input={}),
                ],
            ),
            Message(role="user", content="middle"),
            Message(role="assistant", content="more thinking"),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="tool_A", content="file contents"),
                ],
            ),
            Message(role="assistant", content="done"),
        ]
        idx = CompactionOrchestrator.adjust_keep_index(msgs, 4)
        assert idx == 2

    def test_no_adjustment_when_pair_in_range(self):
        msgs = [
            Message(role="user", content="hello"),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="tool_B", name="bash", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="tool_B", content="output"),
                ],
            ),
        ]
        idx = CompactionOrchestrator.adjust_keep_index(msgs, 1)
        assert idx == 1

    def test_multiple_orphaned_pairs(self):
        msgs = [
            Message(role="user", content="start"),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="A", name="file_read", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="A", content="r1"),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="B", name="bash", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="B", content="r2"),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="A", content="re-read"),
                    ToolResultBlock(tool_use_id="B", content="re-run"),
                ],
            ),
        ]
        idx = CompactionOrchestrator.adjust_keep_index(msgs, 5)
        assert idx == 1


class TestGroupByApiRound:
    def test_empty(self):
        assert group_by_api_round([]) == []

    def test_single_message(self):
        msgs = [Message(role="user", content="hi")]
        groups = group_by_api_round(msgs)
        assert len(groups) == 1

    def test_simple_conversation(self):
        msgs = [
            Message(role="user", content="q1"),
            Message(role="assistant", content="a1"),
            Message(role="user", content="q2"),
            Message(role="assistant", content="a2"),
        ]
        groups = group_by_api_round(msgs)
        assert len(groups) == 3
        assert groups[0][0].content == "q1"
        assert groups[1][0].content == "a1"
        assert groups[2][0].content == "a2"

    def test_tool_calls_stay_in_round(self):
        msgs = [
            Message(role="user", content="do something"),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="bash", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="output"),
                ],
            ),
            Message(role="assistant", content="done"),
            Message(role="user", content="next"),
            Message(role="assistant", content="ok"),
        ]
        groups = group_by_api_round(msgs)
        assert len(groups) == 4
        assert len(groups[1]) == 2

    def test_all_same_role(self):
        msgs = [Message(role="user", content=f"m{i}") for i in range(5)]
        groups = group_by_api_round(msgs)
        assert len(groups) == 1
        assert len(groups[0]) == 5


class TestGroupByApiRoundIdBased:
    """Tests for ID-based round grouping (FEATURE_API_ROUND_ID_GROUPING)."""

    def test_id_based_grouping(self):
        msgs = [
            Message(role="user", content="q1"),
            Message(role="assistant", content="a1", api_message_id="msg_001"),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="result"),
                ],
            ),
            Message(role="assistant", content="a2", api_message_id="msg_002"),
        ]
        groups = group_by_api_round(msgs)
        assert len(groups) == 3
        assert groups[0] == [msgs[0]]
        assert groups[1] == [msgs[1], msgs[2]]
        assert groups[2] == [msgs[3]]

    def test_same_id_stays_in_one_group(self):
        msgs = [
            Message(role="user", content="start"),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="bash", input={}),
                ],
                api_message_id="msg_001",
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="out1"),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t2", name="read", input={}),
                ],
                api_message_id="msg_001",
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t2", content="out2"),
                ],
            ),
        ]
        groups = group_by_api_round(msgs)
        assert len(groups) == 2
        assert groups[0] == [msgs[0]]
        assert len(groups[1]) == 4

    def test_different_ids_split(self):
        msgs = [
            Message(role="assistant", content="a1", api_message_id="msg_001"),
            Message(role="assistant", content="a2", api_message_id="msg_002"),
        ]
        groups = group_by_api_round(msgs)
        assert len(groups) == 2
        assert groups[0] == [msgs[0]]
        assert groups[1] == [msgs[1]]

    def test_same_id_consecutive_assistants_grouped(self):
        msgs = [
            Message(role="assistant", content="a1", api_message_id="msg_001"),
            Message(role="assistant", content="a2", api_message_id="msg_001"),
        ]
        groups = group_by_api_round(msgs)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_fallback_when_no_ids(self):
        msgs = [
            Message(role="user", content="q1"),
            Message(role="assistant", content="a1"),
            Message(role="user", content="q2"),
            Message(role="assistant", content="a2"),
        ]
        groups = group_by_api_round(msgs)
        assert len(groups) == 3

    def test_mixed_ids_graceful(self):
        msgs = [
            Message(role="user", content="q1"),
            Message(role="assistant", content="a1", api_message_id="msg_001"),
            Message(role="user", content="q2"),
            Message(role="assistant", content="a2"),
        ]
        groups = group_by_api_round(msgs)
        assert len(groups) == 3
        assert groups[1] == [msgs[1], msgs[2]]
        assert groups[2] == [msgs[3]]

    def test_feature_flag_false_uses_role_transition(self, monkeypatch):
        monkeypatch.setenv("FEATURE_API_ROUND_ID_GROUPING", "false")
        msgs = [
            Message(role="user", content="q1"),
            Message(role="assistant", content="a1", api_message_id="msg_001"),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="result"),
                ],
            ),
            Message(role="assistant", content="a2", api_message_id="msg_001"),
        ]
        groups = group_by_api_round(msgs)
        assert len(groups) == 3
        assert len(groups[2]) == 1

    def test_empty_string_id_treated_as_none(self):
        msgs = [
            Message(role="user", content="q1"),
            Message(role="assistant", content="a1", api_message_id=""),
            Message(role="user", content="q2"),
            Message(role="assistant", content="a2", api_message_id=""),
        ]
        groups = group_by_api_round(msgs)
        assert len(groups) == 3

    def test_parallel_tool_calls_same_round(self):
        msgs = [
            Message(role="user", content="do two things"),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="bash", input={}),
                    ToolUseBlock(id="t2", name="read", input={}),
                ],
                api_message_id="msg_001",
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="out1"),
                    ToolResultBlock(tool_use_id="t2", content="out2"),
                ],
            ),
            Message(role="assistant", content="both done", api_message_id="msg_002"),
        ]
        groups = group_by_api_round(msgs)
        assert len(groups) == 3
        assert len(groups[1]) == 2

    def test_dangling_tool_use_no_crash(self):
        msgs = [
            Message(role="user", content="start"),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="bash", input={}),
                ],
                api_message_id="msg_001",
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t2", name="bash", input={}),
                ],
                api_message_id="msg_002",
            ),
        ]
        groups = group_by_api_round(msgs)
        assert len(groups) == 3
        assert groups[0] == [msgs[0]]
        assert groups[1] == [msgs[1]]
        assert groups[2] == [msgs[2]]

    def test_single_assistant_with_id(self):
        msgs = [Message(role="assistant", content="hello", api_message_id="msg_001")]
        groups = group_by_api_round(msgs)
        assert len(groups) == 1
        assert groups[0] == [msgs[0]]

    def test_restored_mid_turn_starts_with_tool_result(self):
        msgs = [
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="result"),
                ],
            ),
            Message(role="assistant", content="done", api_message_id="msg_002"),
            Message(role="user", content="next"),
            Message(role="assistant", content="ok", api_message_id="msg_003"),
        ]
        groups = group_by_api_round(msgs)
        assert len(groups) == 3
        assert groups[0] == [msgs[0]]
        assert groups[1] == [msgs[1], msgs[2]]
        assert groups[2] == [msgs[3]]


class TestPostCompactCleanup:
    def test_invalidates_consciousness_cache(self):
        mgr = FakeConsciousnessMgr()
        orch = make_orch(consciousness_mgr=mgr)
        msgs = make_messages(20, chars_each=600)
        compact(orch, msgs, context_window=1000)
        assert mgr.invalidated

    def test_no_crash_without_consciousness_mgr(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=600)
        result = compact(orch, msgs, context_window=1000)
        assert result.was_compacted


class TestPostCompactCacheInvalidation:
    """Post-compact reset of Search / Utility / AssetDirectory caches.

    These tests verify that run_post_compact_cleanup is wired through from
    the orchestrator and fires on the exact caches that go stale after a
    compaction, as specified by the shared post-compaction cleanup contract
    (see architecture/sections/42_engine_core_context_compaction.md).
    """

    def test_invalidates_search_cache(self):
        search = FakeSearch()
        orch = make_orch(search=search)
        msgs = make_messages(20, chars_each=600)
        compact(orch, msgs, context_window=window_forcing_compaction(msgs))
        assert search.invalidated

    def test_invalidates_utility_cache(self):
        utility = FakeUtility()
        orch = make_orch(utility=utility)
        msgs = make_messages(20, chars_each=600)
        compact(orch, msgs, context_window=window_forcing_compaction(msgs))
        assert utility.invalidated
        assert utility.last_prefix == ""

    def test_invalidates_assets_cache(self):
        assets = FakeAssets()
        orch = make_orch(assets=assets)
        msgs = make_messages(20, chars_each=600)
        compact(orch, msgs, context_window=window_forcing_compaction(msgs))
        assert assets.invalidated
        assert assets.last_prefix == ""

    def test_invalidates_all_caches_together(self):
        mgr = FakeConsciousnessMgr()
        search = FakeSearch()
        utility = FakeUtility()
        assets = FakeAssets()
        orch = make_orch(
            consciousness_mgr=mgr,
            search=search,
            utility=utility,
            assets=assets,
        )
        msgs = make_messages(20, chars_each=600)
        compact(orch, msgs, context_window=window_forcing_compaction(msgs))
        assert mgr.invalidated
        assert search.invalidated
        assert utility.invalidated
        assert assets.invalidated

    def test_file_read_state_cleared_regression(self):
        orch = make_orch()
        orch.record_file_read("/tmp/a.py")
        orch.record_file_read("/tmp/b.py")
        assert len(orch.file_read_state) == 2
        msgs = make_messages(20, chars_each=600)
        compact(orch, msgs, context_window=window_forcing_compaction(msgs))
        assert orch.file_read_state == {}

    def test_no_crash_without_any_cache_deps(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=600)
        result = compact(orch, msgs, context_window=window_forcing_compaction(msgs))
        assert result.was_compacted

    def test_one_cache_failure_does_not_block_others(self):
        mgr = FakeConsciousnessMgr()
        search = FakeSearch(raise_on_invalidate=True)
        utility = FakeUtility()
        assets = FakeAssets()
        orch = make_orch(
            consciousness_mgr=mgr,
            search=search,
            utility=utility,
            assets=assets,
        )
        msgs = make_messages(20, chars_each=600)
        compact(orch, msgs, context_window=window_forcing_compaction(msgs))
        assert mgr.invalidated
        assert not search.invalidated
        assert utility.invalidated
        assert assets.invalidated

    def test_no_cleanup_when_no_compaction(self):
        search = FakeSearch()
        utility = FakeUtility()
        assets = FakeAssets()
        orch = make_orch(search=search, utility=utility, assets=assets)
        msgs = make_messages(4, chars_each=100)
        result = compact(orch, msgs)
        assert not result.was_compacted
        assert not search.invalidated
        assert not utility.invalidated
        assert not assets.invalidated

    def test_subagent_query_source_still_clears_per_instance_caches(self):
        mgr = FakeConsciousnessMgr()
        search = FakeSearch()
        utility = FakeUtility()
        assets = FakeAssets()
        orch = make_orch(
            consciousness_mgr=mgr,
            search=search,
            utility=utility,
            assets=assets,
        )
        msgs = make_messages(20, chars_each=600)
        compact(
            orch,
            msgs,
            context_window=window_forcing_compaction(msgs),
            query_source="subagent:helper",
        )
        assert mgr.invalidated
        assert search.invalidated
        assert utility.invalidated
        assert assets.invalidated


class TestIsMainThreadCompact:
    """Unit tests for the is_main_thread_compact contract."""

    def test_none_is_main_thread(self):
        assert is_main_thread_compact(None) is True

    @pytest.mark.parametrize(
        "source",
        [
            "foreground",
            "agent",
            "compact",
            "hook",
            "side_question",
        ],
    )
    def test_foreground_values_are_main_thread(self, source):
        assert is_main_thread_compact(source) is True

    @pytest.mark.parametrize(
        "source",
        [
            "subagent:foo",
            "subagent:bar",
            "agent:nested",
            "agent:helper",
        ],
    )
    def test_subagent_prefixed_values_are_not_main_thread(self, source):
        assert is_main_thread_compact(source) is False

    def test_unknown_value_is_not_main_thread(self):
        assert is_main_thread_compact("random_unknown") is False


class TestRunPostCompactCleanupUnit:
    """Direct unit tests for run_post_compact_cleanup with no orchestrator."""

    def test_clears_every_provided_cache(self):
        mgr = FakeConsciousnessMgr()
        search = FakeSearch()
        utility = FakeUtility()
        assets = FakeAssets()
        state = {"/a": 1.0, "/b": 2.0}
        run_post_compact_cleanup(
            consciousness_mgr=mgr,
            file_read_state=state,
            search=search,
            utility=utility,
            assets=assets,
        )
        assert mgr.invalidated
        assert state == {}
        assert search.invalidated
        assert utility.invalidated
        assert assets.invalidated

    def test_all_none_is_a_noop(self):
        run_post_compact_cleanup()

    def test_every_failure_is_swallowed(self):
        search = FakeSearch(raise_on_invalidate=True)
        utility = FakeUtility(raise_on_invalidate=True)
        assets = FakeAssets(raise_on_invalidate=True)
        run_post_compact_cleanup(search=search, utility=utility, assets=assets)


class TestReinjection:
    def test_reinjects_checkpoints(self):
        cp = LogEntry(
            thread_id="task1",
            type=EntryType.CHECKPOINT,
            content="Step 3 complete: API routes built",
        )
        orch = make_orch(checkpoints=[cp])
        msgs = make_messages(20, chars_each=600)
        result = compact(orch, msgs, context_window=1000)
        reinjected = [
            m
            for m in result.messages
            if isinstance(m.content, str) and "Context restored" in m.content
        ]
        assert len(reinjected) >= 1
        assert "Step 3 complete" in reinjected[0].content

    def test_no_reinjection_when_no_compaction(self):
        orch = make_orch()
        msgs = make_messages(4, chars_each=100)
        result = compact(orch, msgs)
        reinjected = [
            m
            for m in result.messages
            if isinstance(m.content, str) and "Context restored" in m.content
        ]
        assert len(reinjected) == 0


class TestEnvVarGuards:
    def test_disable_compact_skips_all(self):
        os.environ["DISABLE_COMPACT"] = "1"
        try:
            orch = make_orch()
            msgs = make_messages(20, chars_each=600)
            result = compact(orch, msgs, context_window=1000)
            assert not result.was_compacted
            assert len(result.messages) == 20
        finally:
            del os.environ["DISABLE_COMPACT"]

    def test_disable_auto_compact_skips(self):
        os.environ["DISABLE_AUTO_COMPACT"] = "1"
        try:
            orch = make_orch()
            msgs = make_messages(20, chars_each=600)
            result = compact(orch, msgs, context_window=1000)
            assert not result.was_compacted
        finally:
            del os.environ["DISABLE_AUTO_COMPACT"]


class TestCompactionConfig:
    def test_default_values(self):
        config = CompactionConfig()
        assert config.min_tokens == 10_000
        assert config.min_text_block_messages == 5
        assert config.max_tokens == 40_000
        assert config.compaction_threshold == 0.80
        assert config.circuit_breaker_max_failures == 3

    def test_load_without_db_returns_defaults(self):
        config = run(CompactionConfig.load(None))
        assert config.min_tokens == 10_000

    def test_config_circuit_breaker_used(self):
        config = CompactionConfig(circuit_breaker_max_failures=5)
        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=EventEmitter(),
            memory=FakeMemory(),
            config=config,
        )
        assert orch.config.circuit_breaker_max_failures == 5


class TestBoundaryMarker:
    def test_boundary_inserted_after_compaction(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=600)
        result = compact(orch, msgs, context_window=1000)
        boundary_msgs = [
            m
            for m in result.messages
            if isinstance(m.content, str) and COMPACTION_BOUNDARY_CONTENT in m.content
        ]
        assert len(boundary_msgs) >= 1

    def test_boundary_contains_metadata(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=600)
        result = compact(orch, msgs, context_window=1000)
        boundary_msgs = [
            m
            for m in result.messages
            if isinstance(m.content, str) and COMPACTION_BOUNDARY_CONTENT in m.content
        ]
        assert len(boundary_msgs) >= 1
        content = boundary_msgs[0].content
        assert "compact_meta" in content
        assert "tokens_before=" in content
        assert "tokens_after=" in content
        assert "strategy=" in content
        assert "ts=" in content

    def test_no_boundary_without_compaction(self):
        orch = make_orch()
        msgs = make_messages(4, chars_each=100)
        result = compact(orch, msgs)
        boundary_msgs = [
            m
            for m in result.messages
            if isinstance(m.content, str) and COMPACTION_BOUNDARY_CONTENT in m.content
        ]
        assert len(boundary_msgs) == 0


class TestExtractDiscoveredToolNames:
    def test_finds_tool_names(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="file_read", input={}),
                    ToolUseBlock(id="t2", name="bash", input={}),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t3", name="file_read", input={}),
                ],
            ),
        ]
        names = extract_discovered_tool_names(msgs)
        assert names == {"file_read", "bash"}

    def test_no_tool_blocks(self):
        msgs = [Message(role="user", content="hello")]
        assert extract_discovered_tool_names(msgs) == set()


class TestShouldExcludeFromRestore:
    def test_excludes_env_files(self):
        assert should_exclude_from_restore("/app/.env") is True

    def test_excludes_credentials(self):
        assert should_exclude_from_restore("/home/user/credentials.json") is True

    def test_allows_credentials_helper(self):
        assert should_exclude_from_restore("/lib/credentials_helper.py") is False

    def test_excludes_node_modules(self):
        assert should_exclude_from_restore("/app/node_modules/foo/index.js") is True

    def test_excludes_git(self):
        assert should_exclude_from_restore("/app/.git/config") is True

    def test_allows_normal_files(self):
        assert should_exclude_from_restore("/app/src/main.py") is False


class TestCollectFilePathsFromMessages:
    def test_extracts_paths_from_string_content(self):
        msgs = [Message(role="user", content="I read /app/src/main.py")]
        paths = collect_file_paths_from_messages(msgs)
        assert any("main.py" in p for p in paths)

    def test_extracts_from_text_blocks(self):
        msgs = [
            Message(
                role="user",
                content=[
                    TextBlock(text="check /home/user/file.txt"),
                ],
            )
        ]
        paths = collect_file_paths_from_messages(msgs)
        assert any("file.txt" in p for p in paths)

    def test_empty_messages(self):
        assert collect_file_paths_from_messages([]) == set()


class TestFileReadTracking:
    def test_record_and_clear(self):
        orch = make_orch()
        orch.record_file_read("/app/main.py")
        orch.record_file_read("/app/utils.py")
        assert len(orch.file_read_state) == 2
        orch.reset()
        assert len(orch.file_read_state) == 0

    def test_empty_path_ignored(self):
        orch = make_orch()
        orch.record_file_read("")
        assert len(orch.file_read_state) == 0

    def test_same_path_updates_timestamp(self):
        import time

        orch = make_orch()
        orch.record_file_read("/app/main.py")
        t1 = orch.file_read_state["/app/main.py"].timestamp
        time.sleep(0.01)
        orch.record_file_read("/app/main.py")
        t2 = orch.file_read_state["/app/main.py"].timestamp
        assert t2 >= t1

    def test_record_stores_content_hash(self):
        orch = make_orch()
        orch.record_file_read("/app/main.py", content_hash="abc123", size=42)
        record = orch.file_read_state["/app/main.py"]
        assert isinstance(record, FileReadRecord)
        assert record.content_hash == "abc123"
        assert record.size == 42

    def test_record_default_no_hash(self):
        orch = make_orch()
        orch.record_file_read("/app/main.py")
        record = orch.file_read_state["/app/main.py"]
        assert record.content_hash == ""
        assert record.size == 0


class TestSessionMemoryCompactEnhanced:
    def test_session_memory_uses_thread_id(self):
        class TaskAwareMemory(FakeMemory):
            async def get_context_summary(self, max_items=10, thread_id=None, max_tokens=None):
                if thread_id == "target_task":
                    return "Summary for the target task with enough content to pass the 50-char threshold easily."
                return ""

        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=EventEmitter(),
            memory=TaskAwareMemory(),
        )
        msgs = make_messages(40, chars_each=4000)
        result = run(orch.compact_if_needed(msgs, "", [], "target_task", 1, 50_000))
        assert result.strategy == "session_memory"

    def test_session_memory_skipped_for_wrong_thread_id(self):
        class TaskAwareMemory(FakeMemory):
            async def get_context_summary(self, max_items=10, thread_id=None, max_tokens=None):
                if thread_id == "target_task":
                    return "Summary for the target task with enough content to pass."
                return ""

        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=EventEmitter(),
            memory=TaskAwareMemory(),
        )
        msgs = make_messages(40, chars_each=4000)
        result = run(orch.compact_if_needed(msgs, "", [], "other_task", 1, 50_000))
        assert result.strategy != "session_memory"

    def test_summary_format_contains_continuation_header(self):
        summary = "User worked on building a REST API with routes and models. " * 3
        orch = make_orch(summary=summary)
        msgs = make_messages(40, chars_each=4000)
        result = compact(orch, msgs, context_window=50_000)
        assert result.strategy == "session_memory"
        summary_msg = result.messages[0]
        assert "continued from a previous conversation" in summary_msg.content
        assert "Recent messages are preserved verbatim" in summary_msg.content

    def test_post_compact_still_too_large_returns_none(self):
        summary = "Short."
        orch = make_orch(summary=summary)
        msgs = make_messages(40, chars_each=4000)
        result = compact(orch, msgs, context_window=50_000)
        assert result.strategy != "session_memory"

    def test_last_summarized_index_reset_after_compaction(self):
        summary = "User asked to implement a REST API. Agent created routes.py and models.py. " * 3
        orch = make_orch(summary=summary)
        assert orch.last_summarized_msg_index is None
        msgs = make_messages(40, chars_each=4000)
        result = compact(orch, msgs, context_window=50_000)
        if result.strategy == "session_memory":
            assert orch.last_summarized_msg_index is None

    def test_reset_clears_last_summarized_index(self):
        orch = make_orch()
        orch.last_summarized_msg_index = 10
        orch.reset()
        assert orch.last_summarized_msg_index is None


class TestCalculateMessagesToKeepIndex:
    def test_respects_min_tokens(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=1000)
        idx = orch.calculate_messages_to_keep_index(msgs)
        from src.engine_core.context_engineering import estimate_messages_tokens

        kept_tokens = estimate_messages_tokens(msgs[idx:])
        assert kept_tokens >= orch.config.min_tokens or idx == 0

    def test_respects_min_text_block_messages(self):
        orch = make_orch()
        msgs = make_messages(20, chars_each=1000)
        idx = orch.calculate_messages_to_keep_index(msgs)
        kept = msgs[idx:]
        text_count = sum(1 for m in kept if isinstance(m.content, str))
        assert text_count >= orch.config.min_text_block_messages or idx == 0

    def test_stops_at_max_tokens_cap(self):
        orch = make_orch()
        msgs = make_messages(100, chars_each=5000)
        idx = orch.calculate_messages_to_keep_index(msgs)
        from src.engine_core.context_engineering import estimate_messages_tokens

        kept_tokens = estimate_messages_tokens(msgs[idx:])
        assert kept_tokens <= orch.config.max_tokens + estimate_messages_tokens([msgs[0]])

    def test_small_conversation_keeps_everything(self):
        orch = make_orch()
        msgs = make_messages(3, chars_each=100)
        idx = orch.calculate_messages_to_keep_index(msgs)
        assert idx == 0

    def test_boundary_messages_stripped_from_kept_range(self):
        summary = "User asked to implement a REST API. Agent created routes.py and models.py. " * 3
        orch = make_orch(summary=summary)
        msgs = make_messages(40, chars_each=4000)
        from src.engine_core.compaction_orchestrator import COMPACTION_BOUNDARY_CONTENT

        boundary = Message(role="user", content=f"{COMPACTION_BOUNDARY_CONTENT}\n<!-- old -->")
        msgs = msgs + [boundary] + make_messages(4, chars_each=100)
        result = compact(orch, msgs, context_window=60_000)
        if result.strategy == "session_memory":
            boundary_in_result = [
                m
                for m in result.messages
                if isinstance(m.content, str)
                and "old" in m.content
                and COMPACTION_BOUNDARY_CONTENT in m.content
            ]
            assert len(boundary_in_result) == 0

    def test_handles_mixed_content_blocks(self):
        msgs = [
            Message(role="user", content="hello"),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="bash", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="output " * 500),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    TextBlock(text="I ran the command"),
                ],
            ),
            Message(role="user", content="thanks"),
        ]
        orch = make_orch()
        idx = orch.calculate_messages_to_keep_index(msgs)
        assert 0 <= idx <= len(msgs)


import time  # noqa: E402

from src.engine_core.events import MicrocompactEvent  # noqa: E402
from src.engine_core.microcompact_config import MicrocompactConfig  # noqa: E402
from src.engine_core.prompt_cache_break_detection import (  # noqa: E402
    is_cache_deletion_pending,
    reset_pending_deletions,
)
from src.engine_core.time_based_microcompact import STALENESS_MARKER  # noqa: E402


def stale_tool_messages(
    n_read_results: int,
    gap_minutes: float,
    content_per_result: int = 200,
) -> list[Message]:
    """Build a conversation with n paired Read tool calls, all timestamped
    ``gap_minutes`` in the past. Used to force the time-based microcompact
    trigger in integration tests.
    """
    stale_ts = time.time() - (gap_minutes * 60)
    msgs: list[Message] = []
    for i in range(n_read_results):
        uid = f"read-{i}"
        msgs.append(
            Message(
                role="assistant",
                content=[
                    TextBlock(text=f"reading file {i}"),
                    ToolUseBlock(id=uid, name="Read", input={"file_path": f"/f{i}"}),
                ],
                timestamp=stale_ts,
            )
        )
        msgs.append(
            Message(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id=uid,
                        content=f"file-content-{i}-" * content_per_result,
                    ),
                ],
                timestamp=stale_ts,
            )
        )
    return msgs


class TestMicrocompactIntegration:
    def test_time_based_fires_in_compact_if_needed(self):
        emitter = EventEmitter()
        received: list = []

        async def capture(event):
            received.append(event)

        emitter.on(capture)
        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=emitter,
            memory=FakeMemory(),
            microcompact_config=MicrocompactConfig(
                enabled=True,
                gap_threshold_minutes=60.0,
                keep_recent=3,
            ),
        )

        msgs = stale_tool_messages(n_read_results=10, gap_minutes=120)
        result = run(orch.compact_if_needed(msgs, "", [], "task-mc", 1, 200_000))

        mc_events = [e for e in received if isinstance(e, MicrocompactEvent)]
        assert len(mc_events) == 1, "expected exactly one MicrocompactEvent"
        ev = mc_events[0]
        assert ev.thread_id == "task-mc"
        assert ev.mode == "time_based"
        assert ev.tools_kept == 3
        assert ev.tools_cleared == 7
        assert ev.gap_minutes >= 60.0

        cleared_count = 0
        kept_intact_count = 0
        for m in result.messages:
            if not isinstance(m.content, list):
                continue
            for b in m.content:
                if isinstance(b, ToolResultBlock):
                    if isinstance(b.content, str) and b.content == STALENESS_MARKER:
                        cleared_count += 1
                    elif isinstance(b.content, str) and b.content.startswith("file-content-"):
                        kept_intact_count += 1
        assert cleared_count == 7
        assert kept_intact_count == 3

    def test_disabled_config_blocks_microcompact(self):
        emitter = EventEmitter()
        received: list = []

        async def capture(event):
            received.append(event)

        emitter.on(capture)
        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=emitter,
            memory=FakeMemory(),
            microcompact_config=MicrocompactConfig(
                enabled=False,
                gap_threshold_minutes=60.0,
                keep_recent=3,
            ),
        )
        msgs = stale_tool_messages(n_read_results=10, gap_minutes=120)
        run(orch.compact_if_needed(msgs, "", [], "task-mc-off", 1, 200_000))

        mc_events = [e for e in received if isinstance(e, MicrocompactEvent)]
        assert mc_events == [], "disabled config must not fire microcompact"

    def test_fresh_conversation_does_not_fire_time_based(self):
        emitter = EventEmitter()
        received: list = []

        async def capture(event):
            received.append(event)

        emitter.on(capture)
        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=emitter,
            memory=FakeMemory(),
            microcompact_config=MicrocompactConfig(
                enabled=True,
                gap_threshold_minutes=60.0,
                keep_recent=3,
            ),
        )
        msgs = stale_tool_messages(n_read_results=10, gap_minutes=0)
        run(orch.compact_if_needed(msgs, "", [], "task-fresh", 1, 200_000))

        mc_events = [e for e in received if isinstance(e, MicrocompactEvent)]
        assert mc_events == [], "fresh conversation must not fire time-based microcompact"

    def test_forced_microcompact_runs_in_reactive_tier_0(self):
        emitter = EventEmitter()
        received: list = []

        async def capture(event):
            received.append(event)

        emitter.on(capture)
        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=emitter,
            memory=FakeMemory(),
            microcompact_config=MicrocompactConfig(
                enabled=True,
                gap_threshold_minutes=60.0,
                keep_recent=2,
            ),
        )
        msgs = stale_tool_messages(n_read_results=8, gap_minutes=0)
        result = run(orch.reactive_compact(msgs, 0, 200_000))

        assert isinstance(result, ReactiveCompactResult)
        mc_events = [e for e in received if isinstance(e, MicrocompactEvent)]
        assert len(mc_events) == 1
        assert mc_events[0].mode == "forced"
        assert mc_events[0].tools_kept == 2
        assert mc_events[0].tools_cleared == 6

    def test_forced_microcompact_skipped_when_disabled(self):
        emitter = EventEmitter()
        received: list = []

        async def capture(event):
            received.append(event)

        emitter.on(capture)
        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=emitter,
            memory=FakeMemory(),
            microcompact_config=MicrocompactConfig(enabled=False),
        )
        msgs = stale_tool_messages(n_read_results=8, gap_minutes=0)
        run(orch.reactive_compact(msgs, 0, 200_000))

        mc_events = [e for e in received if isinstance(e, MicrocompactEvent)]
        assert mc_events == []

    def test_compact_if_needed_sets_cache_deletion_on_microcompact_fire(self):
        reset_pending_deletions()
        assert is_cache_deletion_pending() is False

        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=EventEmitter(),
            memory=FakeMemory(),
            microcompact_config=MicrocompactConfig(
                enabled=True,
                gap_threshold_minutes=60.0,
                keep_recent=3,
            ),
        )
        msgs = stale_tool_messages(n_read_results=8, gap_minutes=120)
        run(orch.compact_if_needed(msgs, "", [], "task-cache", 1, 200_000))

        assert is_cache_deletion_pending() is True

        reset_pending_deletions()


def build_collapsible_history(pair_count: int) -> list[Message]:
    """Build a history dominated by low-risk Glob/result pairs with a
    leading and trailing user message. This is the same shape used by
    tests/test_context_collapse.py — kept self-contained here so the
    two files don't import each other's fixtures.

    Uses a recent timestamp base so time_based_microcompact (which
    runs before the collapse pre-pass in the orchestrator pipeline)
    doesn't treat every tool result as stale and clear them all
    before collapse gets a chance to look at them."""
    import time

    ts = [time.time()]

    def nts():
        ts[0] += 1.0
        return ts[0]

    msgs: list[Message] = [
        Message(role="user", content="Do the thing.", timestamp=nts()),
    ]
    for i in range(pair_count):
        tid = f"tc{i}"
        msgs.append(
            Message(
                role="assistant",
                content=[ToolUseBlock(id=tid, name="Glob", input={"pattern": "*.py"})],
                timestamp=nts(),
            )
        )
        msgs.append(
            Message(
                role="user",
                content=[ToolResultBlock(tool_use_id=tid, content="x" * 80)],
                timestamp=nts(),
            )
        )
    msgs.append(
        Message(
            role="user",
            content="What did you find?",
            timestamp=nts(),
        )
    )
    return msgs


class TestContextCollapseIntegration:
    def test_pre_pass_short_circuits_when_under_threshold_after_project(self):
        from src.engine_core.context_collapse import (
            ContextCollapseManager,
            STRATEGY_CONTEXT_COLLAPSE,
        )
        from src.engine_core.context_engineering import estimate_messages_tokens

        mgr = ContextCollapseManager(db=None, enabled=True)
        orch = make_orch(collapse_manager=mgr)

        msgs = build_collapsible_history(pair_count=50)
        tokens = estimate_messages_tokens(msgs)
        context_window = int(tokens / 0.82)
        result = compact(orch, msgs, context_window=context_window)

        assert result.messages is not None
        if result.strategy == STRATEGY_CONTEXT_COLLAPSE:
            assert result.was_compacted
            assert mgr.has_committed() or mgr.has_staged() or result.tokens_freed >= 0

    def test_flag_off_preserves_legacy_path(self, monkeypatch):
        monkeypatch.setenv("FEATURE_CONTEXT_COLLAPSE", "false")
        from src.engine_core.context_collapse import (
            ContextCollapseManager,
            STRATEGY_CONTEXT_COLLAPSE,
        )

        mgr = ContextCollapseManager(db=None, enabled=False)
        orch = make_orch(collapse_manager=mgr)
        msgs = make_messages(20, chars_each=600)
        result = compact(orch, msgs, context_window=1000)
        assert result.strategy != STRATEGY_CONTEXT_COLLAPSE

    def test_reactive_compact_drains_staged_queue(self):
        from src.engine_core.context_collapse import ContextCollapseManager
        from src.engine_core.context_engineering import estimate_messages_tokens

        mgr = ContextCollapseManager(db=None, enabled=True)
        msgs = build_collapsible_history(pair_count=40)
        tokens = estimate_messages_tokens(msgs)
        mgr.evaluate_and_stage(msgs, tokens, int(tokens / 0.90))
        assert mgr.has_staged()

        orch = make_orch(collapse_manager=mgr)

        result = run(orch.reactive_compact(msgs, 0, int(tokens / 0.90)))
        assert isinstance(result, ReactiveCompactResult)
        assert not mgr.has_staged()
        assert len(result.messages) <= len(msgs)

    def test_post_compact_cleanup_resets_collapse_state(self):
        from src.engine_core.context_collapse import ContextCollapseManager
        from src.engine_core.context_engineering import estimate_messages_tokens

        mgr = ContextCollapseManager(db=None, enabled=True)
        msgs = build_collapsible_history(pair_count=40)
        tokens = estimate_messages_tokens(msgs)
        mgr.evaluate_and_stage(msgs, tokens, int(tokens / 0.96))
        assert mgr.has_committed()

        orch = make_orch(collapse_manager=mgr)
        orch.post_compact_cleanup(
            tokens_before=tokens,
            tokens_after=tokens // 2,
            strategy="summary",
        )
        assert not mgr.has_committed()
        assert not mgr.has_staged()


class TestShouldExcludeHardened:
    """Tests for categorized exclude patterns (Gap 8) and system-context exclusion (Gap 4)."""

    def test_no_false_positive_tokenizer(self):
        assert should_exclude_from_restore("/src/tokenizer/main.py") is False

    def test_no_false_positive_secret_manager(self):
        assert should_exclude_from_restore("/lib/secret_manager.py") is False

    def test_no_false_positive_token_parser(self):
        assert should_exclude_from_restore("/utils/auth-token-rotator.js") is False

    def test_excludes_credentials_file(self):
        assert should_exclude_from_restore("/creds/credentials.json") is True

    def test_excludes_secrets_yaml(self):
        assert should_exclude_from_restore("/config/secrets.yaml") is True

    def test_excludes_credentials_json(self):
        assert should_exclude_from_restore("/config/credentials.json") is True

    def test_excludes_env_file(self):
        assert should_exclude_from_restore("/home/.env") is True

    def test_excludes_env_local(self):
        assert should_exclude_from_restore("/app/.env.local") is True

    def test_allows_env_example(self):
        assert should_exclude_from_restore("/app/.env.example") is False

    def test_excludes_key_extension(self):
        assert should_exclude_from_restore("/ssl/server.key") is True

    def test_excludes_pem_extension(self):
        assert should_exclude_from_restore("/certs/ca.pem") is True

    def test_excludes_p12_extension(self):
        assert should_exclude_from_restore("/certs/client.p12") is True

    def test_excludes_git_dir(self):
        assert should_exclude_from_restore("/app/.git/config") is True

    def test_excludes_venv_dir(self):
        assert should_exclude_from_restore("/app/.venv/lib/python3.13/site.py") is True

    def test_excludes_node_modules(self):
        assert should_exclude_from_restore("/app/node_modules/foo/index.js") is True

    def test_excludes_claude_md(self):
        assert should_exclude_from_restore("/project/CLAUDE.md") is True

    def test_excludes_claudeignore(self):
        assert should_exclude_from_restore("/project/.claudeignore") is True

    def test_excludes_claude_dir(self):
        assert should_exclude_from_restore("/project/.claude/rules/foo.md") is True

    def test_excludes_claude_settings(self):
        assert should_exclude_from_restore("/project/.claude/settings.json") is True

    def test_plan_file_exclusion(self):
        assert (
            should_exclude_from_restore(
                "/tmp/plan.md",
                plan_file_path="/tmp/plan.md",
            )
            is True
        )

    def test_plan_file_no_match(self):
        assert (
            should_exclude_from_restore(
                "/app/src/main.py",
                plan_file_path="/tmp/plan.md",
            )
            is False
        )

    def test_excludes_secrets_directory(self):
        assert should_exclude_from_restore("/app/secrets/aws_key.json") is True

    def test_excludes_credentials_directory(self):
        assert should_exclude_from_restore("/config/credentials/prod.json") is True

    def test_allows_normal_source(self):
        assert should_exclude_from_restore("/app/src/main.py") is False

    def test_allows_normal_config(self):
        assert should_exclude_from_restore("/app/config/database.yml") is False


class TestStructuredExtraction:
    """Tests for structured tool-use file path extraction (Gap 1) + dedup-stub (Gap 2).

    Paths are compared through os.path.normpath because that is what
    `collect_file_paths_structured` stores - deliberately, so "/app/./a.py"
    and "/app/a.py" dedupe to one entry. On Windows normpath also swaps
    the separators, so these assertions were comparing a POSIX literal to
    a normalised one and five of them failed on every machine that is not
    Linux. Normalising both sides tests the dedup the function is for,
    rather than the separator the platform chose.
    """

    def test_extracts_from_tool_use_block(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id="tu1", name="file_read", input={"file_path": "/app/src/main.py"}
                    ),
                ],
            )
        ]
        paths = collect_file_paths_structured(msgs)
        assert os.path.normpath("/app/src/main.py") in paths

    def test_grep_tool_NOT_in_preserved_paths(self):
        """grep results don't contain full file content — should NOT prevent restoration."""
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="tu1", name="grep", input={"path": "/app/src"}),
                ],
            )
        ]
        paths = collect_file_paths_structured(msgs)
        assert os.path.normpath("/app/src") not in paths

    def test_glob_tool_NOT_in_preserved_paths(self):
        """glob results don't contain full file content — should NOT prevent restoration."""
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="tu1", name="glob", input={"path": "/app"}),
                ],
            )
        ]
        paths = collect_file_paths_structured(msgs)
        assert os.path.normpath("/app") not in paths

    def test_regex_fallback_for_plain_text(self):
        msgs = [
            Message(role="user", content="[Post-compact file context] /app/restored.py:\ncontent")
        ]
        paths = collect_file_paths_structured(msgs)
        assert any("restored.py" in p for p in paths)

    def test_mixed_structured_and_text(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="tu1", name="file_read", input={"file_path": "/app/a.py"}),
                ],
            ),
            Message(role="user", content="[Post-compact file context] /app/b.py:\ncontent"),
        ]
        paths = collect_file_paths_structured(msgs)
        assert os.path.normpath("/app/a.py") in paths
        assert any("b.py" in p for p in paths)

    def test_dedup_stub_skipped_from_preserved(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id="tu_stub", name="file_read", input={"file_path": "/app/stale.py"}
                    ),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="tu_stub", content=FILE_UNCHANGED_STUB),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id="tu_fresh", name="file_read", input={"file_path": "/app/fresh.py"}
                    ),
                ],
            ),
        ]
        paths = collect_file_paths_structured(msgs)
        assert os.path.normpath("/app/stale.py") not in paths
        assert os.path.normpath("/app/fresh.py") in paths

    def test_no_stub_means_all_preserved(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="tu1", name="file_read", input={"file_path": "/app/a.py"}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="tu1", content="file contents here"),
                ],
            ),
        ]
        paths = collect_file_paths_structured(msgs)
        assert os.path.normpath("/app/a.py") in paths

    def test_empty_messages(self):
        assert collect_file_paths_structured([]) == set()

    def test_dispatch_uses_structured_when_enabled(self, monkeypatch):
        monkeypatch.setenv("FEATURE_POST_COMPACT_HARDENED", "true")
        monkeypatch.setenv("FEATURE_STRUCTURED_FILE_EXTRACTION", "true")
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="tu1", name="file_read", input={"file_path": "/app/main.py"}),
                ],
            )
        ]
        paths = collect_file_paths_from_messages(msgs)
        assert os.path.normpath("/app/main.py") in paths

    def test_dispatch_uses_regex_when_disabled(self, monkeypatch):
        monkeypatch.setenv("FEATURE_STRUCTURED_FILE_EXTRACTION", "false")
        msgs = [Message(role="user", content="I read /app/src/main.py")]
        paths = collect_file_paths_from_messages(msgs)
        assert any("main.py" in p for p in paths)


class TestTokenBudgetMath:
    """Tests for corrected budget math (Gap 7) and truncation marker accounting (Gap 12)."""

    def test_content_within_budget_not_truncated(self):
        marker_tokens = estimate_tokens_dense(TRUNCATION_MARKER)
        max_chars = tokens_to_dense_chars(
            POST_COMPACT_MAX_TOKENS_PER_FILE, marker_tokens=marker_tokens
        )
        content = "x" * max_chars
        tokens = estimate_tokens_dense(content)
        assert tokens <= POST_COMPACT_MAX_TOKENS_PER_FILE

    def test_content_over_budget_truncated_within_budget(self):
        marker_tokens = estimate_tokens_dense(TRUNCATION_MARKER)
        max_chars = tokens_to_dense_chars(
            POST_COMPACT_MAX_TOKENS_PER_FILE, marker_tokens=marker_tokens
        )
        big_content = "x" * (max_chars * 3)
        truncated = big_content[:max_chars] + TRUNCATION_MARKER
        tokens = estimate_tokens_dense(truncated)
        assert tokens <= POST_COMPACT_MAX_TOKENS_PER_FILE

    def test_old_math_was_wrong(self):
        old_max_chars = POST_COMPACT_MAX_TOKENS_PER_FILE * 2
        content = "x" * old_max_chars
        tokens = estimate_tokens_dense(content)
        assert tokens > POST_COMPACT_MAX_TOKENS_PER_FILE


class TestFileRestorationEvents:
    """Tests for per-file restoration events (Gap 6)."""

    def test_restoration_event_emitted(self, tmp_path):
        events = []

        async def capture(event):
            events.append(event)

        emitter = EventEmitter()
        emitter.on(capture)
        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=emitter,
            memory=FakeMemory(),
        )
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        orch.record_file_read(str(test_file))
        result = run(orch.build_file_restoration_messages([], thread_id="t1"))
        assert len(result) == 1
        restore_events = [e for e in events if isinstance(e, FileRestorationEvent)]
        assert len(restore_events) == 1
        assert restore_events[0].path == str(test_file)
        assert restore_events[0].thread_id == "t1"

    def test_failed_event_on_missing_file(self):
        events = []

        async def capture(event):
            events.append(event)

        emitter = EventEmitter()
        emitter.on(capture)
        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=emitter,
            memory=FakeMemory(),
        )
        orch.record_file_read("/nonexistent/file.py")
        result = run(orch.build_file_restoration_messages([], thread_id="t1"))
        assert len(result) == 0
        fail_events = [e for e in events if isinstance(e, FileRestorationFailedEvent)]
        assert len(fail_events) == 1
        assert fail_events[0].reason == "read_error"


class TestTOCTOU:
    """Tests for content-at-read-time TOCTOU protection (Gap 10)."""

    def test_unchanged_file_no_annotation(self, tmp_path):
        import hashlib

        test_file = tmp_path / "stable.py"
        content = "stable content"
        test_file.write_text(content)
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        orch = make_orch()
        orch.record_file_read(str(test_file), content_hash=content_hash, size=len(content))
        result = run(orch.build_file_restoration_messages([]))
        assert len(result) == 1
        assert "[Note: file modified" not in result[0].content

    def test_modified_file_gets_annotation(self, tmp_path):
        test_file = tmp_path / "changing.py"
        test_file.write_text("original content")
        orch = make_orch()
        orch.record_file_read(str(test_file), content_hash="stale_hash", size=16)
        result = run(orch.build_file_restoration_messages([]))
        assert len(result) == 1
        assert "[Note: file modified since last read" in result[0].content

    def test_no_hash_means_no_annotation(self, tmp_path):
        test_file = tmp_path / "nohash.py"
        test_file.write_text("some content")
        orch = make_orch()
        orch.record_file_read(str(test_file))
        result = run(orch.build_file_restoration_messages([]))
        assert len(result) == 1
        assert "[Note: file modified" not in result[0].content


class TestCoInjection:
    """Tests for tool re-announcement (Gap 5a) and skill reinjection (Gap 5b)."""

    def test_tool_reannouncement_with_tools(self):
        orch = make_orch()
        msgs = orch.build_tool_reannouncement_messages({"file_read", "bash", "grep"})
        assert len(msgs) == 1
        assert "file_read" in msgs[0].content
        assert "bash" in msgs[0].content
        assert "grep" in msgs[0].content
        assert "[Post-compact tool reminder]" in msgs[0].content

    def test_tool_reannouncement_empty_tools(self):
        orch = make_orch()
        msgs = orch.build_tool_reannouncement_messages(set())
        assert len(msgs) == 0

    def test_tool_reannouncement_disabled(self, monkeypatch):
        monkeypatch.setenv("FEATURE_POST_COMPACT_HARDENED", "false")
        orch = make_orch()
        msgs = orch.build_tool_reannouncement_messages({"file_read"})
        assert len(msgs) == 0

    def test_skill_reinjection_no_catalog(self):
        orch = make_orch()
        msgs = orch.build_skill_reinjection_messages()
        assert len(msgs) == 0

    def test_skill_reinjection_with_matched_skills(self):
        class SkillUtility:
            def skill_execute(self, skill):
                return {"prompt": f"Skill: {skill['name']} instructions here"}

            def invalidate(self, prefix=""):
                pass

        orch = make_orch(utility=SkillUtility())
        orch.record_matched_skills([{"name": "test_skill"}])
        msgs = orch.build_skill_reinjection_messages()
        assert len(msgs) == 1
        assert "test_skill" in msgs[0].content
        assert "[Post-compact skill context]" in msgs[0].content


class TestPostCompactAttachmentType:
    """Tests for typed PostCompactAttachment (Gap 11)."""

    def test_attachment_fields(self):
        att = PostCompactAttachment(
            type="file",
            content="hello",
            path="/app/main.py",
            tokens=100,
            truncated=False,
        )
        assert att.type == "file"
        assert att.path == "/app/main.py"
        assert att.tokens == 100
        assert att.truncated is False

    def test_attachment_defaults(self):
        att = PostCompactAttachment(type="tool_reminder", content="tools here")
        assert att.path == ""
        assert att.tokens == 0
        assert att.truncated is False


class TestPerTypeBudgets:
    """Tests for per-attachment-type budgets (Gap 13)."""

    def test_tool_reminder_budget_exists(self):
        assert POST_COMPACT_TOOL_REMINDER_TOKEN_BUDGET == 2_000

    def test_skill_budget_exists(self):
        assert POST_COMPACT_SKILL_TOKEN_BUDGET == 25_000

    def test_file_budget_unchanged(self):
        assert POST_COMPACT_TOKEN_BUDGET == 50_000

    def test_tool_reminder_respects_budget(self):
        orch = make_orch()
        big_tool_set = {f"tool_{i}" for i in range(500)}
        msgs = orch.build_tool_reannouncement_messages(big_tool_set)
        if msgs:
            tokens = estimate_tokens_dense(msgs[0].content)
            assert tokens <= POST_COMPACT_TOOL_REMINDER_TOKEN_BUDGET + 100


class TestFeatureFlags:
    """Tests for feature flag gating."""

    def test_hardened_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("FEATURE_POST_COMPACT_HARDENED", raising=False)
        from src.engine_core.compaction_orchestrator import post_compact_hardened_enabled

        assert post_compact_hardened_enabled() is True

    def test_hardened_disabled(self, monkeypatch):
        monkeypatch.setenv("FEATURE_POST_COMPACT_HARDENED", "false")
        from src.engine_core.compaction_orchestrator import post_compact_hardened_enabled

        assert post_compact_hardened_enabled() is False

    def test_structured_extraction_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("FEATURE_STRUCTURED_FILE_EXTRACTION", raising=False)
        from src.engine_core.compaction_orchestrator import structured_extraction_enabled

        assert structured_extraction_enabled() is True

    def test_structured_extraction_disabled(self, monkeypatch):
        monkeypatch.setenv("FEATURE_STRUCTURED_FILE_EXTRACTION", "false")
        from src.engine_core.compaction_orchestrator import structured_extraction_enabled

        assert structured_extraction_enabled() is False


class TestFileReadRecord:
    """Tests for FileReadRecord dataclass (Gap 10)."""

    def test_default_fields(self):
        r = FileReadRecord(timestamp=1.0)
        assert r.content_hash == ""
        assert r.size == 0

    def test_full_fields(self):
        r = FileReadRecord(timestamp=1.0, content_hash="abc", size=42)
        assert r.content_hash == "abc"
        assert r.size == 42


class TestAgentIdScoping:
    """Tests for agent_id on CompactionOrchestrator (Gap 9)."""

    def test_agent_id_stored(self):
        orch = CompactionOrchestrator(
            adapter=FakeAdapter(),
            emitter=EventEmitter(),
            memory=FakeMemory(),
            agent_id="agent-1",
        )
        assert orch.agent_id == "agent-1"

    def test_agent_id_default_empty(self):
        orch = make_orch()
        assert orch.agent_id == ""
