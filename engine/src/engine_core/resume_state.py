"""Channel State - SQLite-backed execution checkpoint for retry/resume.

ResumeState is a Pydantic model capturing the full execution snapshot at
checkpoint time. ResumeStateManager provides SQLite CRUD with TTL-based
expiration. Survives container restarts for paused (awaiting-input) turns.

Content store also backed by SQLite for persistence.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

RESUME_STATE_TTL_ACTIVE = 10800
RESUME_STATE_TTL_INPUT = 259200
CONTENT_MAX_CHARS = 200_000
CONTENT_MAX_PER_SESSION = 100


class ResumeState(BaseModel):
    """Execution checkpoint - everything needed to resume a charter."""

    thread_id: str = ""
    charter_id: str
    charter_version: int = 1
    charter_json: dict[str, Any] | None = None
    node_status: dict[str, str] = Field(default_factory=dict)
    partial_results: dict[str, str] = Field(default_factory=dict)
    pending_input: dict[str, Any] | None = None
    scratchpad_snapshot: dict[str, Any] = Field(default_factory=dict)
    coordinator_state: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ResumeStateManager:
    """SQLite CRUD for resume state checkpoints."""

    def __init__(self, db: Any | None) -> None:
        self.db = db
        if db is None:
            logger.warning(
                "ResumeStateManager: SQLite not configured - "
                "checkpoints disabled, tasks cannot be resumed",
            )

    async def write_checkpoint(
        self,
        state: ResumeState,
        ttl: int = RESUME_STATE_TTL_ACTIVE,
        pinned: bool = False,
    ) -> None:
        """Persist a checkpoint. ``pinned=True`` marks it exempt from expiry:
        read and cleanup honor ttl_type='pinned' regardless of expires_at, so
        a pinned thread's parked question stays answerable weeks later."""
        if self.db is None:
            return
        try:
            state.updated_at = datetime.now(timezone.utc).isoformat()
            expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if pinned:
                ttl_type = "pinned"
            else:
                ttl_type = "input" if ttl == RESUME_STATE_TTL_INPUT else "active"
            await self.db.execute(
                """INSERT OR REPLACE INTO resume_state
                   (charter_id, state_json, ttl_type, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (state.charter_id, state.model_dump_json(), ttl_type, expires),
            )
            await self.db.commit()
        except Exception as e:
            logger.warning("resume_state write failed for %s: %s", state.charter_id, e)

    async def read_checkpoint(self, charter_id: str) -> ResumeState | None:
        if self.db is None:
            return None
        try:
            row = await self.db.fetchrow(
                """SELECT state_json FROM resume_state
                   WHERE charter_id = ?
                     AND (expires_at > datetime('now') OR ttl_type = 'pinned')""",
                (charter_id,),
            )
            if row is None:
                return None
            return ResumeState.model_validate_json(row["state_json"])
        except Exception as e:
            logger.warning("resume_state read failed for %s: %s", charter_id, e)
            return None

    async def delete_checkpoint(self, charter_id: str) -> None:
        if self.db is None:
            return
        try:
            await self.db.execute(
                "DELETE FROM resume_state WHERE charter_id = ?",
                (charter_id,),
            )
            await self.db.commit()
        except Exception as e:
            logger.warning("resume_state delete failed for %s: %s", charter_id, e)

    async def write_input_checkpoint(self, state: ResumeState) -> None:
        await self.write_checkpoint(state, ttl=RESUME_STATE_TTL_INPUT)

    async def cleanup_expired(self) -> None:
        """Remove expired channel states and content entries."""
        if self.db is None:
            return
        try:
            await self.db.execute(
                "DELETE FROM resume_state"
                " WHERE expires_at <= datetime('now') AND ttl_type != 'pinned'"
            )
            await self.db.commit()
        except Exception as e:
            logger.warning("resume_state cleanup failed: %s", e)


    async def store_content(
        self,
        thread_id: str,
        content_id: str,
        title: str,
        content: str,
        source: str = "craft",
        metadata: dict | None = None,
    ) -> None:
        if self.db is None:
            return

        if len(content) > CONTENT_MAX_CHARS:
            content = content[:CONTENT_MAX_CHARS] + "\n\n[Content truncated at storage limit]"

        try:
            rows = await self.db.fetch(
                "SELECT COUNT(*) as cnt FROM resume_state WHERE charter_id LIKE ? AND ttl_type = 'content'",
                (f"content:{thread_id}:%",),
            )
            if rows and rows[0]["cnt"] >= CONTENT_MAX_PER_SESSION:
                logger.info(
                    "Content store: max entries (%d) reached for task %s, skipping",
                    CONTENT_MAX_PER_SESSION,
                    thread_id,
                )
                return
        except Exception:
            pass

        entry = json.dumps(
            {
                "content_id": content_id,
                "title": title,
                "source": source,
                "content": content,
                "mime_type": "text/plain",
                "size_tokens": max(1, len(content) // 4),
                "created_at": time.time(),
                "metadata": metadata or {},
            },
            ensure_ascii=False,
        )
        from src.engine_core.conversation_store import CONVERSATION_TTL_SECONDS
        from src.engine_core.context_engineering import SOURCE_TOOL_RESULT

        ttl = CONVERSATION_TTL_SECONDS if source == SOURCE_TOOL_RESULT else RESUME_STATE_TTL_ACTIVE
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        try:
            await self.db.execute(
                """INSERT OR REPLACE INTO resume_state
                   (charter_id, state_json, ttl_type, expires_at)
                   VALUES (?, ?, 'content', ?)""",
                (f"content:{thread_id}:{content_id}", entry, expires),
            )
            await self.db.commit()
        except Exception as e:
            logger.warning("Content store write failed: %s", e)

    async def list_content(self, thread_id: str, source: str = "", pattern: str = "") -> list[dict]:
        if self.db is None:
            return []
        entries = []
        try:
            rows = await self.db.fetch(
                "SELECT state_json FROM resume_state WHERE charter_id LIKE ? AND expires_at > datetime('now')",
                (f"content:{thread_id}:%",),
            )
            for row in rows:
                entry = json.loads(row["state_json"])
                if source and entry.get("source") != source:
                    continue
                if pattern and pattern.lower() not in entry.get("title", "").lower():
                    continue
                entries.append(
                    {
                        "content_id": entry["content_id"],
                        "title": entry["title"],
                        "source": entry["source"],
                        "size_tokens": entry.get("size_tokens", 0),
                        "mime_type": entry.get("mime_type", "text/plain"),
                        "preview": entry.get("content", "")[:200],
                    }
                )
        except Exception as e:
            logger.warning("Content store list failed: %s", e)
        return entries

    async def get_content(self, thread_id: str, content_id: str) -> str | None:
        if self.db is None:
            return None
        try:
            row = await self.db.fetchrow(
                "SELECT state_json FROM resume_state WHERE charter_id = ? AND expires_at > datetime('now')",
                (f"content:{thread_id}:{content_id}",),
            )
            if row is None:
                return None
            return json.loads(row["state_json"]).get("content")
        except Exception as e:
            logger.warning("Content store get failed: %s", e)
            return None

    async def search_content(
        self,
        thread_id: str,
        search_pattern: str,
        content_id: str = "",
        context_lines: int = 3,
        output_mode: str = "content",
        max_results: int = 10,
        case_insensitive: bool = True,
        multiline: bool = False,
    ) -> dict:
        if self.db is None:
            return {"matches": [], "total_matches": 0, "searched_entries": 0}

        entries_to_search: list[dict] = []
        try:
            if content_id:
                row = await self.db.fetchrow(
                    "SELECT state_json FROM resume_state WHERE charter_id = ?"
                    " AND expires_at > datetime('now')",
                    (f"content:{thread_id}:{content_id}",),
                )
                if row:
                    entries_to_search.append(json.loads(row["state_json"]))
            else:
                rows = await self.db.fetch(
                    "SELECT state_json FROM resume_state WHERE charter_id LIKE ?"
                    " AND expires_at > datetime('now')",
                    (f"content:{thread_id}:%",),
                )
                for row in rows:
                    entries_to_search.append(json.loads(row["state_json"]))
        except Exception as e:
            logger.warning("Content store search failed: %s", e)
            return {"matches": [], "total_matches": 0, "searched_entries": 0}

        flags = re.IGNORECASE if case_insensitive else 0
        if multiline:
            flags |= re.DOTALL
        try:
            regex = re.compile(search_pattern, flags)
        except re.error:
            return {
                "error": f"Invalid regex pattern: {search_pattern}",
                "matches": [],
                "total_matches": 0,
                "searched_entries": 0,
            }

        if output_mode == "count":
            counts = []
            total = 0
            for entry in entries_to_search:
                text = entry.get("content", "")
                matches = regex.findall(text)
                counts.append(
                    {
                        "content_id": entry["content_id"],
                        "title": entry["title"],
                        "matches": len(matches),
                    }
                )
                total += len(matches)
            return {
                "counts": counts,
                "total_matches": total,
                "searched_entries": len(entries_to_search),
                "truncated": False,
            }

        all_matches: list = []
        truncated = False
        searched = 0
        for entry in entries_to_search:
            searched += 1
            text = entry.get("content", "")
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if regex.search(line):
                    ctx_before = lines[max(0, i - context_lines) : i]
                    ctx_after = lines[i + 1 : i + 1 + context_lines]

                    if output_mode == "sections":
                        sec_start = i
                        while sec_start > 0 and lines[sec_start - 1].strip():
                            sec_start -= 1
                        sec_end = i
                        while sec_end < len(lines) - 1 and lines[sec_end + 1].strip():
                            sec_end += 1
                        all_matches.append(
                            {
                                "content_id": entry["content_id"],
                                "title": entry["title"],
                                "section_content": "\n".join(lines[sec_start : sec_end + 1]),
                                "section_start_line": sec_start,
                                "section_end_line": sec_end,
                            }
                        )
                    else:
                        all_matches.append(
                            {
                                "content_id": entry["content_id"],
                                "title": entry["title"],
                                "line_number": i,
                                "match": line,
                                "context_before": ctx_before,
                                "context_after": ctx_after,
                            }
                        )

                    if len(all_matches) >= max_results:
                        truncated = True
                        break
            if truncated:
                break

        key_name = "sections" if output_mode == "sections" else "matches"
        return {
            key_name: all_matches,
            "total_matches": len(all_matches),
            "returned_matches": len(all_matches),
            "searched_entries": searched,
            "truncated": truncated,
        }
