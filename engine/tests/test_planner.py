import json
import pytest
from unittest.mock import AsyncMock

from src.engine_core.planner import (
    parse_charter_response,
    validate_charter_refs,
    parse_node,
    parse_edge,
)
from src.engine_core.schema import validate_dag, Charter, Node, Edge, EdgeType


VALID_CHARTER_JSON = json.dumps(
    {
        "needs_charter": True,
        "nodes": [
            {"node_id": "1", "label": "Research the topic", "todo_label": "Research"},
            {"node_id": "2", "label": "Write the summary", "todo_label": "Write"},
            {"node_id": "3", "label": "Review and polish", "todo_label": "Review"},
        ],
        "edges": [
            {
                "from_node": "1",
                "to_node": "2",
                "edge_type": "data_dependency",
                "data_flow": "research findings",
            },
            {"from_node": "2", "to_node": "3", "edge_type": "ordering"},
        ],
    }
)


class TestParseCharterResponse:
    def test_valid_charter(self):
        charter = parse_charter_response(VALID_CHARTER_JSON, "task-1", user_query="summarize")
        assert charter is not None
        assert len(charter.nodes) == 3
        assert len(charter.edges) == 2
        assert charter.thread_id == "task-1"
        assert charter.user_query == "summarize"

    def test_no_charter_needed(self):
        result = parse_charter_response('{"needs_charter": false}', "task-2")
        assert result is None

    def test_empty_nodes_returns_none(self):
        result = parse_charter_response(
            '{"needs_charter": true, "nodes": [], "edges": []}', "task-3"
        )
        assert result is None

    def test_markdown_fences_stripped(self):
        wrapped = "```json\n" + VALID_CHARTER_JSON + "\n```"
        charter = parse_charter_response(wrapped, "task-4")
        assert charter is not None
        assert len(charter.nodes) == 3

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="invalid JSON"):
            parse_charter_response("this is not json at all", "task-5")

    def test_cycle_detection(self):
        cyclic = json.dumps(
            {
                "needs_charter": True,
                "nodes": [
                    {"node_id": "a", "label": "Step A"},
                    {"node_id": "b", "label": "Step B"},
                ],
                "edges": [
                    {"from_node": "a", "to_node": "b", "edge_type": "ordering"},
                    {"from_node": "b", "to_node": "a", "edge_type": "ordering"},
                ],
            }
        )
        with pytest.raises(ValueError, match="validation failed"):
            parse_charter_response(cyclic, "task-6")

    def test_conditional_edge_without_condition(self):
        data = json.dumps(
            {
                "needs_charter": True,
                "nodes": [
                    {"node_id": "1", "label": "First"},
                    {"node_id": "2", "label": "Second"},
                ],
                "edges": [
                    {"from_node": "1", "to_node": "2", "edge_type": "conditional"},
                ],
            }
        )
        with pytest.raises(ValueError, match="validation failed"):
            parse_charter_response(data, "task-7")

    def test_unknown_node_in_edge(self):
        data = json.dumps(
            {
                "needs_charter": True,
                "nodes": [
                    {"node_id": "1", "label": "Only node"},
                ],
                "edges": [
                    {"from_node": "1", "to_node": "999", "edge_type": "ordering"},
                ],
            }
        )
        with pytest.raises(ValueError, match="validation failed"):
            parse_charter_response(data, "task-8")


class TestNodeParsing:
    def test_full_node_with_refs(self):
        raw = {
            "node_id": "n1",
            "label": "Do research",
            "agent": {"name": "researcher", "version": "1.0.0"},
            "skills": [{"name": "summarizer", "version": "1.0.0"}],
            "tools": [{"name": "web_search", "version": "1.0.0"}],
            "assets": [{"connection_id": "ref-1", "server_name": "gmail", "actions": ["send"]}],
            "expected_output": {"type": "text", "description": "research findings"},
            "priority": "high",
            "timeout_ms": 60000,
            "escalation_policy": "on_error",
        }
        node = parse_node(raw)
        assert node.node_id == "n1"
        assert node.agent is not None
        assert node.agent.name == "researcher"
        assert len(node.skills) == 1
        assert len(node.tools) == 1
        assert len(node.assets) == 1
        assert node.priority == "high"
        assert node.timeout_ms == 60000

    def test_minimal_node(self):
        raw = {"node_id": "n2", "label": "Simple step"}
        node = parse_node(raw)
        assert node.node_id == "n2"
        assert node.agent is None
        assert node.skills == []
        assert node.tools == []
        assert node.priority == "medium"


