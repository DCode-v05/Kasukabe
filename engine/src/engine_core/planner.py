"""Planner — charter creation via sub-agent pattern.

A library, not a standalone service. The thread's request_plan tool handler
calls prepare_sub_agent_config() to get the sub-agent setup, then spawns
the sub-agent. The sub-agent makes 1 LLM call to produce the charter JSON.
planner.py parses and validates the result.

search.py provides all capability context (cached, 0 DB queries).
Episodic recall emerges naturally: search.py returns relevant episodes,
the sub-agent uses them (S1-like). No match → reasons from scratch (S2-like).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.engine_core.schema import (
    Charter,
    Node,
    Edge,
    EdgeType,
    validate_dag,
    AgentRef,
    SkillRef,
    ToolRef,
    AssetRef,
    ExpectedOutput,
    EdgeCondition,
)

logger = logging.getLogger(__name__)

PLANNER_SYSTEM = """You are a task planner for an agentic execution engine.

Given a user query and the available capabilities below, decide whether the task
needs specialized agents or skills. If it does, create a charter (DAG) with nodes
for each agent/skill invocation. If it does NOT, return an empty charter.

IMPORTANT: Nodes represent ONLY specialized agent or skill invocations.
- Do NOT create nodes for reasoning steps the model can handle in a single call.
- Nodes are needed when: different expertise is required (e.g., research agent +
  code-writing agent), or a named skill must be invoked, or an external service
  action must be performed.

{capability_context}

Return ONLY valid JSON matching this schema:
{{
  "needs_charter": false,
  "nodes": [],
  "edges": []
}}

Or, when specialized agents/skills ARE needed:
{{
  "needs_charter": true,
  "nodes": [
    {{
      "node_id": "1",
      "label": "what this agent/skill does",
      "todo_label": "short label",
      "agent": {{"name": "agent-name", "version": "1.0.0"}},
      "skills": [{{"name": "skill-name", "version": "1.0.0"}}],
      "tools": [{{"name": "tool-name", "version": "1.0.0"}}],
      "assets": [{{"connection_id": "ref123", "server_name": "gmail", "actions": ["send_email"]}}],
      "expected_output": {{"type": "text", "description": "what this node produces"}},
      "priority": "medium",
      "timeout_ms": 120000,
      "escalation_policy": "never",
      "model_override": {{"model_id": "light", "provider": "", "reason": "simple subtask"}}
    }}
  ],
  "edges": [
    {{
      "from_node": "1",
      "to_node": "2",
      "edge_type": "data_dependency",
      "data_flow": "description of data passed",
      "condition": null
    }}
  ]
}}

Rules:
- needs_charter is false for most queries
- needs_charter is true ONLY when specialized agents or skills are required
- edge_type: "data_dependency", "ordering", or "conditional"
- Conditional edges MUST have a condition object
- node_id should be simple strings like "1", "2", "3"
- Model override tiers: "light" (cheap/fast), "standard" (normal), "heavy" (complex reasoning)
- Return ONLY the JSON object, no markdown fences, no explanation"""


async def build_capability_context(search: Any, query: str) -> str:
    """Build the capability context string from search.py cached data.

    Queries search.py for agents, skills, tools, AD connections, and episodes.
    All from cache — 0 DB queries in the common case.
    """
    import asyncio

    agents, skills, tools, ad_meta, episodes = await asyncio.gather(
        search.agents(),
        search.skills(),
        search.tools(),
        search.ad_connections(),
        search.episodes(query, limit=5),
    )

    sections: list[str] = []

    if agents:
        lines = ["AVAILABLE AGENTS:"]
        for a in agents:
            caps = ", ".join(a.get("capabilities", [])) if a.get("capabilities") else ""
            lines.append(
                f"  - {a['name']} (v{a.get('version', '?')}): {a.get('description', '')} [{caps}]"
            )
        sections.append("\n".join(lines))

    if skills:
        lines = ["AVAILABLE SKILLS:"]
        for s in skills:
            lines.append(f"  - {s['name']} ({s.get('type', '?')}): {s.get('description', '')}")
        sections.append("\n".join(lines))

    if tools:
        lines = ["AVAILABLE TOOLS:"]
        for t in tools:
            lines.append(f"  - {t['name']}: {t.get('description', '')}")
        sections.append("\n".join(lines))

    connections = ad_meta.get("connections", [])
    servers = {s["server_name"]: s for s in ad_meta.get("servers", [])}
    actions_by_server = ad_meta.get("action_names_by_server", {})
    active = [c for c in connections if c.get("status") == "ACTIVE"]
    if active:
        lines = ["CONNECTED SERVICES:"]
        for c in active:
            sn = c.get("server_name", "")
            display = servers.get(sn, {}).get("display_name", sn)
            acts = actions_by_server.get(sn, [])
            lines.append(
                f"  - {display} (connection_id: {c.get('connection_id', '')}): actions=[{', '.join(acts)}]"
            )
        sections.append("\n".join(lines))

    if episodes:
        lines = ["RELEVANT PAST EPISODES:"]
        for ep in episodes[:3]:
            lines.append(f"  - {ep.get('key', '')}: {ep.get('description', '')}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections) if sections else "No capabilities available."


def prepare_sub_agent_config(
    capability_context: str,
    user_query: str,
    conversation_summary: str = "",
) -> dict[str, str]:
    """Prepare the system prompt and user message for the planning sub-agent.

    Returns a dict with 'system_prompt' and 'user_message' for the sub-agent.
    The sub-agent makes 1 LLM call with this config and returns charter JSON.
    """
    system = PLANNER_SYSTEM.replace("{capability_context}", capability_context)

    user_parts = [f"User query: {user_query}"]
    if conversation_summary:
        user_parts.insert(0, f"Conversation context: {conversation_summary}")

    return {
        "system_prompt": system,
        "user_message": "\n\n".join(user_parts),
    }


def parse_charter_response(
    raw_text: str,
    thread_id: str,
    user_query: str = "",
) -> Charter | None:
    """Parse the sub-agent's LLM response into a validated Charter.

    Returns None if the response indicates no charter is needed.
    Raises ValueError if the JSON is invalid or the DAG fails validation.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON: {e}") from e

    if not data.get("needs_charter", False):
        return None

    raw_nodes = data.get("nodes", [])
    raw_edges = data.get("edges", [])

    if not raw_nodes:
        return None

    nodes = [parse_node(n) for n in raw_nodes]
    edges = [parse_edge(e) for e in raw_edges]

    charter = Charter(
        charter_id=f"charter-{thread_id[:8]}",
        thread_id=thread_id,
        user_query=user_query,
        nodes=nodes,
        edges=edges,
    )

    errors = validate_dag(charter)
    if errors:
        raise ValueError(f"Charter DAG validation failed: {errors}")

    return charter


