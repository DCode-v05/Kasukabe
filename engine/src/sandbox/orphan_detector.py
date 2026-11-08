"""Periodic orphan scanner — finds and kills sandbox processes that outlived
their parent tool execution.

Walks /proc every *interval_sec* seconds, looking for processes reparented
to PID 1 whose cwd still points to an executor workspace (/tmp/workspace-*).
Those are orphans from a completed or crashed executor.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
from pathlib import Path

from src.sandbox.process_tree import get_ppid, is_process_alive, tree_kill

logger = logging.getLogger(__name__)

IS_LINUX = platform.system() == "Linux"

FEATURE_FLAG = "FEATURE_ORPHAN_CLEANUP"
FEATURE_DEFAULT = "true"

scanner_task: asyncio.Task | None = None


def orphan_cleanup_enabled() -> bool:
    """Check whether orphan cleanup is active (feature flag)."""
    return os.environ.get(FEATURE_FLAG, FEATURE_DEFAULT).strip().lower() != "false"


def is_sandbox_orphan(pid: int) -> bool:
    """Return True if *pid* is reparented to init AND its cwd is an executor workspace.

    The workspace root is resolved from the executor rather than hardcoded.
    This previously matched the literal ``/tmp/workspace-``, which was already
    wrong: workspaces came from ``tempfile.gettempdir()``, so setting TMPDIR
    silently disabled orphan reaping and let killed grandchildren accumulate
    as zombies until the container ran out of PIDs.
    """
    ppid = get_ppid(pid)
    if ppid != 1:
        return False
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return False

    from src.sandbox.executor import WORKSPACE_PREFIX, workspace_root

    try:
        marker = f"{workspace_root()}/{WORKSPACE_PREFIX}"
    except Exception:
        # Root unresolvable (misconfigured env in a teardown path). Fall back to
        # the prefix alone rather than silently reaping nothing.
        logger.debug("Workspace root unresolvable; matching on prefix only", exc_info=True)
        marker = f"/{WORKSPACE_PREFIX}"
    return marker in cwd


async def scan_once() -> int:
    """Walk /proc, find sandbox orphans reparented to init, tree-kill them."""
    if not IS_LINUX:
        return 0

    killed = 0
    my_pid = os.getpid()
    proc = Path("/proc")

    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == my_pid or pid <= 1:
            continue
        if is_sandbox_orphan(pid) and is_process_alive(pid):
            logger.info("orphan_detector: killing sandbox orphan pid=%d", pid)
            tree_kill(pid)
            killed += 1

    return killed


async def scanner_loop(interval_sec: float) -> None:
    """Run scan_once() every *interval_sec* until cancelled."""
    while True:
        await asyncio.sleep(interval_sec)
        try:
            killed = await scan_once()
            if killed:
                logger.info("orphan_detector: cleaned up %d orphan(s)", killed)
        except Exception:
            logger.debug("orphan_detector: scan failed", exc_info=True)


async def start_orphan_scanner(interval_sec: float = 30.0) -> None:
    """Launch the periodic orphan scanner as a background asyncio task."""
    global scanner_task
    if scanner_task is not None and not scanner_task.done():
        return
    if not orphan_cleanup_enabled():
        return
    scanner_task = asyncio.create_task(scanner_loop(interval_sec))
    logger.info("orphan_detector: scanner started (interval=%.0fs)", interval_sec)


async def stop_orphan_scanner() -> None:
    """Cancel the orphan scanner task."""
    global scanner_task
    if scanner_task is not None and not scanner_task.done():
        scanner_task.cancel()
        try:
            await scanner_task
        except asyncio.CancelledError:
            pass
    scanner_task = None
    logger.info("orphan_detector: scanner stopped")
