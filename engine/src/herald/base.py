"""Provider-agnostic LLM interface.

All engine components call ONLY through this protocol.
Vendor SDKs live exclusively inside provider files.
Swapping provider = one new file in providers/, zero changes elsewhere.

The four methods:
    generate()            — Simple text in, text out.
    stream()              — Text in, async iterator of text chunks out.
    generate_with_tools() — Text + tool definitions in, LLMResponse out.
                            LLMResponse contains text, tool_calls, stop_reason, usage.
    stream_with_tools()   — Streaming version of generate_with_tools().

The *_with_tools methods accept a super_thinking boolean to enable extended
reasoning. Providers map this to their native mechanism (Anthropic thinking
budgets, OpenAI reasoning_effort, Gemini ThinkingConfig).

System prompt is passed as list[SystemPromptBlock] — structured blocks with
per-block cache scope and TTL. Providers that support multi-block system
params (Anthropic) emit each block with its own cache_control. Providers
that don't (OpenAI, Gemini) flatten all blocks to a single string.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from src.herald.types import LLMResponse, Message, StreamEvent, SystemPromptBlock, ToolDef


@runtime_checkable
class LLM(Protocol):
    """Protocol defining the LLM interface. All engine components use this."""

    async def generate(
        self,
        messages: list[Message],
        system: list[SystemPromptBlock] | None = None,
    ) -> str:
        """Send messages and return the full text response."""
        ...

    def stream(
        self,
        messages: list[Message],
        system: list[SystemPromptBlock] | None = None,
    ) -> AsyncIterator[str]:
        """Stream text tokens from the LLM. Yields string chunks."""
        ...

    async def generate_with_tools(
        self,
        messages: list[Message],
        system: list[SystemPromptBlock] | None = None,
        tools: list[ToolDef] | None = None,
        super_thinking: bool = False,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ) -> LLMResponse:
        """Send messages with tool definitions, return structured response.

        skip_cache_write: when True, providers with prompt caching (Anthropic)
        should omit cache_control on this request so the response does not
        seed a new cache entry. Used for fire-and-forget fork paths
        (summaries, compaction probes). Providers without caching ignore it.
        """
        ...

    def stream_with_tools(
        self,
        messages: list[Message],
        system: list[SystemPromptBlock] | None = None,
        tools: list[ToolDef] | None = None,
        super_thinking: bool = False,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Stream text chunks and tool calls from the LLM.

        Yields StreamEvent objects as the model generates. Text arrives
        token-by-token, tool calls arrive as complete objects after
        accumulation. Usage arrives at stream end.
        """
        ...