def parse_node(raw: dict) -> Node:
    agent = None
    if raw.get("agent"):
        a = raw["agent"]
        agent = AgentRef(name=a.get("name", ""), version=a.get("version", ""))

    skills = []
    for s in raw.get("skills", []):
        skills.append(SkillRef(name=s.get("name", ""), version=s.get("version", "")))

    tools = []
    for t in raw.get("tools", []):
        tools.append(ToolRef(name=t.get("name", ""), version=t.get("version", "")))

    assets = []
    for a in raw.get("assets", []):
        assets.append(
            AssetRef(
                connection_id=a.get("connection_id", ""),
                asset_type=a.get("asset_type", a.get("server_name", "")),
                name=a.get("name", a.get("server_name", "")),
            )
        )

    if raw.get("expected_output"):
        eo = raw["expected_output"]
        expected_output = ExpectedOutput(
            type=eo.get("type", "text"),
            description=eo.get("description", ""),
        )
    else:
        expected_output = ExpectedOutput(type="text", description="")

    return Node(
        node_id=raw.get("node_id", ""),
        label=raw.get("label", ""),
        todo_label=raw.get("todo_label", raw.get("label", "")[:60]),
        agent=agent,
        skills=skills,
        tools=tools,
        assets=assets,
        expected_output=expected_output,
        priority=raw.get("priority", "medium"),
        timeout_ms=raw.get("timeout_ms", 120000),
        escalation_policy=raw.get("escalation_policy", "never"),
    )


def parse_edge(raw: dict) -> Edge:
    edge_type = EdgeType.DATA_DEPENDENCY
    raw_type = raw.get("edge_type", "data_dependency")
    if raw_type == "ordering":
        edge_type = EdgeType.ORDERING
    elif raw_type == "conditional":
        edge_type = EdgeType.CONDITIONAL

    condition = None
    if raw.get("condition"):
        c = raw["condition"]
        condition = EdgeCondition(
            field=c.get("field", ""),
            operator=c.get("operator", "equals"),
            value=c.get("value", ""),
        )

    return Edge(
        from_node=raw.get("from_node", ""),
        to_node=raw.get("to_node", ""),
        edge_type=edge_type,
        data_flow=raw.get("data_flow", ""),
        condition=condition,
    )


async def validate_charter_refs(
    charter: Charter,
    search: Any,
) -> list[str]:
    """Cross-check charter node refs against the search cache. Strip invalid refs.

    Returns list of warning strings for stripped references.
    Modifies charter nodes in-place — removes refs that don't exist.
    Name-only matching (version is resolved at hydration time).
    """
    import asyncio as aio

    agents_list, skills_list, tools_list, ad_meta = await aio.gather(
        search.agents(),
        search.skills(),
        search.tools(),
        search.ad_connections(),
    )

    agent_names = {a["name"] for a in agents_list}
    skill_names = {s["name"] for s in skills_list}
    tool_names = {t["name"] for t in tools_list}
    connection_ids = {c["connection_id"] for c in ad_meta.get("connections", [])}

    warnings: list[str] = []

    for node in charter.nodes:
        if node.agent and node.agent.name not in agent_names:
            warnings.append(f"node {node.node_id}: unknown agent '{node.agent.name}' — stripped")
            node.agent = None

        valid_skills = []
        for s in node.skills:
            if s.name in skill_names:
                valid_skills.append(s)
            else:
                warnings.append(f"node {node.node_id}: unknown skill '{s.name}' — stripped")
        node.skills = valid_skills

        valid_tools = []
        for t in node.tools:
            if t.name in tool_names:
                valid_tools.append(t)
            else:
                warnings.append(f"node {node.node_id}: unknown tool '{t.name}' — stripped")
        node.tools = valid_tools

        valid_assets = []
        for a in node.assets:
            if a.connection_id in connection_ids:
                valid_assets.append(a)
            else:
                warnings.append(
                    f"node {node.node_id}: unknown asset ref '{a.connection_id}' — stripped"
                )
        node.assets = valid_assets

    return warnings
