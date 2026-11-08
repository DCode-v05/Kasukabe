"""Context Engineering — token budget enforcement, compaction, and guardrails.

Guardrail constants control context window management. Compaction fires at
COMPACTION_THRESHOLD (80% of window). Keeps last PRESERVE_RECENT (15) messages
verbatim. System prompt, tool schemas, and charter are never compacted.

Overflow recovery chain (graduated):
  1. Normal compaction (gut tool results in old messages)
  2. Tool result aggregate guard (compact oldest tool results)
  3. Emergency compaction (keep first + last 5, gut everything)
Each step is more aggressive. Pairing repair runs after every compaction
to fix orphaned ToolUse/ToolResult pairs.
"""

from __future__ import annotations

import logging
import os
import os.path
import re
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


SAFETY_MARGIN = 1.2
SEARCH_RESULTS_CAP = 10_000
TOOL_RESULT_CAP = 8_000
RESERVE_FLOOR = 25_000
COMPACTION_THRESHOLD = 0.80
PRESERVE_RECENT = 15
AD_TOOL_SCHEMA_CAP = 0.10
SUB_AGENT_RESULT_CAP = 500
TOOL_RESULTS_BUDGET_RATIO = 0.50
COMPACTION_OUTPUT_RESERVE = 4_000
PERSIST_TOKEN_THRESHOLD = 4_000
PER_TURN_TOOL_RESULTS_CAP = 0.25
PER_RESULT_MAX_CHARS = 40_000
SOURCE_TOOL_RESULT = "tool_result"

FEATURE_CACHE_STABLE_REPLACEMENTS = "FEATURE_CACHE_STABLE_REPLACEMENTS"
FEATURE_CACHE_STABLE_REPLACEMENTS_DEFAULT = "true"


def cache_stable_replacements_enabled() -> bool:
    return (
        os.environ.get(FEATURE_CACHE_STABLE_REPLACEMENTS, FEATURE_CACHE_STABLE_REPLACEMENTS_DEFAULT)
        .strip()
        .lower()
        == "true"
    )


class ContentReplacementState:
    """Tracks tool result replacement decisions for prompt cache stability.

    Once a tool result is replaced, the exact replacement text is cached.
    On subsequent turns, the same text is reused byte-for-byte so the
    prompt prefix stays stable for prompt caching. Once a tool result is
    "seen" but NOT replaced, it's frozen — never replaced later (changing
    decisions mid-session would shift the prefix and break cache).

    Matches src's ContentReplacementState at toolResultStorage.ts:390-397.
    """

    def __init__(self) -> None:
        self.seen_ids: set[str] = set()
        self.replacements: dict[str, str] = {}

    def get_cached_replacement(self, tool_use_id: str) -> str | None:
        return self.replacements.get(tool_use_id)

    def is_frozen(self, tool_use_id: str) -> bool:
        return tool_use_id in self.seen_ids and tool_use_id not in self.replacements

    def record_replacement(self, tool_use_id: str, replacement: str) -> None:
        self.seen_ids.add(tool_use_id)
        self.replacements[tool_use_id] = replacement

    def mark_seen(self, tool_use_id: str) -> None:
        self.seen_ids.add(tool_use_id)


COMPACT_PROMPT = """\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use file_read, bash, file_write, file_edit, grep, glob, search_content, \
list_content, web_search, web_fetch, ask_user, spawn_sub_agent, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

Your task is to create a detailed summary of the conversation so far, paying close \
attention to the user's explicit requests and your previous actions. This summary should \
be thorough in capturing technical details, code patterns, and architectural decisions \
that would be essential for continuing development work without losing context.

Before providing your final summary, wrap your analysis in <analysis> tags to organize \
your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each \
section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially \
if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required \
element thoroughly.

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents \
in detail.
2. Key Technical Concepts: List all important technical concepts, technologies, and \
frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, \
modified, or created. Pay special attention to the most recent messages and include \
full code snippets where applicable and include a summary of why this file read or \
edit is important.
4. Errors and Fixes: List all errors that you ran into, and how you fixed them. Pay \
special attention to specific user feedback that you received, especially if the user \
told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All User Messages: List ALL user messages that are not tool results. These are \
critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to \
work on.
8. Current Work: Describe in detail precisely what was being worked on immediately \
before this summary request, paying special attention to the most recent messages from \
both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the \
most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line \
with the user's most recent explicit requests, and the task you were working on \
immediately before this summary request. If your last task was concluded, then only \
list next steps if they are explicitly in line with the users request. Do not start on \
tangential requests or really old requests that were already completed without \
confirming with the user first.
   If there is a next step, include direct quotes from the most recent conversation \
showing exactly what task you were working on and where you left off. This should be \
verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and Fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All User Messages:
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, following this structure \
and ensuring precision and thoroughness in your response.

Note: Large tool results have been persisted to the content store. \
The agent can use search_content(pattern) or list_content(source='tool_result') \
to retrieve full content from previous tool executions without re-running tools.

REMINDER: Do NOT call any tools. Respond with plain text only — an <analysis> block \
followed by a <summary> block. Tool calls will be rejected and you will fail the task."""

COMPACTABLE_TOOLS = frozenset(
    {
        "read_file",
        "bash",
        "python",
        "write_file",
        "list_directory",
        "search_content",
        "grep",
        "glob",
        "web_search",
        "web_fetch",
        "file_read",
        "file_write",
        "file_edit",
    }
)
RETRIEVAL_HINT = f'use search_content or list_content(source="{SOURCE_TOOL_RESULT}") to retrieve'
CONTENT_ID_RE = re.compile(r"Persisted as (tr_[a-zA-Z0-9]+)")


def persist_note(content: str, content_id: str = "") -> str:
    """Build a persist-note suffix from an explicit content_id or by parsing content."""
    if content_id:
        return f" Persisted as {content_id} — {RETRIEVAL_HINT}."
    match = CONTENT_ID_RE.search(content)
    if match:
        return f" Persisted as {match.group(1)} — {RETRIEVAL_HINT}."
    return ""


SNIPPABLE_TOOLS = frozenset(
    {
        "file_read",
        "search_content",
        "list_content",
        "grep",
        "glob",
        "web_search",
        "web_fetch",
    }
)

WRITE_TOOLS = frozenset({"file_write", "file_edit"})


MEDIA_TYPES = frozenset({"image", "document"})


def strip_media_from_messages(messages: list) -> list:
    """Replace image/document content blocks with text markers before compaction.

    Handles both dict-based blocks (type="image"/"document") and ImageBlock
    dataclass instances, including those nested inside ToolResultBlock.content.
    Does not mutate the input --- returns a new list with new Message objects
    only where changes were needed.
    """
    from src.herald.types import (
        Message as LLMMessage,
    )

    new_messages = []
    any_changed = False

    for msg in messages:
        content = getattr(msg, "content", "")
        if not isinstance(content, list):
            new_messages.append(msg)
            continue

        new_blocks, msg_changed = strip_media_blocks(content, MEDIA_TYPES)

        if msg_changed:
            new_messages.append(
                LLMMessage(
                    role=msg.role,
                    content=new_blocks,
                    timestamp=msg.timestamp,
                    metadata=msg.metadata,
                    api_message_id=msg.api_message_id,
                )
            )
            any_changed = True
        else:
            new_messages.append(msg)

    return new_messages if any_changed else messages


