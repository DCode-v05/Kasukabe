"""Tests for the LC v2 signal-based pipeline.

Unit tests for signal store, WM flush, FTS5 search, signal clustering,
trivial task detection, and episode value building. No LLM calls needed.
"""

import json
import pytest
import pytest_asyncio

from src.infra.sqlite_pool import SqlitePool
from src.learning_centre.signals import (
    SignalStore,
    LearningSignal,
    SIGNAL_TYPES,
    SIGNAL_SOURCES,
)
from src.learning_centre.pipeline import LCPipeline
from src.memory.working_memory import EntryType
from src.memory.inmemory_working_memory import InMemoryWorkingMemory


@pytest_asyncio.fixture
async def db():
    pool = SqlitePool(":memory:")
    await pool.connect()
    from pathlib import Path

    migrations_dir = Path(__file__).parent.parent / "migrations"
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        await pool.executescript(sql_file.read_text(encoding="utf-8"))
    await pool.commit()
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def signal_store(db):
    return SignalStore(db)


class TestSignalTypes:
    def test_model_observed_types_present(self):
        for t in (
            "fact",
            "preference",
            "person",
            "correction",
            "event",
            "relationship",
            "approach",
            "outcome",
        ):
            assert t in SIGNAL_TYPES

    def test_wm_flush_types_present(self):
        for t in (
            "tool_call",
            "tool_result",
            "user_input_request",
            "user_input_response",
            "system_event",
            "checkpoint",
        ):
            assert t in SIGNAL_TYPES

    def test_sources(self):
        assert SIGNAL_SOURCES == {"user", "engine", "wm_flush"}


class TestSignalStore:
    @pytest.mark.asyncio
    async def test_write_and_read(self, signal_store):
        sig = LearningSignal(
            type="fact",
            source="user",
            content="Team uses Python 3.12",
            subject="tech stack",
            thread_id="task-1",
        )
        row_id = await signal_store.write(sig)
        assert row_id is not None

        pending = await signal_store.pull_unprocessed()
        assert len(pending) == 1
        assert pending[0]["content"] == "Team uses Python 3.12"
        assert pending[0]["type"] == "fact"
        assert pending[0]["source"] == "user"
        assert pending[0]["thread_id"] == "task-1"

    @pytest.mark.asyncio
    async def test_write_batch(self, signal_store):
        signals = [
            LearningSignal(type="fact", source="user", content="Uses AWS", thread_id="t1"),
            LearningSignal(type="person", source="user", content="Alice is CTO", thread_id="t1"),
            LearningSignal(
                type="approach", source="engine", content="Used web search", thread_id="t1"
            ),
        ]
        stored = await signal_store.write_batch(signals)
        assert stored == 3

        pending = await signal_store.pull_unprocessed()
        assert len(pending) == 3

    @pytest.mark.asyncio
    async def test_mark_processed(self, signal_store):
        await signal_store.write(
            LearningSignal(
                type="fact",
                source="user",
                content="test",
                thread_id="t1",
            )
        )
        pending = await signal_store.pull_unprocessed()
        assert len(pending) == 1

        await signal_store.mark_processed([pending[0]["id"]], "batch-001")
        remaining = await signal_store.pull_unprocessed()
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_delete_processed(self, signal_store):
        await signal_store.write(
            LearningSignal(
                type="fact",
                source="user",
                content="test",
                thread_id="t1",
            )
        )
        pending = await signal_store.pull_unprocessed()
        await signal_store.mark_processed([pending[0]["id"]], "batch-001")

        deleted = await signal_store.delete_processed()
        assert deleted >= 1

    @pytest.mark.asyncio
    async def test_get_by_task(self, signal_store):
        await signal_store.write_batch(
            [
                LearningSignal(type="fact", source="user", content="A", thread_id="task-a"),
                LearningSignal(type="fact", source="user", content="B", thread_id="task-b"),
                LearningSignal(type="person", source="user", content="C", thread_id="task-a"),
            ]
        )

        task_a = await signal_store.get_by_task("task-a")
        assert len(task_a) == 2
        task_b = await signal_store.get_by_task("task-b")
        assert len(task_b) == 1

    @pytest.mark.asyncio
    async def test_temporal_ref_stored(self, signal_store):
        await signal_store.write(
            LearningSignal(
                type="event",
                source="user",
                content="Board meeting happened",
                subject="board meeting",
                temporal_ref="2026-03-15",
                thread_id="t1",
            )
        )
        pending = await signal_store.pull_unprocessed()
        assert pending[0]["temporal_ref"] == "2026-03-15"

    @pytest.mark.asyncio
    async def test_temporal_ref_null_when_not_set(self, signal_store):
        await signal_store.write(
            LearningSignal(
                type="fact",
                source="user",
                content="Uses Python",
                thread_id="t1",
            )
        )
        pending = await signal_store.pull_unprocessed()
        assert pending[0]["temporal_ref"] is None


