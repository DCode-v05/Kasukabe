"""UD-Write — write interface for the Learning Centre.

LC is the sole writer to user state tables. Write behaviour depends on
the item type:
- Platform agents/skills → state updates only (definitions are immutable)
- User-created agents/skills → full writes, overwrites in place
- Tools → user config updates only (tools are platform-only)

UD spec Section 10.

JSONB columns are stored as TEXT in SQLite and serialized via json.dumps().
"""

from __future__ import annotations

import json
from typing import Any

from src.infra.sqlite_pool import SqlitePool


class UDWrite:
    """Write operations for user state — LC only."""

    def __init__(self, db: SqlitePool) -> None:
        self.db = db

    # --- Agents ---

    async def write_user_agent(self, agent_data: dict[str, Any]) -> dict[str, Any]:
        """Write a full user agent record — upsert by (name).

        For user_created agents: full definition (type, capabilities, etc.).
        For platform-derived: creates the initial user row after first use.
        """
        row = await self.db.fetchrow(
            """INSERT INTO user_agents
               (name, source, platform_agent_name, platform_agent_version,
                learned_state, custom_config, type,
                capabilities, input_signature, output_signature, declared_deps,
                description)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (name)
                   DO UPDATE SET
                       source = EXCLUDED.source,
                       platform_agent_name = EXCLUDED.platform_agent_name,
                       platform_agent_version = EXCLUDED.platform_agent_version,
                       learned_state = COALESCE(EXCLUDED.learned_state, user_agents.learned_state),
                       custom_config = EXCLUDED.custom_config,
                       type = EXCLUDED.type,
                       capabilities = EXCLUDED.capabilities,
                       input_signature = EXCLUDED.input_signature,
                       output_signature = EXCLUDED.output_signature,
                       declared_deps = EXCLUDED.declared_deps,
                       description = EXCLUDED.description,
                       updated_at = datetime('now')
               RETURNING *""",
            (
                agent_data["name"],
                agent_data.get("source", "platform"),
                agent_data.get("platform_agent_name"),
                agent_data.get("platform_agent_version"),
                json.dumps(agent_data["learned_state"])
                if agent_data.get("learned_state") is not None
                else None,
                json.dumps(agent_data["custom_config"])
                if agent_data.get("custom_config") is not None
                else None,
                agent_data.get("type"),
                json.dumps(agent_data["capabilities"])
                if agent_data.get("capabilities") is not None
                else None,
                json.dumps(agent_data["input_signature"])
                if agent_data.get("input_signature") is not None
                else None,
                json.dumps(agent_data["output_signature"])
                if agent_data.get("output_signature") is not None
                else None,
                json.dumps(agent_data["declared_deps"])
                if agent_data.get("declared_deps") is not None
                else None,
                agent_data.get("description"),
            ),
        )
        await self.db.commit()
        assert row is not None  # INSERT ... RETURNING always yields a row
        result = dict(row)
        for col in (
            "learned_state",
            "custom_config",
            "capabilities",
            "input_signature",
            "output_signature",
            "declared_deps",
        ):
            if col in result and isinstance(result[col], str):
                result[col] = json.loads(result[col])
        return result

    async def update_agent_state(self, agent_name: str, learned_state: dict[str, Any]) -> bool:
        """Partial update — only the learned-state overlay for a platform-derived agent.

        Returns True if a row was updated, False if no row existed.
        """
        cursor = await self.db.execute(
            """UPDATE user_agents
               SET learned_state = ?, updated_at = datetime('now')
               WHERE name = ?""",
            (json.dumps(learned_state), agent_name),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def update_agent_version(self, agent_name: str, new_version: str) -> bool:
        """LC migrates user's platform agent reference to a new version.

        Returns True if updated, False if no row existed.
        """
        cursor = await self.db.execute(
            """UPDATE user_agents
               SET platform_agent_version = ?, updated_at = datetime('now')
               WHERE name = ? AND source = 'platform'""",
            (new_version, agent_name),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    # --- Skills ---

    async def write_user_skill(self, skill_data: dict[str, Any]) -> dict[str, Any]:
        """Write a user skill's STATE row — upsert by (name).

        State only: the learned overlay plus lifecycle counters. The definition
        (description, capabilities, signatures, prompt, source code) belongs to
        the skill folder on disk and is never written here — definition keys in
        *skill_data* are ignored rather than persisted, so a second competing
        source of truth cannot reappear through this path.
        """
        row = await self.db.fetchrow(
            """INSERT INTO user_skills
               (name, source, platform_skill_name, platform_skill_version,
                learned_state, status, confidence, occurrence_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (name)
                   DO UPDATE SET
                       source = EXCLUDED.source,
                       platform_skill_name = EXCLUDED.platform_skill_name,
                       platform_skill_version = EXCLUDED.platform_skill_version,
                       learned_state = COALESCE(EXCLUDED.learned_state, user_skills.learned_state),
                       occurrence_count = user_skills.occurrence_count + 1,
                       confidence = MAX(EXCLUDED.confidence, user_skills.confidence),
                       status = CASE
                           WHEN user_skills.occurrence_count + 1 >= 3
                                AND MAX(EXCLUDED.confidence, user_skills.confidence) >= 0.8
                           THEN 'active'
                           ELSE user_skills.status
                       END,
                       updated_at = datetime('now')
               RETURNING *""",
            (
                skill_data["name"],
                skill_data.get("source", "user_created"),
                skill_data.get("platform_skill_name"),
                skill_data.get("platform_skill_version"),
                json.dumps(skill_data["learned_state"])
                if skill_data.get("learned_state") is not None
                else None,
                skill_data.get("status", "tentative"),
                skill_data.get("confidence", 0.5),
                skill_data.get("occurrence_count", 1),
            ),
        )
        await self.db.commit()
        assert row is not None  # INSERT ... RETURNING always yields a row
        result = dict(row)
        if isinstance(result.get("learned_state"), str):
            result["learned_state"] = json.loads(result["learned_state"])
        return result