def strip_media_blocks(
    blocks: list,
    media_types: frozenset[str],
) -> tuple[list, bool]:
    """Replace media blocks with text markers, recursing into ToolResultBlock."""
    from src.herald.types import ImageBlock, TextBlock, ToolResultBlock

    new_blocks: list = []
    changed = False

    for block in blocks:
        if isinstance(block, dict) and block.get("type") in media_types:
            new_blocks.append(TextBlock(text=f"[{block.get('type', 'media')}]"))
            changed = True
        elif isinstance(block, ImageBlock):
            new_blocks.append(TextBlock(text="[image]"))
            changed = True
        elif isinstance(block, ToolResultBlock) and isinstance(block.content, list):
            inner_blocks, inner_changed = strip_media_blocks(block.content, media_types)
            if inner_changed:
                new_blocks.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=inner_blocks,
                        is_error=block.is_error,
                    )
                )
                changed = True
            else:
                new_blocks.append(block)
        else:
            new_blocks.append(block)

    return new_blocks, changed


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token, with 1.2x safety margin."""
    return max(1, int(len(text) / 4 * SAFETY_MARGIN))


def estimate_tokens_dense(text: str) -> int:
    """Token estimate for code/JSON content (tool results).

    Tool results are denser than English — JSON keys, code syntax consume
    more tokens per character. Uses chars/2 ratio (vs chars/4 for text).
    """
    return max(1, int(len(text) / 2 * SAFETY_MARGIN))


def estimate_system_tokens(system_prompt: "list | None") -> int:
    """Estimate total tokens for a structured system prompt (list of SystemPromptBlock).

    Tolerates None, empty list, and empty string (from test fixtures and
    legacy callers during migration) — returns 0 for all falsy inputs.
    """
    if not system_prompt or isinstance(system_prompt, str):
        return 0
    return sum(estimate_tokens(b.text) for b in system_prompt if b.text)


def block_content_text(block: Any) -> str:
    """Extract a flat text view of a ToolResultBlock's content.

    ``ToolResultBlock.content`` is ``str | list[ContentBlock]`` since
    the post-tool injection pipeline landed. Most context_engineering
    code reads it for size estimation or string substitution and
    doesn't care about the block structure — this helper hides the
    union and returns concatenated text. Image blocks contribute 0;
    persisted blocks contribute their summary text.

    Lives here (not on ToolResultBlock itself) so the type stays a
    pure data dataclass with no engine-side smarts.
    """
    from src.herald.types import ImageBlock, PersistedBlock, TextBlock

    content = block.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for sub in content:
        if isinstance(sub, TextBlock):
            parts.append(sub.text)
        elif isinstance(sub, PersistedBlock):
            parts.append(sub.summary)
        elif isinstance(sub, ImageBlock):
            pass
    return "\n\n".join(parts)


def estimate_messages_tokens(messages: list) -> int:
    """Estimate total tokens across a message list.

    Uses dense estimation (chars/2) for ToolResultBlock content since
    tool results contain code/JSON which is ~2x denser than English text.
    """
    from src.herald.types import ToolResultBlock

    total = 0
    for msg in messages:
        if isinstance(msg, dict):
            content = msg.get("content", "")
        else:
            content = getattr(msg, "content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, ToolResultBlock):
                    total += estimate_tokens_dense(block_content_text(block))
                elif isinstance(block, dict):
                    total += estimate_tokens(str(block.get("text", block.get("content", ""))))
                else:
                    total += estimate_tokens(
                        str(getattr(block, "text", getattr(block, "content", "")))
                    )
    return total


TAIL_PATTERNS = (
    "error",
    "exception",
    "failed",
    "traceback",
    "panic",
    "fatal",
    "stack trace",
    "stderr",
    "exit code",
)


def has_important_tail(text: str) -> bool:
    """Check if the last 2000 chars contain error patterns or closing structure."""
    tail = text[-2000:].lower()
    return any(p in tail for p in TAIL_PATTERNS)


def cap_tool_result(result: str, content_id: str = "") -> str:
    """Cap a tool result to TOOL_RESULT_CAP tokens with head+tail preservation.

    If the tail contains error patterns or closing structure (stack traces,
    JSON closing braces), keeps 70% head + 30% tail. Otherwise head-only.
    Uses dense token estimation (chars/2) since tool results are code/JSON.

    If content_id is provided, the truncation note includes a retrieval hint
    so the agent can recover the full content from the content store.
    """
    if estimate_tokens_dense(result) <= TOOL_RESULT_CAP:
        return result

    cap_chars = int(TOOL_RESULT_CAP / SAFETY_MARGIN * 2)
    note = persist_note(result, content_id=content_id)

    if has_important_tail(result):
        head_budget = int(cap_chars * 0.70)
        tail_budget = int(cap_chars * 0.30)
        head = result[:head_budget]
        tail = result[-tail_budget:]
        return head + f"\n\n... [middle truncated.{note}] ...\n\n" + tail

    return result[:cap_chars] + f"\n\n[Result truncated.{note}]"


def cap_turn_tool_results(
    tool_results: list,
    context_window: int = 200_000,
    tool_names: dict[str, str] | None = None,
) -> list:
    """Cap aggregate tool results for a single turn.

    When multiple tools run in parallel, their combined results can blow
    the context budget in one turn. Only trims results from COMPACTABLE_TOOLS.
    Error results and results from sensitive tools (ask_user, etc.) are never trimmed.
    """
    from src.herald.types import ToolResultBlock

    budget_tokens = int(context_window * PER_TURN_TOOL_RESULTS_CAP)
    name_map = tool_names or {}

    entries: list[tuple[int, int, ToolResultBlock]] = []
    for i, block in enumerate(tool_results):
        if not isinstance(block, ToolResultBlock) or block.is_error:
            continue
        name = name_map.get(block.tool_use_id, "")
        if name and name not in COMPACTABLE_TOOLS:
            continue
        tokens = estimate_tokens_dense(block_content_text(block))
        entries.append((i, tokens, block))

    total_tokens = sum(t for _, t, _ in entries)
    if total_tokens <= budget_tokens:
        return tool_results

    entries_sorted = sorted(entries, key=lambda e: e[1], reverse=True)
    excess = total_tokens - budget_tokens
    new_results = list(tool_results)

    trimmed_count = 0
    for idx, tokens, block in entries_sorted:
        if excess <= 0:
            break

        trim_tokens = min(tokens - 100, excess)
        if trim_tokens <= 0:
            continue

        target_chars = max(200, (tokens - trim_tokens) * 2)
        block_text = block_content_text(block)
        note = persist_note(block_text)

        truncated = block_text[:target_chars]
        truncated += f"\n\n[Aggregate tool result budget exceeded — result trimmed.{note}]"

        new_results[idx] = ToolResultBlock(
            tool_use_id=block.tool_use_id,
            content=truncated,
            is_error=block.is_error,
        )
        excess -= trim_tokens
        trimmed_count += 1

    logger.info(
        "Per-turn tool result budget: %d -> %d tokens (budget=%d, trimmed %d results)",
        total_tokens,
        budget_tokens + max(excess, 0),
        budget_tokens,
        trimmed_count,
    )
    return new_results


def cap_sub_agent_result(result: str) -> str:
    """Cap a sub-agent's result summary to SUB_AGENT_RESULT_CAP tokens."""
    tokens = estimate_tokens(result)
    if tokens <= SUB_AGENT_RESULT_CAP:
        return result

    char_limit = SUB_AGENT_RESULT_CAP * 4
    truncated = result[:char_limit]
    return truncated + "..."


