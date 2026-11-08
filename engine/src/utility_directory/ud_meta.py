"""UD-Meta — lightweight metadata interface for capability context.

Returns names, versions, descriptions, capabilities, and I/O signatures —
enough for context enrichment without loading full definitions. Consumed by
search.py for capability context building. Returns both platform and
user-created items for the requesting user. UD spec Section 10.

Platform data comes from the in-memory Catalog (file-based, read-only).
User data comes from SQLite.
"""

from __future__ import annotations

import json
from typing import Any

from src.catalog import Catalog
from src.infra.sqlite_pool import SqlitePool

VERSION_USER = "user"


class UDMeta:
    """Metadata queries for charter planning."""

    def __init__(self, db: SqlitePool, catalog: Catalog) -> None:
        self.db = db
        self.catalog = catalog

    async def get_agents_meta(
        self,
        capabilities: list[str] | None = None,
        agent_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return lightweight agent metadata — platform + user-created.

        Fields: name, version, type, capabilities, input_signature,
        output_signature, description, source ("platform" or "user_created").
        """
        results: list[dict[str, Any]] = []

        # Platform agents from catalog (in-memory, Python-side filtering)
        for agent in self.catalog.all_agents():
            if capabilities:
                agent_caps = agent.get("capabilities", [])
                if not all(c in agent_caps for c in capabilities):
                    continue
            if agent_type:
                if agent.get("type", "") != agent_type:
                    continue
            results.append(
                {
                    "name": agent.get("name", ""),
                    "version": agent.get("version", ""),
                    "type": agent.get("type", ""),
                    "capabilities": agent.get("capabilities", []),
                    "input_signature": agent.get("input_signature", {}),
                    "output_signature": agent.get("output_signature", {}),
                    "description": agent.get("description", ""),
                    "source": "platform",
                }
            )

        # User-created agents from SQLite
        u_query = """
            SELECT name, type, capabilities, input_signature,
                   output_signature, description
            FROM user_agents
            WHERE source = 'user_created'
        """
        u_params: list[Any] = []

        if capabilities:
            for cap in capabilities:
                u_query += " AND EXISTS (SELECT 1 FROM json_each(capabilities) WHERE value = ?)"
                u_params.append(cap)

        if agent_type:
            u_query += " AND type = ?"
            u_params.append(agent_type)

        u_query += " ORDER BY name"

        rows = await self.db.fetch(u_query, tuple(u_params))
        for r in rows:
            d = dict(r)
            for col in ("capabilities", "input_signature", "output_signature"):
                if col in d and isinstance(d[col], str):
                    d[col] = json.loads(d[col])
            results.append(
                {
                    "name": d.get("name", ""),
                    "version": VERSION_USER,
                    "type": d.get("type", ""),
                    "capabilities": d.get("capabilities", []),
                    "input_signature": d.get("input_signature", {}),
                    "output_signature": d.get("output_signature", {}),
                    "description": d.get("description", ""),
                    "source": "user_created",
                }
            )

        return results

    async def get_skills_meta(
        self,
        capabilities: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return lightweight skill metadata for every skill in the catalog.

        The catalog spans all skill roots, user skills included, so it is read
        once. SQLite is not consulted: it holds per-skill state, not definitions,
        and reading both produced one entry per skill per store.

        Fields: name, version, capabilities, input_signature, output_signature,
        description, skill_type, source.
        """
        results: list[dict[str, Any]] = []

        for name, entry in self.catalog.skills.items():
            skill = entry.data
            if capabilities:
                skill_caps = skill.get("capabilities", [])
                if not all(c in skill_caps for c in capabilities):
                    continue

            # Determine skill_type from nullable fields
            source_code = skill.get("source_code")
            prompt_template = skill.get("prompt_template") or skill.get("prompt")
            if source_code and prompt_template:
                skill_type = "hybrid"
            elif source_code:
                skill_type = "code"
            else:
                skill_type = "instruction"

            results.append(
                {
                    "name": name,
                    "version": VERSION_USER if entry.source != "core" else entry.version,
                    "capabilities": skill.get("capabilities", []),
                    "input_signature": skill.get("input_signature", {}),
                    "output_signature": skill.get("output_signature", {}),
                    "description": skill.get("description", ""),
                    "skill_type": skill_type,
                    "source": "platform" if entry.source == "core" else entry.source,
                }
            )

        return results

    async def get_tools_meta(self, capabilities: list[str] | None = None) -> list[dict[str, Any]]:
        """Return lightweight tool metadata.

        Tools are platform-only — read from catalog, no user-created tools.
        Fields: name, version, interface_summary, description, capabilities.
        """
        results: list[dict[str, Any]] = []

        for tool in self.catalog.all_tools():
            if capabilities:
                tool_caps = tool.get("capabilities", [])
                if not all(c in tool_caps for c in capabilities):
                    continue

            # Build a compact interface summary from the JSON Schema
            iface = tool.get("interface", {})
            props = iface.get("properties", {})
            summary = {
                "parameters": list(props.keys()),
                "required": iface.get("required", []),
            }
            results.append(
                {
                    "name": tool.get("name", ""),
                    "version": tool.get("version", ""),
                    "interface_summary": summary,
                    "description": tool.get("description", ""),
                    "capabilities": tool.get("capabilities", []),
                }
            )

        return results
