"""Tests for InterruptSource — central user-interrupt dispatcher."""

import asyncio
import sys
import os
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.engine_core.cancellation import CancellationToken, InterruptBehavior
from src.engine_core.interrupt_source import (
    InterruptSource,
    reset_for_tests,
    get_interrupt_source,
)
from src.engine_core.streaming_tool_executor import StreamingToolExecutor
from src.herald.types import ToolCall, ToolResultBlock


@pytest.fixture(autouse=True)
def fresh_source():
    reset_for_tests()
    yield
    reset_for_tests()


def make_call(id: str, name: str = "t") -> ToolCall:
    return ToolCall(id=id, name=name, input={})


async def slow_execute(call: ToolCall, ct: CancellationToken) -> ToolResultBlock:
    for _ in range(50):
        if ct.cancelled:
            return ToolResultBlock(tool_use_id=call.id, content="cancelled", is_error=True)
        await asyncio.sleep(0.01)
    return ToolResultBlock(tool_use_id=call.id, content="done")


def make_executor(
    interrupt_behavior: InterruptBehavior = InterruptBehavior.CANCEL,
) -> StreamingToolExecutor:
    return StreamingToolExecutor(
        execute_fn=slow_execute,
        concurrency_check_fn=lambda _: True,
        interrupt_behavior_fn=lambda _: interrupt_behavior,
    )


def test_register_and_unregister():
    src = InterruptSource()
    ex = make_executor()
    assert src.active_count == 0
    src.register(ex)
    assert src.active_count == 1
    src.unregister(ex)
    assert src.active_count == 0


def test_get_interrupt_source_singleton():
    a = get_interrupt_source()
    b = get_interrupt_source()
    assert a is b


def test_reset_for_tests_drops_singleton():
    a = get_interrupt_source()
    reset_for_tests()
    b = get_interrupt_source()
    assert a is not b


@pytest.mark.asyncio
async def test_trigger_cancels_all_registered_cancel_tools():
    src = InterruptSource()
    ex1 = make_executor(InterruptBehavior.CANCEL)
    ex2 = make_executor(InterruptBehavior.CANCEL)
    src.register(ex1)
    src.register(ex2)

    ex1.add_tool(make_call("a1"))
    ex2.add_tool(make_call("b1"))
    await asyncio.sleep(0.02)

    count = src.trigger("test")
    assert count == 2

    results_1 = [r async for r in ex1.results()]
    results_2 = [r async for r in ex2.results()]
    assert all(r.is_error for r in results_1 + results_2)


@pytest.mark.asyncio
async def test_trigger_leaves_block_tools_alone():
    src = InterruptSource()
    ex = make_executor(InterruptBehavior.BLOCK)
    src.register(ex)
    ex.add_tool(make_call("b1"))
    await asyncio.sleep(0.02)

    src.trigger("test")

    results = [r async for r in ex.results()]
    assert len(results) == 1
    assert results[0].content == "done"


def test_trigger_returns_count_of_zero_when_empty():
    src = InterruptSource()
    assert src.trigger("empty") == 0


@pytest.mark.asyncio
async def test_trigger_isolates_executor_errors():
    """One broken executor must not block cancellation of its siblings."""
    src = InterruptSource()

    class BadExecutor:
        def cancel_interruptible(self, *a, **kw):
            raise RuntimeError("broken")

    good = make_executor()
    bad = BadExecutor()

    src.register(good)  # type: ignore[arg-type]
    src.register(bad)  # type: ignore[arg-type]

    good.add_tool(make_call("t1"))
    await asyncio.sleep(0.02)

    count = src.trigger("isolated")
    assert count == 1


@pytest.mark.asyncio
async def test_install_is_idempotent():
    src = InterruptSource()
    first = src.install()
    second = src.install()
    assert first is True
    assert second is True
    assert src.installed is True
    src.uninstall()
    assert src.installed is False


def test_install_noop_when_no_running_loop():
    """Outside any running loop, install must return False and not raise."""
    src = InterruptSource()
    ok = src.install()
    assert ok is False
    assert src.installed is False


def test_install_noop_in_worker_thread():
    """asyncio.add_signal_handler only works on the main thread."""
    src = InterruptSource()
    result = {}

    def worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def run():
            result["ok"] = src.install()

        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert "ok" in result
    assert isinstance(result["ok"], bool)


@pytest.mark.asyncio
async def test_end_to_end_trigger_then_drain():
    src = InterruptSource()
    ex = make_executor(InterruptBehavior.CANCEL)
    src.register(ex)
    ex.add_tool(make_call("t1", "c"))
    ex.add_tool(make_call("t2", "c"))
    await asyncio.sleep(0.02)
    cancelled = src.trigger("e2e")
    assert cancelled == 1
    results = [r async for r in ex.results()]
    assert len(results) == 2
    assert all(r.is_error for r in results)
    src.unregister(ex)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
