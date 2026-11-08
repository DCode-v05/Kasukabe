"""Soul — Core Soul (immutable identity) + Evolved Soul (per-user personality).

Core Soul: loaded from catalog volume at boot, cached in-memory.
Same for all users. Defines the engine's fundamental character.

Evolved Soul: per-user, per-trait adaptive personality in SQLite. Uses an epigenetic
model where traits start as 'signal' (accumulating evidence) and become 'expressed'
when confidence >= 0.7 AND evidence_count >= 3.

Only expressed traits are visible to callers (consciousness, search, context enrichment).
Signals are LC's internal state.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.infra.sqlite_pool import SqlitePool


EXPRESSION_CONFIDENCE = 0.70
EXPRESSION_EVIDENCE_COUNT = 3


def row_to_dict(row) -> dict[str, Any]:
    return dict(row)


CORE_SOUL_CACHE: dict[str, Any] | None = None


def set_core_soul_cache(document: dict[str, Any]) -> None:
    """Set the Core Soul cache from catalog loader at boot."""
    global CORE_SOUL_CACHE
    CORE_SOUL_CACHE = document


def clear_core_soul_cache() -> None:
    """Reset the module-level Core Soul cache. For testing only."""
    global CORE_SOUL_CACHE
    CORE_SOUL_CACHE = None


class Soul:
    """Async SQLite-backed Soul with catalog-loaded Core Soul.

    Core Soul is loaded from the catalog volume at boot via set_core_soul_cache().
    Evolved Soul reads/writes to per-user SQLite.
    """

    def __init__(self, db: SqlitePool) -> None:
        self.db = db

    async def get_core_soul(self) -> dict[str, Any] | None:
        """Get the immutable Core Soul document from cache.

        Loaded from catalog at boot via set_core_soul_cache().
        Returns None if catalog didn't have core_soul.json.
        """
        return CORE_SOUL_CACHE

    async def get_evolved_soul(self) -> list[dict[str, Any]]:
        """Get ONLY expressed traits."""
        rows = await self.db.fetch(
            """
            SELECT id, trait, value, status, confidence,
                   evidence_count, last_reinforced, created_at
            FROM evolved_soul
            WHERE status = 'expressed'
            ORDER BY trait
            """,
        )
        return [row_to_dict(row) for row in rows]

    async def get_evolved_soul_full(self) -> list[dict[str, Any]]:
        """Get ALL traits — both signals and expressed. Used by LC."""
        rows = await self.db.fetch(
            """
            SELECT id, trait, value, status, confidence,
                   evidence_count, last_reinforced, created_at
            FROM evolved_soul
            ORDER BY trait
            """,
        )
        return [row_to_dict(row) for row in rows]

    async def update_evolved_soul(
        self,
        trait: str,
        value: str,
        confidence: float,
        evidence_count: int,
    ) -> dict[str, Any]:
        """Upsert an Evolved Soul trait.

        Auto-computes status: 'expressed' if confidence >= 0.7 AND evidence_count >= 3,
        'signal' otherwise.
        """
        status = (
            "expressed"
            if (confidence >= EXPRESSION_CONFIDENCE and evidence_count >= EXPRESSION_EVIDENCE_COUNT)
            else "signal"
        )

        trait_id = str(uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()
        conf = round(confidence, 2)

        row = await self.db.fetchrow(
            """
            INSERT INTO evolved_soul
                (id, trait, value, status, confidence,
                 evidence_count, last_reinforced)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (trait)
            DO UPDATE SET
                value = ?,
                status = ?,
                confidence = ?,
                evidence_count = ?,
                last_reinforced = ?
            RETURNING id, trait, value, status, confidence,
                      evidence_count, last_reinforced, created_at
            """,
            (
                trait_id,
                trait,
                value,
                status,
                conf,
                evidence_count,
                now_str,
                value,
                status,
                conf,
                evidence_count,
                now_str,
            ),
        )
        await self.db.commit()
        return row_to_dict(row)
