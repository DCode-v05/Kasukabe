"""Social Graph — person nodes, unbounded attributes, relationship edges.

Person nodes represent people in the user's world. The user themselves is the
primary person node (is_primary=1). Attributes grow unbounded as key-value
pairs. Edges connect any two people with three perspective fields
(user_perspective, engine_inference, observed_dynamics).

Search uses 50/50 keyword/semantic blend because name matching is equally
important as contextual similarity.

Storage: SQLite + sqlite-vec + FTS5.
- social_graph_nodes + social_graph_nodes_vec + social_graph_nodes_fts
- social_graph_node_attributes + social_graph_node_attributes_vec
- social_graph_edges + social_graph_edges_vec
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from src.infra.sqlite_pool import SqlitePool, serialize_f32
from src.memory.embeddings import EmbeddingService, hybrid_score, keyword_score


def row_to_dict(row) -> dict[str, Any]:
    """Convert aiosqlite Row to dict, parsing JSON fields."""
    d = dict(row)
    if "contexts" in d and isinstance(d["contexts"], str):
        try:
            d["contexts"] = json.loads(d["contexts"])
        except (json.JSONDecodeError, TypeError):
            d["contexts"] = []
    if "is_primary" in d:
        d["is_primary"] = bool(d["is_primary"])
    return d


class SocialGraph:
    """Async SQLite-backed social graph with hybrid search."""

    def __init__(
        self,
        db: SqlitePool,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self.db = db
        self.embedder = embedding_service

    async def get_person_by_id(self, person_id: str) -> dict[str, Any] | None:
        node = await self.db.fetchrow(
            "SELECT id, name, is_primary, created_at, updated_at FROM social_graph_nodes WHERE id = ?",
            (person_id,),
        )
        if node is None:
            return None
        attrs = await self.db.fetch(
            "SELECT attribute_key, attribute_value FROM social_graph_node_attributes WHERE node_id = ?",
            (person_id,),
        )
        result = row_to_dict(node)
        result["attributes"] = {r["attribute_key"]: r["attribute_value"] for r in attrs}
        return result

    async def find_or_create_person(self, name: str) -> dict[str, Any]:
        existing = await self.db.fetchrow(
            "SELECT id FROM social_graph_nodes WHERE LOWER(name) = LOWER(?)",
            (name,),
        )
        if existing:
            person = await self.get_person_by_id(existing["id"])
            if person is not None:
                return person

        count = await self.db.fetchval(
            "SELECT COUNT(*) FROM social_graph_nodes",
        )
        if count >= 5:
            results = await self.search_social_graph(name, limit=1)
            if (
                results
                and results[0].get("result_type") == "node"
                and results[0].get("score", 0) >= 0.85
            ):
                person = await self.get_person_by_id(results[0]["id"])
                if person is not None:
                    return person

        return await self.create_person(name)

    async def create_person(
        self,
        name: str,
        is_primary: bool = False,
    ) -> dict[str, Any]:
        person_id = str(uuid.uuid4())
        embedding = None
        if self.embedder:
            embedding = await self.embedder.embed(name)

        row = await self.db.fetchrow(
            """
            INSERT INTO social_graph_nodes (id, name, is_primary)
            VALUES (?, ?, ?)
            RETURNING id, name, is_primary, created_at, updated_at
            """,
            (person_id, name, 1 if is_primary else 0),
        )

        if embedding:
            await self.db.execute(
                "INSERT INTO social_graph_nodes_vec (id, embedding) VALUES (?, ?)",
                (person_id, serialize_f32(embedding)),
            )

        await self.db.execute(
            "INSERT INTO social_graph_nodes_fts (id, name) VALUES (?, ?)",
            (person_id, name),
        )

        await self.db.commit()
        result = row_to_dict(row)
        result["attributes"] = {}
        return result

    async def get_primary_person(self) -> dict[str, Any] | None:
        node = await self.db.fetchrow(
            "SELECT id, name, is_primary, created_at, updated_at FROM social_graph_nodes WHERE is_primary = 1",
        )
        if node is None:
            return None

        attrs = await self.db.fetch(
            "SELECT attribute_key, attribute_value FROM social_graph_node_attributes WHERE node_id = ?",
            (node["id"],),
        )

        result = row_to_dict(node)
        result["attributes"] = {r["attribute_key"]: r["attribute_value"] for r in attrs}
        return result

    async def search_social_graph(
        self,
        query: str,
        limit: int = 20,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid search across person nodes and relationship edges. 50/50 keyword/semantic.

        Pass query_embedding to skip the embed() call (saves an API round-trip).
        """
        if query_embedding is None and self.embedder:
            query_embedding = await self.embedder.embed(query)

        scored: list[tuple[float, dict[str, Any]]] = []

        if query_embedding and self.db.vec_available:
            sem_nodes = await self.db.fetch(
                """
                SELECT n.id, n.name, n.is_primary, n.created_at, n.updated_at,
                       1 - vec_distance_cosine(v.embedding, ?) AS semantic_score
                FROM social_graph_nodes_vec v
                JOIN social_graph_nodes n ON n.id = v.id
                ORDER BY vec_distance_cosine(v.embedding, ?) ASC
                LIMIT ?
                """,
                (serialize_f32(query_embedding), serialize_f32(query_embedding), limit),
            )
        else:
            sem_nodes = []

        if not query_embedding or not self.db.vec_available:
            kw_nodes = await self.db.fetch(
                "SELECT id, name, is_primary, created_at, updated_at FROM social_graph_nodes",
            )
        else:
            kw_nodes = await self.db.fetch(
                """
                SELECT id, name, is_primary, created_at, updated_at
                FROM social_graph_nodes
                WHERE id NOT IN (SELECT id FROM social_graph_nodes_vec)
                """,
            )

        all_node_ids = list({r["id"] for r in sem_nodes} | {r["id"] for r in kw_nodes})
        attr_map: dict[str, dict[str, str]] = {}
        if all_node_ids:
            placeholders = ",".join("?" * len(all_node_ids))
            attrs_rows = await self.db.fetch(
                f"SELECT node_id, attribute_key, attribute_value FROM social_graph_node_attributes WHERE node_id IN ({placeholders})",
                tuple(all_node_ids),
            )
            for ar in attrs_rows:
                nid = ar["node_id"]
                if nid not in attr_map:
                    attr_map[nid] = {}
                attr_map[nid][ar["attribute_key"]] = ar["attribute_value"]

        for node in sem_nodes:
            nid = node["id"]
            node_attrs = attr_map.get(nid, {})
            searchable = node["name"] + " " + " ".join(node_attrs.values())
            sem = node["semantic_score"] if node["semantic_score"] is not None else 0.0
            score = hybrid_score(
                query, searchable, precomputed_semantic=sem, keyword_weight=0.5, semantic_weight=0.5
            )
            if score > 0.0:
                result = row_to_dict(node)
                del result["semantic_score"]
                result["attributes"] = node_attrs
                result["result_type"] = "node"
                result["score"] = score
                scored.append((score, result))

        seen_ids = {s[1]["id"] for s in scored}
        for node in kw_nodes:
            nid = node["id"]
            if nid in seen_ids:
                continue
            node_attrs = attr_map.get(nid, {})
            searchable = node["name"] + " " + " ".join(node_attrs.values())
            kw = keyword_score(query, searchable)
            if kw > 0.0:
                result = row_to_dict(node)
                result["attributes"] = node_attrs
                result["result_type"] = "node"
                result["score"] = kw
                scored.append((kw, result))

        edges = await self.db.fetch(
            """
            SELECT id, person_a_id, person_b_id,
                   user_perspective, engine_inference, observed_dynamics,
                   contexts, created_at, updated_at
            FROM social_graph_edges
            """,
        )

        edge_embeddings: dict[str, list[float]] = {}
        if query_embedding and self.db.vec_available and edges:
            edge_ids = [e["id"] for e in edges]
            placeholders = ",".join("?" * len(edge_ids))
            edge_vec_rows = await self.db.fetch(
                f"SELECT id, embedding FROM social_graph_edges_vec WHERE id IN ({placeholders})",
                tuple(edge_ids),
            )
            from src.infra.sqlite_pool import deserialize_f32

            for evr in edge_vec_rows:
                if evr["embedding"]:
                    edge_embeddings[evr["id"]] = deserialize_f32(evr["embedding"])

        for edge in edges:
            parts = []
            if edge["user_perspective"]:
                parts.append(edge["user_perspective"])
            if edge["engine_inference"]:
                parts.append(edge["engine_inference"])
            if edge["observed_dynamics"]:
                parts.append(edge["observed_dynamics"])
            searchable = " ".join(parts)
            if not searchable:
                continue
            edge_emb = edge_embeddings.get(edge["id"])
            score = hybrid_score(
                query,
                searchable,
                query_embedding,
                edge_emb,
                keyword_weight=0.5,
                semantic_weight=0.5,
            )
            if score > 0.0:
                result = row_to_dict(edge)
                result["result_type"] = "edge"
                result["score"] = score
                scored.append((score, result))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

    async def enrich_social_graph_node(
        self,
        person_id: str,
        data: dict[str, str],
    ) -> dict[str, Any]:
        """Upsert attributes on a person node."""
        node = await self.db.fetchrow(
            "SELECT id, name FROM social_graph_nodes WHERE id = ?",
            (person_id,),
        )
        if node is None:
            raise ValueError(f"Node {person_id} not found")

        now_str = datetime.now(timezone.utc).isoformat()

        for key, value in data.items():
            attr_id = str(uuid.uuid4())
            embedding = None
            if self.embedder:
                embedding = await self.embedder.embed(key + " " + value)

            await self.db.execute(
                """
                INSERT INTO social_graph_node_attributes (id, node_id, attribute_key, attribute_value, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (node_id, attribute_key)
                DO UPDATE SET attribute_value = ?, updated_at = ?
                """,
                (attr_id, person_id, key, value, now_str, value, now_str),
            )

            if embedding:
                existing = await self.db.fetchrow(
                    """
                    SELECT a.id FROM social_graph_node_attributes a
                    WHERE a.node_id = ? AND a.attribute_key = ?
                    """,
                    (person_id, key),
                )
                real_id = existing["id"] if existing else attr_id
                await self.db.execute(
                    "DELETE FROM social_graph_node_attributes_vec WHERE id = ?", (real_id,)
                )
                await self.db.execute(
                    "INSERT INTO social_graph_node_attributes_vec (id, embedding) VALUES (?, ?)",
                    (real_id, serialize_f32(embedding)),
                )

        all_attrs = await self.db.fetch(
            "SELECT attribute_key, attribute_value FROM social_graph_node_attributes WHERE node_id = ?",
            (person_id,),
        )
        all_text = (
            node["name"]
            + " "
            + " ".join(r["attribute_key"] + " " + r["attribute_value"] for r in all_attrs)
        )
        if self.embedder:
            node_emb = await self.embedder.embed(all_text)
            await self.db.execute("DELETE FROM social_graph_nodes_vec WHERE id = ?", (person_id,))
            await self.db.execute(
                "INSERT INTO social_graph_nodes_vec (id, embedding) VALUES (?, ?)",
                (person_id, serialize_f32(node_emb)),
            )

        await self.db.execute(
            "UPDATE social_graph_nodes SET updated_at = ? WHERE id = ?",
            (now_str, person_id),
        )

        await self.db.execute("DELETE FROM social_graph_nodes_fts WHERE id = ?", (person_id,))
        await self.db.execute(
            "INSERT INTO social_graph_nodes_fts (id, name) VALUES (?, ?)",
            (person_id, node["name"]),
        )

        await self.db.commit()

        updated = await self.db.fetchrow(
            "SELECT id, name, is_primary, created_at, updated_at FROM social_graph_nodes WHERE id = ?",
            (person_id,),
        )
        result = row_to_dict(updated)
        result["attributes"] = {r["attribute_key"]: r["attribute_value"] for r in all_attrs}
        return result

    async def enrich_social_graph_edge(
        self,
        person_a_id: str,
        person_b_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Upsert edge perspectives between two people."""
        parts = []
        for field in ("user_perspective", "engine_inference", "observed_dynamics"):
            if data.get(field):
                parts.append(data[field])
        embedding = None
        if self.embedder and parts:
            embedding = await self.embedder.embed(" ".join(parts))

        new_contexts = data.get("contexts", [])

        existing = await self.db.fetchrow(
            "SELECT id, contexts FROM social_graph_edges WHERE person_a_id = ? AND person_b_id = ?",
            (person_a_id, person_b_id),
        )

        now_str = datetime.now(timezone.utc).isoformat()

        if existing:
            existing_contexts = json.loads(existing["contexts"]) if existing["contexts"] else []
            merged_contexts = json.dumps(list(set(existing_contexts + new_contexts)))
            edge_id = existing["id"]

            row = await self.db.fetchrow(
                """
                UPDATE social_graph_edges SET
                    user_perspective = COALESCE(?, user_perspective),
                    engine_inference = COALESCE(?, engine_inference),
                    observed_dynamics = COALESCE(?, observed_dynamics),
                    contexts = ?,
                    updated_at = ?
                WHERE id = ?
                RETURNING id, person_a_id, person_b_id,
                          user_perspective, engine_inference, observed_dynamics,
                          contexts, created_at, updated_at
                """,
                (
                    data.get("user_perspective"),
                    data.get("engine_inference"),
                    data.get("observed_dynamics"),
                    merged_contexts,
                    now_str,
                    edge_id,
                ),
            )
        else:
            edge_id = str(uuid.uuid4())
            merged_contexts = json.dumps(new_contexts)

            row = await self.db.fetchrow(
                """
                INSERT INTO social_graph_edges
                    (id, person_a_id, person_b_id,
                     user_perspective, engine_inference, observed_dynamics, contexts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                RETURNING id, person_a_id, person_b_id,
                          user_perspective, engine_inference, observed_dynamics,
                          contexts, created_at, updated_at
                """,
                (
                    edge_id,
                    person_a_id,
                    person_b_id,
                    data.get("user_perspective"),
                    data.get("engine_inference"),
                    data.get("observed_dynamics"),
                    merged_contexts,
                ),
            )

        if embedding:
            await self.db.execute("DELETE FROM social_graph_edges_vec WHERE id = ?", (edge_id,))
            await self.db.execute(
                "INSERT INTO social_graph_edges_vec (id, embedding) VALUES (?, ?)",
                (edge_id, serialize_f32(embedding)),
            )

        await self.db.commit()
        return row_to_dict(row)

    async def update_primary_person(
        self,
        fields: dict[str, str],
    ) -> dict[str, Any] | None:
        node = await self.db.fetchrow(
            "SELECT id FROM social_graph_nodes WHERE is_primary = 1",
        )
        if node is None:
            return None

        person_id = node["id"]
        now_str = datetime.now(timezone.utc).isoformat()

        if "name" in fields:
            await self.db.execute(
                "UPDATE social_graph_nodes SET name = ?, updated_at = ? WHERE id = ?",
                (fields["name"], now_str, person_id),
            )
            await self.db.execute("DELETE FROM social_graph_nodes_fts WHERE id = ?", (person_id,))
            await self.db.execute(
                "INSERT INTO social_graph_nodes_fts (id, name) VALUES (?, ?)",
                (person_id, fields["name"]),
            )
            await self.db.commit()

        attrs = {k: v for k, v in fields.items() if k != "name"}
        if attrs:
            return await self.enrich_social_graph_node(person_id, attrs)

        return await self.get_primary_person()

    async def write_social_graph_edge(
        self,
        person_a_id: str,
        person_b_id: str,
        perspective_data: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.enrich_social_graph_edge(
            person_a_id,
            person_b_id,
            perspective_data,
        )
