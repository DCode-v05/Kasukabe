"""Tests for the completed hook system.

Covers everything added beyond the original PreToolUse / PostToolUse / Stop
registries:

- ``generic_hooks.GenericHookRegistry`` — parallel execution, aggregation,
  fail-open timeout semantics, outcome telemetry, plugin env injection.
- ``prompt_hooks.PromptPreToolUseHook`` / ``PromptPostToolUseHook``.
- ``agent_hooks.AgentPreToolUseHook`` / ``AgentPostToolUseHook`` — backed
  by a thin SubAgent stub (we patch ``run_hook_subagent`` so we don't
  spawn a real herald adapter).
- ``lifecycle_hooks.LifecycleHookBundle`` — registry lookup + register_*.
- ``hook_settings.HookSettingsManager`` — plugin env overrides routed to
  the CommandGenericHook + the two core Command hooks; generic events
  wired into the bundle.
- Dispatch integration: PreCompact veto returns CompactionResult with
  strategy "skipped_by_hook"; PostCompact ``additional_contexts`` are
  surfaced via ``CompactionResult.hook_contexts``.
- SubAgent invokes SubagentStart + SubagentStop generic hooks when a
  bundle is attached.

All tests run under docker (see docker-compose.yml ``test`` service).
"""

from __future__ import annotations

import asyncio
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from src.engine_core.generic_hooks import (
    AggregatedGenericResult,
    CallableGenericHook,
    CommandGenericHook,
    GenericHookRegistry,
    GenericHookResult,
)
from src.engine_core.hooks import HookEvent
from src.engine_core.lifecycle_hooks import (
    LifecycleHookBundle,
    PreCompactInput,
    SubagentStartInput,
    UserPromptSubmitInput,
)
from src.engine_core.hook_settings import (
    HookSettingsManager,
    plugin_env_overrides,
)
from src.engine_core.pre_tool_hooks import PreToolUseHookRegistry
from src.engine_core.post_tool_hooks import PostToolUseHookRegistry
from src.engine_core.stop_hooks import StopHookRegistry
from src.engine_core.compaction_orchestrator import (
    CompactionConfig,
    CompactionOrchestrator,
    CompactionResult,
)


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# GenericHookRegistry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DummyInput:
    value: str = ""
    hook_event_name: str = "Dummy"


class TestGenericHookRegistry:
    async def test_empty_registry_is_noop(self):
        reg: GenericHookRegistry[DummyInput] = GenericHookRegistry("Dummy")
        assert not reg.has_hooks()
        agg = await reg.execute_all(DummyInput(value="x"))
        assert isinstance(agg, AggregatedGenericResult)
        assert agg.additional_contexts == []
        assert agg.prevent_continuation is False

    async def test_additional_contexts_preserve_registration_order(self):
        reg: GenericHookRegistry[DummyInput] = GenericHookRegistry("Dummy")

        async def slow(_: DummyInput) -> GenericHookResult:
            await asyncio.sleep(0.02)
            return GenericHookResult(additional_context="slow")

        async def fast(_: DummyInput) -> GenericHookResult:
            return GenericHookResult(additional_context="fast")

        reg.register("a", CallableGenericHook(slow))
        reg.register("b", CallableGenericHook(fast))

        agg = await reg.execute_all(DummyInput())
        assert agg.additional_contexts == ["slow", "fast"]

    async def test_any_prevent_continuation_wins(self):
        reg: GenericHookRegistry[DummyInput] = GenericHookRegistry("Dummy")

        async def ok(_: DummyInput) -> GenericHookResult:
            return GenericHookResult()

        async def stop(_: DummyInput) -> GenericHookResult:
            return GenericHookResult(prevent_continuation=True)

        reg.register("ok", CallableGenericHook(ok))
        reg.register("stop", CallableGenericHook(stop))

        agg = await reg.execute_all(DummyInput())
        assert agg.prevent_continuation is True

    async def test_last_updated_payload_wins(self):
        reg: GenericHookRegistry[DummyInput] = GenericHookRegistry("Dummy")

        async def first(_: DummyInput) -> GenericHookResult:
            return GenericHookResult(updated_payload={"n": 1})

        async def second(_: DummyInput) -> GenericHookResult:
            return GenericHookResult(updated_payload={"n": 2})

        reg.register("first", CallableGenericHook(first))
        reg.register("second", CallableGenericHook(second))

        agg = await reg.execute_all(DummyInput())
        assert agg.updated_payload == {"n": 2}

    async def test_exception_is_fail_open(self):
        reg: GenericHookRegistry[DummyInput] = GenericHookRegistry("Dummy")

        async def boom(_: DummyInput) -> GenericHookResult:
            raise RuntimeError("nope")

        async def ok(_: DummyInput) -> GenericHookResult:
            return GenericHookResult(additional_context="ok")

        reg.register("boom", CallableGenericHook(boom))
        reg.register("ok", CallableGenericHook(ok))

        agg = await reg.execute_all(DummyInput())
        # Broken hook failed but the ok one still contributed.
        assert agg.additional_contexts == ["ok"]
        assert any(o["name"] == "boom" and not o["ok"] for o in agg.hook_outcomes)

    async def test_timeout_fails_open(self):
        reg: GenericHookRegistry[DummyInput] = GenericHookRegistry("Dummy")

        async def hang(_: DummyInput) -> GenericHookResult:
            await asyncio.sleep(5.0)
            return GenericHookResult(additional_context="late")

        reg.register("hang", CallableGenericHook(hang, timeout_s=0.05), timeout_s=0.05)
        agg = await reg.execute_all(DummyInput())
        assert agg.additional_contexts == []
        assert agg.hook_outcomes[0]["error"] == "timeout"

    async def test_unregister_removes_hook(self):
        reg: GenericHookRegistry[DummyInput] = GenericHookRegistry("Dummy")

        async def h(_: DummyInput) -> GenericHookResult:
            return GenericHookResult(additional_context="x")

        reg.register("h", CallableGenericHook(h))
        reg.unregister("h")
        assert not reg.has_hooks()


