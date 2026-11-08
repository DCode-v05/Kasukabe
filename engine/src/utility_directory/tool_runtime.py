"""Tool Runtime — ownership and delivery of platform tools.

ToolDef and ToolRegistry are the runtime format for anything the LLM can call
via tool_use. hydrate_tools() reads platform tools from the catalog, resolves handler
code (source_code field preferred, implementation dotted-path as fallback),
and builds a ready-made ToolRegistry for the execution thread.

The execution thread receives a ToolRegistry and dispatches calls through it.
It never defines or owns tool handlers — that's UD's job.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from src.herald.types import ToolDef as LLMToolDef

from src.engine_core.cancellation import InterruptBehavior
from src.herald.types import ToolCall
from src.sandbox.permissions import ToolSource

logger = logging.getLogger(__name__)


def ALWAYS_SAFE(_: dict[str, Any]) -> bool:
    return True


def NEVER_SAFE(_: dict[str, Any]) -> bool:
    return False


@dataclass
class ToolDef:
    """A registered tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[str]]
    source: ToolSource = ToolSource.INTERNAL
    is_concurrency_safe: Callable[[dict[str, Any]], bool] = field(
        default_factory=lambda: lambda _: False
    )
    is_read_only: Callable[[dict[str, Any]], bool] = field(default_factory=lambda: lambda _: False)
    is_destructive: Callable[[dict[str, Any]], bool] = field(
        default_factory=lambda: lambda _: False
    )
    interrupt_behavior: InterruptBehavior = InterruptBehavior.BLOCK
    interrupt_behavior_fn: Callable[[dict[str, Any]], InterruptBehavior] | None = None
    is_command_execution: bool = False
    context_modifier_fn: Callable[[dict[str, Any], str], dict[str, Any] | None] | None = None
    get_summary: Callable[[dict[str, Any]], str | None] | None = None
    defer_loading: bool = False
    server_name: str = ""
    action_name: str = ""
    connection_id: str = ""

    def get_interrupt_behavior(
        self,
        tool_input: dict[str, Any] | None = None,
    ) -> InterruptBehavior:
        """Resolve the tool's interrupt behavior for a specific invocation.

        Query order: ``interrupt_behavior_fn(tool_input)`` (if set) →
        static ``interrupt_behavior`` field.  Any exception inside the
        runtime function falls back to BLOCK — the safe default that
        keeps mid-flight tools from being yanked when their own
        declaration code is broken.  ``tool_input`` lets a tool vary
        per call (e.g. a long-running bash with ``timeout=5`` can opt
        into CANCEL while an uncapped bash stays BLOCK).
        """
        if self.interrupt_behavior_fn is not None:
            try:
                return self.interrupt_behavior_fn(tool_input or {})
            except Exception:
                logger.warning(
                    "interrupt_behavior_fn raised for tool %s; defaulting to BLOCK",
                    self.name,
                    exc_info=True,
                )
                return InterruptBehavior.BLOCK
        return self.interrupt_behavior


class ToolRegistry:
    """Holds all available tools. Provides tool schemas for the
    thread's agent loop and metadata for capability context."""

    def __init__(self) -> None:
        self.tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        self.tools[tool.name] = tool

    def get_tool(self, name: str) -> ToolDef | None:
        return self.tools.get(name)

    def list_tools_meta(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self.tools.values()
        ]

    def to_tool_defs(self) -> list["LLMToolDef"]:
        """Return tool schemas as provider-neutral ToolDef objects.

        Forwards each tool's ``defer_loading`` flag so the herald adapter can
        emit ``defer_loading: true`` on the Anthropic schema for tools that
        opted in.
        """
        from src.herald.types import ToolDef as LLMToolDef

        return [
            LLMToolDef(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
                defer_loading=tool.defer_loading,
            )
            for tool in self.tools.values()
        ]

    def scope(self, names: list[str]) -> "ToolRegistry":
        scoped = ToolRegistry()
        for name in names:
            tool = self.tools.get(name)
            if tool is not None:
                scoped.register(tool)
        return scoped

    async def execute(self, name: str, input: dict[str, Any]) -> str:
        """Look up a tool by name and execute it. Returns the result string."""
        tool = self.tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"
        try:
            return await tool.handler(input)
        except Exception as e:
            return f"Error executing tool '{name}': {e}"

    @property
    def tool_count(self) -> int:
        return len(self.tools)


@dataclass
class ToolBatch:
    """A batch of tool calls sharing the same concurrency classification.

    Concurrency-safe batches may run their calls in parallel.
    Non-safe batches must run their single call serially (one at a time).
    """

    is_concurrency_safe: bool
    calls: list[ToolCall]


def input_valid(tool: ToolDef, inp: dict[str, Any]) -> bool:
    """Lightweight input validation — checks required fields are present.

    Mirrors the TypeScript reference's ``inputSchema.safeParse(toolUse.input)``
    guard: if the input doesn't satisfy the schema's required fields, the
    safety callable never runs and the tool is treated as non-safe.
    """
    required = tool.input_schema.get("required", [])
    return all(k in inp for k in required)


