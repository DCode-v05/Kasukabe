"""Agent disk storage — metadata sidecars, transcript JSONL, eviction.

Background and long-running sub-agents leave artifacts on disk:
- metadata sidecars at {SUBAGENT_METADATA_DIR}/{agent_id}.{phase}.json
- sidechain transcripts at {SIDECHAIN_DIR}/{agent_id}.jsonl

Without eviction these accumulate forever. This module provides a simple,
mtime-based sweep with a configurable grace period. Mirrors evictTaskOutput
+ PANEL_GRACE_MS in the reference.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)


# Default grace period: 1 hour. A finished agent's artifacts stay around
# long enough for a human to inspect post-mortem, but get collected next
# sweep. Override with SUBAGENT_GRACE_MS env var.
PANEL_GRACE_MS_DEFAULT = 60 * 60 * 1000

# Minimum seconds between two real eviction sweeps of the same directory
# pair. Opportunistic eviction runs at the end of every SubAgent.run() — if
# 20 agents finish in the same second, only the first actually sweeps.
SWEEP_MIN_INTERVAL_S = 60.0
LAST_SWEEP_TS: float = 0.0


def resolve_grace_ms(override: int | None = None) -> int:
    if override is not None:
        return override
    raw = os.environ.get("SUBAGENT_GRACE_MS", "").strip()
    if not raw:
        return PANEL_GRACE_MS_DEFAULT
    try:
        return int(raw)
    except ValueError:
        return PANEL_GRACE_MS_DEFAULT


def evict_stale(
    directory: str,
    *,
    grace_ms: int | None = None,
    pattern_suffixes: tuple[str, ...] = (".json", ".jsonl"),
    now: float | None = None,
) -> list[str]:
    """Delete files older than grace_ms in directory; return removed paths.

    Only files whose suffix matches pattern_suffixes are considered — prevents
    wiping unrelated files if someone points this at a shared dir. Non-fatal
    errors are logged and swallowed (eviction is best-effort).
    """
    if not directory or not os.path.isdir(directory):
        return []
    grace = resolve_grace_ms(grace_ms)
    cutoff = (now if now is not None else time.time()) - (grace / 1000.0)
    removed: list[str] = []
    try:
        entries = os.listdir(directory)
    except OSError as exc:
        logger.debug("evict_stale: listdir(%s) failed: %s", directory, exc)
        return []
    for entry in entries:
        if not entry.endswith(pattern_suffixes):
            continue
        path = os.path.join(directory, entry)
        try:
            st = os.stat(path)
            if st.st_mtime > cutoff:
                continue
            os.unlink(path)
            removed.append(path)
        except OSError as exc:
            logger.debug("evict_stale: unlink(%s) failed: %s", path, exc)
    return removed


def evict_all_stale(
    *,
    grace_ms: int | None = None,
    metadata_dir: str | None = None,
    sidechain_dir: str | None = None,
    force: bool = False,
) -> dict[str, list[str]]:
    """Sweep both the metadata and sidechain directories.

    When either dir is None, falls back to the env var or the reference
    default (/tmp/engine-sidechains for sidechain, unset for metadata).
    Returns a dict keyed by directory kind so callers can emit telemetry.

    Rate-limited: sweeps more often than once per SWEEP_MIN_INTERVAL_S
    return empty — opportunistic callers (every SubAgent.run() finally) stay
    cheap. Pass force=True for explicit operator-triggered sweeps (tests,
    shutdown hook).
    """
    global LAST_SWEEP_TS
    if not force:
        now = time.monotonic()
        if (now - LAST_SWEEP_TS) < SWEEP_MIN_INTERVAL_S:
            return {"metadata": [], "sidechain": []}
        LAST_SWEEP_TS = now

    # Sidechain dir default is centralized in fork_subagent.get_sidechain_dir
    # so env-var resolution stays in one place.
    from src.engine_core.fork_subagent import get_sidechain_dir

    md_dir = metadata_dir or os.environ.get("SUBAGENT_METADATA_DIR", "").strip()
    sc_dir = sidechain_dir or get_sidechain_dir()
    return {
        "metadata": evict_stale(md_dir, grace_ms=grace_ms, pattern_suffixes=(".json",)),
        "sidechain": evict_stale(sc_dir, grace_ms=grace_ms, pattern_suffixes=(".jsonl",)),
    }


__all__ = [
    "PANEL_GRACE_MS_DEFAULT",
    "evict_stale",
    "evict_all_stale",
]