def cap_search_results(items: list[dict], key: str = "description") -> list[dict]:
    """Return search results up to SEARCH_RESULTS_CAP total tokens."""
    result = []
    total = 0
    for item in items:
        text = str(item.get(key, ""))
        tokens = estimate_tokens(text)
        if total + tokens > SEARCH_RESULTS_CAP:
            break
        result.append(item)
        total += tokens
    return result


def filter_ad_tools_by_budget(
    tools: list[dict[str, Any]],
    context_window: int = 200_000,
    relevance_scores: dict[str, float] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split AD tools into loaded (full schema) and deferred (name-only).

    Total loaded AD tool tokens must not exceed AD_TOOL_SCHEMA_CAP (10%) of
    the context window. Lowest-relevance tools are deferred first.

    Returns (loaded_tools, deferred_tools).
    """
    cap_tokens = int(context_window * AD_TOOL_SCHEMA_CAP)

    if relevance_scores:
        tools = sorted(
            tools, key=lambda t: relevance_scores.get(t.get("name", ""), 0.0), reverse=True
        )

    loaded = []
    deferred = []
    total = 0

    for tool in tools:
        schema_text = str(tool.get("input_schema", {})) + tool.get("description", "")
        tokens = estimate_tokens(schema_text)
        if total + tokens <= cap_tokens:
            loaded.append(tool)
            total += tokens
        else:
            deferred.append(tool)

    if deferred:
        logger.info(
            "AD tool budget: %d loaded (%d tokens), %d deferred (cap=%d tokens)",
            len(loaded),
            total,
            len(deferred),
            cap_tokens,
        )

    return loaded, deferred


FILE_PATH_RE = re.compile(r'(/[^\s"\',:)\]]+)')


def extract_file_path(tool_name: str, tool_input: dict) -> str | None:
    """Extract and normalize file path from a tool's input dict."""
    path = None
    if tool_name in ("file_read", "file_write", "file_edit"):
        path = tool_input.get("file_path") or tool_input.get("path")
    elif tool_name in ("grep", "glob"):
        path = tool_input.get("path")
    elif tool_name in ("web_search", "web_fetch"):
        path = tool_input.get("url")
    if path and not path.startswith(("http://", "https://")):
        path = os.path.normpath(path)
    return path


ToolPairEntry = tuple[int, str, str, dict, str]
WriteEvent = tuple[int, str, str]


def build_tool_index(messages: list) -> tuple[list[ToolPairEntry], list[WriteEvent]]:
    """Single-pass index of all tool pairs and write events in the conversation.

    Returns (tool_pairs, write_events) to avoid repeated message walks.
    """
    from src.herald.types import ToolUseBlock, ToolResultBlock

    use_map: dict[str, tuple[str, dict]] = {}
    write_events: list[WriteEvent] = []

    for i, msg in enumerate(messages):
        if not isinstance(msg.content, list):
            continue
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    use_map[block.id] = (block.name, block.input)
                    if block.name in WRITE_TOOLS:
                        file_path = extract_file_path(block.name, block.input)
                        if file_path:
                            write_events.append((i, block.name, file_path))

    pairs: list[ToolPairEntry] = []
    for i, msg in enumerate(messages):
        if not isinstance(msg.content, list) or msg.role != "user":
            continue
        for block in msg.content:
            if isinstance(block, ToolResultBlock) and block.tool_use_id in use_map:
                name, inp = use_map[block.tool_use_id]
                pairs.append((i, block.tool_use_id, name, inp, block_content_text(block)))

    return pairs, write_events


def find_duplicate_reads(pairs: list[ToolPairEntry]) -> set[tuple[int, str]]:
    """Same file_path read twice → keep latest, snip earlier occurrence."""
    to_snip: set[tuple[int, str]] = set()
    seen: dict[str, tuple[int, str]] = {}

    for msg_idx, tool_use_id, tool_name, tool_input, result_content in pairs:
        if tool_name != "file_read" or is_compacted_stub(result_content):
            continue
        file_path = extract_file_path(tool_name, tool_input)
        if not file_path:
            continue
        if file_path in seen:
            to_snip.add(seen[file_path])
        seen[file_path] = (msg_idx, tool_use_id)

    return to_snip


def find_superseded_reads(
    pairs: list[ToolPairEntry],
    write_events: list[WriteEvent],
) -> set[tuple[int, str]]:
    """Find file reads superseded by a write then re-read.

    Pattern: read(file) at i → write/edit(file) at j → read(file) at k.
    The read at i is stale because the file changed.
    """
    to_snip: set[tuple[int, str]] = set()
    timelines: dict[str, list[tuple[int, str | None, str]]] = {}

    for msg_idx, tool_use_id, tool_name, tool_input, result_content in pairs:
        if tool_name == "file_read" and not is_compacted_stub(result_content):
            file_path = extract_file_path(tool_name, tool_input)
            if file_path:
                timelines.setdefault(file_path, []).append((msg_idx, tool_use_id, "read"))

    for msg_idx, _, file_path in write_events:
        timelines.setdefault(file_path, []).append((msg_idx, None, "write"))

    for _, events in timelines.items():
        events.sort(key=lambda e: e[0])
        for idx, (msg_idx, read_tu_id, action) in enumerate(events):
            if action != "read" or read_tu_id is None:
                continue
            found_write = False
            for future_idx in range(idx + 1, len(events)):
                _, _, future_action = events[future_idx]
                if future_action == "write":
                    found_write = True
                elif future_action == "read" and found_write:
                    to_snip.add((msg_idx, read_tu_id))
                    break

    return to_snip


def find_search_then_read(pairs: list[ToolPairEntry]) -> set[tuple[int, str]]:
    """Find search results whose target files were all subsequently read.

    grep/glob returned file paths → all those files were later file_read →
    the search result is redundant.
    """
    to_snip: set[tuple[int, str]] = set()
    read_paths: dict[str, int] = {}
    search_results: list[tuple[int, str, str]] = []

    for msg_idx, tool_use_id, tool_name, tool_input, result_content in pairs:
        if tool_name == "file_read" and not is_compacted_stub(result_content):
            file_path = extract_file_path(tool_name, tool_input)
            if file_path:
                if file_path not in read_paths or msg_idx > read_paths[file_path]:
                    read_paths[file_path] = msg_idx
        elif tool_name in ("grep", "glob") and not is_compacted_stub(result_content):
            search_results.append((msg_idx, tool_use_id, result_content))

    for search_msg_idx, tool_use_id, result_content in search_results:
        extracted_paths = set()
        for match in FILE_PATH_RE.finditer(result_content):
            path = os.path.normpath(match.group(1))
            if len(path) > 2 and not path.startswith("/proc"):
                extracted_paths.add(path)

        if not extracted_paths:
            continue

        all_read = all((read_paths.get(p) or -1) > search_msg_idx for p in extracted_paths)
        if all_read:
            to_snip.add((search_msg_idx, tool_use_id))

    return to_snip


def find_stale_reads(
    pairs: list[ToolPairEntry],
    msg_count: int,
) -> set[tuple[int, str]]:
    """Find read-only tool results from the oldest 50% of messages.

    Only applies to conversations with at least PRESERVE_RECENT messages.
    """
    if msg_count < PRESERVE_RECENT:
        return set()

    to_snip: set[tuple[int, str]] = set()
    midpoint = msg_count // 2

    for msg_idx, tool_use_id, tool_name, tool_input, result_content in pairs:
        if msg_idx >= midpoint:
            continue
        if tool_name not in SNIPPABLE_TOOLS:
            continue
        if is_compacted_stub(result_content):
            continue
        to_snip.add((msg_idx, tool_use_id))

    return to_snip


FEATURE_API_ROUND_ID_GROUPING = "FEATURE_API_ROUND_ID_GROUPING"
FEATURE_API_ROUND_ID_GROUPING_DEFAULT = "true"


def api_round_id_grouping_enabled() -> bool:
    return (
        os.environ.get(FEATURE_API_ROUND_ID_GROUPING, FEATURE_API_ROUND_ID_GROUPING_DEFAULT)
        .strip()
        .lower()
        == "true"
    )


def group_by_api_round(messages: list) -> list[list]:
    """Group messages by API round boundaries.

    When FEATURE_API_ROUND_ID_GROUPING is enabled (default) and assistant
    messages carry api_message_id, uses the assistant-id algorithm: a new
    round starts when an assistant message has a different api_message_id
    from the previous assistant. Streaming chunks and parallel tool calls
    from one API response share an ID and stay in one group. This matches
    the src reference at services/compact/grouping.ts.

    Falls back to role-transition grouping when the feature flag is disabled
    or when no messages carry api_message_id.

    API contract assumption: Anthropic guarantees every tool_use is resolved
    before the next assistant turn, so boundary detection is API-safe.

    Malformed input handling: dangling tool_use (after resume-from-partial
    or max_tokens truncation) is NOT repaired here. We let boundaries fire
    naturally; repair_tool_use_result_pairing at the API call site repairs
    the dangling tu. Tracking unresolved tool_use IDs would pin gates shut
    forever, merging all subsequent rounds — strictly worse.
    """
    if not messages:
        return []

    if api_round_id_grouping_enabled():
        result = group_by_message_id(messages)
        if result is not None:
            return result

    return group_by_role_transition(messages)


def group_by_message_id(messages: list) -> list[list] | None:
    """Group by api_message_id. Returns None if no messages carry IDs.

    Mirrors src at services/compact/grouping.ts:22-63. The assistant
    message ID is the sole boundary gate: streaming chunks from the same
    API response share an ID, so boundaries only fire at the start of a
    genuinely new round. Parallel tool calls and their results stay in
    one group because they share the originating assistant's ID.

    When both last_assistant_id and msg_id are available, uses id
    comparison (primary). When either is missing, falls back to
    role-transition within this path (hybrid).

    Does NOT track unresolved tool_use IDs for malformed input — that
    would pin gates shut forever, merging all subsequent rounds into one
    group (see repair_tool_use_result_pairing for downstream repair).
    """
    has_ids = any(
        getattr(m, "api_message_id", None)
        for m in messages
        if getattr(m, "role", "") == "assistant"
    )
    if not has_ids:
        return None

    groups: list[list] = []
    current: list = [messages[0]]
    last_assistant_id: str | None = None
    if getattr(messages[0], "role", "") == "assistant":
        last_assistant_id = getattr(messages[0], "api_message_id", None)
    prev_role = getattr(messages[0], "role", "")

    for msg in messages[1:]:
        role = getattr(msg, "role", "")
        msg_id = getattr(msg, "api_message_id", None)

        is_boundary = False
        if role == "assistant":
            if last_assistant_id is not None and msg_id is not None:
                if msg_id != last_assistant_id:
                    is_boundary = True
            else:
                if prev_role == "user":
                    is_boundary = True

        if is_boundary and current:
            groups.append(current)
            current = [msg]
        else:
            current.append(msg)

        prev_role = role
        if role == "assistant":
            last_assistant_id = msg_id

    if current:
        groups.append(current)

    return groups


def group_by_role_transition(messages: list) -> list[list]:
    """Original role-transition grouping (preserved as fallback)."""
    groups: list[list] = []
    current: list = [messages[0]]
    prev_role = getattr(messages[0], "role", "")

    for msg in messages[1:]:
        role = getattr(msg, "role", "")
        if role == "assistant" and prev_role == "user":
            groups.append(current)
            current = [msg]
        else:
            current.append(msg)
        prev_role = role

    if current:
        groups.append(current)

    return groups


def snip_prepass(messages: list) -> list:
    """Surgical pre-pass: remove provably redundant tool results.

    Does NOT mutate input. Returns new message list.
    Only touches SNIPPABLE_TOOLS. Never guts write results, user responses,
    or checkpoint data.

    Four snip strategies:
    1. Duplicate file reads: same file_path read twice → keep latest
    2. Superseded reads: file read → file edited → file read again → gut pre-edit read
    3. Search-then-read: grep found files, those files were then read → gut grep result
    4. Stale reads: read-only tool results from >50% of messages ago
    """
    from src.herald.types import Message as LLMMessage, ToolResultBlock, ContentBlock

    if len(messages) < 4:
        return messages

    pairs, write_events = build_tool_index(messages)
    if not pairs:
        return messages

    to_snip: set[tuple[int, str]] = set()
    to_snip |= find_duplicate_reads(pairs)
    to_snip |= find_superseded_reads(pairs, write_events)
    to_snip |= find_search_then_read(pairs)
    to_snip |= find_stale_reads(pairs, len(messages))

    if not to_snip:
        return messages

    snip_by_msg: dict[int, set[str]] = {}
    for msg_idx, tool_use_id in to_snip:
        snip_by_msg.setdefault(msg_idx, set()).add(tool_use_id)

    new_messages = []
    for i, msg in enumerate(messages):
        if i not in snip_by_msg or not isinstance(msg.content, list):
            new_messages.append(msg)
            continue

        ids_to_snip = snip_by_msg[i]
        new_blocks: list[ContentBlock] = []
        changed = False
        for block in msg.content:
            if isinstance(block, ToolResultBlock) and block.tool_use_id in ids_to_snip:
                stub = extraction_stub(block_content_text(block))
                new_blocks.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=stub,
                        is_error=block.is_error,
                    )
                )
                changed = True
            else:
                new_blocks.append(block)

        if changed:
            new_messages.append(
                LLMMessage(
                    role=msg.role,
                    content=new_blocks,
                    timestamp=msg.timestamp,
                    api_message_id=msg.api_message_id,
                )
            )
        else:
            new_messages.append(msg)

    logger.debug("Snip pre-pass: removed %d redundant tool results", len(to_snip))
    return new_messages


