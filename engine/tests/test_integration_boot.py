"""Integration test — full engine boot + memory flow.

Boots the engine via bootstrap() with SQLite in-memory, verifies:
- All subsystems wire correctly
- SQLite migrations run
- Catalog loads from disk
- Memory writes persist and search works
- Channel state survives write/read cycle
- Trajectory flushes to LC queue
- Orphan cleanup runs without error
- Working memory is ephemeral (in-memory)
"""

import json
import shutil
import pytest
from pathlib import Path

from src.config import Settings
from src.bootstrap import bootstrap, shutdown, EngineState, BootReport

UNREACHABLE_CITADEL = "http://127.0.0.1:1"


@pytest.fixture(autouse=True)
def there_is_no_citadel(monkeypatch):
    """Tell the two citadel consumers so, instead of letting them find out.

    `catalog_sync.initialize` already has a "no citadel" path and documents
    it - "engine still boots with an EMPTY catalog and self-heals via the
    accelerated first-sync retry; there is no static seed". `EmbeddingService`
    has one too: `is_real` False means `embed()` returns `zero_vector()`.

    Both of those are exactly what these runs were already getting. The log
    said "Embedding API call failed - zero vector" on every boot. The only
    difference is that they used to pay for it first: `timeout=30.0` on the
    embedding client, septemberai's default `max_retries=2`, three attempts
    with backoff, per boot, fifteen boots. That is the ninety-seconds-a-test
    this file used to cost, and why it never finished inside any sane limit.

    So nothing observable changes and nothing is being papered over - the
    degraded paths are the ones under test either way. monkeypatch reverts
    both at teardown.
    """
    from src.herald import catalog_sync
    from src.memory import embeddings

    async def degraded(_settings) -> bool:
        return False

    monkeypatch.setattr(catalog_sync, "initialize", degraded)
    monkeypatch.setattr(
        embeddings.EmbeddingService, "is_real", property(lambda self: False)
    )

needs_bwrap = pytest.mark.skipif(
    shutil.which("bwrap") is None,
    reason="bwrap is not installed; the local-bwrap sandbox cannot be provisioned",
)


def make_test_settings() -> Settings:
    """Settings for integration test — SQLite in-memory, local catalog."""
    return Settings(
        cdt_url=UNREACHABLE_CITADEL,
        cdt_key="sk-sept-cit-test-not-real",
        llm_model="claude-haiku-4-5",
        sqlite_db_path=":memory:",
        catalog_dir=str(Path(__file__).parent.parent / "catalog"),
        ad_encryption_key="",
    )


