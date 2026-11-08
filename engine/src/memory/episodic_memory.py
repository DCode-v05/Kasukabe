"""Episodic Memory — KDV episodes with tiered archival and hybrid search.

Episodes store what happened: conversations, task executions, user interactions.
Each episode has a Key (retrieval anchor), Description (relevance matching),
and Value (raw detail, fetched on demand after K+D match).

Two tiers:
  - 'episode' (< 30 days): Value uncompressed, instant access.
  - 'archive' (>= 30 days): Value gzip-compressed, decompressed on demand.
Both tiers remain searchable via Key + Description.

Hybrid search: 30% keyword (word-overlap) + 70% semantic (cosine similarity).

Storage: SQLite (regular table) + sqlite-vec (vector search) + FTS5 (keyword search).
Three inserts per episode: episodes + episodes_vec + episodes_fts.
"""

from __future__ import annotations

import gzip
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.infra.sqlite_pool import SqlitePool, serialize_f32
from src.memory.embeddings import EmbeddingService, hybrid_score, keyword_score, recency_boost


ARCHIVE_THRESHOLD_DAYS = 30


def row_to_dict(row) -> dict[str, Any]:
    """Convert aiosqlite Row to dict."""
    return dict(row)


class EpisodicMemory:
    """Async SQLite-backed episodic memory with KDV format and hybrid search."""

    def __init__(
        self,
        db: SqlitePool,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self.db = db
        self.embedder = embedding_service

    async def create_episode(
        self,
        key: str,
        description: str,
        value: str,
    ) -> dict[str, Any]:
        """Create a new KDV episode.

        Three inserts: regular table + vector table + FTS table.
        """
        episode_id = str(uuid.uuid4())
        embedding = None
        if self.embedder:
            embedding = await self.embedder.embed(key + " " + description)

        row = await self.db.fetchrow(
            """
            INSERT INTO episodes (id, key, description, value)
            VALUES (?, ?, ?, ?)
            RETURNING id, key, description, tier, created_at
            """,
            (episode_id, key, description, value),
        )

        if embedding:
            await self.db.execute(
                "INSERT INTO episodes_vec (id, embedding) VALUES (?, ?)",
                (episode_id, serialize_f32(embedding)),
            )

        await self.db.execute(
            "INSERT INTO episodes_fts (id, key, description) VALUES (?, ?, ?)",
            (episode_id, key, description),
        )

        await self.db.commit()
        return row_to_dict(row)

    async def search_episodes(
        self,
        query: str,
        limit: int = 20,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid search across episodes and archives by Key + Description.

        sqlite-vec for semantic candidates (70%), blended with keyword
        score (30%). Falls back to keyword-only for rows without embeddings.

        Value is NOT included in results — use get_episode_value() after.
        Pass query_embedding to skip the embed() call (saves an API round-trip).
        """
        if query_embedding is None and self.embedder:
            query_embedding = await self.embedder.embed(query)

        scored: list[tuple[float, dict[str, Any]]] = []

        if query_embedding and self.db.vec_available:
            qvec = serialize_f32(query_embedding)
            sem_rows = await self.db.fetch(
                """
                SELECT e.id, e.key, e.description, e.tier, e.created_at,
                       1 - vec_distance_cosine(v.embedding, ?) AS semantic_score
                FROM episodes_vec v
                JOIN episodes e ON e.id = v.id
                ORDER BY vec_distance_cosine(v.embedding, ?) ASC
                LIMIT ?
                """,
                (qvec, qvec, limit),
            )
            for row in sem_rows:
                searchable = row["key"] + " " + row["description"]
                sem = row["semantic_score"] if row["semantic_score"] is not None else 0.0
                score = hybrid_score(query, searchable, precomputed_semantic=sem)
                if score > 0.0:
                    result = row_to_dict(row)
                    del result["semantic_score"]
                    result["score"] = score
                    scored.append((score, result))

        fts_query = build_fts_query(query)
        if fts_query:
            fts_rows = await self.db.fetch(
                """
                SELECT e.id, e.key, e.description, e.tier, e.created_at,
                       bm25(episodes_fts) AS rank
                FROM episodes_fts f
                JOIN episodes e ON e.id = f.id
                WHERE episodes_fts MATCH ?
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
                SELECT id, key, description, tier, created_at
                FROM episodes
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            seen_ids = {s[1]["id"] for s in scored}
            for row in kw_rows:
                if row["id"] in seen_ids:
                    continue
                searchable = row["key"] + " " + row["description"]
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

    async def get_episode_value(self, episode_id: str) -> str | None:
        """Retrieve the full Value content for a specific episode."""
        row = await self.db.fetchrow(
            "SELECT tier, value, value_compressed FROM episodes WHERE id = ?",
            (episode_id,),
        )

        if row is None:
            return None

        if row["tier"] == "archive" and row["value_compressed"] is not None:
            return gzip.decompress(row["value_compressed"]).decode("utf-8")

        return row["value"]

    async def get_recent(self, limit: int = 5) -> list[dict[str, Any]]:
        """Get most recent episodes by creation time (key + description only)."""
        rows = await self.db.fetch(
            "SELECT key, description, created_at FROM episodes ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [row_to_dict(r) for r in rows]

    async def archive_old_episodes(self) -> int:
        """Move episodes older than 30 days to archive tier.

        Compresses value via gzip, stores in value_compressed, clears value,
        and sets tier to 'archive'.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=ARCHIVE_THRESHOLD_DAYS)).isoformat()

        rows = await self.db.fetch(
            """
            SELECT id, value FROM episodes
            WHERE tier = 'episode' AND created_at < ?
            """,
            (cutoff,),
        )

        if not rows:
            return 0

        for row in rows:
            compressed = gzip.compress(row["value"].encode("utf-8"))
            await self.db.execute(
                """
                UPDATE episodes
                SET value_compressed = ?, value = NULL, tier = 'archive'
                WHERE id = ?
                """,
                (compressed, row["id"]),
            )

        await self.db.commit()
        return len(rows)


def build_fts_query(raw: str) -> str | None:
    """Build an FTS5 MATCH query from a raw search string.

    Tokenizes input, quotes each token, joins with AND.
    FTS5 returns negative BM25 scores (lower = more relevant).
    """
    tokens = re.findall(r"[\w]+", raw)
    if not tokens:
        return None
    quoted = [f'"{t}"' for t in tokens]
    return " AND ".join(quoted)


def bm25_to_score(rank: float) -> float:
    """Normalize FTS5 BM25 rank to a 0-1 score.

    FTS5 returns negative BM25 values (more negative = more relevant).
    Convert to positive 0-1 range.
    """
    if not isinstance(rank, (int, float)) or rank >= 0:
        return 0.01
    relevance = -rank
    return relevance / (1 + relevance)