def needs_compaction(
    used_tokens: int,
    context_window: int = 200_000,
) -> bool:
    """Check if compaction should fire based on token usage."""
    return used_tokens >= int(context_window * COMPACTION_THRESHOLD)


COMPACTED_MARKERS = ("[trimmed]", "[compacted]", "[Tool result compacted.")


def is_compacted_stub(content: str) -> bool:
    """Check if a tool result has already been compacted/gutted."""
    return any(content.startswith(m) for m in COMPACTED_MARKERS)


def shallow_copy_messages(messages: list) -> list:
    """Shallow-copy a message list so block-level mutations don't affect the caller.

    Rebuilds only messages with list content (structured blocks) so that
    individual block replacements in compaction don't mutate the original.
    Preserves all Message fields including api_message_id.
    """
    from src.herald.types import Message as LLMMessage

    messages = list(messages)
    for k, msg in enumerate(messages):
        if isinstance(msg.content, list):
            messages[k] = LLMMessage(
                role=msg.role,
                content=list(msg.content),
                timestamp=msg.timestamp,
                api_message_id=msg.api_message_id,
            )
    return messages


def extraction_stub(content: str) -> str:
    """Build a compaction stub, preserving content_id if the result was persisted."""
    note = persist_note(content)
    if note:
        return f"[Tool result compacted.{note.rstrip('.')} full content.]"
    return "[trimmed]"


