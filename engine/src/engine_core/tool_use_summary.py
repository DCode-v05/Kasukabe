"""Tool Use Summary — generate a brief label for a completed tool batch.

After tools execute, fires a background LLM call (concurrent with the next
agent turn) to produce a short git-commit-style label describing what the
batch accomplished.  The label is ≤30 characters, past-tense, and targets
a single-line mobile display row.

Non-critical: any exception returns None without disturbing the caller.
The background task is fully fire-and-forget — failures are logged and
swallowed.

Port of services/toolUseSummary/toolUseSummaryGenerator.ts.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.herald.base import LLM

logger = logging.getLogger(__name__)

# Feature gate — set FEATURE_TOOL_USE_SUMMARY=false to disable entirely.
ENABLED = os.environ.get("FEATURE_TOOL_USE_SUMMARY", "true").lower() != "false"

SYSTEM_PROMPT = (
    "Write a short summary label describing what these tool calls accomplished. "
    "It appears as a single-line row in a mobile app and truncates around 30 "
    "characters, so think git-commit-subject, not sentence.\n\n"
    "Keep the verb in past tense and the most distinctive noun. Drop articles, "
    "connectors, and long location context first.\n\n"
    "Examples:\n"
    "- Searched in auth/\n"
    "- Fixed NPE in UserService\n"
    "- Created signup endpoint\n"
    "- Read config.json\n"
    "- Ran failing tests"
)

MAX_JSON_LEN = 300


@dataclass
class ToolInfo:
    """Minimal descriptor for one completed tool call."""

    name: str
    input: dict
    output: str


def truncate_json(value: Any, max_length: int = MAX_JSON_LEN) -> str:
    """Serialize *value* to JSON and truncate to *max_length* characters."""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        return "[unable to serialize]"
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


async def generate_tool_use_summary(
    tools: list[ToolInfo],
    adapter: LLM,
    last_assistant_text: str = "",
    is_cancelled: Callable[[], bool] | None = None,
) -> str | None:
    """Generate a brief summary label for a completed tool batch.

    Parameters
    ----------
    tools:
        The tool calls that just finished, with their inputs and outputs.
    adapter:
        An LLM adapter already pointed at the lightest available model.
        The caller is responsible for selecting the right tier.
    last_assistant_text:
        The most recent assistant text turn (used as intent context).
        Capped to 200 characters in the prompt — mirrors the TypeScript ref.
    is_cancelled:
        Optional callable returning True if the parent session was aborted.
        Checked before the LLM call to avoid wasting tokens.

    Returns
    -------
    A short string (≤~30 chars typically) or None if generation fails.
    """
    if not tools:
        return None

    if not ENABLED:
        return None

    if is_cancelled and is_cancelled():
        return None

    try:
        from src.herald.types import Message, SystemPromptBlock

        # Build a concise representation of what tools did
        parts: list[str] = []
        for t in tools:
            input_str = truncate_json(t.input)
            output_str = truncate_json(t.output)
            parts.append(f"Tool: {t.name}\nInput: {input_str}\nOutput: {output_str}")
        tool_block = "\n\n".join(parts)

        context_prefix = ""
        if last_assistant_text:
            snip = last_assistant_text[:200]
            context_prefix = f"User's intent (from assistant's last message): {snip}\n\n"

        user_content = f"{context_prefix}Tools completed:\n\n{tool_block}\n\nLabel:"

        # System prompt with global cache scope so the stable prefix is shared
        # across calls without reconstructing — mirrors enablePromptCaching in ref.
        system = [
            SystemPromptBlock(
                text=SYSTEM_PROMPT,
                cache_scope="global",
            )
        ]

        if is_cancelled and is_cancelled():
            return None

        response = await adapter.generate(
            messages=[Message(role="user", content=user_content)],
            system=system,
        )

        summary = response.strip()
        return summary or None

    except Exception:
        logger.debug("tool_use_summary: generation failed (non-critical)", exc_info=True)
        return None
