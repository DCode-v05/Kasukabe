"""Prompt cache-break detection.

Tracks prompt state before each API call and compares cache token counts
in the response to detect unexpected cache misses. Distinguishes between
expected drops (after compaction/microcompact, announced via
notify_cache_deletion) and genuine cache breaks (content or scope change
that busted the server's KV cache).

Two-phase tracking:
  Phase 1 (pre-call): record_prompt_state() hashes system content both
    WITH and WITHOUT cache_control markers, plus per-tool schema hashes.
    Dual hashing catches scope/TTL flips that change cache behavior
    without changing content.

  Phase 2 (post-call): check_response_for_cache_break() compares
    cache_read_tokens against the previous turn. A drop to zero (or
    significant decrease) triggers a break classification. If a pending
    cache deletion was announced, the drop is suppressed as expected.

Per-source tracking supports main-thread vs sub-agent isolation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field

from src.herald.types import SystemPromptBlock, Usage

logger = logging.getLogger(__name__)


pending_deletions: set[str] = set()


def notify_cache_deletion(source: str = "main") -> None:
    """Record that an upcoming API response will legitimately drop its
    cache read tokens because client-side content was mutated.

    The microcompact path calls this on every successful fire. The
    cache break detector calls consume_pending_cache_deletion() before
    classifying a cache read drop as a break.
    """
    pending_deletions.add(source)
    logger.debug(
        "prompt cache deletion announced for source=%r "
        "(next cache read drop is expected, not a break)",
        source,
    )


def consume_pending_cache_deletion(source: str = "main") -> bool:
    """Atomically check-and-clear the pending deletion flag for source.

    Returns True if a deletion was pending (suppress the cache-break
    alarm), False otherwise. Clears the flag on consumption so a single
    mutation suppresses exactly one drop.
    """
    if source in pending_deletions:
        pending_deletions.discard(source)
        return True
    return False


def is_cache_deletion_pending(source: str = "main") -> bool:
    """Non-consuming peek at the pending flag."""
    return source in pending_deletions


def reset_pending_deletions() -> None:
    """Reset all pending deletion flags. Session boundaries / test teardown."""
    pending_deletions.clear()


@dataclass
class PromptState:
    """Snapshot of prompt content hashes taken before an API call.

    Dual hashing: system_hash_with_cache hashes each block's text + scope + ttl.
    system_hash_without_cache hashes only text. A mismatch between turns on
    _without_cache when _with_cache also changed means a scope/TTL flip that
    would be invisible to content-only hashing.
    """

    system_hash_with_cache: str = ""
    system_hash_without_cache: str = ""
    tool_hashes: dict[str, str] = field(default_factory=dict)
    timestamp: float = 0.0
    prev_cache_read_tokens: int = 0


class CacheBreakType:
    CLIENT_CONTENT = "client_content"
    CLIENT_SCOPE = "client_scope"
    CLIENT_TOOLS = "client_tools"
    SERVER_TTL = "server_ttl"
    EXPECTED = "expected"
    UNKNOWN = "unknown"


@dataclass
class CacheBreakResult:
    """Result of cache break analysis after an API response."""

    break_type: str
    details: str = ""
    changed_tools: list[str] = field(default_factory=list)


def record_prompt_state(
    system_blocks: list[SystemPromptBlock] | None,
    tools: list | None,
    prev_cache_read_tokens: int = 0,
) -> PromptState:
    """Hash prompt content before an API call.

    Call this before every LLM request. The returned PromptState is
    passed to check_response_for_cache_break() after the response.
    """
    with_parts: list[str] = []
    without_parts: list[str] = []
    for block in system_blocks or []:
        with_parts.append(f"{block.text}|{block.cache_scope}|{block.ttl}")
        without_parts.append(block.text)

    h_with = hashlib.sha256("|".join(with_parts).encode()).hexdigest()
    h_without = hashlib.sha256("|".join(without_parts).encode()).hexdigest()

    tool_hashes: dict[str, str] = {}
    for t in tools or []:
        name = getattr(t, "name", "") or ""
        schema_str = json.dumps(getattr(t, "input_schema", {}), sort_keys=True)
        tool_hashes[name] = hashlib.sha256(
            f"{name}|{getattr(t, 'description', '')}|{schema_str}".encode()
        ).hexdigest()

    return PromptState(
        system_hash_with_cache=h_with,
        system_hash_without_cache=h_without,
        tool_hashes=tool_hashes,
        timestamp=time.time(),
        prev_cache_read_tokens=prev_cache_read_tokens,
    )


def check_response_for_cache_break(
    state: PromptState,
    usage: Usage,
    prev_state: PromptState | None = None,
    source: str = "main",
) -> CacheBreakResult | None:
    """Analyze an API response for cache break signals.

    Returns a CacheBreakResult if a break was detected, None otherwise.
    A break is detected when cache_read_input_tokens drops significantly
    compared to prev_state's reading.

    False-positive suppression: if notify_cache_deletion() was called
    for this source, the drop is classified as EXPECTED.
    """
    cache_read = usage.cache_read_input_tokens
    prev_read = state.prev_cache_read_tokens

    if prev_read == 0 or cache_read >= prev_read * 0.5:
        return None

    if consume_pending_cache_deletion(source):
        return CacheBreakResult(
            break_type=CacheBreakType.EXPECTED,
            details=f"cache_read dropped {prev_read} → {cache_read} "
            f"(expected: client-side mutation announced)",
        )

    if prev_state is not None:
        if state.system_hash_without_cache != prev_state.system_hash_without_cache:
            return CacheBreakResult(
                break_type=CacheBreakType.CLIENT_CONTENT,
                details=f"system content hash changed: "
                f"{prev_state.system_hash_without_cache[:12]} → "
                f"{state.system_hash_without_cache[:12]}",
            )

        if state.system_hash_with_cache != prev_state.system_hash_with_cache:
            return CacheBreakResult(
                break_type=CacheBreakType.CLIENT_SCOPE,
                details="cache scope/TTL changed without content change",
            )

        changed_tools: list[str] = []
        all_names = set(state.tool_hashes) | set(prev_state.tool_hashes)
        for name in all_names:
            if state.tool_hashes.get(name) != prev_state.tool_hashes.get(name):
                changed_tools.append(name)
        if changed_tools:
            return CacheBreakResult(
                break_type=CacheBreakType.CLIENT_TOOLS,
                details=f"tool schemas changed: {', '.join(changed_tools)}",
                changed_tools=changed_tools,
            )

    return CacheBreakResult(
        break_type=CacheBreakType.SERVER_TTL,
        details=f"cache_read dropped {prev_read} → {cache_read} "
        f"with no detected client-side change (likely TTL expiry)",
    )
