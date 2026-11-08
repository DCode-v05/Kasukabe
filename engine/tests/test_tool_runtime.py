"""Tests for ToolDef.get_interrupt_behavior() runtime query."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.engine_core.cancellation import InterruptBehavior
from src.utility_directory.tool_runtime import ToolDef


async def noop_handler(input: dict) -> str:
    return ""


def make_tool(**overrides) -> ToolDef:
    base = dict(
        name="t",
        description="d",
        input_schema={},
        handler=noop_handler,
    )
    base.update(overrides)
    return ToolDef(**base)


def test_default_interrupt_behavior_is_block():
    tool = make_tool()
    assert tool.get_interrupt_behavior() == InterruptBehavior.BLOCK


def test_static_cancel_returned_when_fn_none():
    tool = make_tool(interrupt_behavior=InterruptBehavior.CANCEL)
    assert tool.interrupt_behavior_fn is None
    assert tool.get_interrupt_behavior() == InterruptBehavior.CANCEL


def test_runtime_fn_returns_cancel_overrides_static_block():
    tool = make_tool(
        interrupt_behavior=InterruptBehavior.BLOCK,
        interrupt_behavior_fn=lambda input: InterruptBehavior.CANCEL,
    )
    assert tool.get_interrupt_behavior({}) == InterruptBehavior.CANCEL


def test_runtime_fn_returns_block_overrides_static_cancel():
    tool = make_tool(
        interrupt_behavior=InterruptBehavior.CANCEL,
        interrupt_behavior_fn=lambda input: InterruptBehavior.BLOCK,
    )
    assert tool.get_interrupt_behavior({}) == InterruptBehavior.BLOCK


def test_runtime_fn_called_every_time():
    """Result is not cached — each call re-invokes the fn."""
    calls: list[dict] = []
    sequence = [InterruptBehavior.CANCEL, InterruptBehavior.BLOCK, InterruptBehavior.CANCEL]

    def fn(tool_input: dict) -> InterruptBehavior:
        calls.append(tool_input)
        return sequence[len(calls) - 1]

    tool = make_tool(interrupt_behavior_fn=fn)
    assert tool.get_interrupt_behavior({"a": 1}) == InterruptBehavior.CANCEL
    assert tool.get_interrupt_behavior({"a": 2}) == InterruptBehavior.BLOCK
    assert tool.get_interrupt_behavior({"a": 3}) == InterruptBehavior.CANCEL
    assert calls == [{"a": 1}, {"a": 2}, {"a": 3}]


def test_runtime_fn_receives_tool_input_and_can_vary_per_call():
    """The runtime fn can pick CANCEL vs BLOCK based on input — this is
    the whole reason the runtime path exists on top of the static field."""

    def choose(tool_input: dict) -> InterruptBehavior:
        return InterruptBehavior.CANCEL if "timeout" in tool_input else InterruptBehavior.BLOCK

    tool = make_tool(name="bash", interrupt_behavior_fn=choose)
    assert tool.get_interrupt_behavior({"command": "ls"}) == InterruptBehavior.BLOCK
    assert tool.get_interrupt_behavior({"command": "ls", "timeout": 5}) == InterruptBehavior.CANCEL


def test_runtime_fn_with_none_input_passes_empty_dict():
    """Callers that don't supply an input (e.g. static query paths) should
    still work — the fn receives an empty dict, not None."""
    received: list[dict] = []

    def fn(tool_input: dict) -> InterruptBehavior:
        received.append(tool_input)
        return InterruptBehavior.CANCEL

    tool = make_tool(interrupt_behavior_fn=fn)
    assert tool.get_interrupt_behavior() == InterruptBehavior.CANCEL
    assert received == [{}]


def test_runtime_fn_exception_falls_back_to_block():
    def boom(input: dict) -> InterruptBehavior:
        raise RuntimeError("broken tool declaration")

    tool = make_tool(
        interrupt_behavior=InterruptBehavior.CANCEL,
        interrupt_behavior_fn=boom,
    )
    assert tool.get_interrupt_behavior({}) == InterruptBehavior.BLOCK


def test_runtime_fn_exception_is_caught_not_raised():
    def boom(input: dict) -> InterruptBehavior:
        raise ValueError("nope")

    tool = make_tool(interrupt_behavior_fn=boom)
    result = tool.get_interrupt_behavior({})
    assert result == InterruptBehavior.BLOCK


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
