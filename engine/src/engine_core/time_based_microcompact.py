"""Time-based and forced microcompact — selective tool-result clearing.

This module implements two closely-related strategies that share one
clearing algorithm, differing only in their *eligibility* test:

- **TIME_BASED**: fires when the gap between *now* and the timestamp on the
  last assistant message exceeds ``config.gap_threshold_minutes``. Used at
  the top of the normal-turn compaction pipeline in
  :func:`CompactionOrchestrator.compact_if_needed` — the first thing that
  runs when a user returns after an idle period.

- **FORCED**: fires unconditionally (still respecting
  ``config.enabled``). Used as Tier 0 of
  :func:`CompactionOrchestrator.reactive_compact` — the cheapest
  intervention in the API-overflow recovery chain. Clears old tool-result
  content before the heavier :func:`compact_messages` / tool-result guard
  / emergency passes have to touch anything.

The clearing algorithm walks all ``ToolResultBlock`` instances across the
message list, filters to those whose paired ``ToolUseBlock`` is in
:data:`COMPACTABLE_TOOLS`, keeps the most recent ``keep_recent`` intact,
and replaces the older ones with :data:`STALENESS_MARKER`. The
``keep_recent`` floor is always at least 1 — the LLM always retains at
least one piece of real work context.

The pass is non-mutating: the input list and its messages are never
touched. Only the affected messages are rebuilt (immutable
``dataclasses.frozen`` requires a new instance anyway), and untouched
messages are passed through by reference.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum

from src.engine_core.microcompact_config import (
    MicrocompactConfig,
    COMPACTABLE_TOOLS,
)
from src.engine_core.prompt_cache_break_detection import notify_cache_deletion
from src.herald.types import (
    ImageBlock,
    Message,
    PersistedBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)


STALENESS_MARKER: str = "[Old tool result content cleared]"

IMAGE_MAX_TOKEN_SIZE: int = 2000


class TriggerMode(str, Enum):
    """Which eligibility rule to use for a microcompact pass.

    ``TIME_BASED`` fires only when the gap since the last timestamped
    assistant message exceeds the config threshold. ``FORCED`` fires
    unconditionally (still subject to ``config.enabled``).
    """

    TIME_BASED = "time_based"
    FORCED = "forced"


@dataclass(frozen=True)
class TimeBasedTrigger:
    """Return value of :func:`evaluate_trigger` when a microcompact should run.

    Attributes:
        mode: Which trigger mode fired.
        gap_minutes: Elapsed minutes since the last timestamped assistant
            message. For ``FORCED`` mode this is 0.0 when no timestamp is
            available, or the real gap when it is — recorded either way
            for telemetry, not used for gating.
        config: The config instance the trigger was evaluated against.
            Carried so callers don't need to thread it through separately.
    """

    mode: TriggerMode
    gap_minutes: float
    config: MicrocompactConfig


@dataclass(frozen=True)
class MicrocompactResult:
    """Return value of :func:`maybe_microcompact` when content was cleared.

    ``None`` is returned instead when the trigger doesn't fire OR when the
    trigger fires but there's nothing eligible to clear (all compactable
    results already fit inside the ``keep_recent`` window, or the
    conversation has no compactable results at all).

    Attributes:
        messages: A new message list with rebuilt entries for the
            affected positions. Never the same object as the input.
        mode: Which trigger mode actually fired for this pass.
        tools_cleared: Number of tool_result blocks replaced with the
            staleness marker.
        tools_kept: Number of tool_result blocks preserved verbatim
            (the most recent ``keep_recent`` compactable results).
        tokens_saved: Approximate tokens freed by the clearing. Computed
            from the original content length of cleared blocks using the
            same dense-estimation helper the rest of the compaction
            pipeline uses.
        gap_minutes: Elapsed minutes from the trigger, forwarded so
            callers can include it in telemetry events.
    """

    messages: list
    mode: TriggerMode
    tools_cleared: int
    tools_kept: int
    tokens_saved: int
    gap_minutes: float


def last_assistant_timestamp(messages: list) -> float | None:
    """Walk backward, return the timestamp of the most recent assistant
    message that has one. Returns None if no assistant message carries a
    timestamp.
    """
    for msg in reversed(messages):
        if getattr(msg, "role", "") != "assistant":
            continue
        ts = getattr(msg, "timestamp", None)
        if ts is not None:
            return float(ts)
    return None


def evaluate_trigger(
    messages: list,
    config: MicrocompactConfig,
    mode: TriggerMode,
) -> TimeBasedTrigger | None:
    """Decide whether a microcompact pass should run.

    The master kill switch (``config.enabled``) short-circuits both modes
    to ``None``. For ``TIME_BASED``, the trigger also requires a
    timestamped assistant message *and* a gap of at least
    ``config.gap_threshold_minutes`` minutes. ``FORCED`` mode passes as
    long as ``config.enabled`` is true, even if no timestamp exists.
    """
    if not config.enabled:
        return None

    gap_minutes = 0.0
    last_ts = last_assistant_timestamp(messages)

    if mode is TriggerMode.TIME_BASED:
        if last_ts is None:
            return None
        gap_minutes = (time.time() - last_ts) / 60.0
        if gap_minutes < config.gap_threshold_minutes:
            return None
    else:
        if last_ts is not None:
            gap_minutes = max(0.0, (time.time() - last_ts) / 60.0)

    return TimeBasedTrigger(mode=mode, gap_minutes=gap_minutes, config=config)


def is_already_cleared(block: ToolResultBlock) -> bool:
    """Return True when the block's content has already been replaced with
    the staleness marker on a previous microcompact pass.

    Idempotency guard: a conversation that triggers microcompact on two
    separate turns (e.g. user returns after a 2h idle gap, works for a
    while, then returns again after another 2h) would otherwise re-count
    already-cleared blocks in ``tools_cleared`` telemetry and crowd them
    against the ``keep_recent`` window. Matches src's ``block.content !==
    TIME_BASED_MC_CLEARED_MESSAGE`` guard in ``microCompact.ts``.
    """
    content = block.content
    if isinstance(content, str):
        return content == STALENESS_MARKER
    if isinstance(content, list):
        if len(content) == 1:
            only = content[0]
            text = getattr(only, "text", None)
            if isinstance(text, str) and text == STALENESS_MARKER:
                return True
    return False


def collect_compactable_tool_results(
    messages: list,
) -> list[tuple[int, int, ToolResultBlock]]:
    """Return a list of ``(msg_idx, block_idx, ToolResultBlock)`` for every
    tool_result whose paired tool_use (from an earlier assistant message)
    is in :data:`COMPACTABLE_TOOLS` AND whose content has not already been
    cleared by a prior microcompact pass.

    The scan is a single forward pass: build a dict of ``tool_use_id ->
    tool_name`` from assistant ToolUseBlocks, then check each
    ToolResultBlock against it. Results without a matching ``tool_use_id``
    (orphans) are skipped — the pairing repair pass handles those.
    Results whose content is already the staleness marker are also skipped
    so a second pass can't re-count them (see :func:`is_already_cleared`).
    """
    use_id_to_name: dict[str, str] = {}
    for msg in messages:
        if getattr(msg, "role", "") != "assistant":
            continue
        content = getattr(msg, "content", "")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, ToolUseBlock):
                use_id_to_name[block.id] = block.name

    collected: list[tuple[int, int, ToolResultBlock]] = []
    for i, msg in enumerate(messages):
        content = getattr(msg, "content", "")
        if not isinstance(content, list):
            continue
        for j, block in enumerate(content):
            if not isinstance(block, ToolResultBlock):
                continue
            tool_name = use_id_to_name.get(block.tool_use_id)
            if tool_name is None:
                continue
            if tool_name not in COMPACTABLE_TOOLS:
                continue
            if is_already_cleared(block):
                continue
            collected.append((i, j, block))
    return collected


def estimate_tool_result_tokens(block: ToolResultBlock) -> int:
    """Estimate the token cost of a ToolResultBlock's content.

    Text is estimated via ``estimate_tokens_dense`` (the same helper the
    rest of the compaction pipeline uses). Image and document blocks are
    counted at a flat :data:`IMAGE_MAX_TOKEN_SIZE` tokens each — matching
    src's ``calculateToolResultTokens`` in ``microCompact.ts``. This is a
    deliberately rough approximation; calling the real tokenizer on
    arbitrary binary payloads is expensive and unnecessary for compaction
    decisions where exact counts don't matter.
    """
    from src.engine_core.context_engineering import estimate_tokens_dense

    content = block.content
    if isinstance(content, str):
        return estimate_tokens_dense(content)
    if not isinstance(content, list):
        return 0

    total = 0
    for sub in content:
        if isinstance(sub, TextBlock):
            total += estimate_tokens_dense(sub.text)
        elif isinstance(sub, (ImageBlock,)):
            total += IMAGE_MAX_TOKEN_SIZE
        elif isinstance(sub, PersistedBlock):
            total += estimate_tokens_dense(sub.summary)
        else:
            text = getattr(sub, "text", None)
            if isinstance(text, str):
                total += estimate_tokens_dense(text)
            else:
                inner = getattr(sub, "content", None)
                if isinstance(inner, str):
                    total += estimate_tokens_dense(inner)
    return total


def maybe_microcompact(
    messages: list,
    config: MicrocompactConfig,
    mode: TriggerMode,
) -> MicrocompactResult | None:
    """Run a microcompact pass if the trigger fires and there's work to do.

    Returns ``None`` when the trigger doesn't fire, when no compactable
    tool_result blocks exist in the conversation, or when all existing
    compactable results already fit inside the ``keep_recent`` window
    (nothing to clear). Returns a :class:`MicrocompactResult` otherwise.

    The input ``messages`` list is never mutated. Messages whose blocks
    were not touched are passed through by reference; only messages that
    contained a cleared block are rebuilt (timestamp preserved).
    """
    trigger = evaluate_trigger(messages, config, mode)
    if trigger is None:
        return None

    collected = collect_compactable_tool_results(messages)
    if not collected:
        return None

    keep_recent = max(1, config.keep_recent)

    if len(collected) <= keep_recent:
        return None

    cleared_count = len(collected) - keep_recent
    to_clear = collected[:cleared_count]

    clear_by_msg: dict[int, set[int]] = {}
    tokens_saved_total = 0
    for msg_idx, block_idx, block in to_clear:
        clear_by_msg.setdefault(msg_idx, set()).add(block_idx)
        tokens_saved_total += estimate_tool_result_tokens(block)

    notify_cache_deletion()
    logger.warning(
        "microcompact: mutating tool_result content "
        "(mode=%s gap_minutes=%.1f cleared=%d kept=%d tokens_saved≈%d) "
        "— prompt cache prefix will miss on the next API call",
        trigger.mode.value,
        trigger.gap_minutes,
        cleared_count,
        keep_recent,
        tokens_saved_total,
    )

    new_messages: list = []
    for i, msg in enumerate(messages):
        if i not in clear_by_msg:
            new_messages.append(msg)
            continue
        clear_ids = clear_by_msg[i]
        content = msg.content
        if not isinstance(content, list):
            new_messages.append(msg)
            continue
        new_blocks: list = []
        for j, block in enumerate(content):
            if j in clear_ids and isinstance(block, ToolResultBlock):
                new_blocks.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=STALENESS_MARKER,
                        is_error=block.is_error,
                    )
                )
            else:
                new_blocks.append(block)
        new_messages.append(
            Message(
                role=msg.role,
                content=new_blocks,
                timestamp=msg.timestamp,
            )
        )

    return MicrocompactResult(
        messages=new_messages,
        mode=trigger.mode,
        tools_cleared=cleared_count,
        tools_kept=keep_recent,
        tokens_saved=tokens_saved_total,
        gap_minutes=trigger.gap_minutes,
    )
