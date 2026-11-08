"""Tests for src/infra/cleanup_registry.py — LIFO-ordered subsystem teardown."""

from __future__ import annotations

import asyncio

import pytest


class TestRegisterAndRun:
    @pytest.mark.asyncio
    async def test_single_cleanup_runs(self):
        from src.infra.cleanup_registry import register_cleanup, run_cleanups, clear_registry

        called = []

        async def cb():
            called.append("cb")

        try:
            register_cleanup("cb", cb)
            await run_cleanups()
            assert called == ["cb"]
        finally:
            clear_registry()

    @pytest.mark.asyncio
    async def test_multiple_cleanups_run_in_lifo_order(self):
        """Last registered must run first (LIFO = reverse boot order)."""
        from src.infra.cleanup_registry import register_cleanup, run_cleanups, clear_registry

        order = []

        async def first():
            order.append("first")

        async def second():
            order.append("second")

        async def third():
            order.append("third")

        try:
            register_cleanup("first", first)
            register_cleanup("second", second)
            register_cleanup("third", third)
            await run_cleanups()
            assert order == ["third", "second", "first"]
        finally:
            clear_registry()

    @pytest.mark.asyncio
    async def test_empty_registry_is_noop(self):
        from src.infra.cleanup_registry import run_cleanups, clear_registry

        try:
            await run_cleanups()
        finally:
            clear_registry()


class TestUnregister:
    @pytest.mark.asyncio
    async def test_unregister_prevents_execution(self):
        from src.infra.cleanup_registry import register_cleanup, run_cleanups, clear_registry

        called = []

        async def cb():
            called.append("cb")

        try:
            unreg = register_cleanup("cb", cb)
            unreg()
            await run_cleanups()
            assert called == []
        finally:
            clear_registry()

    @pytest.mark.asyncio
    async def test_unregister_is_idempotent(self):
        from src.infra.cleanup_registry import register_cleanup, clear_registry

        async def cb():
            pass

        try:
            unreg = register_cleanup("cb", cb)
            unreg()
            unreg()
        finally:
            clear_registry()

    @pytest.mark.asyncio
    async def test_partial_unregister_runs_remaining(self):
        from src.infra.cleanup_registry import register_cleanup, run_cleanups, clear_registry

        called = []

        async def a():
            called.append("a")

        async def b():
            called.append("b")

        try:
            register_cleanup("a", a)
            unreg_b = register_cleanup("b", b)
            unreg_b()
            await run_cleanups()
            assert called == ["a"]
        finally:
            clear_registry()


class TestTimeouts:
    @pytest.mark.asyncio
    async def test_slow_cleanup_is_skipped_after_timeout(self):
        from src.infra.cleanup_registry import register_cleanup, run_cleanups, clear_registry

        async def slow():
            await asyncio.sleep(10)

        try:
            register_cleanup("slow", slow)
            await run_cleanups(timeout_per_fn=0.1)
        finally:
            clear_registry()

    @pytest.mark.asyncio
    async def test_timeout_does_not_prevent_subsequent_cleanups(self):
        """A hung cleanup must not block later ones from running."""
        from src.infra.cleanup_registry import register_cleanup, run_cleanups, clear_registry

        called = []

        async def slow():
            await asyncio.sleep(10)

        async def fast():
            called.append("fast")

        try:
            register_cleanup("fast", fast)
            register_cleanup("slow", slow)
            await run_cleanups(timeout_per_fn=0.1)
            assert called == ["fast"]
        finally:
            clear_registry()

    @pytest.mark.asyncio
    async def test_failing_cleanup_does_not_prevent_subsequent_cleanups(self):
        """An exception in one cleanup must not block others."""
        from src.infra.cleanup_registry import register_cleanup, run_cleanups, clear_registry

        called = []

        async def boom():
            raise RuntimeError("boom")

        async def fine():
            called.append("fine")

        try:
            register_cleanup("fine", fine)
            register_cleanup("boom", boom)
            await run_cleanups()
            assert called == ["fine"]
        finally:
            clear_registry()


class TestClearRegistry:
    @pytest.mark.asyncio
    async def test_clear_registry_removes_all_entries(self):
        from src.infra.cleanup_registry import register_cleanup, run_cleanups, clear_registry

        called = []

        async def cb():
            called.append("cb")

        register_cleanup("cb", cb)
        clear_registry()
        await run_cleanups()
        assert called == []
