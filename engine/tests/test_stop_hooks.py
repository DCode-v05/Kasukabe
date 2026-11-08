"""Tests for src/engine_core/stop_hooks.py and its integration
into the agent loop in src/engine_core/thread.py.

Unit tests exercise the registry and individual hook implementations
in isolation — pure asyncio, no LLM. Integration tests wire a real
LLM adapter through bootstrap following the pattern in
tests/test_live_execution.py; they are gated on LLM_API_KEY so the
unit suite still runs without credentials.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from src.config import Settings, parse_bool
from src.engine_core.schema import LoopExitReason
from src.engine_core.thread import format_hook_prevent_notice
from src.engine_core.stop_hooks import (
    AggregatedStopResult,
    CallableStopHook,
    CommandStopHook,
    PromptStopHook,
    StopHookInput,
    StopHookRegistry,
    StopHookResult,
)


def make_input(**overrides) -> StopHookInput:
    defaults = dict(
        stop_hook_active=False,
        last_assistant_message="done",
        turn_number=1,
        last_stop_reason="end_turn",
        thread_id="t-test",
    )
    defaults.update(overrides)
    return StopHookInput(**defaults)


class TestFormatHookPreventNotice:
    def test_no_reasons_renders_generic_notice(self):
        agg = AggregatedStopResult()
        assert format_hook_prevent_notice(agg) == "\n\n[Stopped by hook]"

    def test_single_reason_rendered(self):
        agg = AggregatedStopResult(stop_reasons=["safety-check failed"])
        assert format_hook_prevent_notice(agg) == "\n\n[Stopped by hook: safety-check failed]"

    def test_multiple_reasons_joined(self):
        agg = AggregatedStopResult(stop_reasons=["budget-guard", "policy-deny"])
        assert format_hook_prevent_notice(agg) == "\n\n[Stopped by hook: budget-guard; policy-deny]"

    def test_empty_strings_filtered(self):
        agg = AggregatedStopResult(stop_reasons=["", "real-reason", ""])
        assert "real-reason" in format_hook_prevent_notice(agg)
        assert "; ;" not in format_hook_prevent_notice(agg)


class TestParseBoolFlag:
    def test_true_lowercase(self):
        assert parse_bool("F", "true") is True

    def test_false_lowercase(self):
        assert parse_bool("F", "false") is False

    def test_case_insensitive(self):
        assert parse_bool("F", "TRUE") is True
        assert parse_bool("F", "False") is False

    def test_whitespace_tolerated(self):
        assert parse_bool("F", "  true  ") is True

    def test_numeric_one_rejected(self):
        with pytest.raises(ValueError, match="must be 'true' or 'false'"):
            parse_bool("F", "1")

    def test_numeric_zero_rejected(self):
        with pytest.raises(ValueError, match="must be 'true' or 'false'"):
            parse_bool("F", "0")

    def test_yes_rejected(self):
        with pytest.raises(ValueError, match="must be 'true' or 'false'"):
            parse_bool("F", "yes")

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="must be 'true' or 'false'"):
            parse_bool("F", "")

    def test_error_includes_flag_name(self):
        with pytest.raises(ValueError, match="MY_FLAG"):
            parse_bool("MY_FLAG", "maybe")


class TestStopHookRegistry:
    @pytest.mark.asyncio
    async def test_empty_registry_zero_overhead(self):
        reg = StopHookRegistry()
        assert reg.has_hooks() is False
        result = await reg.execute_all(make_input())
        assert isinstance(result, AggregatedStopResult)
        assert result.prevent_continuation is False
        assert result.blocking_errors == []
        assert result.messages == []
        assert result.hook_outcomes == []

    @pytest.mark.asyncio
    async def test_parallel_execution_timing(self):
        async def slow_ok(_):
            await asyncio.sleep(0.3)
            return StopHookResult()

        reg = StopHookRegistry()
        reg.register("a", CallableStopHook(slow_ok))
        reg.register("b", CallableStopHook(slow_ok))

        t0 = time.monotonic()
        await reg.execute_all(make_input())
        elapsed = time.monotonic() - t0

        assert elapsed < 0.5, f"hooks did not run in parallel: {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_first_prevent_wins(self):
        async def fast_prevent(_):
            return StopHookResult(prevent_continuation=True, stop_reason="safety")

        async def slow_ok(_):
            await asyncio.sleep(0.2)
            return StopHookResult()

        reg = StopHookRegistry()
        reg.register("fast", CallableStopHook(fast_prevent))
        reg.register("slow", CallableStopHook(slow_ok))

        result = await reg.execute_all(make_input())
        assert result.prevent_continuation is True
        assert "safety" in result.stop_reasons
        assert len(result.hook_outcomes) == 2
        names = {o["name"] for o in result.hook_outcomes}
        assert names == {"fast", "slow"}

    @pytest.mark.asyncio
    async def test_timeout_is_fail_open(self):
        async def sleepy(_):
            await asyncio.sleep(2.0)
            return StopHookResult(prevent_continuation=True)

        reg = StopHookRegistry()
        reg.register("slow", CallableStopHook(sleepy), timeout_s=0.1)

        result = await reg.execute_all(make_input())
        assert result.prevent_continuation is False
        assert result.blocking_errors == []
        assert len(result.hook_outcomes) == 1
        assert result.hook_outcomes[0]["ok"] is False
        assert result.hook_outcomes[0]["error"] == "timeout"

    @pytest.mark.asyncio
    async def test_exception_is_fail_open(self):
        async def boom(_):
            raise RuntimeError("kaboom")

        reg = StopHookRegistry()
        reg.register("bad", CallableStopHook(boom))

        result = await reg.execute_all(make_input())
        assert result.prevent_continuation is False
        assert result.hook_outcomes[0]["ok"] is False
        assert "RuntimeError" in result.hook_outcomes[0]["error"]

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        async def cancelme(_):
            raise asyncio.CancelledError()

        reg = StopHookRegistry()
        reg.register("cancel", CallableStopHook(cancelme))

        with pytest.raises(asyncio.CancelledError):
            await reg.execute_all(make_input())

    @pytest.mark.asyncio
    async def test_blocking_errors_preserve_registration_order(self):
        async def slow_x(_):
            await asyncio.sleep(0.1)
            return StopHookResult(blocking_error="X")

        async def fast_y(_):
            return StopHookResult(blocking_error="Y")

        reg = StopHookRegistry()
        reg.register("x_slow", CallableStopHook(slow_x))
        reg.register("y_fast", CallableStopHook(fast_y))

        result = await reg.execute_all(make_input())
        assert result.blocking_errors == ["X", "Y"]

    @pytest.mark.asyncio
    async def test_register_replaces_same_name(self):
        async def original(_):
            return StopHookResult(message="original")

        async def replacement(_):
            return StopHookResult(message="replacement")

        reg = StopHookRegistry()
        reg.register("same", CallableStopHook(original))
        reg.register("same", CallableStopHook(replacement))

        result = await reg.execute_all(make_input())
        assert len(result.hook_outcomes) == 1
        assert result.messages == ["replacement"]

    @pytest.mark.asyncio
    async def test_unregister_removes_hook(self):
        async def ok(_):
            return StopHookResult()

        reg = StopHookRegistry()
        reg.register("temp", CallableStopHook(ok))
        assert reg.has_hooks() is True
        reg.unregister("temp")
        assert reg.has_hooks() is False


class TestCommandStopHook:
    @pytest.mark.asyncio
    async def test_exit_0_empty_stdout_returns_default(self):
        hook = CommandStopHook(["sh", "-c", "cat > /dev/null; exit 0"])
        result = await hook.execute(make_input())
        assert result.prevent_continuation is False
        assert result.blocking_error is None

    @pytest.mark.asyncio
    async def test_exit_0_json_stdout_parsed(self):
        hook = CommandStopHook(
            [
                "sh",
                "-c",
                'cat > /dev/null; echo \'{"prevent_continuation": true, "stop_reason": "done"}\'',
            ]
        )
        result = await hook.execute(make_input())
        assert result.prevent_continuation is True
        assert result.stop_reason == "done"

    @pytest.mark.asyncio
    async def test_exit_2_stdout_becomes_blocking_error(self):
        hook = CommandStopHook(["sh", "-c", "echo 'missing tests'; exit 2"])
        result = await hook.execute(make_input())
        assert result.blocking_error == "missing tests"

    @pytest.mark.asyncio
    async def test_exit_2_json_blocking_error(self):
        hook = CommandStopHook(
            [
                "sh",
                "-c",
                'echo \'{"blocking_error": "tests failed"}\'; exit 2',
            ]
        )
        result = await hook.execute(make_input())
        assert result.blocking_error == "tests failed"

    @pytest.mark.asyncio
    async def test_exit_other_is_fail_open(self):
        hook = CommandStopHook(["sh", "-c", "echo 'oops' 1>&2; exit 7"])
        result = await hook.execute(make_input())
        assert result.prevent_continuation is False
        assert result.blocking_error is None

    @pytest.mark.asyncio
    async def test_timeout_kills_subprocess(self):
        hook = CommandStopHook(["sh", "-c", "sleep 10"], timeout_s=0.2)
        t0 = time.monotonic()
        result = await hook.execute(make_input())
        elapsed = time.monotonic() - t0
        assert result.prevent_continuation is False
        assert result.blocking_error is None
        assert elapsed < 1.5, f"subprocess not killed promptly: {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_bad_command_is_fail_open(self):
        hook = CommandStopHook(["/absolutely/not/a/real/binary/xyz123"])
        result = await hook.execute(make_input())
        assert result.prevent_continuation is False
        assert result.blocking_error is None

    def test_empty_command_raises(self):
        with pytest.raises(ValueError):
            CommandStopHook([])


class StubLLM:
    def __init__(self, reply: str):
        self.reply = reply

    async def generate(self, messages, system: str = "") -> str:
        return self.reply


class TestPromptStopHook:
    @pytest.mark.asyncio
    async def test_parses_json_object_from_response(self):
        llm = StubLLM('{"prevent_continuation": true, "stop_reason": "review"}')
        hook = PromptStopHook(llm, "Check: {last_assistant_message}")
        result = await hook.execute(make_input(last_assistant_message="hello"))
        assert result.prevent_continuation is True
        assert result.stop_reason == "review"

    @pytest.mark.asyncio
    async def test_parses_json_inside_code_fence(self):
        llm = StubLLM('Sure, here\'s my verdict:\n```json\n{"blocking_error": "retry please"}\n```')
        hook = PromptStopHook(llm, "Check: {last_assistant_message}")
        result = await hook.execute(make_input())
        assert result.blocking_error == "retry please"

    @pytest.mark.asyncio
    async def test_non_json_response_is_fail_open(self):
        llm = StubLLM("I think this looks fine, go ahead.")
        hook = PromptStopHook(llm, "Check: {last_assistant_message}")
        result = await hook.execute(make_input())
        assert result.prevent_continuation is False
        assert result.blocking_error is None

    @pytest.mark.asyncio
    async def test_llm_exception_is_fail_open(self):
        class Broken:
            async def generate(self, messages, system: str = "") -> str:
                raise RuntimeError("api down")

        hook = PromptStopHook(Broken(), "Check: {last_assistant_message}")
        result = await hook.execute(make_input())
        assert result.prevent_continuation is False

    @pytest.mark.asyncio
    async def test_bad_template_is_fail_open(self):
        llm = StubLLM("{}")
        hook = PromptStopHook(llm, "Check: {unknown_field}")
        result = await hook.execute(make_input())
        assert result.prevent_continuation is False


LIVE = bool(os.environ.get("CDT_URL")) and bool(os.environ.get("CDT_KEY"))
live_only = pytest.mark.skipif(
    not LIVE, reason="CDT_URL/CDT_KEY not set — live stop-hook tests skipped"
)


def live_settings() -> Settings:
    settings = Settings.load()
    settings.sqlite_db_path = ":memory:"
    settings.catalog_dir = str(Path(__file__).parent.parent / "catalog")
    return settings


async def boot_with_task():
    """Bootstrap the engine and return (state, report, thread_id, thread).

    The thread is created eagerly so tests can register stop hooks on
    it before handle_input runs.
    """
    from src.bootstrap import bootstrap

    settings = live_settings()
    state, report = await bootstrap(settings=settings)
    assert state.is_operational, f"Boot failed: {report.errors}"
    slot = state.coordinator.slots.allocate("stop-hook-test-user")
    tid = slot.thread_id
    thread = await state.coordinator.get_or_create_thread(tid)
    return state, report, tid, thread


class TestStopHookIntegration:
    @live_only
    @pytest.mark.asyncio
    async def test_prevent_continuation_exits_with_hook_prevented(self):
        from src.bootstrap import shutdown
        from src.engine_core.events import StopHookExecuted

        state, report, tid, thread = await boot_with_task()
        try:
            events: list = []

            async def capture(event):
                events.append(event)

            state.coordinator.on(capture)

            async def always_prevent(inp: StopHookInput) -> StopHookResult:
                return StopHookResult(prevent_continuation=True, stop_reason="test-abort")

            thread.stop_hooks.register("prevent", CallableStopHook(always_prevent))

            from src.engine_core.events import ExecutionSummary

            result = await state.coordinator.handle_input(
                message="Reply with just the word hi.",
                thread_id=tid,
            )

            summaries = [e for e in events if isinstance(e, ExecutionSummary)]
            assert summaries, "No ExecutionSummary event emitted"
            assert summaries[-1].exit_reason == LoopExitReason.HOOK_PREVENTED.value

            hook_events = [e for e in events if isinstance(e, StopHookExecuted)]
            assert len(hook_events) >= 1
            assert any(e.prevented for e in hook_events)

            assert isinstance(result, str)
            assert "Stopped by hook" in result
            assert "test-abort" in result
        finally:
            await shutdown(state)

    @live_only
    @pytest.mark.asyncio
    async def test_blocking_error_triggers_one_retry(self):
        from src.bootstrap import shutdown
        from src.engine_core.events import StopHookExecuted

        state, report, tid, thread = await boot_with_task()
        try:
            call_log: list[bool] = []

            async def block_once(inp: StopHookInput) -> StopHookResult:
                call_log.append(inp.stop_hook_active)
                if not inp.stop_hook_active:
                    return StopHookResult(blocking_error="Please say 'ok' at the end.")
                return StopHookResult()

            thread.stop_hooks.register("block_once", CallableStopHook(block_once))

            events: list = []

            async def capture(event):
                events.append(event)

            state.coordinator.on(capture)

            await state.coordinator.handle_input(
                message="Reply with just the word hi.",
                thread_id=tid,
            )

            assert len(call_log) >= 2
            assert call_log[0] is False
            assert any(flag is True for flag in call_log[1:])

            hook_events = [e for e in events if isinstance(e, StopHookExecuted)]
            assert any(e.blocked for e in hook_events)
        finally:
            await shutdown(state)

    @live_only
    @pytest.mark.asyncio
    async def test_blocking_error_bounded_to_one_retry(self):
        from src.bootstrap import shutdown

        state, report, tid, thread = await boot_with_task()
        try:
            call_count = {"n": 0}

            async def always_block(inp: StopHookInput) -> StopHookResult:
                call_count["n"] += 1
                return StopHookResult(blocking_error="keep going")

            thread.stop_hooks.register("always_block", CallableStopHook(always_block))

            result = await state.coordinator.handle_input(
                message="Say hi.",
                thread_id=tid,
            )
            assert isinstance(result, str)
        finally:
            await shutdown(state)

    @live_only
    @pytest.mark.asyncio
    async def test_feature_flag_off_bypasses_hooks(self):
        from src.bootstrap import bootstrap, shutdown
        from src.engine_core.events import StopHookExecuted

        settings = live_settings()
        settings.stop_hooks_enabled = False
        state, report = await bootstrap(settings=settings)
        try:
            assert state.is_operational
            slot = state.coordinator.slots.allocate("flag-off-user")
            tid = slot.thread_id
            thread = await state.coordinator.get_or_create_thread(tid)

            hook_calls = {"n": 0}

            async def should_never_run(inp):
                hook_calls["n"] += 1
                return StopHookResult(prevent_continuation=True)

            thread.stop_hooks.register("forbidden", CallableStopHook(should_never_run))

            events: list = []

            async def capture(event):
                events.append(event)

            state.coordinator.on(capture)

            await state.coordinator.handle_input(
                message="Reply with just hi.",
                thread_id=tid,
            )

            assert hook_calls["n"] == 0
            hook_events = [e for e in events if isinstance(e, StopHookExecuted)]
            assert hook_events == []
        finally:
            await shutdown(state)

    @live_only
    @pytest.mark.asyncio
    async def test_stop_hook_event_emitted_on_end_turn(self):
        from src.bootstrap import shutdown
        from src.engine_core.events import StopHookExecuted

        state, report, tid, thread = await boot_with_task()
        try:

            async def noop(inp):
                return StopHookResult()

            thread.stop_hooks.register("noop", CallableStopHook(noop))

            events: list = []

            async def capture(event):
                events.append(event)

            state.coordinator.on(capture)

            await state.coordinator.handle_input(
                message="Reply with just hi.",
                thread_id=tid,
            )

            hook_events = [e for e in events if isinstance(e, StopHookExecuted)]
            assert len(hook_events) >= 1
            assert any(e.trigger == "after_end_turn" for e in hook_events)
            assert all(e.hook_outcomes for e in hook_events)
        finally:
            await shutdown(state)


class ScriptedAdapter:
    """Counts upstream attempts. Either streams a clean end-turn response or
    raises the given exception on every call."""

    def __init__(self, *, raises: Exception | None = None, text: str = "done"):
        self.attempts = 0
        self.raises = raises
        self.text = text

    async def stream_with_tools(
        self,
        messages,
        system=None,
        tools=None,
        super_thinking: bool = False,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ):
        from src.herald.types import StopReason, StreamEvent, Usage

        self.attempts += 1
        if self.raises is not None:
            raise self.raises
        yield StreamEvent(type="text", text=self.text)
        yield StreamEvent(type="stop", stop_reason=StopReason.END_TURN)
        yield StreamEvent(type="usage", usage=Usage(input_tokens=100, output_tokens=10))

    async def generate(self, messages, system=""):
        return "summary"


def loop_tools():
    """One trivial tool so the loop takes the tools>0 streaming path, matching
    production wiring. Never invoked by these tests."""
    from src.utility_directory.tool_runtime import (
        ToolDef as RuntimeToolDef,
        ToolRegistry,
    )

    reg = ToolRegistry()

    async def noop_handler(params):
        return "ok"

    reg.register(
        RuntimeToolDef(
            name="noop",
            description="A no-op tool (never called).",
            input_schema={"type": "object", "properties": {}},
            handler=noop_handler,
        )
    )
    return reg


async def make_loop_thread(db, adapter, session: str):
    from src.engine_core.events import EventEmitter
    from src.engine_core.thread import ExecutionThread
    from src.memory.memory_factory import create_working_memory

    return ExecutionThread(
        adapter=adapter,
        emitter=EventEmitter(),
        memory=create_working_memory(session, db=db),
        tools=loop_tools(),
        settings=Settings(),
    )


class TestStopHookRetryBounds:
    @pytest.mark.asyncio
    async def test_always_blocking_hook_forces_exactly_one_retry(self, db):
        """The stop_hook_active guard is the bound: a hook that blocks on every
        invocation gets one retry turn, then the end-of-turn branch falls
        through to the loop break. Two model calls, not max_turns of them."""
        from src.engine_core.schema import RequestOptions

        adapter = ScriptedAdapter()
        th = await make_loop_thread(db, adapter, "sess-hook-retry-bound")

        seen_active: list[bool] = []

        async def always_block(inp: StopHookInput) -> StopHookResult:
            seen_active.append(inp.stop_hook_active)
            return StopHookResult(blocking_error="keep going")

        th.stop_hooks.register("always_block", CallableStopHook(always_block))

        await th.agent_loop("hi", options=RequestOptions(query_source="foreground"))

        assert adapter.attempts == 2, f"expected 1 retry, got {adapter.attempts - 1}"
        assert seen_active == [False, True]

    @pytest.mark.asyncio
    async def test_overflow_ladder_fails_loud_before_any_hook_retry(self, db):
        """The other bound: when no tier can free tokens, the ladder escalates
        in-process and raises CONTEXT_OVERFLOW_UNRECOVERABLE rather than
        retrying the API. The loop dies at the failing call, so a blocking hook
        never gets a turn boundary to restart the ladder from."""
        from src.engine_core.schema import RequestOptions
        from src.errors import EngineError, ErrorCode

        adapter = ScriptedAdapter(raises=Exception("prompt is too long"))
        th = await make_loop_thread(db, adapter, "sess-hook-overflow-bound")

        hook_calls = {"n": 0}

        async def counting_block(inp: StopHookInput) -> StopHookResult:
            hook_calls["n"] += 1
            return StopHookResult(blocking_error="keep going")

        th.stop_hooks.register("counting_block", CallableStopHook(counting_block))

        with pytest.raises(EngineError) as ei:
            await th.agent_loop("hi", options=RequestOptions(query_source="foreground"))

        assert ei.value.code == ErrorCode.CONTEXT_OVERFLOW_UNRECOVERABLE
        assert adapter.attempts == 1
        assert hook_calls["n"] == 0
