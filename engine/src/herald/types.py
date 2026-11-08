"""Provider-neutral types for the LLM layer.

Every part of the engine uses these types to build messages, define tools,
and read responses. No vendor-specific format leaks beyond the provider files.

These are the internal lingua franca — all providers translate from/to these.
Engine code never constructs vendor-specific dicts (e.g. Anthropic content blocks).

Key types:
    Message         — A conversation message (role + content as str or ContentBlocks)
    TextBlock       — Plain text in a message
    ToolUseBlock    — Model requesting a tool call (in assistant messages)
    ToolResultBlock — Result of tool execution (in user messages fed back to model)
    ToolDef         — A tool the model can call (name + description + JSON Schema)
    ToolCall        — A tool call returned by the model (id + name + input)
    LLMResponse     — Full response: text, tool_calls, stop_reason, usage
    StreamEvent     — One chunk from a streaming LLM call with tool support
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


SUPER_THINKING_BUDGET = 32_768
THINKING_LATCH_BUDGET = 1_024


@dataclass(frozen=True)
class TextBlock:
    """Plain text content in a message."""

    text: str


@dataclass(frozen=True)
class ToolUseBlock:
    """Model is requesting a tool call (appears in assistant messages)."""

    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class ImageBlock:
    """Image content. Provider-neutral source dict — translated by each provider.

    Source examples:
        {"type": "base64", "media_type": "image/png", "data": "..."}
        {"type": "url", "url": "https://..."}
    """

    source: dict


@dataclass(frozen=True)
class PersistedBlock:
    """A tool result that was too large to inline; spilled to disk.

    The provider serializes this as a text hint so the model knows the full
    output exists at `path` and sees `summary` (head excerpt) inline.
    """

    path: str
    summary: str


@dataclass(frozen=True)
class ToolResultBlock:
    """Result of a tool execution (appears in user messages fed back to model).

    `content` is `str` for the legacy/common case (one text block, no extras).
    `content` is `list[ContentBlock]` when the post-tool injection pipeline
    has accumulated multiple blocks (base text + permission feedback +
    hook attachments + images + persisted-output hints).
    """

    tool_use_id: str
    content: "str | list[ContentBlock]"
    is_error: bool = False
    source_assistant_id: str = ""


@dataclass(frozen=True)
class ThinkingBlock:
    """Model reasoning — provider-agnostic. Anthropic: "thinking" + "redacted_thinking"."""

    thinking: str
    is_redacted: bool = False


ContentBlock = (
    TextBlock | ToolUseBlock | ToolResultBlock | ThinkingBlock | ImageBlock | PersistedBlock
)


def format_persisted_block_xml(block: "PersistedBlock") -> str:
    """Render a PersistedBlock as the wire-format XML retrieval hint.

    Single source of truth for the ``<persisted-output path="...">``
    format used by every provider serializer and by the on-disk record
    in tool_result_storage.py. Matches the src reference format so the
    model sees the same shape across providers.
    """
    return f'<persisted-output path="{block.path}">{block.summary}</persisted-output>'


def flatten_tool_result_content_to_text(
    content: "str | list[ContentBlock]",
) -> str:
    """Flatten ToolResultBlock.content to a string for providers that
    can't carry typed block arrays in their tool_result shape.

    Used by OpenAI and Gemini providers (Anthropic emits the native
    array form). Concatenates text-bearing blocks with blank lines,
    renders ``PersistedBlock`` as the XML retrieval hint, and drops
    image blocks (provider-specific code may log the drop separately).
    """
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for sub in content:
        if isinstance(sub, TextBlock):
            parts.append(sub.text)
        elif isinstance(sub, PersistedBlock):
            parts.append(format_persisted_block_xml(sub))
    return "\n\n".join(parts)


@dataclass(frozen=True)
class Message:
    """A single message in the conversation history.

    - role: "user", "assistant", or "tool"
    - content: plain string for simple messages, list of ContentBlock for
      structured messages (tool calls, tool results).
    - timestamp: unix time when the message was constructed (seconds since epoch).
      Foundational telemetry. Used by the time-based microcompact trigger to
      detect idle gaps, and available for replay / analytics / per-result
      staleness logic. Optional (default ``None``) so legacy callers and
      test fixtures don't break; production construction sites in thread.py
      and sub_agent.py set it explicitly via ``time.time()``, and compaction
      routines preserve it when rebuilding messages.
    - metadata: optional per-message metadata (e.g. {"is_meta": True} on
      engine-injected nudges so future transcript exporters, replay tools,
      or debug UIs can filter them without regex-matching the content).
      Default is an empty dict — all existing call sites are unaffected.
    """

    role: str
    content: str | list[ContentBlock]
    timestamp: float | None = None
    metadata: dict = field(default_factory=dict, compare=False)
    api_message_id: str | None = field(default=None, compare=False)

    def text(self) -> str:
        """Extract all text content from this message.

        Walks any TextBlock at the top level, plus any TextBlock nested
        inside a ToolResultBlock with list-form content (which is what the
        post-tool injection pipeline produces).
        """
        if isinstance(self.content, str):
            return self.content
        parts: list[str] = []
        for block in self.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
            elif isinstance(block, ToolResultBlock):
                inner = block.content
                if isinstance(inner, str):
                    parts.append(inner)
                else:
                    for sub in inner:
                        if isinstance(sub, TextBlock):
                            parts.append(sub.text)
        return "".join(parts)


def message_has_image(message: "Message") -> bool:
    """True if this message carries any image content.

    Walks top-level ImageBlocks plus ImageBlocks nested inside a
    ToolResultBlock's list-form content (the post-tool injection pipeline can
    attach images to a tool result). Mirrors Message.text()'s traversal so the
    vision signal sees exactly the same block shapes.
    """
    content = message.content
    if isinstance(content, str):
        return False
    for block in content:
        if isinstance(block, ImageBlock):
            return True
        if isinstance(block, ToolResultBlock) and not isinstance(block.content, str):
            for sub in block.content:
                if isinstance(sub, ImageBlock):
                    return True
    return False


def messages_have_image(messages: "list[Message]") -> bool:
    """True if any message in the list carries image content (the vision signal)."""
    return any(message_has_image(m) for m in messages)


@dataclass(frozen=True)
class ToolDef:
    """A tool the model can call. Schema uses standard JSON Schema.

    ``defer_loading`` mirrors Anthropic's ``ToolParam.defer_loading`` (GA on
    anthropic-python 0.97+). When True, the tool name is advertised but its
    full schema is not eager-loaded into the system prompt; the API surfaces
    it via tool search on demand. Cuts initial prompt + cache-write tokens
    for rarely-used tools without making them invisible.
    """

    name: str
    description: str
    input_schema: dict
    defer_loading: bool = False


TOOL_NAME_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")


def sanitize_tool_name(name: str) -> str:
    """Replace characters invalid in provider tool name patterns with underscores."""
    return TOOL_NAME_UNSAFE.sub("_", name)


def build_tool_name_map(tools: list["ToolDef"]) -> dict[str, str]:
    """Build sanitized→original name map for a tool list.

    Used by providers that sanitize outbound names and need to reverse-map
    the model's tool call responses back to internal names.
    """
    return {sanitize_tool_name(t.name): t.name for t in tools}


class StopReason(str, Enum):
    """Why the model stopped generating."""

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"


@dataclass(frozen=True)
class ToolCall:
    """A tool call returned by the model."""

    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class Usage:
    """Token usage for a single LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    """Structured response from an LLM call.

    Returned by generate_with_tools(). Contains text output, any tool calls
    the model wants to make, why it stopped, and token usage.
    """

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: StopReason = StopReason.END_TURN
    usage: Usage = field(default_factory=Usage)
    message_id: str = ""


