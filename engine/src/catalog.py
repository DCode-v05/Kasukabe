"""Catalog loader — folder-based skills, agents, tools with layered resolution.

Skill sources are a LIST of ``SkillRoot`` values, resolved by the caller and
passed to :func:`load_skills_from_roots`. Precedence is list order: a later root
shadows an earlier one by name. Nothing here hardcodes where skills come from,
so adding a source (managed, registry-fetched, another local tree) is
configuration rather than an edit to this module. That property is deliberate —
the previous two hardcoded loader calls are why a third source could sit in this
docstring for months while never actually being loaded.

Skills are folders:
  skill-name/
  ├── SKILL.md         (YAML frontmatter + instructions — the format)
  ├── skill.json       (legacy metadata; still read, never written)
  ├── scripts/         (composable code, optional)
  ├── references/      (docs loaded on demand, optional)
  └── gotchas.md       (accumulated failure knowledge, optional)

Only the name and description reach the model up front, bounded by
:func:`skill_metadata_budget`. Everything else is read on demand from
``_skill_dir``.

Agents are folders:
  agent-name/
  ├── agent.json       (metadata)
  └── system-prompt.md (loaded when agent spawned)

Tools stay flat JSON (no folder structure needed).
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import yaml

logger = logging.getLogger(__name__)

# Share of the model's context window that skill metadata may occupy before it
# is trimmed. Two percent of a 200k window is ~4k characters, enough for a few
# hundred one-line descriptions.
SKILL_METADATA_CONTEXT_WINDOW_PERCENT = 2

# Used when the context window is unknown. Absolute character count, not tokens:
# the budget guards prompt bloat, and an exact token count is not worth a
# tokenizer round-trip on every catalog read.
DEFAULT_SKILL_METADATA_CHAR_BUDGET = 8_000

SKILL_MD_FILENAME = "SKILL.md"
LEGACY_SKILL_JSON_FILENAME = "skill.json"


@dataclass
class CatalogEntry:
    """A single agent, skill, or tool from the catalog."""

    name: str
    version: str
    data: dict
    source: str = "core"  # core, user, forked, lc_created, managed


@dataclass(frozen=True)
class SkillRoot:
    """One directory to scan for skills, and the provenance to stamp on them.

    ``path`` is resolved to an absolute path on construction so a relative root
    can never quietly resolve against whatever the process cwd happens to be.
    """

    path: Path
    source: str = "core"

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path).expanduser().resolve())


@dataclass
class Catalog:
    """In-memory catalog with layered resolution."""

    agents: dict[str, CatalogEntry] = field(default_factory=dict)
    skills: dict[str, CatalogEntry] = field(default_factory=dict)
    tools: dict[str, CatalogEntry] = field(default_factory=dict)
    core_soul: dict = field(default_factory=dict)
    skill_embeddings: dict[str, list[float]] = field(default_factory=dict)

    def get_agent(self, name: str) -> dict | None:
        entry = self.agents.get(name)
        return entry.data if entry else None

    def get_skill(self, name: str) -> dict | None:
        entry = self.skills.get(name)
        return entry.data if entry else None

    def get_tool(self, name: str) -> dict | None:
        entry = self.tools.get(name)
        return entry.data if entry else None

    def all_agents(self) -> list[dict]:
        return [e.data for e in self.agents.values()]

    def all_skills(self) -> list[dict]:
        return [e.data for e in self.skills.values()]

    def all_tools(self) -> list[dict]:
        return [e.data for e in self.tools.values()]

    def all_agent_names(self) -> list[str]:
        return list(self.agents.keys())

    def all_skill_names(self) -> list[str]:
        return list(self.skills.keys())

    def all_tool_names(self) -> list[str]:
        return list(self.tools.keys())

    async def embed_all_descriptions(self, embedding_svc: Any) -> None:
        """Pre-embed all skill descriptions at boot for fast intent matching."""
        if embedding_svc is None or not getattr(embedding_svc, "is_real", False):
            return
        for name, entry in self.skills.items():
            desc = entry.data.get("description", "")
            if desc:
                try:
                    self.skill_embeddings[name] = await embedding_svc.embed(desc)
                except Exception:
                    pass

    def match_skills(
        self,
        query: str,
        query_embedding: list[float] | None = None,
        top_k: int = 5,
    ) -> list[dict]:
        """Find skills relevant to a query using keyword + semantic matching."""
        from src.memory.embeddings import keyword_score, cosine_similarity

        scored: list[tuple[float, dict]] = []
        for name, entry in self.skills.items():
            desc = entry.data.get("description", "")
            kw = keyword_score(query, desc)

            sem = 0.0
            if query_embedding and name in self.skill_embeddings:
                sem = cosine_similarity(query_embedding, self.skill_embeddings[name])

            score = 0.3 * kw + 0.7 * sem if query_embedding else kw
            if score > 0.1:
                scored.append((score, entry.data))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [data for _, data in scored[:top_k]]

    def reload_user_skills(self, user_skills_dir: Path) -> None:
        """Hot-reload user skills from /data/skills/ after creation/deletion."""
        user_entries = load_skill_folders(user_skills_dir, source="user")
        # Remove existing user skills, keep platform
        to_remove = [
            n for n, e in self.skills.items() if e.source in ("user", "forked", "lc_created")
        ]
        for n in to_remove:
            del self.skills[n]
        # Add fresh user skills (shadow platform by name)
        self.skills.update(user_entries)


# ── Folder loaders ───────────────────────────────────────────


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Split a SKILL.md into its YAML frontmatter mapping and its body.

    A file with no leading ``---`` fence is all body and no metadata. Raises
    ``yaml.YAMLError`` on malformed frontmatter so the caller can skip that one
    skill rather than serve half of it.
    """
    if not text.startswith("---"):
        return {}, text

    rest = text[3:].lstrip("\r\n")
    closing = rest.find("\n---")
    if closing == -1:
        return {}, text

    raw = rest[:closing]
    body = rest[closing + 4 :].lstrip("\r\n")
    parsed = yaml.safe_load(raw) if raw.strip() else {}
    if not isinstance(parsed, dict):
        raise yaml.YAMLError(f"frontmatter must be a mapping, got {type(parsed).__name__}")
    return parsed, body


