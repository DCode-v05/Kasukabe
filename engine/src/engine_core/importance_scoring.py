"""Message importance scoring for Context Collapse.

Every message in a conversation gets a score from 0.0 (fully expendable,
safe to collapse) to 1.0 (critical, must never be collapsed). Context
Collapse uses these scores to decide which spans of messages to stage
and commit as the context window fills.

The scorer is a pure function: deterministic, no I/O, no LLM calls.
This matches the mechanical philosophy of Context Collapse — no extra
model spend to decide what's collapsible.

Factors combined into the final score:
  * User (non-tool-result) messages are pinned to 1.0 — they are the
    intent of the conversation and must survive.
  * Assistant text blocks carry a moderate base score because they
    usually contain reasoning or user-facing answers.
  * Tool uses are scored by tool class:
      - Edit / Write / create / delete operations are higher risk
        because they represent state changes.
      - Read operations are lower risk — their contents can usually
        be re-fetched.
      - Search operations (Glob / Grep / ls-like) are the lowest
        risk — cheapest to re-run.
  * Short tool results (< 100 chars) score lower, since they're
    usually empty lists or single-line status output.
  * Messages containing error / exception keywords score higher —
    error recovery context is load-bearing for subsequent turns.
  * Recency adds a small bonus proportional to position — newer
    messages skew higher so the collapser prefers to shrink older
    spans first.
"""

from __future__ import annotations

from typing import Any

TOOL_CLASS_EDIT = {
    "Edit",
    "Write",
    "NotebookEdit",
    "file_edit",
    "file_write",
    "create",
    "delete",
    "move",
    "rename",
    "mkdir",
}
TOOL_CLASS_READ = {
    "Read",
    "file_read",
    "cat",
    "head",
    "tail",
    "NotebookRead",
    "WebFetch",
    "web_fetch",
}
TOOL_CLASS_SEARCH = {
    "Glob",
    "Grep",
    "ls",
    "list",
    "search",
    "search_content",
    "list_content",
    "find",
    "WebSearch",
    "web_search",
}

BASE_ASSISTANT_TEXT = 0.55
BASE_TOOL_USE_EDIT = 0.70
BASE_TOOL_USE_READ = 0.40
BASE_TOOL_USE_SEARCH = 0.25
BASE_TOOL_USE_OTHER = 0.50
BASE_TOOL_RESULT = 0.30

SHORT_TOOL_RESULT_CHARS = 100
SHORT_RESULT_MULTIPLIER = 0.7
ERROR_MULTIPLIER = 1.3
RECENCY_WEIGHT = 0.25

ERROR_KEYWORDS = (
    "error",
    "exception",
    "failed",
    "failure",
    "traceback",
    "stack trace",
    "fix",
    "bug",
    "crash",
    "denied",
)


def classify_tool(name: str) -> float:
    """Map a tool name to its base score."""
    if name in TOOL_CLASS_EDIT:
        return BASE_TOOL_USE_EDIT
    if name in TOOL_CLASS_READ:
        return BASE_TOOL_USE_READ
    if name in TOOL_CLASS_SEARCH:
        return BASE_TOOL_USE_SEARCH
    return BASE_TOOL_USE_OTHER


def has_error_keyword(text: str) -> bool:
    lower = text.lower()
    return any(k in lower for k in ERROR_KEYWORDS)


def contains_tool_result(content: Any) -> bool:
    from src.herald.types import ToolResultBlock

    if not isinstance(content, list):
        return False
    return any(isinstance(b, ToolResultBlock) for b in content)


def score_message_importance(
    message: Any,
    position: int,
    total: int,
) -> float:
    """Score a message from 0.0 (expendable) to 1.0 (critical).

    Pure function — safe to call on every message every turn. Used by
    ContextCollapseManager to decide which spans to stage for collapse.

    User messages (non-tool-result) are always 1.0 — they represent
    the user's intent and the collapser must never touch them.

    For every other message the score is the base score for the
    content type plus a small recency bonus, multiplied by an error
    keyword modifier, clamped to [0.0, 1.0].
    """
    from src.herald.types import TextBlock, ToolResultBlock, ToolUseBlock

    role = getattr(message, "role", "")
    content = getattr(message, "content", "")

    if role == "user" and not contains_tool_result(content):
        return 1.0

    if total > 1:
        recency_bonus = RECENCY_WEIGHT * (position / (total - 1))
    else:
        recency_bonus = 0.0

    base = 0.0
    has_tool_result = False
    tool_result_text_len = 0
    if isinstance(content, str):
        base = BASE_ASSISTANT_TEXT if role == "assistant" else BASE_TOOL_RESULT
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, TextBlock):
                base = max(base, BASE_ASSISTANT_TEXT)
            elif isinstance(block, ToolUseBlock):
                base = max(base, classify_tool(block.name))
            elif isinstance(block, ToolResultBlock):
                has_tool_result = True
                base = max(base, BASE_TOOL_RESULT)
                inner = block.content
                if isinstance(inner, str):
                    tool_result_text_len += len(inner)
                elif isinstance(inner, list):
                    for sub in inner:
                        if isinstance(sub, TextBlock):
                            tool_result_text_len += len(sub.text)
        if base == 0.0:
            base = BASE_TOOL_RESULT

    if has_tool_result and tool_result_text_len < SHORT_TOOL_RESULT_CHARS:
        base *= SHORT_RESULT_MULTIPLIER

    text_fn = getattr(message, "text", None)
    text = text_fn() if callable(text_fn) else ""
    modifier = ERROR_MULTIPLIER if text and has_error_keyword(text) else 1.0

    score = (base + recency_bonus) * modifier
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score
