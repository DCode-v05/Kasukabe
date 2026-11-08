"""Unified cached hybrid search — no LLM, no agentic behavior.

Searches across: episodic memory, knowledge store, social graph,
AD metadata (connected services), and UD metadata (agents/skills/tools).

Cache strategy: TTL-based (15 min). Single-user single-process, TTL is sufficient.

Does NOT include soul, evolved traits, or persona — those come from the
consciousness object, injected separately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from src.infra.memoize import TTLCache

logger = logging.getLogger(__name__)

# Search result freshness. Independent of the utility and asset cache TTLs
# (coincidentally equal values, separate policies; do not unify).
CACHE_TTL_SECONDS = 900  # 15 minutes


class Search:
    """Unified search interface with in-memory caching.

    AD metadata reads from SQLite. UD platform catalog reads from
    the catalog loader (in-memory). User UD data reads from SQLite.
    """

    def __init__(
        self,
        db: Any,
        episodic_memory: Any | None = None,
        knowledge_store: Any | None = None,
        social_graph: Any | None = None,
        embedding_service: Any | None = None,
        catalog: Any | None = None,
        signal_store: Any | None = None,
        skill_context_window: int | None = None,
    ) -> None:
        self.db = db
        self.episodic = episodic_memory
        self.knowledge_store = knowledge_store
        self.social_graph = social_graph
        self.embedder = embedding_service
        self.catalog = catalog
        self.signal_store = signal_store
        # Sizes the skill-metadata budget. None falls back to a fixed character
        # budget, which is the safe reading when the model is unknown.
        self.skill_context_window = skill_context_window
        self.cache = TTLCache(default_ttl=CACHE_TTL_SECONDS)

    async def start(self) -> None:
        """No-op. TTL handles cache invalidation."""
        pass

    async def stop(self) -> None:
        pass

    def get_cached(self, key: str) -> Any | None:
        return self.cache.get(key)

    def set_cached(self, key: str, data: Any) -> None:
        self.cache.set(key, data)

    def set_signal_store(self, signal_store: Any) -> None:
        self.signal_store = signal_store

    def invalidate_all(self) -> None:
        self.cache.invalidate()

    # --- Episodic Memory ---

    async def episodes(
        self, query: str, limit: int = 20, query_embedding: list[float] | None = None
    ) -> list[dict]:
        if self.episodic is None:
            return []
        return await self.episodic.search_episodes(
            query, limit=limit, query_embedding=query_embedding
        )

    # --- Knowledge Store ---

    async def knowledge(
        self, query: str, limit: int = 20, query_embedding: list[float] | None = None
    ) -> list[dict]:
        if self.knowledge_store is None:
            return []
        return await self.knowledge_store.search_knowledge(
            query, limit=limit, query_embedding=query_embedding
        )

    # --- Social Graph ---

    async def social(
        self, query: str, limit: int = 10, query_embedding: list[float] | None = None
    ) -> list[dict]:
        if self.social_graph is None:
            return []
        return await self.social_graph.search_social_graph(
            query, limit=limit, query_embedding=query_embedding
        )

    # --- Pending Learning Signals (FTS5, pre-consolidation) ---

    async def signals(self, query: str, limit: int = 10) -> list[dict]:
        """Search pending learning signals via FTS5. Pre-consolidation data."""
        if self.signal_store is None:
            return []
        try:
            return await self.signal_store.search_fts(query, limit=limit)
        except Exception:
            return []

    # --- Unified Cross-Memory Search ---

    async def unified(self, query: str, limit: int = 20) -> list[dict]:
        """Cross-memory search with entity fusion.

        Pre-computes query embedding once, runs episodes + knowledge + social
        in parallel, then boosts results that mention people from the social graph.
        """
        # Pre-compute embedding once (eng review decision 4A)
        query_embedding = None
        if self.embedder:
            query_embedding = await self.embedder.embed(query)

        async def empty() -> list:
            return []

        ep_results, kn_results, sg_results, sig_results = await asyncio.gather(
            self.episodic.search_episodes(query, limit=limit, query_embedding=query_embedding)
            if self.episodic
            else empty(),
            self.knowledge_store.search_knowledge(
                query, limit=limit, query_embedding=query_embedding
            )
            if self.knowledge_store
            else empty(),
            self.social_graph.search_social_graph(query, limit=10, query_embedding=query_embedding)
            if self.social_graph
            else empty(),
            self.signals(query, limit=5),
        )

        # Tag results with source
        for r in ep_results:
            r["_source"] = "episode"
        for r in kn_results:
            r["_source"] = "knowledge"
        for r in sg_results:
            r["_source"] = "social"
        for r in sig_results:
            r["_source"] = "pending_signal"
            r["score"] = r.get("score", 0.5)

        # Collect person names from social results for cross-reference boosting
        person_names = set()
        for r in sg_results:
            if r.get("result_type") == "node" and r.get("name"):
                person_names.add(r["name"].lower())

        # Boost episodes/knowledge that mention people found in social results
        all_results = ep_results + kn_results + sg_results + sig_results
        if person_names:
            for r in all_results:
                text = ""
                if r.get("_source") == "episode":
                    text = (r.get("key", "") + " " + r.get("description", "")).lower()
                elif r.get("_source") == "knowledge":
                    text = (r.get("subject", "") + " " + r.get("content", "")).lower()

                if text:
                    # Word-boundary regex matching (eng review decision 2A)
                    mentions = sum(
                        1
                        for name in person_names
                        if re.search(r"\b" + re.escape(name) + r"\b", text)
                    )
                    if mentions > 0:
                        r["score"] = r.get("score", 0) * (1 + 0.15 * mentions)

        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return all_results[:limit]

    # --- AD Metadata (cached, from SQLite) ---

    async def ad_connections(self) -> dict[str, Any]:
        """Return AD catalog: servers, connections, action names by server."""
        cache_key = "ad_meta"
        cached = self.get_cached(cache_key)
        if cached is not None:
            return cached

        if self.db is None:
            return {"servers": [], "connections": [], "action_names_by_server": {}}

        servers = []
        for r in await self.db.fetch(
            "SELECT server_name, display_name, category, vetted, instructions FROM server_registry"
        ):
            entry = dict(r)
            # vetted is a BOOLEAN column; sqlite hands back 1/0, normalize to bool.
            entry["vetted"] = bool(entry.get("vetted"))
            servers.append(entry)
        connections = []
        for r in await self.db.fetch(
            """SELECT connection_id, server_name, status, enabled,
                      provenance, provenance_rank, account_ref, owner_ref, created_at
               FROM connections"""
        ):
            entry = dict(r)
            # enabled is the per-asset pause flag (BOOLEAN); expose it as a real
            # bool so callers can gate selection/exposure on it.
            entry["enabled"] = bool(entry.get("enabled"))
            connections.append(entry)
        actions = await self.db.fetch("SELECT server_name, action_name FROM action_registry")

        actions_by_server: dict[str, list[str]] = {}
        for row in actions:
            actions_by_server.setdefault(row["server_name"], []).append(row["action_name"])

        result = {
            "servers": servers,
            "connections": connections,
            "action_names_by_server": actions_by_server,
        }
        self.set_cached(cache_key, result)
        return result

    # --- UD Metadata (cached, from catalog + SQLite) ---

    async def agents(self, capabilities: list[str] | None = None) -> list[dict]:
        """Return agent metadata from catalog + user overrides from SQLite."""
        cache_key = f"ud_agents:{capabilities}"
        cached = self.get_cached(cache_key)
        if cached is not None:
            return cached

        results: list[dict] = []

        # Platform agents from catalog (in-memory)
        if self.catalog:
            for agent_data in self.catalog.all_agents():
                if capabilities:
                    agent_caps = agent_data.get("capabilities", [])
                    if not all(c in agent_caps for c in capabilities):
                        continue
                d = {
                    "name": agent_data.get("name", ""),
                    "version": agent_data.get("version", "1.0.0"),
                    "type": agent_data.get("type", ""),
                    "capabilities": agent_data.get("capabilities", []),
                    "input_signature": agent_data.get("input_signature", {}),
                    "output_signature": agent_data.get("output_signature", {}),
                    "description": agent_data.get("description", ""),
                    "source": "platform",
                }
                results.append(d)

        # User agents from SQLite
        if self.db:
            user_rows = await self.db.fetch(
                """SELECT name, type, capabilities, input_signature,
                          output_signature, description
                   FROM user_agents ORDER BY name""",
            )
            for row in user_rows:
                d = dict(row)
                d["source"] = "user_created"
                d["version"] = "user"
                # Parse JSON string fields
                for field in ("capabilities", "input_signature", "output_signature"):
                    if isinstance(d.get(field), str):
                        try:
                            d[field] = json.loads(d[field])
                        except (json.JSONDecodeError, TypeError):
                            pass
                results.append(d)

        self.set_cached(cache_key, results)
        return results

    async def skills(self) -> list[dict]:
        """Return skill metadata from the catalog.

        The catalog is the single source of skill definitions — it already spans
        every skill root, user skills included. SQLite holds only per-skill state
        and is deliberately not read here: when it was, a user skill appeared
        twice, the second time as a bare name with an empty description.
        """
        cache_key = "ud_skills"
        cached = self.get_cached(cache_key)
        if cached is not None:
            return cached

        results: list[dict] = []

        if self.catalog:
            for name, entry in self.catalog.skills.items():
                results.append(
                    {
                        "name": name,
                        "version": entry.version,
                        "description": entry.data.get("description", ""),
                        "source": "platform" if entry.source == "core" else entry.source,
                    }
                )

        # Bound what reaches the model. Without this the list grows with the
        # skill count and silently eats context that belongs to the conversation.
        from src.catalog import apply_skill_metadata_budget, skill_metadata_budget

        results, _dropped = apply_skill_metadata_budget(
            results, skill_metadata_budget(self.skill_context_window)
        )

        self.set_cached(cache_key, results)
        return results

    async def tools(self) -> list[dict]:
        """Return platform tool metadata from catalog."""
        cache_key = "ud_tools"
        cached = self.get_cached(cache_key)
        if cached is not None:
            return cached

        results: list[dict] = []
        if self.catalog:
            for tool_data in self.catalog.all_tools():
                results.append(
                    {
                        "name": tool_data.get("name", ""),
                        "description": tool_data.get("description", ""),
                    }
                )

        self.set_cached(cache_key, results)
        return results

    # --- Embedding for slot scoring ---

    EMBED_CACHE_MAX = 256

    async def embed(self, text: str) -> list[float] | None:
        """Embed `text`, with a small LRU on the exact string. Skips an OpenAI
        round-trip when the same prompt is embedded again (router fallback,
        repeat queries, recall scoring on identical inputs)."""
        if self.embedder is None:
            return None
        cache = getattr(self, "embed_cache", None)
        if cache is None:
            from collections import OrderedDict

            self.embed_cache: OrderedDict[str, Any] = OrderedDict()
            cache = self.embed_cache
        hit = cache.get(text)
        if hit is not None:
            cache.move_to_end(text)
            return hit
        emb = await self.embedder.embed(text)
        if emb is not None:
            cache[text] = emb
            if len(cache) > self.EMBED_CACHE_MAX:
                cache.popitem(last=False)
        return emb

    # --- Health ---

    def cache_stats(self) -> dict[str, Any]:
        return {
            "total_entries": len(self.cache),
            "active_entries": sum(1 for e in self.cache.entries.values() if not e.expired),
            "expired_entries": sum(1 for e in self.cache.entries.values() if e.expired),
            "keys": list(self.cache.entries.keys()),
        }
