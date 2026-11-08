"""post_tool_hooks — PostToolUse lifecycle hook system for the agent loop.

Sibling of ``pre_tool_hooks.py`` and ``stop_hooks.py``. Where pre-tool
hooks intercept calls *before* execution, post-tool hooks fire *after*
every tool result is ready — letting external code observe or annotate
results before the model sees them.

A post-tool hook is an async callable that receives a snapshot of the
completed call (``PostToolHookContext``) and returns a verdict
(``PostToolUseHookResult``) with two effects:

1. **additional_context** — if non-empty, appended to the tool result
   so the model sees it in the same turn. Multiple hooks contribute
   separate labeled blocks in registration order.
2. **prevent_continuation** — signals the agent loop to exit after the
   current tool batch, identical to the pre-tool hook flag.

The ``PostToolUseHookRegistry`` runs all hooks concurrently per call
(``asyncio.gather``), each wrapped in ``asyncio.wait_for``. Slow,
crashing, or hung hooks are **fail-open** — execution continues.
``CancelledError`` re-raises for cooperative cancellation.

``PostToolHookContext`` (the input snapshot) is unchanged from its
original definition — it is frozen, fields are stable, and the agent
loop already populates it. New hooks should consume this context
directly via ``CallablePostToolUseHook`` or ``CommandPostToolUseHook``.

Reference parity: ``PostToolHookContext`` maps to the TypeScript SDK's
``PostToolUseHookInputSchema`` (``tool_name``, ``tool_input``,
``tool_response`` / ``result_text``, ``tool_use_id``).
``additional_context`` maps to the SDK's ``additionalContext`` response
field. ``updatedMCPToolOutput`` is intentionally absent — our MCP
tooling does not expose a separate output-replacement pathway at this
layer.

Two hook implementations ship in this module:

``CallablePostToolUseHook``
    Wraps an async Python callable. Simplest path for test frameworks
    and SDK-registered hooks.

``CommandPostToolUseHook``
    Spawns a subprocess (never ``shell=True``). Writes
    ``PostToolHookContext`` as JSON to stdin; reads stdout as JSON
    ``PostToolUseHookResult``. Exit codes: ``0`` = success (parse JSON
    or treat as no-op), ``2`` = prevent_continuation with optional
    additional_context from stdout, anything else = fail-open.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Protocol


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PostToolHookContext:
    """Snapshot of execution state passed to a post-tool hook.

    Frozen so hooks can't mutate it. Future fields land here without
    breaking the Protocol signature.

    ``hook_event_name`` is always ``"PostToolUse"`` — present for
    wire-format compatibility with the reference CLI hook protocol.
    """

    tool_name: str
    tool_input: dict[str, Any]
    result_text: str
    call_id: str
    batch_id: str = ""
    thread_id: str = ""
    permission_feedback: str = ""
    cancelled: bool = False
    hook_event_name: str = "PostToolUse"


@dataclass
class PostToolUseHookResult:
    """One hook's verdict on the completed tool call.

    ``additional_context``:
        Appended to the tool result as a labeled ``"hook"`` block.

    ``prevent_continuation``:
        Signals the agent loop to exit after the current tool batch,
        identical to the pre-tool prevent_continuation flag.

    ``updated_tool_output``:
        If non-``None``, replaces the entire tool result before the model
        sees it. Last non-``None`` value across all hooks wins (later hooks
        can refine earlier replacements).
    """

    additional_context: str = ""
    prevent_continuation: bool = False
    updated_tool_output: str | None = None


@dataclass
class AggregatedPostToolResult:
    """Merged verdict from all post-tool hooks for one call."""

    additional_contexts: list[str] = field(default_factory=list)
    prevent_continuation: bool = False
    updated_tool_output: str | None = None
    hook_outcomes: list[dict] = field(default_factory=list)


class PostToolUseHook(Protocol):
    """A post-tool hook callable (registry style).

    Receives a ``PostToolHookContext`` and returns a
    ``PostToolUseHookResult``. Implementations should catch their own
    exceptions and return a no-op result on failure; uncaught exceptions
    are logged by the registry and treated as fail-open.
    """

    async def execute(self, ctx: PostToolHookContext) -> PostToolUseHookResult: ...


@dataclass
class RegisteredPostHook:
    name: str
    hook: PostToolUseHook
    timeout_s: float


class PostToolUseHookRegistry:
    """Holds ``PostToolUseHook`` instances; runs them concurrently per call.

    ``execute_all`` wraps every hook in ``asyncio.wait_for`` so one slow
    hook cannot stall the result pipeline. Exceptions and timeouts are
    fail-open — additional_context from a failed hook is simply omitted.
    ``CancelledError`` re-raises so cooperative cancellation works.

    Aggregation:
    - ``additional_contexts`` in registration order.
    - ``prevent_continuation`` = ``any(hook.prevent_continuation)``.
    - ``hook_outcomes`` per hook for ``PostToolUseHookExecuted`` event.
    """

    def __init__(self) -> None:
        self.hooks: list[RegisteredPostHook] = []

    def register(self, name: str, hook: PostToolUseHook, timeout_s: float = 60.0) -> None:
        """Register a post-tool hook under a human-readable name.

        Re-registering the same name replaces the prior hook.
        """
        self.hooks = [h for h in self.hooks if h.name != name]
        self.hooks.append(RegisteredPostHook(name=name, hook=hook, timeout_s=timeout_s))

    def unregister(self, name: str) -> None:
        """Remove a previously-registered hook by name (no-op if absent)."""
        self.hooks = [h for h in self.hooks if h.name != name]

    def has_hooks(self) -> bool:
        """Cheap zero-overhead check used by the agent loop."""
        return bool(self.hooks)

    async def execute_all(self, ctx: PostToolHookContext) -> AggregatedPostToolResult:
        """Run all registered hooks in parallel and aggregate results.

        Returns an ``AggregatedPostToolResult`` whose lists are ordered by
        registration order (not completion order) for determinism.
        """
        if not self.hooks:
            return AggregatedPostToolResult()

        registered = list(self.hooks)

        async def run_one(
            reg: RegisteredPostHook,
        ) -> tuple[RegisteredPostHook, PostToolUseHookResult | BaseException, float]:
            t0 = time.monotonic()
            try:
                res = await asyncio.wait_for(reg.hook.execute(ctx), timeout=reg.timeout_s)
                return reg, res, (time.monotonic() - t0) * 1000.0
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 — fail-open
                return reg, exc, (time.monotonic() - t0) * 1000.0

        tasks = [run_one(reg) for reg in registered]
        try:
            raw = await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            raise

        agg = AggregatedPostToolResult()
        for reg, outcome, duration_ms in raw:
            entry: dict[str, Any] = {
                "name": reg.name,
                "ok": False,
                "duration_ms": round(duration_ms, 2),
                "prevented": False,
                "error": "",
            }
            if isinstance(outcome, BaseException):
                if isinstance(outcome, asyncio.TimeoutError):
                    entry["error"] = "timeout"
                else:
                    entry["error"] = f"{type(outcome).__name__}: {outcome}"
                logger.debug(
                    "post-tool hook %s failed: %s (fail-open)",
                    reg.name,
                    entry["error"],
                )
            else:
                entry["ok"] = True
                res: PostToolUseHookResult = outcome
                if res.additional_context:
                    agg.additional_contexts.append(res.additional_context)
                if res.prevent_continuation:
                    agg.prevent_continuation = True
                    entry["prevented"] = True
                if res.updated_tool_output is not None:
                    agg.updated_tool_output = res.updated_tool_output
                    entry["updated_output"] = True
            agg.hook_outcomes.append(entry)

        return agg


class CallablePostToolUseHook:
    """Wraps an async Python callable as a ``PostToolUseHook``.

    The wrapped callable receives a ``PostToolHookContext`` and must
    return a ``PostToolUseHookResult``.
    """

    def __init__(
        self,
        fn: Callable[[PostToolHookContext], Awaitable[PostToolUseHookResult]],
        timeout_s: float = 60.0,
    ) -> None:
        self.fn = fn
        self.timeout_s = timeout_s

    async def execute(self, ctx: PostToolHookContext) -> PostToolUseHookResult:
        return await self.fn(ctx)


class CommandPostToolUseHook:
    """Runs a shell command as a post-tool hook.

    Spawned via ``asyncio.create_subprocess_exec`` — never ``shell=True``.

    Protocol:
    - ``PostToolHookContext`` written to stdin as a single JSON object.
    - Subprocess writes ``PostToolUseHookResult`` to stdout as JSON.
    - Exit codes:
        - ``0`` — success; stdout parsed as JSON (or no-op).
        - ``2`` — prevent_continuation; stdout treated as
          additional_context text (or JSON with those fields).
        - anything else — fail-open (logged, no-op).
    - On timeout the process is hard-killed and a no-op result is
      returned.
    """

    def __init__(
        self,
        command: list[str],
        timeout_s: float = 60.0,
        env_overrides: dict[str, str] | None = None,
    ) -> None:
        if not command:
            raise ValueError("CommandPostToolUseHook requires a non-empty command")
        self.command = list(command)
        self.timeout_s = timeout_s
        self.env_overrides: dict[str, str] = dict(env_overrides or {})

    async def execute(self, ctx: PostToolHookContext) -> PostToolUseHookResult:
        from src.engine_core.hook_executors import (
            build_hook_env,
            run_command_hook,
        )

        payload = json.dumps(asdict(ctx)).encode("utf-8")
        env = build_hook_env(
            "PostToolUse",
            tool_name=ctx.tool_name,
            tool_use_id=ctx.call_id,
            session_id=ctx.thread_id,
            overrides=self.env_overrides,
        )
        run = await run_command_hook(self.command, payload, self.timeout_s, env=env)
        if run is None or run.returncode < 0:
            return PostToolUseHookResult()

        if run.returncode == 0:
            if not run.stdout_text:
                return PostToolUseHookResult()
            try:
                data = json.loads(run.stdout_text)
            except json.JSONDecodeError:
                return PostToolUseHookResult(additional_context=run.stdout_text)
            return result_from_dict(data)

        if run.returncode == 2:
            try:
                data = json.loads(run.stdout_text) if run.stdout_text else {}
                if isinstance(data, dict):
                    data.setdefault("prevent_continuation", True)
                    return result_from_dict(data)
            except json.JSONDecodeError:
                pass
            return PostToolUseHookResult(
                prevent_continuation=True,
                additional_context=run.stdout_text or run.stderr_text or "",
            )

        logger.debug(
            "CommandPostToolUseHook exit=%s stderr=%s (fail-open)",
            run.returncode,
            run.stderr_text[:200],
        )
        return PostToolUseHookResult()


def result_from_dict(data: Any) -> PostToolUseHookResult:
    """Build a ``PostToolUseHookResult`` from a loose dict."""
    if not isinstance(data, dict):
        return PostToolUseHookResult()
    updated = data.get("updated_tool_output")
    if updated is not None and not isinstance(updated, str):
        updated = None
    return PostToolUseHookResult(
        additional_context=str(data.get("additional_context", "") or ""),
        prevent_continuation=bool(data.get("prevent_continuation", False)),
        updated_tool_output=updated,
    )
