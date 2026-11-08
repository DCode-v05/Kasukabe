"""Skill roots, SKILL.md format, and the metadata budget.

Three contracts pinned here:

1. **Roots are data.** Skill sources are a list of ``SkillRoot`` values resolved
   at call time, not hardcoded loader calls. Precedence is list order: a later
   root shadows an earlier one by name. Adding a source is configuration, which
   is what stops a source being documented but never wired (the domain-pack
   drift this replaces).

2. **SKILL.md is the format.** YAML frontmatter carries ``name`` and
   ``description``; the body is the instructions. Legacy ``skill.json`` folders
   still load so nothing breaks mid-migration, but every write emits SKILL.md.

3. **Skill metadata is budgeted.** What reaches the model is capped relative to
   the context window and reports what it dropped, rather than growing without
   limit as the skill count grows.

Real files on real disk throughout; no mocks.
"""

from __future__ import annotations

import json
import logging

from src.catalog import (
    DEFAULT_SKILL_METADATA_CHAR_BUDGET,
    SKILL_METADATA_CONTEXT_WINDOW_PERCENT,
    SkillRoot,
    apply_skill_metadata_budget,
    create_user_skill,
    load_skills_from_roots,
    skill_metadata_budget,
)


def write_skill_md(root, name, description, body="Do the thing.", extra_frontmatter=""):
    """Create a SKILL.md-format skill folder under *root*."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: {description}\n{extra_frontmatter}---\n\n{body}\n"
    (d / "SKILL.md").write_text(fm, encoding="utf-8")
    return d


def write_legacy_skill(root, name, description, version="1.0.0"):
    """Create a legacy skill.json folder under *root*."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.json").write_text(
        json.dumps({"name": name, "description": description, "version": version}),
        encoding="utf-8",
    )
    return d


# ── Roots as data ─────────────────────────────────────────────────


