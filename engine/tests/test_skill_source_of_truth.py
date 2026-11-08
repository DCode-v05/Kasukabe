"""The filesystem is the source of truth for skill definitions.

SQLite keeps per-skill *state* — the learned overlay, lifecycle counters, status
— and nothing else. The definition (name, description, capabilities, signatures,
prompt, source code) lives in the skill folder.

Why this matters beyond tidiness: when both stores held definitions, a
user-created skill was written to disk *and* given a `user_skills` row, and every
reader that merged the two emitted it twice — once properly from the catalog and
once as a bare name whose description column the live write path never populated.
The model saw a duplicate with an empty description. These tests pin that closed.

Real files, real migrated SQLite, live call sites. No mocks.
"""

from __future__ import annotations

import uuid

import pytest

from src.catalog import Catalog, SkillRoot, create_user_skill, load_skills_from_roots
from src.engine_core.search import Search
from src.utility_directory.ud_full import UDFull
from src.utility_directory.ud_meta import UDMeta
from src.utility_directory.ud_write import UDWrite


async def columns(db, table: str) -> set[str]:
    rows = await db.fetch(f"PRAGMA table_info({table})")
    return {dict(r)["name"] for r in rows}


def catalog_with_user_skill(tmp_path, name="my-skill", description="On disk"):
    """Create a real user skill on disk and load it the way boot does."""
    create_user_skill(tmp_path, {"name": name, "description": description, "prompt": "Body"})
    skills = load_skills_from_roots([SkillRoot(path=tmp_path / "skills", source="user")])
    return Catalog(agents={}, skills=skills, tools={})


async def insert_state_row(db, name, source="user", status="active"):
    """The state row the live create path writes alongside the folder."""
    await db.execute(
        "INSERT INTO user_skills (id, name, source, status) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), name, source, status),
    )
    await db.commit()


# ── Schema: definitions no longer live in SQLite ──────────────────


class TestSchema:
    @pytest.mark.asyncio
    async def test_definition_columns_are_gone(self, db):
        cols = await columns(db, "user_skills")
        for definition_col in (
            "source_code",
            "prompt_template",
            "config",
            "capabilities",
            "input_signature",
            "output_signature",
            "description",
        ):
            assert definition_col not in cols, f"{definition_col} still duplicates the folder"

    @pytest.mark.asyncio
    async def test_state_columns_remain(self, db):
        cols = await columns(db, "user_skills")
        for state_col in (
            "learned_state",
            "status",
            "confidence",
            "occurrence_count",
            "correction_count",
            "gotchas",
            "forked_from",
            "last_used",
        ):
            assert state_col in cols


# ── The duplication this closes ───────────────────────────────────


class TestNoDuplication:
    @pytest.mark.asyncio
    async def test_search_skills_returns_a_user_skill_exactly_once(self, db, tmp_path):
        catalog = catalog_with_user_skill(tmp_path)
        await insert_state_row(db, "my-skill")

        search = Search(db=db, catalog=catalog)
        names = [s["name"] for s in await search.skills()]
        assert names.count("my-skill") == 1

    @pytest.mark.asyncio
    async def test_search_skills_keeps_the_real_description(self, db, tmp_path):
        """The duplicate carried an empty description. The survivor must not."""
        catalog = catalog_with_user_skill(tmp_path, description="On disk")
        await insert_state_row(db, "my-skill")

        search = Search(db=db, catalog=catalog)
        entry = next(s for s in await search.skills() if s["name"] == "my-skill")
        assert entry["description"] == "On disk"

    @pytest.mark.asyncio
    async def test_skills_meta_returns_a_user_skill_exactly_once(self, db, tmp_path):
        catalog = catalog_with_user_skill(tmp_path)
        await insert_state_row(db, "my-skill")

        meta = await UDMeta(db, catalog).get_skills_meta()
        assert [s["name"] for s in meta].count("my-skill") == 1

    @pytest.mark.asyncio
    async def test_state_row_without_a_folder_does_not_invent_a_skill(self, db, tmp_path):
        """A stale row must not conjure a skill the model can try to invoke."""
        catalog = Catalog(agents={}, skills={}, tools={})
        await insert_state_row(db, "ghost-skill")

        search = Search(db=db, catalog=catalog)
        assert "ghost-skill" not in [s["name"] for s in await search.skills()]


# ── UDWrite is state-only ─────────────────────────────────────────


class TestUDWriteStateOnly:
    @pytest.mark.asyncio
    async def test_learned_state_still_round_trips(self, db):
        result = await UDWrite(db).write_user_skill({"name": "tuned", "learned_state": {"rev": 3}})
        assert result["learned_state"] == {"rev": 3}

    @pytest.mark.asyncio
    async def test_lifecycle_fields_still_written(self, db):
        result = await UDWrite(db).write_user_skill(
            {"name": "counted", "confidence": 0.9, "occurrence_count": 2}
        )
        assert result["confidence"] == 0.9
        assert result["occurrence_count"] == 2

    @pytest.mark.asyncio
    async def test_definition_fields_are_not_persisted(self, db):
        """Passing a definition must not resurrect a second source of truth."""
        await UDWrite(db).write_user_skill(
            {"name": "defn", "description": "should not persist", "source_code": "x = 1"}
        )
        row = await db.fetchrow("SELECT * FROM user_skills WHERE name = ?", ("defn",))
        assert "description" not in dict(row)
        assert "source_code" not in dict(row)


# ── UDFull reads definitions from the catalog ─────────────────────


class TestUDFullReadsCatalog:
    @pytest.mark.asyncio
    async def test_user_created_skill_comes_from_the_folder(self, db, tmp_path):
        catalog = catalog_with_user_skill(tmp_path, "solo", "From the folder")
        await insert_state_row(db, "solo")

        got = await UDFull(db, catalog).get_skill_user_created("solo")
        assert got is not None
        assert got["description"] == "From the folder"

    @pytest.mark.asyncio
    async def test_platform_skill_merges_only_learned_state(self, db, tmp_path):
        catalog = catalog_with_user_skill(tmp_path, "shared", "Platform text")
        await insert_state_row(db, "shared")
        await UDWrite(db).write_user_skill({"name": "shared", "learned_state": {"n": 1}})

        got = await UDFull(db, catalog).get_skill("shared", "1.0.0")
        assert got is not None
        assert got["description"] == "Platform text"
        assert got["learned_state"] == {"n": 1}

    @pytest.mark.asyncio
    async def test_missing_skill_returns_none(self, db):
        catalog = Catalog(agents={}, skills={}, tools={})
        assert await UDFull(db, catalog).get_skill_user_created("nope") is None


# ── Fails loudly ──────────────────────────────────────────────────


class TestFailsLoudly:
    def test_root_pointing_at_a_file_raises(self, tmp_path):
        """An absent root is normal. A root that is a FILE is a config error.

        Treating the two the same is how a typo in a path becomes a silently
        empty skill list instead of a startup failure.
        """
        f = tmp_path / "not-a-dir"
        f.write_text("oops", encoding="utf-8")
        with pytest.raises(NotADirectoryError):
            load_skills_from_roots([SkillRoot(path=f, source="managed")])

    def test_absent_root_is_still_tolerated(self, tmp_path):
        assert load_skills_from_roots([SkillRoot(path=tmp_path / "gone", source="user")]) == {}
