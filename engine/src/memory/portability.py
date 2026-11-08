"""Memory Portability — export and import complete user memory bundles.

Export dumps all memory subsystems (episodes, knowledge store, social graph,
evolved soul) from SQLite into a versioned JSON bundle. Import restores from
a bundle using existing write methods so embeddings are regenerated and
business rules are enforced.

Not exported: working memory (ephemeral in-memory), core soul (from catalog),
embeddings (model-specific, regenerated on import).
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.infra.sqlite_pool import SqlitePool
from src.memory.episodic_memory import EpisodicMemory
from src.memory.knowledge_store import KnowledgeStore
from src.memory.social_graph import SocialGraph
from src.memory.soul import Soul
from src.security.write_guard import guard_content

logger = logging.getLogger(__name__)

EXPORT_VERSION = "1.0.0"


def ts(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


async def export_memory(db: SqlitePool) -> dict[str, Any]:
    """Bulk-read all memory tables, return a versioned JSON bundle."""
    bundle: dict[str, Any] = {
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }

    # --- Episodes ---
    ep_rows = await db.fetch(
        "SELECT key, description, value, value_compressed, tier, created_at "
        "FROM episodes ORDER BY created_at",
    )
    episodes = []
    for r in ep_rows:
        value = r["value"]
        if r["tier"] == "archive" and r["value_compressed"] is not None:
            value = gzip.decompress(r["value_compressed"]).decode("utf-8")
        episodes.append(
            {
                "key": r["key"],
                "description": r["description"],
                "value": value,
                "tier": r["tier"],
                "created_at": ts(r["created_at"]),
            }
        )
    bundle["episodes"] = episodes

    # --- Knowledge Store ---
    ks_rows = await db.fetch(
        "SELECT subject, content, type, tags, confidence, source, "
        "usage_count, created_at, last_reinforced, valid_from, valid_until, "
        "recorded_at, superseded_at "
        "FROM knowledge_store ORDER BY created_at",
    )
    knowledge = []
    for r in ks_rows:
        tags = r["tags"]
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        knowledge.append(
            {
                "subject": r["subject"],
                "content": r["content"],
                "type": r["type"],
                "tags": tags,
                "confidence": float(r["confidence"]),
                "source": r["source"],
                "usage_count": r["usage_count"],
                "created_at": ts(r["created_at"]),
                "last_reinforced": ts(r["last_reinforced"]),
                "valid_from": ts(r["valid_from"]),
                "valid_until": ts(r["valid_until"]),
                "recorded_at": ts(r["recorded_at"]),
                "superseded_at": ts(r["superseded_at"]),
            }
        )
    bundle["knowledge"] = knowledge

    # --- Social Graph Nodes ---
    node_rows = await db.fetch(
        "SELECT id, name, is_primary, created_at, updated_at "
        "FROM social_graph_nodes ORDER BY created_at",
    )
    nodes = []
    node_ids = []
    for r in node_rows:
        node_ids.append(r["id"])
        nodes.append(
            {
                "ref_id": r["id"],
                "name": r["name"],
                "is_primary": bool(r["is_primary"]),
                "attributes": {},
                "created_at": ts(r["created_at"]),
                "updated_at": ts(r["updated_at"]),
            }
        )

    # --- Node Attributes ---
    if node_ids:
        placeholders = ",".join("?" * len(node_ids))
        attr_rows = await db.fetch(
            f"SELECT node_id, attribute_key, attribute_value "
            f"FROM social_graph_node_attributes WHERE node_id IN ({placeholders})",
            tuple(node_ids),
        )
        attr_map: dict[str, dict[str, str]] = {}
        for r in attr_rows:
            nid = r["node_id"]
            if nid not in attr_map:
                attr_map[nid] = {}
            attr_map[nid][r["attribute_key"]] = r["attribute_value"]

        for node in nodes:
            node["attributes"] = attr_map.get(node["ref_id"], {})

    # --- Social Graph Edges ---
    edge_rows = await db.fetch(
        "SELECT person_a_id, person_b_id, user_perspective, engine_inference, "
        "observed_dynamics, contexts, created_at, updated_at "
        "FROM social_graph_edges ORDER BY created_at",
    )
    edges = []
    for r in edge_rows:
        contexts = r["contexts"]
        if isinstance(contexts, str):
            try:
                contexts = json.loads(contexts)
            except (json.JSONDecodeError, TypeError):
                contexts = []
        edges.append(
            {
                "person_a_ref": r["person_a_id"],
                "person_b_ref": r["person_b_id"],
                "user_perspective": r["user_perspective"],
                "engine_inference": r["engine_inference"],
                "observed_dynamics": r["observed_dynamics"],
                "contexts": contexts,
                "created_at": ts(r["created_at"]),
                "updated_at": ts(r["updated_at"]),
            }
        )

    bundle["social_graph"] = {"nodes": nodes, "edges": edges}

    # --- Evolved Soul ---
    soul_rows = await db.fetch(
        "SELECT trait, value, status, confidence, evidence_count, "
        "last_reinforced, created_at "
        "FROM evolved_soul ORDER BY trait",
    )
    evolved = []
    for r in soul_rows:
        evolved.append(
            {
                "trait": r["trait"],
                "value": r["value"],
                "status": r["status"],
                "confidence": float(r["confidence"]),
                "evidence_count": r["evidence_count"],
                "last_reinforced": ts(r["last_reinforced"]),
                "created_at": ts(r["created_at"]),
            }
        )
    bundle["evolved_soul"] = evolved

    logger.info(
        "Memory export: episodes=%d knowledge=%d nodes=%d edges=%d traits=%d",
        len(episodes),
        len(knowledge),
        len(nodes),
        len(edges),
        len(evolved),
    )
    return bundle


def join_nonempty(*parts: Any) -> str:
    """Join truthy string parts with `\\n`. Empty/None parts are dropped.

    Used to assemble scannable blobs for guard_content(). The scanner treats
    an empty string as a no-op, so dropping empty parts keeps the blob tight
    without changing scan semantics.
    """
    return "\n".join(p for p in parts if p)


def scan_bundle(bundle: dict[str, Any]) -> None:
    """Pre-scan every scannable string in the bundle. Raise on first match.

    Two-pass design: scan all records, *then* write. Guarantees the bundle
    is rejected as a whole — no partial commits. Order mirrors the write
    loop in import_memory() so the `where=` label identifies the same
    section the writer would have touched.
    """
    for trait_data in bundle.get("evolved_soul", []):
        guard_content(
            join_nonempty(trait_data.get("trait"), trait_data.get("value")),
            where="portability-import-soul",
        )

    for ep in bundle.get("episodes", []):
        guard_content(
            join_nonempty(ep.get("key"), ep.get("description"), ep.get("value")),
            where="portability-import-episode",
        )

    for entry in bundle.get("knowledge", []):
        guard_content(
            join_nonempty(entry.get("subject"), entry.get("content")),
            where="portability-import-knowledge",
        )

    sg_data = bundle.get("social_graph", {})
    for node in sg_data.get("nodes", []):
        attrs = node.get("attributes") or {}
        # Scan keys AND values: social_graph.enrich_social_graph_node persists
        # attribute_key alongside attribute_value, and the per-attribute embed
        # call (`embed(key + " " + value)`) plus the node-level rebuild both
        # feed keys into the embedding payload. A secret-as-key would
        # otherwise pass the scan and land in the DB + embedding API.
        attr_items = attrs.items() if isinstance(attrs, dict) else ()
        guard_content(
            join_nonempty(node.get("name"), *(f"{k}\n{v}" for k, v in attr_items)),
            where="portability-import-social-node",
        )

    for edge in sg_data.get("edges", []):
        contexts = edge.get("contexts")
        contexts_blob = " ".join(contexts) if isinstance(contexts, list) else (contexts or "")
        guard_content(
            join_nonempty(
                edge.get("user_perspective"),
                edge.get("engine_inference"),
                edge.get("observed_dynamics"),
                contexts_blob,
            ),
            where="portability-import-social-edge",
        )


async def import_memory(
    bundle: dict[str, Any],
    episodic_memory: EpisodicMemory,
    knowledge_store: KnowledgeStore,
    social_graph: SocialGraph,
    soul: Soul,
) -> dict[str, Any]:
    """Import a memory bundle into the engine's memory.

    Uses existing write methods so embeddings are regenerated and business
    rules are enforced. Import order matters for FK relationships.

    Secret scanning runs as a pre-pass over the entire bundle. If any field
    in any record matches, SecretDetectedError fires before the first write
    — the bundle is rejected atomically with zero side effects on the DB
    and zero embedding-API spend on rejected records.
    """
    version = bundle.get("version", "")
    if not version.startswith("1."):
        raise ValueError(f"Unsupported bundle version: {version}")

    scan_bundle(bundle)

    counts = {
        "evolved_traits": 0,
        "episodes": 0,
        "knowledge": 0,
        "social_nodes": 0,
        "social_edges": 0,
    }

    for trait_data in bundle.get("evolved_soul", []):
        await soul.update_evolved_soul(
            trait=trait_data["trait"],
            value=trait_data["value"],
            confidence=trait_data["confidence"],
            evidence_count=trait_data["evidence_count"],
        )
        counts["evolved_traits"] += 1

    for ep in bundle.get("episodes", []):
        await episodic_memory.create_episode(
            key=ep["key"],
            description=ep["description"],
            value=ep["value"] or "",
        )
        counts["episodes"] += 1

    for entry in bundle.get("knowledge", []):
        if entry["source"] == "user_stated":
            result = await knowledge_store.write_knowledge_fact(
                subject=entry["subject"],
                content=entry["content"],
                tags=entry.get("tags"),
            )
        else:
            result = await knowledge_store.write_knowledge_inference(
                subject=entry["subject"],
                content=entry["content"],
                entry_type=entry.get("type", "knowledge"),
                confidence=entry.get("confidence", 0.6),
                tags=entry.get("tags"),
            )
        if entry.get("superseded_at"):
            await knowledge_store.supersede_entry(result["id"])
        counts["knowledge"] += 1

    sg_data = bundle.get("social_graph", {})
    ref_map: dict[str, str] = {}

    for node in sg_data.get("nodes", []):
        attrs = node.get("attributes", {})
        result = await social_graph.create_person(
            name=node["name"],
            is_primary=node.get("is_primary", False),
        )
        ref_map[node["ref_id"]] = result["id"]

        if attrs:
            await social_graph.enrich_social_graph_node(
                person_id=result["id"],
                data=attrs,
            )
        counts["social_nodes"] += 1

    for edge in sg_data.get("edges", []):
        new_a = ref_map.get(edge["person_a_ref"])
        new_b = ref_map.get(edge["person_b_ref"])
        if new_a is None or new_b is None:
            logger.warning(
                "Skipping edge: missing node ref a=%s b=%s",
                edge["person_a_ref"],
                edge["person_b_ref"],
            )
            continue

        edge_data: dict[str, Any] = {}
        if edge.get("user_perspective"):
            edge_data["user_perspective"] = edge["user_perspective"]
        if edge.get("engine_inference"):
            edge_data["engine_inference"] = edge["engine_inference"]
        if edge.get("observed_dynamics"):
            edge_data["observed_dynamics"] = edge["observed_dynamics"]
        if edge.get("contexts"):
            edge_data["contexts"] = edge["contexts"]

        await social_graph.enrich_social_graph_edge(
            person_a_id=new_a,
            person_b_id=new_b,
            data=edge_data,
        )
        counts["social_edges"] += 1

    logger.info(
        "Memory import: traits=%d episodes=%d knowledge=%d nodes=%d edges=%d",
        counts["evolved_traits"],
        counts["episodes"],
        counts["knowledge"],
        counts["social_nodes"],
        counts["social_edges"],
    )
    return counts
