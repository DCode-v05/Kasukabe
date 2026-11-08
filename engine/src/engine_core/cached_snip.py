"""Cache-aware snip prepass — surgical tool-result deletion via cache editing API.

Instead of mutating tool_result content (which invalidates the prompt cache
prefix on every modification), this module identifies redundant tool results
and queues them for server-side deletion via the Anthropic cache_reference +
cache_edits API.

Key insight: the API allows attaching a ``cache_reference`` identifier to
tool_result blocks. A subsequent ``cache_edits`` content block (inserted into
the last user message) tells the server to delete those entries from its
cached KV store without touching the rest of the prompt. The client keeps
its in-memory messages unchanged, so the prompt hash is stable and the
remaining prefix stays cached — saving up to 3.75× on cache-hit scenarios.

Flow per turn:
  1. ``snip_prepass_get_deletes(messages)`` — compute tool_use_ids to remove,
     using the same four strategies as ``snip_prepass`` (duplicate reads,
     superseded reads, search-then-read, stale reads). Returns a list of IDs
     without touching message content.
  2. ``CompactionOrchestrator.compact_if_needed`` calls
     ``adapter.queue_snip_cache_edits(tool_ids)`` to store the IDs on the
     ``Provider`` instance.
  3. On the next API call, ``Provider.build_kwargs`` calls
     ``apply_cache_edits_to_messages`` which:
       a. Adds ``cache_reference: tool_use_id`` to tool_result blocks strictly
          before the last ``cache_control`` marker.
       b. Re-inserts previously pinned ``cache_edits`` blocks at their original
          user-message positions (so the server cache stays current).
       c. Inserts a new ``cache_edits`` block with the pending deletions into
          the last user message, then pins it for future turns.
  4. On full compaction (messages rewritten / cache invalidated), call
     ``adapter.reset_snip_state()`` to clear all accumulated state.

Feature flag:
  ``FEATURE_CACHED_SNIP_PREPASS`` (default "false"). When false the orchestrator
  falls through to the legacy ``snip_prepass`` which mutates message content.
  Disable when deploying to contexts that do not support the cache-editing
  beta header (non-Anthropic providers, older SDK versions, etc.).

Beta header:
  The cache-editing beta header is hardcoded as ``CACHE_EDITING_BETA_HEADER``.
  Once sent in any request during a session, it is latched on
  (``CachedSnipState.header_latched``) so mid-session feature-flag toggles or
  idle periods do not bust the prompt cache by dropping the header.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


FEATURE_CACHED_SNIP_PREPASS = "FEATURE_CACHED_SNIP_PREPASS"

CACHE_EDITING_BETA_HEADER = "cache-editing-2025-03-19"


def cached_snip_prepass_enabled() -> bool:
    """Return True when the cache-aware snip feature flag is on."""
    return os.environ.get(FEATURE_CACHED_SNIP_PREPASS, "false").strip().lower() == "true"


CACHE_EDITING_SUPPORTED_MODELS: frozenset[str] = frozenset(
    {
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "claude-opus-4-7",
        "claude-sonnet-4-7",
    }
)


def is_model_supported_for_cache_editing(model_id: str | None) -> bool:
    """Return True when ``model_id`` supports cache_reference + cache_edits.

    Unknown or non-Anthropic models return False so the orchestrator and
    adapter fall back to the legacy snip_prepass path (which mutates
    message content instead of relying on server-side cache editing).
    """
    if not model_id:
        return False
    return model_id in CACHE_EDITING_SUPPORTED_MODELS


@dataclass
class PinnedCacheEdit:
    """A cache_edits block pinned to a user-message position for re-sending.

    ``user_message_index`` is the index in the serialised Anthropic message
    array (as produced by ``to_anthropic_messages``) where the block was
    first inserted. Stable as long as no messages are removed from the head —
    which only happens during full compaction, which resets this state.
    """

    user_message_index: int
    tool_ids: list[str]


@dataclass
class CachedSnipState:
    """Per-provider-instance state for cache-aware snip prepass.

    pending_deletes
        Tool-result IDs queued by the latest snip decision but not yet
        consumed by an API call. Consumed (and cleared) by
        ``consume_pending_deletes()``.

    pinned_edits
        Deletion blocks from prior turns. Re-sent on every request so the
        server can reconstruct the edit history. Cleared only on reset.

    deleted_refs
        Cumulative set of IDs already deleted. Used to skip re-queuing the
        same ID on future snip-prepass runs.

    header_latched
        True once the cache-editing beta header has been included in any
        request. Never cleared by reset — once committed, always send the
        header for the session lifetime to avoid busting the prompt cache.
    """

    pending_deletes: list[str] = field(default_factory=list)
    pinned_edits: list[PinnedCacheEdit] = field(default_factory=list)
    deleted_refs: set[str] = field(default_factory=set)
    header_latched: bool = False


def queue_deletions(state: CachedSnipState, tool_ids: list[str]) -> None:
    """Add tool_use_ids to the pending-delete queue, skipping known-deleted IDs."""
    new_ids = [tid for tid in tool_ids if tid not in state.deleted_refs]
    if new_ids:
        state.pending_deletes.extend(new_ids)
        logger.debug(
            "cached_snip: queued %d new deletion(s) (already_deleted=%d, skipped=%d)",
            len(new_ids),
            len(state.deleted_refs),
            len(tool_ids) - len(new_ids),
        )


def consume_pending_deletes(state: CachedSnipState) -> list[str] | None:
    """Return and clear pending deletions. Returns None when the queue is empty."""
    if not state.pending_deletes:
        return None
    result = list(state.pending_deletes)
    state.pending_deletes.clear()
    return result


def pin_edits(state: CachedSnipState, user_message_index: int, tool_ids: list[str]) -> None:
    """Record that a cache_edits block was inserted at ``user_message_index``.

    Also marks the IDs as permanently deleted so future snip runs skip them.
    """
    if not tool_ids:
        return
    state.pinned_edits.append(
        PinnedCacheEdit(user_message_index=user_message_index, tool_ids=list(tool_ids))
    )
    state.deleted_refs.update(tool_ids)
    logger.debug(
        "cached_snip: pinned %d deletion(s) at user_msg[%d] (total_pinned=%d, total_deleted=%d)",
        len(tool_ids),
        user_message_index,
        len(state.pinned_edits),
        len(state.deleted_refs),
    )


def reset_state(state: CachedSnipState) -> None:
    """Purge pending and pinned state after full compaction or cache invalidation.

    Does NOT reset ``header_latched`` — once we have committed to sending the
    cache-editing header, we keep sending it for the remainder of the session
    to avoid busting the prompt cache.
    """
    state.pending_deletes.clear()
    state.pinned_edits.clear()
    state.deleted_refs.clear()
    logger.debug("cached_snip: state reset (pending + pinned cleared, header_latched preserved)")


def detect_unexpected_cache_break(
    state: CachedSnipState | None,
    cache_read_tokens: int,
    prev_cache_read_tokens: int,
) -> bool:
    """Return True when the server-side prompt cache has broken while we had
    active cache_edits state.

    The cache-aware path assumes the server holds KV entries for every
    ``cache_reference`` we previously sent. When the server reports a cache
    miss (``cache_read_tokens == 0``) on a turn where the prior turn got a
    cache hit AND we have pinned edits in flight, our pinned state is
    referencing entries the server no longer knows about — sending them
    again will 4xx. The caller must call ``reset_state`` in this case.

    Returns False when:
      - ``state`` is None or empty (nothing to lose)
      - prior turn had no cache read (first turn or already cold)
      - current turn got a cache hit (cache is still warm)
    """
    if state is None:
        return False
    if not state.pinned_edits and not state.pending_deletes:
        return False
    if prev_cache_read_tokens <= 0:
        return False
    return cache_read_tokens == 0


def snip_prepass_get_deletes(messages: list) -> list[str]:
    """Compute tool_use_ids that snip_prepass would stub out, without mutating messages.

    Applies the same four strategies as ``snip_prepass``:
      1. Duplicate file reads — same file_path read twice, keep latest.
      2. Superseded reads — read → write/edit → re-read of same file.
      3. Search-then-read — grep/glob result, all target files later read.
      4. Stale reads — read-only results from the oldest 50% of messages.

    Returns [] when nothing qualifies or the message list is too short.
    """
    from src.engine_core.context_engineering import (
        build_tool_index,
        find_duplicate_reads,
        find_superseded_reads,
        find_search_then_read,
        find_stale_reads,
    )

    if len(messages) < 4:
        return []

    pairs, write_events = build_tool_index(messages)
    if not pairs:
        return []

    to_delete: set[tuple[int, str]] = set()
    to_delete |= find_duplicate_reads(pairs)
    to_delete |= find_superseded_reads(pairs, write_events)
    to_delete |= find_search_then_read(pairs)
    to_delete |= find_stale_reads(pairs, len(messages))

    if not to_delete:
        return []

    tool_ids = [tool_use_id for msg_idx, tool_use_id in to_delete]
    logger.debug(
        "cached_snip: snip_prepass_get_deletes identified %d tool result(s)",
        len(tool_ids),
    )
    return tool_ids


def apply_cache_edits_to_messages(
    anthropic_messages: list[dict],
    state: CachedSnipState,
    new_deletes: list[str] | None,
) -> tuple[list[dict], bool]:
    """Inject cache_reference and cache_edits into a serialised Anthropic message list.

    Must be called AFTER ``place_message_cache_marker`` so the cache_control
    positions are final. Mutates ``anthropic_messages`` in-place (they are
    freshly-built dicts from ``to_anthropic_messages``).

    Step 1 — cache_reference: add ``cache_reference: tool_use_id`` to every
      tool_result block in user messages STRICTLY BEFORE the message that holds
      the last ``cache_control`` marker. This registers the cached entries so
      the API can match delete operations against them.

    Step 2 — pinned edits: re-insert prior-turn cache_edits blocks at their
      recorded positions (required by the API cache-hit contract).

    Step 3 — new edits: insert a fresh cache_edits block into the last user
      message (after its tool_result blocks), then pin it via
      ``pin_edits(state, …)`` for future turns.

    Returns ``(messages, any_edits_applied)``.
    ``any_edits_applied`` is True when edits were inserted or pinned edits
    were re-sent — used by the caller to decide whether to add the beta header.
    """
    if not anthropic_messages:
        return anthropic_messages, False

    needs_filter = bool(state.pinned_edits) or bool(new_deletes)
    present_tool_ids: set[str] = set()
    if needs_filter:
        for msg in anthropic_messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id", "")
                    if tid:
                        present_tool_ids.add(tid)

    last_cc_msg_idx = -1
    for i, msg in enumerate(anthropic_messages):
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "cache_control" in block:
                    last_cc_msg_idx = i

    if last_cc_msg_idx >= 0:
        for i in range(last_cc_msg_idx):
            msg = anthropic_messages[i]
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            patched = False
            for j, block in enumerate(content):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and "cache_reference" not in block
                ):
                    tool_use_id = block.get("tool_use_id", "")
                    if tool_use_id:
                        if not patched:
                            msg["content"] = list(content)
                            content = msg["content"]
                            patched = True
                        content[j] = {**block, "cache_reference": tool_use_id}

    seen_delete_refs: set[str] = set()

    def make_edits_block(tool_ids: list[str]) -> dict | None:
        edits = [
            {"type": "delete", "cache_reference": tid}
            for tid in tool_ids
            if tid not in seen_delete_refs and not seen_delete_refs.add(tid)  # type: ignore[func-returns-value]
        ]
        return {"type": "cache_edits", "edits": edits} if edits else None

    any_edits = False

    for pinned in state.pinned_edits:
        idx = pinned.user_message_index
        if idx >= len(anthropic_messages):
            logger.debug(
                "cached_snip: pinned edit at [%d] out of range (count=%d), skipping",
                idx,
                len(anthropic_messages),
            )
            continue
        msg = anthropic_messages[idx]
        if msg.get("role") != "user":
            continue
        filtered_ids = [tid for tid in pinned.tool_ids if tid in present_tool_ids]
        if not filtered_ids:
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            content = [{"type": "text", "text": str(content)}]
            msg["content"] = content
        block = make_edits_block(filtered_ids)
        if block:
            msg["content"] = insert_after_tool_results(content, block)
            any_edits = True

    if new_deletes:
        new_deletes = [tid for tid in new_deletes if tid in present_tool_ids]
    if new_deletes:
        for i in range(len(anthropic_messages) - 1, -1, -1):
            msg = anthropic_messages[i]
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                content = [{"type": "text", "text": str(content)}]
            block = make_edits_block(new_deletes)
            if block:
                msg["content"] = insert_after_tool_results(content, block)
                actual_ids = [e["cache_reference"] for e in block["edits"]]
                pin_edits(state, i, actual_ids)
                logger.debug(
                    "cached_snip: inserted cache_edits with %d deletion(s) at message[%d]: %s",
                    len(actual_ids),
                    i,
                    ", ".join(actual_ids[:5]),
                )
                any_edits = True
            break

    return anthropic_messages, any_edits


def insert_after_tool_results(content: list[dict], block: dict) -> list[dict]:
    """Return a new list with block inserted after the last tool_result.

    Does not mutate the input. If there are no tool_result blocks, inserts
    before the last item (preserves trailing text context). If the inserted
    block would be the last item, appends a text sentinel ``{"type": "text",
    "text": "."}`` — some API validators require the final block in a user
    message to be a text block.

    Mirrors ``insertBlockAfterToolResults`` from the reference implementation.
    """
    last_tr_idx = -1
    for i, b in enumerate(content):
        if isinstance(b, dict) and b.get("type") == "tool_result":
            last_tr_idx = i

    result = list(content)
    if last_tr_idx >= 0:
        insert_pos = last_tr_idx + 1
        result.insert(insert_pos, block)
        if insert_pos == len(result) - 1:
            result.append({"type": "text", "text": "."})
    else:
        result.insert(max(0, len(result) - 1), block)
    return result
