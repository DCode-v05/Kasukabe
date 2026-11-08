"""Integration tests for secret scanning at the SignalStore boundary.

Uses the shared `db` fixture from conftest.py (real in-memory SqlitePool +
migrations). No mocks — every assertion runs against a real SQLite backend
so row counts and commit semantics are observed end-to-end.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from src.infra.sqlite_pool import SqlitePool
from src.learning_centre.signals import LearningSignal, SignalStore
from src.security.secret_scanner import SecretDetectedError
from tests.test_secret_scanner import CANONICAL_POSITIVES


FAKE_AWS = CANONICAL_POSITIVES["aws-access-token"]
FAKE_GITHUB = CANONICAL_POSITIVES["github-pat"]


@pytest_asyncio.fixture
async def store(db):
    return SignalStore(db)


async def count_signals(db: SqlitePool) -> int:
    row = await db.fetchval("SELECT count(*) FROM learning_signals")
    return int(row or 0)


@pytest.mark.asyncio
async def test_clean_signal_is_written(store, db):
    sig = LearningSignal(
        type="fact",
        source="user",
        subject="Budget",
        content="Our Q4 budget is 50k",
        thread_id="t1",
    )
    row_id = await store.write(sig)
    assert row_id is not None
    assert await count_signals(db) == 1


@pytest.mark.asyncio
async def test_signal_with_secret_in_content_raises_and_does_not_insert(store, db):
    sig = LearningSignal(
        type="tool_result",
        source="engine",
        subject="Deployment output",
        content=f"Deployed to prod; aws_key={FAKE_AWS} rotate later",
        thread_id="t1",
    )
    with pytest.raises(SecretDetectedError) as exc_info:
        await store.write(sig)
    assert "AWS Access Token" in str(exc_info.value)
    assert FAKE_AWS not in str(exc_info.value)
    assert exc_info.value.where == "signal-ingest"
    assert await count_signals(db) == 0


@pytest.mark.asyncio
async def test_signal_with_secret_in_subject_raises(store, db):
    sig = LearningSignal(
        type="fact",
        source="user",
        subject=f"key leak {FAKE_GITHUB}",
        content="the content body is clean",
        thread_id="t1",
    )
    with pytest.raises(SecretDetectedError) as exc_info:
        await store.write(sig)
    assert "GitHub PAT" in str(exc_info.value)
    assert FAKE_GITHUB not in str(exc_info.value)
    assert await count_signals(db) == 0


@pytest.mark.asyncio
async def test_signal_with_both_subject_and_content_secrets_reports_both(store):
    sig = LearningSignal(
        type="fact",
        source="user",
        subject=f"aws leak {FAKE_AWS}",
        content=f"and github pat {FAKE_GITHUB}",
        thread_id="t1",
    )
    with pytest.raises(SecretDetectedError) as exc_info:
        await store.write(sig)
    assert set(exc_info.value.labels) == {"AWS Access Token", "GitHub PAT"}


@pytest.mark.asyncio
async def test_empty_subject_and_content_does_not_raise(store, db):
    sig = LearningSignal(
        type="system_event",
        source="engine",
        subject="",
        content="",
        thread_id="t1",
    )
    row_id = await store.write(sig)
    assert row_id is not None
    assert await count_signals(db) == 1


@pytest.mark.asyncio
async def test_batch_all_clean_writes_everything(store, db):
    batch = [
        LearningSignal(
            type="fact",
            source="user",
            subject=f"s{i}",
            content=f"clean content {i}",
            thread_id="t1",
        )
        for i in range(3)
    ]
    stored = await store.write_batch(batch)
    assert stored == 3
    assert await count_signals(db) == 3


@pytest.mark.asyncio
async def test_batch_with_dirty_item_aborts_without_partial_writes(store, db):
    batch = [
        LearningSignal(
            type="fact", source="user", subject="s0", content="clean first", thread_id="t1"
        ),
        LearningSignal(
            type="tool_result",
            source="engine",
            subject="s1",
            content=f"leaked {FAKE_AWS}",
            thread_id="t1",
        ),
        LearningSignal(
            type="fact", source="user", subject="s2", content="clean third", thread_id="t1"
        ),
    ]
    with pytest.raises(SecretDetectedError) as exc_info:
        await store.write_batch(batch)
    assert "AWS Access Token" in str(exc_info.value)
    assert exc_info.value.where == "signal-ingest-batch"
    assert await count_signals(db) == 0


@pytest.mark.asyncio
async def test_batch_empty_list_is_noop(store, db):
    stored = await store.write_batch([])
    assert stored == 0
    assert await count_signals(db) == 0


class FakeWMItem:
    """Minimal stand-in for a working-memory item as consumed by flush_wm."""

    def __init__(self, item_type, content: str) -> None:
        self.type = item_type
        self.content = content


@pytest.mark.asyncio
async def test_flush_wm_blocks_tool_result_containing_secret(store, db):
    """A tool result whose content carries an AWS key must not land in
    learning_signals — this is the dominant real-world leak path (env dumps,
    HTTP debug bodies, cat .env output)."""
    from src.memory.working_memory import EntryType

    items = [
        FakeWMItem(
            EntryType.TOOL_RESULT,
            f'{{"name": "bash", "output": "AWS_KEY={FAKE_AWS}"}}',
        ),
    ]
    with pytest.raises(SecretDetectedError) as exc_info:
        await store.flush_wm("task-999", items)
    assert "AWS Access Token" in str(exc_info.value)
    assert FAKE_AWS not in str(exc_info.value)
    assert exc_info.value.where == "signal-ingest-flush-wm"
    assert await count_signals(db) == 0


@pytest.mark.asyncio
async def test_flush_wm_clean_items_all_written(store, db):
    from src.memory.working_memory import EntryType

    items = [
        FakeWMItem(
            EntryType.TOOL_CALL,
            '{"name": "bash", "args": ["ls"]}',
        ),
        FakeWMItem(
            EntryType.TOOL_RESULT,
            '{"name": "bash", "output": "file1.txt\\nfile2.txt"}',
        ),
        FakeWMItem(
            EntryType.SYSTEM_EVENT,
            "task step complete",
        ),
    ]
    stored = await store.flush_wm("task-clean", items)
    assert stored == 4
    assert await count_signals(db) == 4
