"""pre_tool_hooks — PreToolUse lifecycle hook system for the agent loop.

Sibling of ``stop_hooks.py`` and ``post_tool_hooks.py``. Where stop hooks
run after every agent turn to decide whether the loop continues, pre-tool
hooks fire *before* every tool call — letting external code intercept,
deny, or mutate tool invocations before they reach the permission system
and executor.

A pre-tool hook is an async callable that receives a snapshot of the
pending call (``PreToolUseHookInput``) and returns a verdict
(``PreToolUseHookResult``) with three orthogonal effects:

1. **decision** — ``"deny"`` hard-blocks the tool call (returned as a
   permission-denied result), ``"allow"`` bypasses the engine's own
   permission rules, ``"inherit"`` (default) defers to the normal
   permission pipeline.
2. **updated_input** — if non-``None``, replaces ``tool_input`` for the
   remainder of the call chain (permission check, executor, post-tool
   hooks). Last non-``None`` value in registration order wins.
3. **additional_context** — if non-empty, appended to the tool result
   so the model sees it in the same turn.

Two further effects mirror the stop-hook protocol:

4. **prevent_continuation** — sets ``Thread.tool_hook_prevented``
   so the agent loop exits with ``HOOK_PREVENTED`` after the current
   tool batch finishes.
5. **blocking_error** — not implemented for pre-tool hooks (the timing
   is too early for a meaningful retry turn). Kept in the result type
   for schema symmetry with stop hooks but ignored by the registry.

Aggregation rules (``AggregatedPreToolResult``):

- ``denied`` = ``True`` if ANY hook returned ``decision="deny"``
  (fail-secure; a single denier blocks the tool).
- ``deny_reason`` = reason from the *first* denying hook
  (registration order for determinism).
- ``allowed`` = ``True`` if ANY hook returned ``decision="allow"``
  AND no hook denied (deny beats allow).
- ``updated_input`` = last non-``None`` value from hooks in
  registration order (later hooks can refine earlier mutations).
- ``additional_contexts`` = all non-empty strings in registration
  order (appended as separate labeled blocks).
- ``prevent_continuation`` = ``True`` if ANY hook set it.
- ``hook_outcomes`` = one dict per hook for observability
  (mirrors ``StopHookExecuted`` payload structure).

Execution model: identical to ``StopHookRegistry`` — all hooks run
concurrently via ``asyncio.gather``, each wrapped in
``asyncio.wait_for``. Slow, crashing, or hung hooks are **fail-open**:
the tool call proceeds with ``decision="inherit"``. ``CancelledError``
re-raises for cooperative cancellation.

Two hook implementations ship in this module:

``CallablePreToolUseHook``
    Wraps an async Python callable. Simplest path for test frameworks
    and SDK-registered hooks.

``CommandPreToolUseHook``
    Spawns a subprocess (never ``shell=True``). Writes
    ``PreToolUseHookInput`` as JSON to stdin; reads stdout as JSON
    ``PreToolUseHookResult``. Exit codes: ``0`` = success (parse JSON
    or treat as inherit), ``2`` = deny (stdout is the deny reason or
    JSON with a ``deny_reason`` field), anything else = fail-open.

Reference parity: ``PreToolUseHookInput`` maps to the TypeScript SDK's
``PreToolUseHookInputSchema`` (``hook_event_name``, ``tool_name``,
``tool_input``, ``tool_use_id``). ``PreToolUseHookResult.decision``
maps to ``permissionDecision``; ``updated_input`` maps to
``updatedInput``; ``additional_context`` maps to ``additionalContext``.
``session_id`` / ``transcript_path`` are absent — our session ID is
``thread_id`` and we use SQLite not filesystem transcripts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Protocol


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreToolUseHookInput:
    """Snapshot of the pending tool call, passed to every pre-tool hook.

    Frozen so hooks cannot mutate shared state. ``thread_id`` is our
    session identifier (no separate ``session_id`` in this engine).
    ``hook_event_name`` is always ``"PreToolUse"`` — present for
    wire-format compatibility with the reference CLI hook protocol so
    command-based hooks can use the same dispatcher logic.
    """

    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str
    thread_id: str
    cwd: str = ""
    hook_event_name: str = "PreToolUse"


@dataclass
class PreToolUseHookResult:
    """One hook's verdict on the pending tool call.

    ``decision``:
        ``"deny"``    — block the tool; ``deny_reason`` surfaced to model.
        ``"allow"``   — skip the engine's permission pipeline.
        ``"inherit"`` — defer to normal permission rules (default).

    ``updated_input``:
        If non-``None``, replaces ``tool_input`` for the rest of the
        call chain. Last non-``None`` value (by registration order) wins.

    ``additional_context``:
        Appended to the tool result so the model sees it. Non-empty
        strings only.

    ``prevent_continuation``:
        Signals the agent loop to exit after the current tool batch.

    ``blocking_error``:
        If non-empty, the tool is NOT executed; this string is returned to
        the model as the tool result. The loop continues (unlike ``deny``
        which is framed as a permission denial, or ``prevent_continuation``
        which exits the loop). First non-empty value in registration order
        wins across all hooks.
    """

    decision: str = "inherit"  # "allow" | "deny" | "inherit"
    deny_reason: str = ""
    updated_input: dict[str, Any] | None = None
    additional_context: str = ""
    prevent_continuation: bool = False
    blocking_error: str | None = None


@dataclass
class AggregatedPreToolResult:
    """Merged verdict from all registered pre-tool hooks for one call."""

    denied: bool = False
    deny_reason: str = ""
    allowed: bool = False  # explicit allow, only if no deny
    updated_input: dict[str, Any] | None = None
    additional_contexts: list[str] = field(default_factory=list)
    prevent_continuation: bool = False
    blocking_error: str | None = None  # first non-empty wins; returns error as tool result
    hook_outcomes: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class PreToolUseHook(Protocol):
    """A pre-tool hook callable.

    Receives a ``PreToolUseHookInput`` and returns a
    ``PreToolUseHookResult``. Implementations should catch their own
    exceptions and return a ``decision="inherit"`` result on failure;
    uncaught exceptions are logged by the registry and treated as
    fail-open inherit outcomes.
    """

    async def execute(self, input: PreToolUseHookInput) -> PreToolUseHookResult: ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class RegisteredPreHook:
    name: str
    hook: PreToolUseHook
    timeout_s: float


class PreToolUseHookRegistry:
    """Holds ``PreToolUseHook`` instances; runs them concurrently per call.

    ``execute_all`` wraps every hook in ``asyncio.wait_for`` so one slow
    hook cannot stall the tool pipeline. Exceptions and timeouts become
    fail-open inherit outcomes — the tool call continues. ``CancelledError``
    re-raises so cooperative cancellation still works.

    Aggregation:
    - ``denied`` = ``any(hook.decision == "deny")`` — deny beats allow.
    - ``allowed`` = ``any(hook.decision == "allow") and not denied``.
    - ``updated_input`` = last non-``None`` value in registration order.
    - ``additional_contexts`` in registration order for determinism.
    - ``prevent_continuation`` = ``any(hook.prevent_continuation)``.
    - ``hook_outcomes`` per hook for ``PreToolUseHookExecuted`` event.
    """

    def __init__(self) -> None:
        self.hooks: list[RegisteredPreHook] = []

    def register(self, name: str, hook: PreToolUseHook, timeout_s: float = 60.0) -> None:
        """Register a pre-tool hook under a human-readable name.

        Re-registering the same name replaces the prior hook.
        """
        self.hooks = [h for h in self.hooks if h.name != name]
        self.hooks.append(RegisteredPreHook(name=name, hook=hook, timeout_s=timeout_s))

    def unregister(self, name: str) -> None:
        """Remove a previously-registered hook by name (no-op if absent)."""
        self.hooks = [h for h in self.hooks if h.name != name]

    def has_hooks(self) -> bool:
        """Cheap zero-overhead check used by the agent loop."""
        return bool(self.hooks)

    async def execute_all(self, input: PreToolUseHookInput) -> AggregatedPreToolResult:
        """Run all registered hooks in parallel and aggregate verdicts.

        Returns an ``AggregatedPreToolResult`` whose lists are ordered by
        registration order (not completion order) for determinism.
        """
        if not self.hooks:
            return AggregatedPreToolResult()

        registered = list(self.hooks)

        async def run_one(
            reg: RegisteredPreHook,
        ) -> tuple[RegisteredPreHook, PreToolUseHookResult | BaseException, float]:
            t0 = time.monotonic()
            try:
                res = await asyncio.wait_for(reg.hook.execute(input), timeout=reg.timeout_s)
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

        agg = AggregatedPreToolResult()
        first_deny_seen = False

        for reg, outcome, duration_ms in raw:
            entry: dict[str, Any] = {
                "name": reg.name,
                "ok": False,
                "duration_ms": round(duration_ms, 2),
                "decision": "inherit",
                "denied": False,
                "allowed": False,
                "prevented": False,
                "updated_input": False,
                "error": "",
            }
            if isinstance(outcome, BaseException):
                if isinstance(outcome, asyncio.TimeoutError):
                    entry["error"] = "timeout"
                else:
                    entry["error"] = f"{type(outcome).__name__}: {outcome}"
                logger.debug(
                    "pre-tool hook %s failed: %s (fail-open)",
                    reg.name,
                    entry["error"],
                )
            else:
                entry["ok"] = True
                res: PreToolUseHookResult = outcome
                entry["decision"] = res.decision

                if res.decision == "deny":
                    entry["denied"] = True
                    if not first_deny_seen:
                        agg.denied = True
                        agg.deny_reason = res.deny_reason
                        first_deny_seen = True

                elif res.decision == "allow":
                    entry["allowed"] = True
                    # allow is recorded but only applied if no deny (post-loop)

                if res.updated_input is not None:
                    agg.updated_input = res.updated_input
                    entry["updated_input"] = True

                if res.additional_context:
                    agg.additional_contexts.append(res.additional_context)

                if res.prevent_continuation:
                    agg.prevent_continuation = True
                    entry["prevented"] = True

                if res.blocking_error and agg.blocking_error is None:
                    agg.blocking_error = res.blocking_error
                    entry["blocked"] = True

            agg.hook_outcomes.append(entry)

        # Apply allow only when no hook denied.
        if not agg.denied:
            agg.allowed = any(
                e.get("allowed", False) for e in agg.hook_outcomes if e.get("ok", False)
            )

        return agg


# ---------------------------------------------------------------------------
# Hook implementations
# ---------------------------------------------------------------------------


class CallablePreToolUseHook:
    """Wraps an async Python callable as a ``PreToolUseHook``.

    The wrapped callable receives a ``PreToolUseHookInput`` and must
    return a ``PreToolUseHookResult``. Simplest path for test frameworks
    and SDK-registered hooks.
    """

    def __init__(
        self,
        fn: Callable[[PreToolUseHookInput], Awaitable[PreToolUseHookResult]],
        timeout_s: float = 60.0,
    ) -> None:
        self.fn = fn
        self.timeout_s = timeout_s

    async def execute(self, input: PreToolUseHookInput) -> PreToolUseHookResult:
        return await self.fn(input)


class CommandPreToolUseHook:
    """Runs a shell command as a pre-tool hook.

    Spawned via ``asyncio.create_subprocess_exec`` — never ``shell=True``.
    If shell evaluation is needed, the caller passes
    ``["sh", "-c", "..."]`` explicitly.

    Protocol:
    - ``PreToolUseHookInput`` written to stdin as a single JSON object.
    - Subprocess writes ``PreToolUseHookResult`` to stdout as JSON.
    - Exit codes:
        - ``0`` — success; stdout parsed as JSON result (or inherit).
        - ``2`` — deny; stdout is the deny reason or JSON with
          ``deny_reason`` / ``decision`` fields.
        - anything else — fail-open (logged, inherit).
    - On timeout the process is hard-killed and an inherit result is
      returned.
    """

    def __init__(
        self,
        command: list[str],
        timeout_s: float = 60.0,
        env_overrides: dict[str, str] | None = None,
    ) -> None:
        if not command:
            raise ValueError("CommandPreToolUseHook requires a non-empty command")
        self.command = list(command)
        self.timeout_s = timeout_s
        self.env_overrides: dict[str, str] = dict(env_overrides or {})

    async def execute(self, input: PreToolUseHookInput) -> PreToolUseHookResult:
        from src.engine_core.hook_executors import (
            build_hook_env,
            run_command_hook,
        )

        payload = json.dumps(asdict(input)).encode("utf-8")
        env = build_hook_env(
            "PreToolUse",
            tool_name=input.tool_name,
            tool_use_id=input.tool_use_id,
            session_id=input.thread_id,
            cwd=input.cwd,
            overrides=self.env_overrides,
        )
        run = await run_command_hook(self.command, payload, self.timeout_s, env=env)
        if run is None or run.returncode < 0:
            return PreToolUseHookResult()

        if run.returncode == 0:
            if not run.stdout_text:
                return PreToolUseHookResult()
            try:
                data = json.loads(run.stdout_text)
            except json.JSONDecodeError:
                return PreToolUseHookResult()
            return result_from_dict(data)

        if run.returncode == 2:
            # Explicit deny. JSON with fields takes priority; otherwise
            # the stdout text is the deny reason.
            try:
                data = json.loads(run.stdout_text) if run.stdout_text else {}
                if isinstance(data, dict):
                    data.setdefault("decision", "deny")
                    return result_from_dict(data)
            except json.JSONDecodeError:
                pass
            return PreToolUseHookResult(
                decision="deny",
                deny_reason=run.stdout_text or run.stderr_text or "denied by hook",
            )

        logger.debug(
            "CommandPreToolUseHook exit=%s stderr=%s (fail-open)",
            run.returncode,
            run.stderr_text[:200],
        )
        return PreToolUseHookResult()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def result_from_dict(data: Any) -> PreToolUseHookResult:
    """Build a ``PreToolUseHookResult`` from a loose dict."""
    if not isinstance(data, dict):
        return PreToolUseHookResult()
    decision = str(data.get("decision", "inherit") or "inherit")
    if decision not in ("allow", "deny", "inherit"):
        decision = "inherit"
    updated = data.get("updated_input")
    if updated is not None and not isinstance(updated, dict):
        updated = None
    blocking = data.get("blocking_error")
    return PreToolUseHookResult(
        decision=decision,
        deny_reason=str(data.get("deny_reason", "") or ""),
        updated_input=updated,
        additional_context=str(data.get("additional_context", "") or ""),
        prevent_continuation=bool(data.get("prevent_continuation", False)),
        blocking_error=str(blocking) if blocking else None,
    )
