"""Tests for src/engine_core/importance_scoring.py — the pure function
that backs Context Collapse staging decisions.

These are full real tests, not mocks: the scorer has no dependencies
on an LLM, I/O, or the orchestrator, so every test runs end-to-end
against the actual code path."""

from __future__ import annotations

import pytest

from src.engine_core.importance_scoring import (
    score_message_importance,
    BASE_ASSISTANT_TEXT,
    BASE_TOOL_USE_EDIT,
    BASE_TOOL_USE_READ,
    BASE_TOOL_USE_SEARCH,
    BASE_TOOL_RESULT,
)
from src.herald.types import Message, TextBlock, ToolResultBlock, ToolUseBlock


def assistant_text(text: str) -> Message:
    return Message(
        role="assistant",
        content=[TextBlock(text=text)],
        timestamp=1.0,
    )


def tool_use(name: str, tid: str = "tu1") -> Message:
    return Message(
        role="assistant",
        content=[ToolUseBlock(id=tid, name=name, input={"p": "v"})],
        timestamp=1.0,
    )


def tool_result(text: str, tid: str = "tu1") -> Message:
    return Message(
        role="user",
        content=[ToolResultBlock(tool_use_id=tid, content=text)],
        timestamp=1.0,
    )


class TestUserMessagesPinned:
    def test_plain_user_text_pinned_to_1(self):
        msg = Message(role="user", content="what is the status?")
        assert score_message_importance(msg, position=0, total=10) == 1.0

    def test_user_text_block_pinned_to_1(self):
        msg = Message(role="user", content=[TextBlock(text="hi")])
        assert score_message_importance(msg, position=0, total=10) == 1.0

    def test_user_tool_result_NOT_pinned(self):
        msg = tool_result("log line")
        score = score_message_importance(msg, position=0, total=10)
        assert score < 1.0


class TestToolClassHierarchy:
    def test_edit_scores_above_read(self):
        edit = tool_use("Edit")
        read = tool_use("Read")
        s_edit = score_message_importance(edit, 5, 10)
        s_read = score_message_importance(read, 5, 10)
        assert s_edit > s_read

    def test_read_scores_above_glob(self):
        read = tool_use("Read")
        glob = tool_use("Glob")
        s_read = score_message_importance(read, 5, 10)
        s_glob = score_message_importance(glob, 5, 10)
        assert s_read > s_glob

    def test_write_base_matches_edit_base(self):
        e = tool_use("Edit")
        w = tool_use("Write")
        assert score_message_importance(e, 0, 10) == pytest.approx(
            score_message_importance(w, 0, 10)
        )


class TestRecencyBonus:
    def test_newer_scores_higher_than_older(self):
        msg = assistant_text("thinking about the plan")
        oldest = score_message_importance(msg, position=0, total=100)
        newest = score_message_importance(msg, position=99, total=100)
        assert newest > oldest

    def test_single_message_no_divbyzero(self):
        msg = assistant_text("solo")
        score = score_message_importance(msg, position=0, total=1)
        assert 0.0 <= score <= 1.0


class TestShortToolResultsDeprioritized:
    def test_short_result_scores_below_long_result(self):
        short = tool_result("ok")
        long = tool_result("x" * 500)
        s_short = score_message_importance(short, 5, 10)
        s_long = score_message_importance(long, 5, 10)
        assert s_short < s_long


class TestErrorKeywordBoost:
    def test_error_text_scores_above_plain_text(self):
        plain = assistant_text("I will read the file.")
        err = assistant_text("An error occurred while running the tool.")
        s_plain = score_message_importance(plain, 5, 10)
        s_err = score_message_importance(err, 5, 10)
        assert s_err > s_plain

    def test_exception_in_tool_result(self):
        plain = tool_result("listed 3 files")
        err = tool_result("Traceback (most recent call last): ... Exception: boom " + "x" * 200)
        s_plain = score_message_importance(plain, 5, 10)
        s_err = score_message_importance(err, 5, 10)
        assert s_err > s_plain


class TestScoreBounds:
    def test_score_never_below_zero(self):
        msg = assistant_text("")
        assert score_message_importance(msg, 0, 10) >= 0.0

    def test_score_never_above_one(self):
        msg = Message(
            role="assistant",
            content=[
                TextBlock(text="Fixing the error now."),
                ToolUseBlock(id="tu", name="Edit", input={"path": "/etc/x"}),
            ],
            timestamp=1.0,
        )
        assert score_message_importance(msg, 99, 100) <= 1.0


class TestConstantsSanity:
    def test_read_base_below_mid_threshold(self):
        assert BASE_TOOL_USE_READ < 0.60

    def test_search_base_below_low_threshold(self):
        assert BASE_TOOL_USE_SEARCH < 0.30

    def test_edit_base_above_mid_threshold(self):
        assert BASE_TOOL_USE_EDIT >= 0.60

    def test_assistant_text_between(self):
        assert 0.30 < BASE_ASSISTANT_TEXT < 0.60

    def test_tool_result_base_below_low_threshold(self):
        assert BASE_TOOL_RESULT <= 0.30
