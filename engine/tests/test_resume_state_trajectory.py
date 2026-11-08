"""Tests for SQLite-backed channel state and trajectory."""

import json
import pytest
import pytest_asyncio

from src.infra.sqlite_pool import SqlitePool
from src.engine_core.resume_state import ResumeState, ResumeStateManager
from src.engine_core.trajectory import flush_wm_to_lcq, record_feedback
from src.memory.inmemory_working_memory import InMemoryWorkingMemory
from src.memory.working_memory import EntryType


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


class TestResumeState:
    @pytest.mark.asyncio
    async def test_write_and_read_checkpoint(self, db):
        mgr = ResumeStateManager(db)
        state = ResumeState(charter_id="charter-001", thread_id="task-001")
        await mgr.write_checkpoint(state)

        loaded = await mgr.read_checkpoint("charter-001")
        assert loaded is not None
        assert loaded.charter_id == "charter-001"
        assert loaded.thread_id == "task-001"

    @pytest.mark.asyncio
    async def test_read_nonexistent(self, db):
        mgr = ResumeStateManager(db)
        result = await mgr.read_checkpoint("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_checkpoint(self, db):
        mgr = ResumeStateManager(db)
        state = ResumeState(charter_id="charter-002")
        await mgr.write_checkpoint(state)
        await mgr.delete_checkpoint("charter-002")

        result = await mgr.read_checkpoint("charter-002")
        assert result is None

    @pytest.mark.asyncio
    async def test_input_checkpoint(self, db):
        mgr = ResumeStateManager(db)
        state = ResumeState(
            charter_id="charter-input",
            pending_input={"question": "Should I proceed?"},
        )
        await mgr.write_input_checkpoint(state)

        loaded = await mgr.read_checkpoint("charter-input")
        assert loaded.pending_input["question"] == "Should I proceed?"

    @pytest.mark.asyncio
    async def test_checkpoint_preserves_node_status(self, db):
        mgr = ResumeStateManager(db)
        state = ResumeState(
            charter_id="charter-003",
            node_status={"n1": "completed", "n2": "in_progress"},
            partial_results={"n1": "Research done"},
        )
        await mgr.write_checkpoint(state)

        loaded = await mgr.read_checkpoint("charter-003")
        assert loaded.node_status["n1"] == "completed"
        assert loaded.partial_results["n1"] == "Research done"

    @pytest.mark.asyncio
    async def test_none_db_graceful(self):
        mgr = ResumeStateManager(None)
        state = ResumeState(charter_id="test")
        await mgr.write_checkpoint(state)
        result = await mgr.read_checkpoint("test")
        assert result is None

    @pytest.mark.asyncio
    async def test_store_and_list_content(self, db):
        mgr = ResumeStateManager(db)
        await mgr.store_content("task-1", "doc-1", "Report", "Full report content here")
        await mgr.store_content("task-1", "doc-2", "Summary", "Brief summary")

        items = await mgr.list_content("task-1")
        assert len(items) == 2
        titles = {i["title"] for i in items}
        assert "Report" in titles
        assert "Summary" in titles

    @pytest.mark.asyncio
    async def test_get_content(self, db):
        mgr = ResumeStateManager(db)
        await mgr.store_content("task-1", "doc-1", "Report", "Full content")
        content = await mgr.get_content("task-1", "doc-1")
        assert content == "Full content"

    @pytest.mark.asyncio
    async def test_search_content(self, db):
        mgr = ResumeStateManager(db)
        await mgr.store_content(
            "task-1", "doc-1", "Report", "Line one\nLine two with keyword\nLine three"
        )
        result = await mgr.search_content("task-1", "keyword")
        assert result["total_matches"] == 1


class TestTrajectory:
    @pytest.mark.asyncio
    async def test_flush_wm_to_lcq(self, db):
        wm = InMemoryWorkingMemory("test-user")
        await wm.write("task-1", EntryType.MESSAGE_USER, "What is the weather?")
        await wm.write("task-1", EntryType.MESSAGE_ASSISTANT, "It's sunny today.")

        row_id = await flush_wm_to_lcq(
            wm,
            "task-1",
            db,
            duration_ms=500,
        )
        assert row_id is not None

        row = await db.fetchrow("SELECT trajectory FROM lc_queue WHERE id = ?", (row_id,))
        traj = json.loads(row["trajectory"])
        assert traj["duration_ms"] == 500

    @pytest.mark.asyncio
    async def test_flush_none_db(self):
        wm = InMemoryWorkingMemory("test-user")
        result = await flush_wm_to_lcq(wm, "task-1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_record_feedback(self, db):
        wm = InMemoryWorkingMemory("test-user")
        await wm.write("task-1", EntryType.MESSAGE_USER, "Test query")
        row_id = await flush_wm_to_lcq(wm, "task-1", db)
        assert row_id is not None

        await record_feedback(db, "task-1", "positive")

        row = await db.fetchrow("SELECT trajectory FROM lc_queue WHERE id = ?", (row_id,))
        traj = json.loads(row["trajectory"])
        assert traj["user_feedback"]["type"] == "positive"


class TestCatalogLoader:
    @staticmethod
    def skip_if_no_catalog():
        import pytest
        from pathlib import Path

        core_dir = Path(__file__).parent.parent / "catalog" / "core" / "agents"
        if not core_dir.exists() or not any(core_dir.iterdir()):
            pytest.skip("catalog not mounted (Docker / CI)")

    def test_load_catalog_folder_based(self):
        self.skip_if_no_catalog()
        from pathlib import Path
        from src.catalog import load_catalog

        catalog = load_catalog(Path(__file__).parent.parent / "catalog")

        assert len(catalog.agents) == 4
        assert len(catalog.skills) == 10
        assert len(catalog.tools) == 2
        assert catalog.core_soul.get("version") == "1.4.0"

        assert catalog.get_agent("researcher") is not None
        assert catalog.get_skill("summarizer") is not None
        assert catalog.get_tool("web_search") is not None

    def test_skill_has_dir_reference(self):
        self.skip_if_no_catalog()
        from pathlib import Path
        from src.catalog import load_catalog

        catalog = load_catalog(Path(__file__).parent.parent / "catalog")
        skill = catalog.get_skill("summarizer")
        assert skill is not None
        assert "_skill_dir" in skill
        assert Path(skill["_skill_dir"]).exists()

    def test_agent_has_system_prompt(self):
        self.skip_if_no_catalog()
        from pathlib import Path
        from src.catalog import load_catalog

        catalog = load_catalog(Path(__file__).parent.parent / "catalog")
        agent = catalog.get_agent("researcher")
        assert agent is not None
        assert "system_prompt" in agent
        assert len(agent["system_prompt"]) > 50

    def test_all_names(self):
        self.skip_if_no_catalog()
        from pathlib import Path
        from src.catalog import load_catalog

        catalog = load_catalog(Path(__file__).parent.parent / "catalog")
        assert "researcher" in catalog.all_agent_names()
        assert "summarizer" in catalog.all_skill_names()
        assert "web_search" in catalog.all_tool_names()

    def test_nonexistent_returns_none(self):
        self.skip_if_no_catalog()
        from pathlib import Path
        from src.catalog import load_catalog

        catalog = load_catalog(Path(__file__).parent.parent / "catalog")
        assert catalog.get_agent("nonexistent") is None
        assert catalog.get_skill("nonexistent") is None

    def test_skill_source_label(self):
        self.skip_if_no_catalog()
        from pathlib import Path
        from src.catalog import load_catalog

        catalog = load_catalog(Path(__file__).parent.parent / "catalog")
        entry = catalog.skills.get("summarizer")
        assert entry is not None
        assert entry.source == "core"

    def test_create_and_delete_user_skill(self):
        import tempfile
        from pathlib import Path
        from src.catalog import create_user_skill, delete_user_skill

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            result = create_user_skill(
                data_dir,
                {
                    "name": "test-skill",
                    "description": "A test skill",
                    "prompt": "Do the test thing.",
                    "category": "testing",
                },
            )
            assert result["status"] == "created"

            skill_dir = data_dir / "skills" / "test-skill"
            assert skill_dir.exists()
            skill_md = (skill_dir / "SKILL.md").read_text()
            assert "name: test-skill" in skill_md
            assert "category: testing" in skill_md
            assert "Do the test thing." in skill_md

            del_result = delete_user_skill(data_dir, "test-skill")
            assert del_result["status"] == "deleted"
            assert not skill_dir.exists()

    def test_user_skill_shadows_platform(self):
        self.skip_if_no_catalog()
        import tempfile
        from pathlib import Path
        from src.catalog import load_catalog, create_user_skill

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            create_user_skill(
                data_dir,
                {
                    "name": "summarizer",
                    "description": "My custom summarizer",
                    "prompt": "Summarize my way.",
                },
            )

            catalog = load_catalog(
                Path(__file__).parent.parent / "catalog",
                user_data_dir=str(data_dir),
            )
            skill = catalog.get_skill("summarizer")
            assert skill["description"] == "My custom summarizer"
            entry = catalog.skills["summarizer"]
            assert entry.source == "user"

    def test_keyword_skill_matching(self):
        self.skip_if_no_catalog()
        from pathlib import Path
        from src.catalog import load_catalog

        catalog = load_catalog(Path(__file__).parent.parent / "catalog")
        matches = catalog.match_skills("summarize this document")
        assert len(matches) >= 1
        names = [m["name"] for m in matches]
        assert "summarizer" in names
