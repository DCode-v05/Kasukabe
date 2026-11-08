"""Platform Registry — read-only access to platform agents, skills, and tools.

Platform catalog is file-based and loaded into memory at boot via Catalog.
Platform registry methods are read-only at runtime — no writes.

User agents/skills/tools are stored in SQLite and support reads here.
Writes go through UDWrite.

Returns dicts — UD is a dumb store (spec Section 12).
"""

from __future__ import annotations

import json
from typing import Any

from src.catalog import Catalog
from src.infra.sqlite_pool import SqlitePool


class PlatformRegistry:
    """Read operations for platform catalog and user registry tables."""

    def __init__(self, db: SqlitePool, catalog: Catalog) -> None:
        self.db = db
        self.catalog = catalog


    def get_agent(self, name: str, version: str | None = None) -> dict[str, Any] | None:
        """Get a platform agent by name from the catalog.

        Version is ignored — catalog holds the single current version per name.
        """
        return self.catalog.get_agent(name)

    def get_latest_agent(self, name: str) -> dict[str, Any] | None:
        """Get the latest (only) version of a platform agent."""
        return self.catalog.get_agent(name)

    def list_agents(
        self, capabilities: list[str] | None = None, agent_type: str | None = None
    ) -> list[dict[str, Any]]:
        """List platform agents, optionally filtered by capabilities or type.

        Filtering is done in Python since catalog data is in-memory.
        """
        agents = self.catalog.all_agents()
        results: list[dict[str, Any]] = []

        for agent in agents:
            if capabilities:
                agent_caps = agent.get("capabilities", [])
                if not all(c in agent_caps for c in capabilities):
                    continue
            if agent_type:
                if agent.get("type", "") != agent_type:
                    continue
            results.append(agent)

        results.sort(key=lambda a: a.get("name", ""))
        return results


    def get_skill(self, name: str, version: str | None = None) -> dict[str, Any] | None:
        """Get a platform skill by name from the catalog."""
        return self.catalog.get_skill(name)

    def get_latest_skill(self, name: str) -> dict[str, Any] | None:
        """Get the latest (only) version of a platform skill."""
        return self.catalog.get_skill(name)


    def get_tool(self, name: str, version: str | None = None) -> dict[str, Any] | None:
        """Get a platform tool by name from the catalog."""
        return self.catalog.get_tool(name)

    def get_latest_tool(self, name: str) -> dict[str, Any] | None:
        """Get the latest (only) version of a platform tool."""
        return self.catalog.get_tool(name)

    def list_tools(self) -> list[dict[str, Any]]:
        """List all platform tools from the catalog."""
        tools = self.catalog.all_tools()
        tools.sort(key=lambda t: t.get("name", ""))
        return tools


    async def get_user_agent(self, name: str) -> dict[str, Any] | None:
        """Get a user agent record from SQLite."""
        row = await self.db.fetchrow(
            "SELECT * FROM user_agents WHERE name = ?",
            (name,),
        )
        if row is None:
            return None
        result = dict(row)
        for col in (
            "capabilities",
            "input_signature",
            "output_signature",
            "declared_deps",
            "custom_config",
            "learned_state",
        ):
            if col in result and isinstance(result[col], str):
                result[col] = json.loads(result[col])
        return result

    async def list_user_agents(
        self,
        capabilities: list[str] | None = None,
        agent_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List user agents from SQLite, optionally filtered."""
        query = "SELECT * FROM user_agents WHERE 1=1"
        params: list[Any] = []

        if capabilities:
            for cap in capabilities:
                query += " AND EXISTS (SELECT 1 FROM json_each(capabilities) WHERE value = ?)"
                params.append(cap)

        if agent_type:
            query += " AND type = ?"
            params.append(agent_type)

        query += " ORDER BY name"

        rows = await self.db.fetch(query, tuple(params))
        results: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            for col in (
                "capabilities",
                "input_signature",
                "output_signature",
                "declared_deps",
                "custom_config",
                "learned_state",
            ):
                if col in d and isinstance(d[col], str):
                    d[col] = json.loads(d[col])
            results.append(d)
        return results


    async def get_user_skill(self, name: str) -> dict[str, Any] | None:
        """Get a user skill record from SQLite."""
        row = await self.db.fetchrow(
            "SELECT * FROM user_skills WHERE name = ?",
            (name,),
        )
        if row is None:
            return None
        result = dict(row)
        for col in (
            "capabilities",
            "input_signature",
            "output_signature",
            "config",
            "learned_state",
        ):
            if col in result and isinstance(result[col], str):
                result[col] = json.loads(result[col])
        return result


    async def get_user_tool(self, name: str) -> dict[str, Any] | None:
        """Get a user tool config record from SQLite."""
        row = await self.db.fetchrow(
            "SELECT * FROM user_tools WHERE platform_tool_name = ?",
            (name,),
        )
        if row is None:
            return None
        result = dict(row)
        if "custom_config" in result and isinstance(result["custom_config"], str):
            result["custom_config"] = json.loads(result["custom_config"])
        return result
