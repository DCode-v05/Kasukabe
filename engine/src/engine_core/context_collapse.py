"""Context Collapse — incremental, mechanical compression of low-value
message spans as the context window fills.

Full compaction (src/engine_core/context_engineering.py) is expensive
(it spends an LLM call), lossy (the entire conversation is summarised
into one block), and all-or-nothing (either everything gets summarised
or nothing does). Context Collapse is a cheaper, finer-grained
alternative that runs BEFORE the compaction pipeline.

Every turn:
  * Every message is scored by src/engine_core/importance_scoring.py.
  * As usage climbs through three threshold bands (0.75, 0.85, 0.95),
    spans of consecutive low-importance messages get STAGED.
  * At >= 0.95 usage (or on an API 413 overflow), staged spans get
    COMMITTED: replaced with a single mechanical summary message.
  * project_messages() applies committed collapses to the working
    message list, so the caller sees a shorter history without ever
    mutating the original transcript.

Boundaries are fingerprinted by (timestamp, role, content preview,
length). Timestamps are preserved across conversation_store round-trips,
so fingerprints survive session resume — project_messages() can
re-apply committed collapses to a freshly loaded message list.

Design notes:
  * Mechanical only. No LLM call anywhere in this module. Summaries
    are built by concatenating short per-message descriptors.
  * Tool use / tool result pairs are respected at every step. A span
    that would split a pair is shrunk until the pair is whole.
  * User messages (non-tool-result) are never collapsed. The scorer
    pins them to 1.0; the staging algorithm also hard-refuses them.
  * The most recent PRESERVE_RECENT messages are untouchable — they
    anchor the model's short-term working memory.
  * Collapse OWNS the threshold ladder when enabled. The
    CompactionOrchestrator checks the enabled flag at the top of
    compact_if_needed() and, if collapse is on, returns early with
    the projected message list unless the projection is still over
    budget (the all-messages-high-risk edge case).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Sequence

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.infra.sqlite_pool import SqlitePool


# ── Thresholds ────────────────────────────────────────────────────

# Strategy name reported on CompactionResult / CompactionEvent when the
# collapse pre-pass handled the compaction pressure on its own. Shared
# with the orchestrator and tests so nobody matches on a raw string.
STRATEGY_CONTEXT_COLLAPSE = "context_collapse"

# Usage ratio bands. Below LOW nothing happens; between LOW and MID
# only very low-risk spans stage; between MID and HIGH medium-risk
# spans stage; at or above HIGH the staged queue commits immediately.
USAGE_LOW = 0.75
USAGE_MID = 0.85
USAGE_HIGH = 0.95

# Risk thresholds per band. A span's risk is the max score of any
# message in the span — the riskiest member decides the span's band.
RISK_LOW = 0.30
RISK_MID = 0.60

# Minimum span length. Spans of one message are not worth the summary
# overhead — the summary itself costs tokens.
MIN_SPAN_MESSAGES = 2

# Summary token budget: a committed summary may not exceed this
# fraction of the original span's tokens. If the mechanical summary
# would exceed this it gets truncated.
SUMMARY_BUDGET_RATIO = 0.10


# ── Data classes ──────────────────────────────────────────────────


@dataclass(frozen=True)
class StagedSpan:
    """A span of messages marked for collapse but not yet committed.

    payload carries the actual messages at stage time so the summary
    generator can run even after the working message list mutates
    (e.g. after snip_prepass rewrote a tool result in place).
    """

    span_id: str
    start_fp: str
    end_fp: str
    risk: float
    staged_at: float
    payload: tuple = field(default_factory=tuple)  # tuple[Message, ...]


@dataclass(frozen=True)
class CommittedCollapse:
    """A collapsed span: original messages have been replaced with
    a mechanical summary. Persists across session resume."""

    collapse_id: str
    start_fp: str
    end_fp: str
    summary: str
    original_tokens: int
    summary_tokens: int
    committed_at: float


# ── Fingerprints ──────────────────────────────────────────────────


def fingerprint(msg: Any) -> str:
    """Build a stable identifier for a message.

    We can't rely on object identity (frozen dataclasses are rebuilt
    every turn) or list index (indices shift as messages are added).
    Instead we hash (timestamp, role, content preview, content length)
    into a short hex digest. Timestamps are preserved by the
    conversation_store load path, so the fingerprint survives a
    save/restore round-trip.
    """
    from src.herald.types import TextBlock, ToolResultBlock, ToolUseBlock

    role = getattr(msg, "role", "") or ""
    ts = getattr(msg, "timestamp", 0.0) or 0.0
    content = getattr(msg, "content", "")

    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, TextBlock):
                parts.append("T:" + block.text)
            elif isinstance(block, ToolUseBlock):
                parts.append(f"U:{block.name}:{block.id}")
            elif isinstance(block, ToolResultBlock):
                inner = block.content
                if isinstance(inner, str):
                    parts.append(f"R:{block.tool_use_id}:{inner}")
                elif isinstance(inner, list):
                    tparts: list[str] = []
                    for sub in inner:
                        if isinstance(sub, TextBlock):
                            tparts.append(sub.text)
                    parts.append(f"R:{block.tool_use_id}:{''.join(tparts)}")
                else:
                    parts.append(f"R:{block.tool_use_id}:")
        text = "||".join(parts)
    else:
        text = ""

    preview = text[:200]
    payload = f"{ts:.6f}|{role}|{len(text)}|{preview}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


# ── Mechanical summary generation ─────────────────────────────────


def describe_message(msg: Any) -> list[str]:
    """Return short descriptors for each content block in a message.

    Every descriptor is one line, capped at a sensible length. These
    lines are joined into the final summary string by
    generate_collapse_summary().
    """
    from src.herald.types import TextBlock, ToolResultBlock, ToolUseBlock

    out: list[str] = []
    role = getattr(msg, "role", "") or ""
    content = getattr(msg, "content", "")

    if isinstance(content, str):
        preview = content.strip().replace("\n", " ")[:150]
        if preview:
            prefix = "User" if role == "user" else "Assistant"
            out.append(f"{prefix}: {preview}")
        return out

    if not isinstance(content, list):
        return out

    for block in content:
        if isinstance(block, TextBlock):
            preview = block.text.strip().replace("\n", " ")[:150]
            if preview:
                out.append(f"Assistant: {preview}")
        elif isinstance(block, ToolUseBlock):
            try:
                input_preview = json.dumps(block.input, default=str)[:80]
            except Exception:
                input_preview = str(block.input)[:80]
            out.append(f"Called {block.name}({input_preview})")
        elif isinstance(block, ToolResultBlock):
            inner = block.content
            if isinstance(inner, str):
                flat = inner
            elif isinstance(inner, list):
                flat_parts: list[str] = []
                for sub in inner:
                    if isinstance(sub, TextBlock):
                        flat_parts.append(sub.text)
                flat = " ".join(flat_parts)
            else:
                flat = ""
            preview = flat.strip().replace("\n", " ")[:100]
            out.append(f"Result: {preview}")
    return out


def generate_collapse_summary(span_messages: Sequence[Any]) -> str:
    """Build a compact, mechanical summary for a span of messages.

    No LLM call. The summary is a short list of per-message descriptors
    followed by a one-line rollup. The result is truncated so that its
    own token footprint is at most SUMMARY_BUDGET_RATIO of the span's
    original tokens (measured by estimate_messages_tokens). Truncation
    happens character-wise at the tail — the header and earliest
    descriptors always survive.
    """
    from src.engine_core.context_engineering import estimate_messages_tokens
    from src.herald.types import ToolUseBlock

    if not span_messages:
        return ""

    # Count tool calls for the rollup line.
    tool_call_count = 0
    for msg in span_messages:
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, ToolUseBlock):
                    tool_call_count += 1

    descriptors: list[str] = []
    for msg in span_messages:
        descriptors.extend(describe_message(msg))

    header = f"[Collapsed: {len(span_messages)} messages, {tool_call_count} tool calls.]"
    body = "\n".join(f"  - {d}" for d in descriptors)
    summary = f"{header}\n{body}" if body else header

    # Enforce the summary token budget. estimate_tokens is
    # int(len/4 * SAFETY_MARGIN) where SAFETY_MARGIN=1.2 — invert it
    # to get the character budget for a given token budget.
    from src.engine_core.context_engineering import SAFETY_MARGIN

    original_tokens = max(1, estimate_messages_tokens(list(span_messages)))
    max_tokens = max(1, int(original_tokens * SUMMARY_BUDGET_RATIO))
    max_chars = max(len(header) + 1, int(max_tokens * 4 / SAFETY_MARGIN))
    if len(summary) > max_chars:
        marker = "\n  ... [truncated]"
        keep = max(len(header) + 1, max_chars - len(marker))
        summary = summary[:keep].rstrip() + marker
    return summary


# ── Span detection ────────────────────────────────────────────────


def has_tool_result(msg: Any) -> bool:
    from src.herald.types import ToolResultBlock

    content = getattr(msg, "content", "")
    if not isinstance(content, list):
        return False
    return any(isinstance(b, ToolResultBlock) for b in content)


def is_user_anchor(msg: Any) -> bool:
    """True for user messages that must never be collapsed.

    A user message that carries tool_result blocks is not a pure user
    intent message — it's the turn carrier for tool output. Those can
    be collapsed as part of a span because the scorer already treats
    them as low-importance tool_result content.
    """
    return getattr(msg, "role", "") == "user" and not has_tool_result(msg)


def walk_tool_ids(messages: Sequence[Any]) -> tuple[set[str], set[str]]:
    """Return (tool_use_ids, tool_result_ids) inside a message slice."""
    from src.herald.types import ToolResultBlock, ToolUseBlock

    use_ids: set[str] = set()
    result_ids: set[str] = set()
    for msg in messages:
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, ToolUseBlock):
                    use_ids.add(block.id)
                elif isinstance(block, ToolResultBlock):
                    result_ids.add(block.tool_use_id)
    return use_ids, result_ids


def shrink_span_to_whole_pairs(
    messages: Sequence[Any], start: int, end: int
) -> tuple[int, int] | None:
    """Adjust [start, end] so every tool_use inside has its result
    inside, and vice versa. If the span shrinks below MIN_SPAN_MESSAGES,
    return None (the caller drops this candidate)."""
    while end > start:
        use_ids, result_ids = walk_tool_ids(messages[start:end])
        missing_results = use_ids - result_ids
        missing_uses = result_ids - use_ids
        if not missing_results and not missing_uses:
            break
        # An orphaned tool_use at the tail? Drop the last message.
        # An orphaned tool_result at the head? Drop the first message.
        shrunk = False
        if missing_results:
            end -= 1
            shrunk = True
        if missing_uses and end > start:
            start += 1
            shrunk = True
        if not shrunk:
            return None
    if end - start < MIN_SPAN_MESSAGES:
        return None
    return start, end


# ── Manager ───────────────────────────────────────────────────────


class ContextCollapseManager:
    """Owner of staged and committed collapses for one session.

    Lifecycle:
      * Constructed once per ExecutionThread in thread.ensure_compactor.
      * Receives the DB handle so it can save/restore snapshots.
      * Called at the top of CompactionOrchestrator.compact_if_needed
        (pre-pass) and at the top of reactive_compact (413 drain).
      * Reset by post_compact_cleanup when the fallback LLM compaction
        fires, because the committed collapses are now folded into the
        compaction summary.
    """

    def __init__(
        self,
        db: "SqlitePool | None" = None,
        *,
        enabled: bool = True,
    ) -> None:
        self.db = db
        self.enabled = enabled
        self.staged: list[StagedSpan] = []
        self.committed: list[CommittedCollapse] = []

    # ── properties ─────────────────────────────────────────────

    def has_staged(self) -> bool:
        return bool(self.staged)

    def has_committed(self) -> bool:
        return bool(self.committed)

    def staged_spans(self) -> list[StagedSpan]:
        return list(self.staged)

    def committed_collapses(self) -> list[CommittedCollapse]:
        return list(self.committed)

    # ── evaluation / staging ───────────────────────────────────

    def evaluate_and_stage(
        self,
        messages: Sequence[Any],
        token_count: int,
        context_limit: int,
    ) -> None:
        """Inspect the message list and stage any low-risk spans that
        fit the current usage band.

        < USAGE_LOW  → no-op.
        < USAGE_MID  → stage spans with risk < RISK_LOW.
        < USAGE_HIGH → stage spans with risk < RISK_MID.
        >= USAGE_HIGH → stage everything collapsible and commit at once.
        """
        if not self.enabled or context_limit <= 0 or not messages:
            return
        ratio = token_count / context_limit
        if ratio < USAGE_LOW:
            return

        if ratio < USAGE_MID:
            risk_threshold = RISK_LOW
        elif ratio < USAGE_HIGH:
            risk_threshold = RISK_MID
        else:
            risk_threshold = 1.0  # commit anything collapsible

        from src.engine_core.context_engineering import PRESERVE_RECENT
        from src.engine_core.importance_scoring import score_message_importance

        total = len(messages)
        last_touchable = max(0, total - PRESERVE_RECENT)
        if last_touchable < MIN_SPAN_MESSAGES:
            return

        # Single-pass fingerprint cache. Every other stage of this
        # function — exclusion checks, span boundaries, duplicate
        # detection — needs a fingerprint for the same message, so
        # computing it once up front is meaningfully cheaper than
        # recomputing per check.
        fps = [fingerprint(m) for m in messages]

        # Messages already inside a committed OR staged collapse are
        # off-limits. The staged set is the key to avoiding duplicate
        # spans when evaluate_and_stage runs several times at the same
        # usage band before commit drains the queue.
        excluded_fps = self.excluded_fingerprints(fps)

        # Walk forward accumulating consecutive low-risk candidates.
        i = 0
        while i < last_touchable:
            msg = messages[i]
            if is_user_anchor(msg):
                i += 1
                continue
            if fps[i] in excluded_fps:
                i += 1
                continue
            score = score_message_importance(msg, i, total)
            if score >= risk_threshold:
                i += 1
                continue
            # Start of a candidate span — extend while scores stay
            # under the threshold and we don't cross a user anchor or
            # an already-staged / already-committed region.
            start = i
            max_risk = score
            j = i + 1
            while j < last_touchable:
                nxt = messages[j]
                if is_user_anchor(nxt):
                    break
                if fps[j] in excluded_fps:
                    break
                nxt_score = score_message_importance(nxt, j, total)
                if nxt_score >= risk_threshold:
                    break
                if nxt_score > max_risk:
                    max_risk = nxt_score
                j += 1
            end = j  # exclusive
            adjusted = shrink_span_to_whole_pairs(messages, start, end)
            if adjusted is not None:
                a_start, a_end = adjusted
                span_messages = tuple(messages[a_start:a_end])
                span = StagedSpan(
                    span_id=uuid.uuid4().hex,
                    start_fp=fps[a_start],
                    end_fp=fps[a_end - 1],
                    risk=max_risk,
                    staged_at=time.time(),
                    payload=span_messages,
                )
                self.staged.append(span)
            i = max(end, i + 1)

        # Emergency band — commit everything we just staged.
        if ratio >= USAGE_HIGH and self.staged:
            self.commit_all_staged()

    # ── commit ─────────────────────────────────────────────────

    def commit_all_staged(self) -> list[CommittedCollapse]:
        """Turn every staged span into a committed collapse and clear
        the staged queue. Idempotent — calling twice is a no-op the
        second time."""
        if not self.staged:
            return []
        from src.engine_core.context_engineering import (
            estimate_messages_tokens,
            estimate_tokens,
        )

        commits: list[CommittedCollapse] = []
        for span in self.staged:
            summary = generate_collapse_summary(span.payload)
            commit = CommittedCollapse(
                collapse_id=uuid.uuid4().hex,
                start_fp=span.start_fp,
                end_fp=span.end_fp,
                summary=summary,
                original_tokens=estimate_messages_tokens(list(span.payload)),
                summary_tokens=estimate_tokens(summary),
                committed_at=time.time(),
            )
            self.committed.append(commit)
            commits.append(commit)
        self.staged.clear()
        return commits

    # ── projection ─────────────────────────────────────────────

    def project_messages(self, messages: Sequence[Any]) -> list[Any]:
        """Apply every committed collapse to the message list.

        Single O(N) pass builds a {fp: first_index} map so each of the
        K committed collapses can look up its start/end boundaries in
        O(1). Total: O(N+K) instead of O(K*N) when committed spans
        accumulate.

        Messages whose fingerprints don't appear (e.g. already
        replaced by an earlier projection or dropped by snip_prepass)
        are skipped silently.
        """
        if not self.committed:
            return list(messages)
        from src.herald.types import Message as LLMMessage

        work = list(messages)
        fp_to_index: dict[str, int] = {}
        for i, msg in enumerate(work):
            fp = fingerprint(msg)
            if fp not in fp_to_index:
                fp_to_index[fp] = i

        # Apply commits in reverse index order so earlier splices
        # don't shift the indices of later ones. Collect resolved
        # ranges first, then sort by start_idx descending.
        resolved: list[tuple[int, int, CommittedCollapse]] = []
        for commit in self.committed:
            start_idx = fp_to_index.get(commit.start_fp)
            end_idx = fp_to_index.get(commit.end_fp)
            if start_idx is None or end_idx is None or end_idx < start_idx:
                continue
            resolved.append((start_idx, end_idx, commit))
        resolved.sort(key=lambda r: r[0], reverse=True)

        for start_idx, end_idx, commit in resolved:
            start_msg = work[start_idx]
            summary_msg = LLMMessage(
                role="user",
                content=commit.summary,
                timestamp=getattr(start_msg, "timestamp", None) or time.time(),
            )
            work = work[:start_idx] + [summary_msg] + work[end_idx + 1 :]
        return work

    def excluded_fingerprints(self, fps: list[str]) -> set[str]:
        """Return fingerprints of every message currently inside a
        committed or staged collapse span.

        Used by evaluate_and_stage's exclusion check and the
        staged-span dedup guard. Reuses the caller's pre-computed
        fingerprint list instead of re-hashing every message.
        """
        ranges: list[tuple[str, str]] = []
        ranges.extend((c.start_fp, c.end_fp) for c in self.committed)
        ranges.extend((s.start_fp, s.end_fp) for s in self.staged)
        if not ranges:
            return set()

        fp_to_index: dict[str, int] = {}
        for i, fp in enumerate(fps):
            if fp not in fp_to_index:
                fp_to_index[fp] = i

        inside: set[str] = set()
        for start_fp, end_fp in ranges:
            start_idx = fp_to_index.get(start_fp)
            end_idx = fp_to_index.get(end_fp)
            if start_idx is None or end_idx is None or end_idx < start_idx:
                continue
            for i in range(start_idx, end_idx + 1):
                inside.add(fps[i])
        return inside

    # ── reset ──────────────────────────────────────────────────

    def reset(self) -> None:
        """Drop all staged and committed state. Called after a full
        LLM compaction because the committed collapses are now folded
        into the compaction summary and would double-project if kept."""
        self.staged.clear()
        self.committed.clear()

    # ── persistence ────────────────────────────────────────────

    def snapshot_payload(self) -> dict:
        return {
            "staged": [
                {
                    "span_id": s.span_id,
                    "start_fp": s.start_fp,
                    "end_fp": s.end_fp,
                    "risk": s.risk,
                    "staged_at": s.staged_at,
                    # payload is intentionally dropped — it's only
                    # meaningful for in-session commits. After a
                    # restore the caller can re-stage from the live
                    # message list.
                }
                for s in self.staged
            ],
            "committed": [
                {
                    "collapse_id": c.collapse_id,
                    "start_fp": c.start_fp,
                    "end_fp": c.end_fp,
                    "summary": c.summary,
                    "original_tokens": c.original_tokens,
                    "summary_tokens": c.summary_tokens,
                    "committed_at": c.committed_at,
                }
                for c in self.committed
            ],
        }

    async def save_state(self, thread_id: str) -> None:
        """Write a last-wins snapshot and append new commit rows.

        No-ops when there is nothing to persist, so the orchestrator
        can call save_state every turn without forcing a SQL write on
        turns where nothing was staged or committed.

        Two-table pattern mirroring src's entry shapes:
          * context_collapse_snapshot: one row per task, overwritten.
          * context_collapse_commits: append-only log of committed
            collapses, useful for debugging and replay.
        """
        if self.db is None or not thread_id:
            return
        if not self.staged and not self.committed:
            return
        try:
            payload = json.dumps(self.snapshot_payload())
            await self.db.execute(
                "INSERT INTO context_collapse_snapshot "
                "(thread_id, snapshot_json, updated_at) "
                "VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(thread_id) DO UPDATE SET "
                "snapshot_json=excluded.snapshot_json, "
                "updated_at=excluded.updated_at",
                (thread_id, payload),
            )
            # Append new commits in one batch. The unique index
            # idx_context_collapse_commits_collapse_id (on
            # thread_id, collapse_id) makes INSERT OR IGNORE a safe
            # no-op for rows that already exist, so we don't need a
            # per-row existence check.
            if self.committed:
                await self.db.executemany(
                    "INSERT OR IGNORE INTO context_collapse_commits "
                    "(thread_id, collapse_id, start_fp, end_fp, summary, "
                    " original_tokens, summary_tokens, committed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                    [
                        (
                            thread_id,
                            c.collapse_id,
                            c.start_fp,
                            c.end_fp,
                            c.summary,
                            c.original_tokens,
                            c.summary_tokens,
                        )
                        for c in self.committed
                    ],
                )
            await self.db.commit()
        except Exception:
            logger.debug(
                "context_collapse: save_state failed (non-fatal)",
                exc_info=True,
            )

    async def restore_state(self, thread_id: str) -> None:
        """Reload last-wins snapshot for this thread_id, if any. Called
        once per session at the top of ensure_compactor so that a
        resumed session picks up where it left off."""
        if self.db is None or not thread_id:
            return
        try:
            row = await self.db.fetchrow(
                "SELECT snapshot_json FROM context_collapse_snapshot WHERE thread_id=?",
                (thread_id,),
            )
        except Exception:
            logger.debug(
                "context_collapse: restore_state read failed (non-fatal)",
                exc_info=True,
            )
            return
        if row is None:
            return
        try:
            payload = json.loads(row["snapshot_json"])
        except Exception:
            logger.debug(
                "context_collapse: restore_state parse failed (non-fatal)",
                exc_info=True,
            )
            return

        self.staged = [
            StagedSpan(
                span_id=s["span_id"],
                start_fp=s["start_fp"],
                end_fp=s["end_fp"],
                risk=float(s["risk"]),
                staged_at=float(s["staged_at"]),
                payload=(),
            )
            for s in payload.get("staged", [])
        ]
        self.committed = [
            CommittedCollapse(
                collapse_id=c["collapse_id"],
                start_fp=c["start_fp"],
                end_fp=c["end_fp"],
                summary=c["summary"],
                original_tokens=int(c["original_tokens"]),
                summary_tokens=int(c["summary_tokens"]),
                committed_at=float(c["committed_at"]),
            )
            for c in payload.get("committed", [])
        ]