def gut_tool_results(message: Any) -> Any:
    """Replace ToolResultBlock.content with a retrieval stub in a user message.

    If the tool result was previously persisted (contains a content_id),
    the stub includes the ID so the agent can recover the data.
    """
    from src.herald.types import Message as LLMMessage, ToolResultBlock, ContentBlock

    if isinstance(message.content, str):
        return message

    new_blocks: list[ContentBlock] = []
    changed = False
    for block in message.content:
        if isinstance(block, ToolResultBlock):
            stub = extraction_stub(block_content_text(block))
            new_blocks.append(
                ToolResultBlock(
                    tool_use_id=block.tool_use_id,
                    content=stub,
                    is_error=block.is_error,
                )
            )
            changed = True
        else:
            new_blocks.append(block)

    if not changed:
        return message
    return LLMMessage(
        role=message.role,
        content=new_blocks,
        timestamp=message.timestamp,
        api_message_id=message.api_message_id,
    )


def compact_messages(
    messages: list,
    context_window: int = 200_000,
    preserve_recent: int = PRESERVE_RECENT,
) -> list:
    """Token-budget-aware compaction for agent loop messages.

    Fires when used tokens exceed COMPACTION_THRESHOLD. Keeps last
    PRESERVE_RECENT messages verbatim. Guts tool results in older messages,
    then drops oldest pairs if still over budget.

    System prompt, tool schemas, and charter are NOT in the messages list —
    they're handled separately and never compacted.
    """
    used = estimate_messages_tokens(messages)
    threshold = int(context_window * COMPACTION_THRESHOLD)

    if used < threshold:
        return messages

    if len(messages) <= preserve_recent:
        return messages

    old_messages = messages[:-preserve_recent] if preserve_recent > 0 else messages
    recent_messages = messages[-preserve_recent:] if preserve_recent > 0 else []

    gutted = []
    for msg in old_messages:
        gutted.append(gut_tool_results(msg))

    compacted = gutted + recent_messages

    used_after = estimate_messages_tokens(compacted)
    if used_after < threshold:
        logger.debug("Compaction pass-1: gutted tool results, %d -> %d tokens", used, used_after)
        return compacted

    while len(compacted) > preserve_recent + 1 and estimate_messages_tokens(compacted) >= threshold:
        if len(compacted) > 1:
            compacted.pop(1)
        else:
            break

    logger.debug(
        "Compaction pass-2: dropped old messages, %d -> %d tokens, %d messages remain",
        used,
        estimate_messages_tokens(compacted),
        len(compacted),
    )

    return compacted


def emergency_compact(
    messages: list,
    context_window: int = 200_000,
    preserve_recent: int = 5,
) -> list:
    """Aggressive compaction for overflow recovery. Keeps only 5 recent messages.

    Three passes:
      1. Gut ALL tool results (not just old ones)
      2. Keep only first message + last 5
      3. Truncate individual oversized messages (>25% of context)
    Always followed by pairing repair.
    """
    from src.herald.types import Message as LLMMessage

    gutted = [gut_tool_results(msg) for msg in messages]

    groups = group_by_api_round(gutted)
    if len(groups) > 2:
        trailing: list = []
        for g in reversed(groups[1:]):
            trailing = g + trailing
            if len(trailing) >= preserve_recent:
                break
        gutted = groups[0] + trailing
    elif len(gutted) > preserve_recent + 1:
        gutted = [gutted[0]] + gutted[-preserve_recent:]

    max_per_msg = context_window // 4
    final = []
    for msg in gutted:
        msg_tokens = estimate_messages_tokens([msg])
        if msg_tokens > max_per_msg:
            if isinstance(msg.content, str):
                chars = int(max_per_msg / SAFETY_MARGIN * 4)
                final.append(
                    LLMMessage(
                        role=msg.role,
                        content=msg.content[:chars] + "\n[Message truncated for context budget]",
                        api_message_id=msg.api_message_id,
                    )
                )
            else:
                from src.herald.types import TextBlock

                text_parts = []
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text[:1000])
                summary = (
                    " ".join(text_parts)[:2000]
                    + "\n[Structured message truncated for context budget]"
                )
                final.append(
                    LLMMessage(
                        role=msg.role,
                        content=summary,
                        timestamp=msg.timestamp,
                        api_message_id=msg.api_message_id,
                    )
                )
        else:
            final.append(msg)

    final = repair_tool_use_result_pairing(final)

    logger.info("Emergency compaction: %d -> %d messages", len(messages), len(final))
    return final


