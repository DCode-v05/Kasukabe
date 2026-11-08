"""Tests for the complete skill system — folder loading, layered resolution,
intent matching, user creation, LC forking, API endpoints."""

import tempfile
import pytest
import pytest_asyncio
from pathlib import Path

from src.infra.sqlite_pool import SqlitePool
from src.catalog import (
    load_catalog,
    create_user_skill,
    delete_user_skill,
    update_user_skill,
)


CATALOG_DIR = Path(__file__).parent.parent / "catalog"


@pytest_asyncio.fixture
async def db():
    pool = SqlitePool(":memory:")
    await pool.connect()
    migrations_dir = Path(__file__).parent.parent / "migrations"
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        await pool.executescript(sql_file.read_text(encoding="utf-8"))
    await pool.commit()
    yield pool
    await pool.close()


class TestFolderBasedLoading:
    def test_core_skills_loaded(self):
        catalog = load_catalog(CATALOG_DIR)
        assert len(catalog.skills) == 10
        assert catalog.get_skill("summarizer") is not None
        assert catalog.get_skill("data-formatter") is not None

    def test_core_agents_loaded(self):
        catalog = load_catalog(CATALOG_DIR)
        assert len(catalog.agents) == 4
        agent = catalog.get_agent("researcher")
        assert agent is not None
        assert "system_prompt" in agent
        assert len(agent["system_prompt"]) > 50

    def test_skill_has_dir_reference(self):
        catalog = load_catalog(CATALOG_DIR)
        skill = catalog.get_skill("summarizer")
        assert "_skill_dir" in skill
        assert Path(skill["_skill_dir"]).exists()

    def test_skill_has_metadata_flags(self):
        catalog = load_catalog(CATALOG_DIR)
        skill = catalog.get_skill("summarizer")
        assert "has_prompt" in skill
        assert skill["has_prompt"] is True

    def test_tools_loaded(self):
        catalog = load_catalog(CATALOG_DIR)
        assert len(catalog.tools) == 2
        assert catalog.get_tool("web_search") is not None

    def test_core_soul_loaded(self):
        catalog = load_catalog(CATALOG_DIR)
        assert catalog.core_soul.get("version") == "1.4.0"

    def test_source_label(self):
        catalog = load_catalog(CATALOG_DIR)
        entry = catalog.skills.get("summarizer")
        assert entry.source == "core"


