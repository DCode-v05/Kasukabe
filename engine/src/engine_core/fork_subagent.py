"""Fork subagent — cache-sharing child agent execution.

When a SubAgent is spawned with fork_context_messages, it inherits the
parent's message history as a prefix. The Anthropic API cache key is:
  system prompt + tools + model + message prefix

By prepending the parent's messages, the fork child reuses the parent's
already-cached prefix. Only the new task directive at the end is uncached.
This drives cache hit rates to 90%+ for repetitive sub-agent spawns.

Key design decisions (mirroring the reference forkedAgent.ts):
- All fork children share identical tool_result placeholder text so they
  share the same cache entry — only the final directive block differs.
- A FORK_BOILERPLATE_TAG injected into the child message prevents recursive
  forking (guards against a fork child spawning more forks).
- Feature gate: FORK_SUBAGENT env var ("true"/"false" strings — never "1"/"0"
  per engineering standards).
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from src.herald.types import Message, TextBlock, ToolResultBlock, ToolUseBlock

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


FORK_SUBAGENT_FLAG = "FORK_SUBAGENT"
FORK_SUBAGENT_DEFAULT = "false"


FORK_BOILERPLATE_TAG = "fork-child-boilerplate"

FORK_DIRECTIVE_PREFIX = "Your directive: "

FORK_PLACEHOLDER_RESULT = "Fork started — processing in background"


def build_child_message(directive: str) -> str:
    """Build the text block that starts each fork child's message.

    Contains the FORK_BOILERPLATE_TAG (detected by is_in_fork_child) and
    the per-child directive. The boilerplate instructions are intentionally
    identical to the reference implementation so analytics tooling and
    transcript readers see the same shape.
    """
    return (
        f"<{FORK_BOILERPLATE_TAG}>\n"
        "STOP. READ THIS FIRST.\n\n"
        "You are a forked worker process. You are NOT the main agent.\n\n"
        "RULES (non-negotiable):\n"
        "1. Do NOT spawn sub-agents; execute directly.\n"
        "2. Do NOT converse, ask questions, or suggest next steps.\n"
        "3. Do NOT editorialize or add meta-commentary.\n"
        "4. USE your tools directly.\n"
        "5. If you modify files, commit your changes before reporting.\n"
        "6. Do NOT emit text between tool calls. Report once at the end.\n"
        "7. Stay strictly within your directive's scope.\n"
        "8. Keep your report under 500 words unless specified otherwise.\n"
        '9. Your response MUST begin with "Scope:". No preamble.\n'
        "10. REPORT structured facts, then stop.\n\n"
        "Output format:\n"
        "  Scope: <your assigned scope in one sentence>\n"
        "  Result: <answer or key findings>\n"
        "  Key files: <relevant paths — research tasks>\n"
        "  Files changed: <list with commit hash — if you modified files>\n"
        "  Issues: <list — only if there are issues>\n"
        f"</{FORK_BOILERPLATE_TAG}>\n\n"
        f"{FORK_DIRECTIVE_PREFIX}{directive}"
    )


def build_fork_messages(
    directive: str,
    parent_messages: list[Message],
) -> list[Message]:
    """Build the forked conversation messages for a child agent.

    For prompt cache sharing, all fork children must produce byte-identical
    API request prefixes up to the final directive text block. This function:
    1. Keeps all parent messages as-is (the shared cacheable prefix)
    2. Finds the last assistant message and builds tool_result placeholders
       for every tool_use block in it (identical across all children)
    3. Appends the per-child directive as the last text block

    Result:
      [...parent_messages, user(placeholder_results..., directive)]

    When no tool_use blocks are found in the last assistant message, the
    function appends a plain user message with just the directive.

    Args:
        directive: The task string for this fork child.
        parent_messages: The parent's complete message history.

    Returns:
        List of messages to pass as the fork child's initial_messages.
    """
    last_assistant_tool_uses: list[ToolUseBlock] = []
    for msg in reversed(parent_messages):
        if msg.role != "assistant":
            continue
        if not isinstance(msg.content, list):
            break
        tool_uses = [b for b in msg.content if isinstance(b, ToolUseBlock)]
        if tool_uses:
            last_assistant_tool_uses = tool_uses
        break

    child_text = build_child_message(directive)

    if not last_assistant_tool_uses:
        directive_message = Message(
            role="user",
            content=[TextBlock(text=child_text)],
            timestamp=time.time(),
        )
        return [*parent_messages, directive_message]

    tool_result_blocks: list[ToolResultBlock] = [
        ToolResultBlock(
            tool_use_id=block.id,
            content=FORK_PLACEHOLDER_RESULT,
        )
        for block in last_assistant_tool_uses
    ]

    directive_message = Message(
        role="user",
        content=[*tool_result_blocks, TextBlock(text=child_text)],
        timestamp=time.time(),
    )

    return [*parent_messages, directive_message]


SIDECHAIN_DIR_ENV = "SIDECHAIN_DIR"


def sidechain_dir_default() -> str:
    """Sidechains live under the engine root, not /tmp.

    They are sub-agent transcripts: part of a user's history, and therefore
    part of the tree a runtime captures. In /tmp they were outside anything
    that gets snapshotted and were wiped by a tmpfs remount.
    """
    from src.infra.september_home import resolve_september_home, september_paths

    return str(september_paths(resolve_september_home())["sidechains"])


SIDECHAIN_MAX_BYTES_ENV = "SIDECHAIN_MAX_BYTES"
SIDECHAIN_MAX_BYTES_DEFAULT = 10 * 1024 * 1024

SIDECHAIN_ROTATE_CHECK_EVERY = 32

SIDECHAIN_COUNTS_MAX = 4096
sidechain_write_counts: "dict[str, int]" = {}


def get_sidechain_dir() -> str:
    override = os.environ.get(SIDECHAIN_DIR_ENV)
    return override if override else sidechain_dir_default()

