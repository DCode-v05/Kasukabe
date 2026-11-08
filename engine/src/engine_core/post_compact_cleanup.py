"""Post-compact cleanup — centralised cache reset after a compaction event.

After CompactionOrchestrator shrinks the conversation, several components
across the engine still hold results that were computed against the old
long-form conversation. Leaving those caches in place means the next turn
can reuse stale search hits, stale asset-directory schemas, or stale
utility-directory lookups that no longer match what the model will see.

This module owns the single source of truth for "what gets reset after a
compaction". Every dependency is optional so the function is trivially
testable with individual fakes and tolerant of partial wiring. Every call
is wrapped in try/except so a failure in one cache reset cannot block the
rest or crash the agent turn — matching the resilience pattern already in
CompactionOrchestrator.post_compact_cleanup.

The module also exposes is_main_thread_compact, the predicate that
CompactionOrchestrator consults before taking its cache-aware snip
pre-pass: the server-side cache_edits deletion path only runs for a
main-thread compaction, so a subagent-flagged compaction falls back to
the in-place snip_prepass instead (compaction_orchestrator.py:807).
"""

from __future__ import annotations

import logging
from typing import Any

from src.engine_core.schema import FOREGROUND_QUERY_SOURCES

logger = logging.getLogger(__name__)


def is_main_thread_compact(query_source: str | None) -> bool:
    """Return True when this is a main-thread compaction.

    Main-thread values (the cache-aware snip deletion path is safe):
      - None (direct orchestrator call with no request context)
      - any value in FOREGROUND_QUERY_SOURCES: "foreground", "agent",
        "compact", "hook", "side_question"

    Subagent values (fall back to the in-place snip pre-pass):
      - anything prefixed with "subagent:" or "agent:" (e.g. "agent:foo").
        The prefix is checked before membership, so exact "agent" is
        main-thread while "agent:foo" is a subagent.

    CompactionOrchestrator consults this before taking the cache-aware
    snip pre-pass: the server-side cache_edits deletion path only runs for
    a main-thread compaction (compaction_orchestrator.py:807), so a
    subagent-flagged compaction falls back to the in-place snip_prepass.
    """
    if query_source is None:
        return True
    if query_source.startswith("subagent:") or query_source.startswith("agent:"):
        return False
    return query_source in FOREGROUND_QUERY_SOURCES


def run_post_compact_cleanup(
    *,
    consciousness_mgr: Any = None,
    file_read_state: dict | None = None,
    search: Any = None,
    utility: Any = None,
    assets: Any = None,
    collapse_manager: Any = None,
    llm_provider: Any = None,
) -> None:
    """Reset every cache that goes stale after a compaction.

    Every cache reset here is per-instance, so it is safe to run in both
    main-thread and subagent contexts. Each reset is wrapped in try/except
    so one failing cache cannot block the others or surface an exception
    into the agent loop.
    """

    if consciousness_mgr is not None:
        try:
            consciousness_mgr.invalidate()
        except Exception:
            logger.debug("Post-compact: consciousness invalidation failed")

    if file_read_state is not None:
        try:
            file_read_state.clear()
        except Exception:
            logger.debug("Post-compact: file_read_state clear failed")

    if search is not None:
        try:
            search.invalidate_all()
        except Exception:
            logger.debug("Post-compact: search cache invalidation failed")

    if utility is not None:
        try:
            utility.invalidate("")
        except Exception:
            logger.debug("Post-compact: utility cache invalidation failed")

    if assets is not None:
        try:
            assets.invalidate("")
        except Exception:
            logger.debug("Post-compact: assets cache invalidation failed")

    # Context Collapse state reset. Committed collapses are now folded
    # into the LLM compaction summary that just ran, so keeping them
    # would double-project on the next turn. Staged spans are dropped
    # too because their payloads point at messages that the compaction
    # already rewrote. Per-instance state, safe in both main-thread and
    # subagent contexts because the manager is scoped to one orchestrator.
    if collapse_manager is not None:
        try:
            collapse_manager.reset()
        except Exception:
            logger.debug("Post-compact: collapse_manager reset failed")

    # Beta header latching reset. Once thinking is latched on the LLM
    # provider, it stays latched for the session to avoid cache busting.
    # After compaction the session context is effectively reset, so the
    # latch must be cleared — next turn evaluates fresh. Matches src's
    # clearBetaHeaderLatches() called on /clear and /compact.
    if llm_provider is not None:
        try:
            if hasattr(llm_provider, "latched_thinking"):
                llm_provider.latched_thinking = False
        except Exception:
            logger.debug("Post-compact: llm_provider latch reset failed")

    # Cached-snip state lives on the LLM provider instance (not module-level),
    # so it is safe to reset in both main-thread and subagent contexts. After
    # compaction the message history is rewritten from scratch, so pending
    # and pinned cache_edits referring to old indices become invalid.
    # header_latched is intentionally preserved — once committed to the beta
    # header, keep sending it for the session to avoid busting the prompt cache.
    if llm_provider is not None:
        try:
            if hasattr(llm_provider, "reset_snip_state"):
                llm_provider.reset_snip_state()
        except Exception:
            logger.debug("Post-compact: cached_snip state reset failed")
