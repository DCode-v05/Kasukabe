"""Tests for query chain tracking — 12-gap coverage."""

from __future__ import annotations

import pytest

from src.engine_core.schema import create_root_chain
from src.engine_core.loop_state import LoopState
from src.engine_core.events import (
    SubAgentStart,
    SubAgentDone,
    ExecutionSummary,
    CompactionEvent,
    ToolExecution,
)
from src.engine_core.execution_trace import NodeTrace
from src.engine_core.tool_result_builder import ToolResultBuilder


class TestCreateRootChain:
    def test_depth_zero(self):
        chain = create_root_chain("foreground")
        assert chain.depth == 0

    def test_chain_id_full_uuid(self):
        chain = create_root_chain()
        assert len(chain.chain_id) == 36
        assert chain.chain_id.count("-") == 4

    def test_turn_counter_zero(self):
        chain = create_root_chain()
        assert chain.turn_counter == 0

    def test_parent_chain_id_empty(self):
        chain = create_root_chain()
        assert chain.parent_chain_id == ""

    def test_query_source_preserved(self):
        chain = create_root_chain("agent")
        assert chain.query_source == "agent"

    def test_turn_id_full_uuid(self):
        chain = create_root_chain()
        assert len(chain.turn_id) == 36

    def test_unique_chain_ids(self):
        chains = [create_root_chain() for _ in range(10)]
        ids = {c.chain_id for c in chains}
        assert len(ids) == 10


class TestSpawnChild:
    def test_new_chain_id(self):
        parent = create_root_chain()
        child = parent.spawn_child()
        assert child.chain_id != parent.chain_id

    def test_depth_incremented(self):
        parent = create_root_chain()
        child = parent.spawn_child()
        assert child.depth == 1

    def test_grandchild_depth(self):
        root = create_root_chain()
        child = root.spawn_child()
        grandchild = child.spawn_child()
        assert grandchild.depth == 2

    def test_parent_chain_id_set(self):
        parent = create_root_chain()
        child = parent.spawn_child()
        assert child.parent_chain_id == parent.chain_id

    def test_grandchild_parent_chain_id(self):
        root = create_root_chain()
        child = root.spawn_child()
        grandchild = child.spawn_child()
        assert grandchild.parent_chain_id == child.chain_id

    def test_query_source_inherited(self):
        parent = create_root_chain("compact")
        child = parent.spawn_child()
        assert child.query_source == "compact"

    def test_turn_counter_reset(self):
        parent = create_root_chain()
        parent.tick_turn()
        parent.tick_turn()
        child = parent.spawn_child()
        assert child.turn_counter == 0

    def test_fresh_turn_id(self):
        parent = create_root_chain()
        child = parent.spawn_child()
        assert child.turn_id != parent.turn_id


class TestOnCompaction:
    def test_resets_turn_id(self):
        chain = create_root_chain()
        old_turn_id = chain.turn_id
        chain.on_compaction()
        assert chain.turn_id != old_turn_id

    def test_resets_turn_counter(self):
        chain = create_root_chain()
        chain.tick_turn()
        chain.tick_turn()
        assert chain.turn_counter == 2
        chain.on_compaction()
        assert chain.turn_counter == 0

    def test_preserves_chain_id(self):
        chain = create_root_chain()
        original_chain_id = chain.chain_id
        chain.on_compaction()
        assert chain.chain_id == original_chain_id

    def test_preserves_depth(self):
        root = create_root_chain()
        child = root.spawn_child()
        child.on_compaction()
        assert child.depth == 1

    def test_preserves_parent_chain_id(self):
        root = create_root_chain()
        child = root.spawn_child()
        child.on_compaction()
        assert child.parent_chain_id == root.chain_id


class TestTickTurn:
    def test_increments(self):
        chain = create_root_chain()
        assert chain.turn_counter == 0
        chain.tick_turn()
        assert chain.turn_counter == 1
        chain.tick_turn()
        assert chain.turn_counter == 2

    def test_multiple_ticks(self):
        chain = create_root_chain()
        for _ in range(10):
            chain.tick_turn()
        assert chain.turn_counter == 10


class TestLoopStateChain:
    def test_default_none(self):
        state = LoopState()
        assert state.chain is None

    def test_assigned(self):
        state = LoopState()
        chain = create_root_chain()
        state.chain = chain
        assert state.chain is chain
        assert state.chain.chain_id == chain.chain_id

    def test_serialize_without_chain(self):
        state = LoopState()
        d = state.serialize()
        assert d["chain"] is None

    def test_serialize_with_chain(self):
        state = LoopState()
        state.chain = create_root_chain("foreground")
        d = state.serialize()
        assert d["chain"]["chain_id"] == state.chain.chain_id
        assert d["chain"]["depth"] == 0
        assert d["chain"]["query_source"] == "foreground"

    def test_deserialize_without_chain(self):
        state = LoopState()
        d = state.serialize()
        restored = LoopState.deserialize(d)
        assert restored.chain is None

    def test_deserialize_with_chain(self):
        state = LoopState()
        state.chain = create_root_chain("agent")
        state.chain.tick_turn()
        d = state.serialize()
        restored = LoopState.deserialize(d)
        assert restored.chain is not None
        assert restored.chain.chain_id == state.chain.chain_id
        assert restored.chain.depth == 0
        assert restored.chain.turn_counter == 1
        assert restored.chain.query_source == "agent"