def repair_tool_use_result_pairing(messages: list) -> list:
    """Fix orphaned ToolUseBlocks and ToolResultBlocks after message drops.

    Uses per-pair processing (assistant[i] vs user[i+1]) matching the reference
    implementation. For each assistant message, inspects the following user message
    to detect missing/orphaned/duplicate tool results and patches in place.

    Handles:
      - ToolUseBlocks without a matching ToolResultBlock -> synthetic result prepended into adjacent user msg
      - ToolResultBlocks without a matching ToolUseBlock -> stripped from adjacent user msg
      - Duplicate tool_use IDs across messages -> later duplicates stripped
      - Duplicate tool_result IDs -> later duplicates stripped
      - Orphaned tool_results with no preceding assistant -> stripped (any position, not just index 0)
      - Empty content arrays after stripping -> placeholder inserted (preserves role alternation)
    """
    from src.herald.types import (
        Message as LLMMessage,
        ToolUseBlock,
        ToolResultBlock,
        TextBlock,
        ContentBlock,
    )

    if not messages:
        return messages

    result: list = []
    repaired = False
    all_seen_use_ids: set[str] = set()
    i = 0

    while i < len(messages):
        msg = messages[i]

        if msg.role != "assistant":
            if msg.role == "user" and isinstance(msg.content, list):
                last_in_result = result[-1] if result else None
                has_tool_results = any(isinstance(b, ToolResultBlock) for b in msg.content)

                if has_tool_results and (
                    last_in_result is None or last_in_result.role != "assistant"
                ):
                    stripped = [b for b in msg.content if not isinstance(b, ToolResultBlock)]
                    if stripped:
                        result.append(
                            LLMMessage(
                                role="user",
                                content=stripped,
                                timestamp=msg.timestamp,
                                api_message_id=msg.api_message_id,
                            )
                        )
                    elif not result:
                        result.append(
                            LLMMessage(
                                role="user",
                                content=[
                                    TextBlock(
                                        text="[Orphaned tool result removed due to conversation resume]"
                                    )
                                ],
                                timestamp=time.time(),
                            )
                        )
                    repaired = True
                    i += 1
                    continue

            result.append(msg)
            i += 1
            continue

        if isinstance(msg.content, list):
            per_msg_use_ids: set[str] = set()
            final_content = []
            had_dedup = False
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    if block.id in all_seen_use_ids:
                        repaired = True
                        had_dedup = True
                        continue
                    all_seen_use_ids.add(block.id)
                    per_msg_use_ids.add(block.id)
                final_content.append(block)

            if not final_content:
                final_content = [TextBlock(text="[Tool use interrupted]")]

            assistant_msg = (
                LLMMessage(
                    role=msg.role,
                    content=final_content,
                    timestamp=msg.timestamp,
                    api_message_id=msg.api_message_id,
                )
                if had_dedup
                else msg
            )
        else:
            assistant_msg = msg
            per_msg_use_ids = set()

        result.append(assistant_msg)

        if not per_msg_use_ids:
            i += 1
            continue

        next_msg = messages[i + 1] if i + 1 < len(messages) else None
        existing_result_ids: set[str] = set()
        has_duplicate_results = False

        if next_msg is not None and next_msg.role == "user" and isinstance(next_msg.content, list):
            for block in next_msg.content:
                if isinstance(block, ToolResultBlock):
                    if block.tool_use_id in existing_result_ids:
                        has_duplicate_results = True
                    existing_result_ids.add(block.tool_use_id)

        missing_ids = [uid for uid in per_msg_use_ids if uid not in existing_result_ids]
        orphaned_ids = {rid for rid in existing_result_ids if rid not in per_msg_use_ids}

        if not missing_ids and not orphaned_ids and not has_duplicate_results:
            i += 1
            continue

        repaired = True
        synthetic_blocks: list[ContentBlock] = [
            ToolResultBlock(
                tool_use_id=uid,
                content="[Tool result missing \u2014 execution interrupted]",
                is_error=True,
            )
            for uid in missing_ids
        ]

        if next_msg is not None and next_msg.role == "user" and isinstance(next_msg.content, list):
            content = list(next_msg.content)

            if orphaned_ids or has_duplicate_results:
                seen_tr_ids: set[str] = set()
                filtered = []
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        if block.tool_use_id in orphaned_ids:
                            continue
                        if block.tool_use_id in seen_tr_ids:
                            continue
                        seen_tr_ids.add(block.tool_use_id)
                    filtered.append(block)
                content = filtered

            patched_content = [*synthetic_blocks, *content]

            if patched_content:
                result.append(
                    LLMMessage(
                        role="user",
                        content=patched_content,
                        timestamp=next_msg.timestamp,
                        api_message_id=next_msg.api_message_id,
                    )
                )
            else:
                result.append(
                    LLMMessage(
                        role="user",
                        content=[TextBlock(text="[Content removed during repair]")],
                        timestamp=next_msg.timestamp,
                        api_message_id=next_msg.api_message_id,
                    )
                )
            i += 2
        else:
            if synthetic_blocks:
                result.append(
                    LLMMessage(
                        role="user",
                        content=synthetic_blocks,
                        timestamp=time.time(),
                    )
                )
            i += 1

    if repaired and logger.isEnabledFor(logging.DEBUG):
        diag_parts = []
        for idx, msg in enumerate(result):
            if not isinstance(msg.content, list):
                continue
            uses = [b.id for b in msg.content if isinstance(b, ToolUseBlock)]
            results = [b.tool_use_id for b in msg.content if isinstance(b, ToolResultBlock)]
            if uses or results:
                parts = []
                if uses:
                    parts.append(f"tool_uses=[{','.join(uses)}]")
                if results:
                    parts.append(f"tool_results=[{','.join(results)}]")
                diag_parts.append(f"[{idx}] {msg.role}({', '.join(parts)})")
        logger.debug(
            "Pairing repair activated. Structure: %s",
            "; ".join(diag_parts),
        )

    return result


def generate_tombstone_results(
    messages: list,
    reason: str,
) -> list:
    """Generate synthetic tool_result for every orphaned tool_use in messages.

    Called at streaming time on cancel/interrupt/fallback to ensure every
    tool_use block has a matching tool_result before the next API call.
    Returns one user Message per orphaned tool_use (matching reference
    implementation), or an empty list if no orphans exist.
    """
    from src.herald.types import Message as LLMMessage, ToolUseBlock, ToolResultBlock

    use_ids: list[str] = []
    result_ids: set[str] = set()
    for msg in messages:
        if not isinstance(msg.content, list):
            continue
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                use_ids.append(block.id)
            elif isinstance(block, ToolResultBlock):
                result_ids.add(block.tool_use_id)

    orphaned = [uid for uid in use_ids if uid not in result_ids]
    if not orphaned:
        return []

    now = time.time()
    tombstones = [
        LLMMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id=uid, content=reason, is_error=True)],
            timestamp=now,
        )
        for uid in orphaned
    ]
    logger.debug("Tombstone generation: %d orphaned tool_uses, reason=%s", len(orphaned), reason)
    return tombstones


