"""Tests for partition_tool_calls — concurrency/serial batch partitioning.

Mirrors the reference partitionToolCalls() in
services/tools/toolOrchestration.ts:91-116.

Rules under test:
 - Consecutive concurrency-safe calls merge into one batch.
 - Each non-safe call gets its own batch.
 - Unknown tool name → non-safe (conservative).
 - Exception in is_concurrency_safe → non-safe (conservative).
 - is_concurrency_safe receives the actual tool input (per-call).
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.herald.types import ToolCall
from src.utility_directory.tool_runtime import (
    ToolBatch,
    ToolDef,
    ToolRegistry,
    partition_tool_calls,
)


async def noop_handler(_: dict) -> str:
    return ""


def make_registry(**tools: ToolDef) -> ToolRegistry:
    reg = ToolRegistry()
    for td in tools.values():
        reg.register(td)
    return reg


def make_tool(
    name: str,
    concurrency_safe: bool | None = None,
    concurrency_safe_fn: object = None,
) -> ToolDef:
    """Build a ToolDef with configurable is_concurrency_safe."""
    if concurrency_safe_fn is not None:
        safe_fn = concurrency_safe_fn
    elif concurrency_safe is not None:
        safe_fn = lambda _: concurrency_safe  # noqa: E731
    else:
        safe_fn = lambda _: False  # noqa: E731
    return ToolDef(
        name=name,
        description="",
        input_schema={},
        handler=noop_handler,
        is_concurrency_safe=safe_fn,
    )


def make_tc(id: str, name: str = "tool", **input_kw: object) -> ToolCall:
    return ToolCall(id=id, name=name, input=dict(input_kw))


def test_empty_returns_empty_list():
    reg = make_registry()
    assert partition_tool_calls([], reg) == []


def test_single_safe_call_is_one_batch():
    reg = make_registry(t=make_tool("t", concurrency_safe=True))
    batches = partition_tool_calls([make_tc("1", "t")], reg)
    assert len(batches) == 1
    assert batches[0].is_concurrency_safe is True
    assert [c.id for c in batches[0].calls] == ["1"]


def test_single_unsafe_call_is_one_batch():
    reg = make_registry(t=make_tool("t", concurrency_safe=False))
    batches = partition_tool_calls([make_tc("1", "t")], reg)
    assert len(batches) == 1
    assert batches[0].is_concurrency_safe is False
    assert [c.id for c in batches[0].calls] == ["1"]


def test_all_safe_merges_into_one_batch():
    reg = make_registry(t=make_tool("t", concurrency_safe=True))
    calls = [make_tc("1", "t"), make_tc("2", "t"), make_tc("3", "t")]
    batches = partition_tool_calls(calls, reg)
    assert len(batches) == 1
    assert batches[0].is_concurrency_safe is True
    assert [c.id for c in batches[0].calls] == ["1", "2", "3"]


def test_all_unsafe_each_in_own_batch():
    reg = make_registry(t=make_tool("t", concurrency_safe=False))
    calls = [make_tc("1", "t"), make_tc("2", "t"), make_tc("3", "t")]
    batches = partition_tool_calls(calls, reg)
    assert len(batches) == 3
    for i, batch in enumerate(batches):
        assert batch.is_concurrency_safe is False
        assert len(batch.calls) == 1
        assert batch.calls[0].id == str(i + 1)


def test_safe_then_unsafe_produces_two_batches():
    reg = make_registry(
        safe=make_tool("safe", concurrency_safe=True),
        unsafe=make_tool("unsafe", concurrency_safe=False),
    )
    calls = [make_tc("1", "safe"), make_tc("2", "safe"), make_tc("3", "unsafe")]
    batches = partition_tool_calls(calls, reg)
    assert len(batches) == 2
    assert batches[0].is_concurrency_safe is True
    assert [c.id for c in batches[0].calls] == ["1", "2"]
    assert batches[1].is_concurrency_safe is False
    assert [c.id for c in batches[1].calls] == ["3"]


def test_unsafe_then_safe_produces_two_batches():
    reg = make_registry(
        safe=make_tool("safe", concurrency_safe=True),
        unsafe=make_tool("unsafe", concurrency_safe=False),
    )
    calls = [make_tc("1", "unsafe"), make_tc("2", "safe"), make_tc("3", "safe")]
    batches = partition_tool_calls(calls, reg)
    assert len(batches) == 2
    assert batches[0].is_concurrency_safe is False
    assert [c.id for c in batches[0].calls] == ["1"]
    assert batches[1].is_concurrency_safe is True
    assert [c.id for c in batches[1].calls] == ["2", "3"]


def test_interleaved_safe_unsafe_produces_three_batches():
    """safe, unsafe, safe → 3 batches (safe groups don't merge across unsafe)."""
    reg = make_registry(
        safe=make_tool("safe", concurrency_safe=True),
        unsafe=make_tool("unsafe", concurrency_safe=False),
    )
    calls = [make_tc("1", "safe"), make_tc("2", "unsafe"), make_tc("3", "safe")]
    batches = partition_tool_calls(calls, reg)
    assert len(batches) == 3
    assert batches[0].is_concurrency_safe is True
    assert [c.id for c in batches[0].calls] == ["1"]
    assert batches[1].is_concurrency_safe is False
    assert [c.id for c in batches[1].calls] == ["2"]
    assert batches[2].is_concurrency_safe is True
    assert [c.id for c in batches[2].calls] == ["3"]


def test_complex_pattern_safe_safe_unsafe_safe_unsafe_unsafe():
    """Verify batch boundaries for a realistic multi-tool sequence."""
    reg = make_registry(
        r=make_tool("r", concurrency_safe=True),
        w=make_tool("w", concurrency_safe=False),
    )
    calls = [
        make_tc("1", "r"),
        make_tc("2", "r"),
        make_tc("3", "w"),
        make_tc("4", "r"),
        make_tc("5", "w"),
        make_tc("6", "w"),
    ]
    batches = partition_tool_calls(calls, reg)
    assert len(batches) == 5
    assert batches[0] == ToolBatch(is_concurrency_safe=True, calls=[calls[0], calls[1]])
    assert batches[1] == ToolBatch(is_concurrency_safe=False, calls=[calls[2]])
    assert batches[2] == ToolBatch(is_concurrency_safe=True, calls=[calls[3]])
    assert batches[3] == ToolBatch(is_concurrency_safe=False, calls=[calls[4]])
    assert batches[4] == ToolBatch(is_concurrency_safe=False, calls=[calls[5]])


def test_unknown_tool_name_treated_as_unsafe():
    """A tool name not in the registry must produce a non-safe batch."""
    reg = make_registry()
    batches = partition_tool_calls([make_tc("1", "nonexistent")], reg)
    assert len(batches) == 1
    assert batches[0].is_concurrency_safe is False


def test_unknown_tool_does_not_merge_with_adjacent_safe():
    """Unknown tool breaks a concurrent run — it's conservative."""
    reg = make_registry(t=make_tool("t", concurrency_safe=True))
    calls = [make_tc("1", "t"), make_tc("2", "unknown"), make_tc("3", "t")]
    batches = partition_tool_calls(calls, reg)
    assert len(batches) == 3
    assert batches[0].is_concurrency_safe is True
    assert batches[1].is_concurrency_safe is False
    assert batches[2].is_concurrency_safe is True


def test_exception_in_is_concurrency_safe_treated_as_unsafe():
    """Matches the reference: exceptions default to not safe (conservative)."""

    def boom(_: dict) -> bool:
        raise RuntimeError("parse failure")

    reg = make_registry(t=make_tool("t", concurrency_safe_fn=boom))
    batches = partition_tool_calls([make_tc("1", "t")], reg)
    assert len(batches) == 1
    assert batches[0].is_concurrency_safe is False


def test_exception_in_is_concurrency_safe_does_not_propagate():
    """Exception must be swallowed — executor must always get a result."""

    def boom(_: dict) -> bool:
        raise ValueError("bad")

    reg = make_registry(t=make_tool("t", concurrency_safe_fn=boom))
    result = partition_tool_calls([make_tc("1", "t")], reg)
    assert result[0].is_concurrency_safe is False


def test_is_concurrency_safe_receives_correct_input():
    """The callable must be passed the tool call's input dict, not empty."""
    received: list[dict] = []

    def capture(inp: dict) -> bool:
        received.append(inp)
        return True

    reg = make_registry(t=make_tool("t", concurrency_safe_fn=capture))
    calls = [
        make_tc("1", "t", file_path="/a"),
        make_tc("2", "t", file_path="/b"),
    ]
    partition_tool_calls(calls, reg)
    assert received == [{"file_path": "/a"}, {"file_path": "/b"}]


def test_is_concurrency_safe_per_call_variance():
    """A tool can return different safety for different inputs — e.g. read vs write path."""

    def safe_if_read(inp: dict) -> bool:
        return inp.get("mode") == "read"

    reg = make_registry(t=make_tool("t", concurrency_safe_fn=safe_if_read))
    calls = [
        make_tc("1", "t", mode="read"),
        make_tc("2", "t", mode="write"),
        make_tc("3", "t", mode="read"),
    ]
    batches = partition_tool_calls(calls, reg)
    assert len(batches) == 3
    assert batches[0].is_concurrency_safe is True
    assert batches[1].is_concurrency_safe is False
    assert batches[2].is_concurrency_safe is True


def test_tool_batch_is_dataclass_with_expected_fields():
    tc = make_tc("x", "t")
    batch = ToolBatch(is_concurrency_safe=True, calls=[tc])
    assert batch.is_concurrency_safe is True
    assert batch.calls == [tc]


def test_tool_batch_equality():
    tc = make_tc("x", "t")
    a = ToolBatch(is_concurrency_safe=True, calls=[tc])
    b = ToolBatch(is_concurrency_safe=True, calls=[tc])
    assert a == b


def test_missing_required_field_treated_as_unsafe():
    """When a required input field is missing, tool is treated as unsafe
    even if is_concurrency_safe would return True."""
    reg = make_registry(
        t=ToolDef(
            name="t",
            description="",
            input_schema={
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
            handler=noop_handler,
            is_concurrency_safe=lambda _: True,
        )
    )
    batches = partition_tool_calls([make_tc("1", "t")], reg)
    assert len(batches) == 1
    assert batches[0].is_concurrency_safe is False


def test_valid_input_still_checks_concurrency_safe():
    """Required fields present → is_concurrency_safe is called normally."""
    reg = make_registry(
        t=ToolDef(
            name="t",
            description="",
            input_schema={
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
            handler=noop_handler,
            is_concurrency_safe=lambda _: True,
        )
    )
    batches = partition_tool_calls([make_tc("1", "t", file_path="/a")], reg)
    assert len(batches) == 1
    assert batches[0].is_concurrency_safe is True


def test_no_required_field_in_schema_always_passes_validation():
    """When schema has no required array, validation always passes."""
    reg = make_registry(
        t=ToolDef(
            name="t",
            description="",
            input_schema={"type": "object", "properties": {}},
            handler=noop_handler,
            is_concurrency_safe=lambda _: True,
        )
    )
    batches = partition_tool_calls([make_tc("1", "t")], reg)
    assert batches[0].is_concurrency_safe is True


def test_is_destructive_defaults_to_false():
    tool = make_tool("t")
    assert tool.is_destructive({}) is False


def test_is_destructive_custom():
    tool = ToolDef(
        name="t",
        description="",
        input_schema={},
        handler=noop_handler,
        is_destructive=lambda inp: inp.get("force", False),
    )
    assert tool.is_destructive({"force": True}) is True
    assert tool.is_destructive({"force": False}) is False


def test_context_modifier_fn_defaults_to_none():
    tool = make_tool("t")
    assert tool.context_modifier_fn is None


def test_context_modifier_fn_callable():
    def modifier(inp, result):
        return {"cwd": inp.get("path", "/")}

    tool = ToolDef(
        name="t",
        description="",
        input_schema={},
        handler=noop_handler,
        context_modifier_fn=modifier,
    )
    assert tool.context_modifier_fn({"path": "/tmp"}, "ok") == {"cwd": "/tmp"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
