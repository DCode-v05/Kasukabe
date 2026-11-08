"""The hook feature gates must actually gate something.

pre_tool_hooks_enabled, post_tool_hooks_enabled and hooks_settings_load_enabled
were declared on Settings with behavioural docstrings, were never parsed from
the environment, and were never read at any execution point. Setting one
produced no error, no warning and no change - the sibling stop_hooks_enabled
being wired is what made the family look implemented.

These drive the REAL agent loop (ExecutionThread.execute) with a scripted
adapter that emits one tool call, so the gate is observed at the place the
hooks actually fire.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest

from src.config import Settings
from src.engine_core.events import EventEmitter
from src.engine_core.post_tool_hooks import (
    CallablePostToolUseHook,
    PostToolUseHookResult,
)
from src.engine_core.pre_tool_hooks import (
    CallablePreToolUseHook,
    PreToolUseHookResult,
)
from src.engine_core.thread import ExecutionThread
from src.herald.types import Message, StopReason, StreamEvent, ToolCall, ToolDef, Usage
from src.memory.memory_factory import create_working_memory
from src.utility_directory.tool_runtime import ToolDef as RuntimeToolDef, ToolRegistry


class ToolThenStopAdapter:
    """One tool call, then a clean stop on the follow-up turn."""

    def __init__(self) -> None:
        self.turn = 0

    async def stream_with_tools(
        self,
        messages: list[Message],
        system=None,
        tools: list[ToolDef] | None = None,
        super_thinking: bool = False,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        self.turn += 1
        if self.turn == 1:
            yield StreamEvent(
                type="tool_call", tool_call=ToolCall(id="t1", name="noop", input={})
            )
            yield StreamEvent(type="stop", stop_reason=StopReason.TOOL_USE)
        else:
            yield StreamEvent(type="text", text="done")
            yield StreamEvent(type="stop", stop_reason=StopReason.END_TURN)
        yield StreamEvent(type="usage", usage=Usage(input_tokens=5, output_tokens=5))

    async def generate(self, messages, system=""):
        return "summary"


def noop_registry() -> ToolRegistry:
    reg = ToolRegistry()

    async def noop_handler(params):
        return "tool ran"

    reg.register(
        RuntimeToolDef(
            name="noop",
            description="A no-op tool.",
            input_schema={"type": "object", "properties": {}},
            handler=noop_handler,
        )
    )
    return reg


async def run_turn(db, settings: Settings, session: str, thread_id: str) -> dict:
    """Run one real turn with a pre-tool and a post-tool hook registered.

    Returns a dict of how many times each hook was invoked.
    """
    seen = {"pre": 0, "post": 0}

    th = ExecutionThread(
        adapter=ToolThenStopAdapter(),
        emitter=EventEmitter(),
        memory=create_working_memory(session, db=db),
        tools=noop_registry(),
        settings=settings,
    )

    async def pre_hook(inp) -> PreToolUseHookResult:
        seen["pre"] += 1
        return PreToolUseHookResult()

    async def post_hook(ctx) -> PostToolUseHookResult:
        seen["post"] += 1
        return PostToolUseHookResult()

    th.pre_tool_hooks.register("counter", CallablePreToolUseHook(pre_hook))
    th.post_tool_hooks.register("counter", CallablePostToolUseHook(post_hook))

    await th.execute("use the tool", thread_id=thread_id)
    return seen


@pytest.mark.asyncio
async def test_hooks_fire_when_the_gates_are_on(db):
    seen = await run_turn(db, Settings(), "sess-hooks-on", "t-hooks-on")
    assert seen["pre"] == 1
    assert seen["post"] == 1


@pytest.mark.asyncio
async def test_pre_tool_gate_off_skips_the_pre_tool_hook(db):
    settings = Settings()
    settings.pre_tool_hooks_enabled = False
    seen = await run_turn(db, settings, "sess-pre-off", "t-pre-off")
    assert seen["pre"] == 0
    assert seen["post"] == 1


@pytest.mark.asyncio
async def test_post_tool_gate_off_skips_the_post_tool_hook(db):
    settings = Settings()
    settings.post_tool_hooks_enabled = False
    seen = await run_turn(db, settings, "sess-post-off", "t-post-off")
    assert seen["pre"] == 1
    assert seen["post"] == 0


class TestGatesAreParsedFromTheEnvironment:
    """Setting the documented env var must reach Settings, or the operator's
    restart changes nothing and they debug the wrong subsystem."""

    def test_defaults_are_on(self, monkeypatch):
        for name in (
            "PRE_TOOL_HOOKS_ENABLED",
            "POST_TOOL_HOOKS_ENABLED",
            "HOOKS_SETTINGS_LOAD_ENABLED",
        ):
            monkeypatch.delenv(name, raising=False)
        s = Settings.load(env_path=Path("/nonexistent.env"))
        assert s.pre_tool_hooks_enabled is True
        assert s.post_tool_hooks_enabled is True
        assert s.hooks_settings_load_enabled is True

    @pytest.mark.parametrize(
        "env_name,attr",
        [
            ("PRE_TOOL_HOOKS_ENABLED", "pre_tool_hooks_enabled"),
            ("POST_TOOL_HOOKS_ENABLED", "post_tool_hooks_enabled"),
            ("HOOKS_SETTINGS_LOAD_ENABLED", "hooks_settings_load_enabled"),
        ],
    )
    def test_false_is_honoured(self, monkeypatch, env_name, attr):
        monkeypatch.setenv(env_name, "false")
        assert getattr(Settings.load(env_path=Path("/nonexistent.env")), attr) is False


class TestSettingsFileHookLoading:
    """hooks_settings_load_enabled was a decoy: the only working lever was the
    undocumented inverted INTELLIGENCE_ENGINE_DISABLE_HOOK_SETTINGS. Both must
    work now, and either alone must be enough to disable loading."""

    def make(self, db, settings, session):
        return ExecutionThread(
            adapter=ToolThenStopAdapter(),
            emitter=EventEmitter(),
            memory=create_working_memory(session, db=db),
            tools=noop_registry(),
            settings=settings,
        )

    def test_documented_flag_disables_the_loader(self, db, monkeypatch):
        monkeypatch.delenv("INTELLIGENCE_ENGINE_DISABLE_HOOK_SETTINGS", raising=False)
        settings = Settings()
        settings.hooks_settings_load_enabled = False
        th = self.make(db, settings, "sess-hsl-off")
        assert th.hook_settings_manager.enabled is False

    def test_legacy_env_var_still_disables_the_loader(self, db, monkeypatch):
        monkeypatch.setenv("INTELLIGENCE_ENGINE_DISABLE_HOOK_SETTINGS", "1")
        settings = Settings()
        settings.hooks_settings_load_enabled = True
        th = self.make(db, settings, "sess-hsl-legacy")
        assert th.hook_settings_manager.enabled is False

    def test_both_on_leaves_the_loader_enabled(self, db, monkeypatch):
        monkeypatch.delenv("INTELLIGENCE_ENGINE_DISABLE_HOOK_SETTINGS", raising=False)
        settings = Settings()
        settings.hooks_settings_load_enabled = True
        th = self.make(db, settings, "sess-hsl-on")
        assert th.hook_settings_manager.enabled is True