class TestSubAgentStartChainFields:
    def test_defaults_empty(self):
        event = SubAgentStart(
            thread_id="t1",
            agent_id="a1",
            agent_name="test",
            task_description="do stuff",
        )
        assert event.chain_id == ""
        assert event.depth == 0
        assert event.parent_chain_id == ""

    def test_populated(self):
        event = SubAgentStart(
            thread_id="t1",
            agent_id="a1",
            agent_name="test",
            task_description="do stuff",
            chain_id="abc12345",
            depth=1,
            parent_chain_id="parent01",
        )
        assert event.chain_id == "abc12345"
        assert event.depth == 1
        assert event.parent_chain_id == "parent01"


class TestSubAgentDoneChainFields:
    def test_defaults_empty(self):
        event = SubAgentDone(thread_id="t1", agent_id="a1", result_summary="done")
        assert event.chain_id == ""
        assert event.depth == 0

    def test_populated(self):
        event = SubAgentDone(
            thread_id="t1",
            agent_id="a1",
            result_summary="done",
            chain_id="abc12345",
            depth=2,
        )
        assert event.chain_id == "abc12345"
        assert event.depth == 2


class TestExecutionSummaryChainFields:
    def test_defaults_empty(self):
        event = ExecutionSummary(thread_id="t1", status="completed")
        assert event.chain_id == ""
        assert event.depth == 0
        assert event.turn_id == ""
        assert event.turn_counter == 0

    def test_populated(self):
        event = ExecutionSummary(
            thread_id="t1",
            status="completed",
            chain_id="abc",
            depth=1,
            turn_id="tid",
            turn_counter=5,
        )
        assert event.chain_id == "abc"
        assert event.turn_counter == 5


class TestCompactionEventChainFields:
    def test_defaults_empty(self):
        event = CompactionEvent(
            thread_id="t1",
            messages_before=100,
            messages_after=50,
            tokens_before=50000,
            tokens_after=25000,
            method="normal",
        )
        assert event.chain_id == ""
        assert event.turn_id_before == ""
        assert event.turn_id_after == ""

    def test_populated(self):
        event = CompactionEvent(
            thread_id="t1",
            messages_before=100,
            messages_after=50,
            tokens_before=50000,
            tokens_after=25000,
            method="normal",
            chain_id="abc",
            turn_id_before="old",
            turn_id_after="new",
        )
        assert event.chain_id == "abc"
        assert event.turn_id_before == "old"
        assert event.turn_id_after == "new"


class TestToolExecutionChainField:
    def test_default_empty(self):
        event = ToolExecution(
            thread_id="t1",
            tool_name="bash",
            source="platform",
            phase="call",
        )
        assert event.chain_id == ""

    def test_populated(self):
        event = ToolExecution(
            thread_id="t1",
            tool_name="bash",
            source="platform",
            phase="call",
            chain_id="abc12345",
        )
        assert event.chain_id == "abc12345"


class TestBatchChainLinkage:
    def test_batch_id_format_with_chain(self):
        chain = create_root_chain()
        batch_id = f"{chain.chain_id}-abcd1234"
        assert batch_id.startswith(chain.chain_id)
        assert "-" in batch_id


class TestNodeTraceChainFields:
    def test_defaults_empty(self):
        trace = NodeTrace(node_id="n1")
        assert trace.chain_id == ""
        assert trace.depth == 0

    def test_populated(self):
        trace = NodeTrace(node_id="n1", chain_id="abc", depth=2)
        assert trace.chain_id == "abc"
        assert trace.depth == 2

    def test_serializes(self):
        trace = NodeTrace(node_id="n1", chain_id="abc", depth=1)
        d = trace.to_dict()
        assert d["chain_id"] == "abc"
        assert d["depth"] == 1


class TestToolResultBuilderSourceAssistant:
    def test_default_empty(self):
        builder = ToolResultBuilder(
            tool_use_id="tu_1",
            base_result="ok",
        )
        result = builder.build()
        assert result.source_assistant_id == ""

    def test_populated(self):
        builder = ToolResultBuilder(
            tool_use_id="tu_1",
            base_result="ok",
            source_assistant_id="asst_abc",
        )
        result = builder.build()
        assert result.source_assistant_id == "asst_abc"

    def test_multi_block_preserves(self):
        builder = ToolResultBuilder(
            tool_use_id="tu_1",
            base_result="ok",
            source_assistant_id="asst_xyz",
        )
        builder.append_text("extra", label="hook")
        result = builder.build()
        assert result.source_assistant_id == "asst_xyz"

    def test_empty_content_preserves(self):
        builder = ToolResultBuilder(
            tool_use_id="tu_1",
            base_result="",
            tool_name="bash",
            source_assistant_id="asst_123",
        )
        result = builder.build()
        assert result.source_assistant_id == "asst_123"


class TestFeatureFlag:
    def test_default_false(self):
        from src.config import Settings

        s = Settings()
        assert s.query_chain_tracking_enabled is False

    def test_parse_true(self, monkeypatch):
        monkeypatch.setenv("QUERY_CHAIN_TRACKING_ENABLED", "true")
        from src.config import Settings

        s = Settings.load()
        assert s.query_chain_tracking_enabled is True

    def test_parse_false(self, monkeypatch):
        monkeypatch.setenv("QUERY_CHAIN_TRACKING_ENABLED", "false")
        from src.config import Settings

        s = Settings.load()
        assert s.query_chain_tracking_enabled is False

    def test_parse_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("QUERY_CHAIN_TRACKING_ENABLED", "1")
        from src.config import Settings

        with pytest.raises(ValueError, match="feature flags must be"):
            Settings.load()


