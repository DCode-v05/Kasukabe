"""Learned-state column contract — the DSPy-free replacement for the UD overlay.

DSPy was removed from the engine (the dependency was never imported; only the
column names and docstrings referenced it). The per-user overlay it named
survives under the Learning Centre, renamed to ``learned_state``:

    user_agents.dspy_optimized_state -> user_agents.learned_state
    user_skills.optimized_state      -> user_skills.learned_state
    user_agents.dspy_module          -> dropped (write-only, no reader)

These tests pin that contract end to end against a real migrated SQLite
database, through the live UDWrite / UDFull / PlatformRegistry call sites
rather than raw SQL, so a rename that misses a call site fails here.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.catalog import Catalog
from src.utility_directory.platform_registry import PlatformRegistry
from src.utility_directory.ud_full import UDFull
from src.utility_directory.ud_write import UDWrite


async def columns(db, table: str) -> set[str]:
    """Return the column names of *table* from the live schema."""
    rows = await db.fetch(f"PRAGMA table_info({table})")
    return {dict(r)["name"] for r in rows}


# ── Schema ────────────────────────────────────────────────────────


class TestSchema:
    @pytest.mark.asyncio
    async def test_user_agents_has_learned_state(self, db):
        cols = await columns(db, "user_agents")
        assert "learned_state" in cols

    @pytest.mark.asyncio
    async def test_user_skills_has_learned_state(self, db):
        cols = await columns(db, "user_skills")
        assert "learned_state" in cols

    @pytest.mark.asyncio
    async def test_no_dspy_named_columns_remain(self, db):
        """A half-done rename leaves the old column behind. Catch that."""
        agent_cols = await columns(db, "user_agents")
        skill_cols = await columns(db, "user_skills")
        assert "dspy_optimized_state" not in agent_cols
        assert "dspy_module" not in agent_cols
        assert "optimized_state" not in skill_cols

    @pytest.mark.asyncio
    async def test_unknown_column_fails_loudly(self, db):
        """Guard the guard: PRAGMA-based assertions above must be real."""
        with pytest.raises(Exception):
            await db.execute(
                "INSERT INTO user_agents (id, name, dspy_optimized_state) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), "ghost", "{}"),
            )


# ── UDWrite ───────────────────────────────────────────────────────


class TestUDWrite:
    @pytest.mark.asyncio
    async def test_write_user_agent_round_trips_learned_state(self, db):
        w = UDWrite(db)
        result = await w.write_user_agent(
            {
                "name": "researcher",
                "source": "user_created",
                "learned_state": {"demos": [1, 2, 3], "score": 0.91},
            }
        )
        assert result["learned_state"] == {"demos": [1, 2, 3], "score": 0.91}

    @pytest.mark.asyncio
    async def test_learned_state_is_stored_as_json_text(self, db):
        w = UDWrite(db)
        await w.write_user_agent({"name": "planner", "learned_state": {"k": "v"}})
        row = await db.fetchrow(
            "SELECT learned_state FROM user_agents WHERE name = ?", ("planner",)
        )
        assert json.loads(dict(row)["learned_state"]) == {"k": "v"}

    @pytest.mark.asyncio
    async def test_upsert_preserves_learned_state_when_absent(self, db):
        """COALESCE guard: a later write without learned_state must not wipe it."""
        w = UDWrite(db)
        await w.write_user_agent({"name": "keeper", "learned_state": {"keep": True}})
        after = await w.write_user_agent({"name": "keeper", "description": "second write"})
        assert after["learned_state"] == {"keep": True}
        assert after["description"] == "second write"

    @pytest.mark.asyncio
    async def test_update_agent_state_writes_learned_state(self, db):
        w = UDWrite(db)
        await w.write_user_agent({"name": "tuned", "source": "platform"})
        updated = await w.update_agent_state("tuned", {"round": 2})
        assert updated is True
        row = await db.fetchrow("SELECT learned_state FROM user_agents WHERE name = ?", ("tuned",))
        assert json.loads(dict(row)["learned_state"]) == {"round": 2}

    @pytest.mark.asyncio
    async def test_update_agent_state_missing_row_returns_false(self, db):
        w = UDWrite(db)
        assert await w.update_agent_state("nobody", {"round": 1}) is False

    @pytest.mark.asyncio
    async def test_write_user_skill_round_trips_learned_state(self, db):
        w = UDWrite(db)
        result = await w.write_user_skill({"name": "summarize", "learned_state": {"prompt_rev": 4}})
        assert result["learned_state"] == {"prompt_rev": 4}

    @pytest.mark.asyncio
    async def test_skill_upsert_preserves_learned_state_when_absent(self, db):
        w = UDWrite(db)
        await w.write_user_skill({"name": "extract", "learned_state": {"v": 1}})
        after = await w.write_user_skill({"name": "extract", "description": "again"})
        assert after["learned_state"] == {"v": 1}


# ── UDFull ────────────────────────────────────────────────────────


class TestUDFull:
    @pytest.mark.asyncio
    async def test_get_agent_overlays_learned_state(self, db):
        catalog = Catalog(agents={}, skills={}, tools={})
        catalog.agents["writer"] = type(
            "E", (), {"data": {"name": "writer", "description": "platform"}}
        )()
        full = UDFull(db, catalog)

        await UDWrite(db).write_user_agent(
            {
                "name": "writer-user-row",
                "source": "platform",
                "platform_agent_name": "writer",
                "learned_state": {"overlaid": True},
            }
        )
        got = await full.get_agent("writer", "1.0.0")
        assert got is not None
        assert got["learned_state"] == {"overlaid": True}

    @pytest.mark.asyncio
    async def test_get_agent_first_use_has_null_learned_state(self, db):
        catalog = Catalog(agents={}, skills={}, tools={})
        catalog.agents["fresh"] = type("E", (), {"data": {"name": "fresh"}})()
        full = UDFull(db, catalog)
        got = await full.get_agent("fresh", "1.0.0")
        assert got is not None
        assert got["learned_state"] is None

    @pytest.mark.asyncio
    async def test_get_agent_user_created_parses_learned_state(self, db):
        catalog = Catalog(agents={}, skills={}, tools={})
        full = UDFull(db, catalog)
        await UDWrite(db).write_user_agent(
            {"name": "solo", "source": "user_created", "learned_state": {"n": 7}}
        )
        got = await full.get_agent_user_created("solo")
        assert got is not None
        assert got["learned_state"] == {"n": 7}


# ── PlatformRegistry ──────────────────────────────────────────────


class TestPlatformRegistry:
    @pytest.mark.asyncio
    async def test_get_user_agent_parses_learned_state(self, db):
        catalog = Catalog(agents={}, skills={}, tools={})
        reg = PlatformRegistry(db, catalog)
        await UDWrite(db).write_user_agent({"name": "reg-agent", "learned_state": {"a": 1}})
        got = await reg.get_user_agent("reg-agent")
        assert got is not None
        assert got["learned_state"] == {"a": 1}

    @pytest.mark.asyncio
    async def test_list_user_agents_parses_learned_state(self, db):
        catalog = Catalog(agents={}, skills={}, tools={})
        reg = PlatformRegistry(db, catalog)
        await UDWrite(db).write_user_agent({"name": "listed", "learned_state": {"b": 2}})
        rows = await reg.list_user_agents()
        assert [r for r in rows if r["name"] == "listed"][0]["learned_state"] == {"b": 2}

    @pytest.mark.asyncio
    async def test_get_user_skill_parses_learned_state(self, db):
        catalog = Catalog(agents={}, skills={}, tools={})
        reg = PlatformRegistry(db, catalog)
        await UDWrite(db).write_user_skill({"name": "reg-skill", "learned_state": {"c": 3}})
        got = await reg.get_user_skill("reg-skill")
        assert got is not None
        assert got["learned_state"] == {"c": 3}


# ── Dependency removal ────────────────────────────────────────────


class TestDspyRemoved:
    def test_dspy_is_not_installed(self):
        """The dependency is gone from the image, not merely unused in source."""
        with pytest.raises(ImportError):
            __import__("dspy")

    def test_dspy_absent_from_project_dependencies(self):
        """pyproject must not reinstate it on the next dependency refresh."""
        from pathlib import Path

        pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
        deps_block = pyproject.split("[project.optional-dependencies]")[0]
        assert "dspy" not in deps_block.lower()

    def test_no_dspy_identifiers_left_in_source(self):
        """Column names and docstrings included — the rename must be total."""
        from pathlib import Path

        src = Path(__file__).parent.parent / "src"
        migrations = Path(__file__).parent.parent / "migrations"
        offenders = []
        for root in (src, migrations):
            for path in list(root.rglob("*.py")) + list(root.rglob("*.sql")):
                if "__pycache__" in str(path):
                    continue
                if "dspy" in path.read_text(encoding="utf-8").lower():
                    offenders.append(str(path.relative_to(root.parent)))
        assert offenders == [], f"dspy still referenced in: {offenders}"
