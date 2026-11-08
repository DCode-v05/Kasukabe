"""Process tree utilities — recursive tree-kill and process inspection.

Reads /proc on Linux to build parent-child maps. Falls back to empty
results on non-Linux (macOS dev — production is always Linux).
"""

from __future__ import annotations

import logging
import os
import platform
import signal
from pathlib import Path

logger = logging.getLogger(__name__)

IS_LINUX = platform.system() == "Linux"


def build_ppid_map() -> dict[int, int]:
    """Read /proc/*/stat and return {pid: ppid} for every visible process."""
    if not IS_LINUX:
        return {}
    ppid_map: dict[int, int] = {}
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat_text = (entry / "stat").read_text()
            close_paren = stat_text.rfind(")")
            if close_paren == -1:
                continue
            fields_after_comm = stat_text[close_paren + 2 :].split()
            ppid = int(fields_after_comm[1])
            pid = int(entry.name)
            ppid_map[pid] = ppid
        except (OSError, ValueError, IndexError):
            continue
    return ppid_map


def get_child_pids(pid: int) -> list[int]:
    """Return direct child PIDs of *pid*.  Empty list on non-Linux or error."""
    ppid_map = build_ppid_map()
    return [p for p, pp in ppid_map.items() if pp == pid]


def get_ppid(pid: int) -> int | None:
    """Return the parent PID of *pid*, or None on non-Linux or error."""
    if not IS_LINUX:
        return None
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text()
        close_paren = stat_text.rfind(")")
        if close_paren == -1:
            return None
        fields = stat_text[close_paren + 2 :].split()
        return int(fields[1])
    except (OSError, ValueError, IndexError):
        return None


def get_descendant_pids(pid: int) -> list[int]:
    """Collect ALL descendant PIDs of *pid*, deepest-first (safe kill order).

    Single /proc scan, then BFS from *pid*.  Returns bottom-up so killing
    in list order terminates leaves before parents.
    """
    ppid_map = build_ppid_map()

    children: dict[int, list[int]] = {}
    for p, pp in ppid_map.items():
        children.setdefault(pp, []).append(p)

    result: list[int] = []
    queue = children.get(pid, [])
    visited: set[int] = set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        result.append(current)
        queue.extend(children.get(current, []))

    result.reverse()
    return result


def is_process_alive(pid: int) -> bool:
    """Check whether *pid* exists.  Signal-0 probe; EPERM means alive."""
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def tree_kill(pid: int, sig: int = SIGKILL) -> list[int]:
    """Kill *pid* and every descendant, bottom-up.

    Returns list of PIDs that were actually signaled.  Swallows
    ProcessLookupError (already dead) and logs PermissionError.
    """
    descendants = get_descendant_pids(pid)
    signaled: list[int] = []

    for target in descendants:
        try:
            os.kill(target, sig)
            signaled.append(target)
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.warning("tree_kill: permission denied for pid %d", target)

    try:
        os.kill(pid, sig)
        signaled.append(pid)
    except ProcessLookupError:
        pass
    except PermissionError:
        logger.warning("tree_kill: permission denied for root pid %d", pid)

    return signaled
