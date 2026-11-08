"""SQLite-backed Working Memory backend.

Write-through: every write commits immediately for crash safety.
Requires the working_memory_log table (migration 010).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.memory.working_memory import LogEntry, EntryType

if TYPE_CHECKING:
    from src.infra.sqlite_pool import SqlitePool

logger = logging.getLogger(__name__)

# Alias of the canonical conversation-working-set retention window (see the
# comment on CONVERSATION_TTL_SECONDS): WM entries belong to conversations
# and must never be reaped on a different clock.
from src.engine_core.conversation_store import CONVERSATION_TTL_SECONDS

WM_TTL_SECONDS = CONVERSATION_TTL_SECONDS
COLS = "thread_id, entry_type, content, sub_agent_id, timestamp"
COLS_ALIASED = "wm.thread_id, wm.entry_type, wm.content, wm.sub_agent_id, wm.timestamp"


def row_to_entry(row) -> LogEntry:
    return LogEntry(
        thread_id=row["thread_id"],
        type=EntryType(row["entry_type"]),
        content=row["content"],
        timestamp=row["timestamp"],
        sub_agent_id=row["sub_agent_id"] or "",
    )


class SqliteWorkingMemory:
    """SQLite-backed temporal log implementing WorkingMemoryBackend Protocol."""

    def __init__(
        self,
        db: "SqlitePool",
        session_id: str,
        *,
        embedding_service: object | None = None,
    ) -> None:
        self.db = db
        self.session_id = session_id
        self.embedding_service = embedding_service

    async def write(
        self,
        thread_id: str,
        type: EntryType,
        content: str,
        sub_agent_id: str = "",
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """INSERT INTO working_memory_log
               (thread_id, entry_type, content, sub_agent_id, timestamp, session_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (thread_id, type.value, content, sub_agent_id, ts, self.session_id),
        )
        await self.db.commit()

    async def write_batch(self, thread_id: str, items: list[LogEntry]) -> None:
        if not items:
            return
        params = [
            (
                item.thread_id or thread_id,
                item.type.value,
                item.content,
                item.sub_agent_id,
                item.timestamp or datetime.now(timezone.utc).isoformat(),
                self.session_id,
            )
            for item in items
        ]
        await self.db.executemany(
            """INSERT INTO working_memory_log
               (thread_id, entry_type, content, sub_agent_id, timestamp, session_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            params,
        )
        await self.db.commit()

    async def read(self, thread_id: str) -> list[LogEntry]:
        rows = await self.db.fetch(
            f"""SELECT {COLS}
               FROM working_memory_log
               WHERE thread_id = ? AND session_id = ?
               ORDER BY id""",
            (thread_id, self.session_id),
        )
        return [row_to_entry(r) for r in rows]

    async def read_by_type(self, thread_id: str, entry_type: EntryType) -> list[LogEntry]:
        rows = await self.db.fetch(
            f"""SELECT {COLS}
               FROM working_memory_log
               WHERE thread_id = ? AND entry_type = ? AND session_id = ?
               ORDER BY id""",
            (thread_id, entry_type.value, self.session_id),
        )
        return [row_to_entry(r) for r in rows]

    async def read_messages(self, thread_id: str) -> list[LogEntry]:
        rows = await self.db.fetch(
            f"""SELECT {COLS}
               FROM working_memory_log
               WHERE thread_id = ? AND session_id = ?
                 AND entry_type IN (?, ?)
               ORDER BY id""",
            (
                thread_id,
                self.session_id,
                EntryType.MESSAGE_USER.value,
                EntryType.MESSAGE_ASSISTANT.value,
            ),
        )
        return [row_to_entry(r) for r in rows]

    async def read_for_sub_agent(self, thread_id: str, sub_agent_id: str) -> list[LogEntry]:
        rows = await self.db.fetch(
            f"""SELECT {COLS}
               FROM working_memory_log
               WHERE thread_id = ? AND sub_agent_id = ? AND session_id = ?
               ORDER BY id""",
            (thread_id, sub_agent_id, self.session_id),
        )
        return [row_to_entry(r) for r in rows]

    async def read_all(self) -> list[LogEntry]:
        rows = await self.db.fetch(
            f"""SELECT {COLS}
               FROM working_memory_log
               WHERE session_id = ?
               ORDER BY id""",
            (self.session_id,),
        )
        return [row_to_entry(r) for r in rows]

    async def search(self, query: str) -> list[LogEntry]:
        rows = await self.db.fetch(
            f"""SELECT {COLS_ALIASED}
               FROM working_memory_fts fts
               JOIN working_memory_log wm ON fts.rowid = wm.id
               WHERE fts.content MATCH ? AND wm.session_id = ?
               ORDER BY fts.rank""",
            (query, self.session_id),
        )
        return [row_to_entry(r) for r in rows]

    async def get_context_summary(
        self,
        max_items: int = 10,
        thread_id: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        from src.memory.wm_formatting import format_context_summary

        if thread_id is not None:
            items = await self.read(thread_id)
        else:
            items = await self.read_all()
        return format_context_summary(items, max_items=max_items, max_tokens=max_tokens)

    async def clear_task(self, thread_id: str) -> None:
        await self.db.execute(
            "DELETE FROM working_memory_log WHERE thread_id = ? AND session_id = ?",
            (thread_id, self.session_id),
        )
        await self.db.commit()

    async def cleanup_expired(self, ttl_seconds: int = WM_TTL_SECONDS) -> int:
        cutoff = time.time() - ttl_seconds
        cursor = await self.db.execute(
            "DELETE FROM working_memory_log WHERE created_at < ?",
            (cutoff,),
        )
        await self.db.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info("Cleaned up %d expired WM entries (TTL=%ds)", deleted, ttl_seconds)
        return deleted
