"""Memory System Facade — unified interface to all memory components.

Wraps Working Memory, Episodic Memory, Knowledge Store, Social Graph, and Soul
behind a single object. All callers (EC, Thread, LC) access memory through this
facade rather than individual components directly.

This is a thin delegation layer — no business logic. Each method delegates to the
appropriate component.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.memory.memory_factory import WorkingMemoryBackend
from src.memory.episodic_memory import EpisodicMemory
from src.memory.knowledge_store import KnowledgeStore
from src.memory.social_graph import SocialGraph
from src.memory.soul import Soul


class MemorySystem:
    """Unified facade for all memory subsystems.

    Accepts pre-constructed components. Does not own their lifecycle
    (connection/disconnection is the caller's responsibility).

    All components are optional. Reads on None components return empty/None.
    Writes on None components raise RuntimeError.
    """

    def __init__(
        self,
        working_memory: WorkingMemoryBackend | None = None,
        episodic_memory: EpisodicMemory | None = None,
        knowledge_store: KnowledgeStore | None = None,
        social_graph: SocialGraph | None = None,
        soul: Soul | None = None,
    ) -> None:
        self.wm = working_memory
        self.episodic = episodic_memory
        self.ks = knowledge_store
        self.sg = social_graph
        self.soul = soul

    # -- Working Memory --

    async def search_working_memory(self, query: str) -> list:
        """Search across working memory items."""
        if self.wm is None:
            return []
        return await self.wm.search(query)

    async def get_task_memory(self, thread_id: str) -> list:
        """Get all working memory items for a specific task."""
        if self.wm is None:
            return []
        return await self.wm.read(thread_id)

    async def write_working_memory(
        self,
        thread_id: str,
        type: Any,
        content: str,
    ) -> None:
        """Write an item to working memory."""
        if self.wm is None:
            raise RuntimeError("Working memory not configured")
        await self.wm.write(thread_id, type, content)

    async def write_working_memory_batch(
        self,
        thread_id: str,
        items: list,
    ) -> None:
        """Write multiple items to working memory atomically."""
        if self.wm is None:
            raise RuntimeError("Working memory not configured")
        await self.wm.write_batch(thread_id, items)

    async def clear_corrections(self) -> int:
        """Remove all correction items from working memory.

        Corrections are written under the ``"corrections"`` thread (see
        ``bridge_correction``), so clearing them is a targeted ``clear_task``.
        """
        if self.wm is None:
            return 0
        await self.wm.clear_task("corrections")
        return 0

    async def bridge_correction(self, text: str) -> None:
        """Write a user correction to WM under corrections scope."""
        if self.wm is None:
            raise RuntimeError("Working memory not configured")
        from src.memory.working_memory import EntryType

        await self.wm.write("corrections", EntryType.SYSTEM_EVENT, text)

    # -- Episodic Memory --

    async def search_episodes(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Hybrid search across episodes by Key + Description."""
        if self.episodic is None:
            return []
        return await self.episodic.search_episodes(query, limit)

    async def get_episode_value(self, episode_id: str) -> str | None:
        """Get the full Value content of an episode."""
        if self.episodic is None:
            return None
        return await self.episodic.get_episode_value(episode_id)

    async def create_episode(
        self,
        key: str,
        description: str,
        value: str,
    ) -> dict[str, Any]:
        """Create a new KDV episode."""
        if self.episodic is None:
            raise RuntimeError("Episodic memory not configured")
        return await self.episodic.create_episode(key, description, value)

    async def archive_old_episodes(self) -> int:
        """Archive episodes older than 30 days."""
        if self.episodic is None:
            return 0
        return await self.episodic.archive_old_episodes()

    # -- Knowledge Store --

    async def search_knowledge(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Hybrid search across active knowledge entries."""
        if self.ks is None:
            return []
        return await self.ks.search_knowledge(query, limit)

    async def write_knowledge_fact(
        self,
        subject: str,
        content: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Write a user-stated fact at confidence 1.0."""
        if self.ks is None:
            raise RuntimeError("Knowledge store not configured")
        return await self.ks.write_knowledge_fact(subject, content, **kwargs)

    async def write_knowledge_inference(
        self,
        subject: str,
        content: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Write a system-inferred knowledge entry."""
        if self.ks is None:
            raise RuntimeError("Knowledge store not configured")
        return await self.ks.write_knowledge_inference(
            subject,
            content,
            **kwargs,
        )

    async def update_confidence(
        self,
        entry_id: str,
        new_confidence: float,
    ) -> dict[str, Any] | None:
        """Update confidence on a knowledge entry."""
        if self.ks is None:
            return None
        return await self.ks.update_confidence(entry_id, new_confidence)

    async def supersede_entry(
        self,
        entry_id: str,
        valid_until: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Supersede a knowledge entry."""
        if self.ks is None:
            return None
        return await self.ks.supersede_entry(entry_id, valid_until)

    async def run_confidence_decay(self) -> int:
        """Run confidence decay on unreinforced inferences."""
        if self.ks is None:
            return 0
        return await self.ks.run_confidence_decay()

    async def increment_usage(self, entry_id: str) -> None:
        """Increment usage_count on a knowledge entry."""
        if self.ks is None:
            return
        await self.ks.increment_usage(entry_id)

    async def get_fact_staleness_tier(self, entry_id: str) -> str | None:
        """Get staleness tier for a knowledge entry: fresh/aging/stale."""
        if self.ks is None:
            return None
        return await self.ks.get_fact_staleness_tier(entry_id)

    async def search_working_memory_by_type(self, thread_id: str, mem_type: Any) -> list:
        """Filter working memory items for a task by type."""
        if self.wm is None:
            return []
        items = await self.wm.read(thread_id)
        return [item for item in items if item.type == mem_type]

    # -- Maintenance --

    async def run_maintenance_batch(self) -> dict[str, int]:
        """Run all maintenance operations.

        Sequences: confidence decay, episode archival, correction cleanup.
        Returns counts for each operation.
        """
        decayed = await self.run_confidence_decay()
        archived = await self.archive_old_episodes()
        corrections_cleared = await self.clear_corrections()
        return {
            "decayed": decayed,
            "archived": archived,
            "corrections_cleared": corrections_cleared,
        }

    # -- Social Graph --

    async def create_social_person(
        self,
        name: str,
    ) -> dict[str, Any]:
        """Create a new person node in the social graph."""
        if self.sg is None:
            raise RuntimeError("Social graph not configured")
        return await self.sg.create_person(name)

    async def find_or_create_person(
        self,
        name: str,
    ) -> dict[str, Any]:
        """Find existing person by fuzzy match or create a new one."""
        if self.sg is None:
            raise RuntimeError("Social graph not configured")
        return await self.sg.find_or_create_person(name)

    async def get_primary_person(self) -> dict[str, Any] | None:
        """Get the user's own person node (fast path)."""
        if self.sg is None:
            return None
        return await self.sg.get_primary_person()

    async def search_social_graph(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Hybrid search across social graph nodes and edges."""
        if self.sg is None:
            return []
        return await self.sg.search_social_graph(query, limit)

    async def enrich_social_graph_node(
        self,
        person_id: str,
        data: dict[str, str],
    ) -> dict[str, Any]:
        """Upsert attributes on a person node."""
        if self.sg is None:
            raise RuntimeError("Social graph not configured")
        return await self.sg.enrich_social_graph_node(person_id, data)

    async def enrich_social_graph_edge(
        self,
        person_a_id: str,
        person_b_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Upsert edge perspectives between two people."""
        if self.sg is None:
            raise RuntimeError("Social graph not configured")
        return await self.sg.enrich_social_graph_edge(
            person_a_id,
            person_b_id,
            data,
        )

    async def update_primary_person(
        self,
        fields: dict[str, str],
    ) -> dict[str, Any] | None:
        """Update the user's own person node."""
        if self.sg is None:
            return None
        return await self.sg.update_primary_person(fields)

    async def write_social_graph_edge(
        self,
        person_a_id: str,
        person_b_id: str,
        perspective_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Create or update a relationship edge."""
        if self.sg is None:
            raise RuntimeError("Social graph not configured")
        return await self.sg.write_social_graph_edge(
            person_a_id,
            person_b_id,
            perspective_data,
        )

    # -- Soul --

    async def get_core_soul(self) -> dict[str, Any] | None:
        """Get the immutable Core Soul document (cached)."""
        if self.soul is None:
            return None
        return await self.soul.get_core_soul()

    async def get_evolved_soul(self) -> list[dict[str, Any]]:
        """Get expressed Evolved Soul traits."""
        if self.soul is None:
            return []
        return await self.soul.get_evolved_soul()

    async def get_evolved_soul_full(self) -> list[dict[str, Any]]:
        """Get all Evolved Soul traits (signals + expressed) for LC."""
        if self.soul is None:
            return []
        return await self.soul.get_evolved_soul_full()

    async def update_evolved_soul(
        self,
        trait: str,
        value: str,
        confidence: float,
        evidence_count: int,
    ) -> dict[str, Any]:
        """Upsert an Evolved Soul trait."""
        if self.soul is None:
            raise RuntimeError("Soul not configured")
        return await self.soul.update_evolved_soul(
            trait,
            value,
            confidence,
            evidence_count,
        )