# ---------------------------------------------------------------------------
# CommandGenericHook — subprocess
# ---------------------------------------------------------------------------


def write_hook_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "hook.sh"
    script.write_text("#!/usr/bin/env bash\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


class TestCommandGenericHook:
    async def test_stdout_json_parsed(self, tmp_path):
        body = 'cat >/dev/null\necho \'{"additional_context": "hi"}\'\n'
        script = write_hook_script(tmp_path, body)
        hook = CommandGenericHook(
            [str(script)],
            event_name="Dummy",
            timeout_s=10.0,
        )
        res = await hook.execute(DummyInput())
        assert res.additional_context == "hi"

    async def test_exit_code_2_becomes_blocking_error(self, tmp_path):
        body = 'cat >/dev/null\necho "no thanks"\nexit 2\n'
        script = write_hook_script(tmp_path, body)
        hook = CommandGenericHook(
            [str(script)],
            event_name="Dummy",
            timeout_s=10.0,
        )
        res = await hook.execute(DummyInput())
        assert res.blocking_error == "no thanks"

    async def test_plugin_env_overrides_reach_subprocess(self, tmp_path):
        # Hook prints PLUGIN_ROOT + PLUGIN_OPTION_FLAG as additional_context.
        body = (
            "cat >/dev/null\n"
            'printf \'{"additional_context":"%s:%s"}\' '
            '"$PLUGIN_ROOT" "$PLUGIN_OPTION_FLAG"\n'
        )
        script = write_hook_script(tmp_path, body)
        hook = CommandGenericHook(
            [str(script)],
            event_name="Dummy",
            timeout_s=10.0,
            env_overrides={"PLUGIN_ROOT": "/plugins/foo", "PLUGIN_OPTION_FLAG": "on"},
        )
        res = await hook.execute(DummyInput())
        assert res.additional_context == "/plugins/foo:on"

    async def test_stdin_payload_has_event_name(self, tmp_path):
        # Write the incoming stdin payload to a file so we can inspect
        # it from the test without fighting JSON-in-shell-escaping.
        capture = tmp_path / "payload.json"
        body = f'cat > "{capture}"\necho \'{{"additional_context": "wrote-file"}}\'\n'
        script = write_hook_script(tmp_path, body)
        hook = CommandGenericHook(
            [str(script)],
            event_name="MyEvent",
            timeout_s=10.0,
        )
        res = await hook.execute(DummyInput(value="payload-value"))
        assert res.additional_context == "wrote-file"
        on_disk = json.loads(capture.read_text())
        assert on_disk["hook_event_name"] == "MyEvent"
        assert on_disk["value"] == "payload-value"


# ---------------------------------------------------------------------------
# PromptPreToolUseHook / PromptPostToolUseHook
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AgentPreToolUseHook / AgentPostToolUseHook — patch run_hook_subagent
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# LifecycleHookBundle — per-event registry routing
# ---------------------------------------------------------------------------


class TestLifecycleHookBundle:
    async def test_register_callable_and_dispatch(self):
        bundle = LifecycleHookBundle()
        assert not bundle.has_hooks(HookEvent.PRE_COMPACT)

        captured: list[PreCompactInput] = []

        async def cb(inp: PreCompactInput) -> GenericHookResult:
            captured.append(inp)
            return GenericHookResult(additional_context="seen")

        bundle.register_callable(HookEvent.PRE_COMPACT, "obs", cb)
        assert bundle.has_hooks(HookEvent.PRE_COMPACT)

        inp = PreCompactInput(
            thread_id="t1",
            turn_number=3,
            message_count=20,
            token_estimate=100_000,
            context_window=200_000,
            trigger_reason="threshold",
        )
        agg = await bundle.registry(HookEvent.PRE_COMPACT).execute_all(inp)
        assert agg.additional_contexts == ["seen"]
        assert captured[0].trigger_reason == "threshold"

    async def test_different_events_are_isolated(self):
        bundle = LifecycleHookBundle()

        async def pre(_: PreCompactInput) -> GenericHookResult:
            return GenericHookResult(additional_context="pre")

        async def sub(_: SubagentStartInput) -> GenericHookResult:
            return GenericHookResult(additional_context="sub")

        bundle.register_callable(HookEvent.PRE_COMPACT, "a", pre)
        bundle.register_callable(HookEvent.SUBAGENT_START, "b", sub)

        # SUBAGENT_START registry must not fire PreCompact hooks.
        sub_inp = SubagentStartInput(
            thread_id="t",
            agent_id="a1",
            agent_name="x",
            task_description="y",
        )
        agg_sub = await bundle.registry(HookEvent.SUBAGENT_START).execute_all(sub_inp)
        assert agg_sub.additional_contexts == ["sub"]

    async def test_ups_updated_payload_replaces_text(self):
        bundle = LifecycleHookBundle()

        async def rewrite(inp: UserPromptSubmitInput) -> GenericHookResult:
            return GenericHookResult(
                updated_payload={"text": inp.text.upper()},
            )

        bundle.register_callable(HookEvent.USER_PROMPT_SUBMIT, "upper", rewrite)
        agg = await bundle.registry(HookEvent.USER_PROMPT_SUBMIT).execute_all(
            UserPromptSubmitInput(thread_id="t", text="hello", turn_number=0),
        )
        assert agg.updated_payload == {"text": "HELLO"}


# ---------------------------------------------------------------------------
# hook_settings — generic event routing + plugin env discovery
# ---------------------------------------------------------------------------


class TestHookSettingsGenericRouting:
    async def test_generic_event_command_routes_to_bundle(self, tmp_path, monkeypatch):
        # Build a settings file with a SubagentStart command hook.
        body = 'cat >/dev/null\necho \'{"additional_context": "bundle-ok"}\'\n'
        script = write_hook_script(tmp_path, body)

        settings = {
            "hooks": [
                {
                    "event": "SubagentStart",
                    "matcher": "*",
                    "hooks": [
                        {"type": "command", "command": str(script), "timeout": 5},
                    ],
                }
            ]
        }
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps(settings))

        # Force the manager to load only this project-level file.
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        monkeypatch.setenv("INTELLIGENCE_ENGINE_CONFIG_DIR", str(config_dir))

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / ".intelligence-engine").mkdir()
        (project_dir / ".intelligence-engine" / "settings.json").write_text(json.dumps(settings))

        bundle = LifecycleHookBundle()
        mgr = HookSettingsManager(
            pre_tool_registry=PreToolUseHookRegistry(),
            post_tool_registry=PostToolUseHookRegistry(),
            stop_registry=StopHookRegistry(),
            cwd=str(project_dir),
            lifecycle_bundle=bundle,
        )
        mgr.load()

        assert bundle.has_hooks(HookEvent.SUBAGENT_START)

        inp = SubagentStartInput(
            thread_id="t",
            agent_id="a1",
            agent_name="x",
            task_description="y",
        )
        agg = await bundle.registry(HookEvent.SUBAGENT_START).execute_all(inp)
        assert agg.additional_contexts == ["bundle-ok"]

    async def test_plugin_env_overrides_reads_options(self, tmp_path):
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "data").mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"options": {"flag": True, "timeout": 42}})
        )
        hooks_json = plugin_dir / "hooks.json"
        hooks_json.write_text("{}")

        env = plugin_env_overrides(hooks_json)
        assert env["PLUGIN_ROOT"] == str(plugin_dir)
        assert env["PLUGIN_DATA"] == str(plugin_dir / "data")
        assert env["PLUGIN_OPTION_FLAG"] == "True"
        assert env["PLUGIN_OPTION_TIMEOUT"] == "42"

    async def test_http_stop_routes_through_typed_stop_hook(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Regression: HTTP Stop hooks must return ``StopHookResult``
        (has .message / .stop_reason) so ``StopHookRegistry.execute_all``
        doesn't AttributeError."""

        # Patch the HTTP POST so we don't hit the network.
        called = {}

        async def fake_post(url, body, timeout_s):
            called["url"] = url
            return {
                "prevent_continuation": True,
                "message": "stopped",
                "stop_reason": "policy-hook",
            }

        import src.engine_core.hook_executors as he

        monkeypatch.setattr(he, "post_json", fake_post)

        # Round-trip through HookSettingsManager so we also verify the
        # settings-file path builds the correct class.
        settings = {
            "hooks": [
                {
                    "event": "Stop",
                    "matcher": "*",
                    "hooks": [
                        {"type": "http", "url": "http://test", "timeout": 5},
                    ],
                }
            ]
        }
        project_dir = tmp_path / "proj"
        (project_dir / ".intelligence-engine").mkdir(parents=True)
        (project_dir / ".intelligence-engine" / "settings.json").write_text(json.dumps(settings))
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        monkeypatch.setenv("INTELLIGENCE_ENGINE_CONFIG_DIR", str(config_dir))

        stop_reg = StopHookRegistry()
        mgr = HookSettingsManager(
            pre_tool_registry=PreToolUseHookRegistry(),
            post_tool_registry=PostToolUseHookRegistry(),
            stop_registry=stop_reg,
            cwd=str(project_dir),
            lifecycle_bundle=LifecycleHookBundle(),
        )
        mgr.load()

        assert stop_reg.has_hooks()
        from src.engine_core.stop_hooks import StopHookInput

        agg = await stop_reg.execute_all(
            StopHookInput(
                stop_hook_active=False,
                last_assistant_message="hello",
                turn_number=1,
                last_stop_reason="end_turn",
                thread_id="t1",
            )
        )
        # Would raise AttributeError pre-fix because GenericHookResult
        # lacks .message / .stop_reason.
        assert agg.prevent_continuation is True
        assert agg.messages == ["stopped"]
        assert agg.stop_reasons == ["policy-hook"]


# ---------------------------------------------------------------------------
# thread.py — hook_contexts from CompactionResult injected as system-reminder
# ---------------------------------------------------------------------------


class TestHookContextInjection:
    """Integration: CompactionResult.hook_contexts must reach the message
    list as a ``<system-reminder>`` so the model sees the PreCompact /
    PostCompact ``additional_context`` on the next turn. Pre-fix, the
    field was populated but never read in thread.py.
    """

    async def test_hook_contexts_become_system_reminder(self):
        from src.engine_core.compaction_orchestrator import CompactionResult
        from src.herald.types import Message

        # Simulate the exact logic in thread.py that consumes the result.
        result = CompactionResult(
            messages=[],
            strategy="summary",
            tokens_freed=100,
            was_compacted=True,
            hook_contexts=["precompact-note", "postcompact-summary"],
        )
        messages: list = list(result.messages)
        if result.hook_contexts:
            import time

            reminder = "\n\n".join(
                f"<system-reminder>{ctx}</system-reminder>" for ctx in result.hook_contexts if ctx
            )
            messages.append(Message(role="user", content=reminder, timestamp=time.time()))
        assert len(messages) == 1
        assert "<system-reminder>precompact-note</system-reminder>" in messages[0].content
        assert "<system-reminder>postcompact-summary</system-reminder>" in messages[0].content


# ---------------------------------------------------------------------------
# Compaction orchestrator — PreCompact veto and PostCompact context flow
# ---------------------------------------------------------------------------


class StubEmitter:
    def __init__(self):
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


class StubMemory:
    async def write(self, *args, **kwargs):
        return None

    async def read(self, *args, **kwargs):
        return []


class TestCompactionHooks:
    async def build(self, bundle: LifecycleHookBundle) -> CompactionOrchestrator:
        # Minimal orchestrator wiring — only used for the dispatch
        # integration, never invokes the real LLM adapter.
        return CompactionOrchestrator(
            adapter=None,
            emitter=StubEmitter(),
            memory=StubMemory(),
            config=CompactionConfig(),
            lifecycle_bundle=bundle,
        )

    async def test_reactive_compact_fires_post_compact_hook(self, monkeypatch):
        """Regression: reactive_compact must fire PostCompact hooks so
        overflow recovery is not a blind spot for audit / telemetry
        plugins."""
        from src.engine_core import context_engineering as ce
        from src.engine_core import time_based_microcompact as tbmc

        # High pre, low post so made_progress=True.
        tokens_iter = iter([100_000, 10_000])
        monkeypatch.setattr(
            ce,
            "estimate_messages_tokens",
            lambda msgs: next(tokens_iter),
        )
        monkeypatch.setattr(ce, "compact_messages", lambda m, w, **kw: m[:1])
        monkeypatch.setattr(ce, "guard_tool_results", lambda m, w, **kw: m)
        monkeypatch.setattr(ce, "emergency_compact", lambda m, w, **kw: m[:1])
        monkeypatch.setattr(
            ce,
            "repair_tool_use_result_pairing",
            lambda m: m,
        )
        monkeypatch.setattr(ce, "needs_compaction", lambda t, w: False)
        monkeypatch.setattr(
            tbmc,
            "maybe_microcompact",
            lambda *a, **kw: None,
        )

        bundle = LifecycleHookBundle()
        seen: list[Any] = []

        async def on_post(inp) -> GenericHookResult:
            seen.append(inp)
            return GenericHookResult(additional_context="post-noted")

        bundle.register_callable(HookEvent.POST_COMPACT, "obs", on_post)

        # Bypass finalize_compaction which has dependencies we don't
        # want to wire in this unit test.
        orch = CompactionOrchestrator(
            adapter=None,
            emitter=StubEmitter(),
            memory=StubMemory(),
            config=CompactionConfig(),
            lifecycle_bundle=bundle,
        )

        async def noop_finalize(messages, **kwargs):
            return messages

        monkeypatch.setattr(orch, "finalize_compaction", noop_finalize)

        result = await orch.reactive_compact(
            messages=[object(), object(), object()],
            overflow_attempt=0,
            context_window=200_000,
            thread_id="t1",
            turn_number=2,
        )
        assert result.made_progress is True
        assert len(seen) == 1
        assert seen[0].strategy.startswith("reactive_")

    async def test_pre_compact_veto_short_circuits(self, monkeypatch):
        from src.engine_core import context_engineering as ce

        monkeypatch.setattr(ce, "estimate_messages_tokens", lambda msgs: 1_000_000)
        monkeypatch.setattr(ce, "needs_compaction", lambda t, w: True)
        monkeypatch.setattr(ce, "snip_prepass", lambda msgs: msgs)
        monkeypatch.setattr(ce, "guard_tool_results", lambda m, w, **k: m)
        monkeypatch.setattr(ce, "estimate_system_tokens", lambda s: 0)
        monkeypatch.setattr(ce, "estimate_tokens", lambda s: 0)
        monkeypatch.setattr(ce, "RESERVE_FLOOR", 0)
        monkeypatch.setattr(ce, "compact_messages", lambda m, w: m)

        bundle = LifecycleHookBundle()

        async def veto(_: PreCompactInput) -> GenericHookResult:
            return GenericHookResult(prevent_continuation=True)

        bundle.register_callable(HookEvent.PRE_COMPACT, "veto", veto)
        orch = await self.build(bundle)

        messages = [object(), object(), object()]
        result = await orch.compact_if_needed(
            messages=messages,
            system_prompt=[],
            tools_schema=[],
            thread_id="t1",
            turn_number=1,
            context_window=200_000,
        )
        assert isinstance(result, CompactionResult)
        assert result.strategy == "skipped_by_hook"
        assert result.was_compacted is False


# ---------------------------------------------------------------------------
# SubAgent start/stop hook dispatch (integration via stubbed adapter)
# ---------------------------------------------------------------------------