class TestEdgeParsing:
    def test_data_dependency(self):
        raw = {
            "from_node": "1",
            "to_node": "2",
            "edge_type": "data_dependency",
            "data_flow": "results",
        }
        edge = parse_edge(raw)
        assert edge.edge_type == EdgeType.DATA_DEPENDENCY
        assert edge.data_flow == "results"

    def test_conditional_with_condition(self):
        raw = {
            "from_node": "1",
            "to_node": "2",
            "edge_type": "conditional",
            "condition": {"field": "status", "operator": "equals", "value": "success"},
        }
        edge = parse_edge(raw)
        assert edge.edge_type == EdgeType.CONDITIONAL
        assert edge.condition is not None
        assert edge.condition.field == "status"


class TestValidateCharterRefs:
    @pytest.mark.asyncio
    async def test_strips_unknown_refs(self):
        charter = parse_charter_response(
            json.dumps(
                {
                    "needs_charter": True,
                    "nodes": [
                        {
                            "node_id": "1",
                            "label": "Use hallucinated agent",
                            "agent": {"name": "nonexistent-agent", "version": "1.0.0"},
                            "skills": [{"name": "fake-skill", "version": "1.0.0"}],
                            "tools": [{"name": "fake-tool", "version": "1.0.0"}],
                        },
                    ],
                    "edges": [],
                }
            ),
            "task-ref",
        )

        search = AsyncMock()
        search.agents = AsyncMock(return_value=[{"name": "researcher"}])
        search.skills = AsyncMock(return_value=[{"name": "summarizer"}])
        search.tools = AsyncMock(return_value=[{"name": "web_search"}])
        search.ad_connections = AsyncMock(return_value={"connections": []})

        warnings = await validate_charter_refs(charter, search)

        assert len(warnings) == 3
        assert charter.nodes[0].agent is None
        assert charter.nodes[0].skills == []
        assert charter.nodes[0].tools == []

    @pytest.mark.asyncio
    async def test_keeps_valid_refs(self):
        charter = parse_charter_response(
            json.dumps(
                {
                    "needs_charter": True,
                    "nodes": [
                        {
                            "node_id": "1",
                            "label": "Valid refs",
                            "agent": {"name": "researcher", "version": "1.0.0"},
                            "skills": [{"name": "summarizer", "version": "1.0.0"}],
                        },
                    ],
                    "edges": [],
                }
            ),
            "task-valid",
        )

        search = AsyncMock()
        search.agents = AsyncMock(return_value=[{"name": "researcher"}])
        search.skills = AsyncMock(return_value=[{"name": "summarizer"}])
        search.tools = AsyncMock(return_value=[])
        search.ad_connections = AsyncMock(return_value={"connections": []})

        warnings = await validate_charter_refs(charter, search)

        assert len(warnings) == 0
        assert charter.nodes[0].agent.name == "researcher"
        assert len(charter.nodes[0].skills) == 1


class TestValidateDag:
    def test_valid_linear_dag(self):
        charter = Charter(
            nodes=[
                Node(node_id="1", label="A"),
                Node(node_id="2", label="B"),
            ],
            edges=[Edge(from_node="1", to_node="2")],
        )
        assert validate_dag(charter) == []

    def test_no_nodes(self):
        charter = Charter(nodes=[], edges=[])
        errors = validate_dag(charter)
        assert any("no nodes" in e.lower() for e in errors)

    def test_self_loop(self):
        charter = Charter(
            nodes=[Node(node_id="1", label="A")],
            edges=[Edge(from_node="1", to_node="1")],
        )
        errors = validate_dag(charter)
        assert any("cycle" in e.lower() for e in errors)