class TestFTS5Search:
    @pytest.mark.asyncio
    async def test_search_finds_matching_content(self, signal_store):
        await signal_store.write_batch(
            [
                LearningSignal(
                    type="fact",
                    source="user",
                    content="Team uses Python 3.12",
                    subject="tech",
                    thread_id="t1",
                ),
                LearningSignal(
                    type="fact",
                    source="user",
                    content="Deploy to AWS ECS",
                    subject="deploy",
                    thread_id="t1",
                ),
                LearningSignal(
                    type="person",
                    source="user",
                    content="Alice is the CTO",
                    subject="Alice",
                    thread_id="t1",
                ),
            ]
        )

        results = await signal_store.search_fts("Python", limit=10)
        assert len(results) >= 1
        assert any("Python" in r["content"] for r in results)

    @pytest.mark.asyncio
    async def test_search_no_results(self, signal_store):
        await signal_store.write(
            LearningSignal(
                type="fact",
                source="user",
                content="Uses Rust",
                thread_id="t1",
            )
        )
        results = await signal_store.search_fts("JavaScript", limit=10)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_subject_match(self, signal_store):
        await signal_store.write(
            LearningSignal(
                type="person",
                source="user",
                content="She leads the DevOps team",
                subject="Alice Chen",
                thread_id="t1",
            )
        )
        results = await signal_store.search_fts("Alice", limit=10)
        assert len(results) >= 1


class TestWMFlush:
    @pytest.mark.asyncio
    async def test_flushes_tool_calls(self, signal_store):
        wm = InMemoryWorkingMemory(session_id="test")
        await wm.write(
            "task-1", EntryType.TOOL_CALL, json.dumps({"name": "web_search", "args": {"q": "test"}})
        )
        await wm.write(
            "task-1", EntryType.TOOL_RESULT, json.dumps({"name": "web_search", "output": "results"})
        )

        items = await wm.read("task-1")
        stored = await signal_store.flush_wm("task-1", items, status="completed")
        assert stored >= 2

        pending = await signal_store.pull_unprocessed()
        types = [s["type"] for s in pending]
        assert "tool_call" in types
        assert "tool_result" in types
        assert "outcome" in types

    @pytest.mark.asyncio
    async def test_skips_user_and_assistant_messages(self, signal_store):
        wm = InMemoryWorkingMemory(session_id="test")
        await wm.write("task-1", EntryType.MESSAGE_USER, "Hello there")
        await wm.write("task-1", EntryType.MESSAGE_ASSISTANT, "Hi! How can I help?")
        await wm.write("task-1", EntryType.TOOL_CALL, json.dumps({"name": "search"}))

        items = await wm.read("task-1")
        await signal_store.flush_wm("task-1", items, status="completed")

        pending = await signal_store.pull_unprocessed()
        types = [s["type"] for s in pending]
        assert "user_message" not in types
        assert "assistant_message" not in types
        assert "tool_call" in types

    @pytest.mark.asyncio
    async def test_flushes_user_input(self, signal_store):
        wm = InMemoryWorkingMemory(session_id="test")
        await wm.write("task-1", EntryType.USER_INPUT_REQUEST, "What is your team size?")
        await wm.write("task-1", EntryType.USER_INPUT_RESPONSE, "We have 12 engineers")

        items = await wm.read("task-1")
        await signal_store.flush_wm("task-1", items, status="completed")

        pending = await signal_store.pull_unprocessed()
        types = [s["type"] for s in pending]
        assert "user_input_request" in types
        assert "user_input_response" in types

    @pytest.mark.asyncio
    async def test_failure_status_recorded(self, signal_store):
        wm = InMemoryWorkingMemory(session_id="test")
        await wm.write("task-1", EntryType.SYSTEM_EVENT, "timeout occurred")

        items = await wm.read("task-1")
        await signal_store.flush_wm("task-1", items, status="failed")

        pending = await signal_store.pull_unprocessed()
        outcomes = [s for s in pending if s["type"] == "outcome"]
        assert len(outcomes) == 1
        assert "failed" in outcomes[0]["content"]

    @pytest.mark.asyncio
    async def test_tool_name_extracted_as_subject(self, signal_store):
        wm = InMemoryWorkingMemory(session_id="test")
        await wm.write(
            "task-1", EntryType.TOOL_CALL, json.dumps({"name": "web_search", "args": {}})
        )

        items = await wm.read("task-1")
        await signal_store.flush_wm("task-1", items, status="completed")

        pending = await signal_store.pull_unprocessed()
        tool_signals = [s for s in pending if s["type"] == "tool_call"]
        assert len(tool_signals) == 1
        assert tool_signals[0]["subject"] == "web_search"


