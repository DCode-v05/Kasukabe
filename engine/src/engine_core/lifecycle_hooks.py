"""lifecycle_hooks — Typed dispatch structs for every non-core HookEvent.

The core tool-lifecycle hooks (``PreToolUse``, ``PostToolUse``, ``Stop``)
have bespoke registries because their result aggregation needs
event-specific rules (deny>allow, retry-on-blocking, etc.). Every other
``HookEvent`` reuses the generic registry in ``generic_hooks.py``; this
module just carves out the per-event input dataclasses and a single
registry bundle so dispatch sites can look up the right registry by
``HookEvent``.

Events covered here
-------------------
Compaction:
    * ``PreCompact`` — about to compact. Inputs: message count, token
      estimate, trigger reason. Hook can ``prevent_continuation=True``
      to skip the compaction (dispatch site returns the messages
      unchanged) or inject ``additional_context`` that the caller
      prepends to post-compact instructions.
    * ``PostCompact`` — compaction done. Inputs: before/after token
      counts, summary hash. Hook can inject ``additional_context`` into
      the next system prompt or set ``updated_payload`` with a
      replacement summary.

Subagent:
    * ``SubagentStart`` — a SubAgent is about to run. Inputs: agent
      name, task description, agent_id, parent thread_id. Hook can
      ``prevent_continuation=True`` to veto the spawn.
    * ``SubagentStop`` — a SubAgent finished. Inputs: agent name,
      duration, token usage, final text.

Session + user flow:
    * ``Setup`` — first-ever engine setup (one-shot across a machine).
    * ``SessionStart`` / ``SessionEnd`` — session-scoped (string-keyed
      on ``hook_event_name`` only; we don't carry extra payload fields
      beyond what ``thread.py::fire_session_hooks`` already provides
      via env vars and stdin JSON).
    * ``UserPromptSubmit`` — user turn starting. Hook can rewrite the
      text via ``updated_payload={"text": "..."}``.

Task tracking:
    * ``TaskCreated`` / ``TaskCompleted`` — for planner task list items.

Tool lifecycle extras:
    * ``PostToolUseFailure`` — fires when the PostToolUse path saw a
      failed tool result. Non-blocking by default; used for telemetry
      and auto-retry decisions.
    * ``PermissionDenied`` — fires after the permission pipeline denies
      a tool call. Hook can supply ``additional_context`` explaining the
      denial to the model.
    * ``StopFailure`` — a stop hook itself failed. Used for observability.

Config / env (infrastructure events):
    * ``ConfigChange`` — settings file mutated.
    * ``CwdChanged`` — working directory changed.
    * ``FileChanged`` — watched file mutated.
    * ``InstructionsLoaded`` — CLAUDE.md / AGENTS.md loaded.
    * ``WorktreeCreate`` / ``WorktreeRemove`` — git worktree events.

UX:
    * ``Notification`` — generic surface notification.
    * ``TeammateIdle`` — teammate agent went idle.

Every dataclass is frozen so hooks can't mutate the shared payload.
Dispatch sites instantiate the dataclass, call ``registry.execute_all``,
then react to the aggregated result. The ``LifecycleHookBundle`` wires
the full set of event → registry mappings into one object the engine
owns once and hands down to every subsystem that wants to fire hooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.engine_core.generic_hooks import (
    GenericHookRegistry,
    GenericHookResult,
    AggregatedGenericResult,
    CallableGenericHook,
    CommandGenericHook,
    HttpGenericHook,
)
from src.engine_core.hooks import HookEvent


# ---------------------------------------------------------------------------
# Input dataclasses — one per event (keep field set minimal + stable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreCompactInput:
    thread_id: str
    turn_number: int
    message_count: int
    token_estimate: int
    context_window: int
    trigger_reason: str  # "threshold" | "emergency" | "microcompact" | "manual"
    cwd: str = ""
    hook_event_name: str = "PreCompact"


@dataclass(frozen=True)
class PostCompactInput:
    thread_id: str
    turn_number: int
    before_message_count: int
    after_message_count: int
    before_token_estimate: int
    after_token_estimate: int
    strategy: str  # "summary" | "truncate" | "emergency" | "collapse" | ...
    summary_sha: str = ""
    cwd: str = ""
    hook_event_name: str = "PostCompact"


@dataclass(frozen=True)
class SubagentStartInput:
    thread_id: str
    agent_id: str
    agent_name: str
    task_description: str
    parent_chain_id: str = ""
    depth: int = 0
    cwd: str = ""
    hook_event_name: str = "SubagentStart"


@dataclass(frozen=True)
class SubagentStopInput:
    thread_id: str
    agent_id: str
    agent_name: str
    final_text: str
    duration_ms: int
    input_tokens: int
    output_tokens: int
    exit_reason: str  # "completed" | "cancelled" | "error" | "max_turns"
    cwd: str = ""
    hook_event_name: str = "SubagentStop"


@dataclass(frozen=True)
class UserPromptSubmitInput:
    thread_id: str
    text: str
    turn_number: int
    cwd: str = ""
    hook_event_name: str = "UserPromptSubmit"


@dataclass(frozen=True)
class TaskCreatedInput:
    thread_id: str
    planner_thread_id: str
    title: str
    description: str
    cwd: str = ""
    hook_event_name: str = "TaskCreated"


@dataclass(frozen=True)
class TaskCompletedInput:
    thread_id: str
    planner_thread_id: str
    title: str
    status: str  # "completed" | "failed" | "skipped"
    result_summary: str = ""
    cwd: str = ""
    hook_event_name: str = "TaskCompleted"


@dataclass(frozen=True)
class PostToolUseFailureInput:
    thread_id: str
    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str
    error_text: str
    cwd: str = ""
    hook_event_name: str = "PostToolUseFailure"


@dataclass(frozen=True)
class PermissionDeniedInput:
    thread_id: str
    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str
    reason: str
    cwd: str = ""
    hook_event_name: str = "PermissionDenied"


@dataclass(frozen=True)
class StopFailureInput:
    thread_id: str
    failing_hook_name: str
    error_text: str
    cwd: str = ""
    hook_event_name: str = "StopFailure"


@dataclass(frozen=True)
class SetupInput:
    cwd: str = ""
    hook_event_name: str = "Setup"


@dataclass(frozen=True)
class NotificationInput:
    thread_id: str
    level: str  # "info" | "warning" | "error"
    message: str
    cwd: str = ""
    hook_event_name: str = "Notification"


@dataclass(frozen=True)
class TeammateIdleInput:
    thread_id: str
    teammate_id: str
    idle_seconds: float
    cwd: str = ""
    hook_event_name: str = "TeammateIdle"


@dataclass(frozen=True)
class ConfigChangeInput:
    source_path: str
    change_type: str  # "created" | "modified" | "deleted"
    cwd: str = ""
    hook_event_name: str = "ConfigChange"


@dataclass(frozen=True)
class CwdChangedInput:
    old_cwd: str
    new_cwd: str
    hook_event_name: str = "CwdChanged"


@dataclass(frozen=True)
class FileChangedInput:
    path: str
    change_type: str  # "created" | "modified" | "deleted"
    cwd: str = ""
    hook_event_name: str = "FileChanged"


@dataclass(frozen=True)
class InstructionsLoadedInput:
    path: str
    length: int
    cwd: str = ""
    hook_event_name: str = "InstructionsLoaded"


@dataclass(frozen=True)
class WorktreeCreateInput:
    worktree_path: str
    branch: str
    cwd: str = ""
    hook_event_name: str = "WorktreeCreate"


@dataclass(frozen=True)
class WorktreeRemoveInput:
    worktree_path: str
    cwd: str = ""
    hook_event_name: str = "WorktreeRemove"


# ---------------------------------------------------------------------------
# Event → input dataclass mapping (used by hook_settings for serialisation
# and by dispatch sites for type-safety documentation).
# ---------------------------------------------------------------------------

EVENT_INPUT_TYPES: dict[HookEvent, type] = {
    HookEvent.PRE_COMPACT: PreCompactInput,
    HookEvent.POST_COMPACT: PostCompactInput,
    HookEvent.SUBAGENT_START: SubagentStartInput,
    HookEvent.SUBAGENT_STOP: SubagentStopInput,
    HookEvent.USER_PROMPT_SUBMIT: UserPromptSubmitInput,
    HookEvent.TASK_CREATED: TaskCreatedInput,
    HookEvent.TASK_COMPLETED: TaskCompletedInput,
    HookEvent.POST_TOOL_USE_FAILURE: PostToolUseFailureInput,
    HookEvent.PERMISSION_DENIED: PermissionDeniedInput,
    HookEvent.STOP_FAILURE: StopFailureInput,
    HookEvent.SETUP: SetupInput,
    HookEvent.NOTIFICATION: NotificationInput,
    HookEvent.TEAMMATE_IDLE: TeammateIdleInput,
    HookEvent.CONFIG_CHANGE: ConfigChangeInput,
    HookEvent.CWD_CHANGED: CwdChangedInput,
    HookEvent.FILE_CHANGED: FileChangedInput,
    HookEvent.INSTRUCTIONS_LOADED: InstructionsLoadedInput,
    HookEvent.WORKTREE_CREATE: WorktreeCreateInput,
    HookEvent.WORKTREE_REMOVE: WorktreeRemoveInput,
}


# ---------------------------------------------------------------------------
# Bundle — central registry container
# ---------------------------------------------------------------------------


class LifecycleHookBundle:
    """Holds one ``GenericHookRegistry`` per generic lifecycle event.

    Dispatch sites call ``bundle.registry(event).execute_all(input)``.
    The engine owns one instance (on ``Thread``) and hands references
    to subsystems (compaction orchestrator, sub-agent spawner, etc.)
    so settings-loaded hooks surface everywhere.

    Registries are created lazily on first ``registry(event)`` call —
    that keeps construction cheap for events no site ever dispatches.
    """

    def __init__(self) -> None:
        self.registries: dict[HookEvent, GenericHookRegistry[Any]] = {}

    def registry(self, event: HookEvent) -> GenericHookRegistry[Any]:
        reg = self.registries.get(event)
        if reg is None:
            reg = GenericHookRegistry(event_name=event.value)
            self.registries[event] = reg
        return reg

    def has_hooks(self, event: HookEvent) -> bool:
        reg = self.registries.get(event)
        return bool(reg and reg.has_hooks())

    def registered_events(self) -> list[HookEvent]:
        return [e for e, r in self.registries.items() if r.has_hooks()]

    def register_callable(
        self,
        event: HookEvent,
        name: str,
        fn: Any,
        timeout_s: float = 60.0,
    ) -> None:
        """Convenience: register a Python async callable for an event."""
        hook = CallableGenericHook(fn, timeout_s=timeout_s)
        self.registry(event).register(name, hook, timeout_s=timeout_s)

    def register_command(
        self,
        event: HookEvent,
        name: str,
        command: list[str],
        timeout_s: float = 60.0,
        env_overrides: dict[str, str] | None = None,
    ) -> None:
        hook: CommandGenericHook[Any] = CommandGenericHook(
            command,
            timeout_s=timeout_s,
            event_name=event.value,
            env_overrides=env_overrides,
        )
        self.registry(event).register(name, hook, timeout_s=timeout_s)

    def register_http(
        self,
        event: HookEvent,
        name: str,
        url: str,
        timeout_s: float = 30.0,
    ) -> None:
        hook: HttpGenericHook[Any] = HttpGenericHook(
            url, timeout_s=timeout_s, event_name=event.value
        )
        self.registry(event).register(name, hook, timeout_s=timeout_s)

    def unregister(self, event: HookEvent, name: str) -> None:
        reg = self.registries.get(event)
        if reg is not None:
            reg.unregister(name)

    def clear(self) -> None:
        self.registries.clear()


# ---------------------------------------------------------------------------
# Helper re-exports for dispatch sites that only import this module.
# ---------------------------------------------------------------------------

__all__ = [
    "PreCompactInput",
    "PostCompactInput",
    "SubagentStartInput",
    "SubagentStopInput",
    "UserPromptSubmitInput",
    "TaskCreatedInput",
    "TaskCompletedInput",
    "PostToolUseFailureInput",
    "PermissionDeniedInput",
    "StopFailureInput",
    "SetupInput",
    "NotificationInput",
    "TeammateIdleInput",
    "ConfigChangeInput",
    "CwdChangedInput",
    "FileChangedInput",
    "InstructionsLoadedInput",
    "WorktreeCreateInput",
    "WorktreeRemoveInput",
    "EVENT_INPUT_TYPES",
    "LifecycleHookBundle",
    "GenericHookResult",
    "AggregatedGenericResult",
]