class TestSkillRoots:
    def test_single_root_loads_skills(self, tmp_path):
        write_skill_md(tmp_path, "alpha", "First skill")
        got = load_skills_from_roots([SkillRoot(path=tmp_path, source="core")])
        assert "alpha" in got
        assert got["alpha"].data["description"] == "First skill"

    def test_later_root_shadows_earlier_by_name(self, tmp_path):
        core, user = tmp_path / "core", tmp_path / "user"
        write_skill_md(core, "shared", "from core")
        write_skill_md(user, "shared", "from user")
        got = load_skills_from_roots(
            [SkillRoot(path=core, source="core"), SkillRoot(path=user, source="user")]
        )
        assert got["shared"].data["description"] == "from user"
        assert got["shared"].source == "user"

    def test_precedence_follows_list_order_not_source_name(self, tmp_path):
        """Order in the list is the whole precedence rule. Reverse it, reverse the winner."""
        core, user = tmp_path / "core", tmp_path / "user"
        write_skill_md(core, "shared", "from core")
        write_skill_md(user, "shared", "from user")
        got = load_skills_from_roots(
            [SkillRoot(path=user, source="user"), SkillRoot(path=core, source="core")]
        )
        assert got["shared"].data["description"] == "from core"

    def test_roots_union_non_conflicting_names(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        write_skill_md(a, "one", "1")
        write_skill_md(b, "two", "2")
        got = load_skills_from_roots(
            [SkillRoot(path=a, source="core"), SkillRoot(path=b, source="user")]
        )
        assert set(got) == {"one", "two"}

    def test_missing_root_is_logged_not_silent(self, tmp_path, caplog):
        """A configured root that does not exist must leave a trace.

        Silent no-ops are exactly how a source gets configured and never
        noticed as broken.
        """
        missing = tmp_path / "nope"
        with caplog.at_level(logging.INFO):
            got = load_skills_from_roots([SkillRoot(path=missing, source="managed")])
        assert got == {}
        assert any("nope" in r.message or "nope" in str(r.args) for r in caplog.records)

    def test_empty_root_list_returns_empty(self):
        assert load_skills_from_roots([]) == {}

    def test_source_is_recorded_per_entry(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        write_skill_md(a, "one", "1")
        write_skill_md(b, "two", "2")
        got = load_skills_from_roots(
            [SkillRoot(path=a, source="core"), SkillRoot(path=b, source="managed")]
        )
        assert got["one"].source == "core"
        assert got["two"].source == "managed"


# ── SKILL.md format ───────────────────────────────────────────────


class TestSkillMdFormat:
    def test_frontmatter_name_and_description_are_read(self, tmp_path):
        write_skill_md(tmp_path, "parser", "Parses things well")
        got = load_skills_from_roots([SkillRoot(path=tmp_path, source="core")])
        assert got["parser"].data["name"] == "parser"
        assert got["parser"].data["description"] == "Parses things well"

    def test_body_is_available_as_the_prompt(self, tmp_path):
        write_skill_md(tmp_path, "writer", "Writes", body="# Steps\n\nBe concise.")
        got = load_skills_from_roots([SkillRoot(path=tmp_path, source="core")])
        assert "Be concise." in got["writer"].data["prompt"]

    def test_optional_short_description_is_read(self, tmp_path):
        write_skill_md(
            tmp_path,
            "brief",
            "A long description that goes on",
            extra_frontmatter="short-description: Short one\n",
        )
        got = load_skills_from_roots([SkillRoot(path=tmp_path, source="core")])
        assert got["brief"].data["short_description"] == "Short one"

    def test_directory_name_is_the_fallback_name(self, tmp_path):
        d = tmp_path / "unnamed"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\ndescription: No name key\n---\n\nBody\n", encoding="utf-8"
        )
        got = load_skills_from_roots([SkillRoot(path=tmp_path, source="core")])
        assert "unnamed" in got

    def test_legacy_skill_json_still_loads(self, tmp_path):
        write_legacy_skill(tmp_path, "oldtimer", "Legacy format")
        got = load_skills_from_roots([SkillRoot(path=tmp_path, source="core")])
        assert got["oldtimer"].data["description"] == "Legacy format"

    def test_skill_md_wins_when_both_present(self, tmp_path):
        d = write_legacy_skill(tmp_path, "both", "from json")
        (d / "SKILL.md").write_text(
            "---\nname: both\ndescription: from md\n---\n\nBody\n", encoding="utf-8"
        )
        got = load_skills_from_roots([SkillRoot(path=tmp_path, source="core")])
        assert got["both"].data["description"] == "from md"

    def test_folder_without_either_file_is_skipped(self, tmp_path):
        (tmp_path / "empty").mkdir(parents=True)
        write_skill_md(tmp_path, "real", "Real one")
        got = load_skills_from_roots([SkillRoot(path=tmp_path, source="core")])
        assert set(got) == {"real"}

    def test_malformed_frontmatter_is_skipped_and_logged(self, tmp_path, caplog):
        """One broken skill must not take the whole root down with it."""
        d = tmp_path / "broken"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: [unclosed\n---\n\nBody\n", encoding="utf-8")
        write_skill_md(tmp_path, "fine", "Loads fine")
        with caplog.at_level(logging.WARNING):
            got = load_skills_from_roots([SkillRoot(path=tmp_path, source="core")])
        assert "fine" in got
        assert "broken" not in got
        assert any("broken" in r.getMessage() for r in caplog.records)

    def test_create_user_skill_writes_skill_md(self, tmp_path):
        create_user_skill(tmp_path, {"name": "fresh", "description": "New", "prompt": "Body here"})
        written = tmp_path / "skills" / "fresh" / "SKILL.md"
        assert written.exists()
        text = written.read_text(encoding="utf-8")
        assert text.startswith("---")
        assert "name: fresh" in text
        assert "Body here" in text

    def test_created_skill_round_trips_through_the_loader(self, tmp_path):
        create_user_skill(
            tmp_path, {"name": "circular", "description": "Round trip", "prompt": "Instructions"}
        )
        got = load_skills_from_roots([SkillRoot(path=tmp_path / "skills", source="user")])
        assert got["circular"].data["description"] == "Round trip"
        assert "Instructions" in got["circular"].data["prompt"]

    def test_scripts_and_references_still_supported(self, tmp_path):
        d = write_skill_md(tmp_path, "rich-skill", "Has extras")
        (d / "scripts").mkdir()
        (d / "scripts" / "run.py").write_text("print('hi')", encoding="utf-8")
        (d / "references").mkdir()
        (d / "references" / "api.md").write_text("# API", encoding="utf-8")
        (d / "gotchas.md").write_text("Watch out for X", encoding="utf-8")
        got = load_skills_from_roots([SkillRoot(path=tmp_path, source="core")])
        assert got["rich-skill"].data["has_scripts"] is True
        assert got["rich-skill"].data["has_references"] is True
        assert got["rich-skill"].data["has_gotchas"] is True


# ── Metadata budget ───────────────────────────────────────────────


class TestSkillMetadataBudget:
    def test_budget_is_a_percentage_of_the_context_window(self):
        window = 200_000
        expected = window * SKILL_METADATA_CONTEXT_WINDOW_PERCENT // 100
        assert skill_metadata_budget(window) == expected

    def test_budget_falls_back_to_char_default_without_a_window(self):
        assert skill_metadata_budget(None) == DEFAULT_SKILL_METADATA_CHAR_BUDGET

    def test_zero_or_negative_window_falls_back(self):
        assert skill_metadata_budget(0) == DEFAULT_SKILL_METADATA_CHAR_BUDGET
        assert skill_metadata_budget(-1) == DEFAULT_SKILL_METADATA_CHAR_BUDGET

    def test_under_budget_keeps_everything(self):
        skills = [{"name": f"s{i}", "description": "short"} for i in range(3)]
        kept, dropped = apply_skill_metadata_budget(skills, budget_chars=10_000)
        assert kept == skills
        assert dropped == 0

    def test_over_budget_truncates_and_counts_drops(self):
        skills = [{"name": f"s{i}", "description": "x" * 100} for i in range(50)]
        kept, dropped = apply_skill_metadata_budget(skills, budget_chars=500)
        assert len(kept) < len(skills)
        assert dropped == len(skills) - len(kept)

    def test_over_budget_logs_what_it_dropped(self, caplog):
        """Silent truncation reads as 'the model saw everything'. It must not."""
        skills = [{"name": f"s{i}", "description": "x" * 100} for i in range(50)]
        with caplog.at_level(logging.WARNING):
            apply_skill_metadata_budget(skills, budget_chars=500)
        assert any("skill" in r.getMessage().lower() for r in caplog.records)

    def test_budget_never_returns_nothing_when_skills_exist(self):
        """A budget smaller than one entry still surfaces one, not zero."""
        skills = [{"name": "only", "description": "y" * 5_000}]
        kept, _ = apply_skill_metadata_budget(skills, budget_chars=10)
        assert len(kept) == 1

    def test_empty_input_is_handled(self):
        assert apply_skill_metadata_budget([], budget_chars=100) == ([], 0)


# ── Domain-pack removal ───────────────────────────────────────────


class TestDomainPackRemoved:
    def test_no_domain_pack_references_remain(self):
        """It was documented as a source for months and never loaded. Keep it gone."""
        from pathlib import Path

        text = (Path(__file__).parent.parent / "src" / "catalog.py").read_text(encoding="utf-8")
        assert "domain" not in text.lower()