def partition_tool_calls(
    tool_calls: list[ToolCall],
    registry: ToolRegistry,
) -> list[ToolBatch]:
    """Partition tool calls into concurrent/serial batches.

    Consecutive concurrency-safe calls are merged into one batch (they
    may run in parallel). Each non-safe call gets its own batch (must
    run exclusively).

    This is the Python equivalent of ``partitionToolCalls()`` in the
    TypeScript reference (services/tools/toolOrchestration.ts:91-116).

    Conservative by design: unknown tool names, failed input validation,
    and any exception raised by ``is_concurrency_safe`` all fall back to
    non-safe so we never accidentally run a destructive tool in parallel.

    Args:
        tool_calls: Ordered list of tool calls to classify.
        registry: Registry used to look up tool metadata.

    Returns:
        Ordered list of :class:`ToolBatch` objects.  Empty input
        returns an empty list.
    """
    batches: list[ToolBatch] = []
    for tc in tool_calls:
        tool = registry.get_tool(tc.name)
        is_safe: bool
        if tool is None:
            is_safe = False
        elif not input_valid(tool, tc.input):
            is_safe = False
        else:
            try:
                is_safe = bool(tool.is_concurrency_safe(tc.input))
            except Exception:
                logger.warning(
                    "is_concurrency_safe raised for tool %s; treating as unsafe",
                    tc.name,
                    exc_info=True,
                )
                is_safe = False

        if is_safe and batches and batches[-1].is_concurrency_safe:
            batches[-1].calls.append(tc)
        else:
            batches.append(ToolBatch(is_concurrency_safe=is_safe, calls=[tc]))

    return batches


CONCURRENCY_SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "search_content",
        "list_content",
        "tool_search",
        "web_search",
        "web_fetch",
    }
)


async def hydrate_tools(ud_full: Any = None) -> ToolRegistry:
    """Build a ToolRegistry from catalog tool definitions.

    For each tool in the catalog with source_code: exec() the code in-process
    at boot, extract the handler() function, and register it directly.
    Trusted execution — platform tools are vetted catalog code.

    Returns empty registry when ud_full is None (graceful degradation).
    """
    import types

    registry = ToolRegistry()
    if ud_full is None:
        return registry

    try:
        definitions = await ud_full.get_all_latest_tools()
    except Exception:
        return registry

    for definition in definitions:
        name = definition.get("name", "?")
        source_code = definition.get("source_code")

        interface = definition.get("interface", {})
        if isinstance(interface, str):
            try:
                interface = json.loads(interface)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    "Tool '%s' has malformed interface JSON — skipping: %s",
                    name,
                    e,
                )
                continue

        if not source_code or not source_code.strip():
            logger.warning("Tool '%s' has no source_code — skipping", name)
            continue

        try:
            module = types.ModuleType(f"tool_{name}")
            exec(source_code, module.__dict__)
        except Exception as e:
            logger.warning("Tool '%s' failed to load (exec error): %s", name, e)
            continue

        handler_fn = getattr(module, "handler", None)
        if handler_fn is None or not callable(handler_fn):
            logger.warning("Tool '%s' has no callable handler() — skipping", name)
            continue

        is_safe: bool = definition.get("is_concurrency_safe", name in CONCURRENCY_SAFE_TOOLS)
        is_ro: bool = definition.get("is_read_only", False)
        is_destructive: bool = definition.get("is_destructive", False)
        is_cmd: bool = definition.get("is_command_execution", False)

        ib_raw = definition.get("interrupt_behavior", "block")
        try:
            ib = InterruptBehavior(ib_raw)
        except ValueError:
            logger.warning(
                "Tool '%s' has invalid interrupt_behavior '%s' — defaulting to BLOCK",
                name,
                ib_raw,
            )
            ib = InterruptBehavior.BLOCK

        ctx_mod_fn = getattr(module, "context_modifier", None)
        if ctx_mod_fn is not None and not callable(ctx_mod_fn):
            ctx_mod_fn = None
        summary_fn = getattr(module, "get_summary", None)
        if summary_fn is not None and not callable(summary_fn):
            summary_fn = None

        registry.register(
            ToolDef(
                name=name,
                description=definition.get("description", ""),
                input_schema=interface,
                handler=handler_fn,
                source=ToolSource.PLATFORM,
                is_concurrency_safe=ALWAYS_SAFE if is_safe else NEVER_SAFE,
                is_read_only=ALWAYS_SAFE if is_ro else NEVER_SAFE,
                is_destructive=ALWAYS_SAFE if is_destructive else NEVER_SAFE,
                interrupt_behavior=ib,
                is_command_execution=is_cmd,
                context_modifier_fn=ctx_mod_fn,
                get_summary=summary_fn,
            )
        )

    return registry
