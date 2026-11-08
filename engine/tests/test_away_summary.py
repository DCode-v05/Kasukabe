"""Tests for services.away_summary — LLM-powered reconnect recap.

Two layers:
  1. Deterministic recap via ``ReconnectSummary.format_recap()`` — no LLM.
  2. LLM recap via ``generate_away_summary`` — exercised with a fake LLM
     that implements the ``LLM`` protocol's ``generate`` method.

The fake LLM is NOT a mock of provider behavior — it is a stub LLM
implementation that lets us assert prompt construction, timeout handling,
and fallback semantics without hitting a paid API.
"""

from __future__ import annotations

import asyncio
import pytest

from src.utils.activity_manager import (
    ActivityState,
    ReconnectSummary,
    SessionStats,
)
from src.services.away_summary import generate_away_summary


def summary(
    prev: ActivityState = ActivityState.AWAY,
    idle_s: float = 1800.0,
    tool_calls: int = 4,
    errors: int = 1,
    engine_s: float = 12.3,
    user_s: float = 4.0,
) -> ReconnectSummary:
    return ReconnectSummary(
        previous_state=prev,
        idle_seconds=idle_s,
        stats=SessionStats(
            tool_calls=tool_calls,
            errors=errors,
            engine_active_seconds=engine_s,
            user_active_seconds=user_s,
        ),
    )


# ---------------------------------------------------------------------------
# Deterministic recap
# ---------------------------------------------------------------------------


def test_format_recap_minutes():
    recap = summary(idle_s=1800.0, tool_calls=4, errors=1, engine_s=12.3).format_recap()
    assert "30m" in recap
    assert "4 tool calls" in recap
    assert "12.3s of compute" in recap
    assert "1 error" in recap


def test_format_recap_hours():
    recap = summary(idle_s=7200.0).format_recap()
    assert "2.0h" in recap


def test_format_recap_seconds():
    recap = summary(idle_s=45.0).format_recap()
    assert "45s" in recap


def test_format_recap_no_activity():
    recap = summary(tool_calls=0, errors=0, engine_s=0.0).format_recap()
    assert "no autonomous activity" in recap


def test_format_recap_singular_plural():
    one = summary(tool_calls=1, errors=1).format_recap()
    assert "1 tool call," in one or "1 tool call." in one
    assert "1 error" in one

    two = summary(tool_calls=2, errors=2).format_recap()
    assert "2 tool calls" in two
    assert "2 errors" in two


# ---------------------------------------------------------------------------
# LLM recap — fake LLM stub
# ---------------------------------------------------------------------------


class StubLLM:
    """Minimal LLM stub — captures the prompt, returns a canned response.

    Only ``generate_with_tools`` is implemented because that is all
    ``generate_away_summary`` calls.  LLM is a Protocol, so partial
    implementations are accepted structurally by type checkers.
    """

    def __init__(self, response: str = "Task context. Next step.") -> None:
        self.response = response
        self.messages_seen: list = []
        self.max_tokens_seen: int | None = None

    async def generate_with_tools(
        self,
        messages,
        system=None,
        tools=None,
        effort=None,
        max_output_tokens=None,
    ):
        from src.herald.types import LLMResponse

        self.messages_seen = messages
        self.max_tokens_seen = max_output_tokens
        return LLMResponse(text=self.response)


@pytest.mark.asyncio
async def test_llm_recap_happy_path():
    llm = StubLLM(response="You were debugging auth. Look at the failing migration.")
    result = await generate_away_summary(llm, summary())
    assert result == "You were debugging auth. Look at the failing migration."


@pytest.mark.asyncio
async def test_llm_recap_prompt_includes_stats():
    llm = StubLLM()
    await generate_away_summary(llm, summary(tool_calls=7, errors=2, engine_s=15.5))
    assert llm.messages_seen, "LLM.generate was not called"
    prompt = llm.messages_seen[0].content
    assert "tool calls while away: 7" in prompt
    assert "errors while away: 2" in prompt
    assert "engine compute seconds: 15.5" in prompt


@pytest.mark.asyncio
async def test_llm_recap_includes_last_user_message():
    llm = StubLLM()
    await generate_away_summary(
        llm, summary(), last_user_message="please fix the failing test in auth.py"
    )
    prompt = llm.messages_seen[0].content
    assert "please fix the failing test" in prompt


@pytest.mark.asyncio
async def test_llm_recap_timeout_returns_none():
    class SlowLLM(StubLLM):
        async def generate_with_tools(self, *a, **kw):
            await asyncio.sleep(5.0)
            return None

    result = await generate_away_summary(SlowLLM(), summary(), timeout_s=0.1)
    assert result is None


@pytest.mark.asyncio
async def test_llm_recap_error_returns_none():
    class BrokenLLM(StubLLM):
        async def generate_with_tools(self, *a, **kw):
            raise RuntimeError("provider down")

    result = await generate_away_summary(BrokenLLM(), summary())
    assert result is None


@pytest.mark.asyncio
async def test_llm_recap_enforces_max_recap_tokens():
    from src.services.away_summary import MAX_RECAP_TOKENS

    llm = StubLLM()
    await generate_away_summary(llm, summary())
    assert llm.max_tokens_seen == MAX_RECAP_TOKENS


@pytest.mark.asyncio
async def test_llm_recap_empty_response_returns_none():
    result = await generate_away_summary(StubLLM(response="   "), summary())
    assert result is None


@pytest.mark.asyncio
async def test_llm_recap_strips_whitespace():
    result = await generate_away_summary(StubLLM(response="\n  hello  \n"), summary())
    assert result == "hello"
