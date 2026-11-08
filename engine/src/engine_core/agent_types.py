"""Agent types — per-type tool filtering, permission modes, and budget defaults.

Sub-agents of different types get different capabilities:
- EXPLORE: read-only — file reads, grep, glob, search. No writes, no execution.
- PLAN: no tools (pure reasoning) — or minimal report-only tools.
- VERIFY: read + execute — can run tests. No writes.
- GENERAL: all tools minus spawn_sub_agent/spawn_thread (prevents recursion).

Mirrors filterToolsForAgent() and ALL_AGENT_DISALLOWED_TOOLS in the reference
(src/tools/AgentTool/agentToolUtils.ts:70-225).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from src.sandbox.permissions import (
    PermissionMode,
    ToolAction,
    ToolSource,
    classify_action,
)

if TYPE_CHECKING:
    from src.utility_directory.tool_runtime import ToolDef, ToolRegistry


# ── AgentType enum ─────────────────────────────────────────────────────


class AgentType(str, Enum):
    """Sub-agent capability tier.

    Attributes:
        EXPLORE:  Read-only worker for research/discovery. No mutation.
        PLAN:     Reasoning-only worker; produces a plan, no side effects.
        VERIFY:   Read + execute for test runs. No writes.
        GENERAL:  Full tool access except recursive spawns.
    """

    EXPLORE = "explore"
    PLAN = "plan"
    VERIFY = "verify"
    GENERAL = "general"


# ── Tools disallowed for ALL sub-agents (prevents recursion + parent mutation) ──

# Mirrors ALL_AGENT_DISALLOWED_TOOLS in agentToolUtils.ts.
# - spawn_sub_agent / spawn_thread: 1-level depth rule
# - ask_user: closes over parent thread state (WAITING_INPUT on parent)
ALL_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "spawn_sub_agent",
        "spawn_thread",
        "ask_user",
    }
)


# ── Tool classification ───────────────────────────────────────────────

# Actions EXPLORE + VERIFY can reach. VERIFY also gets EXECUTE (below).
READ_ACTIONS: frozenset[ToolAction] = frozenset(
    {
        ToolAction.SEARCH,
        ToolAction.READ,
    }
)
MUTATING_ACTIONS: frozenset[ToolAction] = frozenset(
    {
        ToolAction.WRITE,
        ToolAction.DESTRUCTIVE,
    }
)


def tool_action(tool: "ToolDef") -> ToolAction:
    """Classify a tool using the shared permissions.classify_action taxonomy.

    Preference order: the tool's own declared `is_read_only({})` hook (when
    True, promotes any classification to READ) → `classify_action` by
    (name, source). Keeps agent_types.py in lockstep with permissions.py
    so they can't drift.
    """
    try:
        if tool.is_read_only({}):
            return ToolAction.READ
    except Exception:
        pass
    return classify_action(tool.name, {}, tool.source)


def filter_tools_for_agent(
    agent_type: AgentType,
    parent_registry: "ToolRegistry",
) -> "ToolRegistry":
    """Return a new ToolRegistry containing only tools allowed for agent_type.

    Rules:
    - ALL_AGENT_DISALLOWED_TOOLS always excluded (recursion + parent mutation).
    - MCP tools (`ad:*`) always included — external integrations carry their
      own scoping via the specialized-agent declared_deps path.
    - EXPLORE: read-only only.
    - PLAN: nothing except MCP.
    - VERIFY: read-only + execute.
    - GENERAL: everything except the always-disallowed set.
    """
    from src.utility_directory.tool_runtime import ToolRegistry

    filtered = ToolRegistry()
    for meta in parent_registry.list_tools_meta():
        name = meta["name"]
        if name in ALL_AGENT_DISALLOWED_TOOLS:
            continue
        tool = parent_registry.get_tool(name)
        if tool is None:
            continue

        # MCP tools always pass — specialized agent scoping happens elsewhere.
        if name.startswith("ad:") or tool.source == ToolSource.MCP:
            filtered.register(tool)
            continue

        if agent_type == AgentType.GENERAL:
            filtered.register(tool)
            continue

        action = tool_action(tool)

        if agent_type == AgentType.EXPLORE:
            if action in READ_ACTIONS:
                filtered.register(tool)
            continue

        if agent_type == AgentType.PLAN:
            # Pure reasoning — no platform tools.
            continue

        if agent_type == AgentType.VERIFY:
            if action in MUTATING_ACTIONS:
                continue
            # READ, SEARCH, EXECUTE all pass.
            filtered.register(tool)
            continue

    return filtered


# ── Per-type defaults ──────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentTypeDefaults:
    """Opinionated defaults per AgentType.

    Sub-agent spawners can override any field; these apply when the caller
    passes agent_type without explicit per-agent config.
    """

    permission_mode: PermissionMode
    max_turns: int
    max_budget_usd: float | None
    max_output_tokens: int | None


DEFAULTS: dict[AgentType, AgentTypeDefaults] = {
    AgentType.EXPLORE: AgentTypeDefaults(
        permission_mode=PermissionMode.READ_ONLY,
        max_turns=30,
        max_budget_usd=0.50,
        max_output_tokens=None,
    ),
    AgentType.PLAN: AgentTypeDefaults(
        permission_mode=PermissionMode.READ_ONLY,
        max_turns=15,
        max_budget_usd=0.25,
        max_output_tokens=None,
    ),
    AgentType.VERIFY: AgentTypeDefaults(
        permission_mode=PermissionMode.DEFAULT,
        max_turns=30,
        max_budget_usd=1.00,
        max_output_tokens=None,
    ),
    AgentType.GENERAL: AgentTypeDefaults(
        permission_mode=PermissionMode.DEFAULT,
        max_turns=50,
        max_budget_usd=None,
        max_output_tokens=None,
    ),
}


def defaults_for(agent_type: AgentType) -> AgentTypeDefaults:
    """Return the default config bundle for an agent type."""
    return DEFAULTS[agent_type]


__all__ = [
    "AgentType",
    "AgentTypeDefaults",
    "ALL_AGENT_DISALLOWED_TOOLS",
    "defaults_for",
    "filter_tools_for_agent",
]
