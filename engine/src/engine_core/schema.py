"""Charter schema — DAG of execution nodes.

A Charter is an optional directed acyclic graph. Each node represents an
agent or skill invocation — NOT a reasoning step. The thread's agent loop
runs identically with or without a charter; when present, charter goals
enrich the system prompt but do not change available tools or the
execution path.

Full schema per Engine Core Spec Section 4: per-node agent/skill/tool/asset
assignment, conditional edges, charter-level model selection, and context
tracking.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.engine_core.agent_summarizer import SummarizerConfig


class ThreadState(str, Enum):
    INITIALIZING = "initializing"
    IDLE = "idle"
    EXECUTING = "executing"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"


class EdgeType(str, Enum):
    DATA_DEPENDENCY = "data_dependency"
    ORDERING = "ordering"
    CONDITIONAL = "conditional"


class AgentRef(BaseModel):
    """Version-pinned reference to a specialized agent from the Utility Directory."""

    name: str
    version: str = "0.0.0"
    config_overrides: dict[str, Any] = Field(default_factory=dict)


class SkillRef(BaseModel):
    """Version-pinned reference to a skill."""

    name: str
    version: str = "0.0.0"


class ToolRef(BaseModel):
    """Version-pinned reference to a tool."""

    name: str
    version: str = "0.0.0"


class AssetRef(BaseModel):
    """Reference to an external asset/connection from the Asset Directory."""

    connection_id: str
    asset_type: str = ""
    name: str = ""


class ExpectedOutput(BaseModel):
    """What a node should produce — used by Coordinator for validation."""

    type: str = "text"
    description: str = ""


class ModelSpec(BaseModel):
    """Model assignment for a charter or per-node override."""

    model_id: str = ""
    provider: str = ""
    context_window: int = 200000
    capabilities: list[str] = Field(default_factory=list)
    fallback_model_id: str = ""
    reason: str = ""


class EdgeCondition(BaseModel):
    """Condition for conditional edges — evaluates against from-node output."""

    field: str
    operator: str
    value: Any = None


class Node(BaseModel):
    node_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    node_type: str = "task"
    label: str
    todo_label: str = ""

    agent: AgentRef | None = None
    skills: list[SkillRef] = Field(default_factory=list)
    tools: list[ToolRef] = Field(default_factory=list)
    assets: list[AssetRef] = Field(default_factory=list)
    actions: list[str] = Field(
        default_factory=list
    )

    expected_output: ExpectedOutput = Field(default_factory=ExpectedOutput)

    priority: str = "medium"
    timeout_ms: int = 120000
    max_retries: int = 1
    escalation_policy: str = "never"
    model_override: ModelSpec | None = None

    def model_post_init(self, context: object) -> None:
        if not self.todo_label:
            self.todo_label = self.label[:60]


class Edge(BaseModel):
    from_node: str
    to_node: str
    edge_type: EdgeType = EdgeType.ORDERING
    data_flow: str | None = None
    condition: EdgeCondition | None = None


class Charter(BaseModel):
    charter_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    thread_id: str = ""
    charter_version: int = 1
    model: ModelSpec | None = None
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    user_query: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def validate_dag(charter: Charter) -> list[str]:
    """Validate charter is a proper DAG. Returns list of errors (empty = valid)."""
    errors: list[str] = []

    if not charter.nodes:
        errors.append("Charter has no nodes")
        return errors

    node_ids = {n.node_id for n in charter.nodes}

    for edge in charter.edges:
        if edge.from_node not in node_ids:
            errors.append(f"Edge references unknown from_node: {edge.from_node}")
        if edge.to_node not in node_ids:
            errors.append(f"Edge references unknown to_node: {edge.to_node}")

    for edge in charter.edges:
        if edge.edge_type == EdgeType.CONDITIONAL and edge.condition is None:
            errors.append(f"Conditional edge {edge.from_node}->{edge.to_node} has no condition")

    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for edge in charter.edges:
        if edge.from_node in node_ids and edge.to_node in node_ids:
            adjacency[edge.from_node].append(edge.to_node)
            in_degree[edge.to_node] += 1

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    if not queue:
        errors.append("No root nodes found — graph has a cycle")
        return errors

    visited = 0
    while queue:
        nid = queue.popleft()
        visited += 1
        for neighbor in adjacency[nid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited != len(node_ids):
        errors.append(f"Cycle detected: visited {visited}/{len(node_ids)} nodes")

    return errors


def topological_sort(charter: Charter) -> list[Node]:
    """Return nodes in topological order (Kahn's BFS). Assumes DAG is valid."""
    node_map = {n.node_id: n for n in charter.nodes}
    in_degree: dict[str, int] = {nid: 0 for nid in node_map}
    adjacency: dict[str, list[str]] = {nid: [] for nid in node_map}

    for edge in charter.edges:
        adjacency[edge.from_node].append(edge.to_node)
        in_degree[edge.to_node] += 1

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    ordered: list[Node] = []

    while queue:
        nid = queue.popleft()
        ordered.append(node_map[nid])
        for neighbor in adjacency[nid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return ordered


@dataclass
class ThreadIndexEntry:
    """Structured summary of a persistent thread for routing decisions."""

    thread_id: str
    summary: str = ""
    current_state: str = ""
    charter_summary: str = ""
    tools_used: list[str] = field(default_factory=list)
    assets_connected: list[str] = field(default_factory=list)
    recent_turns: list[str] = field(default_factory=list)
    turn_count: int = 0
    last_activity: str = ""
    pinned: bool = False


VERDICTS = frozenset(
    {"continue", "open", "recall", "claimed", "contested", "status"}
)

EXECUTABLE_VERDICTS = frozenset({"continue", "open", "recall"})

TARGETED_VERDICTS = frozenset({"continue", "recall"})


@dataclass
class DispatchSegment:
    """One piece of work carved out of a single input."""

    extract: str
    verdict: str
    target: str | None = None
    title: str = ""
    related_ids: list[str] = field(default_factory=list)


@dataclass
class Dispatch:
    """The triage verdict for one input: its segments, and why."""

    segments: list[DispatchSegment] = field(default_factory=list)
    reasoning: str = ""

    @property
    def executable(self) -> list[DispatchSegment]:
        return [s for s in self.segments if s.verdict in EXECUTABLE_VERDICTS]

    @property
    def returned(self) -> list[DispatchSegment]:
        """Segments handed back to the caller instead of executed."""
        return [s for s in self.segments if s.verdict in ("claimed", "contested")]


class LoopExitReason(str, Enum):
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    CANCELLED = "cancelled"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    ERROR = "error"
    BUDGET_EXCEEDED = "budget_exceeded"
    MODEL_FALLBACK = "model_fallback"
    HOOK_PREVENTED = "hook_prevented"


VALID_PERMISSION_MODES = frozenset({"default", "read_only", "dont_ask", "bypass"})

FOREGROUND_QUERY_SOURCES = frozenset(
    {
        "foreground",
        "agent",
        "compact",
        "hook",
        "side_question",
    }
)


@dataclass
class RequestOptions:
    """Per-request control knobs — bundled to avoid 4-layer parameter sprawl."""

    max_turns: int | None = None
    max_budget_usd: float | None = None
    super_thinking: bool = False
    permission_mode: str = "default"
    query_source: str = "foreground"
    token_budget: int | None = None
    content_ingestion: bool | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if self.permission_mode not in VALID_PERMISSION_MODES:
            self.permission_mode = "default"


@dataclass
class QueryChainTracking:
    """Execution chain identity for correlating events across turns and sub-agents.

    Each root query gets a unique chain_id at depth 0. Sub-agents get a NEW
    chain_id (not inherited) with depth = parent + 1 — intentional isolation
    so per-chain analytics can attribute costs without blending parent/child.
    turnId resets on compaction; turnCounter increments per turn within a
    compact window.
    """

    chain_id: str
    depth: int
    turn_id: str
    turn_counter: int
    parent_chain_id: str
    query_source: str

    def spawn_child(self) -> QueryChainTracking:
        """Create a child chain for a sub-agent — new chain_id, depth + 1."""
        return QueryChainTracking(
            chain_id=str(uuid.uuid4()),
            depth=self.depth + 1,
            turn_id=str(uuid.uuid4()),
            turn_counter=0,
            parent_chain_id=self.chain_id,
            query_source=self.query_source,
        )

    def on_compaction(self) -> None:
        """Reset turn tracking after compaction — new window, same chain."""
        self.turn_id = str(uuid.uuid4())
        self.turn_counter = 0

    def tick_turn(self) -> None:
        """Increment the turn counter within the current compact window."""
        self.turn_counter += 1

    def event_fields(self) -> dict:
        """Fields for event emission — avoids repeated ternary patterns."""
        return {
            "chain_id": self.chain_id,
            "depth": self.depth,
            "parent_chain_id": self.parent_chain_id,
            "turn_id": self.turn_id,
            "turn_counter": self.turn_counter,
        }


def create_root_chain(query_source: str = "foreground") -> QueryChainTracking:
    """Factory for root query chain — depth 0, no parent."""
    return QueryChainTracking(
        chain_id=str(uuid.uuid4()),
        depth=0,
        turn_id=str(uuid.uuid4()),
        turn_counter=0,
        parent_chain_id="",
        query_source=query_source,
    )


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class NodeCheckpoint(BaseModel):
    node_id: str
    label: str
    status: str
    output: str
    timestamp: str = ""


class SubAgentResult(BaseModel):
    agent_id: str
    agent_name: str
    model_id: str
    task: str
    result: str
    tool_calls: list[Any]
    duration_ms: int
    token_usage: TokenUsage
    fork_label: str = ""
    cache_hit_rate: float = 0.0
    exit_reason: str = "completed"
    cost_usd: float = 0.0


@dataclass
class BudgetConfig:
    """Resource ceilings for a SubAgent run."""

    max_turns: int | None = None
    token_budget: int | None = None
    max_output_tokens: int | None = None
    max_budget_usd: float | None = None


@dataclass
class ObservabilityConfig:
    """Opt-in telemetry + debugging knobs for a SubAgent run.

    Bool fields are Optional[bool] so the coalesce logic can tell
    "caller said nothing" (None) from "caller explicitly said False".
    """

    idle_threshold_ms: int | None = None
    progress_every_turn: bool | None = None
    always_record_transcript: bool | None = None
    metadata_dir: str | None = None
    summarizer_config: "SummarizerConfig | None" = None


@dataclass
class ForkConfig:
    context_messages: "list[Any] | None" = None
    label: str | None = None
    skip_cache_write: bool | None = None
    timeout_s: float | None = None
    on_message: "Any | None" = None


class ExitReason(str, Enum):
    """Terminal state of a sub-agent run. str+Enum so JSON serialization is
    unchanged from the prior free-string form (SubAgentResult.exit_reason)."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TURNS_EXHAUSTED = "turns_exhausted"
    ERROR = "error"


class AgentDefinition(BaseModel):
    """Persisted config for a spawnable sub-agent.

    Loaded from the Utility Directory (agent_def.markdown + frontmatter) or
    supplied inline by spawn_sub_agent callers. Field semantics mirror
    loadAgentsDir.ts in the reference.
    """

    name: str
    agent_type: str = "general"
    system_prompt: str = ""
    model_id: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    max_turns: int | None = None
    max_budget_usd: float | None = None
    max_output_tokens: int | None = None
    permission_mode: str = ""
    run_async_default: bool = False

    @classmethod
    def from_ud_row(cls, row: dict) -> "AgentDefinition":
        """Hydrate from a Utility Directory agent row.

        UD stores `learned_state` and `custom_config` as overlay
        layers on top of a platform row — this collapses the overlay into
        a single AgentDefinition. Presence-checked so an explicit user
        override of ``""`` or ``0`` still beats the platform default (the
        prior ``or`` chain would silently drop falsy overrides).
        """
        cfg = row.get("custom_config") or {}
        declared_deps = row.get("declared_deps") or {}

        def overlay(key: str, default=None):
            """Return cfg[key] if the user explicitly set it, else row[key]."""
            if key in cfg:
                return cfg[key]
            return row.get(key, default)

        allowed_tools = (
            cfg["allowed_tools"] if "allowed_tools" in cfg else declared_deps.get("tools") or []
        )
        return cls(
            name=row.get("name", ""),
            agent_type=overlay("agent_type", "general") or "general",
            system_prompt=overlay("system_prompt", "") or "",
            model_id=overlay("model_id", "") or "",
            allowed_tools=list(allowed_tools),
            max_turns=overlay("max_turns"),
            max_budget_usd=overlay("max_budget_usd"),
            max_output_tokens=overlay("max_output_tokens"),
            permission_mode=overlay("permission_mode", "") or "",
            run_async_default=bool(overlay("run_async_default", False)),
        )


async def load_agent_definition(
    utility: Any,
    name: str,
    version: str = "0.0.0",
) -> "AgentDefinition | None":
    """Fetch an agent row from the Utility Directory and hydrate it.

    Returns None when no UD is wired, no row exists, or the DB lookup
    itself fails. Validation errors from a malformed row are logged but
    still return None — the alternative (raising) would break the caller's
    ``spawn_sub_agent`` path for a single bad row; we prefer graceful
    degradation plus a visible log entry.
    """
    import logging

    log = logging.getLogger(__name__)
    if utility is None or not name:
        return None
    try:
        row = await utility.get_agent(name, version)
    except Exception as exc:
        log.warning("UD fetch failed for agent=%r: %s", name, exc)
        return None
    if not row:
        return None
    try:
        return AgentDefinition.from_ud_row(row)
    except Exception as exc:
        log.error(
            "UD row for agent=%r is malformed, ignoring: %s",
            name,
            exc,
        )
        return None


async def load_agent_definition_with_row(
    utility: Any,
    name: str,
    version: str = "0.0.0",
) -> "tuple[AgentDefinition | None, dict | None]":
    """Variant that returns both the AgentDefinition and the raw UD row.

    Callers that need the raw row (e.g. to read ``declared_deps`` for tool
    scoping in ``spawn_sub_agent``) would otherwise have to hit the DB
    twice. Returns (None, None) on any failure.
    """
    import logging

    log = logging.getLogger(__name__)
    if utility is None or not name:
        return None, None
    try:
        row = await utility.get_agent(name, version)
    except Exception as exc:
        log.warning("UD fetch failed for agent=%r: %s", name, exc)
        return None, None
    if not row:
        return None, None
    try:
        return AgentDefinition.from_ud_row(row), row
    except Exception as exc:
        log.error(
            "UD row for agent=%r is malformed, ignoring: %s",
            name,
            exc,
        )
        return None, row


class ThreadResult(BaseModel):
    thread_id: str
    charter_id: str
    final_text: str
    node_checkpoints: dict[str, NodeCheckpoint] = Field(default_factory=dict)
    sub_agent_results: list[SubAgentResult] = Field(default_factory=list)
    status: str
    exit_reason: str = "completed"
    last_model_stop: str = ""
    num_turns: int = 0
    duration_ms: int = 0
    model_usage: dict[str, TokenUsage] = Field(default_factory=dict)
