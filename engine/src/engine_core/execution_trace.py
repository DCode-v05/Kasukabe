"""Execution trace data model — rich per-node telemetry for trajectories.

Pure data containers (dataclasses). No logic, no I/O. These capture the
LC spec Section 2 trajectory schema: per-node execution traces with token
counts, agent loop turns, tool/skill/asset calls, retries, ask-user interactions.

"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ToolCallTrace:
    """One tool invocation within a node's agent loop."""

    tool: str
    input: dict
    output: str
    duration_ms: int
    success: bool


@dataclass
class SkillCallTrace:
    """One skill invocation (skill:-prefixed tool) within a node."""

    skill: str
    input: dict
    output: str
    duration_ms: int
    success: bool


@dataclass
class AssetCallTrace:
    """One asset directory action (ad:-prefixed tool) within a node."""

    connection_id: str
    server_name: str
    action_slug: str
    asset_type: str = ""
    params: dict = field(default_factory=dict)
    duration_ms: int = 0
    success: bool = True


@dataclass
class RetryTrace:
    """One retry attempt for a node (validation failure + retry)."""

    attempt: int
    error: str
    resolution: str
    tier: int = 0


@dataclass
class AskUserTrace:
    """One human-in-the-loop interaction within a node."""

    request_id: str
    gap_type: str
    question: str
    response: str
    wait_duration_ms: int


@dataclass
class AgentLoopTrace:
    """Summary of the agent loop for a node."""

    turns: int
    total_input_tokens: int
    total_output_tokens: int
    goal: str
    model_id: str = ""
    cost_usd: float = 0.0


@dataclass
class NodeTrace:
    """Full execution trace for a single execution node (charter node or main run)."""

    node_id: str
    agent_name: str = ""
    agent_version: str = ""
    chain_id: str = ""
    depth: int = 0
    ce_input_tokens: int = 0
    ce_output_tokens: int = 0
    agent_loop: AgentLoopTrace | None = None
    agent_input: str = ""
    agent_output: str = ""
    errors: list[str] = field(default_factory=list)
    outcome: str = ""
    model_used: str = ""
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    skill_calls: list[SkillCallTrace] = field(default_factory=list)
    asset_calls: list[AssetCallTrace] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    duration_ms: int = 0
    retries: list[RetryTrace] = field(default_factory=list)
    ask_user_interactions: list[AskUserTrace] = field(default_factory=list)

    def record_tool_call(
        self,
        name: str,
        *,
        input: dict,
        output: str,
        duration_ms: int,
        success: bool,
        call_id: str = "",
        gap_type: str = "info_gap",
        tool_def: Any = None,
    ) -> None:
        """Classify a tool call by name prefix and append to the correct trace list.

        Prefix rules:
          ad:*      → AssetCallTrace
          skill:*   → SkillCallTrace
          ask_user  → AskUserTrace
          otherwise → ToolCallTrace

        ``tool_def`` is the resolved ToolDef when the caller has it. Asset calls
        used to record connection_id and server_name as empty strings with the
        whole prefixed wire name stuffed into action_slug, so trace data could
        never answer "which connection did this thread actually call".
        """
        if name.startswith("ad:"):
            server_name = getattr(tool_def, "server_name", "") or ""
            action_name = getattr(tool_def, "action_name", "") or ""
            connection_id = getattr(tool_def, "connection_id", "") or ""
            if not server_name:
                bare = name[3:]
                server_name, _, action_name = bare.partition(".")
            self.asset_calls.append(
                AssetCallTrace(
                    connection_id=connection_id,
                    server_name=server_name,
                    action_slug=action_name or name,
                    duration_ms=duration_ms,
                    success=success,
                )
            )
        elif name.startswith("skill:"):
            self.skill_calls.append(
                SkillCallTrace(
                    skill=name,
                    input=input,
                    output=output,
                    duration_ms=duration_ms,
                    success=success,
                )
            )
        elif name == "ask_user":
            self.ask_user_interactions.append(
                AskUserTrace(
                    request_id=call_id,
                    gap_type=gap_type,
                    question=input.get("question", ""),
                    response=output,
                    wait_duration_ms=duration_ms,
                )
            )
        else:
            self.tool_calls.append(
                ToolCallTrace(
                    tool=name,
                    input=input,
                    output=output,
                    duration_ms=duration_ms,
                    success=success,
                )
            )

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        d = asdict(self)
        return d


@dataclass
class ExecutionTrace:
    """Full execution trace for a thread run (all nodes)."""

    thread_id: str = ""
    charter_version: int = 0
    total_duration_ms: int = 0
    outcome: str = ""
    model_used: str = ""
    nodes: list[NodeTrace] = field(default_factory=list)
    scratchpad_snapshot: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "thread_id": self.thread_id,
            "charter_version": self.charter_version,
            "total_duration_ms": self.total_duration_ms,
            "outcome": self.outcome,
            "model_used": self.model_used,
            "nodes": [n.to_dict() for n in self.nodes],
            "scratchpad_snapshot": self.scratchpad_snapshot,
        }
