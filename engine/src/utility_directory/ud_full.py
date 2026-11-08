"""UD-Full — complete definition interface for thread hydration.

Returns full definitions for execution: source code, learned state, configs,
and user state merged together. Threads call these during hydration to
resolve charter references into executable components. UD spec Section 10.

Platform data comes from the in-memory Catalog (file-based, read-only).
User data comes from SQLite.

First-use bootstrapping: if no user_agents/user_skills row exists for a user,
the thread gets platform defaults. LC creates the user row after first execution.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.catalog import Catalog
from src.infra.sqlite_pool import SqlitePool
from src.utility_directory.ud_meta import VERSION_USER

logger = logging.getLogger(__name__)


def parse_json_cols(row_dict: dict, cols: tuple[str, ...]) -> dict:
    """Deserialize JSON TEXT columns in a SQLite row dict."""
    for col in cols:
        if col in row_dict and isinstance(row_dict[col], str):
            row_dict[col] = json.loads(row_dict[col])
    return row_dict


class UDFull:
    """Full definition queries for thread hydration."""

    def __init__(self, db: SqlitePool, catalog: Catalog) -> None:
        self.db = db
        self.catalog = catalog

    async def get_agent(self, name: str, version: str) -> dict[str, Any] | None:
        """Get full agent definition: platform source merged with user state.

        Merging: start with platform record, overlay user's learned_state
        and custom_config if a user_agents row exists.

        First-use bootstrapping: no user_agents row → return platform defaults.
        """
        if version == VERSION_USER:
            return await self.get_agent_user_created(name)

        platform = self.catalog.get_agent(name)
        if platform is None:
            return None

        result = dict(platform)

        user_state = await self.db.fetchrow(
            """SELECT learned_state, custom_config
               FROM user_agents
               WHERE platform_agent_name = ?""",
            (name,),
        )

        if user_state:
            us = dict(user_state)
            learned = us.get("learned_state")
            if learned is not None:
                result["learned_state"] = (
                    json.loads(learned) if isinstance(learned, str) else learned
                )
            else:
                result["learned_state"] = None

            cfg = us.get("custom_config")
            if cfg is not None:
                result["custom_config"] = json.loads(cfg) if isinstance(cfg, str) else cfg
            else:
                result["custom_config"] = None
        else:
            result["learned_state"] = None
            result["custom_config"] = None

        return result

    async def get_agent_user_created(self, name: str) -> dict[str, Any] | None:
        """Get a user-created agent (not platform-derived).

        User-created agents carry their full definition in user_agents.
        """
        row = await self.db.fetchrow(
            """SELECT * FROM user_agents
               WHERE name = ? AND source = 'user_created'""",
            (name,),
        )
        if row is None:
            return None
        return parse_json_cols(
            dict(row),
            (
                "learned_state",
                "custom_config",
                "capabilities",
                "input_signature",
                "output_signature",
                "declared_deps",
            ),
        )

    async def get_skill(self, name: str, version: str) -> dict[str, Any] | None:
        """Get full skill definition: platform source merged with user state.

        First-use bootstrapping applies here too. Only returns active user skills.
        """
        if version == VERSION_USER:
            return await self.get_skill_user_created(name)

        platform = self.catalog.get_skill(name)
        if platform is None:
            return None

        result = dict(platform)

        user_state = await self.db.fetchrow(
            """SELECT learned_state
               FROM user_skills
               WHERE (platform_skill_name = ? OR name = ?)
                     AND status = 'active'""",
            (name, name),
        )

        learned = dict(user_state).get("learned_state") if user_state else None
        result["learned_state"] = (
            (json.loads(learned) if isinstance(learned, str) else learned)
            if learned is not None
            else None
        )

        return result

    async def get_skill_user_created(self, name: str) -> dict[str, Any] | None:
        """Get a user-created skill's full definition from the catalog.

        The folder is the definition; SQLite supplies the learned overlay. A
        skill with no folder does not exist, however many state rows point at it.
        """
        definition = self.catalog.get_skill(name)
        if definition is None:
            return None

        result = dict(definition)
        row = await self.db.fetchrow(
            "SELECT learned_state FROM user_skills WHERE name = ?",
            (name,),
        )
        learned = dict(row).get("learned_state") if row else None
        result["learned_state"] = (
            (json.loads(learned) if isinstance(learned, str) else learned)
            if learned is not None
            else None
        )
        return result

    async def get_tool(self, name: str, version: str) -> dict[str, Any] | None:
        """Get full tool definition: interface + user config.

        Tools are platform-only. User table holds config overrides.
        """
        platform = self.catalog.get_tool(name)
        if platform is None:
            return None

        result = dict(platform)

        user_config = await self.db.fetchrow(
            """SELECT custom_config FROM user_tools
               WHERE platform_tool_name = ?""",
            (name,),
        )

        if user_config:
            uc = dict(user_config)
            cfg = uc.get("custom_config")
            if cfg is not None:
                result["custom_config"] = json.loads(cfg) if isinstance(cfg, str) else cfg
            else:
                result["custom_config"] = None
        else:
            result["custom_config"] = None

        return result

    async def get_all_latest_tools(self) -> list[dict[str, Any]]:
        """Get all platform tools with full definitions for hydration.

        Returns every tool from the catalog, each merged with the user's
        custom_config if one exists. Used by hydrate_tools() at boot to
        dynamically resolve handlers via the implementation column.
        """
        user_rows = await self.db.fetch(
            """SELECT platform_tool_name, custom_config
               FROM user_tools""",
        )
        user_configs: dict[str, Any] = {}
        for r in user_rows:
            d = dict(r)
            cfg = d.get("custom_config")
            if cfg is not None and isinstance(cfg, str):
                cfg = json.loads(cfg)
            user_configs[d["platform_tool_name"]] = cfg

        results: list[dict[str, Any]] = []
        for tool in self.catalog.all_tools():
            result = dict(tool)
            tool_name = result.get("name", "")
            result["custom_config"] = user_configs.get(tool_name)
            results.append(result)

        results.sort(key=lambda t: t.get("name", ""))
        return results