def guard_tool_results(
    messages: list,
    context_window: int = 200_000,
    replacement_state: ContentReplacementState | None = None,
) -> list:
    """Cap aggregate tool result tokens. Oldest tool results compacted first.

    Tool results can consume at most TOOL_RESULTS_BUDGET_RATIO (50%) of context.
    When over budget, replaces oldest tool result content with stubs.

    When replacement_state is provided and FEATURE_CACHE_STABLE_REPLACEMENTS is
    enabled, replacement decisions are cached: the exact stub text is reused on
    subsequent turns so the prompt prefix stays byte-identical for prompt caching.
    Tool results seen but not replaced are frozen — never replaced later.
    """
    from src.herald.types import ToolResultBlock

    budget = int(context_window * TOOL_RESULTS_BUDGET_RATIO)
    use_cache = replacement_state is not None and cache_stable_replacements_enabled()

    must_reapply: list[
        tuple[int, int, str, str]
    ] = []
    frozen_tokens = 0
    fresh_entries: list[tuple[int, int, int, str]] = []

    for i, msg in enumerate(messages):
        if not isinstance(msg.content, list):
            continue
        for j, block in enumerate(msg.content):
            if isinstance(block, ToolResultBlock):
                block_text = block_content_text(block)
                if is_compacted_stub(block_text):
                    continue
                tu_id = block.tool_use_id
                tokens = estimate_tokens_dense(block_text)

                if use_cache:
                    assert replacement_state is not None
                    cached = replacement_state.get_cached_replacement(tu_id)
                    if cached is not None:
                        must_reapply.append((i, j, tu_id, cached))
                        continue
                    if replacement_state.is_frozen(tu_id):
                        frozen_tokens += tokens
                        continue

                fresh_entries.append((i, j, tokens, tu_id))

    reapply_total = 0
    if must_reapply:
        messages = shallow_copy_messages(messages)
        for i, j, tu_id, cached_stub in must_reapply:
            block = messages[i].content[j]
            if isinstance(block, ToolResultBlock):
                messages[i].content[j] = ToolResultBlock(
                    tool_use_id=block.tool_use_id,
                    content=cached_stub,
                    is_error=block.is_error,
                )
                reapply_total += estimate_tokens_dense(cached_stub)

    fresh_total = sum(t for _, _, t, _ in fresh_entries)
    total = frozen_tokens + fresh_total + reapply_total
    if total <= budget:
        if use_cache:
            assert replacement_state is not None
            for _, _, _, tu_id in fresh_entries:
                replacement_state.mark_seen(tu_id)
        return messages

    if not must_reapply:
        messages = shallow_copy_messages(messages)

    for i, j, tokens, tu_id in fresh_entries:
        if total <= budget:
            if use_cache:
                assert replacement_state is not None
                replacement_state.mark_seen(tu_id)
            continue
        block = messages[i].content[j]
        if isinstance(block, ToolResultBlock):
            stub = extraction_stub(block_content_text(block))
            messages[i].content[j] = ToolResultBlock(
                tool_use_id=block.tool_use_id,
                content=stub,
                is_error=block.is_error,
            )
            total -= tokens
            total += estimate_tokens_dense(stub)
            if use_cache:
                assert replacement_state is not None
                replacement_state.record_replacement(tu_id, stub)

    logger.debug(
        "Tool result guard: compacted to %d tokens (budget=%d, reapplied=%d, frozen=%d)",
        total,
        budget,
        len(must_reapply),
        frozen_tokens,
    )

    if total > budget:
        groups = group_by_api_round(messages)
        if len(groups) > 2:
            messages = [m for g in ([groups[0]] + groups[2:]) for m in g]
            messages = repair_tool_use_result_pairing(messages)
            logger.debug("Tool result guard: dropped oldest round to meet budget")

    return messages


SUMMARIZATION_TIMEOUT_S = 60.0

ANALYSIS_RE = re.compile(r"<analysis>.*?</analysis>", re.DOTALL)
SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
BLANK_LINES_RE = re.compile(r"\n\n+")

PTL_RETRY_MARKER = "[Compaction retry — earlier messages were dropped to fit context window]"

TOKEN_NUMBER_RE = re.compile(r"\b(\d[\d,]{3,})\b")


def extract_token_gap(exc: Exception) -> int | None:
    """Parse token gap from an overflow error (prompt_tokens - limit).

    Handles both orderings: Anthropic ("210000 tokens > 200000 maximum")
    and OpenAI ("maximum context length is 128000 tokens ... 150000 tokens").
    Extracts all large numbers (4+ digits) and returns max - min as the gap.
    """
    numbers = [int(m.group(1).replace(",", "")) for m in TOKEN_NUMBER_RE.finditer(str(exc))]
    if len(numbers) >= 2:
        gap = max(numbers) - min(numbers)
        if gap > 0:
            return gap
    return None