@dataclass(frozen=True)
class StreamEvent:
    """One event from a streaming LLM call with tool support.

    Yielded by stream_with_tools(). Types:
      "text"      — a chunk of streaming text (text field set)
      "tool_call" — a complete tool call (tool_call field set)
      "stop"      — model stopped generating (stop_reason field set)
      "usage"     — final token counts (usage field set)
    """

    type: str
    text: str = ""
    tool_call: ToolCall | None = None
    stop_reason: StopReason | None = None
    usage: Usage | None = None
    thinking_block: ThinkingBlock | None = None
    message_id: str = ""


@dataclass(frozen=True)
class SystemPromptBlock:
    """One section of the structured system prompt.

    The system prompt is split into blocks at a DYNAMIC_BOUNDARY marker.
    Blocks before the boundary are stable (long ``ttl``, e.g. ``"1h"``).
    Blocks after are dynamic (default 5-minute ephemeral cache).

    ``cache_scope`` is engine-internal intent metadata only. It is read
    by ``herald/cache.py`` to validate that stable blocks declare intent,
    and by ``prompt_cache_break_detection.py`` for fingerprinting. It is
    NOT sent on the wire — Anthropic's cache_control accepts only
    ``type`` and ``ttl``. The provider translates ``ttl`` directly and
    drops ``cache_scope``.

    The Provider class on top of the anthropic SDK turns each block into
    a separate ``{"type": "text", "text": ..., "cache_control": ...}``
    entry in the system param array with per-block ``ttl``.
    """

    text: str
    cache_scope: str | None = None
    ttl: str | None = None


def flatten_system_blocks(system: "list[SystemPromptBlock] | None") -> str:
    """Flatten structured system blocks to a single string.

    Used by providers that don't support multi-block system params
    (OpenAI, Gemini). Anthropic uses per-block cache_control natively.
    """
    if not system:
        return ""
    return "\n\n".join(b.text for b in system if b.text)
