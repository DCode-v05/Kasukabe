"""Drift tripwire: the conversation working set reaps on ONE clock.

Conversations, working memory (both backends), and ingestion chunks
cross-reference each other; their retention windows must be the same value
by construction, not by coincidence. If any module reintroduces a local
literal, this fails.
"""

from __future__ import annotations


def test_retention_constants_are_one_value():
    from src.engine_core.conversation_store import CONVERSATION_TTL_SECONDS
    from src.memory.inmemory_working_memory import TTL_SECONDS
    from src.memory.sqlite_working_memory import WM_TTL_SECONDS

    assert CONVERSATION_TTL_SECONDS == WM_TTL_SECONDS == TTL_SECONDS


def test_retention_window_is_72h():
    from src.engine_core.conversation_store import CONVERSATION_TTL_SECONDS

    assert CONVERSATION_TTL_SECONDS == 259200
