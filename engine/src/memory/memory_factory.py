"""Memory backend abstraction — Protocol and factory.

WorkingMemoryBackend is the Protocol all backends implement.
create_working_memory() is the factory that returns the right backend.

Feature flag: USE_INMEMORY_WM=true opts into the in-memory backend.
Without it, SQLite is required and the system hard-fails at boot if unavailable.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from src.memory.working_memory import LogEntry, EntryType

if TYPE_CHECKING:
    from src.infra.sqlite_pool import SqlitePool

USE_INMEMORY_WM: bool = os.environ.get("USE_INMEMORY_WM", "").lower() in ("1", "true")


@runtime_checkable
class WorkingMemoryBackend(Protocol):
    """Async interface for temporal log backends.

    Consumers type-hint against this, never against a concrete class.
    """

    @property
    def session_id(self) -> str: ...

    async def write(
        self, thread_id: str, type: EntryType, content: str, sub_agent_id: str = ""
    ) -> None: ...

    async def write_batch(self, thread_id: str, items: list[LogEntry]) -> None: ...

    async def read(self, thread_id: str) -> list[LogEntry]: ...

    async def read_by_type(self, thread_id: str, entry_type: EntryType) -> list[LogEntry]: ...

    async def read_messages(self, thread_id: str) -> list[LogEntry]: ...

    async def read_for_sub_agent(self, thread_id: str, sub_agent_id: str) -> list[LogEntry]: ...

    async def read_all(self) -> list[LogEntry]: ...

    async def search(self, query: str) -> list[LogEntry]: ...

    async def get_context_summary(
        self, max_items: int = 10, thread_id: str | None = None, max_tokens: int | None = None
    ) -> str: ...

    async def clear_task(self, thread_id: str) -> None: ...


def create_working_memory(
    session_id: str,
    embedder: object | None = None,
    db: object | None = None,
) -> WorkingMemoryBackend:
    """Factory: create the appropriate working memory backend.

    Default: SqliteWorkingMemory (requires db).
    Feature flag USE_INMEMORY_WM=true: InMemoryWorkingMemory (no db needed).
    """
    if USE_INMEMORY_WM:
        from src.memory.inmemory_working_memory import InMemoryWorkingMemory

        return InMemoryWorkingMemory(session_id, embedding_service=embedder)  # type: ignore[arg-type]
    if db is None:
        raise RuntimeError(
            "SQLite database required for working memory. "
            "Set USE_INMEMORY_WM=true to use in-memory backend."
        )
    from src.memory.sqlite_working_memory import SqliteWorkingMemory

    return SqliteWorkingMemory(cast("SqlitePool", db), session_id, embedding_service=embedder)
