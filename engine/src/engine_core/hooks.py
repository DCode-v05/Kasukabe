"""hooks — Unified hook event system for the intelligence engine.

Defines the canonical ``HookEvent`` enumeration (25 lifecycle events),
the ``HookMatcher`` dataclass for targeting specific tools/events, and
helper functions for evaluating matcher patterns.

The specific hook registries (``PreToolUseHookRegistry``,
``PostToolUseHookRegistry``, ``StopHookRegistry``) live in their own
modules (``pre_tool_hooks.py``, ``post_tool_hooks.py``, ``stop_hooks.py``)
and are wired into the agent loop by ``thread.py``.  This module is the
shared vocabulary layer — it names the events and defines how hooks are
matched to tool calls.

Matcher pattern evaluation
---------------------------
``matches_tool(matcher, tool_name, tool_input)`` evaluates a hook's
``matcher`` string against an incoming tool call:

- ``""`` or ``"*"`` — wildcard: matches everything.
- ``"bash"`` — exact: matches only the tool named ``"bash"``.
- ``"bash|write|edit"`` — pipe-separated OR: matches any listed name.
- ``"^file_.*"`` — regex: compiled and tested against tool_name.
- ``"bash(npm publish)"`` — condition: matches ``bash`` tool whose
  ``command`` input contains ``"npm publish"`` (case-insensitive substring).

Priority in tool-event hooks (``hook_settings.py`` uses this):
    user > project > local > plugin > session > built-in
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HookEvent(str, Enum):
    """All lifecycle points where hooks may be registered.

    Events are grouped by subsystem.  The string value is the canonical
    ``hook_event_name`` written to hook stdin JSON and used in settings
    files — it must match the TypeScript reference ``HOOK_EVENTS`` constant
    in ``src/entrypoints/sdk/coreTypes.ts``.
    """

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    PERMISSION_REQUEST = "PermissionRequest"
    PERMISSION_DENIED = "PermissionDenied"

    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    SETUP = "Setup"
    STOP = "Stop"
    STOP_FAILURE = "StopFailure"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"

    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"

    NOTIFICATION = "Notification"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    TEAMMATE_IDLE = "TeammateIdle"

    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"

    CONFIG_CHANGE = "ConfigChange"
    CWD_CHANGED = "CwdChanged"
    FILE_CHANGED = "FileChanged"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"

    WORKTREE_CREATE = "WorktreeCreate"
    WORKTREE_REMOVE = "WorktreeRemove"


HOOK_EVENTS: frozenset[str] = frozenset(e.value for e in HookEvent)


@dataclass
class HookMatcher:
    """Associates a set of hook configs with an event and an optional
    tool-name pattern.

    ``event``:
        The lifecycle event this matcher applies to.

    ``matcher``:
        Tool-name filter string.  Evaluated by ``matches_tool()``.
        Empty string or ``"*"`` means "all tools."  Only meaningful for
        tool-lifecycle events (``PreToolUse``, ``PostToolUse``, etc.);
        non-tool events ignore the matcher.

    ``hooks``:
        List of hook configuration dicts (serialised HookConfig).
        Populated by ``hook_settings.py`` when loading from JSON files.
    """

    event: HookEvent
    matcher: str = "*"
    hooks: list[dict[str, Any]] = field(default_factory=list)


def matches_tool(
    matcher: str,
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
) -> bool:
    """Return True if the tool call should be processed by this matcher.

    Supports five pattern forms:

    1. Wildcard — ``""`` or ``"*"`` — always True.
    2. Exact — ``"bash"`` — case-sensitive equality.
    3. Pipe-OR — ``"bash|write|edit"`` — matches any listed name.
    4. Regex — starts with ``"^"`` or contains ``".*"`` or ``"["`` —
       compiled and matched against the full tool_name string.
    5. Condition — ``"bash(npm publish)"`` — matches ``bash`` when
       ``tool_input`` contains a string value that includes the substring
       inside the parentheses (case-insensitive).

    Returns True when no tool_name is provided (non-tool events).
    """
    if not tool_name:
        return True

    m = matcher.strip()
    if not m or m == "*":
        return True

    condition_match = re.fullmatch(r"([^(]+)\((.+)\)", m)
    if condition_match:
        name_pat, condition = condition_match.group(1).strip(), condition_match.group(2)
        if not name_matches(name_pat, tool_name):
            return False
        if tool_input is None:
            return False
        condition_lower = condition.lower()
        return any(
            condition_lower in str(v).lower() for v in tool_input.values() if isinstance(v, str)
        )

    return name_matches(m, tool_name)


def name_matches(pattern: str, tool_name: str) -> bool:
    """Evaluate a pure name pattern (no condition) against a tool name."""
    if looks_like_regex(pattern):
        try:
            return bool(re.fullmatch(pattern, tool_name))
        except re.error:
            return pattern == tool_name

    if "|" in pattern:
        return tool_name in {p.strip() for p in pattern.split("|")}

    return pattern == tool_name


def looks_like_regex(pattern: str) -> bool:
    """Heuristic: does the pattern look like a regex rather than a name?"""
    return bool(re.search(r"[.+*?\[\]()^$\\]", pattern))


TOOL_LIFECYCLE_EVENTS: frozenset[HookEvent] = frozenset(
    {
        HookEvent.PRE_TOOL_USE,
        HookEvent.POST_TOOL_USE,
        HookEvent.POST_TOOL_USE_FAILURE,
        HookEvent.PERMISSION_REQUEST,
        HookEvent.PERMISSION_DENIED,
    }
)
