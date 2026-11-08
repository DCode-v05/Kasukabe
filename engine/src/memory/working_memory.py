"""Temporal event log — the single source of truth for task history.

Every entry has a type, timestamp, and content. Entries are stored in
temporal order under a task ID. The log stores real messages, tool calls,
User-input interactions, checkpoints, and system events — not summaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class EntryType(str, Enum):
    MESSAGE_USER = "message:user"
    MESSAGE_ASSISTANT = "message:assistant"
    TOOL_CALL = "tool:call"
    TOOL_RESULT = "tool:result"
    USER_INPUT_REQUEST = "input:request"
    USER_INPUT_RESPONSE = "input:response"
    ACTION_USER = "action:user"
    CHECKPOINT = "checkpoint"
    SYSTEM_EVENT = "system:event"


@dataclass
class LogEntry:
    thread_id: str
    type: EntryType
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sub_agent_id: str = ""
    embedding: list[float] | None = None
