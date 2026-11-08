"""Tests for src/infra/graceful_shutdown.py — signal handlers, failsafe, ordered teardown."""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import pytest


@pytest.fixture(autouse=True)
def reset_shutdown_state():
    """Reset graceful_shutdown and cleanup_registry state before each test."""
    from src.infra.graceful_shutdown import reset_for_testing
    from src.infra.cleanup_registry import clear_registry

    reset_for_testing()
    clear_registry()
    yield
    reset_for_testing()
    clear_registry()


class TestIsShuttingDown:
    def test_initially_false(self):
        from src.infra.graceful_shutdown import is_shutting_down

        assert not is_shutting_down()

    @pytest.mark.asyncio
    async def test_true_after_shutdown_starts(self):
        from src.infra.graceful_shutdown import is_shutting_down, graceful_shutdown

        # Patch sys.exit so the test process doesn't actually die
        exit_code_seen = []
        original_exit = sys.exit

        def fake_exit(code=0):
            exit_code_seen.append(code)
            raise SystemExit(code)

        sys.exit = fake_exit
        try:
            with pytest.raises(SystemExit):
                await graceful_shutdown(exit_code=0)
            assert is_shutting_down()
        finally:
            sys.exit = original_exit


class TestGracefulShutdownIdempotency:
    @pytest.mark.asyncio
    async def test_second_call_is_noop(self):
        """Once shutdown is in progress, subsequent calls must return immediately."""
        from src.infra.graceful_shutdown import graceful_shutdown

        called = []
        original_exit = sys.exit

        def fake_exit(code=0):
            called.append(code)
            raise SystemExit(code)

        sys.exit = fake_exit
        try:
            # First call triggers shutdown
            with pytest.raises(SystemExit):
                await graceful_shutdown(0)
            assert len(called) == 1

            # Second call must be a no-op — shutting_down is already True
            await graceful_shutdown(0)  # should return without calling sys.exit
            assert len(called) == 1, "sys.exit should not be called a second time"
        finally:
            sys.exit = original_exit


class TestDrainCallbacks:
    @pytest.mark.asyncio
    async def test_drain_callback_is_called(self):
        from src.infra.graceful_shutdown import graceful_shutdown, register_drain_callback

        drained = []

        async def drain():
            drained.append(True)

        register_drain_callback(drain)

        original_exit = sys.exit

        def fake_exit(code=0):
            raise SystemExit(code)

        sys.exit = fake_exit
        try:
            with pytest.raises(SystemExit):
                await graceful_shutdown(0)
            assert drained == [True]
        finally:
            sys.exit = original_exit

    @pytest.mark.asyncio
    async def test_drain_callback_timeout_does_not_block_shutdown(self):
        from src.infra.graceful_shutdown import graceful_shutdown, register_drain_callback

        async def slow_drain():
            await asyncio.sleep(60)

        register_drain_callback(slow_drain)

        # Patch wait_for timeout to be very short for this test
        original_exit = sys.exit

        def fake_exit(code=0):
            raise SystemExit(code)

        sys.exit = fake_exit
        try:
            # Even with a slow drain callback, shutdown should complete
            # (drain has its own 30s timeout; here we just verify no hang)
            with pytest.raises(SystemExit):
                # Reduce the internal timeout to avoid slow test
                original_wait_for = asyncio.wait_for

                async def fast_wait_for(coro, timeout):
                    return await original_wait_for(coro, min(timeout, 0.05))

                asyncio.wait_for = fast_wait_for  # type: ignore[assignment]
                try:
                    await graceful_shutdown(0)
                finally:
                    asyncio.wait_for = original_wait_for  # type: ignore[assignment]
        finally:
            sys.exit = original_exit


class TestCleanupRegistryIntegration:
    @pytest.mark.asyncio
    async def test_cleanup_registry_runs_during_shutdown(self):
        from src.infra.graceful_shutdown import graceful_shutdown
        from src.infra.cleanup_registry import register_cleanup

        executed = []

        async def subsystem_cleanup():
            executed.append("subsystem")

        register_cleanup("subsystem", subsystem_cleanup)

        original_exit = sys.exit

        def fake_exit(code=0):
            raise SystemExit(code)

        sys.exit = fake_exit
        try:
            with pytest.raises(SystemExit):
                await graceful_shutdown(0)
            assert executed == ["subsystem"]
        finally:
            sys.exit = original_exit

    @pytest.mark.asyncio
    async def test_lifo_order_preserved_end_to_end(self):
        from src.infra.graceful_shutdown import graceful_shutdown
        from src.infra.cleanup_registry import register_cleanup

        order = []

        async def sqlite_close():
            order.append("sqlite")

        async def coordinator_stop():
            order.append("coordinator")

        async def scheduler_stop():
            order.append("scheduler")

        # Simulate boot order: sqlite first, then coordinator, then scheduler
        register_cleanup("sqlite", sqlite_close)
        register_cleanup("coordinator", coordinator_stop)
        register_cleanup("scheduler", scheduler_stop)

        original_exit = sys.exit

        def fake_exit(code=0):
            raise SystemExit(code)

        sys.exit = fake_exit
        try:
            with pytest.raises(SystemExit):
                await graceful_shutdown(0)
            # LIFO: scheduler → coordinator → sqlite
            assert order == ["scheduler", "coordinator", "sqlite"]
        finally:
            sys.exit = original_exit