def read_skill_folder(skill_dir: Path) -> dict | None:
    """Read one skill folder into its catalog payload, or None if it is not one.

    SKILL.md wins when both formats are present. ``has_scripts`` / ``has_references``
    / ``has_gotchas`` are derived from what is actually on disk rather than
    trusted from stored metadata, which can and does drift.
    """
    skill_md = skill_dir / SKILL_MD_FILENAME
    legacy = skill_dir / LEGACY_SKILL_JSON_FILENAME

    if skill_md.exists():
        meta, body = split_frontmatter(skill_md.read_text(encoding="utf-8"))
        data = dict(meta)
        # codex nests it as `metadata: {short-description: ...}`; accept either
        # that or a flat `short-description` key.
        nested = data.pop("metadata", None)
        if isinstance(nested, dict) and "short-description" in nested:
            data.setdefault("short-description", nested["short-description"])
        if "short-description" in data:
            data["short_description"] = data.pop("short-description")
        if body.strip():
            data["prompt"] = body
    elif legacy.exists():
        data = json.loads(legacy.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{LEGACY_SKILL_JSON_FILENAME} must contain an object")
    else:
        return None

    data["name"] = data.get("name") or skill_dir.name
    data.setdefault("version", "1.0.0")
    data.setdefault("description", "")
    data["_skill_dir"] = str(skill_dir)
    data["has_prompt"] = bool(data.get("prompt")) or (skill_dir / "prompt.md").exists()
    data["has_scripts"] = (skill_dir / "scripts").is_dir()
    data["has_references"] = (skill_dir / "references").is_dir()
    data["has_gotchas"] = (skill_dir / "gotchas.md").exists()
    return data


def load_skills_from_roots(roots: Iterable[SkillRoot]) -> dict[str, CatalogEntry]:
    """Load skills from every root in order; later roots shadow earlier by name.

    A root that does not exist is logged, not silently skipped — a configured
    source that never loads should leave a trace. A single unreadable skill is
    logged and skipped without taking its root down with it.
    """
    entries: dict[str, CatalogEntry] = {}

    for root in roots:
        if root.path.exists() and not root.path.is_dir():
            # An absent root is normal (a user may have no skills yet). A root
            # that exists and is NOT a directory is a misconfiguration, and
            # treating the two alike turns a typo'd path into a silently empty
            # skill list instead of a startup failure.
            raise NotADirectoryError(
                f"Skill root is not a directory: {root.path} (source={root.source})"
            )
        if not root.path.is_dir():
            logger.info("Skill root absent, skipping: %s (source=%s)", root.path, root.source)
            continue

        found = 0
        for skill_dir in sorted(root.path.iterdir()):
            if not skill_dir.is_dir():
                continue
            try:
                data = read_skill_folder(skill_dir)
            except (json.JSONDecodeError, yaml.YAMLError, ValueError, OSError) as e:
                logger.warning("Failed to load skill %s: %s", skill_dir, e)
                continue
            if data is None:
                continue
            entries[data["name"]] = CatalogEntry(
                name=data["name"],
                version=data["version"],
                data=data,
                source=root.source,
            )
            found += 1

        logger.debug("Skill root %s (source=%s): %d skills", root.path, root.source, found)

    return entries


def load_skill_folders(directory: Path, source: str = "core") -> dict[str, CatalogEntry]:
    """Load skills from a single directory.

    Thin wrapper over :func:`load_skills_from_roots` for the single-root callers.
    """
    return load_skills_from_roots([SkillRoot(path=directory, source=source)])


# ── Skill metadata budget ────────────────────────────────────


def skill_metadata_budget(context_window: int | None) -> int:
    """Character budget for the skill list that reaches the model.

    A share of the context window when it is known, a fixed character budget
    when it is not. Sized so that adding skills degrades the list gracefully
    instead of quietly crowding out the conversation.
    """
    if context_window is not None and context_window > 0:
        return max(1, context_window * SKILL_METADATA_CONTEXT_WINDOW_PERCENT // 100)
    return DEFAULT_SKILL_METADATA_CHAR_BUDGET


def apply_skill_metadata_budget(
    skills: Sequence[dict], budget_chars: int
) -> tuple[list[dict], int]:
    """Trim *skills* to fit *budget_chars*, returning (kept, dropped_count).

    Always keeps at least one skill when any exist: showing the model one skill
    is more useful than showing it none. Anything dropped is logged — silent
    truncation reads downstream as "the model saw everything", which is the
    failure mode worth being loud about.
    """
    kept: list[dict] = []
    spent = 0

    for skill in skills:
        cost = len(str(skill.get("name", ""))) + len(str(skill.get("description", ""))) + 2
        if kept and spent + cost > budget_chars:
            break
        kept.append(skill)
        spent += cost

    dropped = len(skills) - len(kept)
    if dropped:
        logger.warning(
            "Skill metadata budget exceeded: kept %d of %d skills (%d chars, budget %d); "
            "%d dropped and not visible to the model",
            len(kept),
            len(skills),
            spent,
            budget_chars,
            dropped,
        )
    return kept, dropped


def load_agent_folders(directory: Path, source: str = "core") -> dict[str, CatalogEntry]:
    """Load agents from folder structure. Each subdirectory with agent.json is an agent."""
    entries: dict[str, CatalogEntry] = {}
    if not directory.exists():
        return entries

    for agent_dir in sorted(directory.iterdir()):
        if not agent_dir.is_dir():
            continue
        meta_file = agent_dir / "agent.json"
        if not meta_file.exists():
            continue
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            data["_agent_dir"] = str(agent_dir)
            # Load system-prompt.md if exists
            prompt_file = agent_dir / "system-prompt.md"
            if prompt_file.exists():
                data["system_prompt"] = prompt_file.read_text(encoding="utf-8")
            name = data.get("name", agent_dir.name)
            version = data.get("version", "1.0.0")
            entries[name] = CatalogEntry(name=name, version=version, data=data, source=source)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load agent %s: %s", agent_dir, e)

    return entries


def load_tool_entries(directory: Path) -> dict[str, CatalogEntry]:
    """Load tools from flat JSON files (tools don't need folder structure)."""
    entries: dict[str, CatalogEntry] = {}
    if not directory.exists():
        return entries

    for json_file in sorted(directory.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            name = data.get("name", json_file.stem)
            version = data.get("version", "1.0.0")
            entries[name] = CatalogEntry(name=name, version=version, data=data, source="core")
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load tool %s: %s", json_file, e)

    return entries


# ── Skill CRUD (for user-created skills) ─────────────────────


def render_skill_md(meta: dict, body: str) -> str:
    """Serialize a skill to SKILL.md: YAML frontmatter, then the instructions."""
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).rstrip("\n")
    return f"---\n{front}\n---\n\n{body.rstrip()}\n" if body.strip() else f"---\n{front}\n---\n"


def create_user_skill(data_dir: Path, skill_data: dict) -> dict:
    """Create a user skill on the filesystem. Called by API or LLM thread.

    Writes SKILL.md. The ``has_*`` flags are not persisted: they are derived
    from the folder at read time, so stored metadata cannot disagree with what
    is actually on disk.
    """
    name = skill_data["name"]
    skill_dir = data_dir / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "name": name,
        "version": skill_data.get("version", "1.0.0"),
        "description": skill_data.get("description", ""),
        "category": skill_data.get("category", "custom"),
        "capabilities": skill_data.get("capabilities", []),
        "input_signature": skill_data.get("input_signature", {}),
        "output_signature": skill_data.get("output_signature", {}),
        "setup_required": skill_data.get("setup_required", False),
    }
    (skill_dir / SKILL_MD_FILENAME).write_text(
        render_skill_md(meta, skill_data.get("prompt", "")), encoding="utf-8"
    )

    if skill_data.get("scripts"):
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        for filename, code in skill_data["scripts"].items():
            (scripts_dir / filename).write_text(code, encoding="utf-8")

    if skill_data.get("references"):
        refs_dir = skill_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        for filename, content in skill_data["references"].items():
            (refs_dir / filename).write_text(content, encoding="utf-8")

    return {"name": name, "status": "created", "path": str(skill_dir)}


def delete_user_skill(data_dir: Path, name: str) -> dict:
    """Delete a user skill from the filesystem."""
    skill_dir = data_dir / "skills" / name
    if not skill_dir.exists():
        return {"name": name, "status": "not_found"}
    shutil.rmtree(skill_dir)
    return {"name": name, "status": "deleted"}


def update_user_skill(data_dir: Path, name: str, updates: dict) -> dict:
    """Update a user skill's content on the filesystem.

    Reads whichever format is on disk and always writes back SKILL.md, so a
    legacy folder migrates the first time it is edited.
    """
    skill_dir = data_dir / "skills" / name
    if not skill_dir.exists():
        return {"name": name, "status": "not_found"}

    if any(k in updates for k in ("prompt", "description", "category")):
        current = read_skill_folder(skill_dir) or {"name": name}
        body = updates.get("prompt", current.get("prompt", ""))
        if not body and (skill_dir / "prompt.md").exists():
            body = (skill_dir / "prompt.md").read_text(encoding="utf-8")

        meta = {
            k: v
            for k, v in current.items()
            if not k.startswith("_")
            and k not in ("prompt", "has_prompt", "has_scripts", "has_references", "has_gotchas")
        }
        if "description" in updates:
            meta["description"] = updates["description"]
        if "category" in updates:
            meta["category"] = updates["category"]

        (skill_dir / SKILL_MD_FILENAME).write_text(render_skill_md(meta, body), encoding="utf-8")
        # The folder now speaks SKILL.md; drop the legacy pair so there is one
        # source of truth rather than two that can disagree.
        (skill_dir / LEGACY_SKILL_JSON_FILENAME).unlink(missing_ok=True)
        (skill_dir / "prompt.md").unlink(missing_ok=True)

    if "scripts" in updates:
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        for filename, code in updates["scripts"].items():
            (scripts_dir / filename).write_text(code, encoding="utf-8")

    return {"name": name, "status": "updated"}


# ── Main loader ──────────────────────────────────────────────


def default_skill_roots(
    catalog_dir: str | Path,
    user_data_dir: str | Path | None = None,
    extra_roots: Sequence[SkillRoot] | None = None,
) -> list[SkillRoot]:
    """Compose the skill roots for a normal boot, lowest authority first.

    Callers that need another source — a managed tree, a registry cache — append
    a root here rather than editing the loader.
    """
    roots = [SkillRoot(path=Path(catalog_dir) / "core" / "skills", source="core")]
    if user_data_dir:
        roots.append(SkillRoot(path=Path(user_data_dir) / "skills", source="user"))
    if extra_roots:
        roots.extend(extra_roots)
    return roots


def load_catalog(
    catalog_dir: str | Path,
    user_data_dir: str | Path | None = None,
    skill_roots: Sequence[SkillRoot] | None = None,
) -> Catalog:
    """Load the full catalog.

    Skills come from ``skill_roots`` when supplied, otherwise from
    :func:`default_skill_roots`. Later roots shadow earlier ones by name.
    Agents and tools remain single-source from the build-shipped catalog.
    """
    root = Path(catalog_dir)

    roots = (
        list(skill_roots)
        if skill_roots is not None
        else default_skill_roots(catalog_dir, user_data_dir)
    )
    skills = load_skills_from_roots(roots)
    agents = load_agent_folders(root / "core" / "agents", source="core")
    tools = load_tool_entries(root / "core" / "tools")

    catalog = Catalog(agents=agents, skills=skills, tools=tools)

    # Load core soul
    soul_path = root / "core" / "core_soul.json"
    if soul_path.exists():
        try:
            catalog.core_soul = json.loads(soul_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load core_soul.json: %s", e)

    logger.info(
        "Catalog loaded: %d agents, %d skills, %d tools, core_soul=%s",
        len(catalog.agents),
        len(catalog.skills),
        len(catalog.tools),
        "loaded" if catalog.core_soul else "missing",
    )

    return catalog
