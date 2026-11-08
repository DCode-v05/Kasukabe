"""Knowledge Store — facts, knowledge, and processes with confidence + bitemporal model.

Three entry types:
  - fact: user-stated, confidence 1.0 (e.g. "Our budget is $50k")
  - knowledge: system-inferred understanding (e.g. "User works in marketing")
  - process: learned workflows (e.g. "User prefers weekly sprint planning")

Bitemporal model:
  - valid_from / valid_until: when the fact was true in reality
  - recorded_at / superseded_at: when the system learned / invalidated it
  Nothing is ever hard-deleted. Superseded entries get superseded_at set.

Confidence decay:
  - Inferences lose 0.95x per batch if not reinforced
  - Facts (1.0, user_stated) never decay
  - Entries below 0.2 threshold get superseded

Storage: SQLite + sqlite-vec + FTS5.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.infra.sqlite_pool import SqlitePool, serialize_f32
from src.memory.embeddings import EmbeddingService, hybrid_score, keyword_score, recency_boost


DECAY_FACTOR = 0.95
REMOVAL_THRESHOLD = 0.2
DECAY_WINDOW_DAYS = 30

FRESH_FACT_DAYS = 30
AGING_FACT_DAYS = 90


def row_to_dict(row) -> dict[str, Any]:
    """Convert aiosqlite Row to dict, parsing JSON string fields."""
    d = dict(row)
    if "tags" in d and isinstance(d["tags"], str):
        try:
            d["tags"] = json.loads(d["tags"])
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
    return d


class KnowledgeStore:
    """Async SQLite-backed knowledge store with confidence scoring and bitemporal model."""

    def __init__(
        self,
        db: SqlitePool,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self.db = db
        self.embedder = embedding_service

    async def write_knowledge_fact(
        self,
        subject: str,
        content: str,
        tags: list[str] | None = None,
        valid_from: datetime | None = None,
    ) -> dict[str, Any]:
        """Write a user-stated fact at confidence 1.0."""
        now = datetime.now(timezone.utc)
        vf = (valid_from or now).isoformat()
        now_str = now.isoformat()
        entry_id = str(uuid.uuid4())
        tags_json = json.dumps(tags or [])

        embedding = None
        if self.embedder:
            embedding = await self.embedder.embed(subject + " " + content)

        row = await self.db.fetchrow(
            """
            INSERT INTO knowledge_store
                (id, subject, content, type, tags,
                 confidence, source, valid_from, recorded_at)
            VALUES (?, ?, ?, 'fact', ?, 1.0, 'user_stated', ?, ?)
            RETURNING *
            """,
            (entry_id, subject, content, tags_json, vf, now_str),
        )

        if embedding:
            await self.db.execute(
                "INSERT INTO knowledge_store_vec (id, embedding) VALUES (?, ?)",
                (entry_id, serialize_f32(embedding)),
            )

        await self.db.execute(
            "INSERT INTO knowledge_store_fts (id, subject, content) VALUES (?, ?, ?)",
            (entry_id, subject, content),
        )

        await self.db.commit()
        return row_to_dict(row)

    async def write_knowledge_inference(
        self,
        subject: str,
        content: str,
        entry_type: str = "knowledge",
        confidence: float = 0.6,
        tags: list[str] | None = None,
        valid_from: datetime | None = None,
    ) -> dict[str, Any]:
        """Write a system-inferred entry."""
        now = datetime.now(timezone.utc)
        vf = (valid_from or now).isoformat()
        now_str = now.isoformat()
        entry_id = str(uuid.uuid4())
        tags_json = json.dumps(tags or [])

        embedding = None
        if self.embedder:
            embedding = await self.embedder.embed(subject + " " + content)

        row = await self.db.fetchrow(
            """
            INSERT INTO knowledge_store
                (id, subject, content, type, tags,
                 confidence, source, valid_from, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, 'system_inferred', ?, ?)
            RETURNING *
            """,
            (entry_id, subject, content, entry_type, tags_json, round(confidence, 2), vf, now_str),
        )

        if embedding:
            await self.db.execute(
                "INSERT INTO knowledge_store_vec (id, embedding) VALUES (?, ?)",
                (entry_id, serialize_f32(embedding)),
            )

        await self.db.execute(
            "INSERT INTO knowledge_store_fts (id, subject, content) VALUES (?, ?, ?)",
            (entry_id, subject, content),
        )

        await self.db.commit()
        return row_to_dict(row)

    async def search_knowledge(
        self,
        query: str,
        limit: int = 20,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid search across active (non-superseded) knowledge entries.

        Pass query_embedding to skip the embed() call (saves an API round-trip).
        """
        if query_embedding is None and self.embedder:
            query_embedding = await self.embedder.embed(query)

        scored: list[tuple[float, dict[str, Any]]] = []

        if query_embedding and self.db.vec_available:
            qvec = serialize_f32(query_embedding)
            sem_rows = await self.db.fetch(
                """
                SELECT k.id, k.subject, k.content, k.type, k.tags,
                       k.confidence, k.source, k.usage_count, k.created_at,
                       k.last_reinforced, k.valid_from, k.valid_until,
                       k.recorded_at, k.superseded_at,
                       1 - vec_distance_cosine(v.embedding, ?) AS semantic_score
                FROM knowledge_store_vec v
                JOIN knowledge_store k ON k.id = v.id
                WHERE k.superseded_at IS NULL
                ORDER BY vec_distance_cosine(v.embedding, ?) ASC
                LIMIT ?
                """,
                (qvec, qvec, limit),
            )
            for row in sem_rows:
                searchable = row["subject"] + " " + row["content"]
                sem = row["semantic_score"] if row["semantic_score"] is not None else 0.0
                score = hybrid_score(query, searchable, precomputed_semantic=sem)
                if score > 0.0:
                    result = row_to_dict(row)
                    del result["semantic_score"]
                    result["score"] = score
                    scored.append((score, result))

        from src.memory.episodic_memory import build_fts_query, bm25_to_score

        fts_query = build_fts_query(query)
        if fts_query:
            fts_rows = await self.db.fetch(
                """
                SELECT k.id, k.subject, k.content, k.type, k.tags,
                       k.confidence, k.source, k.usage_count, k.created_at,
                       k.last_reinforced, k.valid_from, k.valid_until,
                       k.recorded_at, k.superseded_at,
                       bm25(knowledge_store_fts) AS rank
                FROM knowledge_store_fts f
                JOIN knowledge_store k ON k.id = f.id
                WHERE knowledge_store_fts MATCH ?
                AND k.superseded_at IS NULL
                LIMIT ?
                """,
                (fts_query, limit),
            )
            seen_ids = {s[1]["id"] for s in scored}
            for row in fts_rows:
                if row["id"] in seen_ids:
                    continue
                kw = bm25_to_score(row["rank"])
                if kw > 0.0:
                    result = row_to_dict(row)
                    del result["rank"]
                    result["score"] = kw
                    scored.append((kw, result))

        if not query_embedding or not self.db.vec_available:
            kw_rows = await self.db.fetch(
                """
                SELECT id, subject, content, type, tags,
                       confidence, source, usage_count, created_at,
                       last_reinforced, valid_from, valid_until,
                       recorded_at, superseded_at
                FROM knowledge_store
                WHERE superseded_at IS NULL
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            seen_ids = {s[1]["id"] for s in scored}
            for row in kw_rows:
                if row["id"] in seen_ids:
                    continue
                searchable = row["subject"] + " " + row["content"]
                kw = keyword_score(query, searchable)
                if kw > 0.0:
                    result = row_to_dict(row)
                    result["score"] = kw
                    scored.append((kw, result))

        for i, (score, result) in enumerate(scored):
            boost = recency_boost(result.get("created_at", ""))
            boosted = score * boost
            result["score"] = boosted
            scored[i] = (boosted, result)

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

    async def update_confidence(
        self,
        entry_id: str,
        new_confidence: float,
    ) -> dict[str, Any] | None:
        """Update confidence on a specific entry and set last_reinforced."""
        now_str = datetime.now(timezone.utc).isoformat()
        conf = round(new_confidence, 2)

        row = await self.db.fetchrow(
            """
            UPDATE knowledge_store
            SET confidence = ?, last_reinforced = ?
            WHERE id = ?
            RETURNING *
            """,
            (conf, now_str, entry_id),
        )
        if row:
            await self.db.commit()
        return row_to_dict(row) if row else None

    async def supersede_entry(
        self,
        entry_id: str,
        valid_until: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Supersede an entry — mark it as no longer current."""
        now_str = datetime.now(timezone.utc).isoformat()
        vu = valid_until.isoformat() if valid_until else None

        row = await self.db.fetchrow(
            """
            UPDATE knowledge_store
            SET superseded_at = ?, valid_until = ?
            WHERE id = ? AND superseded_at IS NULL
            RETURNING *
            """,
            (now_str, vu, entry_id),
        )
        if row:
            await self.db.commit()
        return row_to_dict(row) if row else None

    async def run_confidence_decay(self) -> int:
        """Run confidence decay on unreinforced inferences.

        Micro-transactions: one row at a time to avoid holding the write lock.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=DECAY_WINDOW_DAYS)).isoformat()
        now_str = datetime.now(timezone.utc).isoformat()

        rows = await self.db.fetch(
            """
            SELECT id, confidence FROM knowledge_store
            WHERE source = 'system_inferred'
              AND superseded_at IS NULL
              AND (last_reinforced IS NULL OR last_reinforced < ?)
            """,
            (cutoff,),
        )

        decay_count = 0
        for row in rows:
            new_conf = round(row["confidence"] * DECAY_FACTOR, 2)
            await self.db.execute(
                "UPDATE knowledge_store SET confidence = ? WHERE id = ?",
                (new_conf, row["id"]),
            )
            decay_count += 1

        supersede_cursor = await self.db.execute(
            """
            UPDATE knowledge_store
            SET superseded_at = ?
            WHERE source = 'system_inferred'
              AND superseded_at IS NULL
              AND confidence < ?
            """,
            (now_str, REMOVAL_THRESHOLD),
        )
        supersede_count = supersede_cursor.rowcount

        await self.db.commit()
        return decay_count + supersede_count

    async def get_active_top(self, limit: int = 5) -> list[dict[str, Any]]:
        """Get top active knowledge entries by usage and confidence."""
        rows = await self.db.fetch(
            """SELECT subject, content FROM knowledge_store
               WHERE superseded_at IS NULL
               ORDER BY usage_count DESC, confidence DESC
               LIMIT ?""",
            (limit,),
        )
        return [row_to_dict(r) for r in rows]

    async def get_fact_staleness_tier(self, entry_id: str) -> str | None:
        """Determine a fact's staleness tier for defeasible priority."""
        row = await self.db.fetchrow(
            "SELECT valid_from FROM knowledge_store WHERE id = ?",
            (entry_id,),
        )

        if row is None:
            return None

        vf = datetime.fromisoformat(row["valid_from"])
        if vf.tzinfo is None:
            vf = vf.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - vf

        if age < timedelta(days=FRESH_FACT_DAYS):
            return "fresh"
        elif age < timedelta(days=AGING_FACT_DAYS):
            return "aging"
        else:
            return "stale"

    async def increment_usage(self, entry_id: str) -> None:
        """Increment usage_count by 1 for a knowledge entry."""
        await self.db.execute(
            "UPDATE knowledge_store SET usage_count = usage_count + 1 WHERE id = ?",
            (entry_id,),
        )
        await self.db.commit()