class TestSignalHandlers:
    def test_setup_signal_handlers_is_idempotent(self):
        from src.infra.graceful_shutdown import setup_signal_handlers

        # Calling twice should not raise
        setup_signal_handlers(register_sigint=False)
        setup_signal_handlers(register_sigint=False)

    def test_sigterm_handler_installed(self):
        from src.infra.graceful_shutdown import setup_signal_handlers

        setup_signal_handlers(register_sigint=False)
        handler = signal.getsignal(signal.SIGTERM)
        assert handler not in (signal.SIG_DFL, signal.SIG_IGN, None)

    def test_sighup_handler_installed(self):
        from src.infra.graceful_shutdown import setup_signal_handlers

        if not hasattr(signal, "SIGHUP"):
            pytest.skip("SIGHUP not available on this platform")
        setup_signal_handlers(register_sigint=False)
        handler = signal.getsignal(signal.SIGHUP)
        assert handler not in (signal.SIG_DFL, signal.SIG_IGN, None)

    def test_sigint_handler_skipped_when_disabled(self):
        """When register_sigint=False, SIGINT handler should be unchanged."""
        from src.infra.graceful_shutdown import setup_signal_handlers

        original = signal.getsignal(signal.SIGINT)
        try:
            setup_signal_handlers(register_sigint=False)
            current = signal.getsignal(signal.SIGINT)
            assert current == original
        finally:
            signal.signal(signal.SIGINT, original)


class TestFailsafeArming:
    @pytest.mark.asyncio
    async def test_failsafe_fires_if_cleanup_hangs(self):
        """Failsafe should force-exit if shutdown takes too long."""
        from src.infra import graceful_shutdown as gs_mod

        force_exit_calls = []
        original_exit = os._exit

        def fake_os_exit(code):
            force_exit_calls.append(code)

        os._exit = fake_os_exit  # type: ignore[assignment]
        try:
            # Arm a very short failsafe (0.1s)
            gs_mod.arm_failsafe(99, 0.1)
            # Wait for it to fire
            await asyncio.sleep(0.3)
            assert len(force_exit_calls) == 1
            assert force_exit_calls[0] == 99
        finally:
            gs_mod.cancel_failsafe()
            os._exit = original_exit  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_failsafe_cancelled_before_firing(self):
        """If we cancel the failsafe, it must not force-exit."""
        from src.infra import graceful_shutdown as gs_mod

        force_exit_calls = []
        original_exit = os._exit

        def fake_os_exit(code):
            force_exit_calls.append(code)

        os._exit = fake_os_exit  # type: ignore[assignment]
        try:
            gs_mod.arm_failsafe(0, 0.2)
            gs_mod.cancel_failsafe()
            await asyncio.sleep(0.4)
            assert force_exit_calls == []
        finally:
            os._exit = original_exit  # type: ignore[assignment]


class TestExceptionHandlers:
    """Observability hooks for uncaught sync + asyncio exceptions."""

    @pytest.fixture(autouse=True)
    def installed(self):
        from src.infra.graceful_shutdown import (
            install_exception_handlers,
            reset_for_testing,
        )

        install_exception_handlers()
        yield
        reset_for_testing()

    def test_sys_excepthook_logs_non_keyboard_interrupt(self, caplog):
        import logging

        with caplog.at_level(logging.ERROR, logger="src.infra.graceful_shutdown"):
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                sys.excepthook(*sys.exc_info())
        messages = [r.getMessage() for r in caplog.records]
        assert any("uncaught_exception" in m and "RuntimeError" in m for m in messages)

    def test_sys_excepthook_forwards_keyboard_interrupt_to_prior(self, monkeypatch):
        from src.infra.graceful_shutdown import install_exception_handlers, reset_for_testing

        # Reset first so we can stage a fake prior hook before installing.
        reset_for_testing()
        prior_called = []
        monkeypatch.setattr(sys, "excepthook", lambda *a: prior_called.append(a))
        install_exception_handlers()

        try:
            raise KeyboardInterrupt()
        except KeyboardInterrupt:
            sys.excepthook(*sys.exc_info())
        assert len(prior_called) == 1
        assert prior_called[0][0] is KeyboardInterrupt

    @pytest.mark.asyncio
    async def test_asyncio_exception_handler_logs_unhandled(self, caplog):
        import logging
        from src.infra.graceful_shutdown import install_exception_handlers

        # Re-invoke under the running loop so the asyncio handler attaches.
        install_exception_handlers()
        loop = asyncio.get_running_loop()
        with caplog.at_level(logging.ERROR, logger="src.infra.graceful_shutdown"):
            loop.call_exception_handler(
                {
                    "exception": ValueError("oops"),
                    "message": "unhandled",
                }
            )
        messages = [r.getMessage() for r in caplog.records]
        assert any("unhandled_rejection" in m and "ValueError" in m for m in messages)

    def test_install_is_idempotent(self):
        from src.infra.graceful_shutdown import install_exception_handlers

        first = sys.excepthook
        install_exception_handlers()
        second = sys.excepthook
        assert first is second, "second install must not replace the first closure"

    def test_reset_restores_prior_excepthook(self):
        from src.infra.graceful_shutdown import install_exception_handlers, reset_for_testing

        reset_for_testing()  # clear autouse install
        original = sys.excepthook
        install_exception_handlers()
        assert sys.excepthook is not original
        reset_for_testing()
        assert sys.excepthook is original