class TestPipelineHelpers:
    def test_group_by_task(self):
        pipeline = LCPipeline(adapter=None, db=None, memory_system=None)
        signals = [
            {"thread_id": "a", "type": "fact", "source": "user"},
            {"thread_id": "b", "type": "fact", "source": "user"},
            {"thread_id": "a", "type": "person", "source": "user"},
        ]
        groups = pipeline.group_by_task(signals)
        assert len(groups) == 2
        assert len(groups["a"]) == 2
        assert len(groups["b"]) == 1

    def test_is_trivial_task_no_user_facts(self):
        pipeline = LCPipeline(adapter=None, db=None, memory_system=None)
        signals = [
            {"type": "approach", "source": "engine"},
            {"type": "outcome", "source": "engine"},
        ]
        assert pipeline.is_trivial_task(signals) is True

    def test_is_trivial_task_has_user_fact(self):
        pipeline = LCPipeline(adapter=None, db=None, memory_system=None)
        signals = [
            {"type": "fact", "source": "user"},
            {"type": "outcome", "source": "engine"},
        ]
        assert pipeline.is_trivial_task(signals) is False

    def test_is_trivial_task_has_correction(self):
        pipeline = LCPipeline(adapter=None, db=None, memory_system=None)
        signals = [
            {"type": "correction", "source": "user"},
        ]
        assert pipeline.is_trivial_task(signals) is False

    def test_is_trivial_task_has_person(self):
        pipeline = LCPipeline(adapter=None, db=None, memory_system=None)
        signals = [
            {"type": "person", "source": "user"},
        ]
        assert pipeline.is_trivial_task(signals) is False

    def test_build_episode_value(self):
        pipeline = LCPipeline(adapter=None, db=None, memory_system=None)
        signals = [
            {
                "type": "fact",
                "source": "user",
                "content": "Uses Python",
                "temporal_ref": None,
                "thread_id": "t1",
            },
            {
                "type": "approach",
                "source": "engine",
                "content": "Recommended FastAPI",
                "temporal_ref": None,
                "thread_id": "t1",
            },
        ]
        value = pipeline.build_episode_value(signals)
        parsed = json.loads(value)
        assert parsed["thread_id"] == "t1"
        assert parsed["signal_count"] == 2
        assert len(parsed["signals"]) == 2

    def test_cosine_similarity(self):
        assert LCPipeline.cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
        assert LCPipeline.cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
        assert LCPipeline.cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)
        assert LCPipeline.cosine_similarity([0, 0], [1, 1]) == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_cluster_signals_small_batch_single_group(self):
        pipeline = LCPipeline(adapter=None, db=None, memory_system=None)
        signals = [
            {"type": "fact", "source": "user", "content": "A", "subject": "x"},
            {"type": "fact", "source": "user", "content": "B", "subject": "y"},
        ]
        groups = await pipeline.cluster_signals(signals)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    @pytest.mark.asyncio
    async def test_cluster_signals_empty(self):
        pipeline = LCPipeline(adapter=None, db=None, memory_system=None)
        groups = await pipeline.cluster_signals([])
        assert groups == []

    @pytest.mark.asyncio
    async def test_cluster_signals_no_embeddings_groups_by_subject(self):
        pipeline = LCPipeline(adapter=None, db=None, memory_system=None)
        signals = [
            {"type": "fact", "source": "user", "content": "A", "subject": "tech"},
            {"type": "fact", "source": "user", "content": "B", "subject": "tech"},
            {"type": "fact", "source": "user", "content": "C", "subject": "people"},
            {"type": "fact", "source": "user", "content": "D", "subject": "people"},
            {"type": "fact", "source": "user", "content": "E", "subject": "process"},
            {"type": "fact", "source": "user", "content": "F", "subject": "process"},
        ]
        groups = await pipeline.cluster_signals(signals)
        assert len(groups) == 3


class TestPipelineNoSignals:
    @pytest.mark.asyncio
    async def test_run_batch_empty(self, db, signal_store):
        pipeline = LCPipeline(
            adapter=None,
            db=db,
            memory_system=None,
            signal_store=signal_store,
        )
        result = await pipeline.run_batch()
        assert result.signals_processed == 0
        assert result.episodes_created == 0
