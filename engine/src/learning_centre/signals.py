"""Learning signals — deterministic capture of conversation exchanges.

The post-turn hook in thread.py captures a model-summarized signal after
every agent loop turn. No voluntary tool call — every exchange with
substance (input >= 20 chars) gets captured deterministically.

Signals are stored in learning_signals table, searchable via FTS5,
and processed by the LC batch pipeline into episodes, knowledge, and
social graph entries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.security.write_guard import guard_content

if TYPE_CHECKING:
    from src.infra.sqlite_pool import SqlitePool

logger = logging.getLogger(__name__)

SIGNAL_TYPES = frozenset(
    {
        "event",
        "fact",
        "preference",
        "person",
        "correction",
        "relationship",
        "approach",
        "outcome",
        "tool_call",
        "tool_result",
        "user_input_request",
        "user_input_response",
        "system_event",
        "checkpoint",
    }
)

SIGNAL_SOURCES = frozenset({"user", "engine", "wm_flush"})


@dataclass
class LearningSignal:
    type: str
    source: str
    content: str
    subject: str = ""
    temporal_ref: str | None = None
    thread_id: str = ""


class SignalStore:
    def __init__(self, db: "SqlitePool") -> None:
        self.db = db

    async def write(self, signal: LearningSignal) -> int:
        guard_content(
            (signal.subject or "") + "\n" + (signal.content or ""),
            where="signal-ingest",
        )
        cursor = await self.db.execute(
            "INSERT INTO learning_signals "
            "(type, source, subject, content, temporal_ref, thread_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                signal.type,
                signal.source,
                signal.subject,
                signal.content,
                signal.temporal_ref,
                signal.thread_id,
            ),
        )
        await self.db.commit()
        return cursor.lastrowid or 0

    async def write_batch(self, signals: list[LearningSignal]) -> int:
        for sig in signals:
            guard_content(
                (sig.subject or "") + "\n" + (sig.content or ""),
                where="signal-ingest-batch",
            )
        stored = 0
        for sig in signals:
            await self.db.execute(
                "INSERT INTO learning_signals "
                "(type, source, subject, content, temporal_ref, thread_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sig.type, sig.source, sig.subject, sig.content, sig.temporal_ref, sig.thread_id),
            )
            stored += 1
        await self.db.commit()
        return stored

    async def pull_unprocessed(self) -> list[dict]:
        rows = await self.db.fetch(
            "SELECT id, type, source, subject, content, temporal_ref, "
            "thread_id, created_at FROM learning_signals "
            "WHERE processed = 0 ORDER BY created_at",
        )
        return [dict(r) for r in rows]

    async def mark_processed(self, signal_ids: list[int], batch_id: str) -> None:
        if not signal_ids:
            return
        placeholders = ",".join("?" * len(signal_ids))
        await self.db.execute(
            f"UPDATE learning_signals SET processed = 1, batch_id = ? WHERE id IN ({placeholders})",
            (batch_id, *signal_ids),
        )
        await self.db.commit()

    async def unmark_processed(self, batch_id: str) -> None:
        await self.db.execute(
            "UPDATE learning_signals SET processed = 0, batch_id = NULL WHERE batch_id = ?",
            (batch_id,),
        )
        await self.db.commit()

    async def delete_processed(self) -> int:
        """DEPRECATED, and dangerous once more than one batch can be in
        flight. Use `delete_batch(batch_id)`.

        `WHERE processed = 1` is every claimed row in the table, not this
        batch's. Two batches almost never overlapped at the old six-hour
        interval, so the flag effectively identified one batch and this
        was safe by accident. The learning-cadence spec named it first in
        its plan for that reason - *"delete_batch ends the cross-batch
        deletion. This is the one change that must land BEFORE the cadence
        change, not with it."*

        The cadence change landed without it. `SimLearningGate` now runs a
        batch every 18 waking ticks and `trigger_extra()` runs another at
        end-of-run, so a drain firing while a waking batch is mid-encode
        deletes the waking batch's claimed rows before they are written -
        silently, with no error and no log line. Kept only so an
        out-of-tree caller does not break at import.
        """
        count_row = await self.db.fetchval(
            "SELECT count(*) FROM learning_signals WHERE processed = 1",
        )
        count = count_row or 0
        await self.db.execute(
            "DELETE FROM learning_signals WHERE processed = 1",
        )
        await self.db.commit()
        return count

    async def delete_batch(self, batch_id: str) -> int:
        """Delete only the rows THIS batch claimed.

        AUTHORISED LAW 1 EXCEPTION (2026-09-10, operator-directed; see
        docs/policy/LAW-1-MANIFEST.md). No seam can re-scope somebody
        else's `DELETE` statement, and the statement being replaced ran
        `WHERE processed = 1` — every row claimed by any batch.

        The other half of `mark_processed`/`unmark_processed`, which have
        both been batch-scoped since they were written - only the delete
        was not, and the delete is the irreversible one.

        An empty `batch_id` deletes nothing and says so, rather than
        falling back to the flag: a caller that lost its batch id must not
        take the whole table with it.
        """
        if not batch_id:
            logger.warning("delete_batch called with no batch_id - nothing deleted")
            return 0
        count_row = await self.db.fetchval(
            "SELECT count(*) FROM learning_signals WHERE batch_id = ?",
            (batch_id,),
        )
        count = count_row or 0
        await self.db.execute(
            "DELETE FROM learning_signals WHERE batch_id = ?",
            (batch_id,),
        )
        await self.db.commit()
        return count

    async def search_fts(self, query: str, limit: int = 20) -> list[dict]:
        rows = await self.db.fetch(
            "SELECT ls.id, ls.type, ls.source, ls.subject, ls.content, "
            "ls.temporal_ref, ls.thread_id, ls.created_at "
            "FROM learning_signals ls "
            "JOIN learning_signals_fts fts ON ls.id = fts.rowid "
            "WHERE learning_signals_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (query, limit),
        )
        return [dict(r) for r in rows]

    async def get_by_task(self, thread_id: str) -> list[dict]:
        rows = await self.db.fetch(
            "SELECT id, type, source, subject, content, temporal_ref, created_at "
            "FROM learning_signals WHERE thread_id = ? ORDER BY created_at",
            (thread_id,),
        )
        return [dict(r) for r in rows]

    async def flush_wm(self, thread_id: str, wm_items: list, status: str = "completed") -> int:
        """Flush WM execution data to learning_signals.

        Only flushes: tool calls, tool results, user input, system events, checkpoints.
        User and assistant messages are NOT flushed — the post-turn hook handles those.
        """
        from src.memory.working_memory import EntryType

        TYPE_MAP = {
            EntryType.TOOL_CALL: "tool_call",
            EntryType.TOOL_RESULT: "tool_result",
            EntryType.USER_INPUT_REQUEST: "user_input_request",
            EntryType.USER_INPUT_RESPONSE: "user_input_response",
            EntryType.ACTION_USER: "user_input_response",
            EntryType.SYSTEM_EVENT: "system_event",
            EntryType.CHECKPOINT: "checkpoint",
        }
        SKIP = {EntryType.MESSAGE_USER, EntryType.MESSAGE_ASSISTANT}

        stored = 0
        for item in wm_items:
            if item.type in SKIP:
                continue
            sig_type = TYPE_MAP.get(item.type)
            if sig_type is None:
                continue

            source = "user" if item.type == EntryType.USER_INPUT_RESPONSE else "wm_flush"
            subject = ""
            content = item.content or ""

            if item.type in (EntryType.TOOL_CALL, EntryType.TOOL_RESULT):
                try:
                    import json

                    parsed = json.loads(content)
                    subject = parsed.get("name", parsed.get("tool_name", ""))
                except (ValueError, TypeError, AttributeError):
                    pass

            guard_content(
                subject + "\n" + content,
                where="signal-ingest-flush-wm",
            )

            await self.db.execute(
                "INSERT INTO learning_signals "
                "(type, source, subject, content, thread_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (sig_type, source, subject, content[:10000], thread_id),
            )
            stored += 1

        await self.db.execute(
            "INSERT INTO learning_signals "
            "(type, source, subject, content, thread_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("outcome", "wm_flush", "task_completion", f"Task {status}", thread_id),
        )
        stored += 1
        await self.db.commit()
        return stored
