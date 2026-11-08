"""Real tests for the per-iteration memory-prefetch consume path.

Covers two modules in src/engine_core/thread.py:

- ``build_recall_reminder(bundle)`` — formats a memory prefetch bundle as a
  ``<system-reminder>`` block, or returns ``None`` when nothing matched.
- ``consume_pending_recall(messages, task, consumed, *, message_factory)`` —
  per-iteration consume helper that the agent loop calls. Peeks the task,
  appends a synthetic user-role message when settled, returns updated state.

Why this matters
----------------
The coordinator kicks a memory prefetch task at the top of ``handle_input``
so episodic / knowledge / social recall runs in parallel with the thread
router. ``execute_turn`` waits at most ``PREFETCH_BOUNDED_WAIT_SECONDS``
for the task to settle:

- if it settles in time, the recall is folded into the user message just
  like before — "memory always-on" stays honored on the fast path;
- if it does not settle in time, the task is handed to ``agent_loop`` and
  ``consume_pending_recall`` injects the recall as a synthetic user-role
  message at the next iteration as soon as the task is done.

This is the Claude-Code-style "settle and consume" pattern (see
/Others/src/utils/attachments.ts and /Others/src/query.ts) ported to the
engine. It hides slow recall under the model + tool work that follows
the first iteration, instead of blocking the first model call on it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from src.engine_core.thread import (
    build_recall_reminder,
    consume_pending_recall,
)


class TestBuildRecallReminder:
    def test_empty_bundle_returns_none(self):
        assert build_recall_reminder({}) is None
        assert build_recall_reminder({"episodes": [], "knowledge": [], "social": []}) is None

    def test_episodes_only_renders_past_context_block(self):
        bundle = {
            "episodes": [
                {"key": "ep_1", "description": "talked about engine latency", "score": 0.9},
            ],
            "knowledge": [],
            "social": [],
        }
        out = build_recall_reminder(bundle)
        assert out is not None
        assert out.startswith("<system-reminder>")
        assert out.endswith("</system-reminder>")
        assert "Relevant past context:" in out
        assert "ep_1" in out
        assert "talked about engine latency" in out
        assert "Known facts:" not in out
        assert "People context:" not in out

    def test_knowledge_only_renders_known_facts_block(self):
        bundle = {
            "episodes": [],
            "knowledge": [
                {"content": "User prefers terse summaries.", "score": 0.7},
                {"key": "fallback_key", "score": 0.6},
            ],
            "social": [],
        }
        out = build_recall_reminder(bundle)
        assert out is not None
        assert "Known facts:" in out
        assert "User prefers terse summaries." in out
        assert "fallback_key" in out

    def test_social_only_renders_people_block_capped_at_three(self):
        bundle = {
            "episodes": [],
            "knowledge": [],
            "social": [
                {"name": "Aanya"},
                {"name": "Beena"},
                {"name": "Chitra"},
                {"name": "Devika"},
            ],
        }
        out = build_recall_reminder(bundle)
        assert out is not None
        assert "People context:" in out
        assert "Aanya" in out
        assert "Beena" in out
        assert "Chitra" in out
        assert "Devika" not in out

    def test_all_three_render_in_one_reminder_in_order(self):
        bundle = {
            "episodes": [{"key": "ep_a", "description": "x"}],
            "knowledge": [{"content": "fact_a"}],
            "social": [{"name": "Aanya"}],
        }
        out = build_recall_reminder(bundle)
        assert out is not None
        idx_past = out.index("Relevant past context:")
        idx_known = out.index("Known facts:")
        idx_people = out.index("People context:")
        assert idx_past < idx_known < idx_people

    def test_missing_keys_are_treated_as_empty(self):
        assert build_recall_reminder({"episodes": None}) is None
        assert build_recall_reminder({"knowledge": None, "social": None}) is None


@dataclass
class FakeMessage:
    role: str
    content: str


def make_fake(content: str) -> FakeMessage:
    return FakeMessage(role="user", content=content)


class TestConsumePendingRecall:
    @pytest.mark.asyncio
    async def test_no_task_is_a_noop(self):
        messages, consumed = consume_pending_recall(
            ["hi"],
            None,
            False,
            message_factory=make_fake,
        )
        assert messages == ["hi"]
        assert consumed is False

    @pytest.mark.asyncio
    async def test_already_consumed_short_circuits(self):
        async def settled():
            return {"episodes": [{"key": "k", "description": "d"}]}

        task = asyncio.create_task(settled())
        await task

        messages, consumed = consume_pending_recall(
            ["hi"],
            task,
            recall_consumed=True,
            message_factory=make_fake,
        )
        assert messages == ["hi"]
        assert consumed is True

    @pytest.mark.asyncio
    async def test_pending_task_skipped_zero_wait(self):
        loop_event = asyncio.Event()

        async def slow():
            await loop_event.wait()
            return {}

        task = asyncio.create_task(slow())
        try:
            messages, consumed = consume_pending_recall(
                ["hi"],
                task,
                False,
                message_factory=make_fake,
            )
            assert messages == ["hi"]
            assert consumed is False
        finally:
            loop_event.set()
            await task

    @pytest.mark.asyncio
    async def test_settled_task_with_results_injects_user_message_once(self):
        async def settled():
            return {
                "episodes": [{"key": "ep_1", "description": "prior latency probe"}],
                "knowledge": [],
                "social": [],
            }

        task = asyncio.create_task(settled())
        await task

        messages, consumed = consume_pending_recall(
            ["hi"],
            task,
            False,
            message_factory=make_fake,
        )
        assert consumed is True
        assert len(messages) == 2
        appended = messages[-1]
        assert isinstance(appended, FakeMessage)
        assert appended.role == "user"
        assert appended.content.startswith("<system-reminder>")
        assert "prior latency probe" in appended.content

        messages_after, consumed_after = consume_pending_recall(
            messages,
            task,
            recall_consumed=True,
            message_factory=make_fake,
        )
        assert messages_after == messages
        assert consumed_after is True

    @pytest.mark.asyncio
    async def test_settled_task_with_empty_bundle_marks_consumed_no_inject(self):
        async def settled():
            return {"episodes": [], "knowledge": [], "social": []}

        task = asyncio.create_task(settled())
        await task

        messages, consumed = consume_pending_recall(
            ["hi"],
            task,
            False,
            message_factory=make_fake,
        )
        assert messages == ["hi"]
        assert consumed is True

    @pytest.mark.asyncio
    async def test_settled_task_with_exception_marks_consumed_no_inject(self):
        async def boom():
            raise RuntimeError("recall storage offline")

        task = asyncio.create_task(boom())
        with pytest.raises(RuntimeError):
            await task

        messages, consumed = consume_pending_recall(
            ["hi"],
            task,
            False,
            message_factory=make_fake,
        )
        assert messages == ["hi"]
        assert consumed is True
