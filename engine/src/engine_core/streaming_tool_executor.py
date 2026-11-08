"""StreamingToolExecutor — execute tools as they arrive from the LLM stream.

Instead of collecting all tool_call events and running them after the stream
completes, this executor starts each tool as soon as it arrives, respecting
concurrency constraints:
  - Concurrency-safe tools run in parallel with each other
  - Non-safe tools run exclusively (nothing else executes alongside them)
  - Results are always yielded in insertion order regardless of completion order

The executor is a pure scheduler. It delegates actual execution to an
execute_fn callback provided by the caller (thread.py), keeping permission
checks, tracing, and event emission in the thread where they belong.

Sibling abort: when a command-execution tool (is_command_execution=True on its
ToolDef) errors, a separate sibling cancellation token fires — killing sibling
tool processes without aborting the parent turn.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncGenerator, Awaitable, Callable, Generator

from src.engine_core.cancellation import (
    AbortReason,
    CancellationToken,
    CombinedCancellationToken,
    InterruptBehavior,
    create_child_token,
)
from src.herald.types import ToolCall, ToolResultBlock

if TYPE_CHECKING:
    from src.engine_core.events import EventEmitter

logger = logging.getLogger(__name__)

MAX_TOOL_USE_CONCURRENCY: int = int(
    os.environ.get("CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY", "") or "10"
)

DESCRIPTION_KEYS = ("command", "file_path", "path", "pattern", "query", "url")
DESC_MAX_LEN = 40

REJECT_MESSAGE = (
    "The user doesn't want to proceed with this tool use. The tool use "
    "was rejected (eg. if it was a file edit, the new_string was NOT "
    "written to the file). STOP what you are doing and wait for the "
    "user to tell you how to proceed."
)


class ToolStatus(Enum):
    QUEUED = "queued"
    EXECUTING = "executing"
    COMPLETED = "completed"
    YIELDED = "yielded"


@dataclass
class TrackedTool:
    index: int
    tool_call: ToolCall
    status: ToolStatus
    is_concurrency_safe: bool
    interrupt_behavior: InterruptBehavior
    cancellation_token: CancellationToken
    task: asyncio.Task[None] | None = None
    result: ToolResultBlock | None = None
    error: BaseException | None = None
    pending_progress: list[str] = field(default_factory=list)
    context_modifiers: list[dict[str, Any]] = field(default_factory=list)


def tool_description(tool_call: ToolCall) -> str:
    """Extract a short human-readable description from tool input."""
    for key in DESCRIPTION_KEYS:
        val = tool_call.input.get(key)
        if val and isinstance(val, str):
            return val[:DESC_MAX_LEN] + ("..." if len(val) > DESC_MAX_LEN else "")
    return tool_call.name


def wrap_tool_use_error(msg: str) -> str:
    """Wrap a terse error string in the ``<tool_use_error>`` tag the model
    is trained to recognize as a structured tool-use failure."""
    return f"<tool_use_error>{msg}</tool_use_error>"


def synthetic_error_content(
    tool_call: ToolCall,
    abort_reason: AbortReason | None,
    errored_description: str = "",
) -> str:
    """Build a model-facing error message for a cancelled tool.

    User interruptions return REJECT_MESSAGE verbatim — a full
    instructional sentence that tells the model to stop and wait.
    Sibling errors, streaming fallbacks, and timeouts wrap a terse
    description in ``<tool_use_error>`` XML tags so the model sees a
    structured cancellation signal on top of the ``is_error=True``
    provider flag (tags match the TypeScript reference at
    ``src/services/tools/StreamingToolExecutor.ts``).
    """
    if abort_reason == AbortReason.USER_INTERRUPTED:
        return REJECT_MESSAGE
    if abort_reason == AbortReason.SIBLING_ERROR:
        detail = f" (`{errored_description}`)" if errored_description else ""
        return wrap_tool_use_error(f"Cancelled: parallel tool call{detail} errored")
    if abort_reason == AbortReason.STREAMING_FALLBACK:
        return wrap_tool_use_error("Error: Streaming fallback - tool execution discarded")
    if abort_reason == AbortReason.TIMEOUT:
        return wrap_tool_use_error("Error: Tool execution timed out")
    return wrap_tool_use_error(f"Cancelled: {tool_description(tool_call)}")


class StreamingToolExecutor:
    """Executes tools as they stream in, with concurrency control and ordered results."""

    def __init__(
        self,
        execute_fn: Callable[[ToolCall, CancellationToken], Awaitable[ToolResultBlock]],
        concurrency_check_fn: Callable[[ToolCall], bool] = lambda _: False,
        interrupt_behavior_fn: Callable[[ToolCall], InterruptBehavior] = lambda _: (
            InterruptBehavior.BLOCK
        ),
        is_command_execution_fn: Callable[[ToolCall], bool] = lambda _: False,
        tool_exists_fn: Callable[[str], bool] = lambda _: True,
        context_modifier_fn: Callable[[ToolCall, str], dict[str, Any] | None] | None = None,
        parent_cancellation: CancellationToken | None = None,
        emitter: "EventEmitter | None" = None,
        thread_id: str = "",
        on_interruptible_change: Callable[[bool], None] | None = None,
        max_concurrency: int = MAX_TOOL_USE_CONCURRENCY,
    ) -> None:
        self.execute_fn = execute_fn
        self.concurrency_check_fn = concurrency_check_fn
        self.interrupt_behavior_fn = interrupt_behavior_fn
        self.is_command_execution_fn = is_command_execution_fn
        self.tool_exists_fn = tool_exists_fn
        self.context_modifier_fn = context_modifier_fn
        self.parent_cancellation = parent_cancellation
        self.emitter = emitter
        self.thread_id = thread_id
        self.on_interruptible_change = on_interruptible_change
        self.semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self.tracked: list[TrackedTool] = []
        self.completion_event = asyncio.Event()
        self.progress_event = asyncio.Event()
        self.discarded = False
        self.yield_index = 0

        self.sibling_cancel = (
            create_child_token(parent_cancellation) if parent_cancellation else CancellationToken()
        )
        self.has_errored = False
        self.errored_tool_description = ""
        self.executing_safe_count = 0
        self.executing_unsafe_count = 0
        self.executing_cancel_count = 0
        self.executing_block_count = 0

        self.last_interruptible: bool | None = None

    def add_tool(self, tool_call: ToolCall) -> None:
        """Called during streaming as each tool_call event arrives."""
        if self.discarded:
            return

        if not self.tool_exists_fn(tool_call.name):
            tracked = TrackedTool(
                index=len(self.tracked),
                tool_call=tool_call,
                status=ToolStatus.COMPLETED,
                is_concurrency_safe=True,
                interrupt_behavior=InterruptBehavior.BLOCK,
                cancellation_token=CancellationToken(),
                result=ToolResultBlock(
                    tool_use_id=tool_call.id,
                    content=wrap_tool_use_error(
                        f"Error: No such tool available: {tool_call.name}",
                    ),
                    is_error=True,
                ),
            )
            self.tracked.append(tracked)
            self.completion_event.set()
            return

        is_safe = self.concurrency_check_fn(tool_call)
        interrupt = self.interrupt_behavior_fn(tool_call)

        token = CombinedCancellationToken(
            parent=self.parent_cancellation,
            sibling=self.sibling_cancel,
        )

        if self.parent_cancellation is not None:
            parent = self.parent_cancellation

            def bubble_to_parent() -> None:
                if (
                    token.abort_reason in (AbortReason.SIBLING_ERROR, AbortReason.TIMEOUT)
                    or self.discarded
                    or parent.cancelled
                ):
                    return
                parent.cancel(token.reason, token.abort_reason)

            token.on_cancel(bubble_to_parent)

        tracked = TrackedTool(
            index=len(self.tracked),
            tool_call=tool_call,
            status=ToolStatus.QUEUED,
            is_concurrency_safe=is_safe,
            interrupt_behavior=interrupt,
            cancellation_token=token,
        )
        self.tracked.append(tracked)
        self.process_queue()

    def can_execute(self, candidate: TrackedTool) -> bool:
        """Check if a tool can start executing given current state."""
        if self.executing_safe_count == 0 and self.executing_unsafe_count == 0:
            return True
        if not candidate.is_concurrency_safe:
            return False
        return self.executing_unsafe_count == 0

    def process_queue(self) -> None:
        """Start eligible queued tools. Stops at first non-executable tool."""
        started_any = False
        for tracked in self.tracked:
            if tracked.status != ToolStatus.QUEUED:
                continue
            if not self.can_execute(tracked):
                break
            tracked.status = ToolStatus.EXECUTING
            if tracked.is_concurrency_safe:
                self.executing_safe_count += 1
            else:
                self.executing_unsafe_count += 1
            if tracked.interrupt_behavior == InterruptBehavior.CANCEL:
                self.executing_cancel_count += 1
            else:
                self.executing_block_count += 1
            tracked.task = asyncio.ensure_future(self.execute_one(tracked))
            started_any = True
        if started_any:
            self.update_interruptible_state()

    def update_interruptible_state(self) -> None:
        """Recompute interruptible flag and fire callback/event if it changed.

        ALL-or-nothing rule: interruptible is True only when at least one
        tool is executing AND every executing tool is CANCEL.  One BLOCK
        tool in the mix ⇒ not interruptible.  Zero executing tools ⇒ not
        interruptible either (nothing to interrupt).
        """
        new_state = self.executing_cancel_count > 0 and self.executing_block_count == 0
        if new_state == self.last_interruptible:
            return
        self.last_interruptible = new_state

        if self.on_interruptible_change is not None:
            try:
                self.on_interruptible_change(new_state)
            except Exception:
                logger.exception("on_interruptible_change callback failed")

        if self.emitter is not None:
            from src.engine_core.events import InterruptibleStateChanged

            try:
                asyncio.ensure_future(
                    self.emitter.emit(
                        InterruptibleStateChanged(
                            thread_id=self.thread_id,
                            interruptible=new_state,
                            tool_count=self.executing_cancel_count + self.executing_block_count,
                        )
                    )
                )
            except RuntimeError:
                pass

    def maybe_fire_sibling_abort(self, tracked: TrackedTool) -> None:
        """Fire sibling abort if this is a command-execution tool that errored."""
        if self.has_errored or not self.is_command_execution_fn(tracked.tool_call):
            return
        self.has_errored = True
        self.errored_tool_description = tool_description(tracked.tool_call)
        self.sibling_cancel.cancel(
            f"Sibling error: {self.errored_tool_description}",
            AbortReason.SIBLING_ERROR,
        )

    async def execute_one(self, tracked: TrackedTool) -> None:
        """Run one tool via execute_fn. Handles cancellation and errors."""
        tool_start = time.monotonic()
        try:
            if tracked.cancellation_token.cancelled:
                tracked.result = ToolResultBlock(
                    tool_use_id=tracked.tool_call.id,
                    content=synthetic_error_content(
                        tracked.tool_call,
                        tracked.cancellation_token.abort_reason,
                        self.errored_tool_description,
                    ),
                    is_error=True,
                )
                return

            async with self.semaphore:
                tracked.result = await self.execute_fn(
                    tracked.tool_call,
                    tracked.cancellation_token,
                )

            if (
                tracked.cancellation_token.cancelled
                and tracked.cancellation_token.abort_reason is not None
            ):
                tracked.result = ToolResultBlock(
                    tool_use_id=tracked.tool_call.id,
                    content=synthetic_error_content(
                        tracked.tool_call,
                        tracked.cancellation_token.abort_reason,
                        self.errored_tool_description,
                    ),
                    is_error=True,
                )

            if tracked.result and tracked.result.is_error:
                self.maybe_fire_sibling_abort(tracked)

            if (
                self.context_modifier_fn is not None
                and not tracked.is_concurrency_safe
                and tracked.result is not None
                and not tracked.result.is_error
            ):
                try:
                    modifier = self.context_modifier_fn(
                        tracked.tool_call,
                        tracked.result.content if isinstance(tracked.result.content, str) else "",
                    )
                    if modifier is not None:
                        tracked.context_modifiers.append(modifier)
                except Exception:
                    logger.warning(
                        "context_modifier_fn raised for tool %s; ignoring",
                        tracked.tool_call.name,
                        exc_info=True,
                    )

        except Exception as e:
            tracked.error = e
            tracked.result = ToolResultBlock(
                tool_use_id=tracked.tool_call.id,
                content=f"Error: {e}",
                is_error=True,
            )
            self.maybe_fire_sibling_abort(tracked)
        finally:
            if tracked.is_concurrency_safe:
                self.executing_safe_count -= 1
            else:
                self.executing_unsafe_count -= 1
            if tracked.interrupt_behavior == InterruptBehavior.CANCEL:
                self.executing_cancel_count -= 1
            else:
                self.executing_block_count -= 1
            tracked.status = ToolStatus.COMPLETED
            tool_duration_ms = (time.monotonic() - tool_start) * 1000
            try:
                from src.raven.events import ToolCallEvent
                from src.raven.api import fire_and_forget

                fire_and_forget(
                    ToolCallEvent(
                        tool_name=tracked.tool_call.name,
                        duration_ms=tool_duration_ms,
                        success=not (tracked.result and tracked.result.is_error),
                        agent_step_id=self.thread_id,
                    )
                )
            except Exception:
                pass
            self.completion_event.set()
            self.progress_event.set()
            self.update_interruptible_state()
            self.process_queue()


    def get_completed_results(self) -> Generator[ToolResultBlock, None, None]:
        """Non-blocking: yield completed results in order. Stops at first incomplete.

        Called inside the streaming loop between tool_call events so results
        flow to the caller while the LLM stream is still producing.
        """
        if self.discarded:
            return
        while self.yield_index < len(self.tracked):
            tracked = self.tracked[self.yield_index]
            if tracked.status == ToolStatus.COMPLETED:
                self.raise_if_denial(tracked)
                tracked.status = ToolStatus.YIELDED
                assert tracked.result is not None
                yield tracked.result
                self.yield_index += 1
                continue
            if not tracked.is_concurrency_safe:
                break
            break

    async def results(self) -> AsyncGenerator[ToolResultBlock, None]:
        """Yield completed results in insertion order. Waits as needed.

        Called after the stream ends to drain all remaining results.
        """
        while self.yield_index < len(self.tracked):
            tracked = self.tracked[self.yield_index]

            if tracked.status in (ToolStatus.COMPLETED, ToolStatus.YIELDED):
                if tracked.status == ToolStatus.COMPLETED:
                    self.raise_if_denial(tracked)
                    tracked.status = ToolStatus.YIELDED
                    assert tracked.result is not None
                    yield tracked.result
                self.yield_index += 1
                continue

            self.completion_event.clear()
            await self.completion_event.wait()

    @staticmethod
    def raise_if_denial(tracked: TrackedTool) -> None:
        if tracked.error is not None:
            from src.sandbox.permissions import DenialLimitExceeded

            if isinstance(tracked.error, DenialLimitExceeded):
                raise tracked.error

    def discard(self) -> None:
        """Cancel all pending tools. Queued get synthetic errors.
        Tools with interrupt_behavior=BLOCK keep running to avoid orphaned subprocesses.
        """
        self.discarded = True
        for tracked in self.tracked:
            if tracked.status == ToolStatus.QUEUED:
                tracked.result = ToolResultBlock(
                    tool_use_id=tracked.tool_call.id,
                    content=synthetic_error_content(
                        tracked.tool_call,
                        AbortReason.STREAMING_FALLBACK,
                    ),
                    is_error=True,
                )
                tracked.status = ToolStatus.COMPLETED
            elif (
                tracked.status == ToolStatus.EXECUTING
                and tracked.interrupt_behavior == InterruptBehavior.CANCEL
            ):
                tracked.cancellation_token.cancel(
                    "Streaming fallback — discarded",
                    AbortReason.STREAMING_FALLBACK,
                )
        self.completion_event.set()

    def cancel_interruptible(
        self,
        reason: str = "User interrupted",
        abort_reason: AbortReason = AbortReason.USER_INTERRUPTED,
    ) -> None:
        """Cancel only tools with interrupt_behavior=CANCEL. Block tools keep running."""
        for tracked in self.tracked:
            if (
                tracked.status == ToolStatus.EXECUTING
                and tracked.interrupt_behavior == InterruptBehavior.CANCEL
            ):
                tracked.cancellation_token.cancel(reason, abort_reason)

    def report_progress(self, tool_use_id: str, message: str) -> None:
        """Record a progress message for a tool. Bypasses result ordering."""
        for tracked in self.tracked:
            if tracked.tool_call.id == tool_use_id:
                tracked.pending_progress.append(message)
                self.progress_event.set()
                break

    def drain_progress(self) -> list[tuple[str, str]]:
        """Drain all pending progress messages. Returns [(tool_use_id, message), ...]."""
        out: list[tuple[str, str]] = []
        for tracked in self.tracked:
            for msg in tracked.pending_progress:
                out.append((tracked.tool_call.id, msg))
            tracked.pending_progress.clear()
        return out

    def drain_context_modifiers(self) -> list[dict[str, Any]]:
        """Drain accumulated context modifiers from completed serial tools.

        Returns a list of modifier dicts in execution order.  The owning
        thread applies these to its execution context so subsequent tools
        see updated state (e.g. changed working directory).
        """
        out: list[dict[str, Any]] = []
        for tracked in self.tracked:
            if tracked.context_modifiers:
                out.extend(tracked.context_modifiers)
                tracked.context_modifiers.clear()
        return out


    @property
    def tool_count(self) -> int:
        return len(self.tracked)

    @property
    def has_pending(self) -> bool:
        return any(t.status in (ToolStatus.QUEUED, ToolStatus.EXECUTING) for t in self.tracked)

    @property
    def has_completed_results(self) -> bool:
        if self.yield_index >= len(self.tracked):
            return False
        return self.tracked[self.yield_index].status == ToolStatus.COMPLETED

    def cleanup(self) -> None:
        """Release per-turn resources.  Idempotent, safe after discard().

        Called by the owning thread at turn end to:
          1. Wake any lingering waiters on completion/progress events.
          2. Dispose per-tool :class:`CombinedCancellationToken` timers
             and neutralize late parent/sibling fires so a dead turn's
             tools cannot leak timer callbacks or fire stale cancels.

        Listener lists on the parent/sibling source tokens are
        naturally cleaned up when those sources next cancel
        (``CancellationToken.cancel`` clears its callback list after
        invoking each callback).
        """
        for tracked in self.tracked:
            tok = tracked.cancellation_token
            if isinstance(tok, CombinedCancellationToken):
                tok.cleanup()
        self.completion_event.set()
        self.progress_event.set()