def drop_oldest_rounds(msgs: list, token_gap: int | None = None) -> list | None:
    """Drop oldest API round groups. Uses token gap for precise count when available."""
    from src.herald.types import Message as LLMMessage

    inp = msgs
    if (
        inp
        and getattr(inp[0], "role", "") == "user"
        and getattr(inp[0], "content", "") == PTL_RETRY_MARKER
    ):
        inp = inp[1:]
    groups = group_by_api_round(inp)
    if len(groups) < 2:
        return None
    if token_gap is not None and token_gap > 0:
        acc = 0
        drop_count = 0
        for g in groups:
            acc += estimate_messages_tokens(g)
            drop_count += 1
            if acc >= token_gap:
                break
    else:
        drop_count = max(1, len(groups) // 5)
    drop_count = min(drop_count, len(groups) - 1)
    if drop_count < 1:
        return None
    sliced = [m for g in groups[drop_count:] for m in g]
    if sliced and getattr(sliced[0], "role", "") == "assistant":
        sliced = [
            LLMMessage(
                role="user",
                content=PTL_RETRY_MARKER,
                timestamp=time.time(),
            )
        ] + sliced
    return sliced


def format_compact_summary(raw: str) -> str:
    """Post-process LLM compaction response: strip analysis scratchpad, extract summary.

    The structured prompt asks for <analysis> (reasoning) then <summary> (output).
    We strip the analysis to save context tokens and extract only the summary content.
    Falls back to the full text (minus analysis) if no <summary> tags found.
    """
    if not raw or not raw.strip():
        return raw

    formatted = ANALYSIS_RE.sub("", raw)

    match = SUMMARY_RE.search(formatted)
    if match:
        content = (match.group(1) or "").strip()
        if content:
            formatted = formatted.replace(match.group(0), f"Summary:\n{content}")

    formatted = BLANK_LINES_RE.sub("\n\n", formatted).strip()
    return formatted if formatted else raw


async def summarize_old_messages(
    messages: list,
    adapter: Any,
    preserve_recent: int = PRESERVE_RECENT,
    system_prompt: "list | None" = None,
    tools_schema: list | None = None,
    context_window: int = 200_000,
    *,
    on_ptl_event: "Callable[[Any], Awaitable[None]] | None" = None,
    ptl_thread_id: str = "",
) -> tuple[str, list]:
    """Cache-safe summarization of old messages.

    Sends the parent conversation's exact prefix (system prompt, tools, full
    message history) with a compaction instruction appended as the final user
    message.  This lets the API reuse the cached prefix from the main
    conversation — only the compaction prompt itself is new/uncached.

    Returns (summary_text, preserved_recent_messages).
    Falls back to empty string on failure (caller should use mechanical compaction).

    ``on_ptl_event`` — optional async callback ``(event) -> None`` invoked on
    each PTL retry attempt and on exhaustion. When provided by the
    CompactionOrchestrator, it emits structured ``CompactionPtlRetryEvent``
    and ``CompactionPtlFailedEvent`` through the event system. When ``None``,
    existing logger calls remain as the fallback.
    """
    import asyncio as aio
    from src.engine_core.events import (
        CompactionPtlRetryEvent,
        CompactionPtlFailedEvent,
    )

    async def emit(evt: Any) -> None:
        if on_ptl_event is not None:
            await on_ptl_event(evt)

    if len(messages) <= preserve_recent:
        return "", messages

    recent_messages = messages[-preserve_recent:]

    compaction_prompt = COMPACT_PROMPT

    MAX_PTL_RETRIES = 3

    ptl_retry_enabled = (
        os.environ.get("FEATURE_COMPACTION_PTL_RETRY", "true").strip().lower() != "false"
    )
    max_retries = MAX_PTL_RETRIES if ptl_retry_enabled else 0

    messages_to_summarize = strip_media_from_messages(list(messages))

    fixed_tokens = (
        estimate_system_tokens(system_prompt)
        + (sum(estimate_tokens(str(t)) for t in tools_schema) if tools_schema else 0)
        + COMPACTION_OUTPUT_RESERVE
    )

    from src.engine_core.fork_subagent import build_fork_messages

    for ptl_attempt in range(max_retries + 1):
        compaction_messages = build_fork_messages(compaction_prompt, messages_to_summarize)

        total_tokens = estimate_messages_tokens(compaction_messages) + fixed_tokens
        if total_tokens >= context_window:
            if ptl_attempt < max_retries:
                gap = total_tokens - context_window
                before_count = len(messages_to_summarize)
                trimmed = drop_oldest_rounds(messages_to_summarize, token_gap=gap)
                if trimmed is None:
                    await emit(
                        CompactionPtlFailedEvent(
                            thread_id=ptl_thread_id,
                            reason="no_droppable_rounds",
                            attempts=ptl_attempt + 1,
                            final_message_count=before_count,
                        )
                    )
                    break
                logger.info(
                    "Compaction pre-check overflow (attempt %d, gap=%d): %d messages remain",
                    ptl_attempt + 1,
                    gap,
                    len(trimmed),
                )
                await emit(
                    CompactionPtlRetryEvent(
                        thread_id=ptl_thread_id,
                        attempt=ptl_attempt + 1,
                        max_retries=max_retries,
                        trigger="pre_check",
                        token_gap=gap,
                        messages_before=before_count,
                        messages_after=len(trimmed),
                    )
                )
                messages_to_summarize = trimmed
                continue
            logger.warning(
                "Cache-safe compaction would exceed context (%d / %d) — skipping to mechanical",
                total_tokens,
                context_window,
            )
            await emit(
                CompactionPtlFailedEvent(
                    thread_id=ptl_thread_id,
                    reason="context_exceeded",
                    attempts=ptl_attempt + 1,
                    final_message_count=len(messages_to_summarize),
                )
            )
            return "", recent_messages

        try:
            response = await aio.wait_for(
                adapter.generate_with_tools(
                    compaction_messages,
                    system=system_prompt,
                    tools=tools_schema,
                    skip_cache_write=True,
                ),
                timeout=SUMMARIZATION_TIMEOUT_S,
            )
            summary = format_compact_summary(response.text)
            logger.info(
                "Cache-safe summarization produced %d chars from %d messages",
                len(summary),
                len(messages) - preserve_recent,
            )
            return summary, recent_messages
        except aio.TimeoutError:
            logger.warning("Summarization timed out after %.0fs", SUMMARIZATION_TIMEOUT_S)
            return "", recent_messages
        except Exception as e:
            from src.herald.retry import is_context_overflow

            if is_context_overflow(e) and ptl_attempt < max_retries:
                token_gap = extract_token_gap(e)
                before_count = len(messages_to_summarize)
                trimmed = drop_oldest_rounds(messages_to_summarize, token_gap=token_gap)
                if trimmed is None:
                    await emit(
                        CompactionPtlFailedEvent(
                            thread_id=ptl_thread_id,
                            reason="no_droppable_rounds",
                            attempts=ptl_attempt + 1,
                            final_message_count=before_count,
                        )
                    )
                    break
                logger.warning(
                    "Compaction hit prompt-too-long (attempt %d/%d, gap=%s): %d messages remain",
                    ptl_attempt + 1,
                    max_retries,
                    token_gap,
                    len(trimmed),
                )
                await emit(
                    CompactionPtlRetryEvent(
                        thread_id=ptl_thread_id,
                        attempt=ptl_attempt + 1,
                        max_retries=max_retries,
                        trigger="api_overflow",
                        token_gap=token_gap,
                        messages_before=before_count,
                        messages_after=len(trimmed),
                    )
                )
                messages_to_summarize = trimmed
                continue
            logger.warning("Summarization failed, falling back to mechanical compaction: %s", e)
            return "", recent_messages

    logger.warning("Summarization PTL retries exhausted — falling back to mechanical")
    await emit(
        CompactionPtlFailedEvent(
            thread_id=ptl_thread_id,
            reason="retries_exhausted",
            attempts=max_retries + 1,
            final_message_count=len(messages_to_summarize),
        )
    )
    return "", recent_messages


async def compact_messages_with_summary(
    messages: list,
    adapter: Any,
    context_window: int = 200_000,
    preserve_recent: int = PRESERVE_RECENT,
    system_prompt: "list | None" = None,
    tools_schema: list | None = None,
    *,
    on_ptl_event: "Callable[[Any], Awaitable[None]] | None" = None,
    ptl_thread_id: str = "",
) -> list:
    """Cache-safe summarize-first compaction. Falls back to gut+drop on failure.

    Sends the parent conversation's full prefix (system prompt, tools, messages)
    with a compaction instruction appended, so the API reuses the cached prefix.
    Only called when needs_compaction() is True.
    """
    from src.herald.types import Message as LLMMessage

    used = estimate_messages_tokens(messages)
    threshold = int(context_window * COMPACTION_THRESHOLD)

    if used < threshold:
        return messages

    if len(messages) <= preserve_recent:
        return messages

    summary, recent = await summarize_old_messages(
        messages,
        adapter,
        preserve_recent,
        system_prompt=system_prompt,
        tools_schema=tools_schema,
        context_window=context_window,
        on_ptl_event=on_ptl_event,
        ptl_thread_id=ptl_thread_id,
    )

    if summary:
        wrapper = (
            "This session is being continued from a previous conversation that "
            "ran out of context. The summary below covers the earlier portion "
            "of the conversation.\n\n"
            f"{summary}\n\n"
            "Recent messages are preserved verbatim."
        )
        summary_msg = LLMMessage(role="user", content=wrapper, timestamp=time.time())
        result = [summary_msg] + recent
        result = repair_tool_use_result_pairing(result)
        new_used = estimate_messages_tokens(result)
        logger.info("Summarization compaction: %d -> %d tokens", used, new_used)

        if new_used < threshold:
            return result

    return compact_messages(messages, context_window, preserve_recent)
