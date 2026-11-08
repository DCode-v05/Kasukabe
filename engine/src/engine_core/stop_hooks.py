"""stop_hooks — Turn-boundary hook system for the agent loop.

Sibling of ``post_tool_hooks.py``. Where post-tool hooks run after every
tool call to mutate tool-result text, stop hooks run after every agent
turn to decide whether the loop should keep going.

A stop hook is an async callable that receives a snapshot of what just
happened (``StopHookInput``) and returns a verdict (``StopHookResult``)
with three possible effects:

1. ``prevent_continuation=True`` → the loop exits with the new
   ``LoopExitReason.HOOK_PREVENTED`` exit reason.
2. ``blocking_error="..."`` → the loop injects the error as a
   user-role ``<system-reminder>`` and runs ONE more turn. A local
   ``stop_hook_active`` flag on the loop side prevents infinite
   retries: when it's set, blocking errors are ignored.
3. Neither of the above → the loop continues normally.

The ``StopHookRegistry`` runs all registered hooks concurrently via
``asyncio.gather``; each hook has its own timeout. A slow, crashing,
or hung hook is **fail-open** — the loop continues. Only explicit
``prevent_continuation`` or ``blocking_error`` values influence
control flow. ``CancelledError`` is re-raised so cooperative
cancellation (SIGINT, Thread.cancel) still works.

Aggregation rules (``AggregatedStopResult``):

- ``prevent_continuation`` = ``True`` if ANY hook returned ``True``
  (first-prevent-wins — semantically, one blocker is enough).
- ``blocking_errors`` are collected in **registration order** (not
  completion order) so the injected reminder text is deterministic
  across runs.
- ``hook_outcomes`` carries one dict per hook for observability
  (``StopHookExecuted`` event payload).

Three hook implementations ship in this module:

``CallableStopHook``
    Wraps an async Python callable. The simplest path — test
    frameworks and SDK-registered hooks use this.

``CommandStopHook``
    Spawns a subprocess via ``asyncio.create_subprocess_exec``
    (never ``shell=True`` — caller passes explicit
    ``["sh", "-c", "..."]`` if shell evaluation is wanted). Writes
    ``StopHookInput`` as JSON to stdin, reads stdout as JSON
    ``StopHookResult``. Exit code semantics: ``0`` = success,
    ``2`` = blocking, anything else = non-blocking error (logged,
    fail-open).

``PromptStopHook``
    Renders a prompt template with the ``StopHookInput`` fields and
    calls a pre-constructed LLM adapter's ``generate``. The caller
    owns adapter construction — this module never calls
    ``create_llm`` — so tests can inject any adapter and provider
    coupling stays out of the hook system.

Loop-side recursion-guard semantics and MAX_AGENT_TURNS interaction
are documented in ``thread.py::agent_loop``. The retry turn counts
toward the existing 200-turn cap; stop hooks do not bypass it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from src.herald.base import LLM
from src.herald.types import Message

logger = logging.getLogger(__name__)


# --- Data types ---


@dataclass(frozen=True)
class StopHookInput:
    """Snapshot of turn state passed to every stop hook.

    Frozen so hooks cannot mutate shared state. ``stop_hook_active=True``
    means the loop is inside a retry triggered by a prior blocking hook;
    hooks that want to avoid feedback loops should early-return on this
    flag (the registry does not enforce it — it's a hook-side hint).

    Field parity notes vs. the TypeScript reference (src/utils/hooks.ts
    ``StopHookInputSchema``): ``thread_id`` is our session identifier (we
    don't ship a separate ``session_id``). ``transcript_path`` is
    intentionally absent — our conversation history lives in SQLite via
    ``conversation_store`` and working memory, not a filesystem
    transcript, so there is no path to hand out. ``cwd`` and
    ``hook_event_name`` are present to keep command-hook behavior and
    telemetry interoperable with the reference.
    """

    stop_hook_active: bool
    last_assistant_message: str
    turn_number: int
    last_stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | "refusal" | ...
    thread_id: str
    cwd: str = ""
    hook_event_name: str = "Stop"


@dataclass
class StopHookResult:
    """One hook's verdict.

    ``prevent_continuation=True`` → loop exits with ``HOOK_PREVENTED``.
    ``blocking_error`` non-empty → loop injects the text as a
    ``<system-reminder>`` and retries the turn (exactly once, guarded
    by ``stop_hook_active``). ``message``/``stop_reason`` are optional
    observability payloads — they appear in ``hook_outcomes`` but do
    not alone influence control flow.
    """

    prevent_continuation: bool = False
    blocking_error: str | None = None
    message: str = ""
    stop_reason: str = ""


@dataclass
class AggregatedStopResult:
    """Merge of all hook verdicts for one turn boundary."""

    prevent_continuation: bool = False
    blocking_errors: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    stop_reasons: list[str] = field(default_factory=list)
    hook_outcomes: list[dict] = field(default_factory=list)


# --- Protocol ---


class StopHook(Protocol):
    """A stop-hook callable.

    Receives a ``StopHookInput`` and returns a ``StopHookResult``.
    Implementations should catch their own exceptions and return a
    non-blocking result if they want the loop to see the failure;
    uncaught exceptions are logged by the registry and treated as
    fail-open non-blocking outcomes.
    """

    async def execute(self, input: StopHookInput) -> StopHookResult: ...


# --- Registry ---


@dataclass
class RegisteredHook:
    name: str
    hook: StopHook
    timeout_s: float


class StopHookRegistry:
    """Holds ``StopHook`` instances; runs them concurrently per turn.

    ``execute_all`` wraps every hook in ``asyncio.wait_for`` so one slow
    hook cannot stall the loop. Exceptions and timeouts become fail-open
    non-blocking outcomes — the loop continues. ``CancelledError`` is
    re-raised so cooperative cancellation still works.

    Aggregation:
    - ``prevent_continuation`` = ``any(hook.prevent_continuation)``
    - ``blocking_errors`` preserved in **registration order** for
      deterministic reminder text across runs (not completion order)
    - ``hook_outcomes`` carries per-hook telemetry for the
      ``StopHookExecuted`` event
    """

    def __init__(self) -> None:
        self.hooks: list[RegisteredHook] = []

    def register(self, name: str, hook: StopHook, timeout_s: float = 600.0) -> None:
        """Register a stop hook under a human-readable name.

        Re-registering an existing name replaces the prior hook.
        """
        self.hooks = [h for h in self.hooks if h.name != name]
        self.hooks.append(RegisteredHook(name=name, hook=hook, timeout_s=timeout_s))

    def unregister(self, name: str) -> None:
        """Remove a previously-registered hook by name (no-op if absent)."""
        self.hooks = [h for h in self.hooks if h.name != name]

    def has_hooks(self) -> bool:
        """Cheap zero-overhead check used by the agent loop before
        allocating a ``StopHookInput`` or awaiting anything.
        """
        return bool(self.hooks)

    async def execute_all(self, input: StopHookInput) -> AggregatedStopResult:
        """Run every registered hook in parallel and aggregate verdicts.

        Returns an ``AggregatedStopResult`` whose lists are ordered by
        registration order (not completion order) for determinism.
        """
        if not self.hooks:
            return AggregatedStopResult()

        # Snapshot registration order so concurrency doesn't reshuffle it.
        registered = list(self.hooks)

        async def run_one(
            reg: RegisteredHook,
        ) -> tuple[RegisteredHook, StopHookResult | BaseException, float]:
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
            # Cooperative cancellation must propagate.
            raise

        agg = AggregatedStopResult()
        for reg, outcome, duration_ms in raw:
            entry: dict[str, Any] = {
                "name": reg.name,
                "ok": False,
                "duration_ms": round(duration_ms, 2),
                "prevented": False,
                "blocked": False,
                "error": "",
            }
            if isinstance(outcome, BaseException):
                if isinstance(outcome, asyncio.TimeoutError):
                    entry["error"] = "timeout"
                else:
                    entry["error"] = f"{type(outcome).__name__}: {outcome}"
                logger.debug(
                    "stop hook %s failed: %s (fail-open)",
                    reg.name,
                    entry["error"],
                )
            else:
                entry["ok"] = True
                res: StopHookResult = outcome
                if res.prevent_continuation:
                    agg.prevent_continuation = True
                    entry["prevented"] = True
                if res.blocking_error:
                    agg.blocking_errors.append(res.blocking_error)
                    entry["blocked"] = True
                if res.message:
                    agg.messages.append(res.message)
                if res.stop_reason:
                    agg.stop_reasons.append(res.stop_reason)
            agg.hook_outcomes.append(entry)

        return agg


# --- Hook implementations ---


class CallableStopHook:
    """Wraps an async Python callable as a ``StopHook``.

    This is the simplest path and what test frameworks and SDK callers
    use. The wrapped callable receives a ``StopHookInput`` and must
    return a ``StopHookResult``. The ``timeout_s`` here is informational
    only — the registry enforces the actual timeout.
    """

    def __init__(
        self,
        fn: Callable[[StopHookInput], Awaitable[StopHookResult]],
        timeout_s: float = 600.0,
    ) -> None:
        self.fn = fn
        self.timeout_s = timeout_s

    async def execute(self, input: StopHookInput) -> StopHookResult:
        return await self.fn(input)


class CommandStopHook:
    """Runs a shell command as a stop hook.

    Spawned via ``asyncio.create_subprocess_exec`` — never ``shell=True``.
    If shell evaluation is needed, the caller passes
    ``["sh", "-c", "..."]`` explicitly.

    Protocol:
    - ``StopHookInput`` is written to the subprocess stdin as a single
      JSON object, then stdin is closed.
    - The subprocess writes its ``StopHookResult`` to stdout as JSON.
    - Exit codes:
        - ``0`` — success; stdout parsed as JSON result (or empty result)
        - ``2`` — blocking error; stdout is the blocking_error text
          (or JSON with a ``blocking_error`` field)
        - anything else — non-blocking error (logged, fail-open)
    - On timeout the process is hard-killed (``proc.kill``) and the
      call returns an empty non-blocking result.
    """

    def __init__(
        self,
        command: list[str],
        timeout_s: float = 600.0,
        env_overrides: dict[str, str] | None = None,
    ) -> None:
        if not command:
            raise ValueError("CommandStopHook requires a non-empty command")
        self.command = list(command)
        self.timeout_s = timeout_s
        self.env_overrides: dict[str, str] = dict(env_overrides or {})

    async def execute(self, input: StopHookInput) -> StopHookResult:
        from src.engine_core.hook_executors import (
            build_hook_env,
            run_command_hook,
        )

        payload = json.dumps(asdict(input)).encode("utf-8")
        env = build_hook_env(
            "Stop",
            session_id=input.thread_id,
            cwd=input.cwd,
            overrides=self.env_overrides,
        )
        run = await run_command_hook(self.command, payload, self.timeout_s, env=env)
        if run is None or run.returncode < 0:
            return StopHookResult()

        if run.returncode == 0:
            if not run.stdout_text:
                return StopHookResult()
            try:
                data = json.loads(run.stdout_text)
            except json.JSONDecodeError:
                return StopHookResult(message=run.stdout_text)
            return result_from_dict(data)

        if run.returncode == 2:
            try:
                data = json.loads(run.stdout_text) if run.stdout_text else {}
                if isinstance(data, dict) and data.get("blocking_error"):
                    return result_from_dict(data)
            except json.JSONDecodeError:
                pass
            return StopHookResult(blocking_error=run.stdout_text or run.stderr_text or "blocked")

        logger.debug(
            "CommandStopHook exit=%s stderr=%s (fail-open)",
            run.returncode,
            run.stderr_text[:200],
        )
        return StopHookResult()


class PromptStopHook:
    """Runs a stop hook via an LLM prompt.

    The ``llm`` adapter is pre-constructed by the caller — this class
    never calls ``create_llm``. This keeps the module free of provider
    coupling and lets tests inject any adapter.

    The ``prompt_template`` is rendered with ``StopHookInput`` fields
    via ``str.format(**asdict(input))``. The LLM is asked to reply with
    a JSON object matching ``StopHookResult`` — keys
    ``prevent_continuation`` (bool), ``blocking_error`` (str|null),
    ``message`` (str), ``stop_reason`` (str). Unparseable output is
    treated as a non-blocking fail-open result.
    """

    def __init__(
        self,
        llm: LLM,
        prompt_template: str,
        timeout_s: float = 600.0,
    ) -> None:
        self.llm = llm
        self.template = prompt_template
        self.timeout_s = timeout_s

    async def execute(self, input: StopHookInput) -> StopHookResult:
        try:
            rendered = self.template.format(**asdict(input))
        except (KeyError, IndexError) as exc:
            logger.debug("PromptStopHook template render failed: %s", exc)
            return StopHookResult()

        messages = [Message(role="user", content=rendered, timestamp=time.time())]
        try:
            text = await self.llm.generate(messages)
        except Exception as exc:  # noqa: BLE001
            logger.debug("PromptStopHook llm.generate failed: %s", exc)
            return StopHookResult()

        data = extract_json_object(text)
        if data is None:
            return StopHookResult()
        return result_from_dict(data)


# --- Helpers ---


def result_from_dict(data: Any) -> StopHookResult:
    """Build a ``StopHookResult`` from a loose dict, defending against
    missing or mis-typed fields.
    """
    if not isinstance(data, dict):
        return StopHookResult()
    return StopHookResult(
        prevent_continuation=bool(data.get("prevent_continuation", False)),
        blocking_error=(str(data["blocking_error"]) if data.get("blocking_error") else None),
        message=str(data.get("message", "") or ""),
        stop_reason=str(data.get("stop_reason", "") or ""),
    )


def extract_json_object(text: str) -> Any:
    """Extract the first JSON object from a text blob. Returns ``None``
    on failure. Tolerant of code fences and surrounding prose.
    """
    if not text:
        return None
    # Strip common code-fence wrappers the model sometimes emits.
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        # Remove optional language tag on the first line.
        nl = stripped.find("\n")
        if nl != -1:
            stripped = stripped[nl + 1 :]
    # Find the first brace-balanced object.
    start = stripped.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = stripped[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
    return None
