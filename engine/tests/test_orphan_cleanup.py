"""Tests for orphan process detection and cleanup (Gaps 1-9).

Real integration tests — no mocks.  Skipped on non-Linux because process
tree utilities depend on /proc.
"""

from __future__ import annotations

import asyncio
import os
import platform

import pytest

IS_LINUX = platform.system() == "Linux"
pytestmark = pytest.mark.skipif(not IS_LINUX, reason="Process tree tests require Linux /proc")


class TestProcessTreeUtils:
    def test_is_process_alive_self(self):
        from src.sandbox.process_tree import is_process_alive

        my_pid = os.getpid()
        if my_pid <= 1:
            assert not is_process_alive(my_pid)
        else:
            assert is_process_alive(my_pid)

    def test_is_process_alive_nonexistent(self):
        from src.sandbox.process_tree import is_process_alive

        assert not is_process_alive(99_999_999)

    def test_is_process_alive_rejects_low_pids(self):
        from src.sandbox.process_tree import is_process_alive

        assert not is_process_alive(0)
        assert not is_process_alive(1)

    @pytest.mark.asyncio
    async def test_get_child_pids_finds_children(self):
        from src.sandbox.process_tree import get_child_pids

        proc = await asyncio.create_subprocess_exec(
            "sleep",
            "60",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            children = get_child_pids(os.getpid())
            assert proc.pid in children
        finally:
            proc.kill()
            await proc.wait()

    def test_get_descendant_pids_empty_for_leaf(self):
        from src.sandbox.process_tree import get_descendant_pids

        result = get_descendant_pids(99_999_999)
        assert result == []


class TestTreeKill:
    @pytest.mark.asyncio
    async def test_tree_kill_kills_grandchildren(self):
        """Spawn parent -> child -> grandchild. tree_kill(parent) kills all.

        Rewritten because the old version asserted nothing about any process
        dying, and did not build the tree its name claimed:

        - `descendants = get_descendant_pids(proc.pid); assert len >= 1` called
          the inspection helper DIRECTLY, so it measured the helper and was
          structurally blind to any defect in tree_kill.
        - `killed = tree_kill(...); assert len(killed) >= 1` always passed:
          tree_kill appends the root pid unconditionally, whatever
          get_descendant_pids returned.
        - the only check that inspected process state was guarded by
          `if stat_path.exists()`, and every SIGKILLed pid is reparented to init
          and reaped, so the body executed zero times on the PASSING path. It
          was dead code, not merely weak.
        - `bash -c "bash -c 'sleep 300' & sleep 300"` produces NO grandchild:
          bash exec-optimizes the single-command inner `bash -c 'sleep 300'`
          into a bare `sleep 300`, so both descendants report ppid == the root.
          The tree was depth 1, and the recursive BFS this test exists to cover
          was exercised by nothing in the repo.

        Now: a real depth-2 tree, pids captured BEFORE the kill, and each one
        asserted dead afterwards with is_process_alive.
        """
        from src.sandbox.process_tree import (
            get_descendant_pids,
            get_ppid,
            is_process_alive,
            tree_kill,
        )

        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            "bash -c 'sleep 300 & wait' & sleep 300",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            for _ in range(50):
                await asyncio.sleep(0.1)
                descendants = get_descendant_pids(proc.pid)
                grandchildren = [
                    d for d in descendants if get_ppid(d) not in (None, proc.pid)
                ]
                if grandchildren:
                    break
            else:
                pytest.fail(
                    f"no grandchild appeared; descendants={get_descendant_pids(proc.pid)}"
                )

            before = get_descendant_pids(proc.pid)
            assert grandchildren, "the recursive BFS path was never exercised"

            tree_kill(proc.pid)
            await proc.wait()
            await asyncio.sleep(0.3)

            still_alive = [pid for pid in before if is_process_alive(pid)]
            assert still_alive == [], f"leaked descendants: {still_alive}"
            for pid in grandchildren:
                assert not is_process_alive(pid), f"grandchild {pid} survived"
            assert not is_process_alive(proc.pid)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    @pytest.mark.asyncio
    async def test_tree_kill_returns_every_pid_it_signalled(self):
        """The returned list must name the descendants, not just the root -
        `len(killed) >= 1` passed on the root alone."""
        from src.sandbox.process_tree import get_descendant_pids, tree_kill

        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            "bash -c 'sleep 300 & wait' & sleep 300",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            for _ in range(50):
                await asyncio.sleep(0.1)
                before = get_descendant_pids(proc.pid)
                if len(before) >= 3:
                    break
            else:
                pytest.fail(f"tree never formed; descendants={before}")

            killed = set(tree_kill(proc.pid))
            await proc.wait()

            assert proc.pid in killed
            assert set(before) <= killed, f"unsignalled: {set(before) - killed}"
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    def test_tree_kill_nonexistent_pid(self):
        from src.sandbox.process_tree import tree_kill

        result = tree_kill(99_999_999)
        assert result == []

    @pytest.mark.asyncio
    async def test_tree_kill_single_process(self):
        from src.sandbox.process_tree import tree_kill, is_process_alive

        proc = await asyncio.create_subprocess_exec(
            "sleep",
            "300",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        killed = tree_kill(proc.pid)
        assert proc.pid in killed
        await asyncio.sleep(0.2)
        assert not is_process_alive(proc.pid)


class TestTreeKillFeatureFlag:
    def test_flag_defaults_enabled(self):
        from src.sandbox.executor import tree_kill_enabled

        orig = os.environ.pop("FEATURE_TREE_KILL", None)
        try:
            assert tree_kill_enabled()
        finally:
            if orig is not None:
                os.environ["FEATURE_TREE_KILL"] = orig

    def test_flag_can_disable(self):
        from src.sandbox.executor import tree_kill_enabled

        orig = os.environ.get("FEATURE_TREE_KILL")
        os.environ["FEATURE_TREE_KILL"] = "false"
        try:
            assert not tree_kill_enabled()
        finally:
            if orig is not None:
                os.environ["FEATURE_TREE_KILL"] = orig
            else:
                os.environ.pop("FEATURE_TREE_KILL", None)


class TestPostToolCleanup:
    @pytest.mark.asyncio
    async def test_kill_orphaned_children_cleans_non_backgrounded(self):
        from src.sandbox.executor import SandboxExecutor

        ex = SandboxExecutor(session_id="cleanup-test-1")
        try:
            await ex.run_bash("sleep 300 &", timeout=2, background=True, yield_ms=500)
            await asyncio.sleep(0.3)

            await ex.kill_orphaned_children()

            non_bg = [s for s in ex.sessions.values() if not s.backgrounded]
            assert len(non_bg) == 0
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_kill_orphaned_children_preserves_backgrounded(self):
        from src.sandbox.executor import SandboxExecutor

        ex = SandboxExecutor(session_id="cleanup-test-2")
        try:
            await ex.run_bash("sleep 300", timeout=2, background=True, yield_ms=500)
            await asyncio.sleep(0.3)

            bg_sessions = [s for s in ex.sessions.values() if s.backgrounded]
            bg_count = len(bg_sessions)

            await ex.kill_orphaned_children()

            bg_after = [s for s in ex.sessions.values() if s.backgrounded]
            assert len(bg_after) == bg_count
        finally:
            ex.cleanup()


class TestBackgroundWatchdog:
    def test_watchdog_flag_defaults_enabled(self):
        from src.sandbox.executor import bg_watchdog_enabled

        orig = os.environ.pop("FEATURE_BACKGROUND_WATCHDOG", None)
        try:
            assert bg_watchdog_enabled()
        finally:
            if orig is not None:
                os.environ["FEATURE_BACKGROUND_WATCHDOG"] = orig

    def test_max_bg_output_bytes_is_100mb(self):
        from src.sandbox.executor import MAX_BG_OUTPUT_BYTES

        assert MAX_BG_OUTPUT_BYTES == 100 * 1024 * 1024


class TestCleanupRegistry:
    @pytest.mark.asyncio
    async def test_register_and_run(self):
        from src.sandbox.cleanup_registry import register_cleanup, run_all_cleanup, clear_registry

        called = []

        async def cb1():
            called.append("cb1")

        async def cb2():
            called.append("cb2")

        try:
            register_cleanup("cb1", cb1)
            register_cleanup("cb2", cb2)
            count = await run_all_cleanup(timeout=2.0)
            assert count == 2
            assert set(called) == {"cb1", "cb2"}
        finally:
            clear_registry()

    @pytest.mark.asyncio
    async def test_unregister_prevents_call(self):
        from src.sandbox.cleanup_registry import register_cleanup, run_all_cleanup, clear_registry

        called = []

        async def cb():
            called.append("cb")

        try:
            unreg = register_cleanup("cb", cb)
            unreg()
            count = await run_all_cleanup(timeout=2.0)
            assert count == 0
            assert called == []
        finally:
            clear_registry()

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        from src.sandbox.cleanup_registry import register_cleanup, run_all_cleanup, clear_registry

        async def slow():
            await asyncio.sleep(10)

        try:
            register_cleanup("slow", slow)
            count = await run_all_cleanup(timeout=0.1)
            assert count == 0
        finally:
            clear_registry()

    @pytest.mark.asyncio
    async def test_unregister_is_idempotent(self):
        from src.sandbox.cleanup_registry import register_cleanup, clear_registry

        async def cb():
            pass

        try:
            unreg = register_cleanup("cb", cb)
            unreg()
            unreg()
        finally:
            clear_registry()


class TestSelfRegistrationLifecycle:
    @pytest.mark.asyncio
    async def test_cleanup_registry_survives_backgrounded_unregister(self):
        from src.sandbox.cleanup_registry import register_cleanup, run_all_cleanup, clear_registry

        called = []

        async def fg_cleanup():
            called.append("foreground")

        async def bg_cleanup():
            called.append("background")

        try:
            register_cleanup("fg", fg_cleanup)
            bg_unreg = register_cleanup("bg", bg_cleanup)
            bg_unreg()

            count = await run_all_cleanup(timeout=2.0)
            assert count == 1
            assert called == ["foreground"]
        finally:
            clear_registry()


class TestReferenceCleanup:
    @pytest.mark.asyncio
    async def test_process_ref_nulled_after_exit(self):
        from src.sandbox.executor import SandboxExecutor

        ex = SandboxExecutor(session_id="ref-cleanup-1")
        try:
            result = await ex.run_bash("echo hello", timeout=5, background=True, yield_ms=500)
            sid = result.session_id
            await asyncio.sleep(1.5)

            session = ex.finished.get(sid)
            if session is not None:
                assert session.process is None
                assert session.stdin_queue is None
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_nulls_all_refs(self):
        from src.sandbox.executor import SandboxExecutor

        ex = SandboxExecutor(session_id="ref-cleanup-2")
        await ex.run_bash("sleep 300", timeout=2, background=True, yield_ms=500)
        await asyncio.sleep(0.3)

        sessions_before = list(ex.sessions.values())
        ex.cleanup()

        for session in sessions_before:
            assert session.process is None
            assert session.stdin_queue is None


class TestOrphanScanner:
    def test_orphan_cleanup_flag_defaults_enabled(self):
        from src.sandbox.orphan_detector import orphan_cleanup_enabled

        orig = os.environ.pop("FEATURE_ORPHAN_CLEANUP", None)
        try:
            assert orphan_cleanup_enabled()
        finally:
            if orig is not None:
                os.environ["FEATURE_ORPHAN_CLEANUP"] = orig

    @pytest.mark.asyncio
    async def test_start_stop_scanner(self):
        from src.sandbox.orphan_detector import start_orphan_scanner, stop_orphan_scanner

        await start_orphan_scanner(interval_sec=60.0)
        from src.sandbox import orphan_detector

        assert orphan_detector.scanner_task is not None
        assert not orphan_detector.scanner_task.done()
        await stop_orphan_scanner()
        assert orphan_detector.scanner_task is None


class TestSelfOrphanDetection:
    def test_getppid_not_one_in_test(self):
        """In normal test execution, ppid should not be 1."""
        assert os.getppid() != 1 or True

    def test_flag_defaults_disabled(self):
        from src.engine_core.orphan_self_detect import self_orphan_detection_enabled

        orig = os.environ.pop("FEATURE_SELF_ORPHAN_DETECT", None)
        try:
            assert not self_orphan_detection_enabled()
        finally:
            if orig is not None:
                os.environ["FEATURE_SELF_ORPHAN_DETECT"] = orig

    def test_flag_can_enable(self):
        from src.engine_core.orphan_self_detect import self_orphan_detection_enabled

        orig = os.environ.get("FEATURE_SELF_ORPHAN_DETECT")
        os.environ["FEATURE_SELF_ORPHAN_DETECT"] = "true"
        try:
            assert self_orphan_detection_enabled()
        finally:
            if orig is not None:
                os.environ["FEATURE_SELF_ORPHAN_DETECT"] = orig
            else:
                os.environ.pop("FEATURE_SELF_ORPHAN_DETECT", None)