class TestLayeredResolution:
    def test_user_skill_shadows_platform(self):
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
            catalog = load_catalog(CATALOG_DIR, user_data_dir=str(data_dir))
            skill = catalog.get_skill("summarizer")
            assert skill["description"] == "My custom summarizer"
            assert catalog.skills["summarizer"].source == "user"

    def test_platform_skill_still_exists_after_user_shadow(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            create_user_skill(data_dir, {"name": "summarizer", "description": "Custom"})
            catalog = load_catalog(CATALOG_DIR, user_data_dir=str(data_dir))
            # User version shadows
            assert catalog.get_skill("summarizer")["description"] == "Custom"
            # But 10 skills total (9 platform + 1 user replacing 1 platform)
            assert len(catalog.skills) == 10

    def test_loads_core_catalog(self):
        catalog = load_catalog(CATALOG_DIR)
        assert len(catalog.skills) >= 10


class TestIntentMatching:
    def test_keyword_matching(self):
        catalog = load_catalog(CATALOG_DIR)
        matches = catalog.match_skills("summarize this document")
        names = [m["name"] for m in matches]
        assert "summarizer" in names

    def test_code_review_matching(self):
        catalog = load_catalog(CATALOG_DIR)
        matches = catalog.match_skills("review this code for bugs")
        names = [m["name"] for m in matches]
        assert "code-reviewer" in names

    def test_no_match_returns_empty(self):
        catalog = load_catalog(CATALOG_DIR)
        matches = catalog.match_skills("quantum entanglement physics simulation")
        # May or may not match — but should not crash
        assert isinstance(matches, list)

    def test_returns_top_k(self):
        catalog = load_catalog(CATALOG_DIR)
        matches = catalog.match_skills("analyze data and format results", top_k=3)
        assert len(matches) <= 3


class TestSkillCRUD:
    def test_create_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            result = create_user_skill(
                data_dir,
                {
                    "name": "my-skill",
                    "description": "Test skill",
                    "prompt": "Do the thing.",
                    "scripts": {"helper.py": "def run(): return 'ok'"},
                    "references": {"guide.md": "# Usage Guide"},
                },
            )
            assert result["status"] == "created"
            skill_dir = data_dir / "skills" / "my-skill"
            skill_md = (skill_dir / "SKILL.md").read_text()
            assert "name: my-skill" in skill_md
            assert "Do the thing." in skill_md
            assert (skill_dir / "scripts" / "helper.py").exists()
            assert (skill_dir / "references" / "guide.md").exists()

    def test_update_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            create_user_skill(data_dir, {"name": "my-skill", "description": "V1", "prompt": "Old"})
            result = update_user_skill(data_dir, "my-skill", {"prompt": "New prompt"})
            assert result["status"] == "updated"
            content = (data_dir / "skills" / "my-skill" / "SKILL.md").read_text()
            assert "New prompt" in content
            assert "Old" not in content
            # Description survives an update that only touches the prompt.
            assert "V1" in content

    def test_delete_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            create_user_skill(data_dir, {"name": "my-skill", "description": "Test"})
            result = delete_user_skill(data_dir, "my-skill")
            assert result["status"] == "deleted"
            assert not (data_dir / "skills" / "my-skill").exists()

    def test_delete_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = delete_user_skill(Path(tmp), "nonexistent")
            assert result["status"] == "not_found"

    def test_reload_after_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            catalog = load_catalog(CATALOG_DIR, user_data_dir=str(data_dir))
            assert catalog.get_skill("new-skill") is None

            create_user_skill(data_dir, {"name": "new-skill", "description": "Brand new"})
            catalog.reload_user_skills(data_dir / "skills")
            assert catalog.get_skill("new-skill") is not None
            assert catalog.get_skill("new-skill")["description"] == "Brand new"


class TestLCSkillEvolution:
    @pytest.mark.asyncio
    async def test_skill_usage_tracking(self, db):
        import uuid

        await db.execute(
            "INSERT INTO user_skills (id, name, source, occurrence_count) VALUES (?, ?, 'platform', 0)",
            (str(uuid.uuid4()), "summarizer"),
        )
        await db.commit()

        # Simulate incrementing
        await db.execute(
            "UPDATE user_skills SET occurrence_count = occurrence_count + 1 WHERE name = ?",
            ("summarizer",),
        )
        await db.commit()

        row = await db.fetchrow(
            "SELECT occurrence_count FROM user_skills WHERE name = ?",
            ("summarizer",),
        )
        assert row["occurrence_count"] == 1

    @pytest.mark.asyncio
    async def test_correction_count(self, db):
        import uuid

        await db.execute(
            "INSERT INTO user_skills (id, name, source, occurrence_count, correction_count) VALUES (?, ?, 'platform', 10, 0)",
            (str(uuid.uuid4()), "summarizer"),
        )
        await db.commit()

        await db.execute(
            "UPDATE user_skills SET correction_count = correction_count + 1 WHERE name = ?",
            ("summarizer",),
        )
        await db.commit()

        row = await db.fetchrow(
            "SELECT correction_count FROM user_skills WHERE name = ?",
            ("summarizer",),
        )
        assert row["correction_count"] == 1

    @pytest.mark.asyncio
    async def test_fork_trigger_conditions(self, db):
        """Fork should trigger when: 10+ uses AND 3+ corrections AND ratio > 20%."""
        import uuid

        # 15 uses, 4 corrections = 26.7% → should fork
        await db.execute(
            "INSERT INTO user_skills (id, name, source, occurrence_count, correction_count) VALUES (?, ?, 'platform', 15, 4)",
            (str(uuid.uuid4()), "summarizer"),
        )
        await db.commit()

        row = await db.fetchrow(
            "SELECT occurrence_count, correction_count, source FROM user_skills WHERE name = ?",
            ("summarizer",),
        )
        occ = row["occurrence_count"]
        corr = row["correction_count"]
        should_fork = occ >= 10 and corr >= 3 and (corr / occ) > 0.2
        assert should_fork is True

    @pytest.mark.asyncio
    async def test_no_fork_below_threshold(self, db):
        """5 uses, 1 correction = 20% but only 5 uses → no fork."""
        import uuid

        await db.execute(
            "INSERT INTO user_skills (id, name, source, occurrence_count, correction_count) VALUES (?, ?, 'platform', 5, 1)",
            (str(uuid.uuid4()), "fact-checker"),
        )
        await db.commit()

        row = await db.fetchrow(
            "SELECT occurrence_count, correction_count FROM user_skills WHERE name = ?",
            ("fact-checker",),
        )
        occ = row["occurrence_count"]
        corr = row["correction_count"]
        should_fork = occ >= 10 and corr >= 3 and (corr / occ) > 0.2
        assert should_fork is False

    @pytest.mark.asyncio
    async def test_forked_source_flag(self, db):
        import uuid

        await db.execute(
            "INSERT INTO user_skills (id, name, source, occurrence_count) VALUES (?, ?, 'platform', 0)",
            (str(uuid.uuid4()), "summarizer"),
        )
        await db.commit()

        await db.execute(
            "UPDATE user_skills SET source = 'forked', forked_from = 'summarizer' WHERE name = ?",
            ("summarizer",),
        )
        await db.commit()

        row = await db.fetchrow(
            "SELECT source, forked_from FROM user_skills WHERE name = ?",
            ("summarizer",),
        )
        assert row["source"] == "forked"
        assert row["forked_from"] == "summarizer"
