"""Consciousness — session-scoped identity bundle.

ConsciousnessContext bundles Core Soul + Evolved Soul + User Persona into
one object that gets injected into every LLM system prompt. It is NOT
Working Memory — identity is separate from task state.

ConsciousnessManager builds, caches (in-memory only), and refreshes
the context. Core Soul is loaded from a JSON file at boot (immutable,
zero DB calls). Evolved traits and persona are fetched from SQLite
and cached until invalidated by data writes (LC pipeline, memory import).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from src.herald.types import SystemPromptBlock

if TYPE_CHECKING:
    from src.memory.episodic_memory import EpisodicMemory
    from src.memory.knowledge_store import KnowledgeStore
    from src.memory.social_graph import SocialGraph
    from src.memory.soul import Soul


class SectionStability(Enum):
    """How cacheable a prompt section is across turns."""

    STABLE = "stable"
    DYNAMIC = "dynamic"
    VOLATILE = "volatile"


DYNAMIC_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"

logger = logging.getLogger(__name__)

CORE_SOUL_PATH = Path(__file__).resolve().parent.parent / "engine_core" / "core_soul.json"
CORE_SOUL: dict[str, Any] = {}
try:
    CORE_SOUL = json.loads(CORE_SOUL_PATH.read_text(encoding="utf-8"))
except Exception as e:
    logger.warning("Failed to load packaged core_soul.json fallback: %s", e)


def set_core_soul(document: dict[str, Any]) -> None:
    """Override the in-process core soul. Called by bootstrap after the
    catalog volume is loaded — collapses the dual sources into one."""
    global CORE_SOUL
    CORE_SOUL = document or {}


def reload_core_soul() -> dict[str, Any]:
    """Re-read core_soul.json from the packaged fallback. For admin
    hot-reload endpoint when no live catalog volume is available."""
    global CORE_SOUL
    CORE_SOUL = json.loads(CORE_SOUL_PATH.read_text(encoding="utf-8"))
    return CORE_SOUL


@dataclass
class ConsciousnessContext:
    """Session-scoped identity bundle — never stored in Working Memory."""

    core_soul: dict[str, Any]
    evolved_traits: list[dict[str, Any]]
    user_persona: dict[str, Any] | None
    recent_episodes: list[dict[str, Any]] = field(default_factory=list)
    active_knowledge: list[dict[str, Any]] = field(default_factory=list)
    built_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def render_core(self) -> str:
        """Stable identity layers — cacheable across turns within a session.

        Includes: preamble, core truths, voice, never, your human, self-awareness,
        evolution, boundaries, continuity, behavioral adaptations, persona.
        Excludes: recent episodes and active knowledge (those change per turn).
        """
        parts: list[str] = []

        doc = self.core_soul

        if doc.get("preamble"):
            parts.append(doc["preamble"])

        sections = [
            ("core_truths", "Core Truths"),
            ("voice", "Voice"),
            ("never", "Never"),
            ("your_human", "Your Human"),
            ("self_awareness", "Self-Awareness"),
            ("evolution", "Evolution"),
            ("boundaries", "Boundaries"),
        ]
        for key, heading in sections:
            items = doc.get(key, [])
            if items:
                lines = [f"\n{heading}\n"]
                for item in items:
                    lines.append(f"- {item}")
                parts.append("\n".join(lines))

        if doc.get("continuity"):
            parts.append(f"\nContinuity\n\n{doc['continuity']}")

        if self.evolved_traits:
            trait_lines = ["\nBehavioral Adaptations\n"]
            for t in self.evolved_traits:
                name = t.get("trait", "")
                value = t.get("value", "")
                trait_lines.append(f"- {name}: {value}")
            parts.append("\n".join(trait_lines))

        if self.user_persona:
            persona_lines = ["\nYour Human's Profile\n"]
            name = self.user_persona.get("name", "")
            if name:
                persona_lines.append(f"Name: {name}")
            attrs = self.user_persona.get("attributes", {})
            for k, v in attrs.items():
                if v:
                    persona_lines.append(f"{k}: {v}")
            if len(persona_lines) > 1:
                parts.append("\n".join(persona_lines))

        return "\n\n".join(parts)

    def render_identity_delta(self, previous: "ConsciousnessContext") -> str:
        """Render only the identity parts that changed since *previous*.

        Returns empty string if nothing changed. Used to inject identity
        updates via <system-reminder> without rebuilding the cached system
        prompt.
        """
        parts: list[str] = []

        old_traits = {t.get("trait", ""): t.get("value", "") for t in previous.evolved_traits}
        new_traits = {t.get("trait", ""): t.get("value", "") for t in self.evolved_traits}
        if old_traits != new_traits:
            trait_lines = ["Updated Behavioral Adaptations:"]
            for t in self.evolved_traits:
                name = t.get("trait", "")
                value = t.get("value", "")
                trait_lines.append(f"- {name}: {value}")
            parts.append("\n".join(trait_lines))

        if self.user_persona != previous.user_persona and self.user_persona:
            persona_lines = ["Updated Human Profile:"]
            name = self.user_persona.get("name", "")
            if name:
                persona_lines.append(f"Name: {name}")
            attrs = self.user_persona.get("attributes", {})
            for k, v in attrs.items():
                if v:
                    persona_lines.append(f"{k}: {v}")
            if len(persona_lines) > 1:
                parts.append("\n".join(persona_lines))

        return "\n\n".join(parts)

    def render_context(self) -> str:
        """Dynamic context — changes per turn, sent via <system-reminder> in messages.

        Includes: recent episodes and active knowledge.
        """
        context_lines: list[str] = []
        if self.recent_episodes:
            context_lines.append("Recently:")
            for ep in self.recent_episodes[:5]:
                key = ep.get("key", "")
                if key:
                    context_lines.append(f"- {key}")

        if self.active_knowledge:
            context_lines.append("Active priorities:")
            for kn in self.active_knowledge[:5]:
                subject = kn.get("subject", "")
                content = kn.get("content", "")
                if subject:
                    context_lines.append(f"- {subject}: {content}")

        return "\n".join(context_lines)

    def render(self) -> str:
        """Full render — backward compatible, combines core + context."""
        core = self.render_core()
        context = self.render_context()
        if context:
            return core + "\n\n\nCurrent Context\n" + context
        return core


class ConsciousnessManager:
    """Builds and caches ConsciousnessContext.

    Core Soul loaded from JSON file at import time (immutable, zero DB calls).
    Evolved traits, persona, recent episodes, and active knowledge are fetched
    from SQLite and cached until invalidate() is called by data writers
    (LC pipeline, memory import, admin reload).
    """

    def __init__(
        self,
        soul: "Soul | None" = None,
        social_graph: "SocialGraph | None" = None,
        episodic_memory: "EpisodicMemory | None" = None,
        knowledge_store: "KnowledgeStore | None" = None,
        global_cache_ttl: str = "1h",
    ) -> None:
        self.soul = soul
        self.social_graph = social_graph
        self.episodic = episodic_memory
        self.knowledge = knowledge_store
        self.global_cache_ttl = global_cache_ttl
        self.cache: dict[str, ConsciousnessContext] = {}
        self.sections: list[tuple[str, SectionStability, Callable[[], str]]] = []
        self.section_cache: dict[str, str] = {}

    async def get(self) -> ConsciousnessContext | None:
        """Get or build consciousness.

        Returns cached context if available. Call invalidate() when underlying
        data changes (soul traits, persona, etc.) to force a rebuild on the
        next get() call.
        """
        if not CORE_SOUL:
            return None

        cached = self.cache.get("_single_user")
        if cached is not None:
            return cached

        ctx = await self.build()
        if ctx is not None:
            self.cache["_single_user"] = ctx
        return ctx

    def invalidate(self) -> None:
        """Clear cached consciousness AND section cache.

        Next get() rebuilds consciousness from DB. Next build_prompt_blocks()
        recomputes all sections. Single invalidation point — post-compact
        cleanup calls this one method and both caches reset.
        """
        self.cache.pop("_single_user", None)
        self.section_cache.clear()

    def invalidate_all(self) -> None:
        self.cache.clear()
        self.section_cache.clear()


    def register_section(
        self,
        name: str,
        stability: SectionStability,
        compute: Callable[[], str],
    ) -> None:
        """Register a named prompt section with its stability class.

        Called by the thread at session start. Sections are resolved in
        registration order — stable sections first, then the boundary
        marker, then dynamic/volatile sections.
        """
        self.sections.append((name, stability, compute))

    def resolve_sections(self) -> list[tuple[str, SectionStability, str]]:
        """Resolve all registered sections, returning (name, stability, content).

        STABLE and DYNAMIC sections are memoized — computed once and cached
        until invalidate() is called. VOLATILE sections recompute on every
        call (they represent content that can change between turns, like
        MCP instructions from servers that connect/disconnect).
        """
        result: list[tuple[str, SectionStability, str]] = []
        for name, stability, compute in self.sections:
            if stability == SectionStability.VOLATILE:
                content = compute()
            elif name in self.section_cache:
                content = self.section_cache[name]
            else:
                content = compute()
                self.section_cache[name] = content
            result.append((name, stability, content))
        return result

    def build_prompt_blocks(
        self,
        has_dynamic_tools: bool = False,
    ) -> list[SystemPromptBlock]:
        """Resolve sections and emit SystemPromptBlock list for the LLM provider.

        Splits at DYNAMIC_BOUNDARY:
        - Sections before boundary: scope="global", ttl=self.global_cache_ttl
          (cross-user cacheable; defaults to "1h", configurable via
          GLOBAL_CACHE_TTL for short one-off contexts that should pay the
          cheaper "5m" cache-write)
        - Sections after boundary: scope=None (ephemeral only)
        - If has_dynamic_tools is True (MCP tools present): no global scope
          because per-user tool lists make the prompt user-specific.

        Empty sections are skipped — they contribute nothing and waste a
        cache_control slot.
        """
        resolved = self.resolve_sections()
        blocks: list[SystemPromptBlock] = []
        before_boundary = True

        for name, stability, content in resolved:
            if content == DYNAMIC_BOUNDARY:
                before_boundary = False
                continue
            if not content:
                continue
            if before_boundary and not has_dynamic_tools:
                scope = "global"
                ttl = self.global_cache_ttl
            else:
                scope = None
                ttl = None
            blocks.append(SystemPromptBlock(text=content, cache_scope=scope, ttl=ttl))

        return blocks

    def clear_section_cache(self) -> None:
        """Explicitly clear memoized section content.

        Normally called via invalidate() which clears both consciousness
        and sections. This method exists for cases where only the section
        cache needs resetting without touching the consciousness context.
        """
        self.section_cache.clear()

    def section_content_hashes(self) -> dict[str, str]:
        """SHA-256 hash of each cached section's content.

        Used by the cache break detector to identify which section changed
        when a cache miss occurs. Only returns hashes for sections currently
        in the cache — call resolve_sections() first to populate.
        """
        hashes: dict[str, str] = {}
        for name, stability, compute in self.sections:
            content = self.section_cache.get(name, "")
            hashes[name] = hashlib.sha256(content.encode()).hexdigest()
        return hashes

    async def build(self) -> ConsciousnessContext | None:
        """Fetch evolved traits, persona, recent episodes, and active knowledge concurrently."""

        async def fetch_evolved():
            if self.soul is None:
                return []
            return await self.soul.get_evolved_soul()

        async def fetch_persona():
            if self.social_graph is None:
                return None
            return await self.social_graph.get_primary_person()

        async def recent_episodes():
            if self.episodic is None:
                return []
            return await self.episodic.get_recent(limit=5)

        async def active_knowledge():
            if self.knowledge is None:
                return []
            return await self.knowledge.get_active_top(limit=5)

        evolved, persona, episodes, knowledge = await asyncio.gather(
            fetch_evolved(),
            fetch_persona(),
            recent_episodes(),
            active_knowledge(),
        )

        return ConsciousnessContext(
            core_soul=CORE_SOUL,
            evolved_traits=evolved or [],
            user_persona=persona,
            recent_episodes=episodes or [],
            active_knowledge=knowledge or [],
        )
