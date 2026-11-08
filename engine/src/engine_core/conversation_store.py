"""Conversation persistence - SQLite-backed message history for persistent threads.

Each thread has one row in `conversations` (metadata) and N rows in
`conversation_messages` (individual messages in sequence order). Both tables
are deleted after 24h TTL - episodic memory is the long-term store.

ConversationStore is the single owner of these tables. Thread and coordinator
call through this interface; they never touch the tables directly.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.infra.sqlite_pool import SqlitePool

logger = logging.getLogger(__name__)

# 72 hours - after this, conversations are deleted (episodic memory is
# long-term). Raised from 24h so a weekend-spanning session survives the
# sweep. Not a liveness TTL (no heartbeat feeds it), so the ratio principle
# in src/config.py does not apply; this is a retention policy.
#
# CANONICAL retention window for the whole conversation working set:
# conversations, working memory (sqlite + in-memory), ingestion chunks, and
# tool-result content all import THIS constant. They cross-reference each
# other (WM entries and chunks belong to conversations; recall reads across
# all of them), so a drift between their windows serves rows whose parent
# thread was already reaped - that bug class has occurred before
# (resume_state.py: "search_content reported matches from TTL-expired
# entries"). Change the value here and every store moves together.
CONVERSATION_TTL_SECONDS = 259200


class ConversationPersistenceError(RuntimeError):
    """Raised when a conversation write could not be persisted.

    Distinct from the metadata writers, which log and swallow because the next
    turn rewrites them wholesale. Message writes and sequence reads have no
    such repair path.
    """


class ConversationStore:
    """SQLite CRUD for persistent thread conversations."""

    def __init__(self, db: "SqlitePool") -> None:
        self.db = db

    # ── Write operations ──────────────────────────────────────────

    async def ensure_conversation(
        self,
        thread_id: str,
    ) -> None:
        """Create a conversation row if it doesn't exist."""
        try:
            await self.db.execute(
                """INSERT OR IGNORE INTO conversations (thread_id)
                   VALUES (?)""",
                (thread_id,),
            )
            await self.db.commit()
        except Exception as e:
            logger.warning("conversation ensure failed for %s: %s", thread_id, e)

    async def save_message(
        self,
        thread_id: str,
        seq: int,
        role: str,
        content_json: str,
    ) -> None:
        """Append one message to a thread's history. Retries once, then RAISES.

        Unlike the metadata writers here, a dropped message does NOT self-heal:
        the next turn appends at the following seq and the hole is permanent, so
        the history ends up holding an assistant reply with no preceding user
        message. Swallowing the failure would also let the caller write the
        second half of a turn after the first half was lost. The caller must
        hear about it, so the final failed attempt re-raises.
        """
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                await self.db.execute(
                    """INSERT INTO conversation_messages (thread_id, seq, role, content_json)
                       VALUES (?, ?, ?, ?)""",
                    (thread_id, seq, role, content_json),
                )
                await self.db.execute(
                    """UPDATE conversations
                       SET turn_count = turn_count + 1,
                           last_activity = datetime('now')
                       WHERE thread_id = ?""",
                    (thread_id,),
                )
                await self.db.commit()
                return
            except Exception as e:
                last_error = e
                if attempt == 0:
                    logger.warning("save_message retry for %s seq=%d: %s", thread_id, seq, e)
                else:
                    logger.critical(
                        "save_message FAILED for %s seq=%d after retry: %s",
                        thread_id,
                        seq,
                        e,
                    )
        raise ConversationPersistenceError(
            f"save_message failed for {thread_id} seq={seq} after retry: {last_error}"
        ) from last_error

    async def save_metadata(
        self,
        thread_id: str,
        *,
        charter_json: str | None = None,
        scratchpad_json: str | None = None,
        lc_watermark: int | None = None,
    ) -> None:
        """Update conversation metadata (charter, scratchpad, watermark).

        Note: ``task_index_json`` is no longer accepted - thread-routing
        fields (summary, current_state, recent_turns) live in their own
        columns now and are written via :meth:`save_thread_index`.
        """
        parts: list[str] = []
        params: list[Any] = []
        if charter_json is not None:
            parts.append("charter_json = ?")
            params.append(charter_json)
        if scratchpad_json is not None:
            parts.append("scratchpad_json = ?")
            params.append(scratchpad_json)
        if lc_watermark is not None:
            parts.append("lc_watermark = ?")
            params.append(lc_watermark)
        if not parts:
            return
        parts.append("last_activity = datetime('now')")
        params.append(thread_id)
        try:
            await self.db.execute(
                f"UPDATE conversations SET {', '.join(parts)} WHERE thread_id = ?",
                tuple(params),
            )
            await self.db.commit()
        except Exception as e:
            logger.warning("save_metadata failed for %s: %s", thread_id, e)

    async def save_thread_index(
        self,
        thread_id: str,
        *,
        summary: str,
        current_state: str,
        recent_turns: list[str],
    ) -> None:
        """Persist thread-routing fields per turn so they survive restart.

        These three fields back the LLM router's task-index. They live in
        dedicated columns (migration 015) so the schema documents intent
        and the router can read them straight without JSON parsing.

        ``recent_turns`` is bounded (≤3 short strings) and stored as a JSON
        array because SQLite has no native list type; a relational child
        table would be over-engineered for the size.

        Failure is logged and swallowed (matches ``save_metadata`` /
        ``save_message``): the in-memory ``ThreadIndexEntry`` is still the
        source of truth this turn, and the next turn fully overwrites all
        three fields - a missed write self-heals.
        """
        try:
            await self.db.execute(
                """UPDATE conversations
                   SET summary = ?,
                       current_state = ?,
                       recent_turns = ?,
                       last_activity = datetime('now')
                   WHERE thread_id = ?""",
                (summary, current_state, json.dumps(recent_turns), thread_id),
            )
            await self.db.commit()
        except Exception as e:
            logger.warning("save_thread_index failed for %s: %s", thread_id, e)

    async def set_pinned(self, thread_id: str, pinned: bool) -> None:
        """Persist the pin flag. Deliberately does NOT touch last_activity -
        pinning is ownership, not activity; a pinned thread's idle clock only
        matters again after unpin, and it should reflect real turns.

        Unlike the other writers here, this one RAISES on failure instead of
        logging and swallowing. Those writes self-heal (the next turn rewrites
        summary/metadata wholesale); a pin does not - there is no next turn to
        repair it, and a silently-dropped pin means the reaper deletes work the
        product layer claimed. The caller must hear about it.
        """
        await self.db.execute(
            "UPDATE conversations SET pinned = ? WHERE thread_id = ?",
            (pinned, thread_id),
        )
        await self.db.commit()

    async def is_pinned(self, thread_id: str) -> bool:
        """Read the pin flag. Missing rows are unpinned."""
        try:
            val = await self.db.fetchval(
                "SELECT pinned FROM conversations WHERE thread_id = ?",
                (thread_id,),
            )
            return bool(val)
        except Exception as e:
            logger.warning("is_pinned failed for %s: %s", thread_id, e)
            return False

    # ── Read operations ───────────────────────────────────────────

    async def load_messages(self, thread_id: str) -> list[dict]:
        """Load all messages for a thread in sequence order.

        Returns list of {"seq": int, "role": str, "content": ..., "created_at": str}.
        ``created_at`` is the SQLite-stored ISO timestamp (UTC). Callers that
        rebuild ``LLMMessage`` objects parse it to a float for the
        ``Message.timestamp`` field so the time-based microcompact trigger
        can detect idle gaps across session resumes.
        Skips corrupt JSON entries (partial recovery).
        """
        try:
            rows = await self.db.fetch(
                """SELECT seq, role, content_json, created_at
                   FROM conversation_messages
                   WHERE thread_id = ?
                   ORDER BY seq""",
                (thread_id,),
            )
        except Exception as e:
            logger.warning("load_messages failed for %s: %s", thread_id, e)
            return []

        messages: list[dict] = []
        for row in rows:
            try:
                content = json.loads(row["content_json"])
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "corrupt message in %s seq=%d - skipping",
                    thread_id,
                    row["seq"],
                )
                continue
            messages.append(
                {
                    "seq": row["seq"],
                    "role": row["role"],
                    "content": content,
                    "created_at": row["created_at"],
                }
            )
        return messages

    async def next_seq(self, thread_id: str) -> int:
        """Return the sequence number the next message must use.

        Derived from ``MAX(seq)`` in the database rather than from whatever
        :meth:`load_messages` handed back. Two things make that necessary:
        ``load_messages`` returns ``[]`` on a read error, and it deliberately
        skips rows whose ``content_json`` is corrupt. Either would rewind the
        counter onto seq values that already exist, and ``UNIQUE(thread_id,
        seq)`` would then reject every subsequent write.

        Raises on a read failure instead of guessing 0 - a guessed sequence
        collides, and a collision is exactly the failure this method exists to
        prevent.
        """
        try:
            val = await self.db.fetchval(
                "SELECT MAX(seq) FROM conversation_messages WHERE thread_id = ?",
                (thread_id,),
            )
        except Exception as e:
            raise ConversationPersistenceError(
                f"next_seq failed for {thread_id}: {e}"
            ) from e
        return 0 if val is None else int(val) + 1

    async def load_conversation(self, thread_id: str) -> dict | None:
        """Load full conversation state (metadata + messages) for crash recovery."""
        try:
            meta = await self.db.fetchrow(
                "SELECT * FROM conversations WHERE thread_id = ?",
                (thread_id,),
            )
        except Exception as e:
            logger.warning("load_conversation metadata failed for %s: %s", thread_id, e)
            return None

        if meta is None:
            return None

        messages = await self.load_messages(thread_id)

        charter = None
        if meta["charter_json"]:
            try:
                charter = json.loads(meta["charter_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        scratchpad = {}
        if meta["scratchpad_json"]:
            try:
                scratchpad = json.loads(meta["scratchpad_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Routing fields live in dedicated columns (migration 015).
        # recent_turns is a JSON-encoded list; corrupt JSON degrades to [].
        try:
            recent_turns = json.loads(meta["recent_turns"]) if meta["recent_turns"] else []
            if not isinstance(recent_turns, list):
                recent_turns = []
        except (json.JSONDecodeError, TypeError):
            recent_turns = []

        thread_index = {
            "summary": meta["summary"] or "",
            "current_state": meta["current_state"] or "",
            "recent_turns": recent_turns,
        }

        return {
            "thread_id": meta["thread_id"],
            "charter": charter,
            "scratchpad": scratchpad,
            "thread_index": thread_index,
            "turn_count": meta["turn_count"],
            "lc_watermark": meta["lc_watermark"],
            "created_at": meta["created_at"],
            "last_activity": meta["last_activity"],
            "pinned": bool(meta["pinned"]),
            "messages": messages,
        }

    async def list_active(self) -> list[dict]:
        """List all active conversations (not yet expired)."""
        try:
            rows = await self.db.fetch(
                """SELECT thread_id, turn_count, lc_watermark, created_at, last_activity,
                          summary, current_state, recent_turns, pinned
                   FROM conversations
                   ORDER BY last_activity DESC""",
            )
        except Exception as e:
            logger.warning("list_active failed: %s", e)
            return []

        result: list[dict] = []
        for row in rows:
            try:
                recent_turns = json.loads(row["recent_turns"]) if row["recent_turns"] else []
                if not isinstance(recent_turns, list):
                    recent_turns = []
            except (json.JSONDecodeError, TypeError):
                recent_turns = []
            result.append(
                {
                    "thread_id": row["thread_id"],
                    "turn_count": row["turn_count"],
                    "lc_watermark": row["lc_watermark"],
                    "created_at": row["created_at"],
                    "last_activity": row["last_activity"],
                    "pinned": bool(row["pinned"]),
                    "thread_index": {
                        "summary": row["summary"] or "",
                        "current_state": row["current_state"] or "",
                        "recent_turns": recent_turns,
                    },
                }
            )
        return result

    # ── Cleanup operations ────────────────────────────────────────

    async def delete_thread(self, thread_id: str) -> None:
        """Delete a conversation and all its messages (CASCADE)."""
        try:
            await self.db.execute(
                "DELETE FROM conversations WHERE thread_id = ?",
                (thread_id,),
            )
            await self.db.commit()
            logger.info("Deleted conversation %s", thread_id)
        except Exception as e:
            logger.warning("delete_thread failed for %s: %s", thread_id, e)

    async def cleanup_expired(self, ttl_seconds: int = CONVERSATION_TTL_SECONDS) -> int:
        """Delete conversations older than TTL. Pinned threads are exempt -
        they belong to the product layer and only expire after unpin.
        Returns count deleted."""
        try:
            cursor = await self.db.execute(
                """DELETE FROM conversations
                   WHERE last_activity < datetime('now', ? || ' seconds')
                     AND pinned = FALSE""",
                (f"-{ttl_seconds}",),
            )
            await self.db.commit()
            deleted = cursor.rowcount
            if deleted:
                logger.info("Cleaned up %d expired conversations (TTL=%ds)", deleted, ttl_seconds)
            return deleted
        except Exception as e:
            logger.warning("cleanup_expired failed: %s", e)
            return 0

    async def message_count(self, thread_id: str) -> int:
        """Return the number of messages in a thread."""
        try:
            val = await self.db.fetchval(
                "SELECT COUNT(*) FROM conversation_messages WHERE thread_id = ?",
                (thread_id,),
            )
            return val or 0
        except Exception:
            return 0
