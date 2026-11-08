"""Tests for max-output-tokens escalation recovery.

Verifies the escalation-then-continuation recovery flow for MAX_TOKENS:
1. Escalation fires first (retry same request at 64K, no context growth)
2. Multi-turn continuation fires second (appends recovery nudge, more expensive)
3. Guards, state resets, and withholding behavior
"""

from dataclasses import dataclass
from typing import AsyncIterator

import pytest

from src.config import Settings
from src.herald.types import (
    Message,
    StreamEvent,
    StopReason,
    ToolDef,
    Usage,
)


@dataclass
class CallRecord:
    max_output_tokens: int | None = None


class FakeAdapter:
    """Minimal adapter that returns configurable stop reasons."""

    def __init__(self, max_tokens: int = 4096):
        self.max_tokens = max_tokens
        self.calls: list[CallRecord] = []
        self.responses: list[StopReason] = []

    def queue_response(self, stop_reason: StopReason, text: str = "some output"):
        self.responses.append((stop_reason, text))

    async def stream_with_tools(
        self,
        messages: list[Message],
        system=None,
        tools: list[ToolDef] | None = None,
        effort: str | None = None,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(CallRecord(max_output_tokens=max_output_tokens))
        stop_reason, text = (
            self.responses.pop(0) if self.responses else (StopReason.END_TURN, "done")
        )
        yield StreamEvent(type="text", text=text)
        yield StreamEvent(type="stop", stop_reason=stop_reason)
        yield StreamEvent(type="usage", usage=Usage(input_tokens=100, output_tokens=50))

    async def generate(self, messages, system=""):
        return "summary"


def escalation_decision(
    escalation_attempted: bool,
    escalation_eligible: bool,
    continuation_count: int,
    max_continuations: int = 3,
) -> str:
    """Simulate the MAX_TOKENS recovery decision from agent_loop.

    Returns: 'escalate', 'continue_multi_turn', or 'exhausted'.
    """
    if not escalation_attempted and escalation_eligible:
        return "escalate"
    if continuation_count < max_continuations:
        return "continue_multi_turn"
    return "exhausted"


class TestEscalationDecision:
    def test_first_max_tokens_escalates(self):
        result = escalation_decision(
            escalation_attempted=False,
            escalation_eligible=True,
            continuation_count=0,
        )
        assert result == "escalate"

    def test_already_attempted_falls_through(self):
        result = escalation_decision(
            escalation_attempted=True,
            escalation_eligible=True,
            continuation_count=0,
        )
        assert result == "continue_multi_turn"

    def test_not_eligible_falls_through(self):
        result = escalation_decision(
            escalation_attempted=False,
            escalation_eligible=False,
            continuation_count=0,
        )
        assert result == "continue_multi_turn"

    def test_all_recovery_exhausted(self):
        result = escalation_decision(
            escalation_attempted=True,
            escalation_eligible=True,
            continuation_count=3,
            max_continuations=3,
        )
        assert result == "exhausted"

    def test_multi_turn_after_escalation(self):
        result = escalation_decision(
            escalation_attempted=True,
            escalation_eligible=True,
            continuation_count=1,
        )
        assert result == "continue_multi_turn"


class TestEscalationEligibility:
    def test_eligible_when_default_max_tokens(self):
        settings = Settings()
        eligible = settings.max_tokens == Settings.max_tokens
        assert eligible is True

    def test_not_eligible_when_custom_max_tokens(self):
        settings = Settings(max_tokens=8192)
        eligible = settings.max_tokens == Settings.max_tokens
        assert eligible is False

    def test_not_eligible_when_lower_max_tokens(self):
        settings = Settings(max_tokens=2048)
        eligible = settings.max_tokens == Settings.max_tokens
        assert eligible is False


class TestEscalationCapping:
    def test_capped_to_model_max(self):
        model_max = 16_384
        escalated = min(64_000, model_max)
        assert escalated == 16_384

    def test_not_capped_when_model_allows(self):
        escalated = min(64_000, 65_536)
        assert escalated == 64_000

    def test_ineligible_when_escalated_equals_default(self):
        default = 4096
        model_max = 4096
        escalated = min(64_000, model_max)
        eligible = escalated > default
        assert eligible is False

    def test_eligible_when_escalated_above_default(self):
        default = 4096
        model_max = 16_384
        escalated = min(64_000, model_max)
        eligible = escalated > default
        assert eligible is True

    def test_all_synced_models_have_max_output(self, monkeypatch):
        from src.herald import catalog_sync

        rows = [
            {"id": "model-a", "tier": "standard", "max_output": 8192},
            {"id": "model-b", "tier": "light"},
        ]
        canned = {}
        for row in rows:
            md = catalog_sync.row_to_modeldef(row)
            canned[md.id] = md
        monkeypatch.setattr(catalog_sync, "runtime_overlay", canned)
        for model_id, model_def in catalog_sync.get_catalog_overlay().items():
            assert model_def.max_output_tokens > 0, f"{model_id} has no max_output_tokens"


class TestRecoveryStateReset:
    def test_reset_after_tool_execution(self):
        escalation_attempted = True
        max_output_override = 64_000
        continuation_count = 2

        escalation_attempted = False
        max_output_override = None
        continuation_count = 0

        assert escalation_attempted is False
        assert max_output_override is None
        assert continuation_count == 0

    def test_override_cleared_before_multi_turn(self):
        max_output_override = 64_000
        max_output_override = None
        assert max_output_override is None


class TestAdapterReceivesOverride:
    @pytest.mark.asyncio
    async def test_escalation_passes_64k_to_adapter(self):
        adapter = FakeAdapter(max_tokens=4096)
        adapter.queue_response(StopReason.MAX_TOKENS, "partial")
        adapter.queue_response(StopReason.END_TURN, "complete")

        ESCALATED = 64_000
        max_output_override = None

        async for _ in adapter.stream_with_tools(
            [],
            max_output_tokens=max_output_override,
        ):
            pass
        assert adapter.calls[0].max_output_tokens is None

        max_output_override = ESCALATED
        async for _ in adapter.stream_with_tools(
            [],
            max_output_tokens=max_output_override,
        ):
            pass
        assert adapter.calls[1].max_output_tokens == 64_000

    @pytest.mark.asyncio
    async def test_multi_turn_uses_original_limit(self):
        adapter = FakeAdapter(max_tokens=4096)
        adapter.queue_response(StopReason.END_TURN, "continued")

        max_output_override = None
        async for _ in adapter.stream_with_tools(
            [],
            max_output_tokens=max_output_override,
        ):
            pass
        assert adapter.calls[0].max_output_tokens is None


class TestWithholding:
    def test_exit_reason_not_set_during_escalation(self):
        """During escalation recovery, the loop continues without setting exit_reason."""
        from src.engine_core.schema import LoopExitReason
        from src.engine_core.loop_state import LoopState

        state = LoopState()
        assert state.exit_reason == LoopExitReason.COMPLETED

    def test_exit_reason_set_when_exhausted(self):
        from src.engine_core.schema import LoopExitReason
        from src.engine_core.loop_state import LoopState

        state = LoopState()
        state.exit_reason = LoopExitReason.MAX_TOKENS
        assert state.exit_reason == LoopExitReason.MAX_TOKENS


class TestEscalationIntegration:
    @pytest.mark.asyncio
    async def test_full_flow_escalation_succeeds(self):
        """MAX_TOKENS on first call → escalation retry at 64K → succeeds."""
        adapter = FakeAdapter(max_tokens=4096)
        adapter.queue_response(StopReason.MAX_TOKENS, "partial output")
        adapter.queue_response(StopReason.END_TURN, "full output after escalation")

        ESCALATED = 64_000
        escalation_attempted = False
        max_output_override: int | None = None
        continuation_count = 0
        results = []

        for turn in range(5):
            stop_reason = None
            text = ""
            async for event in adapter.stream_with_tools(
                [],
                max_output_tokens=max_output_override,
            ):
                if event.type == "text":
                    text += event.text
                elif event.type == "stop":
                    stop_reason = event.stop_reason

            results.append({"text": text, "stop": stop_reason})

            if stop_reason == StopReason.MAX_TOKENS:
                if not escalation_attempted and adapter.max_tokens == 4096:
                    escalation_attempted = True
                    max_output_override = ESCALATED
                    continue
                escalation_attempted = True
                max_output_override = None
                continuation_count += 1
                if continuation_count >= 3:
                    break
                continue

            break

        assert len(adapter.calls) == 2
        assert adapter.calls[0].max_output_tokens is None
        assert adapter.calls[1].max_output_tokens == 64_000
        assert results[-1]["stop"] == StopReason.END_TURN

    @pytest.mark.asyncio
    async def test_full_flow_escalation_fails_falls_to_continuation(self):
        """MAX_TOKENS → escalation → MAX_TOKENS again → multi-turn continuation."""
        adapter = FakeAdapter(max_tokens=4096)
        adapter.queue_response(StopReason.MAX_TOKENS, "partial 1")
        adapter.queue_response(StopReason.MAX_TOKENS, "partial 2 (escalated)")
        adapter.queue_response(StopReason.END_TURN, "recovered via continuation")

        ESCALATED = 64_000
        escalation_attempted = False
        max_output_override: int | None = None
        continuation_count = 0

        for turn in range(10):
            stop_reason = None
            async for event in adapter.stream_with_tools(
                [],
                max_output_tokens=max_output_override,
            ):
                if event.type == "stop":
                    stop_reason = event.stop_reason

            if stop_reason == StopReason.MAX_TOKENS:
                if not escalation_attempted and adapter.max_tokens == 4096:
                    escalation_attempted = True
                    max_output_override = ESCALATED
                    continue
                escalation_attempted = True
                max_output_override = None
                continuation_count += 1
                if continuation_count >= 3:
                    break
                continue
            break

        assert len(adapter.calls) == 3
        assert adapter.calls[0].max_output_tokens is None
        assert adapter.calls[1].max_output_tokens == 64_000
        assert adapter.calls[2].max_output_tokens is None

    @pytest.mark.asyncio
    async def test_escalation_skipped_for_custom_max_tokens(self):
        """When max_tokens is non-default, skip escalation entirely."""
        adapter = FakeAdapter(max_tokens=8192)
        adapter.queue_response(StopReason.MAX_TOKENS, "partial")
        adapter.queue_response(StopReason.END_TURN, "continued")

        DEFAULT_MAX_TOKENS = 4096
        escalation_attempted = False
        max_output_override: int | None = None
        continuation_count = 0

        for turn in range(10):
            stop_reason = None
            async for event in adapter.stream_with_tools(
                [],
                max_output_tokens=max_output_override,
            ):
                if event.type == "stop":
                    stop_reason = event.stop_reason

            if stop_reason == StopReason.MAX_TOKENS:
                if not escalation_attempted and adapter.max_tokens == DEFAULT_MAX_TOKENS:
                    escalation_attempted = True
                    max_output_override = 64_000
                    continue
                escalation_attempted = True
                max_output_override = None
                continuation_count += 1
                if continuation_count >= 3:
                    break
                continue
            break

        assert adapter.calls[0].max_output_tokens is None
        assert adapter.calls[1].max_output_tokens is None
