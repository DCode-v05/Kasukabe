import os
import pytest
from pathlib import Path

from src.config import Settings
from src.bootstrap import bootstrap, shutdown
from src.engine_core.events import (
    ThreadLifecycle,
    ThreadCreated,
    ExecutionSummary,
    ToolExecution,
)

LIVE = bool(os.environ.get("CDT_URL")) and bool(os.environ.get("CDT_KEY"))
pytestmark = pytest.mark.skipif(
    not LIVE, reason="CDT_URL/CDT_KEY not set — live citadel tests skipped"
)


def live_settings() -> Settings:
    settings = Settings.load()
    settings.sqlite_db_path = ":memory:"
    settings.catalog_dir = str(Path(__file__).parent.parent / "catalog")
    return settings


class TestE2ESimpleQuery:
    @pytest.mark.asyncio
    async def test_handle_input_returns_text(self):
        settings = live_settings()
        state, report = await bootstrap(settings=settings)
        try:
            assert state.is_operational, f"Boot failed: {report.errors}"

            events = []

            async def capture(event):
                events.append(event)

            state.coordinator.on(capture)

            result = await state.coordinator.handle_input(
                message="What is 2+2? Answer with just the number.",
            )

            assert isinstance(result, str)
            assert len(result) > 0
            assert "4" in result

            lifecycle_events = [e for e in events if isinstance(e, ThreadLifecycle)]
            statuses = [e.status for e in lifecycle_events]
            assert "started" in statuses
            assert "completed" in statuses

            created_events = [e for e in events if isinstance(e, ThreadCreated)]
            assert len(created_events) >= 1

            summary_events = [e for e in events if isinstance(e, ExecutionSummary)]
            assert len(summary_events) >= 1
            assert summary_events[0].duration_ms > 0
        finally:
            await shutdown(state)


class TestE2EToolCall:
    @pytest.mark.asyncio
    async def test_tool_dispatch_and_result(self):
        settings = live_settings()
        state, report = await bootstrap(settings=settings)
        try:
            assert state.is_operational

            from src.utility_directory.tool_runtime import ToolDef

            tool_called = {"count": 0, "last_input": None}

            async def echo_handler(params: dict) -> str:
                tool_called["count"] += 1
                tool_called["last_input"] = params
                return f"Echo: {params.get('text', '')}"

            state.tools.register(
                ToolDef(
                    name="echo_test",
                    description="Echo back the provided text. Always use this tool when asked to echo something.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "Text to echo back"},
                        },
                        "required": ["text"],
                    },
                    handler=echo_handler,
                )
            )

            events = []

            async def capture(event):
                events.append(event)

            state.coordinator.on(capture)

            result = await state.coordinator.handle_input(
                message="Use the echo_test tool to echo 'hello world'.",
            )

            assert isinstance(result, str)
            assert len(result) > 0
            assert tool_called["count"] >= 1

            tool_events = [
                e for e in events if isinstance(e, ToolExecution) and e.tool_name == "echo_test"
            ]
            assert len(tool_events) >= 1
        finally:
            await shutdown(state)


class TestE2EMultiTurn:
    @pytest.mark.asyncio
    async def test_conversation_context_flows(self):
        settings = live_settings()
        state, report = await bootstrap(settings=settings)
        try:
            assert state.is_operational

            slot = state.coordinator.slots.allocate("live-test-user")
            tid = slot.thread_id

            result1 = await state.coordinator.handle_input(
                message="My name is TestUser42. Remember that.",
                thread_id=tid,
            )
            assert isinstance(result1, str)

            result2 = await state.coordinator.handle_input(
                message="What is my name?",
                thread_id=tid,
            )
            assert isinstance(result2, str)
            assert "TestUser42" in result2 or "testuser42" in result2.lower()
        finally:
            await shutdown(state)


class TestLCSignalPipeline:
    @pytest.mark.asyncio
    async def test_signal_capture_and_batch_encoding(self):
        settings = live_settings()
        state, report = await bootstrap(settings=settings)
        try:
            assert state.is_operational
            assert state.signal_store is not None
            assert state.lc_pipeline is not None

            from src.learning_centre.signals import LearningSignal

            await state.signal_store.write(
                LearningSignal(
                    type="fact",
                    source="user",
                    content="Our tech stack is Python 3.12 with FastAPI",
                    subject="tech stack",
                    thread_id="task-lc-test",
                )
            )
            await state.signal_store.write(
                LearningSignal(
                    type="person",
                    source="user",
                    content="Alice Chen is the DevOps lead",
                    subject="Alice Chen",
                    thread_id="task-lc-test",
                )
            )
            await state.signal_store.write(
                LearningSignal(
                    type="approach",
                    source="engine",
                    content="Recommended ElastiCache for caching based on AWS stack",
                    subject="caching recommendation",
                    thread_id="task-lc-test",
                )
            )
            await state.signal_store.write(
                LearningSignal(
                    type="correction",
                    source="user",
                    content="User corrected: they use self-hosted Redis, not ElastiCache",
                    subject="caching infrastructure",
                    thread_id="task-lc-test",
                )
            )
            await state.signal_store.write(
                LearningSignal(
                    type="outcome",
                    source="engine",
                    content="Helped user review caching options. Corrected initial recommendation.",
                    subject="caching review",
                    thread_id="task-lc-test",
                )
            )

            pending = await state.signal_store.pull_unprocessed()
            assert len(pending) == 5

            result = await state.lc_pipeline.run_batch()

            assert result.signals_processed == 5
            assert result.episodes_created >= 1
            assert result.knowledge_written >= 1
            assert len(result.errors) == 0

            remaining = await state.signal_store.pull_unprocessed()
            assert len(remaining) == 0
        finally:
            await shutdown(state)

    @pytest.mark.asyncio
    async def test_trivial_signals_skip_episode(self):
        settings = live_settings()
        state, report = await bootstrap(settings=settings)
        try:
            assert state.is_operational

            from src.learning_centre.signals import LearningSignal

            await state.signal_store.write(
                LearningSignal(
                    type="approach",
                    source="engine",
                    content="Answered simple math question",
                    subject="math",
                    thread_id="task-trivial",
                )
            )
            await state.signal_store.write(
                LearningSignal(
                    type="outcome",
                    source="engine",
                    content="Task completed successfully",
                    subject="completion",
                    thread_id="task-trivial",
                )
            )

            result = await state.lc_pipeline.run_batch()

            assert result.signals_processed == 2
            assert result.episodes_created == 0
        finally:
            await shutdown(state)

    @pytest.mark.asyncio
    async def test_empty_batch_runs_deterministic_steps(self):
        settings = live_settings()
        state, report = await bootstrap(settings=settings)
        try:
            assert state.is_operational

            result = await state.lc_pipeline.run_batch()
            assert result.signals_processed == 0
            assert result.episodes_created == 0
            assert result.knowledge_written == 0
        finally:
            await shutdown(state)
