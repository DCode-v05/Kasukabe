"""In-memory working memory — dict-based temporal log with TTL reaping.

All entries live in process memory and are lost on container restart.
This is by design — working memory is ephemeral and flushes to the
LC queue after each execution. The LC pipeline extracts durable
knowledge into SQLite-backed memory layers.

Key design decisions:
- Dict-based storage for O(1) writes (no I/O overhead)
- TTL reaping on every write to prevent unbounded growth
- Implements WorkingMemoryBackend protocol
- Hybrid search (keyword + semantic) computed in-process
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from src.memory.embeddings import EmbeddingService, hybrid_score
from src.memory.working_memory import LogEntry, EntryType

# Alias of the canonical conversation-working-set retention window (see the
# comment on CONVERSATION_TTL_SECONDS in conversation_store.py).
from src.engine_core.conversation_store import CONVERSATION_TTL_SECONDS

TTL_SECONDS = CONVERSATION_TTL_SECONDS


class InMemoryWorkingMemory:
    """In-memory temporal log scoped to a single user.

    Thread-safe within a single asyncio event loop (all access is
    single-threaded). No locks needed.
    """

    def __init__(
        self,
        session_id: str,
        embedding_service: EmbeddingService | None = None,
        ttl_seconds: int = TTL_SECONDS,
    ) -> None:
        self.session_id = session_id
        self.embedder = embedding_service
        self.ttl = ttl_seconds
        self.entries: dict[str, list[LogEntry]] = {}
        self.timestamps: dict[str, float] = {}

    def touch(self, thread_id: str) -> None:
        self.timestamps[thread_id] = time.monotonic()

    def reap_stale(self) -> None:
        now = time.monotonic()
        stale = [k for k, t in self.timestamps.items() if now - t > self.ttl]
        for k in stale:
            self.entries.pop(k, None)
            self.timestamps.pop(k, None)

    async def write(
        self, thread_id: str, type: EntryType, content: str, sub_agent_id: str = ""
    ) -> None:
        self.reap_stale()

        ts = datetime.now(timezone.utc).isoformat()
        embedding = None
        if self.embedder:
            embedding = await self.embedder.embed(content)

        entry = LogEntry(
            thread_id=thread_id,
            type=type,
            content=content,
            timestamp=ts,
            sub_agent_id=sub_agent_id,
            embedding=embedding,
        )

        self.entries.setdefault(thread_id, []).append(entry)
        self.touch(thread_id)

    async def write_batch(self, thread_id: str, items: list[LogEntry]) -> None:
        if not items:
            return
        self.reap_stale()
        self.entries.setdefault(thread_id, []).extend(items)
        self.touch(thread_id)

    async def read(self, thread_id: str) -> list[LogEntry]:
        self.reap_stale()
        return list(self.entries.get(thread_id, []))

    async def read_by_type(self, thread_id: str, entry_type: EntryType) -> list[LogEntry]:
        entries = await self.read(thread_id)
        return [e for e in entries if e.type == entry_type]

    async def read_messages(self, thread_id: str) -> list[LogEntry]:
        entries = await self.read(thread_id)
        return [
            e for e in entries if e.type in (EntryType.MESSAGE_USER, EntryType.MESSAGE_ASSISTANT)
        ]

    async def read_for_sub_agent(self, thread_id: str, sub_agent_id: str) -> list[LogEntry]:
        entries = await self.read(thread_id)
        return [e for e in entries if e.sub_agent_id == sub_agent_id]

    async def read_all(self) -> list[LogEntry]:
        self.reap_stale()
        all_items: list[LogEntry] = []
        for entries in self.entries.values():
            all_items.extend(entries)
        all_items.sort(key=lambda it: it.timestamp)
        return all_items

    async def search(self, query: str) -> list[LogEntry]:
        self.reap_stale()

        all_items: list[LogEntry] = []
        for entries in self.entries.values():
            all_items.extend(entries)

        if not all_items:
            return []

        query_embedding = (await self.embedder.embed(query)) if self.embedder else None

        scored: list[tuple[float, LogEntry]] = []
        for item in all_items:
            score = hybrid_score(query, item.content, query_embedding, item.embedding)
            if score > 0.0:
                scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]

    async def get_context_summary(
        self, max_items: int = 10, thread_id: str | None = None, max_tokens: int | None = None
    ) -> str:
        from src.memory.wm_formatting import format_context_summary

        if thread_id is not None:
            items = await self.read(thread_id)
        else:
            items = await self.read_all()
        return format_context_summary(items, max_items=max_items, max_tokens=max_tokens)

    async def clear_task(self, thread_id: str) -> None:
        self.entries.pop(thread_id, None)
        self.timestamps.pop(thread_id, None)