class TestBootstrap:
    @pytest.mark.asyncio
    async def test_boot_produces_state_and_report(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            assert isinstance(state, EngineState)
            assert isinstance(report, BootReport)
            assert report.boot_duration_ms > 0
        finally:
            await shutdown(state)

    @needs_bwrap
    @pytest.mark.asyncio
    async def test_sandbox_provisioned_and_tools_registered(self):
        """Step-1 wire-in: one bwrap sandbox per engine, tools in the shared registry."""
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            assert report.subsystems.get("sandbox") is True
            assert state.sandbox_provider is not None
            assert state.sandbox_provider.name == "local-bwrap"
            assert state.sandbox_session is not None
            for name in (
                "bash",
                "python",
                "read_file",
                "write_file",
                "file_edit",
                "list_directory",
                "search_files",
                "process",
            ):
                assert state.tools.get_tool(name) is not None, f"{name} not registered"
        finally:
            await shutdown(state)

    @pytest.mark.asyncio
    async def test_sqlite_connects(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            assert state.db is not None
            assert report.subsystems.get("sqlite") is True
            health = await state.db.health_check()
            assert health is True
        finally:
            await shutdown(state)

    @pytest.mark.asyncio
    async def test_migrations_run(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            assert report.subsystems.get("migrations") is True
            tables = await state.db.fetch(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            table_names = {r["name"] for r in tables}
            assert "episodes" in table_names
            assert "knowledge_store" in table_names
            assert "social_graph_nodes" in table_names
            assert "evolved_soul" in table_names
            assert "resume_state" in table_names
            assert "lc_queue" in table_names
            assert "user_skills" in table_names
            assert "learning_signals" in table_names
        finally:
            await shutdown(state)

    @pytest.mark.asyncio
    async def test_catalog_loads(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            assert state.catalog is not None
            assert report.subsystems.get("catalog") is True
            assert len(state.catalog.agents) == 4
            assert len(state.catalog.skills) == 10
            assert len(state.catalog.tools) == 2
            assert state.catalog.core_soul.get("version") == "1.4.0"
        finally:
            await shutdown(state)

    @pytest.mark.asyncio
    async def test_working_memory_in_memory(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            assert state.working_memory is not None
            assert report.subsystems.get("working_memory") is True
            from src.memory.working_memory import EntryType

            await state.working_memory.write("task-1", EntryType.MESSAGE_USER, "Hello integration")
            entries = await state.working_memory.read("task-1")
            assert len(entries) == 1
            assert entries[0].content == "Hello integration"
        finally:
            await shutdown(state)

    @pytest.mark.asyncio
    async def test_memory_subsystems_boot(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            assert state.episodic_memory is not None
            assert state.knowledge_store is not None
            assert state.social_graph is not None
            assert state.soul is not None
            assert state.memory_system is not None
        finally:
            await shutdown(state)

    @pytest.mark.asyncio
    async def test_soul_core_from_catalog(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            core = await state.soul.get_core_soul()
            assert core is not None
            assert core.get("version") == "1.4.0"
            assert "preamble" in core
        finally:
            await shutdown(state)


class TestMemoryFlow:
    @pytest.mark.asyncio
    async def test_episodic_write_and_search(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            ep = await state.episodic_memory.create_episode(
                "Budget meeting Q4",
                "Discussed quarterly allocation",
                "Full transcript",
            )
            assert ep["id"]
            assert ep["key"] == "Budget meeting Q4"

            results = await state.episodic_memory.search_episodes("budget quarterly")
            assert len(results) >= 1
            assert results[0]["key"] == "Budget meeting Q4"
        finally:
            await shutdown(state)

    @pytest.mark.asyncio
    async def test_knowledge_write_and_search(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            fact = await state.knowledge_store.write_knowledge_fact(
                "Team size",
                "Engineering team has 12 members",
            )
            assert fact["confidence"] == 1.0

            results = await state.knowledge_store.search_knowledge("engineering team")
            assert len(results) >= 1
        finally:
            await shutdown(state)

    @pytest.mark.asyncio
    async def test_social_graph_create_and_search(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            person = await state.social_graph.create_person("Alice Johnson", is_primary=True)
            assert person["name"] == "Alice Johnson"

            primary = await state.social_graph.get_primary_person()
            assert primary is not None
            assert primary["name"] == "Alice Johnson"

            results = await state.social_graph.search_social_graph("Alice")
            assert len(results) >= 1
        finally:
            await shutdown(state)

    @pytest.mark.asyncio
    async def test_evolved_soul_trait(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            trait = await state.soul.update_evolved_soul(
                "communication_style",
                "Prefers concise responses",
                0.85,
                5,
            )
            assert trait["status"] == "expressed"

            expressed = await state.soul.get_evolved_soul()
            assert len(expressed) == 1
            assert expressed[0]["trait"] == "communication_style"
        finally:
            await shutdown(state)


class TestResumeStateAndTrajectory:
    @pytest.mark.asyncio
    async def test_resume_state_survives_write_read(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            from src.engine_core.resume_state import ResumeState, ResumeStateManager

            mgr = ResumeStateManager(state.db)
            cs = ResumeState(
                charter_id="charter-int-test",
                thread_id="task-int-test",
                node_status={"n1": "completed"},
            )
            await mgr.write_checkpoint(cs)
            loaded = await mgr.read_checkpoint("charter-int-test")
            assert loaded is not None
            assert loaded.node_status["n1"] == "completed"
        finally:
            await shutdown(state)

    @pytest.mark.asyncio
    async def test_trajectory_flush_to_lc_queue(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            from src.memory.working_memory import EntryType
            from src.engine_core.trajectory import flush_wm_to_lcq

            await state.working_memory.write("task-traj", EntryType.MESSAGE_USER, "Test query")
            await state.working_memory.write(
                "task-traj", EntryType.MESSAGE_ASSISTANT, "Test response"
            )

            row_id = await flush_wm_to_lcq(
                state.working_memory,
                "task-traj",
                state.db,
            )
            assert row_id is not None

            row = await state.db.fetchrow("SELECT trajectory FROM lc_queue WHERE id = ?", (row_id,))
            traj = json.loads(row["trajectory"])
            assert traj["status"] == "completed"
        finally:
            await shutdown(state)

    @pytest.mark.asyncio
    async def test_orphan_cleanup(self):
        settings = make_test_settings()
        state, report = await bootstrap(settings=settings)
        try:
            import uuid

            await state.db.execute(
                "INSERT INTO user_skills (id, name, platform_skill_name) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), "orphan_skill", "deleted_platform_skill"),
            )
            await state.db.commit()

            count = await state.db.fetchval(
                "SELECT count(*) FROM user_skills WHERE platform_skill_name = ?",
                ("deleted_platform_skill",),
            )
            assert count == 1

            state2, report2 = await bootstrap(settings=settings)
            try:
                assert report2.subsystems.get("orphan_cleanup") is True
            finally:
                await shutdown(state2)
        finally:
            await shutdown(state)
