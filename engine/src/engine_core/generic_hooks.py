"""generic_hooks — Reusable lifecycle hook registry for the intelligence engine.

Sibling of ``pre_tool_hooks.py`` / ``post_tool_hooks.py`` / ``stop_hooks.py``.
Those modules each ship a bespoke registry with event-specific aggregation
rules (deny-vs-allow, retry-on-blocking-error, etc.). Most other lifecycle
events (PreCompact, PostCompact, SubagentStart, SubagentStop,
UserPromptSubmit, TaskCreated, TaskCompleted, PostToolUseFailure, etc.)
only need a plain "fire every hook in parallel, collect additional_contexts,
accumulate prevent_continuation, surface any blocking_error." This module
provides a single generic registry that covers all of them.

Design
------
``GenericHookResult`` is the shared result schema — same shape for every
simple lifecycle event:

- ``additional_context``:  string appended to the agent's next system
  prompt (or injected into the triggering operation, per dispatch site).
- ``prevent_continuation``: if True the dispatch site is asked to abort
  whatever it was about to do (e.g. skip the compact, skip the subagent
  spawn, ignore the prompt). Semantics are per-event — generic registry
  just propagates the flag.
- ``blocking_error``: opaque error string surfaced to the caller for
  logging / error handling.
- ``updated_payload``: optional dict replacement for the event input
  payload (e.g. UserPromptSubmit can rewrite the user text).

``AggregatedGenericResult`` merges all hook results for one dispatch:
- ``additional_contexts`` — registration-order list.
- ``prevent_continuation`` — any hook True → True.
- ``blocking_errors`` — registration-order list.
- ``updated_payload`` — last non-None wins (deterministic by order).
- ``hook_outcomes`` — per-hook telemetry for the emitted event.

Registry
--------
``GenericHookRegistry[InputT]`` is parameterised on the input dataclass
type. Every hook is an async callable ``(InputT) -> GenericHookResult``.
``execute_all`` runs them in parallel with per-hook timeout and fail-open
semantics identical to the stop / pre-tool / post-tool registries. A
``CancelledError`` inside a hook re-raises so cooperative cancellation
(SIGINT, Thread.cancel) still works.

Three hook implementations ship:

``CallableGenericHook``
    Wraps an async Python callable. Used by SDK/test code.

``CommandGenericHook``
    Spawns a subprocess (never ``shell=True``). Writes the input dataclass
    as JSON to stdin; reads stdout as JSON ``GenericHookResult``. Exit
    codes: ``0`` = success, ``2`` = blocking (stdout → blocking_error),
    anything else = fail-open. Optional plugin env vars (PLUGIN_ROOT,
    PLUGIN_DATA, PLUGIN_OPTION_*) are injected when set.

``HttpGenericHook``
    POSTs the input as JSON to a webhook URL (curl-backed, same pattern
    as ``hook_executors.HttpPreToolUseHook``). Fail-open on any network
    error or non-2xx response.

Extensibility for AGENT / PROMPT hook types is handled by subclassing
``CallableGenericHook`` — those types live in ``agent_hooks.py`` and
``prompt_hooks.py`` so they can import heavier dependencies (herald,
sub_agent) without dragging them into every event's registry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Awaitable, Callable, Generic, Protocol, TypeVar, cast


logger = logging.getLogger(__name__)


@dataclass
class GenericHookResult:
    """One generic hook's verdict on a lifecycle event.

    All fields are optional — a hook that only wants to observe can
    return a bare ``GenericHookResult()``.
    """

    additional_context: str = ""
    prevent_continuation: bool = False
    blocking_error: str | None = None
    updated_payload: dict[str, Any] | None = None
    permission_decision: str | None = None


@dataclass
class AggregatedGenericResult:
    """Merged verdict from all hooks for a single lifecycle event."""

    additional_contexts: list[str] = field(default_factory=list)
    prevent_continuation: bool = False
    blocking_errors: list[str] = field(default_factory=list)
    updated_payload: dict[str, Any] | None = None
    hook_outcomes: list[dict] = field(default_factory=list)
    permission_decision: str | None = None


InputT = TypeVar("InputT")
InputT_contra = TypeVar("InputT_contra", contravariant=True)


class GenericHook(Protocol[InputT_contra]):
    """A generic lifecycle hook callable."""

    async def execute(self, input: InputT_contra) -> GenericHookResult:  # noqa: A002
        ...


@dataclass
class RegisteredGenericHook(Generic[InputT]):
    name: str
    hook: GenericHook[InputT]
    timeout_s: float


class GenericHookRegistry(Generic[InputT]):
    """Holds ``GenericHook`` instances; runs them concurrently per dispatch.

    Identical fail-open / timeout / cooperative-cancellation semantics as
    ``StopHookRegistry``. Cheap ``has_hooks()`` for zero-overhead short-
    circuiting in dispatch sites.

    Parameters
    ----------
    event_name:
        Human-readable label used in log lines and hook_outcomes entries
        (e.g. ``"PreCompact"``, ``"SubagentStart"``).
    """

    def __init__(self, event_name: str) -> None:
        self.event_name = event_name
        self.hooks: list[RegisteredGenericHook[InputT]] = []

    def register(
        self,
        name: str,
        hook: GenericHook[InputT],
        timeout_s: float = 60.0,
    ) -> None:
        self.hooks = [h for h in self.hooks if h.name != name]
        self.hooks.append(RegisteredGenericHook(name=name, hook=hook, timeout_s=timeout_s))

    def unregister(self, name: str) -> None:
        self.hooks = [h for h in self.hooks if h.name != name]

    def has_hooks(self) -> bool:
        return bool(self.hooks)

    def hook_count(self) -> int:
        return len(self.hooks)

    async def execute_all(self, input: InputT) -> AggregatedGenericResult:  # noqa: A002
        if not self.hooks:
            return AggregatedGenericResult()

        registered = list(self.hooks)

        async def run_one(
            reg: RegisteredGenericHook[InputT],
        ) -> tuple[RegisteredGenericHook[InputT], GenericHookResult | BaseException, float]:
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

        agg = AggregatedGenericResult()
        for reg, outcome, duration_ms in raw:
            entry: dict[str, Any] = {
                "name": reg.name,
                "event": self.event_name,
                "ok": False,
                "duration_ms": round(duration_ms, 2),
                "prevented": False,
                "blocked": False,
                "injected_context": False,
                "updated_payload": False,
                "error": "",
            }
            if isinstance(outcome, BaseException):
                if isinstance(outcome, asyncio.TimeoutError):
                    entry["error"] = "timeout"
                else:
                    entry["error"] = f"{type(outcome).__name__}: {outcome}"
                logger.debug(
                    "%s hook %s failed: %s (fail-open)",
                    self.event_name,
                    reg.name,
                    entry["error"],
                )
            else:
                entry["ok"] = True
                res = outcome
                if res.additional_context:
                    agg.additional_contexts.append(res.additional_context)
                    entry["injected_context"] = True
                if res.prevent_continuation:
                    agg.prevent_continuation = True
                    entry["prevented"] = True
                if res.blocking_error:
                    agg.blocking_errors.append(res.blocking_error)
                    entry["blocked"] = True
                if res.updated_payload is not None:
                    agg.updated_payload = res.updated_payload
                    entry["updated_payload"] = True
                decision = (res.permission_decision or "").strip().lower()
                if decision in ("allow", "deny"):
                    entry["permission_decision"] = decision
                    if decision == "deny" or agg.permission_decision is None:
                        agg.permission_decision = decision
                    if decision == "deny":
                        agg.permission_decision = "deny"
            agg.hook_outcomes.append(entry)

        return agg


class CallableGenericHook(Generic[InputT]):
    """Wraps an async Python callable as a ``GenericHook``."""

    def __init__(
        self,
        fn: Callable[[InputT], Awaitable[GenericHookResult]],
        timeout_s: float = 60.0,
    ) -> None:
        self.fn = fn
        self.timeout_s = timeout_s

    async def execute(self, input: InputT) -> GenericHookResult:  # noqa: A002
        return await self.fn(input)


class CommandGenericHook(Generic[InputT]):
    """Runs a shell command as a generic lifecycle hook.

    Subprocess invocation, stdin JSON, stdout JSON, exit codes:
    ``0`` success, ``2`` blocking, anything else fail-open. Mirrors the
    CommandStopHook contract so a single hook script can serve multiple
    events.

    ``env_overrides`` lets the settings loader inject plugin-scoped env
    vars (PLUGIN_ROOT, PLUGIN_DATA, PLUGIN_OPTION_*, HOOK_EVENT_NAME,
    etc.) without repeating that logic in every hook type.
    """

    def __init__(
        self,
        command: list[str],
        timeout_s: float = 60.0,
        event_name: str = "",
        env_overrides: dict[str, str] | None = None,
    ) -> None:
        if not command:
            raise ValueError("CommandGenericHook requires a non-empty command")
        self.command = list(command)
        self.timeout_s = timeout_s
        self.event_name = event_name
        self.env_overrides: dict[str, str] = dict(env_overrides or {})

    async def execute(self, input: InputT) -> GenericHookResult:  # noqa: A002
        from src.engine_core.hook_executors import (
            build_hook_env,
            run_command_hook,
        )

        if is_dataclass(input):
            payload_obj: Any = asdict(cast(Any, input))
        elif isinstance(input, dict):
            payload_obj = input
        else:
            payload_obj = {"value": repr(input)}

        if self.event_name and isinstance(payload_obj, dict):
            payload_obj["hook_event_name"] = self.event_name

        payload = json.dumps(payload_obj).encode("utf-8")
        env = build_hook_env(self.event_name, overrides=self.env_overrides)

        run = await run_command_hook(self.command, payload, self.timeout_s, env=env)
        if run is None or run.returncode < 0:
            return GenericHookResult()

        if run.returncode == 0:
            if not run.stdout_text:
                return GenericHookResult()
            try:
                data = json.loads(run.stdout_text)
            except json.JSONDecodeError:
                return GenericHookResult()
            return result_from_dict(data)

        if run.returncode == 2:
            try:
                data = json.loads(run.stdout_text) if run.stdout_text else {}
                if isinstance(data, dict):
                    data.setdefault("blocking_error", run.stderr_text or "blocked by hook")
                    return result_from_dict(data)
            except json.JSONDecodeError:
                pass
            return GenericHookResult(
                blocking_error=run.stdout_text or run.stderr_text or "blocked by hook",
            )

        logger.debug(
            "CommandGenericHook(%s) exit=%s stderr=%s (fail-open)",
            self.event_name,
            run.returncode,
            run.stderr_text[:200],
        )
        return GenericHookResult()


class HttpGenericHook(Generic[InputT]):
    """POSTs the input JSON to a webhook URL. Fail-open on any error."""

    def __init__(
        self,
        url: str,
        timeout_s: float = 30.0,
        event_name: str = "",
    ) -> None:
        if not url:
            raise ValueError("HttpGenericHook requires a non-empty URL")
        self.url = url
        self.timeout_s = timeout_s
        self.event_name = event_name

    async def execute(self, input: InputT) -> GenericHookResult:  # noqa: A002
        from src.engine_core.hook_executors import post_json

        if is_dataclass(input):
            payload_obj: Any = asdict(cast(Any, input))
        elif isinstance(input, dict):
            payload_obj = input
        else:
            payload_obj = {"value": repr(input)}
        if self.event_name and isinstance(payload_obj, dict):
            payload_obj["hook_event_name"] = self.event_name
        body = json.dumps(payload_obj).encode("utf-8")
        try:
            data = await post_json(self.url, body, self.timeout_s)
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.debug(
                "HttpGenericHook(%s) POST %s failed: %s",
                self.event_name,
                self.url,
                exc,
            )
            return GenericHookResult()
        return result_from_dict(data)


def result_from_dict(data: Any) -> GenericHookResult:
    if not isinstance(data, dict):
        return GenericHookResult()
    payload = data.get("updated_payload")
    if payload is not None and not isinstance(payload, dict):
        payload = None
    blocking = data.get("blocking_error")
    return GenericHookResult(
        additional_context=str(data.get("additional_context", "") or ""),
        prevent_continuation=bool(data.get("prevent_continuation", False)),
        blocking_error=str(blocking) if blocking else None,
        updated_payload=payload,
    )
